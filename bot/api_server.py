import logging
from io import BytesIO
import os
from datetime import date

import pymysql
import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from PIL import Image, UnidentifiedImageError
from waitress import serve
from urllib.parse import quote
from technician_photo_ocr import extract_technician_profile

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
    },
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)


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


def parse_venue(venue):
    venue["search_tags"] = venue["search_tags"].split(",") if venue["search_tags"] else []
    technician_codes = venue.get("technician_codes") or ""
    venue["technician_list"] = [
        code.strip() for code in technician_codes.split(",") if code.strip()
    ]
    venue["on_duty_count"] = len(venue["technician_list"])
    venue["description"] = None
    return venue


@app.get("/photos/<int:photo_id>/image")
def technician_photo_image(photo_id):
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.file_id
            FROM technician_photos p
            WHERE p.id = %s AND p.status = 'matched'
                  AND p.technician_code IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM attendance_daily a
                      WHERE a.venue_id = p.venue_id
                            AND a.duty_date = p.duty_date
                            AND FIND_IN_SET(p.technician_code, a.technician_codes) > 0
                  )
            """,
            (photo_id,),
        )
        photo = cur.fetchone()
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

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return jsonify({"error": "Photo service is not configured"}), 503

    try:
        file_response = requests.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": photo["file_id"]},
            timeout=(5, 15),
        )
        file_response.raise_for_status()
        file_info = file_response.json()
        if not file_info.get("ok") or not file_info.get("result", {}).get("file_path"):
            logger.error("Telegram did not return a file path for photo %s", photo_id)
            return jsonify({"error": "Photo is unavailable"}), 502

        file_path = file_info["result"]["file_path"]
        image_response = requests.get(
            f"https://api.telegram.org/file/bot{token}/{quote(file_path, safe='/')}",
            timeout=(5, 30),
        )
        image_response.raise_for_status()
    except requests.RequestException as exc:
        logger.error(
            "Failed to retrieve Telegram photo %s (%s)",
            photo_id,
            type(exc).__name__,
        )
        return jsonify({"error": "Photo service is temporarily unavailable"}), 502

    try:
        with Image.open(BytesIO(image_response.content)) as image:
            content_type = Image.MIME.get(image.format)
            image.verify()
    except (UnidentifiedImageError, OSError):
        logger.error("Telegram returned invalid image data for photo %s", photo_id)
        return jsonify({"error": "Stored file is not an image"}), 502
    if content_type is None:
        logger.error("Unsupported Telegram image format for photo %s", photo_id)
        return jsonify({"error": "Unsupported photo format"}), 502

    response = Response(image_response.content, mimetype=content_type)
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.post("/webhook")
def webhook():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    action = data.get("action")
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
                SELECT id, name, address, phone, business_hours,
                       map_url, cover_image, search_tags, technician_codes, status
                FROM venues
                WHERE status = 'active'
                ORDER BY id
                """
            )
            venues = [parse_venue(venue) for venue in cur.fetchall()]
            today = date.today()

            for venue in venues:
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

                    cur.execute(
                        """
                        SELECT id, technician_code, channel_id, msg_id, ocr_text
                        FROM technician_photos p
                        WHERE venue_id = %s AND duty_date = %s AND status = 'matched'
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM technician_photos earlier
                                  WHERE earlier.venue_id = p.venue_id
                                        AND earlier.duty_date = p.duty_date
                                        AND earlier.file_id = p.file_id
                                        AND earlier.status = 'matched'
                                        AND earlier.id < p.id
                              )
                        ORDER BY id
                        """,
                        (venue["id"], attendance["duty_date"]),
                    )
                    profile_texts = {}
                    for photo in cur.fetchall():
                        venue["technician_photos"].setdefault(
                            photo["technician_code"], []
                        ).append(photo["id"])
                        profile_texts.setdefault(
                            photo["technician_code"], []
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
                    if venue["id"] == 2:
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
                    (attendance["duty_date"] if attendance else today, venue["id"]),
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
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", 8000))
    logger.info("Starting API server on %s:%s", host, port)
    serve(app, host=host, port=port)
