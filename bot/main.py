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
from image_hash import compute_hash, ensure_columns as ensure_image_hash_columns
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
from movies import ensure_tables as ensure_movie_tables, save_movie_message
from music import ensure_tables as ensure_music_tables, handle_channel_message as handle_music_message
from publisher import (
    handle_publisher_command,
    handle_video as publish_video,
    publisher_ids,
    video_media,
)
from footer import ensure_tables as ensure_footer_tables, post_footer, schedule_footer
from admin import (
    BROADCAST_CONTENT_TYPES,
    PANEL_BUTTON,
    ensure_tables as ensure_admin_tables,
    handle_admin_callback,
    handle_pending_message,
    has_pending,
    send_panel,
)
from subscribe import (
    ensure_tables as ensure_sub_tables,
    notify_attendance,
    show_sub_menu,
    sub_menu_keyboard,
    sub_menu_text,
)
from welcome import send_help, send_welcome, show_screen
from visit_stats import admin_ids, ensure_table, format_stats
from relay import (
    RELAY_CONTENT_TYPES,
    ensure_tables,
    is_rate_limited,
    relay_admin_reply,
    relay_to_admins,
    should_ack,
    touch_user,
    user_summary,
)
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

# ===== 客人私聊机器人时发送欢迎信息 =====
@bot.message_handler(commands=['start'], chat_types=['private'])
def handle_start(message):
    touch_user(message.from_user)
    send_welcome(bot, message.chat.id)


@bot.message_handler(commands=['help'], chat_types=['private'])
def handle_help(message):
    touch_user(message.from_user)
    send_help(bot, message.chat.id)


@bot.message_handler(commands=['wallet'], chat_types=['private'])
def handle_wallet(message):
    from wallet import account, pending, review, set_address
    from telebot import types
    if message.from_user.id not in admin_ids():
        bot.reply_to(message, "请通过主菜单的充值中心查看账户余额。")
        return
    parts = (message.text or "").split()
    try:
        if len(parts) == 3 and parts[1] == "address":
            set_address(parts[2])
            bot.reply_to(message, "收款地址已配置，TRC20 余额充值申请已开放。请确认该地址由你控制。")
        elif len(parts) == 4 and parts[1] == "approve":
            from wallet import amount_value
            amount = amount_value(parts[3])
            deposit_id = int(parts[2])
            if deposit_id <= 0:
                raise ValueError("申请编号必须为正整数")
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(types.InlineKeyboardButton(
                "已核实链上到账，确认入账",
                callback_data=f"wallet:approve:{deposit_id}:{amount:f}",
            ))
            bot.reply_to(
                message,
                f"申请 #{deposit_id} 将入账 {amount:f} U。\n"
                "请在链上独立核对：TRON 主网、官方 USDT 合约、收款地址、"
                "实际到账金额、交易成功及确认情况，不能仅凭截图。\n确认后余额不可通过此命令撤销。",
                reply_markup=keyboard,
            )
        elif len(parts) == 3 and parts[1] == "reject":
            review(int(parts[2]), message.from_user.id)
            bot.reply_to(message, "申请已拒绝，余额未变动。")
        elif len(parts) == 1:
            rows = pending()
            address = account(message.from_user.id)["address"] or "未配置，充值关闭"
            text = (
                f"TRC20 收款地址：{address}\n\n"
                "配置：/wallet address 收款地址\n"
                "核款：/wallet approve 申请编号 实际到账金额\n"
                "拒绝：/wallet reject 申请编号\n\n待审核（最多8条，处理后查看下一批）：\n"
            )
            for row in rows:
                text += (
                    f"\n#{row['id']} 用户 {row['user_id']} 申报 {row['claimed']} U\n"
                    f"地址：{row['address']}\nTXID：{row['txid']}\n"
                )
            bot.send_message(message.chat.id, text, disable_web_page_preview=True)
        else:
            raise ValueError("格式错误，请发送 /wallet 查看用法")
    except ValueError as exc:
        bot.reply_to(message, str(exc))
    except pymysql.MySQLError:
        logger.exception("充值后台操作失败")
        bot.reply_to(message, "数据库操作失败，请检查申请记录后再操作，勿重复核款。")


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("wallet:"))
def handle_wallet_confirmation(call):
    from wallet import review
    if call.from_user.id not in admin_ids() or call.message.chat.type != "private":
        bot.answer_callback_query(call.id, "仅管理员可操作", show_alert=True)
        return
    try:
        _, action, deposit_id, amount = call.data.split(":")
        if action != "approve":
            raise ValueError("无效操作")
        review(int(deposit_id), call.from_user.id, amount)
    except ValueError as exc:
        bot.answer_callback_query(call.id, str(exc), show_alert=True)
        return
    except pymysql.MySQLError:
        logger.exception("充值确认失败")
        bot.answer_callback_query(call.id, "数据库操作失败，请先检查记录", show_alert=True)
        return
    bot.answer_callback_query(call.id, "入账成功")
    bot.send_message(call.message.chat.id, f"申请 #{deposit_id} 已入账 {amount} U。")


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("menu:"))
def handle_menu_callback(call):
    touch_user(call.from_user)
    show_screen(bot, call)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("sub:"))
def handle_sub_callback(call):
    touch_user(call.from_user)
    show_sub_menu(bot, call)


@bot.message_handler(commands=['remind'], chat_types=['private'])
def handle_remind(message):
    touch_user(message.from_user)
    bot.send_message(
        message.chat.id,
        sub_menu_text(),
        parse_mode="HTML",
        reply_markup=sub_menu_keyboard(message.from_user.id),
    )


# ===== 管理后台(仅管理员) =====
def _is_admin_pending(message):
    return message.from_user.id in admin_ids() and has_pending(message.from_user.id) and not (
        message.text and message.text.startswith("/")
    )


@bot.message_handler(commands=['admin'], chat_types=['private'])
def handle_admin(message):
    if message.from_user.id in admin_ids():
        send_panel(bot, message.chat.id, message.from_user.id, message.message_id)


@bot.message_handler(
    func=lambda m: m.text == PANEL_BUTTON and m.from_user.id in admin_ids(),
    chat_types=['private'],
)
def handle_admin_button(message):
    send_panel(bot, message.chat.id, message.from_user.id, message.message_id)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("adm:"))
def handle_admin_cb(call):
    if call.from_user.id in admin_ids():
        handle_admin_callback(bot, call)


@bot.message_handler(
    func=_is_admin_pending, chat_types=['private'], content_types=BROADCAST_CONTENT_TYPES
)
def handle_admin_pending(message):
    handle_pending_message(bot, message)


# ===== 代发:管理员私聊发视频,整理说明后发到观影频道 =====
def _is_admin_video(message):
    return (
        (message.from_user.id in admin_ids() or message.from_user.id in publisher_ids())
        and message.reply_to_message is None
        and not has_pending(message.from_user.id)
        and video_media(message) is not None
    )


@bot.message_handler(commands=['publisher'], chat_types=['private'])
def handle_publisher(message):
    if message.from_user.id in admin_ids():
        handle_publisher_command(bot, message)


@bot.message_handler(
    func=_is_admin_video, chat_types=['private'], content_types=['video', 'document']
)
def handle_admin_video(message):
    publish_video(bot, message)


# ===== 客人留言转发给管理员,管理员回复后发回客人 =====
@bot.message_handler(commands=['users'], chat_types=['private'])
def handle_users(message):
    if message.from_user.id in admin_ids():
        bot.send_message(message.chat.id, user_summary())


def _is_admin_reply(message):
    return message.from_user.id in admin_ids() and message.reply_to_message is not None


@bot.message_handler(
    func=_is_admin_reply, chat_types=['private'], content_types=RELAY_CONTENT_TYPES
)
def handle_admin_reply(message):
    relay_admin_reply(bot, message)


def _is_customer_message(message):
    if message.text and message.text.startswith("/"):
        return False
    return message.from_user.id not in admin_ids()


@bot.message_handler(
    func=_is_customer_message, chat_types=['private'], content_types=RELAY_CONTENT_TYPES
)
def handle_customer_message(message):
    if touch_user(message.from_user, count_message=True):
        return
    if is_rate_limited(message.from_user.id):
        return
    if relay_to_admins(bot, message):
        if should_ack(message.from_user.id):
            bot.send_message(
                message.chat.id,
                "✅ 已收到您的留言，昊天看到后会尽快回复您。\n"
                "也可以点 /start 查看实时出勤和活动。",
            )
    else:
        bot.send_message(
            message.chat.id,
            "暂时无法转达，请直接联系 @haotian99988866。",
        )


# ===== 访问统计:仅管理员可查看 =====
@bot.message_handler(commands=['myid'], chat_types=['private'])
def handle_myid(message):
    bot.send_message(message.chat.id, f"你的 Telegram ID：{message.from_user.id}")


@bot.message_handler(commands=['stats'], chat_types=['private'])
def handle_stats(message):
    admins = admin_ids()
    if message.from_user.id not in admins:
        if not admins:
            bot.send_message(
                message.chat.id,
                f"尚未设置管理员。你的 ID 是 {message.from_user.id}，"
                "请把它填到 bot/.env 的 ADMIN_USER_IDS 后重启机器人。",
            )
        return
    bot.send_message(message.chat.id, format_stats())


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
    if get_venue_config(message.chat.id):
        schedule_footer(bot, message.chat.id)
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
        if message.text and get_venue_config(message.chat.id):
            schedule_footer(bot, message.chat.id)
        return
    text = message.text or message.caption or ""

    # 只处理包含出勤关键词的消息
    if not any(keyword in text for keyword in ['出勤', '按摩部', '技师', '白班']):
        if get_venue_config(message.chat.id):
            schedule_footer(bot, message.chat.id)
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
        if notify_attendance(
            bot,
            venue_config["venue_id"],
            attendance["duty_date"],
            attendance["duty_shift"],
            len(attendance["codes"]),
        ):
            print("🔔 已向订阅用户推送出勤提醒")

    # 回复确认
    confirmation = bot.send_message(message.chat.id, msg)
    record_channel_activity(confirmation)
    if ok:
        post_footer(bot, message.chat.id)


@bot.channel_post_handler(
    content_types=list(
        dict.fromkeys(util.content_type_media + util.content_type_service)
    )
)
def handle_other_channel_post(message):
    if handle_music_message(bot, message, admin_ids()):
        return
    record_channel_activity(message)
    if save_movie_message(message):
        print("🎬 观影视频已入库")
        return
    save_ktv_video(message)
    save_delivery_media(message)
    venue_config = get_venue_config(message.chat.id)
    if venue_config and (message.photo or message.video):
        schedule_footer(bot, message.chat.id)


DELIVERY_VENUE_IDS = {7, 8, 9, 10}


@bot.edited_channel_post_handler(content_types=['video', 'document', 'audio'])
def handle_edited_movie_post(message):
    if handle_music_message(bot, message, admin_ids()):
        return
    if save_movie_message(message):
        print("🎬 观影视频已更新")


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
    ocr_text, age, image_hash = None, extract_age(message.caption), None
    if media_type == "photo":
        try:
            image_bytes = bot.download_file(bot.get_file(file_id).file_path)
            image_hash = compute_hash(image_bytes)
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
                     media_type, file_id, caption, ocr_text, age, image_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    file_id = VALUES(file_id),
                    media_group_id = VALUES(media_group_id),
                    caption = VALUES(caption),
                    ocr_text = VALUES(ocr_text),
                    age = VALUES(age),
                    image_hash = VALUES(image_hash)
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
                    image_hash,
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
    from wallet import ensure_tables as ensure_wallet_tables
    ensure_wallet_tables()
    print("🤖 机器人启动 — 监听会所频道模式")
    print(f"📋 已监听 {len(LISTEN_CHANNELS)} 个频道")
    ensure_table()
    ensure_tables()
    ensure_sub_tables()
    ensure_admin_tables()
    ensure_footer_tables()
    ensure_movie_tables()
    ensure_music_tables()
    ensure_image_hash_columns()
    start_cleanup_thread()
    bot.infinity_polling()