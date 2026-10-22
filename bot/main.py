import os
import re
import telebot
from dotenv import load_dotenv
from db import get_conn
from config.channels import LISTEN_CHANNELS, TAG_ALIASES

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

# 同时监听普通消息 和 频道发布消息
@bot.message_handler(content_types=['photo'])
@bot.channel_post_handler(content_types=['photo'])
def handle_photo_message(message):
    print(f"\n=== 收到消息 ===")
    print(f"来源类型: {'频道' if message.content_type == 'channel_post' else '普通消息'}")
    print(f"原始频道ID: {message.chat.id}")
    print(f"消息文字: {message.caption}")
    
    # 兼容不同格式的ID匹配
    chat_id = message.chat.id
    chat_id_str = str(chat_id)
    # 有些情况会带-100前缀，确保两种形式都能匹配
    if chat_id_str.startswith('-100'):
        chat_id_str_short = chat_id_str
    else:
        chat_id_str_short = f"-100{chat_id_str.replace('-', '')}"
    
    print(f"匹配ID: {chat_id_str} / {chat_id_str_short}")
    print(f"监听列表: {list(LISTEN_CHANNELS.keys())}")

    # 尝试两种ID格式匹配
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
    chat_numeric = str(message.chat.id).replace('-100', '').replace('-', '')
    photo_link = f"https://t.me/c/{chat_numeric}/{message.message_id}"

    try:
        conn = get_conn()
        cur = conn.cursor()
        
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

        cur.execute("""
            UPDATE venues SET cover_image = %s WHERE id = %s
        """, (photo_link, venue_config["venue_id"]))

        conn.commit()
        cur.close()
        conn.close()

        print(f"✅ 更新成功: {venue_config['venue_name']} -> {photo_link}")
        
        # 频道回复方式略有不同
        bot.send_message(
            message.chat.id,
            f"✅ 已更新【{venue_config['venue_name']}】\n"
            f"标签：{' '.join(['#'+t for t in tags])}\n"
            f"链接：{photo_link}"
        )
    except Exception as e:
        print(f"❌ 数据库错误: {e}")

if __name__ == "__main__":
    print("🤖 机器人启动 — 监听会所频道模式")
    print(f"📋 已监听 {len(LISTEN_CHANNELS)} 个频道")
    bot.infinity_polling()