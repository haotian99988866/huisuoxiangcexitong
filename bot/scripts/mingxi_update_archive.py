"""Update simple existing Mingxi photo captions without reposting photos."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys

from dotenv import load_dotenv
import requests
import telebot
from telebot import apihelper, types
from telethon import TelegramClient, errors
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from channel_sync import credentials, state_directory
from db import get_conn
from mingxi_channel_album import MINGXI_CHANNEL_ID
from price_catalog import read_catalog
from venue_pricing import PRICES, staff_group_name
from welcome import BOT_URL, CHANNEL_URL, CONTACT_URL

INTERVAL = 5


def caption(code, board_name, catalog=PRICES):
    board = next((item for item in catalog["mingxiBoardPrices"]
                  if item["board"] == board_name), None)
    choices = []
    if board:
        for field, label, minutes in (("half", "半价", 45), ("member", "会员", 90)):
            if board.get(field) is not None:
                choices.append(f"{label} ${board[field]} · {minutes}分钟")
    price = " / ".join(choices) if choices else "待确认"
    return (f"【名玺人员档案】\n工号：{code}\n类别：{board_name or '待确认'}\n"
            f"价格：{price}")


def keyboard(row, username):
    markup = types.InlineKeyboardMarkup()
    url = (f"https://t.me/{username}?startapp=staff_2_{row['staff_id']}"
           if row["exact_photo"] else "https://app.haotianzhushou.com/?module=venue&venue=2&album=1")
    markup.row(types.InlineKeyboardButton("查看人员档案", url=url),
               types.InlineKeyboardButton("西港会所导航", url=CHANNEL_URL))
    markup.row(types.InlineKeyboardButton("联系昊天", url=CONTACT_URL),
               types.InlineKeyboardButton("昊天助手机器人", url=BOT_URL))
    return markup


def original_issue(message):
    if not message or not message.photo:
        return "missing_photo"
    if message.grouped_id:
        return "media_group_no_buttons"
    return None


def candidates(after, limit):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT a.code,a.msg_id,s.staff_id,m.price_tags_json,
                EXISTS(SELECT 1 FROM staff_photos p WHERE p.staff_id=s.staff_id
                       AND p.channel_id=a.channel_id AND p.msg_id=a.msg_id
                       AND p.media_type='photo') AS exact_photo
                FROM mingxi_channel_album a
                JOIN staff_slots s ON s.venue_id=2 AND s.code=a.code
                JOIN staff_members m ON m.id=s.staff_id
                WHERE a.channel_id=%s AND a.msg_id>%s AND m.ended_at IS NULL
                    AND m.deleted_at IS NULL AND m.blocked=0
                ORDER BY a.msg_id LIMIT %s""", (MINGXI_CHANNEL_ID, after, limit))
            return cur.fetchall()
    finally:
        conn.close()


def current_prices():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS present FROM information_schema.tables "
                        "WHERE table_schema=DATABASE() AND table_name='venue_price_catalog'")
            if not cur.fetchone()["present"]:
                return PRICES, None
            return read_catalog(cur)
    finally:
        conn.close()


async def run(bot, reader, after, limit, apply=False, boards=None):
    me = bot.get_me()
    member = bot.get_chat_member(int(MINGXI_CHANNEL_ID), me.id)
    if not me.username or not getattr(member, "can_edit_messages", False):
        raise RuntimeError("机器人缺少名玺频道编辑权限")
    channel = await reader.get_entity("mingxi03")
    if channel.id != int(MINGXI_CHANNEL_ID[4:]):
        raise RuntimeError("名玺频道身份不匹配")
    rows = candidates(after, limit)
    counts = {"checked": 0, "eligible": 0, "updated": 0, "already_updated": 0,
              "review": 0, "last_message_id": after, "review_messages": []}
    if not rows:
        return counts
    await asyncio.sleep(INTERVAL)
    messages = await reader.get_messages(channel, ids=[row["msg_id"] for row in rows])
    if len(messages) != len(rows):
        raise RuntimeError("频道读取结果不完整，请核对后重试")
    for row, message in zip(rows, messages):
        counts["checked"] += 1
        counts["last_message_id"] = row["msg_id"]
        tags = json.loads(row["price_tags_json"] or "[]")
        board_name = staff_group_name(2, row["code"], tags)
        if boards is not None and board_name not in boards:
            continue
        issue = original_issue(message)
        if issue:
            counts["review"] += 1
            counts["review_messages"].append({"message_id": row["msg_id"], "reason": issue})
            continue
        counts["eligible"] += 1
        if not apply:
            continue
        await asyncio.sleep(INTERVAL)
        catalog, _revision = current_prices()
        markup = keyboard(row, me.username)
        text = caption(row["code"], board_name, catalog)
        try:
            result = bot.edit_message_caption(text, int(MINGXI_CHANNEL_ID), row["msg_id"],
                                              reply_markup=markup, timeout=20)
        except apihelper.ApiTelegramException as error:
            if error.error_code == 429:
                wait = (error.result_json.get("parameters") or {}).get("retry_after")
                if type(wait) is int and wait > 0:
                    await asyncio.sleep(wait + 1)
            if error.error_code == 400:
                if "message is not modified" in str(error.description).lower():
                    counts["already_updated"] += 1
                    continue
                counts["review"] += 1
                counts["review_messages"].append({"message_id": row["msg_id"],
                                                   "reason": "telegram_rejected",
                                                   "detail": error.description})
                continue
            raise
        except (requests.RequestException, ConnectionError, TimeoutError, apihelper.ApiException) as error:
            raise RuntimeError("频道编辑结果不明，请人工核对后再继续") from error
        if (getattr(result, "message_id", None) != row["msg_id"]
                or getattr(result, "caption", None) != text
                or not getattr(result, "reply_markup", None)
                or result.reply_markup.to_dict() != markup.to_dict()):
            raise RuntimeError("频道编辑结果无法确认，请人工核对后再继续")
        counts["updated"] += 1
    return counts


async def main_async(args, bot=None, boards=None):
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("缺少主机器人令牌")
    session_path = state_directory() / "user.session"
    with sqlite3.connect(session_path.as_uri() + "?mode=ro", uri=True) as db:
        saved = db.execute("SELECT dc_id,server_address,port,auth_key FROM sessions").fetchone()
    if not saved:
        raise RuntimeError("频道读取会话不存在")
    session = MemorySession()
    session.set_dc(*saved[:3])
    session.auth_key = AuthKey(saved[3])
    reader = TelegramClient(session, *credentials(), receive_updates=False, flood_sleep_threshold=0)
    try:
        await reader.connect()
        if not await reader.is_user_authorized() or (await reader.get_me()).bot:
            raise RuntimeError("频道读取会话未授权")
        bot = bot or telebot.TeleBot(token)
        after = args.after
        failures = []
        while True:
            try:
                result = await run(bot, reader, after, args.limit, args.apply, boards=boards)
            except errors.FloodWaitError as error:
                await asyncio.sleep(error.seconds + 1)
                raise RuntimeError("频道读取限流，已等待指定时间，请核对后重试") from error
            print(json.dumps(result, ensure_ascii=False), flush=True)
            failures.extend(item["message_id"] for item in result["review_messages"])
            if not args.all or result["checked"] < args.limit:
                break
            after = result["last_message_id"]
            await asyncio.sleep(INTERVAL)
        return failures
    finally:
        await reader.disconnect()


def main():
    parser = argparse.ArgumentParser(description="核对并更新名玺频道现有照片说明")
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--limit", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--apply", action="store_true", help="实际编辑；默认只读核对")
    parser.add_argument("--all", action="store_true", help="按小批次继续处理剩余照片")
    args = parser.parse_args()
    if args.after < 0:
        parser.error("--after 必须大于等于零")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
