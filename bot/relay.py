import logging
import time
from collections import defaultdict, deque

from db import get_conn
from visit_stats import admin_ids

logger = logging.getLogger(__name__)

RATE_LIMIT_COUNT = 10
RATE_LIMIT_WINDOW = 60
ACK_INTERVAL_SECONDS = 600
RELAY_RETENTION_DAYS = 30
RELAY_CONTENT_TYPES = [
    "text", "photo", "voice", "video", "video_note",
    "audio", "document", "sticker", "animation", "location", "contact",
]

CREATE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS bot_users (
        tg_user_id BIGINT PRIMARY KEY,
        username VARCHAR(64) NULL,
        display_name VARCHAR(128) NULL,
        language VARCHAR(8) NOT NULL DEFAULT 'zh',
        blocked TINYINT(1) NOT NULL DEFAULT 0,
        message_count INT NOT NULL DEFAULT 0,
        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_relay (
        admin_chat_id BIGINT NOT NULL,
        admin_message_id BIGINT NOT NULL,
        customer_id BIGINT NOT NULL,
        customer_message_id BIGINT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (admin_chat_id, admin_message_id),
        INDEX idx_bot_relay_time (created_at)
    )
    """,
)

_recent = defaultdict(deque)
_last_ack = {}


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            for sql in CREATE_SQL:
                cursor.execute(sql)
            cursor.execute("SHOW COLUMNS FROM bot_relay LIKE 'customer_message_id'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE bot_relay ADD COLUMN customer_message_id BIGINT NULL")
        conn.commit()
    finally:
        conn.close()


def _display_name(user):
    return " ".join(
        part for part in (user.first_name, user.last_name) if part
    )[:128]


def touch_user(user, count_message=False):
    """记录/更新客人资料,返回该客人是否被拉黑。"""
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bot_users (tg_user_id, username, display_name, message_count)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    username = VALUES(username),
                    display_name = VALUES(display_name),
                    message_count = message_count + VALUES(message_count),
                    last_active = CURRENT_TIMESTAMP
                """,
                (user.id, (user.username or "")[:64], _display_name(user),
                 1 if count_message else 0),
            )
            cursor.execute(
                "SELECT blocked FROM bot_users WHERE tg_user_id = %s", (user.id,)
            )
            row = cursor.fetchone()
        conn.commit()
        return bool(row and row["blocked"])
    finally:
        conn.close()


def set_blocked(customer_id, blocked):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE bot_users SET blocked = %s WHERE tg_user_id = %s",
                (1 if blocked else 0, customer_id),
            )
            changed = cursor.rowcount
        conn.commit()
        return changed > 0
    finally:
        conn.close()


def remember_relay(admin_chat_id, admin_message_id, customer_id, customer_message_id=None):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "REPLACE INTO bot_relay (admin_chat_id, admin_message_id, customer_id, "
                "customer_message_id) VALUES (%s, %s, %s, %s)",
                (admin_chat_id, admin_message_id, customer_id, customer_message_id),
            )
        conn.commit()
    finally:
        conn.close()


def find_relay(admin_chat_id, admin_message_id):
    """返回 (客人ID, 客人原消息ID),找不到返回 (None, None)。"""
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT customer_id, customer_message_id FROM bot_relay "
                "WHERE admin_chat_id = %s AND admin_message_id = %s",
                (admin_chat_id, admin_message_id),
            )
            row = cursor.fetchone()
        if not row:
            return None, None
        return row["customer_id"], row["customer_message_id"]
    finally:
        conn.close()


def find_customer(admin_chat_id, admin_message_id):
    return find_relay(admin_chat_id, admin_message_id)[0]


def delete_old_relays(days=RELAY_RETENTION_DAYS):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM bot_relay WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
                (days,),
            )
            count = cursor.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def user_summary():
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(last_active >= DATE_SUB(NOW(), INTERVAL 1 DAY)) AS day1, "
                "SUM(last_active >= DATE_SUB(NOW(), INTERVAL 7 DAY)) AS day7, "
                "SUM(blocked) AS blocked FROM bot_users"
            )
            row = cursor.fetchone()
            cursor.execute(
                "SELECT tg_user_id, username, display_name, last_active "
                "FROM bot_users ORDER BY last_active DESC LIMIT 10"
            )
            latest = cursor.fetchall()
    finally:
        conn.close()
    lines = [
        "👥 机器人客人",
        "",
        f"累计：{row['total']} 人",
        f"24小时内活跃：{int(row['day1'] or 0)} 人",
        f"7天内活跃：{int(row['day7'] or 0)} 人",
        f"已拉黑：{int(row['blocked'] or 0)} 人",
        "",
        "🕒 最近活跃：",
    ]
    for item in latest:
        who = f"@{item['username']}" if item["username"] else (
            item["display_name"] or str(item["tg_user_id"])
        )
        lines.append(f"{item['last_active']:%m-%d %H:%M}  {who}")
    return "\n".join(lines)


def is_rate_limited(user_id):
    now = time.time()
    window = _recent[user_id]
    while window and now - window[0] > RATE_LIMIT_WINDOW:
        window.popleft()
    if len(window) >= RATE_LIMIT_COUNT:
        return True
    window.append(now)
    return False


def should_ack(user_id):
    now = time.time()
    if now - _last_ack.get(user_id, 0) < ACK_INTERVAL_SECONDS:
        return False
    _last_ack[user_id] = now
    return True


def customer_header(user):
    who = f"@{user.username}" if user.username else "无用户名"
    return (
        f"📩 新留言\n"
        f"👤 {_display_name(user) or '未知'}（{who}）\n"
        f"🆔 {user.id}\n\n"
        f"↩️ 直接回复这条或下面的消息，就会发给客人。\n"
        f"🚫 回复 /block 拉黑，/unblock 取消拉黑。"
    )


def relay_to_admins(bot, message):
    """把客人消息转给管理员,并记录对应关系便于回复。返回是否送达至少一人。"""
    delivered = False
    for admin in admin_ids():
        try:
            header = bot.send_message(admin, customer_header(message.from_user))
            copied = bot.copy_message(admin, message.chat.id, message.message_id)
            remember_relay(admin, header.message_id, message.from_user.id)
            remember_relay(admin, copied.message_id, message.from_user.id, message.message_id)
            delivered = True
        except Exception:
            logger.exception("转发留言给管理员失败: %s", admin)
    return delivered


def relay_admin_reply(bot, message):
    """管理员回复转发消息时,把内容发回给客人。返回是否已处理。"""
    reply_to = message.reply_to_message
    if not reply_to:
        return False
    customer_id, origin_id = find_relay(message.chat.id, reply_to.message_id)
    if customer_id is None:
        return False

    text = (message.text or "").strip()
    if text in ("/block", "/unblock"):
        changed = set_blocked(customer_id, text == "/block")
        bot.send_message(
            message.chat.id,
            ("🚫 已拉黑 " if text == "/block" else "✅ 已取消拉黑 ") + str(customer_id)
            if changed else "没有找到这位客人。",
        )
        return True

    try:
        options = {}
        if origin_id:
            options = {"reply_to_message_id": origin_id, "allow_sending_without_reply": True}
        bot.copy_message(customer_id, message.chat.id, message.message_id, **options)
        bot.send_message(message.chat.id, "✅ 已发送给客人", reply_to_message_id=message.message_id)
    except Exception as error:
        logger.warning("回复客人失败: %s", error)
        bot.send_message(
            message.chat.id,
            "❌ 发送失败，客人可能已屏蔽机器人。",
            reply_to_message_id=message.message_id,
        )
    return True
