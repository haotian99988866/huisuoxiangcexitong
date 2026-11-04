import logging
import os
import re
import tempfile
import threading
import announcements
import hotels
import json
from datetime import date

import pymysql
import requests
from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, request, send_file, stream_with_context
from flask_cors import CORS
from PIL import Image, UnidentifiedImageError
from waitress import serve
from telebot import apihelper
from urllib.parse import quote

MIN_AGE = 18
from delivery_age import extract_work_no, parse_price_table, price_for_work_no
from image_hash import ImageDeduper, ensure_columns as ensure_image_hash_columns
from image_cache import ImageCache
from video_cache import VideoCache
from music import ensure_tables as ensure_music_tables, catalog as music_catalog, audio_file as music_audio_file
from music_stream import RemoteAudio, audio_response as music_stream_response
from tenants import load_tenant, public_config, TenantUnavailable
from duty_shift import SHIFT_VENUE_IDS, current_shift_window
from visit_stats import ensure_table, record_visit, verify_init_data
from movies import (
    ensure_tables as ensure_movie_tables,
    movie_catalog,
    movie_episodes,
    short_drama_catalog, DRAMA_CHANNEL_ID,
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
        r"/member-deposit": {"origins": cors_origins},
        r"/admin-hotel-save": {"origins": cors_origins},
        r"/hotel-images/*": {"origins": cors_origins},
        r"/photos/*": {"origins": cors_origins},
        r"/ktv-videos/*": {"origins": cors_origins},
        r"/delivery-media/*": {"origins": cors_origins},
        r"/music-audio/*": {"origins": cors_origins},
        r"/announcement-media/*": {"origins": cors_origins},
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

from venue_pricing import dihao_category_for_photo as _dihao_category_for_photo

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
    if request.path in {"/member-deposit", "/admin-hotel-save"}:
        request.max_content_length = 6 * 1024 * 1024
    if request.method == "OPTIONS":
        return None
    header, query = request.headers.get("X-Tenant"), request.args.get("tenant")
    if header is not None and query is not None and header != query:
        return jsonify({"error": "租户入口参数冲突"}), 400
    slug = header if header is not None else query
    if slug is not None and request.path in {"/admin-hotel-save"}:
        return jsonify({"error": "租户入口不开放酒店管理"}), 403
    if slug is not None and request.endpoint == "hotel_image":
        return jsonify({"error": "租户入口暂未开放酒店推荐"}), 403
    if slug is not None and request.path == "/member-deposit":
        return jsonify({"error": "会所入口不开放充值"}), 403
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
                   "get_movies", "get_movie_series", "get_staff", "get_staff_detail"}
        if g.tenant.get("announcements_enabled"):
            allowed.update({"get_announcements", "get_announcement"})
        if not isinstance(body.get("action"), str) or body["action"] not in allowed:
            return jsonify({"error": "此入口仅提供会所与观影展示，不开放账户或充值"}), 403
        if body.get("action") in {"get_venue_detail", "get_technicians"}:
            try:
                venue_id = int(body.get("id", body.get("venue_id")))
            except (ValueError, TypeError):
                return jsonify({"error": "A valid venue id is required"}), 400
            if venue_id not in g.tenant["venue_ids"]:
                return jsonify({"error": "此入口未开放该会所"}), 403
        if body.get("action") == "get_staff" and body.get("venue_id") not in g.tenant["venue_ids"]:
            return jsonify({"error": "此入口未开放该会所档案"}), 403
    elif request.endpoint == "announcement_media" and not g.tenant.get("announcements_enabled"):
        return jsonify({"error": "此入口未开放公告"}), 403
    elif request.endpoint == "music_audio":
        return jsonify({"error": "此租户入口暂未开放音乐"}), 403
    elif request.endpoint == "staff_photo_image":
        from staff_archive import photo
        try:
            row = photo(request.view_args["photo_id"])
            if not row or row["venue_id"] not in g.tenant["venue_ids"]:
                return jsonify({"error": "此入口未开放该人员照片"}), 403
        except pymysql.MySQLError:
            logger.exception("Staff archive media tenant scope failed")
            return jsonify({"error": "人员档案暂不可用"}), 503
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
    body = request.get_json(silent=True) if request.endpoint == "webhook" and request.is_json else None
    admin_request = isinstance(body, dict) and isinstance(body.get("action"), str) and body["action"].startswith("admin_")
    music_request = isinstance(body, dict) and isinstance(body.get("action"), str) and body["action"].startswith("music_")
    payment_request = isinstance(body, dict) and isinstance(body.get("action"), str) and body["action"].startswith(("member_", "wallet_", "phone_", "customer_"))
    staff_request = isinstance(body, dict) and body.get("action") in {
        "get_staff", "get_staff_detail", "staff_customer_status", "staff_customer_subscribe"}
    hotel_request = isinstance(body, dict) and body.get("action") == "get_hotels"
    if request.path in {"/member-deposit", "/admin-hotel-save"} or hotel_request or request.endpoint == "hotel_image" or staff_request or admin_request or music_request or payment_request or getattr(g, "tenant", None) is not None or "tenant" in request.args or "X-Tenant" in request.headers:
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


@app.get("/staff-photos/<int:photo_id>/image")
def staff_photo_image(photo_id):
    from staff_archive import photo
    try:
        row = photo(photo_id)
        if not row:
            return jsonify({"error": "人员照片不存在或已停用"}), 404
        return _telegram_image(row["file_id"])
    except pymysql.MySQLError:
        logger.exception("Staff archive photo unavailable")
        return jsonify({"error": "人员照片暂不可用"}), 503


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


@app.get("/short-drama-thumb/<int:message_id>")
def short_drama_thumb(message_id):
    if g.tenant is not None:
        return jsonify({"error": "此租户入口暂未开放短剧"}), 403
    try:
        file_id = movie_thumb_file_id(message_id, DRAMA_CHANNEL_ID)
    except pymysql.MySQLError:
        logger.exception("Failed to look up short drama thumb %s", message_id)
        return jsonify({"error": "Database request failed"}), 500
    if not file_id:
        return jsonify({"error": "Thumbnail not found"}), 404
    return _telegram_image(file_id)


@app.get("/music-audio/<int:track_id>")
def music_audio(track_id):
    try:
        result = music_audio_file(track_id, stream=True)
        if result is None:
            return jsonify({"error": "歌曲不存在或已下架"}), 404
        if isinstance(result, RemoteAudio):
            return music_stream_response(result)
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


@app.get("/announcement-media/<int:ident>/<int:index>")
def announcement_media(ident, index):
    try:
        media = announcements.media_file(ident, index)
        if media is None:
            return jsonify({"error": "公告媒体不存在或已下架"}), 404
        path = announcements.local_media_path(media["filename"])
        if not path.is_file():
            return jsonify({"error": "公告媒体暂不可用，请稍后重试"}), 503
        content_type = "image/jpeg" if media["kind"] == "photo" else (
            "video/webm" if path.suffix == ".webm" else "video/mp4"
        )
        response = send_file(path, mimetype=content_type,
                             conditional=True, max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except pymysql.MySQLError:
        logger.exception("Announcement media unavailable")
        return jsonify({"error": "公告媒体暂不可用"}), 503


@app.get("/hotel-images/<hotel_id>")
def hotel_image(hotel_id):
    try:
        picture = hotels.image(hotel_id)
        if picture is None:
            return jsonify({"error": "酒店照片不存在或已下架"}), 404
        response = Response(picture, mimetype="image/jpeg")
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except pymysql.MySQLError:
        logger.exception("Hotel image unavailable")
        return jsonify({"error": "酒店照片暂不可用"}), 503


@app.errorhandler(413)
def upload_too_large(error):
    if request.path == "/admin-hotel-save":
        return jsonify({"error": "上传内容过大，请选择不超过5MB的门头照片"}), 413
    return error


@app.post("/webhook")
@app.post("/member-deposit")
@app.post("/admin-hotel-save")
def webhook():
    proof_upload = request.path == "/member-deposit"
    hotel_upload = request.path == "/admin-hotel-save"
    if hotel_upload:
        try:
            data = json.loads(request.form.get("payload", ""))
        except (ValueError, TypeError):
            return jsonify({"error": "酒店资料格式无效"}), 400
        if isinstance(data, dict):
            data["action"] = "admin_hotel_save"
    else:
        data = {**request.form, "action": "member_deposit"} if proof_upload else request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    action = data.get("action")
    if not isinstance(action, str):
        return jsonify({"error": "A valid action is required"}), 400
    if action in {"admin_dashboard", "admin_tenant_save", "admin_deposits", "admin_deposit_proof", "admin_deposit_review",
                  "admin_credited_customers",
                  "admin_hotels", "admin_hotel_save", "admin_hotel_image",
                  "admin_operations_health",
                  "admin_stars", "admin_stars_invoice", "admin_analytics", "admin_analytics_customer",
                  "admin_staff", "admin_staff_replace", "admin_staff_management", "admin_staff_management_save",
                  "admin_customers", "admin_customer_detail",
                  "admin_customer_save", "admin_customer_create", "admin_customer_bind",
                  "admin_consumption_save", "admin_consumption_void"}:
        from admin_console import authenticate, allowed_admin, dashboard, is_tenant_session, save_from_request
        from tenants import TenantConflict
        init_data = data.get("init_data")
        user = authenticate(init_data, os.getenv("TELEGRAM_BOT_TOKEN"))
        if user is None:
            response = jsonify({"error": "请从 Telegram 重新打开主入口，以验证管理员身份"})
            if action == "admin_operations_health":
                response.headers["Cache-Control"] = "private, no-store"
            return response, 401
        if not allowed_admin(user) or is_tenant_session(init_data):
            response = jsonify({"error": "仅平台管理员可从主入口访问管理台"})
            if action == "admin_operations_health":
                response.headers["Cache-Control"] = "private, no-store"
            return response, 403
        try:
            if action == "admin_operations_health":
                from operations_health import snapshot as operations_health
                response = jsonify(operations_health())
                response.headers["Cache-Control"] = "private, no-store"
                return response
            if action == "admin_hotels":
                return jsonify(hotels.catalog(data, admin=True))
            if action == "admin_hotel_image":
                import base64
                picture = hotels.image(data.get("id"), admin=True)
                if picture is None:
                    return jsonify({"error": "酒店照片不存在"}), 404
                return jsonify({"image": "data:image/jpeg;base64," + base64.b64encode(picture).decode("ascii")})
            if action == "admin_hotel_save":
                if not hotel_upload:
                    raise ValueError("酒店保存请使用图片上传入口")
                uploaded = request.files.get("image")
                raw = uploaded.read(hotels.MAX_IMAGE_BYTES + 1) if uploaded else None
                from customer_records import RecordConflict
                try:
                    return jsonify(hotels.save(data, raw, user["id"]))
                except RecordConflict as exc:
                    return jsonify({"error": str(exc)}), 409
            if action in {"admin_staff_management", "admin_staff_management_save"}:
                from staff_management import overview as staff_management_overview, save as staff_management_save
                from customer_records import RecordConflict
                try:
                    return jsonify(staff_management_overview(data, user["id"]) if action == "admin_staff_management"
                                   else staff_management_save(data, user["id"]))
                except RecordConflict as exc:
                    return jsonify({"error": str(exc)}), 409
            if action in {"admin_customers", "admin_customer_detail", "admin_customer_save", "admin_customer_create", "admin_customer_bind",
                          "admin_consumption_save", "admin_consumption_void"}:
                from customer_records import directory, detail as customer_record_detail, save as save_customer_record, RecordConflict
                try:
                    result = directory(data) if action == "admin_customers" else (
                        customer_record_detail(data) if action == "admin_customer_detail" else save_customer_record(data, user["id"]))
                    return jsonify(result)
                except RecordConflict as exc:
                    return jsonify({"error": str(exc)}), 409
            if action in {"admin_staff", "admin_staff_replace"}:
                from staff_archive import listing as staff_listing, replace as replace_staff
                try:
                    return jsonify(staff_listing(data, admin=True) if action == "admin_staff"
                                   else replace_staff(data, user["id"]))
                except RuntimeError:
                    logger.exception("Staff archive change unavailable")
                    return jsonify({"error": "档案更新暂不可用，请刷新核对后重试"}), 503
            if action in {"admin_analytics", "admin_analytics_customer"}:
                from customer_analytics import dashboard as analytics_dashboard, customer_detail
                response = jsonify(analytics_dashboard(data) if action == "admin_analytics" else customer_detail(data))
                response.headers["Cache-Control"] = "private, no-store"
                return response
            if action in {"admin_stars", "admin_stars_invoice"}:
                import telebot
                from admin_stars import create_invoice, dashboard as stars_dashboard
                bot = telebot.TeleBot(os.environ["TELEGRAM_BOT_TOKEN"])
                if action == "admin_stars":
                    return jsonify(stars_dashboard(bot, user["id"], data.get("order_id")))
                if data.get("confirmed") is not True:
                    raise ValueError("请明确确认使用个人 Stars 补充机器人库存")
                return jsonify(create_invoice(bot, user["id"], data.get("order_id"), data.get("stars")))
            if action == "admin_credited_customers":
                from wallet import credited_customers
                return jsonify(credited_customers(data))
            if action == "admin_deposits":
                from wallet import pending_details
                return jsonify({"deposits": pending_details()})
            if action in {"admin_deposit_proof", "admin_deposit_review"}:
                from wallet import proof_image, review
                deposit_id = data.get("deposit_id")
                if type(deposit_id) is not int or deposit_id <= 0:
                    raise ValueError("申请编号必须为正整数")
                if action == "admin_deposit_proof":
                    import base64
                    return jsonify({"image": "data:image/jpeg;base64," + base64.b64encode(proof_image(deposit_id)).decode("ascii")})
                decision = data.get("decision")
                if not isinstance(decision, str) or decision not in {"approve", "reject"} or data.get("confirmed") is not True:
                    raise ValueError("请明确核对并确认审核操作")
                if decision == "approve" and data.get("amount") is None:
                    raise ValueError("请输入实际到账金额")
                if decision == "reject":
                    from review_notifications import rejection_reason
                    rejection_reason(data.get("reason"))
                review(deposit_id, user["id"],
                       data.get("amount") if decision == "approve" else None,
                       reference=data.get("reference"), reason=data.get("reason"))
                return jsonify({"ok": True})
            if action == "admin_tenant_save":
                save_from_request(data, user["id"])
                return jsonify({"ok": True})
            return jsonify(dashboard())
        except TenantConflict as exc:
            return jsonify({"error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.IntegrityError:
            if action == "admin_deposit_review":
                return jsonify({"error": "该真实交易已用于其他申请，不能重复入账；请核实充值记录"}), 409
            if action in {"admin_stars", "admin_stars_invoice"}:
                return jsonify({"error": "Stars 订单记录冲突，请刷新核对原订单，勿重复付款"}), 409
            return jsonify({"error": "入口标识已存在，请更换标识或刷新配置"}), 409
        except pymysql.MySQLError:
            logger.exception("Admin console database operation failed")
            return jsonify({"error": "管理服务暂不可用，请稍后重试"}), 503
        except (apihelper.ApiException, requests.RequestException, RuntimeError):
            if action not in {"admin_stars", "admin_stars_invoice"}:
                raise
            logger.error("Telegram Stars funding service unavailable action=%s", action)
            return jsonify({"error": "Telegram Stars 服务暂不可用，请刷新核对库存和订单，勿重复付款"}), 503
    if action == "tenant_config":
        return jsonify({"tenant": public_config(g.tenant) if g.tenant else None})
    if action == "get_hotels":
        try:
            return jsonify(hotels.catalog(data))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Hotel catalog unavailable")
            return jsonify({"error": "酒店推荐暂不可用，请稍后重试"}), 503
    if action in {"get_announcements", "get_announcement"}:
        try:
            result = announcements.catalog(data.get("page", 1)) if action == "get_announcements" else announcements.detail(data.get("id"))
            if result is None:
                return jsonify({"error": "公告不存在或已下架"}), 404
            response = jsonify(result)
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Announcement service unavailable")
            return jsonify({"error": "公告暂不可用，请稍后重试"}), 503
    if action in {"staff_customer_status", "staff_customer_subscribe"}:
        from admin_console import authenticate, is_tenant_session
        from staff_customer_duty import status as customer_duty_status, save as customer_duty_save
        from customer_records import RecordConflict
        user = authenticate(data.get("init_data"), os.getenv("TELEGRAM_BOT_TOKEN"))
        if user is None:
            return jsonify({"error": "请从 Telegram 重新打开，以验证订阅身份"}), 401
        if is_tenant_session(data["init_data"]):
            return jsonify({"error": "请从主入口管理个人订阅"}), 403
        try:
            return jsonify(customer_duty_status(data, user["id"]) if action == "staff_customer_status"
                           else customer_duty_save(data, user["id"]))
        except RecordConflict as exc:
            return jsonify({"error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Customer duty subscription unavailable")
            return jsonify({"error": "出勤订阅暂不可用，请稍后核对"}), 503
    if action in {"get_staff", "get_staff_detail"}:
        from staff_archive import listing as staff_listing, detail as staff_detail
        try:
            result = staff_listing(data) if action == "get_staff" else staff_detail(data)
            if result is None:
                return jsonify({"error": "人员档案不存在或已停用"}), 404
            if getattr(g, "tenant", None) and result["venue_id"] not in g.tenant["venue_ids"]:
                return jsonify({"error": "此入口未开放该会所档案"}), 403
            if action == "get_staff_detail" and result["venue_id"] == 1:
                from staff_customer_duty import status as customer_duty_status
                result["customer_duty"] = customer_duty_status(data)
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Staff archive query failed")
            return jsonify({"error": "人员档案暂不可用，请稍后重试"}), 503
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
    if action in {
        "member_login", "member_logout", "member_account", "member_deposit",
        "member_catalog", "member_purchase", "phone_purchase", "member_orders", "member_order", "member_ledger",
        "member_test_account", "member_test_purchase", "customer_mine", "customer_contact",
    }:
        from member_accounts import authenticate as browser_auth, exchange_link, logout, site_origin
        from premium_balance import catalog, create_order, get_order, order_public, recent_orders, recent_ledger
        from wallet import account, submit
        try:
            from admin_console import authenticate as telegram_auth, is_tenant_session
            init_data = data.get("init_data")
            telegram_user = None
            if init_data is not None:
                telegram_user = telegram_auth(init_data, os.getenv("TELEGRAM_BOT_TOKEN"))
                if telegram_user is None:
                    return jsonify({"error": "未能验证 Telegram 账户，请重新打开小程序登录"}), 401
                if is_tenant_session(init_data):
                    return jsonify({"error": "会所入口不开放账户或服务购买"}), 403
                if action in {"member_login", "member_logout"}:
                    return jsonify({"error": "小程序账户由 Telegram 管理，请在客户端切换账户"}), 400
            elif request.headers.get("Origin") != site_origin():
                return jsonify({"error": "网站入口来源无效"}), 403
            if action == "member_login":
                try:
                    session = exchange_link(data.get("token"))
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 401
                return jsonify({"session": session})
            authorization = request.headers.get("Authorization", "")
            session = authorization[7:] if authorization.startswith("Bearer ") else ""
            try:
                user_id = telegram_user["id"] if telegram_user else browser_auth(session)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 401
            if action == "member_logout":
                logout(session)
                return jsonify({"logged_out": True})
            if action in {"customer_mine", "customer_contact"}:
                from customer_records import mine, contact
                return jsonify(mine(user_id, data) if action == "customer_mine" else contact(user_id))
            from visit_stats import admin_ids
            if action.startswith("member_test_"):
                if user_id not in admin_ids():
                    return jsonify({"error": "模拟测试仅允许管理员使用"}), 403
                from member_simulation import account as test_account, purchase as test_purchase
                if action == "member_test_account":
                    result = test_account(user_id)
                else:
                    result = test_purchase(
                        user_id, data.get("order_id"), data.get("recipient_username"),
                        data.get("months"), data.get("scenario"))
            elif action == "member_account":
                result = {**account(user_id), "user_id": user_id, "can_test": user_id in admin_ids()}
            elif action == "member_deposit":
                if proof_upload:
                    upload = request.files.get("proof")
                    if upload is None:
                        raise ValueError("请上传付款凭证")
                    result = {"id": submit(user_id, None, data.get("amount"), data.get("method"), proof=upload.read(5 * 1024 * 1024 + 1))}
                else:
                    result = {"id": submit(user_id, data.get("reference"), data.get("amount"), data.get("method"))}
                from wallet import notify_deposit
                if not notify_deposit(result["id"]):
                    result["warning"] = "申请已保存，但管理员提醒发送失败，请联系客服提供申请编号，勿重复付款或提交。"
            elif action == "member_catalog":
                result = catalog()
            elif action == "member_orders":
                result = {"orders": recent_orders(user_id)}
            elif action == "member_ledger":
                result = {"entries": recent_ledger(user_id)}
            elif action == "member_order":
                order = get_order(data.get("order_id"), user_id)
                if not order:
                    return jsonify({"error": "订单不存在"}), 404
                result = order_public(order)
            else:
                import telebot
                bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
                if not bot_token:
                    raise RuntimeError("Telegram bot token is not configured")
                if action == "phone_purchase":
                    order = create_order(
                        None, user_id, None, None, data.get("order_id"), product_kind="phone",
                        phone=data.get("phone"), amount=data.get("amount"))
                else:
                    order = create_order(
                        telebot.TeleBot(bot_token), user_id, data.get("recipient_username"),
                        data.get("months"), data.get("order_id"))
                result = order_public(order)
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            logger.exception("Browser member operation failed action=%s", action)
            if action in {"customer_mine", "customer_contact"}:
                return jsonify({"error": "客户消费记录或联系提醒暂不可用，请稍后重试或联系昊天人工核对"}), 503
            return jsonify({"error": "账户服务暂不可用；购买请求异常时请先查询订单，勿重新扣款"}), 503
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
    if action in {"analytics_record", "analytics_preferences"}:
        from admin_console import authenticate, is_tenant_session
        from customer_analytics import preferences, record
        init_data = data.get("init_data")
        user = authenticate(init_data, os.getenv("TELEGRAM_BOT_TOKEN"))
        if user is None:
            return jsonify({"error": "请从 Telegram 主入口打开，以验证浏览账户"}), 401
        if is_tenant_session(init_data):
            return jsonify({"error": "专属租户入口不记录平台客户行为"}), 403
        try:
            if action == "analytics_preferences" and "recording" in data and type(data["recording"]) is not bool:
                raise ValueError("浏览记录开关必须为布尔值")
            result = record(user, data, init_data) if action == "analytics_record" else preferences(
                user, data.get("recording"), init_data)
            if result.pop("new_session", False):
                record_visit(user)
            response = jsonify(result)
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Customer analytics request failed action=%s", action)
            return jsonify({"error": "浏览统计暂不可用，请稍后重试"}), 503
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

    if action == "get_short_dramas":
        try:
            query = str(data.get("query") or "").strip()[:50] or None
            return jsonify(short_drama_catalog(query, data.get("page", 1)))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Failed to load short dramas")
            return jsonify({"error": "短剧加载失败，请稍后重试"}), 503

    if action in {"get_movies", "get_movie_series"}:
        try:
            if action == "get_movies":
                category = str(data.get("category") or "").strip()[:50] or None
                query = str(data.get("query") or "").strip()[:50] or None
                return jsonify(movie_catalog(category, query, limit=data.get("limit", 120), page=data.get("page", 1)))
            series = str(data.get("series") or "").strip()[:255]
            if not series:
                return jsonify({"error": "A series name is required"}), 400
            return jsonify({"episodes": movie_episodes(series)})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
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
    from member_accounts import ensure_tables as ensure_member_account_tables
    from premium_balance import ensure_tables as ensure_member_sales_tables
    ensure_member_account_tables()
    ensure_member_sales_tables()
    from admin_stars import ensure_tables as ensure_admin_stars_tables
    ensure_admin_stars_tables()
    from member_simulation import ensure_tables as ensure_member_simulation_tables
    ensure_member_simulation_tables()
    ensure_table()
    from customer_analytics import ensure_tables as ensure_analytics_tables
    ensure_analytics_tables()
    ensure_movie_tables()
    ensure_music_tables()
    announcements.ensure_tables()
    hotels.ensure_tables()
    from music_social import ensure_tables as ensure_music_social_tables
    ensure_music_social_tables()
    ensure_image_hash_columns()
    from staff_archive import ensure_tables as ensure_staff_tables
    ensure_staff_tables()
    from staff_management import ensure_tables as ensure_staff_management_tables
    ensure_staff_management_tables()
    from staff_customer_duty import ensure_tables as ensure_customer_duty_tables
    ensure_customer_duty_tables()
    from customer_records import ensure_tables as ensure_customer_record_tables
    ensure_customer_record_tables()
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
