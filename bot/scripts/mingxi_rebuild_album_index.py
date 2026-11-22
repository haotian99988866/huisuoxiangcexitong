"""运维：转发探测频道现帖，按 caption 重建名玺相册索引。

会向管理员私聊转发探测消息（用完即删），误跑会刷屏。

用法（在 bot 目录）:
  .\\venv\\Scripts\\python.exe scripts\\mingxi_rebuild_album_index.py --start 10390 --end 10488
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv
import telebot

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mingxi_channel_album import (
    MINGXI_CHANNEL_ID,
    ensure_tables,
    extract_code_from_caption,
    upsert_album_message,
)
from visit_stats import admin_ids

logger = logging.getLogger(__name__)


def rebuild_index_from_channel(bot, *, start_msg, end_msg, dig_chat_id=None):
    ensure_tables()
    target = dig_chat_id or next(iter(admin_ids() or []), None)
    if not target:
        raise RuntimeError("无可用私聊用于探测频道消息")
    channel_id = MINGXI_CHANNEL_ID
    indexed = 0
    for msg_id in range(int(start_msg), int(end_msg) + 1):
        try:
            sent = bot.forward_message(
                target, channel_id, msg_id, disable_notification=True
            )
        except Exception:
            continue
        try:
            if not (sent.photo or sent.video):
                continue
            code = extract_code_from_caption(sent.caption or "")
            if not code:
                continue
            if upsert_album_message(code, msg_id, channel_id):
                indexed += 1
                logger.info("Mingxi album index %s -> %s", code, msg_id)
        finally:
            try:
                bot.delete_message(target, sent.message_id)
            except Exception:
                pass
    return indexed


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="重建名玺频道相册索引")
    parser.add_argument("--start", type=int, default=10390)
    parser.add_argument("--end", type=int, default=10488)
    parser.add_argument("--dig-chat-id", type=int, default=None)
    args = parser.parse_args()

    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("缺少 TELEGRAM_BOT_TOKEN")
    bot = telebot.TeleBot(token, parse_mode=None)
    count = rebuild_index_from_channel(
        bot,
        start_msg=args.start,
        end_msg=args.end,
        dig_chat_id=args.dig_chat_id,
    )
    print(f"indexed={count}")


if __name__ == "__main__":
    main()
