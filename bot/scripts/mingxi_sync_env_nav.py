"""运维：就地更新名玺【环境导航】并置顶。

用法（在 bot 目录）:
  .\\venv\\Scripts\\python.exe scripts\\mingxi_sync_env_nav.py
  .\\venv\\Scripts\\python.exe scripts\\mingxi_sync_env_nav.py --msg-id 10503
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
import telebot

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from footer import footer_keyboard
from mingxi_channel_album import MINGXI_CHANNEL_ID
from mingxi_complete_attendance import (
    ANCHOR_ENV_NAV,
    ENV_NAV_MSG_ID,
    _pin_channel_message,
    build_env_nav_html,
    set_channel_anchor,
)


def main():
    parser = argparse.ArgumentParser(description="同步名玺环境导航帖")
    parser.add_argument("--msg-id", type=int, default=ENV_NAV_MSG_ID)
    parser.add_argument("--venue-name", default="名玺水汇会所")
    args = parser.parse_args()

    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("缺少 TELEGRAM_BOT_TOKEN")
    bot = telebot.TeleBot(token, parse_mode=None)
    channel_id = int(MINGXI_CHANNEL_ID)
    text = build_env_nav_html(args.venue_name)
    try:
        bot.edit_message_text(
            text,
            channel_id,
            int(args.msg_id),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=footer_keyboard(),
        )
    except telebot.apihelper.ApiTelegramException as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    set_channel_anchor(channel_id, ANCHOR_ENV_NAV, args.msg_id)
    _pin_channel_message(bot, channel_id, args.msg_id)
    print(f"env_nav synced msg={args.msg_id}")


if __name__ == "__main__":
    main()
