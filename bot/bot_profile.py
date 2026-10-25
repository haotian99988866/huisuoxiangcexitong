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
    "🎬 分类找剧、找综艺，查看剧集\n"
    "🎵 按语言找歌，在线播放、收藏与歌单\n"
    "📰 浏览东南亚大事件，主动订阅新资讯\n"
    "🔔 自主订阅会所出勤提醒\n\n"
    "水墨界面每日换景，节气与节日自动点缀。\n\n"
    "👇 使用方法\n"
    "1. 点击下方【开始】，进入主菜单\n"
    "2. 点【实时出勤】或左下角【昊天生活通】，进入小程序\n"
    "3. 点【观影】或【音乐】，直接进入对应模块\n"
    "4. /remind 管理出勤提醒；/news 订阅新闻，/news_off 取消\n"
    "5. /help 查看指南；预约或咨询可直接留言\n\n"
    "📞 联系：@haotian99988866"
)
SHORT_DESCRIPTION = "昊天生活通｜会所出勤、晚班外送、影视、音乐与东南亚资讯；出勤/新闻自主订阅。水墨每日换景。咨询 @haotian99988866"
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
