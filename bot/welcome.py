from urllib.parse import quote

from telebot import types

from admin import get_setting
MINIAPP_URL = "https://t.me/haotianzhaopian_bot?startapp"
MOVIES_URL = "https://t.me/haotianzhaopian_bot?startapp=movies"
CONTACT_URL = "https://t.me/haotian99988866"
CHANNEL_URL = "https://t.me/haotianyl99988866"
BOT_URL = "https://t.me/haotianzhaopian_bot"
SHARE_URL = (
    "https://t.me/share/url?url="
    + quote(BOT_URL, safe="")
    + "&text="
    + quote("西港会所/外送实时出勤、技师照片、价格，一键自助查询", safe="")
)

HOME_TEXT = (
    "🌴 <b>欢迎使用 昊天西港查询助手</b>\n\n"
    "西港会所 · 外送，一站查看：\n"
    "✅ 今日白班 / 晚班出勤\n"
    "✅ 技师照片与资料\n"
    "✅ 价格与服务项目\n"
    "✅ 西港外送实时信息\n"
    "✅ 🎬 影视观影，按分类找剧、找综艺\n\n"
    "👇 点下面的按钮开始"
)

PROMO_TEXT = (
    "💎 <b>名玺·水汇 半价活动</b> 💎\n\n"
    "⏱ 服务时间：45分钟，不限次\n"
    "📅 活动时间：10月全天\n\n"
    "🎫 <b>门票 $39</b>\n"
    "含泡澡、汗蒸、自助餐、休息、台球、电玩，24小时畅享\n"
    "（超时每12小时加收1张门票）\n\n"
    "🎁 消费项目满 <b>$58</b>，免 $39 门票"
)

HELP_TEXT = (
    "📖 <b>使用指南</b>\n\n"
    "<b>1️⃣ 查看实时出勤</b>\n"
    "点【🌴 实时出勤】进入小程序，顶部下拉框选择会所，即可看到当前班次的出勤名单。\n\n"
    "<b>2️⃣ 白班 / 晚班</b>\n"
    "白班 06:00–18:00，晚班 18:00–次日06:00。到点自动切换，名单以当班为准。\n\n"
    "<b>3️⃣ 看技师详情</b>\n"
    "点技师工号，查看照片、身高体重、产地和服务介绍。\n\n"
    "<b>4️⃣ 西港外送</b>\n"
    "小程序里选择【西港外送】，只展示晚班出勤；白班请直接联系昊天。\n\n"
    "<b>5️⃣ 出勤提醒</b>\n"
    "点主菜单【🔔 出勤提醒】订阅会所，名单一发布就通知你。\n\n"
    "<b>6️⃣ 预约 / 咨询</b>\n"
    "点【💬 联系昊天】，或直接在这里发文字、图片、语音留言，我会回复你。\n\n"
    "💡 小提示：名单会随频道实时更新，下单前建议再刷新一次。"
)


def home_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        types.InlineKeyboardButton("🌴 实时出勤", url=MINIAPP_URL),
        types.InlineKeyboardButton("🎬 观影", url=MOVIES_URL),
    )
    keyboard.row(
        types.InlineKeyboardButton("🎉 最新活动", callback_data="menu:promo"),
        types.InlineKeyboardButton("📖 使用指南", callback_data="menu:help"),
    )
    keyboard.row(
        types.InlineKeyboardButton("💬 联系昊天", url=CONTACT_URL),
        types.InlineKeyboardButton("📢 频道", url=CHANNEL_URL),
    )
    keyboard.row(
        types.InlineKeyboardButton("🔔 出勤提醒", callback_data="sub:menu"),
        types.InlineKeyboardButton("🔗 分享给朋友", url=SHARE_URL),
    )
    return keyboard


def sub_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("🌴 查看实时出勤", url=MINIAPP_URL),
        types.InlineKeyboardButton("💬 联系昊天预约", url=CONTACT_URL),
        types.InlineKeyboardButton("⬅️ 返回主菜单", callback_data="menu:home"),
    )
    return keyboard


SCREENS = {
    "home": (HOME_TEXT, home_keyboard),
    "promo": (PROMO_TEXT, sub_keyboard),
    "help": (HELP_TEXT, sub_keyboard),
}


def screen_text(name):
    """活动文案可由管理员在后台修改,其余固定。"""
    if name == "promo":
        return get_setting("promo", PROMO_TEXT)
    return SCREENS[name][0]


def send_welcome(bot, chat_id):
    bot.send_message(
        chat_id,
        HOME_TEXT,
        parse_mode="HTML",
        reply_markup=home_keyboard(),
        disable_web_page_preview=True,
    )


def send_help(bot, chat_id):
    text, keyboard = SCREENS["help"]
    bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=keyboard(),
        disable_web_page_preview=True,
    )


def show_screen(bot, call):
    """点击菜单按钮时就地替换消息内容,不刷屏。"""
    name = (call.data or "").split(":", 1)[-1]
    screen = SCREENS.get(name)
    bot.answer_callback_query(call.id)
    if not screen:
        return
    _, keyboard = screen
    text = screen_text(name)
    try:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=keyboard(),
            disable_web_page_preview=True,
        )
    except Exception:
        # 内容没变化或消息过旧时,忽略即可
        pass
