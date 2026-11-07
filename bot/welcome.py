from urllib.parse import quote
import logging

from telebot import apihelper, types

from admin import get_setting
from menu_messages import send_tracked

logger = logging.getLogger(__name__)
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
SHARE_URL = (
    "https://t.me/share/url?url="
    + quote(BOT_URL, safe="")
    + "&text="
    + quote("昊天生活通 · 西港生活服务与东南亚资讯，一站查看", safe="")
)

HOME_TEXT = (
    "<b>欢迎使用昊天生活通</b>\n"
    "西港生活服务，一站直达。\n\n"
    "会所外送 · 出勤提醒 · 观影音乐\n"
    "资讯订阅 · Telegram会员 · 话费充值\n"
    "充值服务 · 我的账户与客服\n\n"
    "充值订单人工处理，非即时到账。\n"
    "点击下方分类展开功能；账户操作请从 Telegram 打开小程序。"
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
    "点击首页分类展开功能并直接打开网页；账户、下单、收藏和人员订阅如需登录，请从 Telegram 打开小程序。\n\n"
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


def legacy_home_keyboard(user_id=None):
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


MENU_CATEGORIES = {
    "venue": (
        "会所 / 外送",
        (
            ("会所出勤", VENUE_URL),
            ("西港外送", DELIVERY_URL),
            ("酒店推荐", HOTELS_URL),
        ),
    ),
    "media": (
        "影音娱乐",
        (
            ("影视观影", MOVIES_URL),
            ("短剧合集", SHORT_DRAMAS_URL),
            ("在线音乐", MUSIC_URL),
        ),
    ),
    "news": (
        "资讯与提醒",
        (
            ("昊天大事件", NEWS_URL),
            ("新闻订阅", "callback:news:menu"),
            ("出勤提醒", "callback:sub:menu"),
        ),
    ),
    "services": (
        "充值服务",
        (
            ("Telegram会员", MEMBERSHIP_URL),
            ("话费充值", PHONE_URL),
            ("余额充值", RECHARGE_URL),
            ("服务中心", SERVICES_URL),
        ),
    ),
    "account": (
        "我的与客服",
        (
            ("我的账户", PERSONAL_URL),
            ("频道", CHANNEL_URL),
            ("分享好友", "share"),
            ("联系昊天", CONTACT_URL),
        ),
    ),
}


def home_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(title, callback_data=f"menu:category:{key}")
        for key, (title, _) in MENU_CATEGORIES.items()
    ]
    for offset in range(0, len(buttons), 2):
        keyboard.row(*buttons[offset:offset + 2])
    return keyboard


def category_keyboard(category, user_id=None):
    title, entries = MENU_CATEGORIES[category]
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for label, destination in entries:
        if destination.startswith("callback:"):
            buttons.append(types.InlineKeyboardButton(
                label, callback_data=destination.removeprefix("callback:")))
        else:
            url = personal_share_url(user_id) if destination == "share" else destination
            buttons.append(types.InlineKeyboardButton(label, url=url))
    for offset in range(0, len(buttons), 2):
        keyboard.row(*buttons[offset:offset + 2])
    keyboard.row(types.InlineKeyboardButton("‹ 返回分类", callback_data="menu:home"))
    return title, keyboard


def personal_share_url(user_id):
    if user_id is None:
        return SHARE_URL
    from customer_analytics import invite_link
    link = invite_link(user_id)
    return "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote(
        "昊天生活通 · 西港生活服务与东南亚资讯，一站查看", safe="")


def is_menu_message(message):
    return (not getattr(message, "reply_to_message", None)
            and any(message.text == button.text
                    for row in legacy_home_keyboard().keyboard for button in row))


def send_menu_link(bot, chat_id, user_id, text):
    button = next((button for row in legacy_home_keyboard().keyboard
                   for button in row if button.text == text), None)
    if button is None or not button.url:
        raise ValueError("无效的功能入口，请发送 /start 刷新菜单。")
    if text == "分享好友":
        button = types.InlineKeyboardButton(text, url=personal_share_url(user_id))
    markup = types.InlineKeyboardMarkup()
    markup.row(button)
    return send_tracked(
        bot, chat_id, f"点击下方按钮打开{text}。", reply_markup=markup,
        disable_web_page_preview=True)


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
    return send_tracked(
        bot,
        chat_id,
        HOME_TEXT,
        parse_mode="HTML",
        reply_markup=home_keyboard(),
        disable_web_page_preview=True,
    )

def send_help(bot, chat_id):
    text, keyboard = SCREENS["help"]
    return send_tracked(bot,
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=keyboard(),
        disable_web_page_preview=True,
    )


def show_screen(bot, call):
    """点击菜单按钮时就地替换消息内容,不刷屏。"""
    data = call.data or ""
    bot.answer_callback_query(call.id)
    if data == "menu:home":
        text, keyboard = HOME_TEXT, home_keyboard()
    elif data.startswith("menu:category:"):
        category = data.removeprefix("menu:category:")
        if category not in MENU_CATEGORIES:
            return
        title, keyboard = category_keyboard(category, call.from_user.id)
        text = f"<b>{title}</b>\n选择要打开的功能。"
    else:
        return
    try:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except apihelper.ApiTelegramException as error:
        if error.error_code == 400 and "message is not modified" in error.description.lower():
            return
        logger.exception("Could not update grouped bot menu")
        raise
