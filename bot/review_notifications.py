"""Shared customer review reasons and transactionally queued notifications."""
import logging
import threading
import time

from telebot import apihelper, types

from db import get_conn

logger = logging.getLogger(__name__)
CONTACT_URL = "https://t.me/haotian99988866"


def rejection_reason(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 300:
        raise ValueError("请填写拒绝原因，最多300字")
    value = value.strip()
    if any(ord(char) < 32 and char != "\n" for char in value):
        raise ValueError("拒绝原因不能包含控制字符")
    return value


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS review_notifications (
                event_key VARCHAR(80) PRIMARY KEY, user_id BIGINT NOT NULL,
                body TEXT NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'queued',
                error_code VARCHAR(64) NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
    finally:
        conn.close()


def enqueue(cur, event_key, user_id, text):
    cur.execute(
        "INSERT INTO review_notifications(event_key,user_id,body) VALUES(%s,%s,%s)",
        (event_key, user_id, text + "\n\n有疑问请联系管理员昊天，请保留申请／订单编号。"),
    )


def deliver_once(bot):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM review_notifications WHERE status='queued' ORDER BY created_at,event_key LIMIT 1 FOR UPDATE")
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return
            cur.execute("UPDATE review_notifications SET status='sending' WHERE event_key=%s", (row["event_key"],))
        conn.commit()
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("有疑问，联系管理员昊天", url=CONTACT_URL))
        status, error_code = "sent", None
        try:
            bot.send_message(row["user_id"], row["body"], reply_markup=keyboard, parse_mode=None)
        except (apihelper.ApiException, OSError) as error:
            status, error_code = "uncertain", type(error).__name__
            logger.error("Review notification delivery unconfirmed event=%s error=%s", row["event_key"], error_code)
        with conn.cursor() as cur:
            cur.execute("UPDATE review_notifications SET status=%s,error_code=%s,finished_at=NOW() WHERE event_key=%s",
                        (status, error_code, row["event_key"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def worker(bot):
    initialized = False
    while True:
        try:
            if not initialized:
                conn = get_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE review_notifications SET status='uncertain',error_code='Interrupted' WHERE status='sending'")
                    conn.commit()
                finally:
                    conn.close()
                initialized = True
                logger.warning("Review notifications READY: persistent customer review outbox")
            deliver_once(bot)
        except Exception:
            logger.exception("Review notification worker failed; check persisted delivery state")
        time.sleep(5)


def start_worker(bot):
    ensure_tables()
    threading.Thread(target=worker, args=(bot,), daemon=True, name="review-notifications").start()
