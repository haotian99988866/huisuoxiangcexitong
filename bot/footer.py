from html import escape
import logging
import threading

import pymysql
from telebot import types

from admin import get_setting
from config.channels import LISTEN_CHANNELS, STAFF_ARCHIVE_VENUES
from db import get_conn
from duty_shift import current_shift_window
from staff_archive import listing as staff_listing
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
MAX_FOOTER_ATTENDEES = 90
COMPLETE_ATTENDANCE_MARKER = "【出勤完整版】"
COMPLETE_ATTENDANCE_VENUES = {4, 5, 6}
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
    venue_id = config.get("venue_id")
    if venue_id in COMPLETE_ATTENDANCE_VENUES:
        return _complete_attendance_text(venue_id)
    template = get_setting("footer", FOOTER_TEXT)
    return template.replace("{name}", config.get("venue_name", "西港会所·外送"))


def _complete_attendance_text(venue_id):
    venue = STAFF_ARCHIVE_VENUES[venue_id]
    members = staff_listing({"venue_id": venue_id, "mode": "duty"})["members"]
    start, _, _ = current_shift_window()
    members_by_code = {}
    for member in members:
        code = str(member["code"]).strip()
        if code:
            members_by_code.setdefault(code, member)
    if len(members_by_code) > MAX_FOOTER_ATTENDEES:
        raise ValueError(
            f"{venue['venue_name']}本班出勤人数超过挂底可展示上限 {MAX_FOOTER_ATTENDEES}"
        )

    members = list(members_by_code.values())
    member_ids = [member["id"] for member in members if member.get("cover")]
    placeholders = ",".join(["%s"] * len(member_ids))
    posts_table = f"{venue['table_prefix']}_posts"
    pages_table = f"{venue['table_prefix']}_pages"
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            if member_ids:
                cursor.execute(
                    f"SELECT staff_id,message_id FROM {posts_table} "
                    f"WHERE staff_id IN ({placeholders}) AND status='sent' "
                    "AND message_id IS NOT NULL",
                    tuple(member_ids),
                )
                posts = cursor.fetchall()
            else:
                posts = []
            cursor.execute(
                f"SELECT message_id FROM {pages_table} "
                "WHERE page_number=1 AND status='sent' AND message_id IS NOT NULL"
            )
            page = cursor.fetchone()
    finally:
        conn.close()

    message_ids = {}
    for post in posts:
        message_id = post["message_id"]
        if type(message_id) is not int or message_id <= 0:
            logger.error("%s has invalid attendance photo message id: %r",
                         venue["venue_name"], message_id)
            continue
        message_ids[post["staff_id"]] = message_id

    attendance_groups = {}
    missing = []
    link_count = 0
    for member in members:
        group_name = str(member.get("group_name") or "类别待确认")
        attendance_groups.setdefault(group_name, []).append(member)
        if member.get("cover") and message_ids.get(member["id"]):
            link_count += 1
        else:
            missing.append(escape(str(member["code"])))
    entity_count = link_count + len(attendance_groups) + bool(missing) + 1
    if page and type(page.get("message_id")) is int and page["message_id"] > 0:
        entity_count += 1
    entity_count += 1
    if entity_count > 100:
        raise ValueError(f"{venue['venue_name']}本班出勤链接超过 Telegram 消息限制")

    date_text = start.date().isoformat()
    lines = [
        COMPLETE_ATTENDANCE_MARKER,
        f"<b>{escape(venue['venue_name'])}</b> · 实时出勤 · {date_text}",
        "",
    ]
    if not attendance_groups:
        lines.append("当前班次暂无公开出勤人员。")
    for group_name, group_members in attendance_groups.items():
        labels = set()
        for member in group_members:
            if member["price_usd"] is None:
                labels.add("价格待确认")
            else:
                detail = str(member.get("price_detail") or "")
                duration = detail.rsplit(" · ", 1)[-1] if detail.endswith("分钟") else "时长待确认"
                labels.add(f"${member['price_usd']} · {duration}")
        price_text = next(iter(labels)) if len(labels) == 1 else "价格请咨询"
        lines.append(f"<b>{escape(group_name)}</b>  {escape(price_text)}")
        codes = []
        for member in group_members:
            code = escape(str(member["code"]))
            message_id = message_ids.get(member["id"]) if member.get("cover") else None
            if message_id:
                url = f"https://t.me/{venue['channel_name']}/{message_id}"
                codes.append(f'<a href="{escape(url, quote=True)}">{code}</a>')
            else:
                codes.append(code)
        lines.append("  ".join(codes))

    if missing:
        lines.extend(["", "<b>暂无相册</b>  " + "  ".join(missing)])
    lines.extend(["", "点蓝字看相册"])
    if page and type(page.get("message_id")) is int and page["message_id"] > 0:
        page_url = f"https://t.me/{venue['channel_name']}/{page['message_id']}"
        lines.append(
            f'导航  <a href="{escape(page_url, quote=True)}">【人员全部名单】</a>'
            f'  ·  <a href="{escape(CHANNEL_URL, quote=True)}">【导航频道】</a>'
        )
    else:
        lines.append(f'导航  <a href="{escape(CHANNEL_URL, quote=True)}">【导航频道】</a>')
    return "\n".join(lines)


def skip_footer(channel_id):
    """已有专用出勤/档案枢纽的名玺频道不再挂通用底栏。"""
    config = LISTEN_CHANNELS.get(str(channel_id), {})
    return config.get("venue_id") in {1, 2}


def _clear_stored_footer(bot, channel_id):
    """停用挂底时清掉频道里残留的旧底栏。"""
    channel_id = str(channel_id)
    old = _get_last(channel_id)
    if not old:
        return
    try:
        bot.delete_message(channel_id, old)
    except Exception:
        pass
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM bot_footers WHERE channel_id = %s", (channel_id,))
        conn.commit()
    finally:
        conn.close()


def post_footer(bot, channel_id):
    """在频道最底部挂一条小程序推广信息,并删除上一条,保证频道里只留最新一条。"""
    channel_id = str(channel_id)
    if skip_footer(channel_id):
        _clear_stored_footer(bot, channel_id)
        return None
    try:
        text = footer_text(channel_id)
    except (pymysql.MySQLError, ValueError):
        logger.exception("生成挂底信息失败: %s", channel_id)
        return None
    try:
        sent = bot.send_message(
            channel_id,
            text,
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
    if skip_footer(channel_id):
        _clear_stored_footer(bot, channel_id)
        with _timers_lock:
            old = _timers.pop(channel_id, None)
            if old:
                old.cancel()
        return
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
