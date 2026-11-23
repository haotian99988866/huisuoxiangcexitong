import logging
import io
import os
import re
import tempfile
import threading
import announcements
import hotels
import venue_recommendations
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
from urllib.parse import quote, urlsplit

from delivery_posts import (
    build_delivery_posts, filter_adult_posts, public_person_media, attendance_publication,
)
from delivery_age import extract_work_no, parse_price_table, price_for_work_no
from image_hash import ImageDeduper, ensure_columns as ensure_image_hash_columns
from media_caches import delivery_video_cache, image_cache, video_cache
from music import ensure_tables as ensure_music_tables, catalog as music_catalog, audio_file as music_audio_file
from music_stream import RemoteAudio, audio_response as music_stream_response
from tenants import load_tenant, public_config, TenantUnavailable
from duty_shift import SHIFT_VENUE_IDS, canonical_shift_name, current_shift_window
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
from venues_public import (
    DELIVERY_VENUE_IDS,
    apple_category,
    confirmed_adult_technician_codes,
    parse_venue,
    underage_technician_codes,
)

_underage_technician_codes = underage_technician_codes
_confirmed_adult_technician_codes = confirmed_adult_technician_codes
_apple_category = apple_category

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
        r"/admin-delivery-upload": {"origins": cors_origins},
        r"/member-deposit": {"origins": cors_origins},
        r"/admin-hotel-save": {"origins": cors_origins},
        r"/admin-venue-recommendation-save": {"origins": cors_origins},
        r"/venue-recommendation-images/*": {"origins": cors_origins},
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


def _local_admin_debug(data):
    if os.getenv("LOCAL_ADMIN_DEBUG") != "1" or data.get("init_data"):
        return False
    if os.getenv("API_HOST", "127.0.0.1") not in {"127.0.0.1", "::1", "localhost"}:
        return False
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        return False
    if request.headers.get("X-Local-Admin-Debug") != "1":
        return False
    if any(name.lower().startswith(("forwarded", "x-forwarded-", "cf-"))
           or name.lower() == "x-real-ip" for name in request.headers.keys()):
        return False
    if request.headers.get("X-Tenant") or request.args.get("tenant"):
        return False
    origin = request.headers.get("Origin", "")
    return (
        urlsplit(request.host_url).hostname in {"localhost", "127.0.0.1", "::1"}
        and origin in {
            "http://localhost:4173", "http://127.0.0.1:4173",
            "http://localhost:5173", "http://127.0.0.1:5173",
        }
    )


from venue_pricing import (
    dihao_category_for_photo as _dihao_category_for_photo,
    staff_group_name as _staff_group_name,
)

def _view_endpoint():
    endpoint = request.endpoint or ""
    if endpoint.startswith("media."):
        return endpoint.split(".", 1)[1]
    return endpoint


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
    if request.path in {"/admin-delivery-upload", "/admin-venue-recommendation-save"}:
        request.max_content_length = 21 * 1024 * 1024
    if request.path in {"/member-deposit", "/admin-hotel-save"}:
        request.max_content_length = 6 * 1024 * 1024
    if request.method == "OPTIONS":
        return None
    header, query = request.headers.get("X-Tenant"), request.args.get("tenant")
    if header is not None and query is not None and header != query:
        return jsonify({"error": "租户入口参数冲突"}), 400
    slug = header if header is not None else query
    if slug is not None and request.path in {"/admin-hotel-save", "/admin-delivery-upload", "/admin-venue-recommendation-save"}:
        return jsonify({"error": "租户入口不开放后台上传管理"}), 403
    if slug is not None and _view_endpoint() == "hotel_image":
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
            return jsonify({"error": "请求体须为 JSON 对象"}), 400
        allowed = {"tenant_config", "get_venues", "get_venue_detail", "get_technicians",
                   "get_movies", "get_movie_series", "get_staff", "get_staff_detail",
                   "get_delivery_archive", "get_venue_recommendations"}
        if g.tenant.get("announcements_enabled"):
            allowed.update({"get_announcements", "get_announcement"})
        if not isinstance(body.get("action"), str) or body["action"] not in allowed:
            return jsonify({"error": "此入口仅提供会所与观影展示，不开放账户或充值"}), 403
        if body.get("action") in {"get_venue_detail", "get_technicians"}:
            try:
                venue_id = int(body.get("id", body.get("venue_id")))
            except (ValueError, TypeError):
                return jsonify({"error": "请提供有效的会所编号"}), 400
            if venue_id not in g.tenant["venue_ids"]:
                return jsonify({"error": "此入口未开放该会所"}), 403
        if body.get("action") in {"get_staff", "get_delivery_archive", "get_venue_recommendations"} and body.get("venue_id") not in g.tenant["venue_ids"]:
            return jsonify({"error": "此入口未开放该会所档案"}), 403
    elif _view_endpoint() == "venue_recommendation_image" and not venue_recommendations.SHARED_VENUE_IDS.intersection(g.tenant["venue_ids"]):
        return jsonify({"error": "此入口未开放该会所推荐"}), 403
    elif _view_endpoint() == "announcement_media" and not g.tenant.get("announcements_enabled"):
        return jsonify({"error": "此入口未开放公告"}), 403
    elif _view_endpoint() == "music_audio":
        return jsonify({"error": "此租户入口暂未开放音乐"}), 403
    elif _view_endpoint() == "staff_photo_image":
        from staff_archive import photo
        try:
            row = photo(request.view_args["photo_id"])
            if not row or row["venue_id"] not in g.tenant["venue_ids"]:
                return jsonify({"error": "此入口未开放该人员照片"}), 403
        except pymysql.MySQLError:
            logger.exception("Staff archive media tenant scope failed")
            return jsonify({"error": "人员档案暂不可用"}), 503
    elif _view_endpoint() in {"technician_photo_image", "ktv_video", "delivery_media"}:
        view = _view_endpoint()
        if view == "delivery_media" and not DELIVERY_VENUE_IDS.intersection(g.tenant["venue_ids"]):
            return jsonify({"error": "此入口未开放外送"}), 403
        table, field = {
            "technician_photo_image": ("technician_photos", "photo_id"),
            "ktv_video": ("ktv_room_videos", "video_id"),
            "delivery_media": ("delivery_media", "media_id"),
        }[view]
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
        "get_staff", "get_staff_detail", "get_delivery_archive", "staff_customer_status", "staff_customer_subscribe", "staff_customer_catalog",
        "delivery_customer_status", "delivery_customer_subscribe"}
    hotel_request = isinstance(body, dict) and body.get("action") in {"get_hotels", "get_venue_recommendations"}
    if request.path in {"/member-deposit", "/admin-hotel-save", "/admin-delivery-upload", "/admin-venue-recommendation-save"} or hotel_request or _view_endpoint() in {"hotel_image", "venue_recommendation_image"} or staff_request or admin_request or music_request or payment_request or getattr(g, "tenant", None) is not None or "tenant" in request.args or "X-Tenant" in request.headers:
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("X-Tenant")
    return response


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


def attach_delivery_archive_media(venue_id, rows, posts):
    person_by_media = {row["id"]: row.get("person_id") for row in rows}
    catalogs = {}
    for post in posts:
        people = {person_by_media.get(item["id"]) for item in post["media"]}
        if len(people) != 1 or None in people:
            continue
        category = post.get("category") if venue_id == 10 else None
        if category not in catalogs:
            catalogs[category] = {
                person["id"]: person["media"]
                for person in delivery_archive(venue_id, category)["people"]
            }
        media = catalogs[category].get(next(iter(people)))
        if media:
            post["media"] = media


def delivery_archive(venue_id, category=None):
    if type(venue_id) is not int or venue_id not in DELIVERY_VENUE_IDS:
        raise ValueError("请选择有效的外送分类")
    if category not in (None, "small", "red") or (category is not None and venue_id != 10):
        raise ValueError("外送分类无效")
    start, end, shift = current_shift_window()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id, m.person_id, m.message_id, m.channel_id,
                       m.media_group_id, m.media_type, m.caption, m.age,
                       m.ocr_text, m.image_hash, m.created_at, m.archive_only
                FROM delivery_media m
                LEFT JOIN delivery_people p ON p.id=m.person_id
                WHERE m.venue_id=%s AND (
                    m.media_type='text' OR
                    (p.deleted_at IS NULL AND p.merged_into_id IS NULL
                     AND p.consent_status='granted' AND p.id IS NOT NULL))
                ORDER BY m.message_id DESC
                """, (venue_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    categories = classify_apple_rows(rows) if venue_id == 10 else {}
    grouped = {}
    for row in rows:
        if row["media_type"] in {"photo", "video"}:
            grouped.setdefault(row["person_id"], []).append(row)
    people = []
    for person_id, media_rows in grouped.items():
        media = public_person_media(media_rows, categories, category)
        if not media:
            continue
        on_duty = attendance_publication(media_rows, media, start, end, shift) is not None
        people.append({
            "id": person_id, "media": media, "on_duty": on_duty,
            "attendance_overlay": "今晚出勤" if on_duty else
            "本班未见照片" if shift == "晚班" else "外送仅晚班",
        })
    return {"venue_id": venue_id, "people": people, "shift": shift,
            "shift_date": start.date().isoformat()}


def cached_video_response(file_id, cache):
    try:
        cached = cache.get(file_id)
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
        logger.exception("Cannot serve cached video; using Telegram stream")
    response = app.make_response(_proxy_telegram_media(file_id))
    response.headers["X-Media-Cache"] = "MISS"
    return response


def _telegram_image(file_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return jsonify({"error": "图片服务未配置"}), 503

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
        return jsonify({"error": "图片服务暂时不可用"}), 502
    except OSError:
        logger.exception("Failed to read, process or cache image")
        return jsonify({"error": "图片缓存暂时不可用"}), 503
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
        return jsonify({"error": "视频服务未配置"}), 503
    try:
        info = requests.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=(5, 15),
        ).json()
        file_path = (info.get("result") or {}).get("file_path")
        if not info.get("ok") or not file_path:
            return jsonify({"error": "视频过大，暂时无法播放"}), 502
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
            return jsonify({"error": "视频暂时不可用"}), 502
    except (requests.RequestException, ValueError) as exc:
        logger.error("Failed to retrieve Telegram media (%s)", type(exc).__name__)
        return jsonify({"error": "视频服务暂时不可用"}), 502

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


from media_warmup import (
    maintain_ktv_video_cache as _maintain_ktv_video_cache,
    warm_delivery_video_cache as _warm_delivery_video_cache,
    warm_ktv_video_cache as _warm_ktv_video_cache,
    warm_video_rows,
)


def warm_ktv_video_cache():
    _warm_ktv_video_cache(get_db_connection, video_cache)


def warm_delivery_video_cache():
    _warm_delivery_video_cache(get_db_connection, delivery_video_cache)


def maintain_ktv_video_cache(stop, *, delivery=False):
    _maintain_ktv_video_cache(
        stop,
        get_db_connection,
        delivery=delivery,
        ktv_cache=video_cache,
        delivery_cache=delivery_video_cache,
    )


from api import register_blueprints

register_blueprints(app)


@app.errorhandler(413)
def upload_too_large(error):
    if request.path == "/admin-delivery-upload":
        return jsonify({"error": "上传内容过大，请选择不超过20MB的视频或5MB的照片"}), 413
    if request.path == "/admin-hotel-save":
        return jsonify({"error": "上传内容过大，请选择不超过5MB的门头照片"}), 413
    if request.path == "/admin-venue-recommendation-save":
        return jsonify({"error": "推荐视频不能超过20MB，照片不能超过5MB"}), 413
    return error


@app.post("/webhook")
@app.post("/member-deposit")
@app.post("/admin-hotel-save")
@app.post("/admin-delivery-upload")
@app.post("/admin-venue-recommendation-save")
def webhook():
    from api.webhook import parse_webhook_payload, try_public_read_handlers

    parsed = parse_webhook_payload(request)
    if "error" in parsed:
        message, status = parsed["error"]
        return jsonify({"error": message}), status

    data = parsed["data"]
    action = parsed["action"]
    proof_upload = parsed["proof_upload"]
    hotel_upload = parsed["hotel_upload"]
    delivery_upload = parsed["delivery_upload"]
    recommendation_upload = parsed["recommendation_upload"]
    if action in {"admin_dashboard", "admin_tenant_save", "admin_deposits", "admin_deposit_proof", "admin_deposit_review",
                  "admin_credited_customers",
                  "admin_hotels", "admin_hotel_save", "admin_hotel_image",
                  "admin_venue_recommendations", "admin_venue_recommendation_image", "admin_venue_recommendation_save",
                  "admin_operations_health",
                  "admin_stars", "admin_stars_invoice", "admin_analytics", "admin_analytics_customer",
                  "admin_staff", "admin_staff_replace", "admin_staff_management", "admin_staff_management_save",
                  "admin_staff_detail", "admin_staff_description", "admin_staff_delete",
                  "admin_customers", "admin_customer_detail",
                  "admin_customer_save", "admin_customer_create", "admin_customer_bind",
                  "admin_finance_dashboard", "admin_finance_daily_summary",
                  "admin_finance_transaction", "admin_finance_budget",
                  "admin_finance_goal", "admin_finance_account", "admin_finance_category",
                  "admin_finance_account_opening",
                  "admin_finance_category_update", "admin_finance_category_delete",
                  "admin_finance_category_restore",
                  "admin_finance_commissions", "admin_finance_void",
                  "admin_delivery_staff", "admin_delivery_staff_consent",
                  "admin_delivery_staff_save", "admin_delivery_staff_merge",
                  "admin_delivery_staff_match",
                  "admin_delivery_staff_description", "admin_delivery_staff_upload",
                  "admin_delivery_staff_attachment",
                  "admin_delivery_staff_delete", "admin_delivery_staff_attachment_delete",
                  "admin_consumption_save", "admin_consumption_void"}:
        from admin_console import (
            authenticate_admin_request, allowed_admin, dashboard, is_tenant_session,
            save_from_request,
        )
        from tenants import TenantConflict
        init_data = data.get("init_data")
        local_debug = _local_admin_debug(data)
        user = {"id": -1, "first_name": "Local development"} if local_debug else (
            authenticate_admin_request(
                init_data, action, os.getenv("TELEGRAM_BOT_TOKEN"),
                os.getenv("FINANCE_BOT_TOKEN"), os.getenv("FINANCE_BOT_OWNER_ID"),
            )
        )
        if user is None:
            response = jsonify({"error": "请从 Telegram 重新打开主入口，以验证管理员身份"})
            if action == "admin_operations_health":
                response.headers["Cache-Control"] = "private, no-store"
            return response, 401
        if not local_debug and (not allowed_admin(user) or is_tenant_session(init_data)):
            response = jsonify({"error": "仅平台管理员可从主入口访问管理台"})
            if action == "admin_operations_health":
                response.headers["Cache-Control"] = "private, no-store"
            return response, 403
        if local_debug:
            logger.warning("Local admin debug request: %s (audit actor -1)", action)
        try:
            if action == "admin_operations_health":
                from operations_health import snapshot as operations_health
                response = jsonify(operations_health())
                response.headers["Cache-Control"] = "private, no-store"
                return response
            if action.startswith("admin_finance_"):
                import finance
                finance.ensure_tables()
                handlers = {
                    "admin_finance_transaction": finance.save_transaction,
                    "admin_finance_budget": finance.set_budget,
                    "admin_finance_goal": finance.add_goal,
                    "admin_finance_account": finance.add_account,
                    "admin_finance_account_opening": finance.set_account_opening_balance,
                    "admin_finance_category": finance.add_category,
                    "admin_finance_category_update": finance.update_category,
                    "admin_finance_category_delete": finance.delete_category,
                    "admin_finance_category_restore": finance.restore_category,
                    "admin_finance_commissions": finance.save_commission_items,
                    "admin_finance_void": finance.void_transaction,
                }
                if action == "admin_finance_dashboard":
                    response = jsonify(finance.dashboard(data, user["id"]))
                elif action == "admin_finance_daily_summary":
                    response = jsonify(finance.daily_summary(data, user["id"]))
                elif action in handlers:
                    response = jsonify(handlers[action](data, user["id"]))
                else:
                    raise ValueError("财务操作无效")
                response.headers["Cache-Control"] = "private, no-store"
                return response
            if action == "admin_hotels":
                return jsonify(hotels.catalog(data, admin=True))
            if action == "admin_venue_recommendations":
                return jsonify(venue_recommendations.catalog(data, admin=True))
            if action == "admin_venue_recommendation_image":
                import base64
                media = venue_recommendations.media(data.get("id"), admin=True)
                if media is None:
                    return jsonify({"error": "推荐媒体不存在"}), 404
                return send_file(io.BytesIO(media["image"]),
                                 mimetype="video/mp4" if media["media_type"] == "video" else "image/jpeg",
                                 etag=False, max_age=0)
            if action == "admin_venue_recommendation_save":
                if not recommendation_upload:
                    raise ValueError("请使用推荐图片上传入口")
                uploaded = request.files.get("image")
                raw = uploaded.read(venue_recommendations.MAX_MEDIA_BYTES + 1) if uploaded else None
                from customer_records import RecordConflict
                try:
                    return jsonify(venue_recommendations.save(data, raw, user["id"], uploaded.filename if uploaded else None))
                except RecordConflict as exc:
                    return jsonify({"error": str(exc)}), 409
                except (OSError, RuntimeError):
                    logger.exception("Recommendation media processing unavailable")
                    return jsonify({"error": "推荐媒体处理暂不可用，请稍后使用原编号重试"}), 503
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
                        customer_record_detail(data, user["id"]) if action == "admin_customer_detail"
                        else save_customer_record(data, user["id"]))
                    return jsonify(result)
                except RecordConflict as exc:
                    return jsonify({"error": str(exc)}), 409
            if action in {"admin_staff", "admin_staff_replace", "admin_staff_detail",
                          "admin_staff_description", "admin_staff_delete"}:
                from staff_archive import listing as staff_listing, replace as replace_staff, detail as staff_detail, update_archive
                from customer_records import RecordConflict
                try:
                    if action == "admin_staff":
                        return jsonify(staff_listing(data, admin=True))
                    if action == "admin_staff_detail":
                        result = staff_detail(data, admin=True)
                        if result is None:
                            return jsonify({"error": "人员档案不存在或已删除"}), 404
                        return jsonify(result)
                    if action in {"admin_staff_description", "admin_staff_delete"}:
                        return jsonify(update_archive(data, user["id"], delete=action == "admin_staff_delete"))
                    return jsonify(replace_staff(data, user["id"]))
                except RecordConflict as exc:
                    return jsonify({"error": str(exc)}), 409
                except RuntimeError:
                    logger.exception("Staff archive change unavailable")
                    return jsonify({"error": "档案更新暂不可用，请刷新核对后重试"}), 503
            if action in {
                "admin_delivery_staff", "admin_delivery_staff_consent",
                "admin_delivery_staff_save", "admin_delivery_staff_merge",
                "admin_delivery_staff_match",
                "admin_delivery_staff_description", "admin_delivery_staff_upload",
                "admin_delivery_staff_attachment",
                "admin_delivery_staff_delete", "admin_delivery_staff_attachment_delete",
            }:
                import delivery_staff
                try:
                    if action == "admin_delivery_staff":
                        return jsonify(delivery_staff.admin_listing(data.get("venue_id")))
                    if action in {"admin_delivery_staff_delete", "admin_delivery_staff_attachment_delete"}:
                        if action == "admin_delivery_staff_attachment_delete" and data.get("attachment_id") is None:
                            raise ValueError("请选择需要删除的补充媒体")
                        return jsonify(delivery_staff.delete_archive_content(
                            data.get("person_id"),
                            data.get("attachment_id") if action == "admin_delivery_staff_attachment_delete" else None,
                            data.get("confirmed"), user["id"],
                        ))
                    if action == "admin_delivery_staff_description":
                        return jsonify(delivery_staff.save_description(
                            data.get("person_id"), data.get("description"), user["id"]
                        ))
                    if action == "admin_delivery_staff_upload":
                        if not delivery_upload:
                            raise ValueError("请使用媒体上传入口")
                        uploaded = request.files.get("media")
                        if uploaded is None:
                            raise ValueError("请选择媒体文件")
                        return jsonify(delivery_staff.add_attachment(
                            data.get("person_id"), uploaded.read(delivery_staff.MAX_MEDIA_BYTES + 1),
                            uploaded.filename, user["id"],
                            description=data.get("description"),
                        ))
                    if action == "admin_delivery_staff_attachment":
                        item = delivery_staff.attachment(data.get("attachment_id"))
                        if item is None:
                            return jsonify({"error": "媒体不存在或档案已合并"}), 404
                        response = Response(item["content"], mimetype=item["content_type"])
                        response.headers["X-Content-Type-Options"] = "nosniff"
                        return response
                    if action == "admin_delivery_staff_consent":
                        return jsonify(delivery_staff.set_consent(
                            data.get("person_id"), data.get("granted"), user["id"]
                        ))
                    if action == "admin_delivery_staff_save":
                        return jsonify(delivery_staff.update_profile(
                            data.get("person_id"), data.get("display_name"), user["id"]
                        ))
                    if action == "admin_delivery_staff_merge":
                        return jsonify(delivery_staff.merge_people(
                            data.get("source_id"), data.get("target_id"), user["id"]
                        ))
                    return jsonify(delivery_staff.match_candidates(
                        data.get("person_id"), delivery_staff.download_telegram_media
                    ))
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
                except FileNotFoundError as exc:
                    return jsonify({"error": str(exc)}), 503
                except requests.RequestException:
                    logger.exception("Telegram media download failed during local face matching")
                    return jsonify({"error": "无法下载外送媒体，请稍后重试"}), 502
                except pymysql.MySQLError:
                    logger.exception("Delivery personnel database operation failed")
                    return jsonify({"error": "外送人员资料暂不可用"}), 503
                except (RuntimeError, OSError):
                    logger.exception("Local delivery face matching failed")
                    return jsonify({"error": "本地人脸比对暂不可用"}), 503
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
            if action == "admin_dashboard":
                page = data.get("tenant_page", 1)
                if type(page) is not int:
                    raise ValueError("租户页码无效，请刷新后重试")
                return jsonify(dashboard(
                    tenant_page=page,
                    tenant_search=data.get("tenant_search", ""),
                    tenant_status=data.get("tenant_status", "all"),
                ))
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
    public_response = try_public_read_handlers(action, data)
    if public_response is not None:
        return public_response
    # Remaining webhook actions stay here until migrated into api.webhook.PUBLIC_READ_HANDLERS.
    if action in {"subscription_catalog", "subscription_save",
                  "staff_customer_status", "staff_customer_subscribe", "staff_customer_catalog",
                  "delivery_customer_status", "delivery_customer_subscribe"}:
        from admin_console import authenticate, is_tenant_session
        from staff_customer_duty import status as customer_duty_status, save as customer_duty_save
        from customer_records import RecordConflict
        user = authenticate(data.get("init_data"), os.getenv("TELEGRAM_BOT_TOKEN"))
        if user is None:
            return jsonify({"error": "请从 Telegram 重新打开，以验证订阅身份"}), 401
        if is_tenant_session(data["init_data"]):
            return jsonify({"error": "请从主入口管理个人订阅"}), 403
        try:
            if action in {"subscription_catalog", "subscription_save"}:
                import subscription_center
                return jsonify(subscription_center.catalog(user["id"], data.get("venue_id"))
                               if action == "subscription_catalog" else subscription_center.save(data, user["id"]))
            if action == "staff_customer_catalog":
                from staff_customer_duty import catalog_status
                return jsonify(catalog_status(data.get("venue_id"), user["id"]))
            if action == "delivery_customer_status":
                from delivery_customer_duty import catalog_status
                return jsonify(catalog_status(data.get("venue_id"), user["id"]))
            if action == "delivery_customer_subscribe":
                return jsonify(customer_duty_save(data, user["id"], delivery=True))
            return jsonify(customer_duty_status(data, user["id"]) if action == "staff_customer_status"
                           else customer_duty_save(data, user["id"]))
        except RecordConflict as exc:
            return jsonify({"error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Customer duty subscription unavailable")
            return jsonify({"error": "出勤订阅暂不可用，请稍后核对"}), 503
    if action == "get_delivery_archive":
        try:
            return jsonify(delivery_archive(data.get("venue_id"), data.get("category")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Delivery archive unavailable")
            return jsonify({"error": "外送人员档案暂不可用，请稍后重试"}), 503
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
                bot = telebot.TeleBot(bot_token)
                if action == "phone_purchase":
                    order = create_order(
                        bot, user_id, None, None, data.get("order_id"), product_kind="phone",
                        phone=data.get("phone"), amount=data.get("amount"))
                else:
                    order = create_order(
                        bot, user_id, data.get("recipient_username"),
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
                return jsonify({"error": "请提供剧集名称"}), 400
            return jsonify({"episodes": movie_episodes(series)})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except pymysql.MySQLError:
            logger.exception("Failed to load movies")
            return jsonify({"error": "数据库请求失败，请稍后重试"}), 500

    if action not in {"get_venues", "get_venue_detail", "get_technicians"}:
        return jsonify({"error": "未知操作"}), 400

    venue_id = None
    if action in {"get_venue_detail", "get_technicians"}:
        try:
            venue_id = int(data.get("id", data.get("venue_id")))
            if venue_id < 1:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "请提供有效的会所编号"}), 400

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
                WHERE v.status = 'active' AND v.id <> 10
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
                        SELECT id, person_id, message_id, channel_id, media_group_id, media_type, caption, age, ocr_text,
                               image_hash
                        FROM delivery_media
                        WHERE venue_id = %s
                              AND archive_only=FALSE
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
                    attach_delivery_archive_media(venue["id"], delivery_rows, venue["delivery_posts"])
                if venue["id"] in SHIFT_VENUE_IDS:
                    cur.execute(
                        """
                        SELECT duty_date, technician_codes, duty_shift, created_at
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
                if attendance:
                    raw_shift = attendance["duty_shift"]
                    # 名玺历史「实时出勤」按发布时间归一为白班/晚班
                    venue["duty_shift_today"] = (
                        canonical_shift_name(raw_shift, attendance.get("created_at"))
                        if venue["id"] == 2
                        else raw_shift
                    )
                else:
                    venue["duty_shift_today"] = None
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

                # 名玺(2)当班出勤只看出勤初稿，不读当班频道照片
                if venue["id"] != 2 and (attendance or venue["id"] in {4, 5, 6}):
                    if venue["id"] in {4, 5, 6}:
                        # 公开仅 matched；pending_review 不进列表
                        cur.execute(
                            """
                            SELECT id, technician_code, channel_id, msg_id,
                                   ocr_text, file_id, status, image_hash
                            FROM technician_photos
                            WHERE venue_id = %s
                                  AND status = 'matched'
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
                        if venue["id"] in {4, 5, 6}
                        else set()
                    )
                    adult_codes = (
                        _confirmed_adult_technician_codes(photos)
                        if venue["id"] in {4, 5, 6}
                        else set()
                    )
                    profile_texts = {}
                    seen_coded_file_ids = set()
                    category_codes = {}
                    for photo in photos:
                        technician_code = photo["technician_code"]
                        if venue["id"] in {4, 5, 6}:
                            technician_code = technician_code or (
                                extract_labeled_technician_code(photo["ocr_text"])
                            )
                            if technician_code is None:
                                continue
                            if technician_code in underage_codes:
                                continue
                            if (
                                venue["id"] in {4, 5, 6}
                                and technician_code not in adult_codes
                            ):
                                continue
                            file_id = photo["file_id"]
                            unique_file_id = file_id or f"photo:{photo['id']}"
                            if unique_file_id in seen_coded_file_ids:
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
                        venue["technician_list"] = list(
                            venue["technician_photos"]
                        )
                        # 出勤人数只计已识别工号，不含无工号图
                        venue["on_duty_count"] = len(venue["technician_list"])
                    if venue["id"] == 4:
                        for technician_code in venue["technician_list"]:
                            category_name = _staff_group_name(4, technician_code, [])
                            if category_name:
                                category_codes.setdefault(
                                    category_name, []
                                ).append(technician_code)
                    if venue["id"] in {4, 5, 6} and category_codes:
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
                WHERE id = %s AND status = 'active' AND id <> 10
                """,
                (venue_id,),
            )
            venue = cur.fetchone()
            if venue is None:
                return jsonify({"error": "找不到该会所"}), 404
            venue["description"] = None
            return jsonify(venue)

        cur.execute(
            "SELECT id FROM venues WHERE id = %s AND status = 'active' AND id <> 10",
            (venue_id,),
        )
        if cur.fetchone() is None:
            return jsonify({"error": "找不到该会所"}), 404

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
        return jsonify({"error": "数据库请求失败，请稍后重试"}), 500
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
    venue_recommendations.ensure_tables()
    from music_social import ensure_tables as ensure_music_social_tables
    ensure_music_social_tables()
    ensure_image_hash_columns()
    from staff_archive import ensure_tables as ensure_staff_tables
    ensure_staff_tables()
    from staff_management import ensure_tables as ensure_staff_management_tables
    ensure_staff_management_tables()
    from staff_customer_duty import ensure_tables as ensure_customer_duty_tables
    ensure_customer_duty_tables()
    from delivery_staff import ensure_tables as ensure_delivery_staff_tables
    ensure_delivery_staff_tables()
    ensure_customer_duty_tables(delivery=True)
    from subscription_center import ensure_tables as ensure_subscription_center
    ensure_subscription_center()
    from customer_records import ensure_tables as ensure_customer_record_tables
    ensure_customer_record_tables()
    from finance import ensure_tables as ensure_finance_tables
    ensure_finance_tables()
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", 8000))
    logger.info("Starting API server on %s:%s", host, port)
    stop_cache = threading.Event()
    threading.Thread(
        target=maintain_ktv_video_cache, args=(stop_cache,),
        name="ktv-cache", daemon=True,
    ).start()
    threading.Thread(
        target=maintain_ktv_video_cache, args=(stop_cache,), kwargs={"delivery": True},
        name="delivery-video-cache", daemon=True,
    ).start()
    from announcement_video import maintain as maintain_announcement_videos
    threading.Thread(
        target=maintain_announcement_videos, args=(stop_cache,),
        name="news-video-faststart", daemon=True,
    ).start()
    try:
        serve(
            app, host=host, port=port, threads=16,
            # Keep forwarding headers visible so local debug can reject them.
            clear_untrusted_proxy_headers=os.getenv("LOCAL_ADMIN_DEBUG") != "1",
        )
    finally:
        stop_cache.set()
