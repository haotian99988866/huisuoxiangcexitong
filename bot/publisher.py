"""代发:管理员把视频私聊发给机器人,机器人整理说明文字后发到观影频道。"""
import queue
import re
import threading
import time
from datetime import datetime

from telebot import types

from movies import MOVIE_CHANNEL_ID, TAG_PATTERN, save_movie
from welcome import BOT_URL, CONTACT_URL

PUBLISH_INTERVAL = 3.5  # 频道每分钟只允许发约 20 条
CAPTION_LIMIT = 1024
FOOTER_TEXT = "👇 推荐机器人 · 联系昊天"

_queue = queue.Queue()
_worker_started = False
_lock = threading.Lock()


def publisher_ids():
    """被授权代发视频的账号(管理员授权的名单,存在 bot_settings)。"""
    from admin import get_setting

    return {
        int(value)
        for value in (get_setting("publishers", "") or "").split(",")
        if value.strip().isdigit()
    }


def set_publisher(user_id, allowed):
    from admin import set_setting

    ids = publisher_ids()
    ids.add(user_id) if allowed else ids.discard(user_id)
    set_setting("publishers", ",".join(str(i) for i in sorted(ids)))
    return ids


def handle_publisher_command(bot, message):
    """管理员命令:/publisher 123 授权, /publisher del 123 取消, /publisher 查看名单。"""
    parts = (message.text or "").split()[1:]
    if not parts:
        ids = publisher_ids()
        text = "已授权代发的账号:\n" + "\n".join(map(str, sorted(ids))) if ids else "还没有授权任何代发账号。"
        text += "\n\n授权:/publisher 用户ID\n取消:/publisher del 用户ID\n用户ID 让对方给机器人发 /myid 获取。"
        bot.reply_to(message, text)
        return
    remove = parts[0].lower() in ("del", "remove", "删除")
    value = parts[-1]
    if not value.isdigit():
        bot.reply_to(message, "用户ID 必须是数字,让对方给机器人发 /myid 获取。")
        return
    set_publisher(int(value), not remove)
    bot.reply_to(message, f"{'已取消' if remove else '已授权'}账号 {value} 代发视频。")


def build_caption(raw):
    """只保留剧名集数和 # 标签,再加上固定文案。没有剧名返回 None。"""
    raw = (raw or "").strip()
    first_line = raw.split("\n", 1)[0]
    title = TAG_PATTERN.sub("", first_line).strip()
    if not title:
        return None
    tags = []
    for tag in TAG_PATTERN.findall(raw):
        if tag not in tags:
            tags.append(tag)
    lines = [title]
    if tags:
        lines.append(" ".join(f"#{tag}" for tag in tags))
    lines += ["", FOOTER_TEXT]
    return "\n".join(lines)[:CAPTION_LIMIT]


def channel_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton("🤖 进入机器人", url=BOT_URL),
        types.InlineKeyboardButton("💬 联系昊天", url=CONTACT_URL),
    )
    return keyboard


def video_media(message):
    video = getattr(message, "video", None)
    if video:
        return video
    document = getattr(message, "document", None)
    if document and (document.mime_type or "").startswith("video/"):
        return document
    return None


def _react(bot, chat_id, message_id, emoji):
    try:
        bot.set_message_reaction(chat_id, message_id, [types.ReactionTypeEmoji(emoji)])
    except Exception:
        pass


def _publish(bot, job):
    chat_id, message_id, caption, media = job
    for _ in range(20):
        try:
            sent = bot.copy_message(
                MOVIE_CHANNEL_ID,
                chat_id,
                message_id,
                caption=caption,
                reply_markup=channel_keyboard(),
            )
            thumb = getattr(media, "thumbnail", None) or getattr(media, "thumb", None)
            save_movie(
                MOVIE_CHANNEL_ID,
                sent.message_id,
                caption,
                duration=getattr(media, "duration", None),
                thumb_file_id=getattr(thumb, "file_id", None),
                posted_at=datetime.now(),
            )
            _react(bot, chat_id, message_id, "👍")
            return
        except Exception as exc:
            wait = re.search(r"retry after (\d+)", str(exc))
            if wait:
                time.sleep(int(wait.group(1)) + 1)
                continue
            print(f"代发失败 {message_id}: {exc}")
            try:
                bot.send_message(
                    chat_id, f"❌ 发送失败:{str(exc)[:120]}", reply_to_message_id=message_id
                )
            except Exception:
                pass
            return


def _worker(bot):
    while True:
        job = _queue.get()
        _publish(bot, job)
        time.sleep(PUBLISH_INTERVAL)


def handle_video(bot, message):
    """管理员私聊发来视频:整理说明后排队发到频道。"""
    media = video_media(message)
    if not media:
        return False
    caption = build_caption(message.caption)
    if not caption:
        bot.reply_to(message, "请在视频说明里写好剧名和集数,例如:《剧名》第一集01 #电视剧")
        return True
    global _worker_started
    with _lock:
        if not _worker_started:
            threading.Thread(target=_worker, args=(bot,), daemon=True).start()
            _worker_started = True
    _queue.put((message.chat.id, message.message_id, caption, media))
    _react(bot, message.chat.id, message.message_id, "⏳")
    return True
