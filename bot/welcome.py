from urllib.parse import quote
from telebot import types

from admin import get_setting
from menu_messages import keep_latest
MINIAPP_URL = "https://t.me/haotianzhaopian_bot?startapp"
VENUE_URL = "https://t.me/haotianzhaopian_bot?startapp=venue"
DELIVERY_URL = "https://t.me/haotianzhaopian_bot?startapp=delivery"
HOTELS_URL = "https://t.me/haotianzhaopian_bot?startapp=hotels"
SHORT_DRAMAS_URL = "https://t.me/haotianzhaopian_bot?startapp=short_dramas"
NEWS_URL = "https://t.me/haotianzhaopian_bot?startapp=announcements"
MOVIES_URL = "https://t.me/haotianzhaopian_bot?startapp=movies"
MUSIC_URL = "https://t.me/haotianzhaopian_bot?startapp=music"
RECHARGE_URL = "https://t.me/haotianzhaopian_bot?startapp=recharge"
SERVICES_URL = "https://t.me/haotianzhaopian_bot?startapp=services"
PERSONAL_URL = "https://t.me/haotianzhaopian_bot?startapp=personal"
MEMBERSHIP_URL = "https://t.me/haotianzhaopian_bot?startapp=membership"
PHONE_URL = "https://t.me/haotianzhaopian_bot?startapp=phone"
CONTACT_URL = "https://t.me/haotian99988866"
CHANNEL_URL = "https://t.me/haotianyl99988866"
BOT_URL = "https://t.me/haotianzhaopian_bot"
WEBAPP_BASE_URL = "https://app.haotianzhushou.com"
WEBAPP_PATHS = {
    VENUE_URL: "/?module=venue",
    DELIVERY_URL: "/?module=delivery",
    HOTELS_URL: "/hotels",
    MOVIES_URL: "/movies",
    SHORT_DRAMAS_URL: "/short-dramas",
    MUSIC_URL: "/music",
    NEWS_URL: "/announcements",
    MEMBERSHIP_URL: "/membership",
    PHONE_URL: "/phone-topup",
    RECHARGE_URL: "/recharge",
    PERSONAL_URL: "/personal",
    SERVICES_URL: "/services",
    CHANNEL_URL: "/channel.html",
    CONTACT_URL: "/channel.html?target=contact",
}
SHARE_URL = (
    "https://t.me/share/url?url="
    + quote(BOT_URL, safe="")
    + "&text="
    + quote("昊天生活通 · 西港生活服务与东南亚资讯，一站查看", safe="")
)

HOME_TEXT = (
    "<b>欢迎使用昊天生活通</b>\n"
    "西港生活服务，一站直达。\n\n"
    "会所出勤 · 西港外送 · 酒店推荐\n"
    "影视观影 · 短剧合集 · 在线音乐\n"
    "东南亚资讯 · 出勤与新闻订阅\n"
    "Telegram会员 · 话费代充 · 我的账户\n\n"
    "充值订单人工处理，非即时到账。\n"
    "下方功能按钮直接打开网页。\n"
    "账户操作如需登录，请从左下角【昊天生活通】进入。"
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
    "底部网页按钮直接打开功能，不发送消息；账户、下单、收藏和人员订阅如需登录，请从左下角【昊天生活通】进入。\n\n"
    "<b>1️⃣ 查看实时出勤</b>\n"
    "点【会所出勤】进入小程序，顶部下拉框选择会所，即可看到当前班次的出勤名单。\n\n"
    "<b>2️⃣ 白班 / 晚班</b>\n"
    "白班 06:00–18:00，晚班 18:00–次日06:00。到点自动切换，名单以当班为准。\n\n"
    "<b>3️⃣ 看技师详情</b>\n"
    "点技师工号，查看照片、身高体重、产地和服务介绍。\n\n"
    "<b>4️⃣ 西港外送</b>\n"
    "点【西港外送】进入外送板块，只展示晚班出勤；白班请直接联系昊天。\n\n"
    "<b>5️⃣ 出勤提醒</b>\n"
    "点【出勤提醒】或发送 /remind，订阅会所当班更新或管理已有订阅；人员提醒在对应资料页订阅。\n\n"
    "<b>6️⃣ 预约 / 咨询</b>\n"
    "点【联系昊天】，或直接在这里发文字、图片、语音留言，我会回复你。\n\n"
    "<b>7️⃣ 东南亚大事件订阅</b>\n"
    "点【昊天大事件】浏览近三天资讯；点【新闻订阅】或发送 /news，确认后接收新资讯提醒；发送 /news_off 随时取消。\n\n"
    "<b>8️⃣ 影视观影</b>\n"
    "点【影视观影】进入影视模块，按分类或搜索查找内容；点【短剧合集】查看合集及Telegram播放入口。\n\n"
    "<b>9️⃣ 在线音乐</b>\n"
    "点【在线音乐】进入播放器，可按语言分类、搜索歌曲，支持播放队列、收藏和歌单；收藏与歌单需在 Telegram 中登录使用。\n\n"
    "<b>🔟 酒店推荐</b>\n"
    "点【酒店推荐】查看酒店照片、地图与前台联系方式。\n\n"
    "💳 底部【首页】查看内容，【服务】选择会员套餐，【我的】管理余额、充值记录和购买订单。"
    "小程序自动识别账户，订单进度在【我的】查看。\n\n"
    "<b>⭐ Telegram会员充值</b>\n"
    "点主菜单【Telegram会员】，选择3 / 6 / 12个月套餐，可为自己或他人赠送；"
    "核对收件人及金额后使用钱包余额购买，由管理员人工开通，不是即时自动到账。\n\n"
    "<b>📱 柬埔寨话费充值</b>\n"
    "点主菜单【话费充值】，填写手机号和充值金额，核对手续费与总额后使用钱包余额支付，由管理员人工代充。"
    "付款结果未确认时先查看订单，不要重复提交。\n"
    "点【余额充值】提交钱包充值申请，待管理员核实到账；点【我的账户】查看余额和订单，或点【服务中心】选择便民服务。普通浏览器可发送 /member 获取网站登录链接。\n"
    "付款或赠送问题可发送 /paysupport 联系管理员。\n\n"
    "💡 小提示：名单会随频道实时更新，下单前建议再刷新一次。"
)


def home_keyboard(user_id=None):
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    keyboard.row(
        types.InlineKeyboardButton("会所出勤", url=VENUE_URL),
        types.InlineKeyboardButton("西港外送", url=DELIVERY_URL),
        types.InlineKeyboardButton("酒店推荐", url=HOTELS_URL),
    )
    keyboard.row(
        types.InlineKeyboardButton("影视观影", url=MOVIES_URL),
        types.InlineKeyboardButton("短剧合集", url=SHORT_DRAMAS_URL),
        types.InlineKeyboardButton("在线音乐", url=MUSIC_URL),
    )
    keyboard.row(
        types.InlineKeyboardButton("昊天大事件", url=NEWS_URL),
        types.InlineKeyboardButton("新闻订阅", callback_data="news:menu"),
        types.InlineKeyboardButton("出勤提醒", callback_data="sub:menu"),
    )
    keyboard.row(
        types.InlineKeyboardButton("Telegram会员", url=MEMBERSHIP_URL),
        types.InlineKeyboardButton("话费充值", url=PHONE_URL),
        types.InlineKeyboardButton("余额充值", url=RECHARGE_URL),
    )
    keyboard.row(
        types.InlineKeyboardButton("我的账户", url=PERSONAL_URL),
        types.InlineKeyboardButton("服务中心", url=SERVICES_URL),
    )
    keyboard.row(
        types.InlineKeyboardButton("频道", url=CHANNEL_URL),
        types.InlineKeyboardButton("分享好友", url=personal_share_url(user_id)),
        types.InlineKeyboardButton("联系昊天", url=CONTACT_URL),
    )
    return keyboard


def personal_share_url(user_id):
    if user_id is None:
        return SHARE_URL
    from customer_analytics import invite_link
    link = invite_link(user_id)
    return "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote(
        "昊天生活通 · 西港生活服务与东南亚资讯，一站查看", safe="")


def reply_keyboard():
    keyboard = types.ReplyKeyboardMarkup(
        row_width=3, resize_keyboard=True, one_time_keyboard=False, is_persistent=True)
    entries = [button for row in home_keyboard().keyboard for button in row]
    direct = [button for button in entries if button.url in WEBAPP_PATHS]
    replies = [button for button in entries if button.url not in WEBAPP_PATHS]
    for group in (direct, replies):
        for offset in range(0, len(group), 3):
            buttons = []
            for button in group[offset:offset + 3]:
                path = WEBAPP_PATHS.get(button.url)
                web_app = types.WebAppInfo(WEBAPP_BASE_URL + path) if path else None
                buttons.append(types.KeyboardButton(button.text, web_app=web_app))
            keyboard.row(*buttons)
    return keyboard


def is_menu_message(message):
    return (not getattr(message, "reply_to_message", None)
            and any(message.text == button.text
                    for row in home_keyboard().keyboard for button in row))


def send_menu_link(bot, chat_id, user_id, text):
    button = next((button for row in home_keyboard().keyboard
                   for button in row if button.text == text), None)
    if button is None or not button.url:
        raise ValueError("无效的功能入口，请发送 /start 刷新菜单。")
    if text == "分享好友":
        button = types.InlineKeyboardButton(text, url=personal_share_url(user_id))
    markup = types.InlineKeyboardMarkup()
    markup.row(button)
    return keep_latest(bot, bot.send_message(
        chat_id, f"点击下方按钮打开{text}。", reply_markup=markup, disable_web_page_preview=True))


def sub_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    keyboard.add(
        types.InlineKeyboardButton("会所出勤", url=VENUE_URL),
        types.InlineKeyboardButton("联系昊天", url=CONTACT_URL),
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
    return keep_latest(bot, bot.send_message(
        chat_id,
        HOME_TEXT,
        parse_mode="HTML",
        reply_markup=reply_keyboard(),
        disable_web_page_preview=True,
    ), is_keyboard=True)


def send_help(bot, chat_id):
    text, keyboard = SCREENS["help"]
    return keep_latest(bot, bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=keyboard(),
        disable_web_page_preview=True,
    ))


def show_screen(bot, call):
    """点击菜单按钮时就地替换消息内容,不刷屏。"""
    name = (call.data or "").split(":", 1)[-1]
    screen = SCREENS.get(name)
    bot.answer_callback_query(call.id)
    if not screen:
        return
    if name == "home":
        send_welcome(bot, call.message.chat.id)
        return
    _, keyboard = screen
    markup = keyboard()
    text = screen_text(name)
    try:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception:
        # 内容没变化或消息过旧时,忽略即可
        pass
