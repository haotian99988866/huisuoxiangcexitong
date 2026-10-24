import logging
import threading

from telebot import types

from admin import get_setting
from config.channels import LISTEN_CHANNELS
from db import get_conn
from welcome import BOT_URL, CHANNEL_URL, CONTACT_URL, MINIAPP_URL

logger = logging.getLogger(__name__)

FOOTER_TEXT = (
    "📍 <b>{name}</b>\n"
    "本频道每日更新：今日出勤名单 · 技师照片 · 价格\n\n"
    "📲 <b>一键查看全西港会所/外送</b>\n"
    "打开小程序，所有会所实时出勤、照片、价格一目了然\n\n"
    "🤖 <b>关注机器人</b>：订阅出勤提醒，名单一更新马上通知你，还能直接留言预约\n\n"
    "🧭 更多会所、外送，点【导航频道】"
)
FOOTER_BUTTON = "🌴 打开小程序，查看全西港"
BOT_BUTTON = "🤖 进入机器人·订阅提醒"
NAV_BUTTON = "🧭 导航频道"
CONTACT_BUTTON = "💬 联系昊天"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS bot_footers (
    channel_id VARCHAR(30) PRIMARY KEY,
    message_id BIGINT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
)
"""


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_SQL)
        conn.commit()
    finally:
        conn.close()


def _get_last(channel_id):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT message_id FROM bot_footers WHERE channel_id = %s", (channel_id,)
            )
            row = cursor.fetchone()
        return row["message_id"] if row else None
    finally:
        conn.close()


def _set_last(channel_id, message_id):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "REPLACE INTO bot_footers (channel_id, message_id) VALUES (%s, %s)",
                (str(channel_id), message_id),
            )
        conn.commit()
    finally:
        conn.close()


def footer_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(FOOTER_BUTTON, url=MINIAPP_URL))
    keyboard.row(
        types.InlineKeyboardButton(BOT_BUTTON, url=BOT_URL),
        types.InlineKeyboardButton(NAV_BUTTON, url=CHANNEL_URL),
    )
    keyboard.add(types.InlineKeyboardButton(CONTACT_BUTTON, url=CONTACT_URL))
    return keyboard


def footer_text(channel_id):
    config = LISTEN_CHANNELS.get(str(channel_id), {})
    template = get_setting("footer", FOOTER_TEXT)
    return template.replace("{name}", config.get("venue_name", "西港会所·外送"))


def post_footer(bot, channel_id):
    """在频道最底部挂一条小程序推广信息,并删除上一条,保证频道里只留最新一条。"""
    channel_id = str(channel_id)
    try:
        sent = bot.send_message(
            channel_id,
            footer_text(channel_id),
            parse_mode="HTML",
            reply_markup=footer_keyboard(),
            disable_web_page_preview=True,
            disable_notification=True,
        )
    except Exception:
        logger.exception("发送挂底信息失败: %s", channel_id)
        return None
    old = _get_last(channel_id)
    if old:
        try:
            bot.delete_message(channel_id, old)
        except Exception:
            pass
    _set_last(channel_id, sent.message_id)
    return sent


_timers = {}
_timers_lock = threading.Lock()
QUIET_SECONDS = 90


def schedule_footer(bot, channel_id, delay=QUIET_SECONDS):
    """连续发内容的频道(外送):安静 delay 秒后才挂底,期间再有新内容则重新计时。"""
    channel_id = str(channel_id)
    with _timers_lock:
        old = _timers.pop(channel_id, None)
        if old:
            old.cancel()
        timer = threading.Timer(delay, _fire, args=(bot, channel_id))
        timer.daemon = True
        _timers[channel_id] = timer
        timer.start()


def _fire(bot, channel_id):
    with _timers_lock:
        _timers.pop(channel_id, None)
    post_footer(bot, channel_id)
