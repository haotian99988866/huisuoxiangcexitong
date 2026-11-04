import argparse
import hashlib
import io
import os
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, UnidentifiedImageError

from db import get_conn
from duty_shift import current_shift_window
from image_hash import ImageDeduper, SAME_IMAGE_DISTANCE


DELIVERY_VENUE_IDS = {7, 8, 9, 10}
MODEL_COMMIT = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
MODEL_FILES = {
    "yunet": (
        "face_detection_yunet_2023mar.onnx",
        "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    ),
    "sface": (
        "face_recognition_sface_2021dec.onnx",
        "face_recognition_sface/face_recognition_sface_2021dec.onnx",
    ),
}
MODEL_SIZES = {"yunet": 232589, "sface": 38696353}
MODEL_SHA256 = {
    "yunet": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "sface": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
}
MODEL_LICENSES = {
    "yunet": "face_detection_yunet/LICENSE",
    "sface": "face_recognition_sface/LICENSE",
}
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MAX_SCAN_MEDIA = 300
MAX_VIDEO_FRAMES = 6
MAX_FACES_PER_FRAME = 8
PHOTO_PROFILE_VERSION = 2
MAX_DESCRIPTION_LENGTH = 2000
_FACE_MODEL_LOCK = threading.Lock()


class MediaTooLargeError(ValueError):
    pass


def _model_directory():
    configured = os.getenv("DELIVERY_FACE_MODEL_DIR")
    if configured:
        return Path(configured)
    root = os.getenv("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(root) / "Huisuo" / "face-models"


def download_models():
    directory = _model_directory()
    directory.mkdir(parents=True, exist_ok=True)
    base_url = (
        "https://github.com/opencv/opencv_zoo/raw/"
        f"{MODEL_COMMIT}/models"
    )
    for model_name, (filename, relative_path) in MODEL_FILES.items():
        target = directory / filename
        if _is_valid_model(target, model_name):
            continue
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            digest = hashlib.sha256()
            with requests.get(
                f"{base_url}/{relative_path}",
                stream=True,
                timeout=(10, 90),
            ) as response:
                response.raise_for_status()
                size = 0
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > MODEL_SIZES[model_name]:
                            raise ValueError(f"{model_name} model exceeds the pinned size")
                        digest.update(chunk)
                        output.write(chunk)
            if size != MODEL_SIZES[model_name] or digest.hexdigest() != MODEL_SHA256[model_name]:
                raise ValueError(f"{model_name} model download was incomplete")
            os.replace(temporary, target)
        except (requests.RequestException, OSError, ValueError):
            temporary.unlink(missing_ok=True)
            raise
    for model_name, relative_path in MODEL_LICENSES.items():
        target = directory / f"{model_name}-LICENSE.txt"
        if target.is_file() and target.stat().st_size > 0:
            continue
        response = requests.get(
            f"{base_url}/{relative_path}", timeout=(10, 30)
        )
        response.raise_for_status()
        target.write_text(response.text, encoding="utf-8")


def _is_valid_model(path, model_name):
    if not path.is_file() or path.stat().st_size != MODEL_SIZES[model_name]:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as model:
        for chunk in iter(lambda: model.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == MODEL_SHA256[model_name]


@lru_cache(maxsize=1)
def _face_models():
    import cv2

    directory = _model_directory()
    yunet_path = directory / MODEL_FILES["yunet"][0]
    sface_path = directory / MODEL_FILES["sface"][0]
    if not _is_valid_model(yunet_path, "yunet") or not _is_valid_model(sface_path, "sface"):
        raise FileNotFoundError(
            "Face models are missing or failed integrity checks; "
            "run python delivery_staff.py download-models"
        )
    try:
        detector = cv2.FaceDetectorYN.create(
            str(yunet_path), "", (320, 320), 0.8, 0.3, 5000
        )
        recognizer = cv2.FaceRecognizerSF.create(str(sface_path), "")
    except cv2.error as exc:
        raise RuntimeError("Local OpenCV face models could not be loaded") from exc
    return detector, recognizer


def _features_from_frame(frame):
    import cv2
    import numpy as np

    if frame is None or frame.size == 0:
        return []
    height, width = frame.shape[:2]
    if width < 80 or height < 80:
        return []
    scale = min(1.0, 1280 / max(width, height))
    if scale < 1:
        frame = cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    detector, recognizer = _face_models()
    with _FACE_MODEL_LOCK:
        detector.setInputSize((frame.shape[1], frame.shape[0]))
        _, faces = detector.detect(frame)
        if faces is None:
            return []
        features = []
        faces = sorted(faces, key=lambda face: face[2] * face[3], reverse=True)
        for face in faces[:MAX_FACES_PER_FRAME]:
            aligned = recognizer.alignCrop(frame, face)
            feature = np.asarray(recognizer.feature(aligned), dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(feature))
            if norm > 0:
                features.append(feature / norm)
        return features


def _image_features(content):
    import cv2
    import numpy as np
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(content)) as source:
            width, height = source.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("A delivery photo could not be decoded") from exc
    if width <= 0 or height <= 0 or width * height > 40_000_000:
        raise ValueError("A delivery photo exceeds the local processing limits")
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("A delivery photo could not be decoded")
    return _features_from_frame(image)


def _video_features(content):
    import cv2

    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as video:
            path = video.name
            video.write(content)
        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            raise ValueError("A delivery video could not be opened")
        try:
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if frame_count <= 0:
                raise ValueError("A delivery video has no readable frames")
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if width <= 0 or height <= 0 or width * height > 40_000_000:
                raise ValueError("A delivery video exceeds the local processing limits")
            sample_count = min(MAX_VIDEO_FRAMES, frame_count)
            positions = sorted({
                round(index * (frame_count - 1) / max(1, sample_count - 1))
                for index in range(sample_count)
            })
            features = []
            for position in positions:
                capture.set(cv2.CAP_PROP_POS_FRAMES, position)
                ok, frame = capture.read()
                if ok:
                    features.extend(_features_from_frame(frame))
            return features
        finally:
            capture.release()
    finally:
        if path and os.path.exists(path):
            os.remove(path)


def extract_features(media_type, content):
    import cv2

    try:
        if media_type == "photo":
            return _image_features(content)
        if media_type == "video":
            return _video_features(content)
        raise ValueError("Unsupported delivery media type")
    except cv2.error as exc:
        raise RuntimeError("OpenCV could not process delivery media") from exc


def download_telegram_media(file_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    with requests.get(
        f"https://api.telegram.org/bot{token}/getFile",
        params={"file_id": file_id},
        timeout=(5, 15),
    ) as response:
        response.raise_for_status()
        payload = response.json()
    result = payload.get("result") or {}
    file_path = result.get("file_path")
    file_size = result.get("file_size")
    if not payload.get("ok") or not file_path:
        raise ValueError("Telegram did not return a delivery-media path")
    if file_size is not None and (
        type(file_size) is not int or file_size <= 0 or file_size > MAX_MEDIA_BYTES
    ):
        if type(file_size) is int and file_size > MAX_MEDIA_BYTES:
            raise MediaTooLargeError("Delivery media exceeds the 20 MB local matching limit")
        raise ValueError("Delivery media size is invalid for local matching")
    with requests.get(
        f"https://api.telegram.org/file/bot{token}/{quote(file_path, safe='/')}",
        stream=True,
        timeout=(5, 30),
    ) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_MEDIA_BYTES:
                raise MediaTooLargeError("Delivery media exceeds the 20 MB local matching limit")
            chunks.append(chunk)
    return b"".join(chunks)


def _identity_key(venue_id, channel_id, message_id, media_type):
    value = f"{venue_id}:{channel_id}:{media_type}:{message_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_people (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    venue_id INT NOT NULL,
                    identity_key CHAR(64) NOT NULL,
                    display_name VARCHAR(128) NOT NULL DEFAULT '',
                    consent_status VARCHAR(16) NOT NULL DEFAULT 'granted',
                    merged_into_id BIGINT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_delivery_people_identity (venue_id, identity_key),
                    INDEX idx_delivery_people_venue (venue_id, merged_into_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_person_audit (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    person_id BIGINT NOT NULL,
                    actor_id BIGINT NOT NULL,
                    action VARCHAR(32) NOT NULL,
                    details TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_delivery_person_audit_person (person_id, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_people_state (
                    id TINYINT PRIMARY KEY,
                    photo_profile_version INT NOT NULL DEFAULT 0
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                INSERT IGNORE INTO delivery_people_state
                    (id, photo_profile_version)
                VALUES (1, 0)
                """
            )
            cur.execute("SHOW COLUMNS FROM delivery_people_state LIKE 'archive_build_mode'")
            if cur.fetchone() is None:
                cur.execute("ALTER TABLE delivery_people_state ADD COLUMN archive_build_mode BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("SHOW COLUMNS FROM delivery_media LIKE 'archive_only'")
            if cur.fetchone() is None:
                cur.execute("ALTER TABLE delivery_media ADD COLUMN archive_only BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_person_notes (
                    person_id BIGINT PRIMARY KEY,
                    description TEXT NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute("SHOW COLUMNS FROM delivery_people LIKE 'deleted_at'")
            if cur.fetchone() is None:
                cur.execute("ALTER TABLE delivery_people ADD COLUMN deleted_at DATETIME NULL")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_person_attachments (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    person_id BIGINT NOT NULL,
                    content_hash CHAR(64) NOT NULL,
                    media_type VARCHAR(16) NOT NULL,
                    content_type VARCHAR(32) NOT NULL,
                    content LONGBLOB NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_delivery_attachment (person_id, content_hash)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute("SHOW COLUMNS FROM delivery_media LIKE 'person_id'")
            if cur.fetchone() is None:
                cur.execute(
                    "ALTER TABLE delivery_media ADD COLUMN person_id BIGINT NULL"
                )
                cur.execute(
                    "ALTER TABLE delivery_media ADD INDEX idx_delivery_media_person (person_id)"
                )
            cur.execute(
                "SELECT photo_profile_version FROM delivery_people_state "
                "WHERE id=1 FOR UPDATE"
            )
            profile_state = cur.fetchone()
            cur.execute(
                "SELECT 1 FROM delivery_media "
                "WHERE media_type IN ('photo', 'video') AND person_id IS NULL LIMIT 1"
            )
            has_unassigned_media = cur.fetchone() is not None
            if (
                profile_state["photo_profile_version"] < PHOTO_PROFILE_VERSION
                or has_unassigned_media
            ):
                _rebuild_photo_profiles(cur)
                cur.execute(
                    "UPDATE delivery_people_state SET photo_profile_version=%s WHERE id=1",
                    (PHOTO_PROFILE_VERSION,),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _rebuild_photo_profiles(cur):
    cur.execute(
        """
        SELECT id, venue_id, channel_id, message_id, media_group_id,
               media_type, image_hash, person_id
        FROM delivery_media
        WHERE media_type IN ('photo', 'video')
        ORDER BY id
        """
    )
    rows = cur.fetchall()
    dedupers = {}
    media_people = {}
    profile_photo_used = set()
    photo_people_by_group = {}
    duplicate_profiles = {}

    for row in rows:
        old_person_id = row["person_id"]
        if row["media_type"] == "photo":
            deduper = dedupers.setdefault(
                row["venue_id"], ImageDeduper(SAME_IMAGE_DISTANCE)
            )
            duplicate_key = deduper.key_for(row["image_hash"], row["id"])
            if duplicate_key != row["id"]:
                person_id = media_people[duplicate_key]
                if old_person_id and old_person_id != person_id:
                    duplicate_profiles[old_person_id] = person_id
            elif old_person_id and old_person_id not in profile_photo_used:
                person_id = old_person_id
                profile_photo_used.add(person_id)
            else:
                person_id = _ensure_person(cur, row)
                profile_photo_used.add(person_id)
            if row["media_group_id"]:
                group_key = (row["venue_id"], row["channel_id"], row["media_group_id"])
                photo_people_by_group.setdefault(group_key, set()).add(person_id)
        else:
            person_id = old_person_id or _ensure_person(cur, row)

        media_people[row["id"]] = person_id
        if old_person_id != person_id:
            cur.execute(
                "UPDATE delivery_media SET person_id=%s WHERE id=%s",
                (person_id, row["id"]),
            )

    for row in rows:
        if row["media_type"] != "video" or not row["media_group_id"]:
            continue
        group_key = (row["venue_id"], row["channel_id"], row["media_group_id"])
        photo_people = photo_people_by_group.get(group_key, set())
        if len(photo_people) == 1:
            person_id = next(iter(photo_people))
            old_person_id = row["person_id"]
            if old_person_id != person_id:
                cur.execute(
                    "UPDATE delivery_media SET person_id=%s WHERE id=%s",
                    (person_id, row["id"]),
                )
                media_people[row["id"]] = person_id
                if old_person_id and old_person_id != person_id:
                    duplicate_profiles[old_person_id] = person_id

    for source_id, target_id in duplicate_profiles.items():
        if source_id == target_id:
            continue
        cur.execute(
            "SELECT COUNT(*) AS count FROM delivery_media WHERE person_id=%s",
            (source_id,),
        )
        if cur.fetchone()["count"] == 0:
            cur.execute(
                """
                UPDATE delivery_people
                SET merged_into_id=%s
                WHERE id=%s AND merged_into_id IS NULL
                """,
                (target_id, source_id),
            )


def _ensure_person(cur, row):
    identity_key = _identity_key(
        row["venue_id"], row["channel_id"], row["message_id"], row["media_type"]
    )
    consent_status = "granted"
    if row.get("person_id"):
        cur.execute(
            "SELECT consent_status FROM delivery_people WHERE id=%s",
            (row["person_id"],),
        )
        existing = cur.fetchone()
        if existing:
            consent_status = existing["consent_status"]
    cur.execute(
        """
        INSERT INTO delivery_people (venue_id, identity_key, consent_status)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)
        """,
        (row["venue_id"], identity_key, consent_status),
    )
    person_id = cur.lastrowid
    cur.execute("SELECT merged_into_id FROM delivery_people WHERE id=%s", (person_id,))
    person = cur.fetchone()
    return person["merged_into_id"] if person and person["merged_into_id"] else person_id


def _unique_profile_media(media_rows):
    seen_images = ImageDeduper(SAME_IMAGE_DISTANCE)
    kept = []
    for media in media_rows:
        image_hash = media.get("image_hash")
        if (
            media["media_type"] == "photo"
            and image_hash
            and seen_images.key_for(image_hash, media["id"]) != media["id"]
        ):
            continue
        kept.append(media)
    return kept


def archive_build_mode(cur):
    cur.execute("SELECT archive_build_mode FROM delivery_people_state WHERE id=1 FOR SHARE")
    state = cur.fetchone()
    if state is None:
        raise RuntimeError("Delivery ingestion state is missing")
    return bool(state["archive_build_mode"])


def set_archive_build_mode(enabled):
    if type(enabled) is not bool:
        raise ValueError("Archive build mode must be boolean")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM delivery_people_state WHERE id=1 FOR UPDATE")
            if cur.fetchone() is None:
                raise RuntimeError("Delivery ingestion state is missing")
            cur.execute("UPDATE delivery_people_state SET archive_build_mode=%s WHERE id=1", (enabled,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def register_media(venue_id, channel_id, message_id):
    if venue_id not in DELIVERY_VENUE_IDS:
        raise ValueError("Delivery venue id is invalid")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM venues WHERE id=%s FOR UPDATE", (venue_id,))
            cur.execute(
                """
                SELECT id, venue_id, channel_id, message_id, media_group_id,
                       media_type, image_hash, person_id
                FROM delivery_media
                WHERE channel_id=%s AND message_id=%s
                """,
                (str(channel_id), message_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("Delivery media was not saved before profile registration")
            person_id = None
            if row["media_type"] == "photo":
                person_id = _matching_photo_person(cur, row)
            elif row["media_type"] == "video" and row["media_group_id"]:
                cur.execute(
                    """
                    SELECT DISTINCT person_id
                    FROM delivery_media
                    WHERE channel_id=%s AND media_group_id=%s
                          AND media_type='photo' AND person_id IS NOT NULL
                    """,
                    (str(channel_id), row["media_group_id"]),
                )
                photo_people = [item["person_id"] for item in cur.fetchall()]
                if len(photo_people) == 1:
                    person_id = photo_people[0]
            if person_id is None:
                person_id = row["person_id"] or _ensure_person(cur, row)
            cur.execute(
                "UPDATE delivery_media SET person_id=%s WHERE id=%s",
                (person_id, row["id"]),
            )
            if row["media_type"] == "photo" and row["media_group_id"]:
                cur.execute(
                    """
                    SELECT DISTINCT person_id
                    FROM delivery_media
                    WHERE channel_id=%s AND media_group_id=%s
                          AND media_type='photo' AND person_id IS NOT NULL
                    """,
                    (str(channel_id), row["media_group_id"]),
                )
                photo_people = [item["person_id"] for item in cur.fetchall()]
                if len(photo_people) == 1:
                    cur.execute(
                        """
                        UPDATE delivery_media SET person_id=%s
                        WHERE channel_id=%s AND media_group_id=%s AND media_type='video'
                        """,
                        (photo_people[0], str(channel_id), row["media_group_id"]),
                    )
        conn.commit()
        return person_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _matching_photo_person(cur, row):
    if row["image_hash"]:
        cur.execute(
            """
            SELECT id, image_hash, person_id
            FROM delivery_media
            WHERE venue_id=%s AND media_type='photo' AND image_hash IS NOT NULL
                  AND id < %s AND person_id IS NOT NULL
            ORDER BY id
            """,
            (row["venue_id"], row["id"]),
        )
        deduper = ImageDeduper(SAME_IMAGE_DISTANCE)
        first_person_by_media = {}
        for earlier in cur.fetchall():
            key = deduper.key_for(earlier["image_hash"], earlier["id"])
            if key == earlier["id"]:
                first_person_by_media[key] = earlier["person_id"]
        key = deduper.key_for(row["image_hash"], row["id"])
        if key != row["id"]:
            return first_person_by_media[key]
    return row["person_id"] or _ensure_person(cur, row)


def admin_listing(venue_id):
    if type(venue_id) is not int or venue_id not in DELIVERY_VENUE_IDS:
        raise ValueError("请选择有效的外送会所")
    shift_start, shift_end, shift = current_shift_window()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.venue_id, p.display_name, p.consent_status,
                       p.merged_into_id, p.created_at,
                       COUNT(m.id) AS media_count,
                       MAX(m.created_at) AS latest_media_at
                FROM delivery_people p
                LEFT JOIN delivery_media m ON m.person_id=p.id
                WHERE p.venue_id=%s AND p.deleted_at IS NULL
                GROUP BY p.id
                ORDER BY p.merged_into_id IS NOT NULL, latest_media_at DESC, p.id DESC
                """,
                (venue_id,),
            )
            people = cur.fetchall()
            active = [person["id"] for person in people if person["merged_into_id"] is None]
            media_by_person = {person_id: [] for person_id in active}
            if active:
                placeholders = ",".join(["%s"] * len(active))
                cur.execute(
                    f"""
                    SELECT id, person_id, media_type, caption, created_at, image_hash, archive_only
                    FROM delivery_media
                    WHERE person_id IN ({placeholders})
                    ORDER BY created_at DESC, id DESC
                    """,
                    tuple(active),
                )
                for media in cur.fetchall():
                    media_by_person[media["person_id"]].append(media)
            for person in people:
                all_media = media_by_person.get(person["id"], [])
                person["on_duty"] = shift == "晚班" and any(
                    item["media_type"] == "photo"
                    and not item.get("archive_only", False)
                    and shift_start <= item["created_at"] < shift_end
                    for item in all_media
                )
                person["attendance_overlay"] = (
                    "今晚出勤" if person["on_duty"] else
                    "本班未见照片" if shift == "晚班" else "外送仅晚班"
                )
                person["media"] = _unique_profile_media(
                    all_media
                )
                person["media_count"] = len(person["media"])
                person["attachments"] = []
                person["description"] = ""
            if active:
                cur.execute(
                    f"SELECT person_id, description FROM delivery_person_notes "
                    f"WHERE person_id IN ({placeholders})", tuple(active),
                )
                descriptions = {row["person_id"]: row["description"] for row in cur.fetchall()}
                cur.execute(
                    f"SELECT id, person_id, media_type, created_at FROM delivery_person_attachments "
                    f"WHERE person_id IN ({placeholders}) ORDER BY created_at, id", tuple(active),
                )
                attachments = {}
                for item in cur.fetchall():
                    attachments.setdefault(item["person_id"], []).append(item)
                for person in people:
                    person["description"] = descriptions.get(person["id"], "")
                    person["attachments"] = attachments.get(person["id"], [])
            return {
                "venue_id": venue_id,
                "shift": shift,
                "shift_date": shift_start.date().isoformat(),
                "on_duty_count": sum(person["on_duty"] for person in people
                                     if person["merged_into_id"] is None),
                "people": [
                    {
                        **person,
                    }
                    for person in people
                ]
            }
    finally:
        conn.close()


def public_people(cur, venue_id, shift_start, shift_end):
    cur.execute(
        """
        SELECT DISTINCT p.id, p.display_name
        FROM delivery_people p
        JOIN delivery_media today ON today.person_id=p.id
        WHERE p.venue_id=%s AND p.merged_into_id IS NULL AND p.deleted_at IS NULL
              AND p.consent_status='granted'
              AND today.created_at >= %s AND today.created_at < %s
              AND today.archive_only=FALSE AND today.media_type='photo'
        ORDER BY p.id DESC
        """,
        (venue_id, shift_start, shift_end),
    )
    people = cur.fetchall()
    if not people:
        return []
    person_ids = [person["id"] for person in people]
    placeholders = ",".join(["%s"] * len(person_ids))
    cur.execute(
        f"""
        SELECT id, person_id, media_type, created_at, image_hash
        FROM delivery_media
        WHERE person_id IN ({placeholders})
        ORDER BY created_at DESC, id DESC
        """,
        tuple(person_ids),
    )
    media_by_person = {person_id: [] for person_id in person_ids}
    for media in cur.fetchall():
        media_by_person[media["person_id"]].append(
            {
                "id": media["id"],
                "media_type": media["media_type"],
                "image_hash": media["image_hash"],
            }
        )
    media_by_person = {
        person_id: _unique_profile_media(rows)
        for person_id, rows in media_by_person.items()
    }
    return [
        {
            "id": person["id"],
            "display_name": person["display_name"] or "外送人员",
            "media": [
                {"id": media["id"], "type": media["media_type"]}
                for media in media_by_person[person["id"]]
            ],
        }
        for person in people
    ]


def set_consent(person_id, granted, actor_id):
    if type(person_id) is not int or person_id <= 0 or type(granted) is not bool:
        raise ValueError("人员编号或授权状态无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT venue_id, consent_status, merged_into_id FROM delivery_people WHERE id=%s AND deleted_at IS NULL FOR UPDATE",
                (person_id,),
            )
            person = cur.fetchone()
            if person is None or person["merged_into_id"] is not None:
                raise ValueError("人员档案不存在或已合并")
            status = "granted" if granted else "revoked"
            cur.execute(
                "UPDATE delivery_people SET consent_status=%s WHERE id=%s",
                (status, person_id),
            )
            cur.execute(
                """
                INSERT INTO delivery_person_audit
                    (person_id, actor_id, action, details)
                VALUES (%s, %s, %s, %s)
                """,
                (person_id, actor_id, "consent_granted" if granted else "consent_revoked",
                 "No face templates are persisted."),
            )
        conn.commit()
        return {"person_id": person_id, "consent_status": status}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _validate_description(description):
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValueError("档案说明不能超过2000个字符")


def _write_description(cur, person_id, description, actor_id):
    cur.execute(
        "INSERT INTO delivery_person_notes (person_id, description) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE description=VALUES(description)",
        (person_id, description.strip()),
    )
    cur.execute(
        "INSERT INTO delivery_person_audit (person_id, actor_id, action, details) "
        "VALUES (%s, %s, 'description_saved', %s)",
        (person_id, actor_id, f"description_length={len(description.strip())}"),
    )


def save_description(person_id, description, actor_id):
    if type(person_id) is not int or person_id <= 0:
        raise ValueError("人员档案编号无效")
    _validate_description(description)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT merged_into_id FROM delivery_people WHERE id=%s AND deleted_at IS NULL FOR UPDATE", (person_id,))
            person = cur.fetchone()
            if person is None or person["merged_into_id"] is not None:
                raise ValueError("人员档案不存在或已合并")
            _write_description(cur, person_id, description, actor_id)
        conn.commit()
        return {"person_id": person_id, "description": description.strip()}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _validate_attachment(raw, filename):
    if not raw or len(raw) > MAX_MEDIA_BYTES:
        raise ValueError("媒体文件不能为空且不能超过20MB")
    extension = Path(filename or "").suffix.lower()
    if extension in {".jpg", ".jpeg", ".png", ".webp"}:
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("照片不能超过5MB")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 20_000_000:
                    raise ValueError("照片分辨率不能超过2000万像素")
                image.load()
                image = image.convert("RGB")
                image.thumbnail((2560, 2560))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=90)
                return "photo", "image/jpeg", output.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("照片损坏或格式不支持") from exc
    if extension not in {".mp4", ".webm"}:
        raise ValueError("请选择 JPG、PNG、WebP 照片或 MP4、WebM 视频")
    import cv2
    with tempfile.TemporaryDirectory(prefix="delivery-upload-") as directory:
        path = Path(directory) / f"video{extension}"
        path.write_bytes(raw)
        capture = cv2.VideoCapture(str(path))
        try:
            readable, frame = capture.read()
            if not readable or frame is None:
                raise ValueError("视频损坏或无法播放")
        except cv2.error as exc:
            raise ValueError("视频损坏或无法播放") from exc
        finally:
            capture.release()
    if extension == ".mp4" and raw[4:8] != b"ftyp":
        raise ValueError("MP4 视频格式无效")
    if extension == ".webm" and not raw.startswith(b"\x1a\x45\xdf\xa3"):
        raise ValueError("WebM 视频格式无效")
    return "video", "video/mp4" if extension == ".mp4" else "video/webm", raw


def add_attachment(person_id, raw, filename, actor_id, *, description=None):
    if type(person_id) is not int or person_id <= 0:
        raise ValueError("人员档案编号无效")
    if description is not None:
        _validate_description(description)
    media_type, content_type, content = _validate_attachment(raw, filename)
    digest = hashlib.sha256(raw).hexdigest()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT merged_into_id FROM delivery_people WHERE id=%s AND deleted_at IS NULL FOR UPDATE", (person_id,))
            person = cur.fetchone()
            if person is None or person["merged_into_id"] is not None:
                raise ValueError("人员档案不存在或已合并")
            if description is not None:
                _write_description(cur, person_id, description, actor_id)
            cur.execute(
                "SELECT id FROM delivery_person_attachments WHERE person_id=%s AND content_hash=%s",
                (person_id, digest),
            )
            existing = cur.fetchone()
            if existing:
                conn.commit()
                return {"person_id": person_id, "attachment_id": existing["id"], "duplicate": True}
            cur.execute(
                "INSERT INTO delivery_person_attachments "
                "(person_id, content_hash, media_type, content_type, content) VALUES (%s,%s,%s,%s,%s)",
                (person_id, digest, media_type, content_type, content),
            )
            ident = cur.lastrowid
            cur.execute(
                "INSERT INTO delivery_person_audit (person_id, actor_id, action, details) "
                "VALUES (%s,%s,'attachment_added',%s)",
                (person_id, actor_id, f"attachment_id={ident}; bytes={len(content)}"),
            )
        conn.commit()
        return {"person_id": person_id, "attachment_id": ident, "duplicate": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def attachment(attachment_id):
    if type(attachment_id) is not int or attachment_id <= 0:
        raise ValueError("媒体编号无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.content, a.content_type FROM delivery_person_attachments a "
                "JOIN delivery_people p ON p.id=a.person_id "
                "WHERE a.id=%s AND p.merged_into_id IS NULL AND p.deleted_at IS NULL", (attachment_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def delete_archive_content(person_id, attachment_id, confirmed, actor_id):
    if confirmed is not True:
        raise ValueError("请先确认删除操作")
    if type(person_id) is not int or person_id <= 0:
        raise ValueError("人员档案编号无效")
    if attachment_id is not None and (type(attachment_id) is not int or attachment_id <= 0):
        raise ValueError("补充媒体编号无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT merged_into_id, deleted_at FROM delivery_people WHERE id=%s FOR UPDATE",
                (person_id,),
            )
            person = cur.fetchone()
            if person is None or person["merged_into_id"] is not None:
                raise ValueError("人员档案不存在或已合并")
            if person["deleted_at"] is not None:
                if attachment_id is not None:
                    raise ValueError("人员档案已删除")
                conn.commit()
                return {"person_id": person_id, "deleted": True}
            if attachment_id is None:
                cur.execute("DELETE FROM delivery_person_attachments WHERE person_id=%s", (person_id,))
                cur.execute("DELETE FROM delivery_person_notes WHERE person_id=%s", (person_id,))
                cur.execute("UPDATE delivery_people SET deleted_at=UTC_TIMESTAMP() WHERE id=%s", (person_id,))
            else:
                cur.execute(
                    "SELECT id FROM delivery_person_attachments WHERE id=%s AND person_id=%s",
                    (attachment_id, person_id),
                )
                if cur.fetchone() is None:
                    raise ValueError("补充媒体不存在或不属于此档案，请刷新核对")
                cur.execute(
                    "DELETE FROM delivery_person_attachments WHERE id=%s AND person_id=%s",
                    (attachment_id, person_id),
                )
            cur.execute(
                "INSERT INTO delivery_person_audit (person_id, actor_id, action, details) "
                "VALUES (%s,%s,%s,%s)",
                (person_id, actor_id,
                 "archive_deleted" if attachment_id is None else "attachment_deleted",
                 "channel_media_preserved" if attachment_id is None else f"attachment_id={attachment_id}"),
            )
        conn.commit()
        return {"person_id": person_id, "attachment_id": attachment_id, "deleted": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_profile(person_id, display_name, actor_id):
    if type(person_id) is not int or person_id <= 0:
        raise ValueError("人员档案编号无效")
    if not isinstance(display_name, str):
        raise ValueError("人员名称无效")
    display_name = display_name.strip()
    if len(display_name) > 60:
        raise ValueError("人员名称不能超过60个字符")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT merged_into_id FROM delivery_people WHERE id=%s AND deleted_at IS NULL FOR UPDATE",
                (person_id,),
            )
            person = cur.fetchone()
            if person is None or person["merged_into_id"] is not None:
                raise ValueError("人员档案不存在或已合并")
            cur.execute(
                "UPDATE delivery_people SET display_name=%s WHERE id=%s",
                (display_name, person_id),
            )
            cur.execute(
                """
                INSERT INTO delivery_person_audit
                    (person_id, actor_id, action, details)
                VALUES (%s, %s, 'profile_renamed', %s)
                """,
                (person_id, actor_id, f"display_name_length={len(display_name)}"),
            )
        conn.commit()
        return {"person_id": person_id, "display_name": display_name}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def merge_people(source_id, target_id, actor_id):
    if type(source_id) is not int or type(target_id) is not int or source_id <= 0 or target_id <= 0:
        raise ValueError("人员档案编号无效")
    if source_id == target_id:
        raise ValueError("不能将人员档案合并到自身")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, venue_id, consent_status, merged_into_id
                FROM delivery_people
                WHERE id IN (%s, %s) AND deleted_at IS NULL
                FOR UPDATE
                """,
                (source_id, target_id),
            )
            people = {row["id"]: row for row in cur.fetchall()}
            if len(people) != 2:
                raise ValueError("人员档案不存在")
            source, target = people[source_id], people[target_id]
            if source["venue_id"] != target["venue_id"]:
                raise ValueError("只能合并同一外送会所的档案")
            if source["merged_into_id"] is not None or target["merged_into_id"] is not None:
                raise ValueError("已合并档案不能再次参与合并")
            if source["consent_status"] != "granted" or target["consent_status"] != "granted":
                raise ValueError("请先确认两个档案均已获得人脸比对授权")
            cur.execute(
                "UPDATE delivery_media SET person_id=%s WHERE person_id=%s",
                (target_id, source_id),
            )
            cur.execute(
                "UPDATE delivery_people SET merged_into_id=%s WHERE id=%s",
                (target_id, source_id),
            )
            cur.execute(
                """
                INSERT INTO delivery_person_audit
                    (person_id, actor_id, action, details)
                VALUES (%s, %s, 'profile_merged', %s)
                """,
                (target_id, actor_id, f"source_person_id={source_id}"),
            )
        conn.commit()
        return {"source_id": source_id, "target_id": target_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def match_candidates(person_id, fetch_media):
    import numpy as np

    if type(person_id) is not int or person_id <= 0:
        raise ValueError("人员档案编号无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, venue_id, consent_status, merged_into_id
                FROM delivery_people WHERE id=%s AND deleted_at IS NULL
                """,
                (person_id,),
            )
            selected = cur.fetchone()
            if selected is None or selected["merged_into_id"] is not None:
                raise ValueError("人员档案不存在或已合并")
            if selected["consent_status"] != "granted":
                raise ValueError("请先确认已获得当事人授权")
            cur.execute(
                """
                SELECT p.id, p.consent_status, m.id AS media_id,
                       p.display_name, m.file_id, m.media_type
                FROM delivery_people p
                JOIN delivery_media m ON m.person_id=p.id
                WHERE p.venue_id=%s AND p.merged_into_id IS NULL AND p.deleted_at IS NULL
                      AND p.consent_status='granted'
                ORDER BY p.id, m.id
                """,
                (selected["venue_id"],),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    _face_models()
    media_by_person = {}
    for row in rows:
        media_by_person.setdefault(row["id"], []).append(row)
    target_rows = media_by_person.get(person_id, [])
    if not target_rows:
        raise ValueError("该档案没有可用于比对的照片或视频")
    selected_rows = target_rows[-MAX_SCAN_MEDIA:]
    scan_limited = len(selected_rows) < len(target_rows)
    remaining = MAX_SCAN_MEDIA - len(selected_rows)
    candidate_ids = sorted(
        (candidate_id for candidate_id in media_by_person if candidate_id != person_id),
        key=lambda candidate_id: media_by_person[candidate_id][-1]["media_id"],
        reverse=True,
    )
    for candidate_id in candidate_ids:
        candidate_rows = media_by_person[candidate_id]
        sample = candidate_rows[-min(3, remaining):] if remaining else []
        if len(sample) < len(candidate_rows):
            scan_limited = True
        selected_rows.extend(sample)
        remaining -= len(sample)
        if remaining <= 0:
            scan_limited = True
            break
    selected_by_person = {}
    for row in selected_rows:
        selected_by_person.setdefault(row["id"], []).append(row)
    features_by_person = {}
    skipped_oversize = 0
    skipped_oversize_by_person = {}
    for candidate_id, media_rows in selected_by_person.items():
        features = []
        for media in media_rows:
            try:
                content = fetch_media(media["file_id"])
            except MediaTooLargeError:
                skipped_oversize += 1
                skipped_oversize_by_person[candidate_id] = (
                    skipped_oversize_by_person.get(candidate_id, 0) + 1
                )
                continue
            features.extend(extract_features(media["media_type"], content))
        features_by_person[candidate_id] = features
    target_features = features_by_person.get(person_id, [])
    if not target_features:
        if skipped_oversize_by_person.get(person_id):
            raise ValueError("该档案的媒体均超过20 MB，无法进行本地人脸比对")
        raise ValueError("该档案的媒体中没有检测到清晰人脸")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM delivery_people
                WHERE venue_id=%s AND consent_status='granted'
                      AND merged_into_id IS NULL AND deleted_at IS NULL
                """,
                (selected["venue_id"],),
            )
            still_authorized = {row["id"] for row in cur.fetchall()}
    finally:
        conn.close()
    if person_id not in still_authorized:
        raise ValueError("比对期间授权已撤回或档案已变更，请刷新后重试")
    candidates = []
    for candidate_id, features in features_by_person.items():
        if candidate_id == person_id or candidate_id not in still_authorized or not features:
            continue
        score = max(
            float(np.dot(target, candidate))
            for target in target_features
            for candidate in features
        )
        candidates.append({
            "person_id": candidate_id,
            "display_name": next(
                row["display_name"] for row in rows if row["id"] == candidate_id
            ),
            "similarity": round(score, 4),
        })
    candidates.sort(key=lambda result: result["similarity"], reverse=True)
    return {
        "person_id": person_id,
        "scanned_media_count": len(selected_rows),
        "skipped_oversize_count": skipped_oversize,
        "scan_limited": scan_limited,
        "candidates": candidates[:10],
        "auto_merged": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage local delivery face-matching models")
    parser.add_argument("command", choices=["download-models", "archive-build-start", "archive-build-finish", "archive-build-status"])
    args = parser.parse_args()
    if args.command == "download-models":
        download_models()
        print(f"Local face models saved under {_model_directory()}")
    else:
        ensure_tables()
        if args.command != "archive-build-status":
            set_archive_build_mode(args.command == "archive-build-start")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                enabled = archive_build_mode(cur)
            print(f"Delivery archive build mode: {'ON (no attendance or reminders)' if enabled else 'OFF (normal attendance)'}")
        finally:
            conn.close()
