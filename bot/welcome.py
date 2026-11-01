from urllib.parse import quote
import logging
import pymysql

from telebot import types

from admin import get_setting
MINIAPP_URL = "https://t.me/haotianzhaopian_bot?startapp"
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
    "🌴 <b>欢迎使用 昊天生活助手</b>\n"
    "昊天生活通 · 西港生活服务与东南亚资讯\n\n"
    "🌿 会所服务：当班出勤、技师资料、价格与服务项目\n"
    "🛵 西港外送：查看晚班实时信息\n"
    "🎬 影视观影：按分类找剧、找综艺\n"
    "🎵 在线音乐：语言分类、搜索、收藏与歌单\n"
    "⭐ Telegram会员充值：3 / 6 / 12个月，支持赠送，人工开通\n"
    "📱 柬埔寨话费充值：填写手机号，钱包余额支付，人工代充\n"
    "📰 东南亚大事件：浏览资讯，主动订阅新消息\n"
    "🔔 出勤提醒：自主订阅，随时管理\n\n"
    "水墨界面每日换景，节气与节日自动点缀。\n"
    "👇 选择下方入口，或点左下角【昊天生活通】"
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
    "小程序顶部点【外送】，只展示晚班出勤；白班请直接联系昊天。\n\n"
    "<b>5️⃣ 出勤提醒</b>\n"
    "发送 /remind 管理已有出勤订阅；主菜单不再展示出勤提醒按钮。\n\n"
    "<b>6️⃣ 预约 / 咨询</b>\n"
    "点【💬 联系昊天】，或直接在这里发文字、图片、语音留言，我会回复你。\n\n"
    "<b>7️⃣ 东南亚大事件订阅</b>\n"
    "点【📰 东南亚大事件订阅】或发送 /news，确认后接收新资讯提醒；发送 /news_off 随时取消。\n\n"
    "<b>8️⃣ 影视观影</b>\n"
    "点【🎬 观影】进入影视模块，按分类或搜索查找内容，查看剧集后选择播放入口。\n\n"
    "<b>9️⃣ 在线音乐</b>\n"
    "点【🎵 音乐】进入播放器，可按语言分类、搜索歌曲，支持播放队列、收藏和歌单；收藏与歌单需在 Telegram 中登录使用。\n\n"
    "<b>🔟 水墨与今日时令</b>\n"
    "七套水墨构图按北京时间周一至周日轮换，叠加节气与节日配色。小程序点【更多内容】→【今日时令】查看农历和当前节气。\n\n"
    "💳 底部【首页】查看内容，【服务】选择会员套餐，【我的】管理余额、充值记录和购买订单。"
    "小程序自动识别账户，订单进度在【我的】查看。\n\n"
    "<b>⭐ Telegram会员充值</b>\n"
    "点主菜单【⭐ Telegram会员充值】，选择3 / 6 / 12个月套餐，可为自己或他人赠送；"
    "核对收件人及金额后使用钱包余额购买，由管理员人工开通，不是即时自动到账。\n\n"
    "<b>📱 柬埔寨话费充值</b>\n"
    "点主菜单【📱 柬埔寨话费充值】，填写手机号和充值金额，核对手续费与总额后使用钱包余额支付，由管理员人工代充。"
    "付款结果未确认时先查看订单，不要重复提交。\n"
    "钱包余额不足时从【我的】进入充值，待管理员核实到账。普通浏览器可发送 /member 获取网站登录链接。\n"
    "付款或赠送问题可发送 /paysupport 联系管理员。\n\n"
    "💡 小提示：名单会随频道实时更新，下单前建议再刷新一次。"
)


def home_keyboard(user_id=None):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        types.InlineKeyboardButton("🌴 实时出勤", url=MINIAPP_URL),
        types.InlineKeyboardButton("🎬 观影", url=MOVIES_URL),
    )
    keyboard.row(
        types.InlineKeyboardButton("🎵 音乐", url=MUSIC_URL),
        types.InlineKeyboardButton("👤 我的账户", url=PERSONAL_URL),
    )
    keyboard.row(
        types.InlineKeyboardButton("⭐ Telegram会员充值", url=MEMBERSHIP_URL),
        types.InlineKeyboardButton("📱 柬埔寨话费充值", url=PHONE_URL),
    )
    keyboard.row(
        types.InlineKeyboardButton("📢 频道", url=CHANNEL_URL),
        types.InlineKeyboardButton("🔗 分享给朋友", url=personal_share_url(user_id)),
    )
    keyboard.row(types.InlineKeyboardButton("📰 东南亚大事件订阅", callback_data="news:menu"))
    keyboard.row(
        types.InlineKeyboardButton("📖 使用指南", callback_data="menu:help"),
        types.InlineKeyboardButton("💬 联系昊天", url=CONTACT_URL),
    )
    return keyboard


def personal_share_url(user_id):
    if user_id is None:
        return SHARE_URL
    from customer_analytics import invite_link
    link = invite_link(user_id)
    return "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote(
        "昊天生活通 · 西港生活服务与东南亚资讯，一站查看", safe="")


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
    try:
        keyboard = home_keyboard(chat_id)
    except (ValueError, pymysql.MySQLError):
        logging.getLogger(__name__).exception("Could not create personal invitation menu")
        bot.send_message(chat_id, "专属邀请入口暂不可用，本次菜单分享无法记录归属，请稍后重试 /start。")
        keyboard = home_keyboard()
    bot.send_message(
        chat_id,
        HOME_TEXT,
        parse_mode="HTML",
        reply_markup=keyboard,
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
    if name == "home":
        try:
            markup = home_keyboard(call.from_user.id)
        except (ValueError, pymysql.MySQLError):
            logging.getLogger(__name__).exception("Could not refresh personal invitation menu")
            bot.send_message(call.message.chat.id, "专属邀请入口暂不可用，请稍后重试 /start。")
            return
    else:
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
