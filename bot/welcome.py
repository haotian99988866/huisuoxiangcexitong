from urllib.parse import quote

from telebot import types

MINIAPP_URL = "https://t.me/haotianzhaopian_bot?startapp"
CONTACT_URL = "https://t.me/haotian99988866"
CHANNEL_URL = "https://t.me/haotianyl99988866"
BOT_URL = "https://t.me/haotianzhaopian_bot"
SHARE_URL = (
    "https://t.me/share/url?url="
    + quote(BOT_URL, safe="")
    + "&text="
    + quote("名玺·水汇半价活动｜实时查看西港会所/外送出勤、照片、价格", safe="")
)

WELCOME_TEXT = (
    "💎 <b>名玺·水汇 半价活动</b> 💎\n\n"
    "⏱ 服务时间：45分钟，不限次\n"
    "📅 活动时间：10月全天\n\n"
    "🎫 <b>门票 $39</b>\n"
    "含泡澡、汗蒸、自助餐、休息、台球、电玩，24小时畅享\n"
    "（超时每12小时加收1张门票）\n\n"
    "🎁 消费项目满 <b>$58</b>，免 $39 门票\n\n"
    "👇 点击下方按钮，实时查看今日出勤、技师照片和价格"
)


def build_welcome_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("🌴 查看全西港会所/外送", url=MINIAPP_URL),
        types.InlineKeyboardButton("📢 西港会所外送频道", url=CHANNEL_URL),
        types.InlineKeyboardButton("💬 联系昊天预约", url=CONTACT_URL),
        types.InlineKeyboardButton("🔗 分享给朋友", url=SHARE_URL),
    )
    return keyboard


def send_welcome(bot, chat_id):
    bot.send_message(
        chat_id,
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=build_welcome_keyboard(),
        disable_web_page_preview=True,
    )
