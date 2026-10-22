import os
import re
import telebot
from dotenv import load_dotenv
from db import get_conn
from config.channels import LISTEN_CHANNELS, TAG_ALIASES
from attendance_parser import save_daily_attendance  # 出勤解析模块

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

def normalize_tag(tag):
    t = tag.strip()
    return TAG_ALIASES.get(t, t)

def parse_caption(caption):
    if not caption:
        return [], ""
    hashtag_pattern = r'#(\S+)'
    tags = re.findall(hashtag_pattern, caption)
    tags = [normalize_tag(t) for t in tags]
    text_without_tags = re.sub(hashtag_pattern, '', caption).strip()
    return tags, text_without_tags

# ===== 处理照片消息（更新封面） =====
@bot.message_handler(content_types=['photo'])
@bot.channel_post_handler(content_types=['photo'])
def handle_photo_message(message):
    print(f"\n=== 收到照片消息 ===")
    print(f"来源类型: {'频道' if message.content_type == 'channel_post' else '普通消息'}")
    print(f"原始频道ID: {message.chat.id}")
    print(f"消息文字: {message.caption}")

    chat_id = message.chat.id
    chat_id_str = str(chat_id)
    chat_id_str_short = chat_id_str if chat_id_str.startswith('-100') else f"-100{chat_id_str.replace('-', '')}"

    # 匹配频道
    if chat_id_str in LISTEN_CHANNELS:
        venue_config = LISTEN_CHANNELS[chat_id_str]
    elif chat_id_str_short in LISTEN_CHANNELS:
        venue_config = LISTEN_CHANNELS[chat_id_str_short]
    else:
        print(f"❌ 频道ID不在监听列表中！")
        return

    primary_tag = venue_config["primary_tag"]
    caption = message.caption or ""
    tags, _ = parse_caption(caption)

    print(f"解析标签: {tags}")
    print(f"需要匹配: [{primary_tag}]")

    if primary_tag not in tags:
        print(f"❌ 主标签不匹配，跳过")
        return

    # 生成照片链接
    file_id = message.photo[-1].file_id
    chat_numeric = chat_id_str.replace('-100', '').replace('-', '')
    photo_link = f"https://t.me/c/{chat_numeric}/{message.message_id}"

    try:
        conn = get_conn()
        cur = conn.cursor()

        # 保存照片记录
        cur.execute("""
            INSERT INTO venue_photos (venue_id, file_id, photo_link, caption, tags, channel_id, msg_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            venue_config["venue_id"],
            file_id,
            photo_link,
            caption,
            ",".join(tags),
            chat_id_str,
            message.message_id
        ))

        # 更新封面
        cur.execute("""
            UPDATE venues SET cover_image = %s WHERE id = %s
        """, (photo_link, venue_config["venue_id"]))

        conn.commit()
        cur.close()
        conn.close()

        print(f"✅ 封面更新成功: {venue_config['venue_name']} -> {photo_link}")

        bot.send_message(
            message.chat.id,
            f"✅ 已更新【{venue_config['venue_name']}】\n"
            f"标签：{' '.join(['#'+t for t in tags])}\n"
            f"链接：{photo_link}"
        )
    except Exception as e:
        print(f"❌ 数据库错误: {e}")

# ===== 处理纯文字出勤公告（新增） =====
@bot.message_handler(content_types=['text'])
@bot.channel_post_handler(content_types=['text'])
def handle_attendance_text(message):
    text = message.text or message.caption or ""

    # 只处理包含出勤关键词的消息
    if not any(keyword in text for keyword in ['出勤', '按摩部', '技师']):
        return

    print(f"\n=== 收到出勤公告 ===")
    print(f"内容: {text[:100]}...")

    chat_id = message.chat.id
    chat_id_str = str(chat_id)
    chat_id_str_short = chat_id_str if chat_id_str.startswith('-100') else f"-100{chat_id_str.replace('-', '')}"

    # 匹配会所
    venue_config = None
    if chat_id_str in LISTEN_CHANNELS:
        venue_config = LISTEN_CHANNELS[chat_id_str]
    elif chat_id_str_short in LISTEN_CHANNELS:
        venue_config = LISTEN_CHANNELS[chat_id_str_short]

    if not venue_config:
        print(f"❌ 未匹配到会所")
        return

    # 解析并保存出勤
    ok, msg = save_daily_attendance(venue_config["venue_id"], text)
    print(f"出勤同步结果: {msg}")

    # 回复确认
    bot.send_message(message.chat.id, msg)

if __name__ == "__main__":
    print("🤖 机器人启动 — 监听会所频道模式")
    print(f"📋 已监听 {len(LISTEN_CHANNELS)} 个频道")
    bot.infinity_polling()