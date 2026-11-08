import os

import telebot
from dotenv import load_dotenv
from telebot import types

load_dotenv(".env")

NAME = "昊天机器人助手"
DESCRIPTION = (
    "西港会所/外送\n"
    "西港会所与外送信息，一站直达。\n\n"
    "会所出勤 · 西港外送 · 出勤提醒\n"
    "影视观影 · 在线音乐 · 昊天大事件\n"
    "Telegram会员 · 柬埔寨话费代充\n\n"
    "充值订单人工处理，非即时到账。\n"
    "点击【开始】进入主菜单。\n\n"
    "咨询：@haotian99988866"
)
SHORT_DESCRIPTION = (
    "西港生活服务｜出勤、影视音乐、资讯、会员与话费代充。"
    "充值订单人工处理。咨询 @haotian99988866"
)
COMMANDS = [
    types.BotCommand("start", "主菜单"),
    types.BotCommand("remind", "出勤提醒"),
    types.BotCommand("news", "大事件新闻订阅"),
    types.BotCommand("news_off", "取消新闻订阅"),
    types.BotCommand("paysupport", "Premium 付款帮助"),
    types.BotCommand("member", "会员网站登录"),
    types.BotCommand("member_sales", "会员余额订单管理"),
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
