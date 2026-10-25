import logging
import os
import re
import tempfile
import threading
from datetime import date

import pymysql
import requests
from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, request, send_file, stream_with_context
from flask_cors import CORS
from PIL import Image, UnidentifiedImageError
from waitress import serve
from urllib.parse import quote

MIN_AGE = 18
from delivery_age import extract_work_no, parse_price_table, price_for_work_no
from image_hash import ImageDeduper, ensure_columns as ensure_image_hash_columns
from image_cache import ImageCache
from video_cache import VideoCache
from music import ensure_tables as ensure_music_tables, catalog as music_catalog, audio_file as music_audio_file
from tenants import load_tenant, public_config, TenantUnavailable
from duty_shift import SHIFT_VENUE_IDS, current_shift_window
from visit_stats import ensure_table, record_visit, verify_init_data
from movies import (
    ensure_tables as ensure_movie_tables,
    movie_catalog,
    movie_episodes,
    thumb_file_id as movie_thumb_file_id,
)
from technician_photo_ocr import (
    extract_labeled_technician_code,
    extract_technician_category,
    extract_technician_profile,
)

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

default_cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", ",".join(default_cors_origins)).split(",")
    if origin.strip()
]
CORS(
    app,
    resources={
        r"/webhook": {"origins": cors_origins},
        r"/photos/*": {"origins": cors_origins},
        r"/ktv-videos/*": {"origins": cors_origins},
        r"/delivery-media/*": {"origins": cors_origins},
        r"/music-audio/*": {"origins": cors_origins},
    },
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)
image_cache = ImageCache(os.path.join(
    os.getenv("LOCALAPPDATA") or tempfile.gettempdir(), "Huisuo", "image-cache"
))
video_cache = VideoCache(os.path.join(
    os.getenv("LOCALAPPDATA") or tempfile.gettempdir(), "Huisuo", "ktv-video-cache"
))

_dihao_categories_by_prefix = {
    "0": "0牌",
    "1": "1牌",
    "2": "2牌",
    "6": "6牌艺人",
    "7": "7牌公主",
    "8": "8牌佳丽",
    "9": "9牌贵妃",
}
_dihao_category_labels = {
    "艺人": "6牌艺人",
    "公主": "7牌公主",
    "佳丽": "8牌佳丽",
    "贵妃": "9牌贵妃",
}

def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "venue_db"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


@app.before_request
def tenant_context():
    g.tenant = None
    if request.method == "OPTIONS":
        return None
    header, query = request.headers.get("X-Tenant"), request.args.get("tenant")
    if header is not None and query is not None and header != query:
        return jsonify({"error": "租户入口参数冲突"}), 400
    slug = header if header is not None else query
    if slug is None:
        return None
    try:
        g.tenant = load_tenant(slug)
    except TenantUnavailable as exc:
        return jsonify({"error": str(exc)}), 403
    except (ValueError, TypeError, KeyError, pymysql.MySQLError):
        logger.exception("Failed to load tenant configuration")
        return jsonify({"error": "租户配置暂不可用，请联系服务提供方"}), 503
    if request.endpoint == "webhook":
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        allowed = {"tenant_config", "get_venues", "get_venue_detail", "get_technicians",
                   "get_movies", "get_movie_series"}
        if not isinstance(body.get("action"), str) or body["action"] not in allowed:
            return jsonify({"error": "此入口仅提供会所与观影展示，不开放账户或充值"}), 403
        if body.get("action") in {"get_venue_detail", "get_technicians"}:
            try:
                venue_id = int(body.get("id", body.get("venue_id")))
            except (ValueError, TypeError):
                return jsonify({"error": "A valid venue id is required"}), 400
            if venue_id not in g.tenant["venue_ids"]:
                return jsonify({"error": "此入口未开放该会所"}), 403
    elif request.endpoint == "music_audio":
        return jsonify({"error": "此租户入口暂未开放音乐"}), 403
    elif request.endpoint in {"technician_photo_image", "ktv_video", "delivery_media"}:
        if request.endpoint == "delivery_media" and not DELIVERY_VENUE_IDS.intersection(g.tenant["venue_ids"]):
            return jsonify({"error": "此入口未开放外送"}), 403
        table, field = {
            "technician_photo_image": ("technician_photos", "photo_id"),
            "ktv_video": ("ktv_room_videos", "video_id"),
            "delivery_media": ("delivery_media", "media_id"),
        }[request.endpoint]
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute(f"SELECT venue_id FROM {table} WHERE id=%s", (request.view_args[field],))
                row = cur.fetchone()
            if row is None or row["venue_id"] not in g.tenant["venue_ids"]:
                return jsonify({"error": "此入口未开放该会所资料"}), 403
        except pymysql.MySQLError:
            logger.exception("Failed to check tenant media scope")
            return jsonify({"error": "资料服务暂不可用"}), 503
        finally:
            if conn is not None:
                conn.close()
    return None


@app.after_request
def tenant_cache_policy(response):
    body = request.get_json(silent=True) if request.endpoint == "webhook" else None
    admin_request = isinstance(body, dict) and isinstance(body.get("action"), str) and body["action"] in {"admin_dashboard", "admin_tenant_save"}
    music_request = isinstance(body, dict) and isinstance(body.get("action"), str) and body["action"].startswith("music_")
    if admin_request or music_request or getattr(g, "tenant", None) is not None or "tenant" in request.args or "X-Tenant" in request.headers:
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("X-Tenant")
    return response


def parse_venue(venue):
    venue["search_tags"] = venue["search_tags"].split(",") if venue["search_tags"] else []
    technician_codes = venue.get("technician_codes") or ""
    venue["technician_list"] = [
        code.strip() for code in technician_codes.split(",") if code.strip()
    ]
    venue["on_duty_count"] = len(venue["technician_list"])
    venue["description"] = None
    return venue


def _underage_technician_codes(photos):
    underage_codes = set()
    for photo in photos:
        technician_code = photo["technician_code"] or (
            extract_labeled_technician_code(photo["ocr_text"])
        )
        if technician_code is None:
            continue
        profile = extract_technician_profile(
            [photo["ocr_text"] or ""],
            technician_code,
        )
        age = profile.get("age")
        if age and age.isdigit() and int(age) < 18:
            underage_codes.add(technician_code)
    return underage_codes


def _confirmed_adult_technician_codes(photos):
    adult_codes = set()
    for photo in photos:
        technician_code = photo["technician_code"] or (
            extract_labeled_technician_code(photo["ocr_text"])
        )
        if technician_code is None:
            continue
        profile = extract_technician_profile(
            [photo["ocr_text"] or ""],
            technician_code,
        )
        age = profile.get("age")
        if age and age.isdigit() and int(age) >= 18:
            adult_codes.add(technician_code)
    return adult_codes


def _dihao_category_for_photo(technician_code, ocr_text):
    if technician_code == "韩国08":
        return "韩国牌"
    category_labels = set()
    for line in (ocr_text or "").splitlines():
        match = re.fullmatch(
            r"\s*(艺人|公主|佳丽|贵妃)\s*[:：;；]\s*\d{2,4}\s*",
            line,
        )
        if match:
            category_labels.add(_dihao_category_labels[match.group(1)])
    if len(category_labels) == 1:
        return next(iter(category_labels))
    if len(category_labels) > 1 or not technician_code:
        return None
    return _dihao_categories_by_prefix.get(str(technician_code)[0])


@app.get("/photos/<int:photo_id>/image")
def technician_photo_image(photo_id):
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.file_id, p.venue_id, p.status, p.technician_code,
                   p.duty_date,
                   p.ocr_text,
                   EXISTS (
                       SELECT 1
                       FROM attendance_daily a
                       WHERE a.venue_id = p.venue_id
                             AND a.duty_date = p.duty_date
                             AND FIND_IN_SET(p.technician_code, a.technician_codes) > 0
                   ) AS is_on_roster
            FROM technician_photos p
            WHERE p.id = %s
            """,
            (photo_id,),
        )
        photo = cur.fetchone()
        underage_codes = set()
        if photo is not None and photo["venue_id"] == 5:
            cur.execute(
                """
                SELECT technician_code, ocr_text
                FROM technician_photos
                WHERE venue_id = %s AND duty_date = %s
                """,
                (photo["venue_id"], photo["duty_date"]),
            )
            underage_codes = _underage_technician_codes(cur.fetchall())
        adult_codes = set()
        if photo is not None and photo["venue_id"] == 6:
            cur.execute(
                """
                SELECT technician_code, ocr_text
                FROM technician_photos
                WHERE venue_id = %s AND duty_date = %s
                """,
                (photo["venue_id"], photo["duty_date"]),
            )
            adult_codes = _confirmed_adult_technician_codes(cur.fetchall())
    except pymysql.MySQLError:
        logger.exception("Failed to look up technician photo %s", photo_id)
        return jsonify({"error": "Database request failed"}), 500
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()

    if photo is None:
        return jsonify({"error": "Photo not found"}), 404

    is_matched_roster_photo = (
        photo["status"] == "matched"
        and photo["technician_code"] is not None
        and photo["is_on_roster"]
    )
    is_labeled_kaidi_photo = (
        photo["venue_id"] == 4
        and photo["status"] in {"matched", "pending_review"}
        and extract_labeled_technician_code(photo["ocr_text"]) is not None
    )
    is_labeled_atlantic_photo = (
        photo["venue_id"] == 5
        and photo["status"] in {"matched", "pending_review"}
        and extract_labeled_technician_code(photo["ocr_text"]) is not None
        and (
            photo["technician_code"]
            or extract_labeled_technician_code(photo["ocr_text"])
        ) not in underage_codes
    )
    is_labeled_dihao_photo = (
        photo["venue_id"] == 6
        and photo["status"] in {"matched", "pending_review"}
        and (
            photo["technician_code"]
            or extract_labeled_technician_code(photo["ocr_text"])
        ) in adult_codes
    )
    if (
        not is_matched_roster_photo
        and not is_labeled_kaidi_photo
        and not is_labeled_atlantic_photo
        and not is_labeled_dihao_photo
    ):
        return jsonify({"error": "Photo not found"}), 404

    return _telegram_image(photo["file_id"])


DELIVERY_VENUE_IDS = {7, 8, 9, 10}


APPLE_CATEGORY_KEYWORDS = (("小苹果", "small"), ("红苹果", "red"))


def _apple_category(text):
    for keyword, category in APPLE_CATEGORY_KEYWORDS:
        if keyword in text:
            return category
    return None


def classify_apple_rows(rows):
    """按文字判断小苹果/红苹果。
    频道里的纯文字消息是分类标记，管它上面（更早发的）还没分类的内容；
    图片/视频自己的说明文字里有关键词时优先；找不到类别的返回 None。"""
    categories, _ = _classify_and_markers(rows)
    return categories


def apple_price_tables(rows):
    """每条图片/视频上面紧挨着的文字标记里的价目表：{行id: {牌: 价格}}。"""
    _, markers = _classify_and_markers(rows)
    return {row_id: parse_price_table(text) for row_id, text in markers.items()}


def _classify_and_markers(rows):
    group_captions = {}
    for row in rows:
        caption = (row["caption"] or "").strip()
        if row["media_group_id"] and caption and row["media_type"] != "text":
            group_captions.setdefault(row["media_group_id"], caption)

    categories = {}
    markers = {}
    next_marker = None
    next_marker_text = ""
    for row in sorted(rows, key=lambda r: r["message_id"], reverse=True):
        if row["media_type"] == "text":
            next_marker = _apple_category(row["caption"] or "") or next_marker
            next_marker_text = row["caption"] or next_marker_text
            continue
        caption = (row["caption"] or "").strip() or group_captions.get(row["media_group_id"], "")
        categories[row["id"]] = _apple_category(caption) or next_marker
        markers[row["id"]] = next_marker_text
    return categories, markers


def _message_link(row):
    channel_id = str(row.get("channel_id") or "")
    if not channel_id or not row.get("message_id"):
        return None
    numeric = channel_id[4:] if channel_id.startswith("-100") else channel_id.lstrip("-")
    return f"https://t.me/c/{numeric}/{row['message_id']}"


def build_delivery_posts(rows, categories=None):
    """rows 按 message_id 倒序。带文字说明或图片+视频的相册合并成一条，其余每个媒体单独一条。"""
    categories = categories or {}
    rows = [row for row in rows if row["media_type"] != "text"]
    group_captions = {}
    group_types = {}
    for row in rows:
        group_id = row["media_group_id"]
        if not group_id:
            continue
        group_types.setdefault(group_id, set()).add(row["media_type"])
        caption = (row["caption"] or "").strip()
        if caption and not group_captions.get(group_id):
            group_captions[group_id] = caption
    # 图片和视频一起发送视为同一个人，即使没有文字也合并成一条
    for group_id, types in group_types.items():
        if "video" in types and "photo" in types:
            group_captions.setdefault(group_id, "")

    posts = []
    grouped = {}
    for row in rows:
        media = {"id": row["id"], "type": row["media_type"]}
        group_id = row["media_group_id"]
        if group_id in group_captions:
            post = grouped.get(group_id)
            if post is None:
                post = {
                    "id": f"g{row['id']}",
                    "description": group_captions[group_id],
                    "category": categories.get(row["id"]),
                    "age": None,
                    "media": [],
                    "_hashes": [],
                }
                grouped[group_id] = post
                posts.append(post)
            post["media"].insert(0, media)
            post["link"] = _message_link(row) or post.get("link")
            if row.get("image_hash"):
                post["_hashes"].append(row["image_hash"])
            post["age"] = _min_age(post["age"], row.get("age"))
            continue
        posts.append(
            {
                "id": f"m{row['id']}",
                "description": (row["caption"] or "").strip(),
                "category": categories.get(row["id"]),
                "age": row.get("age"),
                "media": [media],
                "link": _message_link(row),
                "_ocr": row.get("ocr_text"),
                "_hashes": [row["image_hash"]] if row.get("image_hash") else [],
            }
        )
    return dedupe_posts(posts)


def _post_key(post):
    text = "".join((post.get("description") or "").split())
    if text:
        return ("d", text, len(post["media"]))
    ocr = "".join((post.get("_ocr") or "").split())
    return ("o", ocr, len(post["media"])) if len(ocr) >= 12 else None


def _dedupe_photos_by_hash(photos):
    """相同图片(指纹相近)只留最早一张;无指纹的保留。"""
    images = ImageDeduper()
    kept = []
    for photo in photos:
        image_hash = photo.get("image_hash")
        if image_hash and images.key_for(image_hash, photo["id"]) != photo["id"]:
            continue
        kept.append(photo)
    return kept


def dedupe_posts(posts):
    """内容相同的帖子只留最新一条(rows 为倒序,先出现的最新)。先比图片指纹,再比文字。"""
    seen = {}
    images = ImageDeduper()
    kept_by_id = {}
    unique = []
    for post in posts:
        own = id(post)
        original = None
        for image_hash in post.get("_hashes", []):
            key = images.key_for(image_hash, own)
            if key != own:
                original = kept_by_id.get(key)
        key = _post_key(post)
        if original is None and key is not None:
            original = seen.get(key)
            if original is None:
                seen[key] = post
        post.pop("_ocr", None)
        post.pop("_hashes", None)
        if original is not None:
            if original.get("age") is None:
                original["age"] = post.get("age")
            continue
        kept_by_id[own] = post
        unique.append(post)
    return unique


def _min_age(current, new):
    if current is None:
        return new
    if new is None:
        return current
    return min(current, new)


def attach_delivery_prices(rows, posts, price_tables):
    """工号第一位对应价目表里的牌，给每条内容加上 work_no / price。"""
    row_by_id = {row["id"]: row for row in rows}
    for post in posts:
        post["work_no"] = None
        post["price"] = None
        for media in post["media"]:
            row = row_by_id.get(media["id"])
            if row is None:
                continue
            work_no = extract_work_no(row.get("ocr_text"), row.get("caption"))
            if work_no and not post["work_no"]:
                post["work_no"] = work_no
                post["price"] = price_for_work_no(
                    work_no, price_tables.get(media["id"])
                )
    return posts


def filter_adult_posts(posts):
    """只保留识别出年龄且满 18 周岁的内容；年龄未知或不满 18 的一律不展示。"""
    return [
        post for post in posts
        if post.get("age") is not None and post["age"] >= MIN_AGE
    ]


@app.get("/ktv-videos/<int:video_id>")
def ktv_video(video_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_id FROM ktv_room_videos WHERE id = %s AND venue_id = 3",
                (video_id,),
            )
            row = cur.fetchone()
    except pymysql.MySQLError:
        logger.exception("Failed to look up KTV video %s", video_id)
        return jsonify({"error": "Database request failed"}), 500
    finally:
        if conn is not None:
            conn.close()
    if row is None:
        return jsonify({"error": "Video not found"}), 404
    try:
        cached = video_cache.get(row["file_id"])
        if cached is not None:
            response = send_file(
                cached, mimetype=MEDIA_CONTENT_TYPES[cached.suffix],
                conditional=True, max_age=300,
            )
            response.headers["Cache-Control"] = "private, max-age=300"
            response.headers["Accept-Ranges"] = "bytes"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Media-Cache"] = "HIT"
            return response
    except OSError:
        logger.exception("Cannot serve cached KTV video %s; using Telegram stream", video_id)
    response = app.make_response(_proxy_telegram_media(row["file_id"]))
    response.headers["X-Media-Cache"] = "MISS"
    return response


def warm_ktv_video_cache():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, file_id FROM ktv_room_videos WHERE venue_id = 3 ORDER BY id DESC")
            rows = cur.fetchall()
    finally:
        conn.close()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Cannot warm KTV videos: Telegram is not configured")
        return
    reserved = 0
    for row in rows:
        try:
            cached = video_cache.get(row["file_id"])
            if cached is not None:
                reserved += cached.stat().st_size
                continue
            with requests.get(
                f"https://api.telegram.org/bot{token}/getFile",
                params={"file_id": row["file_id"]}, timeout=(5, 15),
            ) as info:
                info.raise_for_status()
                payload = info.json()
            result = payload.get("result") or {}
            path, size = result.get("file_path"), result.get("file_size")
            if not payload.get("ok") or not path or not isinstance(size, int):
                raise ValueError("Telegram did not return a video path and size")
            if size <= 0 or size > video_cache.max_file_bytes:
                raise ValueError("Video exceeds cache file size limit or has no size")
            if reserved + size > video_cache.max_bytes:
                logger.warning("KTV video cache budget reached; remaining videos use Telegram")
                break
            with requests.get(
                f"https://api.telegram.org/file/bot{token}/{quote(path, safe='/')}",
                stream=True, timeout=(5, 30),
            ) as upstream:
                upstream.raise_for_status()
                video_cache.store(
                    row["file_id"], upstream.iter_content(chunk_size=256 * 1024),
                    os.path.splitext(path)[1].lower(), size,
                )
            reserved += size
            logger.info("KTV video %s cached (%s bytes)", row["id"], size)
        except (requests.RequestException, ValueError, OSError) as exc:
            logger.error("Cannot cache KTV video %s (%s); will retry", row["id"], type(exc).__name__)


def maintain_ktv_video_cache(stop):
    while not stop.is_set():
        try:
            warm_ktv_video_cache()
        except pymysql.MySQLError:
            logger.exception("Cannot query KTV videos for cache warmup; will retry")
        if stop.wait(60):
            break


@app.get("/movie-thumb/<int:message_id>")
def movie_thumb(message_id):
    try:
        file_id = movie_thumb_file_id(message_id)
    except pymysql.MySQLError:
        logger.exception("Failed to look up movie thumb %s", message_id)
        return jsonify({"error": "Database request failed"}), 500
    if not file_id:
        return jsonify({"error": "Thumbnail not found"}), 404
    return _telegram_image(file_id)


@app.get("/music-audio/<int:track_id>")
def music_audio(track_id):
    try:
        result = music_audio_file(track_id)
        if result is None:
            return jsonify({"error": "歌曲不存在或已下架"}), 404
        path, mimetype = result
        response = send_file(path, mimetype=mimetype, conditional=True, max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except (ValueError, OSError):
        logger.exception("Local music file unavailable")
        return jsonify({"error": "音频文件暂不可用，请联系管理员"}), 503
    except pymysql.MySQLError:
        logger.exception("Music audio lookup failed")
        return jsonify({"error": "音乐服务暂不可用"}), 503


@app.get("/delivery-media/<int:media_id>")
def delivery_media(media_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_id, media_type FROM delivery_media WHERE id = %s",
                (media_id,),
            )
            row = cur.fetchone()
    except pymysql.MySQLError:
        logger.exception("Failed to look up delivery media %s", media_id)
        return jsonify({"error": "Database request failed"}), 500
    finally:
        if conn is not None:
            conn.close()
    if row is None:
        return jsonify({"error": "Media not found"}), 404
    if row["media_type"] == "photo":
        return _telegram_image(row["file_id"])
    return _proxy_telegram_media(row["file_id"])


def _telegram_image(file_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return jsonify({"error": "Image service is not configured"}), 503

    def download():
        with requests.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id}, timeout=(5, 15),
        ) as info:
            info.raise_for_status()
            payload = info.json()
        path = (payload.get("result") or {}).get("file_path")
        if not payload.get("ok") or not path:
            raise ValueError("Telegram did not return an image path")
        with requests.get(
            f"https://api.telegram.org/file/bot{token}/{quote(path, safe='/')}",
            timeout=(5, 30),
        ) as upstream:
            upstream.raise_for_status()
            return upstream.content

    try:
        data, content_type = image_cache.get(
            file_id, download, thumbnail=request.args.get("thumb") == "1"
        )
    except (requests.RequestException, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        logger.error("Failed to retrieve Telegram image (%s)", type(exc).__name__)
        return jsonify({"error": "Image service is temporarily unavailable"}), 502
    except OSError:
        logger.exception("Failed to read, process or cache image")
        return jsonify({"error": "Image cache is temporarily unavailable"}), 503
    response = Response(data, mimetype=content_type)
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.add_etag()
    return response.make_conditional(request)


MEDIA_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
}


def _proxy_telegram_media(file_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return jsonify({"error": "Video service is not configured"}), 503
    try:
        info = requests.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=(5, 15),
        ).json()
        file_path = (info.get("result") or {}).get("file_path")
        if not info.get("ok") or not file_path:
            return jsonify({"error": "Video is unavailable (file too large)"}), 502
        upstream_headers = {}
        range_header = request.headers.get("Range")
        if range_header and re.fullmatch(r"bytes=\d*-\d*", range_header):
            upstream_headers["Range"] = range_header
        upstream = requests.get(
            f"https://api.telegram.org/file/bot{token}/{quote(file_path, safe='/')}",
            headers=upstream_headers,
            stream=True,
            timeout=(5, 30),
        )
        if upstream.status_code not in (200, 206):
            upstream.close()
            return jsonify({"error": "Video is unavailable"}), 502
    except (requests.RequestException, ValueError) as exc:
        logger.error("Failed to retrieve Telegram media (%s)", type(exc).__name__)
        return jsonify({"error": "Video service is temporarily unavailable"}), 502

    extension = os.path.splitext(file_path)[1].lower()
    content_type = MEDIA_CONTENT_TYPES.get(extension, "video/mp4")
    def stream():
        try:
            yield from upstream.iter_content(chunk_size=64 * 1024)
        finally:
            upstream.close()

    response = Response(
        stream_with_context(stream()),
        status=upstream.status_code,
        mimetype=content_type,
    )
    for header in ("Content-Length", "Content-Range"):
        if upstream.headers.get(header):
            response.headers[header] = upstream.headers[header]
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.call_on_close(upstream.close)
    return response


@app.post("/webhook")
def webhook():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    action = data.get("action")
    if not isinstance(action, str):
        return jsonify({"error": "A valid action is required"}), 400
    if action in {"admin_dashboard", "admin_tenant_save"}:
        from admin_console import authenticate, allowed_admin, dashboard, is_tenant_session, save_from_request
        from tenants import TenantConflict
        init_data = data.get("init_data")
        user = authenticate(init_data, os.getenv("TELEGRAM_BOT_TOKEN"))
        if user is None:
            return jsonify({"error": "请从 Telegram 重新打开主入口，以验证管理员身份"}), 401
        if not allowed_admin(user) or is_tenant_session(init_data):
            return jsonify({"error": "仅平台管理员可从主入口访问管理台"}), 403
        try:
            if action == "admin_tenant_save":
                save_from_request(data, user["id"])
                return jsonify({"ok": True})
            return jsonify(dashboard())
        except TenantConflict as exc:
            return jsonify({"error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.IntegrityError:
            return jsonify({"error": "入口标识已存在，请更换标识或刷新配置"}), 409
        except pymysql.MySQLError:
            logger.exception("Admin console database operation failed")
            return jsonify({"error": "管理服务暂不可用，请稍后重试"}), 503
    if action == "tenant_config":
        return jsonify({"tenant": public_config(g.tenant) if g.tenant else None})
    if action == "get_music":
        try:
            response = jsonify({"tracks": music_catalog()})
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Music catalog unavailable")
            return jsonify({"error": "音乐列表暂不可用，请稍后重试"}), 503
    if action in {"music_profile", "music_update", "music_playlist_create",
                  "music_playlist_save", "music_playlist_delete", "music_shared"}:
        from admin_console import authenticate, is_tenant_session
        from music_social import account as music_account, shared_playlist, PlaylistConflict
        init_data = data.get("init_data")
        if isinstance(init_data, str) and is_tenant_session(init_data):
            return jsonify({"error": "租户入口不开放音乐"}), 403
        user = None if action == "music_shared" else authenticate(init_data, os.getenv("TELEGRAM_BOT_TOKEN"))
        if action != "music_shared" and user is None:
            return jsonify({"error": "请从 Telegram 重新打开小程序，以保存你的音乐数据"}), 401
        try:
            result = shared_playlist(data.get("token")) if action == "music_shared" else music_account(user["id"], action, data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except PlaylistConflict as exc:
            return jsonify({"error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Music account database operation failed")
            return jsonify({"error": "音乐账户服务暂不可用，请稍后重试"}), 503
    if action in {"wallet_account", "wallet_submit"}:
        from wallet import account, submit
        init_data = data.get("init_data")
        user = (
            verify_init_data(init_data, os.getenv("TELEGRAM_BOT_TOKEN"))
            if isinstance(init_data, str) else None
        )
        if not isinstance(user, dict) or int(user["id"]) <= 0:
            return jsonify({"error": "请从 Telegram 重新打开小程序后操作"}), 401
        try:
            if action == "wallet_submit":
                deposit_id = submit(int(user["id"]), data.get("txid"), data.get("amount"))
                return jsonify({"id": deposit_id})
            from visit_stats import admin_ids
            return jsonify({**account(int(user["id"])), "can_manage": int(user["id"]) in admin_ids()})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("余额服务数据库操作失败")
            return jsonify({"error": "余额服务暂不可用，请稍后重试"}), 503
    if action == "track_visit":
        try:
            user = verify_init_data(
                data.get("init_data"), os.getenv("TELEGRAM_BOT_TOKEN")
            )
            if user:
                record_visit(user)
        except Exception:
            logger.exception("记录访问失败")
        return jsonify({"ok": True})

    if action in {"get_movies", "get_movie_series"}:
        try:
            if action == "get_movies":
                category = str(data.get("category") or "").strip()[:50] or None
                query = str(data.get("query") or "").strip()[:50] or None
                return jsonify(movie_catalog(category, query))
            series = str(data.get("series") or "").strip()[:255]
            if not series:
                return jsonify({"error": "A series name is required"}), 400
            return jsonify({"episodes": movie_episodes(series)})
        except pymysql.MySQLError:
            logger.exception("Failed to load movies")
            return jsonify({"error": "Database request failed"}), 500

    if action not in {"get_venues", "get_venue_detail", "get_technicians"}:
        return jsonify({"error": "Unknown action"}), 400

    venue_id = None
    if action in {"get_venue_detail", "get_technicians"}:
        try:
            venue_id = int(data.get("id", data.get("venue_id")))
            if venue_id < 1:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "A valid venue id is required"}), 400

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if action == "get_venues":
            cur.execute(
                """
                SELECT v.id, v.name, v.address, v.phone, v.business_hours,
                       v.map_url, v.cover_image, v.search_tags,
                       v.technician_codes, v.status,
                       activity.message_date AS last_channel_message_at
                FROM venues v
                LEFT JOIN venue_channel_activity activity
                    ON activity.venue_id = v.id
                WHERE v.status = 'active'
                ORDER BY v.id
                """
            )
            venues = [parse_venue(venue) for venue in cur.fetchall()]
            if g.tenant:
                venues = [venue for venue in venues if venue["id"] in g.tenant["venue_ids"]]
            for venue in venues:
                message_date = venue["last_channel_message_at"]
                venue["last_channel_message_at"] = (
                    message_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if message_date
                    else None
                )
            today = date.today()
            shift_start, shift_end, shift_name = current_shift_window()

            for venue in venues:
                venue["current_shift"] = (
                    shift_name
                    if venue["id"] in SHIFT_VENUE_IDS | DELIVERY_VENUE_IDS
                    else None
                )
                if venue["id"] == 3:
                    cur.execute(
                        """
                        SELECT id, room_number, caption
                        FROM ktv_room_videos
                        WHERE venue_id = 3
                        ORDER BY room_number IS NULL, room_number, message_id
                        """
                    )
                    venue["ktv_videos"] = cur.fetchall()
                    cur.execute(
                        """
                        SELECT content
                        FROM ktv_channel_texts
                        WHERE venue_id = 3
                        ORDER BY message_id DESC
                        LIMIT 20
                        """
                    )
                    venue["ktv_texts"] = [r["content"] for r in cur.fetchall()]
                if venue["id"] in DELIVERY_VENUE_IDS:
                    cur.execute(
                        """
                        SELECT id, message_id, channel_id, media_group_id, media_type, caption, age, ocr_text,
                               image_hash
                        FROM delivery_media
                        WHERE venue_id = %s
                              AND created_at >= %s AND created_at < %s
                        ORDER BY message_id DESC
                        LIMIT 200
                        """,
                        (venue["id"], shift_start, shift_end),
                    )
                    delivery_rows = cur.fetchall()
                    is_apple = venue["id"] == 10
                    posts = build_delivery_posts(
                        delivery_rows,
                        classify_apple_rows(delivery_rows) if is_apple else None,
                    )
                    if is_apple:
                        attach_delivery_prices(
                            delivery_rows, posts, apple_price_tables(delivery_rows)
                        )
                    venue["delivery_posts"] = (
                        filter_adult_posts(posts) if shift_name == "晚班" else []
                    )
                if venue["id"] in SHIFT_VENUE_IDS:
                    cur.execute(
                        """
                        SELECT duty_date, technician_codes, duty_shift
                        FROM attendance_daily
                        WHERE venue_id = %s
                              AND created_at >= %s AND created_at < %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                        """,
                        (venue["id"], shift_start, shift_end),
                    )
                else:
                    cur.execute(
                        """
                        SELECT duty_date, technician_codes, duty_shift
                        FROM attendance_daily
                        WHERE venue_id = %s AND duty_date = %s
                        ORDER BY FIELD(duty_shift, '晚班', '早班')
                        LIMIT 1
                        """,
                        (venue["id"], today),
                    )
                attendance = cur.fetchone()
                codes = attendance["technician_codes"] if attendance else None
                venue["technician_list"] = [
                    code.strip() for code in codes.split(",") if code.strip()
                ] if codes else []
                venue["duty_shift_today"] = (
                    attendance["duty_shift"] if attendance else None
                )
                venue["on_duty_count"] = len(venue["technician_list"])
                venue["technician_photos"] = {}
                venue["technician_profiles"] = {}
                venue["attendance_groups"] = []
                venue["technician_categories"] = []
                if attendance:
                    if venue["id"] == 2:
                        cur.execute(
                            """
                            SELECT group_type, group_name, technician_codes
                            FROM attendance_groups
                            WHERE venue_id = %s AND duty_date = %s
                            ORDER BY sort_order, id
                            """,
                            (venue["id"], attendance["duty_date"]),
                        )
                        venue["attendance_groups"] = [
                            {
                                **group,
                                "technician_codes": [
                                    code.strip()
                                    for code in (group["technician_codes"] or "").split(",")
                                    if code.strip()
                                ],
                            }
                            for group in cur.fetchall()
                        ]

                if attendance or venue["id"] in {4, 5, 6}:
                    if venue["id"] in {4, 5, 6}:
                        cur.execute(
                            """
                            SELECT id, technician_code, channel_id, msg_id,
                                   ocr_text, file_id, status, image_hash
                            FROM technician_photos
                            WHERE venue_id = %s
                                  AND created_at >= %s AND created_at < %s
                            ORDER BY id
                            """,
                            (venue["id"], shift_start, shift_end),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT id, technician_code, channel_id, msg_id, ocr_text, image_hash
                            FROM technician_photos p
                            WHERE venue_id = %s
                                  AND status = 'matched'
                                  AND created_at >= %s AND created_at < %s
                                  AND NOT EXISTS (
                                      SELECT 1
                                      FROM technician_photos earlier
                                      WHERE earlier.venue_id = p.venue_id
                                            AND earlier.created_at >= %s
                                            AND earlier.created_at < %s
                                            AND earlier.file_id = p.file_id
                                            AND earlier.status = 'matched'
                                            AND earlier.id < p.id
                                  )
                            ORDER BY id
                            """,
                            (
                                venue["id"],
                                shift_start, shift_end,
                                shift_start, shift_end,
                            ),
                        )
                    photos = _dedupe_photos_by_hash(cur.fetchall())
                    underage_codes = (
                        _underage_technician_codes(photos)
                        if venue["id"] == 5
                        else set()
                    )
                    adult_codes = (
                        _confirmed_adult_technician_codes(photos)
                        if venue["id"] == 6
                        else set()
                    )
                    profile_texts = {}
                    seen_file_ids = set()
                    seen_coded_file_ids = set()
                    photo_count = 0
                    category_codes = {}
                    for photo in photos:
                        technician_code = photo["technician_code"]
                        if venue["id"] in {4, 5, 6}:
                            technician_code = technician_code or (
                                extract_labeled_technician_code(photo["ocr_text"])
                            )
                            if (
                                venue["id"] == 5
                                and technician_code in underage_codes
                            ):
                                continue
                            if (
                                venue["id"] == 6
                                and (
                                    technician_code is None
                                    or technician_code not in adult_codes
                                )
                            ):
                                continue
                            file_id = photo["file_id"]
                            unique_file_id = file_id or f"photo:{photo['id']}"
                            if unique_file_id not in seen_file_ids:
                                seen_file_ids.add(unique_file_id)
                                photo_count += 1
                            if (
                                technician_code is None
                                or unique_file_id in seen_coded_file_ids
                            ):
                                continue
                            seen_coded_file_ids.add(unique_file_id)
                            venue["technician_photos"].setdefault(
                                technician_code, []
                            ).append(photo["id"])
                            profile_texts.setdefault(
                                technician_code, []
                            ).append(photo["ocr_text"] or "")
                            if venue["id"] == 5:
                                category_name = extract_technician_category(
                                    photo["ocr_text"]
                                )
                                if category_name:
                                    category_codes.setdefault(
                                        category_name, []
                                    ).append(technician_code)
                            elif venue["id"] == 6:
                                category_name = _dihao_category_for_photo(
                                    technician_code,
                                    photo["ocr_text"],
                                )
                                if category_name:
                                    category_codes.setdefault(
                                        category_name, []
                                    ).append(technician_code)
                            channel_id = str(photo["channel_id"])
                            channel_numeric = (
                                channel_id[4:]
                                if channel_id.startswith("-100")
                                else channel_id.lstrip("-")
                            )
                            photo_link = (
                                f"https://t.me/c/{channel_numeric}/{photo['msg_id']}"
                            )
                            venue.setdefault("technician_photo_links", {}).setdefault(
                                technician_code, []
                            ).append(photo_link)
                            continue

                        if technician_code is None:
                            continue
                        venue["technician_photos"].setdefault(
                            technician_code, []
                        ).append(photo["id"])
                        profile_texts.setdefault(
                            technician_code, []
                        ).append(photo["ocr_text"] or "")
                        channel_id = str(photo["channel_id"])
                        channel_numeric = (
                            channel_id[4:]
                            if channel_id.startswith("-100")
                            else channel_id.lstrip("-")
                        )
                        photo_link = (
                            f"https://t.me/c/{channel_numeric}/{photo['msg_id']}"
                        )
                        venue.setdefault("technician_photo_links", {}).setdefault(
                            photo["technician_code"], []
                        ).append(photo_link)
                    if venue["id"] in {4, 5, 6}:
                        venue["on_duty_count"] = photo_count
                        venue["technician_list"] = list(
                            venue["technician_photos"]
                        )
                    if venue["id"] in {5, 6}:
                        venue["technician_categories"] = [
                            {
                                "group_name": category_name,
                                "technician_codes": list(dict.fromkeys(codes)),
                            }
                            for category_name, codes in category_codes.items()
                        ]
                    if venue["id"] in {2, 4, 5, 6}:
                        venue["technician_profiles"] = {
                            technician_code: extract_technician_profile(
                                texts,
                                technician_code,
                            )
                            for technician_code, texts in profile_texts.items()
                        }

                cur.execute(
                    """
                    SELECT si.id, si.category, si.category_name, si.name,
                           si.description, si.price, si.duration, si.sort_order,
                           COALESCE(st.technician_codes, '') AS technician_codes
                    FROM service_items si
                    LEFT JOIN (
                        SELECT service_item_id,
                               GROUP_CONCAT(
                                   technician_code
                                   ORDER BY technician_code
                                   SEPARATOR ','
                               ) AS technician_codes
                        FROM service_item_technicians
                        WHERE duty_date = %s
                        GROUP BY service_item_id
                    ) st ON st.service_item_id = si.id
                    WHERE si.venue_id = %s
                    ORDER BY FIELD(si.category, 'small', 'medium', 'high', 'tcm', 'pedicure'),
                             si.sort_order, si.id
                    """,
                    (
                        attendance["duty_date"]
                        if attendance
                        else (None if venue["id"] in SHIFT_VENUE_IDS else today),
                        venue["id"],
                    ),
                )
                venue["service_items"] = cur.fetchall()
                venue["technicians"] = []

            return jsonify({"venues": venues})

        if action == "get_venue_detail":
            cur.execute(
                """
                SELECT id, name, address, phone, business_hours, map_url,
                       cover_image, search_tags, status
                FROM venues
                WHERE id = %s AND status = 'active'
                """,
                (venue_id,),
            )
            venue = cur.fetchone()
            if venue is None:
                return jsonify({"error": "Venue not found"}), 404
            venue["description"] = None
            return jsonify(venue)

        cur.execute(
            "SELECT id FROM venues WHERE id = %s AND status = 'active'",
            (venue_id,),
        )
        if cur.fetchone() is None:
            return jsonify({"error": "Venue not found"}), 404

        cur.execute(
            """
            SELECT id, name, specialty, avatar_link, status
            FROM technicians
            WHERE venue_id = %s
            ORDER BY id
            """,
            (venue_id,),
        )
        return jsonify({"list": cur.fetchall()})
    except pymysql.MySQLError:
        logger.exception("Database request failed for action %s", action)
        return jsonify({"error": "Database request failed"}), 500
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    from tenants import ensure_tables as ensure_tenant_tables
    ensure_tenant_tables()
    from wallet import ensure_tables as ensure_wallet_tables
    ensure_wallet_tables()
    ensure_table()
    ensure_movie_tables()
    ensure_music_tables()
    from music_social import ensure_tables as ensure_music_social_tables
    ensure_music_social_tables()
    ensure_image_hash_columns()
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", 8000))
    logger.info("Starting API server on %s:%s", host, port)
    stop_cache = threading.Event()
    threading.Thread(
        target=maintain_ktv_video_cache, args=(stop_cache,),
        name="ktv-cache", daemon=True,
    ).start()
    try:
        serve(app, host=host, port=port, threads=16)
    finally:
        stop_cache.set()
