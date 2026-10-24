import logging
import threading
import time

from telebot import types

from db import get_conn
from relay import RELAY_CONTENT_TYPES, user_summary
from visit_stats import format_stats

logger = logging.getLogger(__name__)

SEND_INTERVAL = 0.05
PANEL_BUTTON = "🛠 管理后台"

# 管理员待输入状态: {管理员ID: {"kind": "promo"|"broadcast", "panel": 后台消息ID}}
_pending = {}
# 待确认群发: {管理员ID: {"input": 原消息ID, "panel": 后台消息ID, "preview": 预览消息ID}}
_draft = {}
# 每个管理员当前的后台消息,重新打开时删掉旧的,保证聊天里只有一个
_panel_msg = {}
_keyboard_shown = set()

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS bot_settings (
    setting_key VARCHAR(50) PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
)
"""

PANEL_TEXT = (
    "🛠 <b>管理后台</b>\n\n"
    "📊 访问统计：小程序访问人数和次数\n"
    "👥 客人情况：客人数量和最近活跃\n"
    "✏️ 修改活动文案：改主菜单里的【最新活动】\n"
    "📢 群发公告：给所有客人发一条消息（发送前先预览确认）\n\n"
    "💡 回复客人留言即可回信；回复 /block 拉黑、/unblock 取消。\n"
    "找不到后台时，点输入框下方的【🛠 管理后台】按钮。"
)


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_SQL)
        conn.commit()
    finally:
        conn.close()


def get_setting(key, default=None):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = %s", (key,)
            )
            row = cursor.fetchone()
        return row["setting_value"] if row else default
    except Exception:
        logger.exception("读取设置失败: %s", key)
        return default
    finally:
        conn.close()


def set_setting(key, value):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            if value is None:
                cursor.execute("DELETE FROM bot_settings WHERE setting_key = %s", (key,))
            else:
                cursor.execute(
                    "REPLACE INTO bot_settings (setting_key, setting_value) VALUES (%s, %s)",
                    (key, value),
                )
        conn.commit()
    finally:
        conn.close()


def broadcast_targets():
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT tg_user_id FROM bot_users WHERE blocked = 0")
            return [row["tg_user_id"] for row in cursor.fetchall()]
    finally:
        conn.close()


def _panel_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("📊 访问统计", callback_data="adm:stats"),
        types.InlineKeyboardButton("👥 客人情况", callback_data="adm:users"),
        types.InlineKeyboardButton("✏️ 修改活动文案", callback_data="adm:promo"),
        types.InlineKeyboardButton("📢 群发公告", callback_data="adm:broadcast"),
    )
    return keyboard


def _back_keyboard(extra=None):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for button in extra or []:
        keyboard.add(button)
    keyboard.add(types.InlineKeyboardButton("⬅️ 返回后台", callback_data="adm:home"))
    return keyboard


def _quick_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    keyboard.add(types.KeyboardButton(PANEL_BUTTON))
    return keyboard


def _safe_delete(bot, chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def _edit(bot, chat_id, message_id, text, keyboard, html=True):
    """就地改写后台消息;内容没变化等情况忽略。"""
    try:
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
            parse_mode="HTML" if html else None,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception as error:
        if "not modified" not in str(error):
            logger.warning("更新后台消息失败: %s", error)


def _reset_state(bot, chat_id, admin):
    _pending.pop(admin, None)
    draft = _draft.pop(admin, None)
    if draft:
        _safe_delete(bot, chat_id, draft["preview"])


def send_panel(bot, chat_id, admin=None, command_message_id=None):
    """打开后台:删掉旧后台和命令消息,只保留一个新的。"""
    admin = admin or chat_id
    _reset_state(bot, chat_id, admin)
    old = _panel_msg.pop(admin, None)
    if old:
        _safe_delete(bot, chat_id, old)
    if command_message_id:
        _safe_delete(bot, chat_id, command_message_id)
    if admin not in _keyboard_shown:
        _keyboard_shown.add(admin)
        hint = bot.send_message(
            chat_id, "✅ 已在输入框下方添加【🛠 管理后台】快捷按钮", reply_markup=_quick_keyboard()
        )
        _safe_delete(bot, chat_id, hint.message_id)
    sent = bot.send_message(
        chat_id, PANEL_TEXT, parse_mode="HTML", reply_markup=_panel_keyboard()
    )
    _panel_msg[admin] = sent.message_id


def handle_admin_callback(bot, call):
    admin = call.from_user.id
    chat_id = call.message.chat.id
    panel = call.message.message_id
    action = (call.data or "").split(":", 1)[-1]
    bot.answer_callback_query(call.id)
    _panel_msg[admin] = panel

    if action == "home":
        _reset_state(bot, chat_id, admin)
        _edit(bot, chat_id, panel, PANEL_TEXT, _panel_keyboard())
    elif action == "stats":
        _edit(bot, chat_id, panel, format_stats(), _back_keyboard(), html=False)
    elif action == "users":
        _edit(bot, chat_id, panel, user_summary(), _back_keyboard(), html=False)
    elif action == "promo":
        _reset_state(bot, chat_id, admin)
        _pending[admin] = {"kind": "promo", "panel": panel}
        reset = types.InlineKeyboardButton("♻️ 恢复默认文案", callback_data="adm:promo_reset")
        _edit(
            bot,
            chat_id,
            panel,
            "✏️ <b>修改活动文案</b>\n\n请直接发送新的活动文案（纯文字，支持 &lt;b&gt;粗体&lt;/b&gt; 等 HTML 格式）。",
            _back_keyboard([reset]),
        )
    elif action == "promo_reset":
        _reset_state(bot, chat_id, admin)
        set_setting("promo", None)
        _edit(bot, chat_id, panel, "♻️ 已恢复默认活动文案。", _back_keyboard(), html=False)
    elif action == "broadcast":
        _reset_state(bot, chat_id, admin)
        _pending[admin] = {"kind": "broadcast", "panel": panel}
        _edit(
            bot,
            chat_id,
            panel,
            "📢 <b>群发公告</b>\n\n请发送要群发的内容（文字、图片、语音都可以）。\n我会先预览，确认后才发出。",
            _back_keyboard(),
        )
    elif action == "send":
        _start_broadcast(bot, admin, chat_id, panel)


def has_pending(admin_id):
    return admin_id in _pending


def handle_pending_message(bot, message):
    """管理员处于等待输入状态时,处理其发来的内容。"""
    admin = message.from_user.id
    chat_id = message.chat.id
    state = _pending.get(admin)
    if not state:
        return
    panel = state["panel"]

    if state["kind"] == "promo":
        if not message.text:
            _edit(bot, chat_id, panel, "❌ 活动文案只支持纯文字，请重新发送。", _back_keyboard(), html=False)
            _safe_delete(bot, chat_id, message.message_id)
            return
        try:
            bot.edit_message_text(
                "✅ 活动文案已更新，客人点【最新活动】即可看到。效果如下：\n\n" + message.text,
                chat_id,
                panel,
                parse_mode="HTML",
                reply_markup=_back_keyboard(),
            )
        except Exception as error:
            _edit(
                bot, chat_id, panel,
                f"❌ 文案格式有误（HTML 标签没写对）：{error}\n请修改后重新发送。",
                _back_keyboard(), html=False,
            )
            _safe_delete(bot, chat_id, message.message_id)
            return
        set_setting("promo", message.text)
        _pending.pop(admin, None)
        _safe_delete(bot, chat_id, message.message_id)
    else:
        _pending.pop(admin, None)
        count = len(broadcast_targets())
        preview = bot.copy_message(chat_id, chat_id, message.message_id)
        _draft[admin] = {
            "input": message.message_id,
            "panel": panel,
            "preview": preview.message_id,
        }
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(
            types.InlineKeyboardButton(f"✅ 确认发送给 {count} 人", callback_data="adm:send"),
            types.InlineKeyboardButton("⬅️ 取消，返回后台", callback_data="adm:home"),
        )
        _edit(
            bot, chat_id, panel,
            f"👇 上面是预览，将发给 <b>{count}</b> 位客人。确认发送吗？",
            keyboard,
        )
        _safe_delete(bot, chat_id, message.message_id)


def _start_broadcast(bot, admin, chat_id, panel):
    draft = _draft.get(admin)
    if not draft:
        _edit(bot, chat_id, panel, "没有待发送的内容，请重新操作。", _back_keyboard(), html=False)
        return
    targets = broadcast_targets()
    _edit(bot, chat_id, panel, f"📤 正在发送给 {len(targets)} 人…", None, html=False)
    threading.Thread(
        target=_run_broadcast, args=(bot, admin, chat_id, panel, draft, targets), daemon=True
    ).start()


def _run_broadcast(bot, admin, chat_id, panel, draft, targets):
    sent = failed = 0
    for user_id in targets:
        try:
            bot.copy_message(user_id, chat_id, draft["preview"])
            sent += 1
        except Exception:
            failed += 1
        time.sleep(SEND_INTERVAL)
    _draft.pop(admin, None)
    _safe_delete(bot, chat_id, draft["preview"])
    _edit(
        bot, chat_id, panel,
        f"✅ 群发完成：成功 {sent} 人，失败 {failed} 人（失败多为客人已屏蔽机器人）。",
        _back_keyboard(), html=False,
    )


BROADCAST_CONTENT_TYPES = RELAY_CONTENT_TYPES
