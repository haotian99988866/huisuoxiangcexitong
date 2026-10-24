import os
import re
import logging
from datetime import datetime, timezone

import pymysql
import requests
import telebot
from telebot import util
from dotenv import load_dotenv
from db import get_conn
from config.channels import LISTEN_CHANNELS, TAG_ALIASES
from attendance_parser import (
    PLATE_CODE_VENUE_ID,
    parse_attendance_text,
    parse_plate_attendance_text,
    save_daily_attendance,
)
from technician_photo_ocr import (
    match_pending_technician_photos,
    save_technician_photo,
)
from data_cleanup import start_cleanup_thread
from delivery_age import extract_age, ocr_image_text

load_dotenv()
logger = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

def normalize_tag(tag):
    t = tag.strip()
    return TAG_ALIASES.get(t, t)


def get_venue_config(chat_id):
    chat_id_str = str(chat_id)
    chat_id_str_short = (
        chat_id_str
        if chat_id_str.startswith("-100")
        else f"-100{chat_id_str.replace('-', '')}"
    )
    return LISTEN_CHANNELS.get(chat_id_str) or LISTEN_CHANNELS.get(
        chat_id_str_short
    )


def record_channel_activity(message):
    venue_config = get_venue_config(message.chat.id)
    if not venue_config:
        return

    message_date = datetime.fromtimestamp(message.date, timezone.utc).replace(
        tzinfo=None
    )
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO venue_channel_activity
                (venue_id, channel_id, message_id, message_date)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                message_id = IF(
                    VALUES(message_date) >= message_date,
                    VALUES(message_id),
                    message_id
                ),
                message_date = GREATEST(message_date, VALUES(message_date))
            """,
            (
                venue_config["venue_id"],
                str(message.chat.id),
                message.message_id,
                message_date,
            ),
        )
        conn.commit()
    except pymysql.MySQLError:
        conn.rollback()
        logger.exception(
            "Failed to record channel activity for message %s",
            message.message_id,
        )
        raise
    finally:
        if cur is not None:
            cur.close()
        conn.close()


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
    record_channel_activity(message)
    print(f"\n=== 收到照片消息 ===")
    print(f"来源类型: {'频道' if message.content_type == 'channel_post' else '普通消息'}")
    print(f"原始频道ID: {message.chat.id}")
    print(f"消息文字: {message.caption}")

    chat_id = message.chat.id
    chat_id_str = str(chat_id)
    venue_config = get_venue_config(chat_id)
    if not venue_config:
        print(f"❌ 频道ID不在监听列表中！")
        return

    save_delivery_media(message)
    if venue_config["venue_id"] in DELIVERY_VENUE_IDS:
        return

    primary_tag = venue_config["primary_tag"]
    caption = message.caption or ""
    tags, _ = parse_caption(caption)

    print(f"解析标签: {tags}")
    print(f"需要匹配: [{primary_tag}]")

    file_id = message.photo[-1].file_id
    chat_numeric = chat_id_str.replace('-100', '').replace('-', '')
    photo_link = f"https://t.me/c/{chat_numeric}/{message.message_id}"

    if primary_tag in tags:
        conn = get_conn()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO venue_photos
                    (venue_id, file_id, photo_link, caption, tags, channel_id, msg_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    venue_config["venue_id"],
                    file_id,
                    photo_link,
                    caption,
                    ",".join(tags),
                    chat_id_str,
                    message.message_id,
                ),
            )
            cur.execute(
                "UPDATE venues SET cover_image = %s WHERE id = %s",
                (photo_link, venue_config["venue_id"]),
            )
            conn.commit()
        except pymysql.MySQLError:
            conn.rollback()
            logger.exception(
                "Failed to update venue cover for message %s", message.message_id
            )
            raise
        finally:
            if cur is not None:
                cur.close()
            conn.close()

        try:
            confirmation = bot.send_message(
                message.chat.id,
                f"✅ 已更新【{venue_config['venue_name']}】封面\n链接：{photo_link}",
            )
            record_channel_activity(confirmation)
        except telebot.apihelper.ApiTelegramException:
            logger.error(
                "Failed to confirm venue cover update for message %s",
                message.message_id,
            )

        return

    try:
        telegram_file = bot.get_file(file_id)
        image_bytes = bot.download_file(telegram_file.file_path)
    except (
        telebot.apihelper.ApiTelegramException,
        requests.RequestException,
    ) as exc:
        logger.error(
            "Failed to download technician photo %s (%s)",
            message.message_id,
            type(exc).__name__,
        )
        raise

    try:
        result = save_technician_photo(
            venue_config["venue_id"],
            chat_id_str,
            message.message_id,
            file_id,
            image_bytes,
            datetime.fromtimestamp(message.date).date(),
        )
    except (pymysql.MySQLError, ValueError):
        logger.exception("Failed to process technician photo %s", message.message_id)
        raise


    if result["status"] == "matched":
        print(
            f"✅ OCR 匹配成功：{venue_config['venue_name']} "
            f"#{result['technician_code']}，照片记录 {result['id']}"
        )
    else:
        print(
            f"⚠️ OCR 待人工核对：{venue_config['venue_name']} "
            f"照片记录 {result['id']}，候选工号："
            f"{','.join(result['candidate_codes']) or '无'}"
        )

# ===== 处理纯文字出勤公告（新增） =====
@bot.message_handler(content_types=['text'])
@bot.channel_post_handler(content_types=['text'])
def handle_attendance_text(message):
    record_channel_activity(message)
    save_ktv_text(message)
    if save_delivery_text(message):
        return
    text = message.text or message.caption or ""

    # 只处理包含出勤关键词的消息
    if not any(keyword in text for keyword in ['出勤', '按摩部', '技师']):
        return

    print(f"\n=== 收到出勤公告 ===")
    print(f"内容: {text[:100]}...")

    chat_id = message.chat.id
    venue_config = get_venue_config(chat_id)

    if not venue_config:
        print(f"❌ 未匹配到会所")
        return

    # 解析并保存出勤
    ok, msg = save_daily_attendance(venue_config["venue_id"], text)
    print(f"出勤同步结果: {msg}")
    if ok:
        attendance = (
            parse_plate_attendance_text(text)
            if venue_config["venue_id"] == PLATE_CODE_VENUE_ID
            else parse_attendance_text(text)
        )
        photo_date = datetime.fromtimestamp(message.date).date()
        matched_count = match_pending_technician_photos(
            venue_config["venue_id"],
            photo_date,
            attendance["duty_date"],
            attendance["codes"],
        )
        if matched_count:
            print(f"✅ 出勤名单已补匹配 {matched_count} 张先到照片")

    # 回复确认
    confirmation = bot.send_message(message.chat.id, msg)
    record_channel_activity(confirmation)


@bot.channel_post_handler(
    content_types=list(
        dict.fromkeys(util.content_type_media + util.content_type_service)
    )
)
def handle_other_channel_post(message):
    record_channel_activity(message)
    save_ktv_video(message)
    save_delivery_media(message)


DELIVERY_VENUE_IDS = {7, 8, 9, 10}


def save_delivery_media(message):
    venue_config = get_venue_config(message.chat.id)
    if not venue_config or venue_config["venue_id"] not in DELIVERY_VENUE_IDS:
        return
    if message.photo:
        media_type, file_id = "photo", message.photo[-1].file_id
    elif message.video:
        media_type, file_id = "video", message.video.file_id
    else:
        return
    ocr_text, age = None, extract_age(message.caption)
    if media_type == "photo":
        try:
            image_bytes = bot.download_file(bot.get_file(file_id).file_path)
            ocr_text = ocr_image_text(image_bytes)
            age = extract_age(message.caption, ocr_text)
        except Exception as exc:
            print(f"⚠️ 外送照片识别失败: {type(exc).__name__}")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO delivery_media
                    (venue_id, channel_id, message_id, media_group_id,
                     media_type, file_id, caption, ocr_text, age)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    file_id = VALUES(file_id),
                    media_group_id = VALUES(media_group_id),
                    caption = VALUES(caption),
                    ocr_text = VALUES(ocr_text),
                    age = VALUES(age)
                """,
                (
                    venue_config["venue_id"],
                    str(message.chat.id),
                    message.message_id,
                    getattr(message, "media_group_id", None),
                    media_type,
                    file_id,
                    message.caption or "",
                    ocr_text,
                    age,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def save_delivery_text(message):
    """外送频道的文字消息存成 text 记录，用来给前面的图片/视频判断类别；返回 True 表示已按外送处理。"""
    venue_config = get_venue_config(message.chat.id)
    if not venue_config or venue_config["venue_id"] not in DELIVERY_VENUE_IDS:
        return False
    if not message.text:
        return True
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO delivery_media
                    (venue_id, channel_id, message_id, media_type, file_id, caption)
                VALUES (%s, %s, %s, 'text', '', %s)
                ON DUPLICATE KEY UPDATE caption = VALUES(caption)
                """,
                (
                    venue_config["venue_id"],
                    str(message.chat.id),
                    message.message_id,
                    message.text,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return True


KTV_VENUE_ID = 3
ROOM_PATTERNS = (
    re.compile(r"(?:房间|包房|包厢|房号)\s*[:：#\-]?\s*([A-Za-z]?\d{2,5}[A-Za-z]?)"),
    re.compile(r"^\s*([A-Za-z]?\d{2,5}[A-Za-z]?)\s*$"),
    re.compile(r"(\d{3,4})\s*$"),
)


def extract_room_number(caption):
    for pattern in ROOM_PATTERNS:
        match = pattern.search(caption or "")
        if match:
            return match.group(1).upper()
    return None


def save_ktv_text(message):
    venue_config = get_venue_config(message.chat.id)
    if not venue_config or venue_config["venue_id"] != KTV_VENUE_ID or not message.text:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ktv_channel_texts (venue_id, channel_id, message_id, content)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE content = VALUES(content)
                """,
                (KTV_VENUE_ID, str(message.chat.id), message.message_id, message.text),
            )
        conn.commit()
    finally:
        conn.close()


def save_ktv_video(message):
    venue_config = get_venue_config(message.chat.id)
    if not venue_config or venue_config["venue_id"] != KTV_VENUE_ID or not message.video:
        return
    caption = message.caption or ""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ktv_room_videos
                    (venue_id, channel_id, message_id, file_id, room_number, caption)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    file_id = VALUES(file_id),
                    room_number = VALUES(room_number),
                    caption = VALUES(caption)
                """,
                (
                    KTV_VENUE_ID,
                    str(message.chat.id),
                    message.message_id,
                    message.video.file_id,
                    extract_room_number(caption),
                    caption,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    print(f"KTV视频已记录: 房间 {extract_room_number(caption) or '未识别'}")


if __name__ == "__main__":
    print("🤖 机器人启动 — 监听会所频道模式")
    print(f"📋 已监听 {len(LISTEN_CHANNELS)} 个频道")
    start_cleanup_thread()
    bot.infinity_polling()