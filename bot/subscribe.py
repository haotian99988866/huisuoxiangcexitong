import logging
import threading
import time

from telebot import types

from db import get_conn
from welcome import MINIAPP_URL

logger = logging.getLogger(__name__)

# 可订阅出勤提醒的会所(有白班/晚班名单)
SUBSCRIBE_VENUES = {
    1: "名玺水汇按摩",
    2: "名玺水汇会所",
    4: "凯迪/大浪淘沙会所",
    5: "大西洋/五象会所",
    6: "帝豪会所",
}
SEND_INTERVAL = 0.05

CREATE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS bot_subscriptions (
        tg_user_id BIGINT NOT NULL,
        venue_id INT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (tg_user_id, venue_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_push_log (
        venue_id INT NOT NULL,
        duty_date DATE NOT NULL,
        duty_shift VARCHAR(20) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (venue_id, duty_date, duty_shift)
    )
    """,
)


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            for sql in CREATE_SQL:
                cursor.execute(sql)
        conn.commit()
    finally:
        conn.close()


def get_subscriptions(user_id):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT venue_id FROM bot_subscriptions WHERE tg_user_id = %s", (user_id,)
            )
            return {row["venue_id"] for row in cursor.fetchall()}
    finally:
        conn.close()


def toggle_subscription(user_id, venue_id):
    """切换订阅状态,返回切换后是否已订阅。"""
    if venue_id not in SUBSCRIBE_VENUES:
        return None
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM bot_subscriptions WHERE tg_user_id = %s AND venue_id = %s",
                (user_id, venue_id),
            )
            if cursor.rowcount:
                subscribed = False
            else:
                cursor.execute(
                    "INSERT INTO bot_subscriptions (tg_user_id, venue_id) VALUES (%s, %s)",
                    (user_id, venue_id),
                )
                subscribed = True
        conn.commit()
        return subscribed
    finally:
        conn.close()


def remove_user_subscriptions(user_id):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM bot_subscriptions WHERE tg_user_id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()


def claim_push(venue_id, duty_date, duty_shift):
    """每个会所每个班次只推送一次;首次调用返回 True。"""
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT IGNORE INTO bot_push_log (venue_id, duty_date, duty_shift) "
                "VALUES (%s, %s, %s)",
                (venue_id, duty_date, duty_shift),
            )
            claimed = cursor.rowcount == 1
        conn.commit()
        return claimed
    finally:
        conn.close()


def subscribers_of(venue_id):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT s.tg_user_id FROM bot_subscriptions s "
                "LEFT JOIN bot_users u ON u.tg_user_id = s.tg_user_id "
                "WHERE s.venue_id = %s AND COALESCE(u.blocked, 0) = 0",
                (venue_id,),
            )
            return [row["tg_user_id"] for row in cursor.fetchall()]
    finally:
        conn.close()


def delete_old_push_logs(days=7):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM bot_push_log WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
                (days,),
            )
            count = cursor.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def push_text(venue_id, duty_shift, count):
    return (
        f"🔔 <b>{SUBSCRIBE_VENUES[venue_id]}</b> {duty_shift}出勤已更新\n"
        f"今日共 <b>{count}</b> 位技师在岗\n\n"
        "👇 点下方按钮查看照片和价格"
    )


def _push_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton("🌴 查看实时出勤", url=MINIAPP_URL))
    keyboard.row(types.InlineKeyboardButton("🔕 管理提醒", callback_data="sub:menu"))
    return keyboard


def _broadcast(bot, venue_id, duty_shift, count):
    text = push_text(venue_id, duty_shift, count)
    sent = 0
    for user_id in subscribers_of(venue_id):
        try:
            bot.send_message(user_id, text, parse_mode="HTML", reply_markup=_push_keyboard())
            sent += 1
        except Exception as error:
            # 用户屏蔽机器人后,清除其订阅
            if "403" in str(error) or "blocked" in str(error).lower():
                remove_user_subscriptions(user_id)
            else:
                logger.warning("出勤提醒发送失败 %s: %s", user_id, error)
        time.sleep(SEND_INTERVAL)
    logger.info("出勤提醒已推送 venue=%s shift=%s 人数=%s", venue_id, duty_shift, sent)


def notify_attendance(bot, venue_id, duty_date, duty_shift, count):
    """出勤名单保存后调用;后台线程推送,不阻塞频道消息处理。"""
    if venue_id not in SUBSCRIBE_VENUES or duty_shift not in ("白班", "晚班"):
        return False
    if not claim_push(venue_id, duty_date, duty_shift):
        return False
    threading.Thread(
        target=_broadcast, args=(bot, venue_id, duty_shift, count), daemon=True
    ).start()
    return True


def sub_menu_text():
    return (
        "🔔 <b>出勤提醒</b>\n\n"
        "选择想关注的会所，白班 / 晚班名单一发布，机器人就会第一时间通知你。\n"
        "点一下订阅，再点一下取消。"
    )


def sub_menu_keyboard(user_id):
    subscribed = get_subscriptions(user_id)
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for venue_id, name in SUBSCRIBE_VENUES.items():
        mark = "✅" if venue_id in subscribed else "➕"
        keyboard.add(
            types.InlineKeyboardButton(f"{mark} {name}", callback_data=f"sub:t:{venue_id}")
        )
    keyboard.add(types.InlineKeyboardButton("⬅️ 返回主菜单", callback_data="menu:home"))
    return keyboard


def show_sub_menu(bot, call):
    """处理 sub: 回调:打开菜单或切换订阅,就地刷新。"""
    parts = (call.data or "").split(":")
    notice = None
    if len(parts) == 3 and parts[1] == "t" and parts[2].isdigit():
        result = toggle_subscription(call.from_user.id, int(parts[2]))
        if result is not None:
            notice = "已订阅，名单发布时通知你" if result else "已取消提醒"
    bot.answer_callback_query(call.id, notice)
    try:
        bot.edit_message_text(
            sub_menu_text(),
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=sub_menu_keyboard(call.from_user.id),
        )
    except Exception:
        pass
