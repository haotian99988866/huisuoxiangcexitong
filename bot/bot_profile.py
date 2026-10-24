import os

import telebot
from dotenv import load_dotenv
from telebot import types

load_dotenv(".env")

DESCRIPTION = (
    "🌴 西港昊天会所 · 外送 实时信息\n\n"
    "在这里你可以：\n"
    "✅ 查看名玺水汇、凯迪、大西洋、帝豪等会所今日出勤\n"
    "✅ 浏览技师照片、价格与服务项目\n"
    "✅ 查看西港外送实时信息\n\n"
    "👇 使用方法\n"
    "1. 点击下方【开始】，查看最新活动\n"
    "2. 点击左下角【打开会所】，进入小程序\n"
    "3. 需要预约或咨询，直接联系昊天\n\n"
    "📞 联系：@haotian99988866"
)
SHORT_DESCRIPTION = "西港会所/外送实时出勤、技师照片、价格一键查看。预约咨询请联系 @haotian99988866"
COMMANDS = [
    types.BotCommand("start", "查看活动与入口"),
]


def main():
    bot = telebot.TeleBot(os.getenv("TELEGRAM_BOT_TOKEN"))
    bot.set_my_description(DESCRIPTION)
    bot.set_my_short_description(SHORT_DESCRIPTION)
    bot.set_my_commands(COMMANDS)
    print("机器人介绍:", bot.get_my_description().description[:20], "...")
    print("简介:", bot.get_my_short_description().short_description)


if __name__ == "__main__":
    main()
