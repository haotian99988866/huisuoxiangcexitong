"""Persistent, once-per-post navigation for the owner's designated chats."""
import logging
import json
import threading
import time

import pymysql
from telebot import apihelper, types

from db import get_conn
from visit_stats import admin_ids


logger = logging.getLogger(__name__)
CHANNEL_ID = -1002214279762
GROUP_ID = -1002165469154
NEWS_CHANNEL_ID = -1002402734271
NEWS_TEXT = "联系昊天 · 西港娱乐 · 会员充值"
NEWS_BUTTONS = (
    ("联系昊天", "https://t.me/haotian99988866"),
    ("西港娱乐", "https://t.me/haotianzhaopian_bot"),
    ("会员充值", "https://t.me/haotianzhaopian_bot?startapp=membership"),
)
NAVIGATION_TEXT = "🧭 西港生活服务导航\n会所 · 外送 · 资讯 · 影音，选择下方入口"
ALBUM_QUIET_SECONDS = 20
BUTTON_ROWS = (
    (("活动与折扣专区", "https://t.me/haotianhd99988866"),),
    (("名玺按摩", "https://t.me/nhlg01"), ("名玺选妃", "https://t.me/mingxi03"),
     ("名玺KTV", "https://t.me/sbhui01")),
    (("大浪会所", "https://t.me/dlts02"), ("五象会所", "https://t.me/haotianydl99988866"),
     ("帝豪会所", "https://t.me/dihao02")),
    (("170", "https://t.me/haotianws99988866"), ("180", "https://t.me/haotiantk99988866"),
     ("160", "https://t.me/jinb201"), ("苹果", "https://t.me/haotianxp")),
    (("西港订房", "https://t.me/wnshs01"), ("香烟保真", "https://t.me/haotianxp999888666")),
    (("成人影音", "https://t.me/cryymk"), ("服务反馈", "https://t.me/+zuAAQWyth-xmN2Q1"),
     ("昊天大事件", "https://t.me/haotiancm99988866")),
    (("联系昊天", "https://t.me/haotian99988866"),
     ("西港娱乐信息", "https://app.haotianzhushou.com/?module=venue")),
)


def keyboard():
    markup = types.InlineKeyboardMarkup(row_width=4)
    for row in BUTTON_ROWS:
        markup.row(*(types.InlineKeyboardButton(text, url=url) for text, url in row))
    return markup


def news_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.row(*(types.InlineKeyboardButton(text, url=url) for text, url in NEWS_BUTTONS))
    return markup


def enqueue_recent_news():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,message_ids,media,published_at>=("
                        "SELECT enabled_at FROM post_navigation_policy WHERE id=1) AS allow_companion"
                        " FROM channel_announcements WHERE enabled=1"
                        " AND published_at>=UTC_TIMESTAMP() - INTERVAL 72 HOUR ORDER BY id DESC")
            pending = []
            for row in cur.fetchall():
                ids = json.loads(row["message_ids"]) if isinstance(row["message_ids"], str) else row["message_ids"]
                if not ids or any(type(ident) is not int or ident <= 0 for ident in ids):
                    raise ValueError(f"Invalid news message IDs for announcement {row['id']}")
                media = json.loads(row["media"]) if isinstance(row["media"], str) else row["media"] or []
                button_ids = [ids[-1]] if media and len(ids) > len(media) else ids
                pending.append((NEWS_CHANNEL_ID, f"news:{row['id']}", min(button_ids), max(button_ids),
                                row["allow_companion"]))
            if pending:
                cur.executemany("INSERT INTO post_navigation_jobs"
                                " (chat_id,post_key,first_message_id,last_message_id,allow_companion,due_at)"
                                " VALUES (%s,%s,%s,%s,%s,UTC_TIMESTAMP())"
                                " ON DUPLICATE KEY UPDATE post_key=VALUES(post_key)", pending)
        conn.commit()
    finally:
        conn.close()


def accepts(message):
    if message.chat.id not in (CHANNEL_ID, GROUP_ID):
        return False
    if message.content_type not in ("text", "photo", "video"):
        return False
    if getattr(message, "is_automatic_forward", False) or getattr(message, "via_bot", None):
        return False
    author = getattr(message, "from_user", None)
    if author and author.is_bot:
        return False
    if (getattr(message, "text", None) or getattr(message, "caption", None)) == NAVIGATION_TEXT:
        return False
    if message.chat.id == GROUP_ID:
        return bool(author and author.id in admin_ids())
    # The owner confirmed that only their own posts are published in this channel.
    return True


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS post_navigation_jobs (
                chat_id BIGINT NOT NULL,
                post_key VARCHAR(80) NOT NULL,
                first_message_id BIGINT NOT NULL,
                last_message_id BIGINT NOT NULL,
                due_at DATETIME NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'queued',
                result_id BIGINT NULL,
                error VARCHAR(255) NULL,
                PRIMARY KEY (chat_id,post_key),
                INDEX idx_post_navigation_pending (status,due_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("SHOW COLUMNS FROM post_navigation_jobs LIKE 'allow_companion'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE post_navigation_jobs ADD COLUMN allow_companion"
                            " TINYINT NOT NULL DEFAULT 0")
            cur.execute("CREATE TABLE IF NOT EXISTS post_navigation_policy"
                        " (id INT PRIMARY KEY,enabled_at DATETIME NOT NULL)")
            cur.execute("INSERT IGNORE INTO post_navigation_policy VALUES (1,UTC_TIMESTAMP())")
        conn.commit()
    finally:
        conn.close()


def enqueue(message):
    album = getattr(message, "media_group_id", None)
    key = f"album:{album}" if album else f"message:{message.message_id}"
    delay = ALBUM_QUIET_SECONDS if album else 1
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO post_navigation_jobs"
                " (chat_id,post_key,first_message_id,last_message_id,due_at)"
                " VALUES (%s,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(),INTERVAL %s SECOND))"
                " ON DUPLICATE KEY UPDATE"
                " due_at=IF(status='queued' AND VALUES(last_message_id)>last_message_id,"
                " VALUES(due_at),due_at),"
                " first_message_id=LEAST(first_message_id,VALUES(first_message_id)),"
                " last_message_id=GREATEST(last_message_id,VALUES(last_message_id))",
                (message.chat.id, key, message.message_id, message.message_id, delay),
            )
        conn.commit()
    finally:
        conn.close()


def claim():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM post_navigation_jobs WHERE status='queued'"
                        " AND due_at<=UTC_TIMESTAMP() ORDER BY due_at,chat_id,post_key LIMIT 1 FOR UPDATE")
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE post_navigation_jobs SET status='sending'"
                            " WHERE chat_id=%s AND post_key=%s", (row["chat_id"], row["post_key"]))
        conn.commit()
        return row
    finally:
        conn.close()


def update(row, status, error=None, result_id=None, retry_seconds=0):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE post_navigation_jobs SET status=%s,error=%s,result_id=%s,"
                        " due_at=DATE_ADD(UTC_TIMESTAMP(),INTERVAL %s SECOND)"
                        " WHERE chat_id=%s AND post_key=%s",
                        (status, error, result_id, retry_seconds, row["chat_id"], row["post_key"]))
        conn.commit()
    finally:
        conn.close()


def deliver(bot, row):
    news = row["chat_id"] == NEWS_CHANNEL_ID
    companion = news and row["first_message_id"] != row["last_message_id"]
    if companion:
        update(row, "skipped", "Multi-message news cannot carry direct buttons; no companion posted")
        return 1
    if news:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM channel_announcements WHERE id=%s AND enabled=1"
                            " AND published_at>=UTC_TIMESTAMP() - INTERVAL 72 HOUR",
                            (int(row["post_key"].split(":")[1]),))
                visible = cur.fetchone()
        finally:
            conn.close()
        if not visible:
            update(row, "skipped", "News expired or unavailable")
            return 1
    markup = news_keyboard() if news else keyboard()
    options = {}
    if row["chat_id"] == GROUP_ID or companion:
        options["reply_parameters"] = types.ReplyParameters(
            message_id=row["first_message_id"], allow_sending_without_reply=False)
    try:
        if ((row["chat_id"] == CHANNEL_ID and row["post_key"].startswith("message:"))
                or (news and not companion)):
            sent = bot.edit_message_reply_markup(chat_id=row["chat_id"],
                                                 message_id=row["first_message_id"],
                                                 reply_markup=markup, timeout=20)
        else:
            sent = bot.send_message(row["chat_id"], NEWS_TEXT if news else NAVIGATION_TEXT, reply_markup=markup,
                                    disable_notification=True, disable_web_page_preview=True,
                                    timeout=20, **options)
    except apihelper.ApiTelegramException as error:
        if error.error_code == 400 and "message is not modified" in error.description.lower():
            update(row, "sent", result_id=row["first_message_id"])
            logger.info("News navigation already present for %s", row["post_key"])
            return 1
        if error.error_code == 429:
            delay = max(1, int(error.result_json.get("parameters", {}).get("retry_after", 30)))
            update(row, "queued", "Telegram rate limit", retry_seconds=delay)
            logger.warning("Navigation rate limited in %s; waiting %s seconds", row["chat_id"], delay)
            return delay
        update(row, "failed" if error.error_code in (400, 403) else "uncertain",
               f"Telegram error {error.error_code}")
        logger.exception("Navigation rejected for %s:%s", row["chat_id"], row["post_key"])
    except Exception:
        update(row, "uncertain", "Send outcome unknown; inspect before retrying")
        logger.exception("Navigation send outcome unknown for %s:%s; not resent",
                         row["chat_id"], row["post_key"])
    else:
        update(row, "sent", result_id=sent.message_id if sent is not True else row["first_message_id"])
        logger.info("Navigation sent for %s:%s", row["chat_id"], row["post_key"])
    return 1


def worker(bot):
    next_news_scan = 0
    while True:
        delay = 1
        try:
            if time.monotonic() >= next_news_scan:
                enqueue_recent_news()
                next_news_scan = time.monotonic() + 30
            row = claim()
            if row:
                delay = deliver(bot, row)
        except pymysql.MySQLError:
            logger.exception("Navigation queue database unavailable; retrying in 5 seconds")
            delay = 5
        except ValueError:
            logger.exception("Invalid news navigation metadata; retrying in 30 seconds")
            delay = 30
        time.sleep(delay)


def start_worker(bot):
    ensure_tables()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE post_navigation_jobs SET status='uncertain',"
                        " error='Interrupted during send; inspect before retrying' WHERE status='sending'")
        conn.commit()
    finally:
        conn.close()
    threading.Thread(target=worker, args=(bot,), daemon=True, name="post-navigation").start()
    logger.warning("Post navigation READY: owner group posts and channel posts; albums grouped")
