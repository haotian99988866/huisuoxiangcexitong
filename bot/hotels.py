"""Hotel recommendations with transactional image uploads and admin audit."""
from datetime import datetime, timezone
import hashlib
import io
import json
import re
from urllib.parse import urlsplit
import warnings

import pymysql
from PIL import Image, ImageOps, UnidentifiedImageError

from db import get_conn

MAX_IMAGE_BYTES = 5 * 1024 * 1024
FIELDS = "id,name,map_url,telegram_username,enabled,revision,updated_at"


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS recommended_hotels (
                id CHAR(32) PRIMARY KEY, name VARCHAR(100) NOT NULL,
                map_url VARCHAR(2048) NOT NULL, telegram_username VARCHAR(32) NOT NULL,
                image MEDIUMBLOB NOT NULL, enabled BOOLEAN NOT NULL,
                revision INT NOT NULL DEFAULT 1, updated_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS hotel_admin_audit (
                request_id CHAR(32) PRIMARY KEY, actor_id BIGINT NOT NULL,
                hotel_id CHAR(32) NOT NULL, request_hash CHAR(64) NOT NULL,
                before_json JSON NOT NULL, after_json JSON NOT NULL,
                created_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
    finally:
        conn.close()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("酒店或操作编号无效")
    return value


def _serialize(row):
    return {**row, "enabled": bool(row["enabled"]),
            "updated_at": row["updated_at"].isoformat() + "+00:00",
            "image_url": f"/hotel-images/{row['id']}?v={row['revision']}"}


def catalog(data, admin=False):
    page = data.get("page", 1)
    if type(page) is not int or not 1 <= page <= 10000:
        raise ValueError("酒店列表页码无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            where = "" if admin else " WHERE enabled=1"
            cur.execute("SELECT COUNT(*) AS total FROM recommended_hotels" + where)
            total = cur.fetchone()["total"]
            cur.execute("SELECT " + FIELDS + " FROM recommended_hotels" + where +
                        " ORDER BY updated_at DESC,id DESC LIMIT 20 OFFSET %s", ((page - 1) * 20,))
            return {"hotels": [_serialize(row) for row in cur.fetchall()], "page": page, "total": total}
    finally:
        conn.close()


def image(hotel_id, admin=False):
    hotel_id = _id(hotel_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT image FROM recommended_hotels WHERE id=%s" + ("" if admin else " AND enabled=1"), (hotel_id,))
            row = cur.fetchone()
            return row["image"] if row else None
    finally:
        conn.close()


def normalize_image(raw, label="门头"):
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"请上传不超过5MB的{label}照片")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"} or source.width * source.height > 20000000:
                    raise ValueError(f"{label}照须为JPG、PNG或WEBP，且不超过2000万像素")
                source.load()
                picture = ImageOps.exif_transpose(source).convert("RGB")
                picture.thumbnail((1600, 1600))
                output = io.BytesIO()
                picture.save(output, format="JPEG", quality=85)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError(f"{label}照片无法读取，请重新选择有效图片") from exc


def save(data, raw_image, actor_id):
    from customer_records import RecordConflict
    hotel_id, request_id = _id(data.get("id")), _id(data.get("request_id"))
    revision = data.get("revision")
    if type(revision) is not int or revision < 0 or data.get("confirmed") is not True:
        raise ValueError("请核对并确认酒店资料")
    name, map_url, username = (data.get(key) for key in ("name", "map_url", "telegram_username"))
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("酒店名称须为1–100个字符")
    if not isinstance(map_url, str) or not 1 <= len(map_url.strip()) <= 2048:
        raise ValueError("请填写有效地图链接")
    map_url = map_url.strip()
    try:
        url = urlsplit(map_url)
        valid_url = (url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
                     and not any(char.isspace() or ord(char) < 32 for char in map_url))
    except ValueError as exc:
        raise ValueError("地图链接格式无效") from exc
    if not valid_url:
        raise ValueError("地图链接必须是完整HTTPS链接，不可含账号密码")
    if not isinstance(username, str):
        raise ValueError("请填写前台Telegram用户名")
    username = username.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        raise ValueError("前台Telegram用户名须为5–32位字母、数字或下划线")
    enabled = data.get("enabled")
    if type(enabled) is not bool:
        raise ValueError("酒店展示状态无效")
    picture = normalize_image(raw_image) if raw_image is not None else None
    content = {"name": name.strip(), "map_url": map_url, "telegram_username": username, "enabled": enabled}
    digest = hashlib.sha256(json.dumps([hotel_id, revision, content,
                                       hashlib.sha256(raw_image).hexdigest() if raw_image is not None else None],
                                      sort_keys=True).encode()).hexdigest()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT " + FIELDS + " FROM recommended_hotels WHERE id=%s FOR UPDATE", (hotel_id,))
            previous = cur.fetchone()
            cur.execute("SELECT actor_id,request_hash FROM hotel_admin_audit WHERE request_id=%s", (request_id,))
            replay = cur.fetchone()
            if replay:
                if replay["actor_id"] != actor_id or replay["request_hash"] != digest:
                    raise RecordConflict("操作编号已用于其他内容，请刷新核对")
                return {"ok": True, "replayed": True}
            if revision != (previous["revision"] if previous else 0):
                raise RecordConflict("酒店资料已更新，请刷新后重新编辑")
            if previous:
                cur.execute("""UPDATE recommended_hotels SET name=%s,map_url=%s,telegram_username=%s,
                    enabled=%s,revision=revision+1,updated_at=%s WHERE id=%s""",
                            (content["name"], map_url, username, enabled, now, hotel_id))
                if picture is not None:
                    cur.execute("UPDATE recommended_hotels SET image=%s WHERE id=%s", (picture, hotel_id))
            else:
                if picture is None:
                    raise ValueError("新增酒店必须上传门头照片")
                cur.execute("""INSERT INTO recommended_hotels
                    (id,name,map_url,telegram_username,enabled,image,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                            (hotel_id, content["name"], map_url, username, enabled, picture, now))
            cur.execute("""INSERT INTO hotel_admin_audit
                (request_id,actor_id,hotel_id,request_hash,before_json,after_json,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                        (request_id, actor_id, hotel_id, digest,
                         json.dumps(previous, default=str), json.dumps({**content, "revision": revision + 1,
                                                                       "image_replaced": picture is not None}), now))
        conn.commit()
        return {"ok": True}
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if exc.args[0] == 1062:
            raise RecordConflict("酒店或操作编号已存在，请用原编号重试核对") from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
