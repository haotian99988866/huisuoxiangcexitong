"""Manually reviewed, time-limited recommendations for the Mingxi pilot."""
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
from pathlib import Path
import re
import subprocess
import tempfile

from PIL import Image

from db import get_conn
from hotels import MAX_IMAGE_BYTES, normalize_image
from customer_records import RecordConflict

VENUE_ID = 2
MAX_MEDIA_BYTES = 20 * 1024 * 1024
FIELDS = "id,venue_id,kind,title,description,starts_at,ends_at,enabled,revision,updated_at,media_type,media_width,media_height"
logger = logging.getLogger(__name__)


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS venue_recommendations (
                id CHAR(32) PRIMARY KEY, venue_id INT NOT NULL,
                kind VARCHAR(16) NOT NULL, title VARCHAR(80) NOT NULL,
                description VARCHAR(1000) NOT NULL, image LONGBLOB NOT NULL,
                media_type VARCHAR(16) NOT NULL DEFAULT 'photo',
                media_width INT NULL, media_height INT NULL,
                starts_at DATETIME NOT NULL, ends_at DATETIME NOT NULL,
                enabled BOOLEAN NOT NULL, revision INT NOT NULL DEFAULT 1,
                updated_at DATETIME NOT NULL,
                INDEX visible_window (venue_id,enabled,ends_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("SHOW COLUMNS FROM venue_recommendations")
            columns = {row["Field"]: row["Type"] for row in cur.fetchall()}
            for name, definition in (("media_type", "VARCHAR(16) NOT NULL DEFAULT 'photo'"),
                                     ("media_width", "INT NULL"), ("media_height", "INT NULL")):
                if name not in columns:
                    cur.execute(f"ALTER TABLE venue_recommendations ADD COLUMN {name} {definition}")
            if columns["image"].lower() != "longblob":
                cur.execute("ALTER TABLE venue_recommendations MODIFY COLUMN image LONGBLOB NOT NULL")
            cur.execute("""CREATE TABLE IF NOT EXISTS venue_recommendation_audit (
                request_id CHAR(32) PRIMARY KEY, actor_id BIGINT NOT NULL,
                recommendation_id CHAR(32) NOT NULL, request_hash CHAR(64) NOT NULL,
                before_json JSON NOT NULL, after_json JSON NOT NULL,
                created_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
    finally:
        conn.close()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("推荐或操作编号无效")
    return value


def _venue(data):
    if type(data.get("venue_id")) is not int or data["venue_id"] != VENUE_ID:
        raise ValueError("当前仅开放名玺水汇推荐试点")
    return VENUE_ID


def _time(value):
    if not isinstance(value, str):
        raise ValueError("请设置有效的推荐开始和结束时间")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or not 2000 <= result.year <= 2100:
            raise ValueError
        return result.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    except ValueError as exc:
        raise ValueError("推荐时间须为带时区的有效时间") from exc


def _serialize(row):
    result = {**row, "enabled": bool(row["enabled"])}
    for field in ("starts_at", "ends_at", "updated_at"):
        result[field] = row[field].isoformat() + "+00:00"
    result["image_url"] = f"/venue-recommendation-images/{row['id']}?v={row['revision']}"
    return result


def catalog(data, admin=False):
    venue_id = _venue(data)
    page = data.get("page", 1)
    if type(page) is not int or not 1 <= page <= 10000:
        raise ValueError("推荐列表页码无效")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    where, args = " WHERE venue_id=%s", [venue_id]
    if not admin:
        where += " AND enabled=1 AND starts_at<=%s AND ends_at>%s"
        args += [now, now]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM venue_recommendations" + where, args)
            total = cur.fetchone()["total"]
            query = "SELECT " + FIELDS + " FROM venue_recommendations" + where + " ORDER BY updated_at DESC,id DESC"
            if admin:
                query += " LIMIT 20 OFFSET %s"
                args += [(page - 1) * 20]
            cur.execute(query, args)
            return {"recommendations": [_serialize(row) for row in cur.fetchall()],
                    "total": total, "page": page, "server_now": now.isoformat() + "+00:00"}
    finally:
        conn.close()


def media(ident, admin=False):
    ident = _id(ident)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            query = "SELECT image,media_type FROM venue_recommendations WHERE id=%s AND venue_id=%s"
            args = [ident, VENUE_ID]
            if not admin:
                query += " AND enabled=1 AND starts_at<=%s AND ends_at>%s"
                args += [now, now]
            cur.execute(query, args)
            row = cur.fetchone()
            return row
    finally:
        conn.close()


def image(ident, admin=False):
    row = media(ident, admin)
    return row["image"] if row else None


def normalize_media(raw, filename):
    extension = Path(filename or "").suffix.lower()
    if extension not in {".mp4", ".mov", ".webm"}:
        picture = normalize_image(raw, label="推荐")
        with Image.open(io.BytesIO(picture)) as source:
            return picture, "photo", source.width, source.height
    if not raw or len(raw) > MAX_MEDIA_BYTES:
        raise ValueError("推荐视频不能为空且不能超过20MB")
    if (extension in {".mp4", ".mov"} and raw[4:8] != b"ftyp") or (
            extension == ".webm" and not raw.startswith(b"\x1a\x45\xdf\xa3")):
        raise ValueError("视频格式无效，请上传MP4、MOV或WebM")
    import imageio_ffmpeg
    import cv2
    with tempfile.TemporaryDirectory(prefix="venue-recommendation-") as directory:
        source, output = Path(directory) / ("source" + extension), Path(directory) / "prepared.mp4"
        source.write_bytes(raw)
        try:
            result = subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
                 "-vf", "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                 "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                 "-fs", str(MAX_MEDIA_BYTES + 1), str(output)],
                capture_output=True, timeout=90,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("视频处理超时，请压缩或缩短视频后重试") from exc
        if result.returncode or not output.is_file():
            logger.warning("Recommendation video conversion failed: %s", result.stderr[-2000:].decode(errors="replace"))
            raise ValueError("视频损坏或无法转换，请重新选择有效视频")
        if output.stat().st_size > MAX_MEDIA_BYTES:
            raise ValueError("处理后视频超过20MB，请压缩或缩短视频后重试")
        capture = cv2.VideoCapture(str(output))
        try:
            readable, frame = capture.read()
            if not readable or frame is None:
                raise ValueError("视频无法播放，请重新选择")
            height, width = frame.shape[:2]
        finally:
            capture.release()
        return output.read_bytes(), "video", width, height


def save(data, raw_image, actor_id, filename=None):
    venue_id = _venue(data)
    ident, request_id = _id(data.get("id")), _id(data.get("request_id"))
    revision = data.get("revision")
    if type(revision) is not int or revision < 0 or data.get("confirmed") is not True:
        raise ValueError("请核实推荐资料及照片使用许可后确认保存")
    kind, title, description = (data.get(key) for key in ("kind", "title", "description"))
    if not isinstance(kind, str) or kind not in {"activity", "newcomer"}:
        raise ValueError("请选择活动或已确认的新人推荐")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
        raise ValueError("推荐标题须为1–80个字符")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1000:
        raise ValueError("请填写推荐说明及适用条件，最多1000字")
    starts_at, ends_at = _time(data.get("starts_at")), _time(data.get("ends_at"))
    if starts_at >= ends_at:
        raise ValueError("推荐结束时间须晚于开始时间")
    enabled = data.get("enabled")
    if type(enabled) is not bool:
        raise ValueError("推荐展示状态无效")
    prepared = normalize_media(raw_image, filename) if raw_image is not None else None
    content = {"venue_id": venue_id, "kind": kind, "title": title.strip(), "description": description.strip(),
               "starts_at": starts_at, "ends_at": ends_at, "enabled": enabled}
    digest = hashlib.sha256(json.dumps([ident, revision, content,
                                       hashlib.sha256(raw_image).hexdigest() if raw_image is not None else None],
                                      default=str, sort_keys=True).encode()).hexdigest()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT " + FIELDS + " FROM venue_recommendations WHERE id=%s FOR UPDATE", (ident,))
            previous = cur.fetchone()
            cur.execute("SELECT actor_id,request_hash FROM venue_recommendation_audit WHERE request_id=%s", (request_id,))
            replay = cur.fetchone()
            if replay:
                if replay["actor_id"] != actor_id or replay["request_hash"] != digest:
                    raise RecordConflict("操作编号已用于其他内容，请刷新核对")
                return {"ok": True, "replayed": True}
            if revision != (previous["revision"] if previous else 0):
                raise RecordConflict("推荐已更新，请刷新后重新编辑")
            if enabled and ends_at <= now:
                raise ValueError("已过期推荐请先调整有效期，再重新上架")
            values = (venue_id, kind, content["title"], content["description"], starts_at, ends_at, enabled, now)
            if previous:
                cur.execute("""UPDATE venue_recommendations SET venue_id=%s,kind=%s,title=%s,description=%s,
                    starts_at=%s,ends_at=%s,enabled=%s,updated_at=%s,revision=revision+1 WHERE id=%s""",
                            (*values, ident))
                if prepared is not None:
                    cur.execute("UPDATE venue_recommendations SET image=%s,media_type=%s,media_width=%s,media_height=%s WHERE id=%s",
                                (*prepared, ident))
            else:
                if prepared is None:
                    raise ValueError("新增推荐必须上传照片或视频")
                cur.execute("""INSERT INTO venue_recommendations
                    (venue_id,kind,title,description,starts_at,ends_at,enabled,updated_at,id,image,media_type,media_width,media_height)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (*values, ident, *prepared))
            cur.execute("""INSERT INTO venue_recommendation_audit
                (request_id,actor_id,recommendation_id,request_hash,before_json,after_json,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                        (request_id, actor_id, ident, digest, json.dumps(previous, default=str),
                         json.dumps({**content, "revision": revision + 1}, default=str), now))
        conn.commit()
        return {"ok": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
