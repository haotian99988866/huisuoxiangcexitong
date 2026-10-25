import os

import telebot
from dotenv import load_dotenv
from telebot import types

load_dotenv(".env")

NAME = "昊天生活助手"
DESCRIPTION = (
    "🌴 昊天生活助手\n"
    "西港生活服务与东南亚资讯\n\n"
    "在这里你可以：\n"
    "✅ 查看名玺水汇、凯迪、大西洋、帝豪等会所今日出勤\n"
    "✅ 浏览技师照片、价格与服务项目\n"
    "✅ 查看西港外送实时信息\n"
    "✅ 订阅出勤提醒，名单一发布就通知你\n\n"
    "📰 /news 主动订阅东南亚大事件，/news_off 随时取消\n\n"
    "👇 使用方法\n"
    "1. 点击下方【开始】，进入主菜单\n"
    "2. 点【实时出勤】或左下角【昊天生活通】，进入小程序\n"
    "3. 点【🔔 出勤提醒】订阅会所，或发送 /remind\n"
    "4. 不会用？发送 /help 查看使用指南\n"
    "5. 需要预约或咨询，直接发消息留言，昊天会回复你\n\n"
    "📞 联系：@haotian99988866"
)
SHORT_DESCRIPTION = "昊天生活通 · 西港生活服务与东南亚资讯。会所、外送、影视、音乐与新闻提醒，一站查看。咨询 @haotian99988866"
COMMANDS = [
    types.BotCommand("start", "主菜单"),
    types.BotCommand("remind", "出勤提醒"),
    types.BotCommand("news", "大事件新闻订阅"),
    types.BotCommand("news_off", "取消新闻订阅"),
    types.BotCommand("help", "使用指南"),
]


def main():
    bot = telebot.TeleBot(os.getenv("TELEGRAM_BOT_TOKEN"))
    bot.set_my_name(NAME)
    bot.set_my_description(DESCRIPTION)
    bot.set_my_short_description(SHORT_DESCRIPTION)
    bot.set_my_commands(COMMANDS)
    print("机器人介绍:", bot.get_my_description().description[:20], "...")
    print("简介:", bot.get_my_short_description().short_description)


if __name__ == "__main__":
    main()
