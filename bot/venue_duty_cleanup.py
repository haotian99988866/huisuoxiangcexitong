"""Track original duty media and remove older shifts without removing archives."""
from datetime import datetime
import logging
import threading
import time

import pymysql
import requests
from telebot import apihelper

from config.channels import STAFF_ARCHIVE_VENUES
from db import get_conn
from duty_shift import current_shift_window
from staff_archive import sync as sync_staff_archive


logger = logging.getLogger(__name__)
VENUE_IDS = (4, 5, 6)
REQUEST_INTERVAL = 5


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS venue_duty_messages (
                venue_id INT NOT NULL,
                message_id BIGINT NOT NULL,
                published_at DATETIME NOT NULL,
                shift_start DATETIME NOT NULL,
                kind VARCHAR(10) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'retained',
                error VARCHAR(255) NULL,
                PRIMARY KEY (venue_id,message_id),
                INDEX pending_cleanup (status,venue_id,message_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS venue_duty_cleanup_policy (
                venue_id INT PRIMARY KEY,
                next_request_at DATETIME NULL
            ) ENGINE=InnoDB""")
            cur.executemany(
                "INSERT IGNORE INTO venue_duty_cleanup_policy(venue_id) VALUES(%s)",
                [(venue_id,) for venue_id in VENUE_IDS],
            )
            for venue_id in VENUE_IDS:
                channel_id = str(STAFF_ARCHIVE_VENUES[venue_id]["channel_id"])
                cur.execute("""SELECT t.msg_id,t.created_at FROM technician_photos t
                    WHERE t.venue_id=%s AND t.channel_id=%s AND t.msg_id>0
                    AND NOT EXISTS (SELECT 1 FROM venue_photos v
                        WHERE v.channel_id=t.channel_id AND v.msg_id=t.msg_id)""",
                            (venue_id, channel_id))
                rows = cur.fetchall()
                if rows:
                    cur.executemany("""INSERT IGNORE INTO venue_duty_messages
                        (venue_id,message_id,published_at,shift_start,kind)
                        VALUES(%s,%s,%s,%s,'photo')""", [
                            (venue_id, row["msg_id"], row["created_at"],
                             current_shift_window(row["created_at"])[0])
                            for row in rows
                        ])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_media(message, venue_id, *, now=None):
    if venue_id not in VENUE_IDS:
        return
    venue = STAFF_ARCHIVE_VENUES[venue_id]
    caption = message.caption or ""
    if (message.chat.id != venue["channel_id"]
            or caption.startswith((venue["repost_prefix"], venue["duty_summary_prefix"]))):
        return
    kind = "photo" if message.photo else "video" if message.video else None
    if kind is None:
        return
    published_at = datetime.fromtimestamp(message.date)
    shift_start = current_shift_window(published_at)[0]
    rollover = kind == "photo" and shift_start == current_shift_window(now)[0]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT IGNORE INTO venue_duty_messages
                (venue_id,message_id,published_at,shift_start,kind)
                VALUES(%s,%s,%s,%s,%s)""",
                        (venue_id, message.message_id, published_at, shift_start, kind))
            if rollover:
                cur.execute("""SELECT message_id,updated_at FROM bot_footers
                    WHERE channel_id=%s AND updated_at<%s""",
                            (str(venue["channel_id"]), shift_start))
                footer = cur.fetchone()
                if footer:
                    cur.execute("""INSERT IGNORE INTO venue_duty_messages
                        (venue_id,message_id,published_at,shift_start,kind)
                        VALUES(%s,%s,%s,%s,'footer')""",
                                (venue_id, footer["message_id"], footer["updated_at"],
                                 current_shift_window(footer["updated_at"])[0]))
                cur.execute("""UPDATE venue_duty_messages SET status='queued'
                    WHERE venue_id=%s AND shift_start<%s AND status='retained'""",
                            (venue_id, shift_start))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _protected(cur, row):
    venue = STAFF_ARCHIVE_VENUES[row["venue_id"]]
    prefix = venue["table_prefix"]
    for table in (f"{prefix}_posts", f"{prefix}_pages"):
        cur.execute(f"SELECT message_id FROM {table} WHERE message_id=%s LIMIT 1",
                    (row["message_id"],))
        if cur.fetchone():
            return True
    cur.execute("SELECT msg_id FROM venue_photos WHERE channel_id=%s AND msg_id=%s LIMIT 1",
                (str(venue["channel_id"]), row["message_id"]))
    if cur.fetchone():
        return True
    # 待人工核对的出勤图：频道原帖删了就无法再审，必须保护
    cur.execute(
        """
        SELECT 1 FROM technician_photos
        WHERE venue_id=%s AND channel_id=%s AND msg_id=%s
              AND status='pending_review'
        LIMIT 1
        """,
        (row["venue_id"], str(venue["channel_id"]), row["message_id"]),
    )
    return bool(cur.fetchone())


def _finish(cur, row, status, error=None):
    cur.execute("""UPDATE venue_duty_messages SET status=%s,error=%s
        WHERE venue_id=%s AND message_id=%s""",
                (status, error, row["venue_id"], row["message_id"]))


def run_once(bot, venue_id):
    venue = STAFF_ARCHIVE_VENUES[venue_id]
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s,0) AS acquired",
                        (f"venue_duty_cleanup_{venue_id}",))
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                return False
            cur.execute("""SELECT venue_id,message_id,published_at,kind
                FROM venue_duty_messages WHERE venue_id=%s AND status='queued'
                ORDER BY message_id LIMIT 1""", (venue_id,))
            row = cur.fetchone()
            if not row:
                return False
            if _protected(cur, row):
                _finish(cur, row, "protected")
                conn.commit()
                return False
            cur.execute("""SELECT next_request_at IS NULL
                OR next_request_at<=UTC_TIMESTAMP() AS ready
                FROM venue_duty_cleanup_policy WHERE venue_id=%s""", (venue_id,))
            if not cur.fetchone()["ready"]:
                return False
            sync_staff_archive()
            cur.execute("""UPDATE venue_duty_cleanup_policy
                SET next_request_at=DATE_ADD(UTC_TIMESTAMP(),INTERVAL %s SECOND)
                WHERE venue_id=%s""", (REQUEST_INTERVAL, venue_id))
            conn.commit()
            try:
                deleted = bot.delete_message(
                    venue["channel_id"], row["message_id"], timeout=20
                )
                if deleted is not True:
                    raise ValueError("Telegram did not confirm duty message deletion")
            except apihelper.ApiTelegramException as error:
                description = error.description.lower()
                if error.error_code == 400 and "message to delete not found" in description:
                    deleted = True
                elif error.error_code == 429:
                    delay = max(REQUEST_INTERVAL, int(
                        error.result_json.get("parameters", {}).get("retry_after", 30)
                    ) + 1)
                    cur.execute("""UPDATE venue_duty_cleanup_policy
                        SET next_request_at=DATE_ADD(UTC_TIMESTAMP(),INTERVAL %s SECOND)
                        WHERE venue_id=%s""", (delay, venue_id))
                    logger.warning("出勤清理限流 venue=%s message=%s wait=%s",
                                   venue_id, row["message_id"], delay)
                    conn.commit()
                    return True
                else:
                    _finish(cur, row, "failed", f"Telegram error {error.error_code}")
                    conn.commit()
                    logger.exception("出勤清理失败，需人工检查 venue=%s message=%s",
                                     venue_id, row["message_id"])
                    return True
            except (requests.RequestException, ValueError):
                logger.exception("出勤清理结果未确认，保留队列稍后核对重试 venue=%s message=%s",
                                 venue_id, row["message_id"])
                return True
            _finish(cur, row, "deleted")
            if row["kind"] == "footer":
                cur.execute("DELETE FROM bot_footers WHERE channel_id=%s AND message_id=%s",
                            (str(venue["channel_id"]), row["message_id"]))
            conn.commit()
            logger.info("已删除旧班次出勤消息 venue=%s message=%s", venue_id, row["message_id"])
            return True
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (f"venue_duty_cleanup_{venue_id}",))
        conn.close()


def start_worker(bot):
    ensure_tables()

    def run():
        while True:
            for venue_id in VENUE_IDS:
                try:
                    if run_once(bot, venue_id):
                        time.sleep(REQUEST_INTERVAL)
                except (pymysql.MySQLError, RuntimeError, ValueError):
                    logger.exception("旧班次出勤清理失败，暂不删除 venue=%s", venue_id)
            time.sleep(REQUEST_INTERVAL)

    thread = threading.Thread(target=run, name="venue-duty-cleanup", daemon=True)
    thread.start()
    return thread
