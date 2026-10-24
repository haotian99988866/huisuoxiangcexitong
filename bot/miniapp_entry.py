"""小程序入口工具：设置机器人聊天菜单按钮，或往频道发送带“打开小程序”按钮的消息。

用法（在 bot 目录下）：
  python miniapp_entry.py menu <小程序网址> [按钮文字]
      设置机器人聊天框左下角的菜单按钮（每次隧道地址变化后重新执行即可）
  python miniapp_entry.py post <频道ID|all> [--pin] [--text 文案] [--button 按钮文字]
      往指定频道（all = 已监听的全部会所频道）发送带按钮的消息，--pin 同时置顶
  python miniapp_entry.py update [--text 文案] [--button 按钮文字]
      修改之前由本脚本发送的消息（编号记录在 miniapp_posts.json）
"""
import argparse
import json
import os
import sys

import telebot
from dotenv import load_dotenv
from telebot import types

from config.channels import LISTEN_CHANNELS

load_dotenv()

DEFAULT_MENU_TEXT = "打开会所"
DEFAULT_POST_TEXT = (
    "🌴 西港会所 · 外送 实时信息\n\n"
    "今日出勤、技师照片、价格一键查看，随时更新\n\n"
    "👇 点击下方按钮进入"
)
DEFAULT_BUTTON_TEXT = "查看全西港会所/外送"
POSTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "miniapp_posts.json")


def load_posts():
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE, encoding="utf-8") as file:
            return json.load(file)
    return {}


def save_posts(posts):
    with open(POSTS_FILE, "w", encoding="utf-8") as file:
        json.dump(posts, file, ensure_ascii=False, indent=2)


def get_bot():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("未找到 TELEGRAM_BOT_TOKEN，请检查 bot/.env")
    return telebot.TeleBot(token)


def miniapp_link(bot):
    # 主小程序直达链接，需要先在 BotFather 里为机器人配置 Mini App
    return f"https://t.me/{bot.get_me().username}?startapp"


def set_menu(url, text):
    bot = get_bot()
    bot.set_chat_menu_button(
        menu_button=types.MenuButtonWebApp(
            type="web_app", text=text, web_app=types.WebAppInfo(url=url)
        )
    )
    print(f"菜单按钮已设置：{text} -> {url}")


def post_to_channels(target, text, button_text, pin):
    bot = get_bot()
    chat_ids = list(LISTEN_CHANNELS) if target == "all" else [target]
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(button_text, url=miniapp_link(bot)))
    posts = load_posts()
    for chat_id in chat_ids:
        try:
            message = bot.send_message(chat_id, text, reply_markup=markup)
            posts[str(chat_id)] = message.message_id
            if pin:
                bot.pin_chat_message(
                    chat_id, message.message_id, disable_notification=True
                )
            print(f"已发送到 {chat_id}{'（已置顶）' if pin else ''}")
        except Exception as exc:
            print(f"发送到 {chat_id} 失败：{type(exc).__name__} {exc}")
    save_posts(posts)


def update_posts(text, button_text):
    """修改之前已发送（已记录编号）的消息文案和按钮。"""
    bot = get_bot()
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(button_text, url=miniapp_link(bot)))
    for chat_id, message_id in load_posts().items():
        try:
            bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=markup
            )
            print(f"已更新 {chat_id}")
        except Exception as exc:
            print(f"更新 {chat_id} 失败：{type(exc).__name__} {exc}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    menu = sub.add_parser("menu")
    menu.add_argument("url")
    menu.add_argument("text", nargs="?", default=DEFAULT_MENU_TEXT)

    post = sub.add_parser("post")
    post.add_argument("target")
    post.add_argument("--pin", action="store_true")
    post.add_argument("--text", default=DEFAULT_POST_TEXT)
    post.add_argument("--button", default=DEFAULT_BUTTON_TEXT)

    update = sub.add_parser("update")
    update.add_argument("--text", default=DEFAULT_POST_TEXT)
    update.add_argument("--button", default=DEFAULT_BUTTON_TEXT)

    args = parser.parse_args()
    if args.command == "menu":
        set_menu(args.url, args.text)
    elif args.command == "update":
        update_posts(args.text, args.button)
    else:
        post_to_channels(args.target, args.text, args.button, args.pin)


if __name__ == "__main__":
    main()
