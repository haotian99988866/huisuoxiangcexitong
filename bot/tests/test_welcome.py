import unittest
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_profile
import welcome


class WelcomeTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("welcome.keep_latest", side_effect=lambda bot, message, **kwargs: message)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_main_menu_has_all_entries_in_six_three_button_rows(self):
        buttons = [button for row in welcome.home_keyboard().keyboard for button in row]
        self.assertEqual(sum(button.url == welcome.MUSIC_URL for button in buttons), 1)
        self.assertTrue(welcome.MUSIC_URL.endswith("?startapp=music"))
        self.assertEqual(sum(button.url == welcome.MEMBERSHIP_URL for button in buttons), 1)
        self.assertEqual(sum(button.url == welcome.PHONE_URL for button in buttons), 1)
        self.assertTrue(welcome.MEMBERSHIP_URL.endswith("?startapp=membership"))
        self.assertTrue(welcome.PHONE_URL.endswith("?startapp=phone"))
        self.assertTrue({welcome.VENUE_URL, welcome.DELIVERY_URL, welcome.HOTELS_URL,
                         welcome.SHORT_DRAMAS_URL, welcome.NEWS_URL, welcome.RECHARGE_URL,
                         welcome.SERVICES_URL, welcome.MOVIES_URL, welcome.PERSONAL_URL,
                         welcome.CONTACT_URL, welcome.CHANNEL_URL, welcome.SHARE_URL}
                        .issubset({button.url for button in buttons}))
        self.assertTrue({"news:menu", "sub:menu"}
                        .issubset({button.callback_data for button in buttons}))
        self.assertEqual([button.text for button in welcome.home_keyboard().keyboard[-1]],
                         ["频道", "分享好友", "联系昊天"])
        self.assertEqual([len(row) for row in welcome.home_keyboard().keyboard], [3, 3, 3, 3, 2, 3])
        self.assertEqual(len(buttons), 17)
        self.assertEqual(len({button.text for button in buttons}), 17)
        self.assertNotIn("使用指南", {button.text for button in buttons})
        self.assertNotIn("menu:promo", {button.callback_data for button in buttons})
        self.assertNotIn("member:login", {button.callback_data for button in buttons})
        news_buttons = [button for button in buttons if button.callback_data == "news:menu"]
        self.assertEqual([button.text for button in news_buttons], ["新闻订阅"])
        self.assertIn("【新闻订阅】", welcome.HELP_TEXT)
        self.assertEqual(len(welcome.sub_keyboard().keyboard), 1)
        self.assertEqual(len(welcome.sub_keyboard().keyboard[0]), 2)
        self.assertNotIn("menu:home", {button.callback_data
                                     for row in welcome.sub_keyboard().keyboard for button in row})

    def test_descriptions_fit_telegram_limits_and_cover_current_modules(self):
        self.assertLessEqual(len(bot_profile.DESCRIPTION.encode("utf-16-le")) // 2, 512)
        self.assertLessEqual(len(bot_profile.SHORT_DESCRIPTION.encode("utf-16-le")) // 2, 120)
        for text in [welcome.HOME_TEXT, welcome.HELP_TEXT, bot_profile.DESCRIPTION]:
            for feature in ["观影", "音乐", "出勤"]:
                self.assertIn(feature, text)
            self.assertNotIn("水墨", text)
            self.assertNotIn("今日时令", text)
        self.assertIn("/news_off", welcome.HELP_TEXT)
        self.assertNotIn("筹备中", welcome.HELP_TEXT)
        self.assertNotIn("正式会员销售暂未开放", welcome.HELP_TEXT)
        for text in [welcome.HOME_TEXT, welcome.HELP_TEXT, bot_profile.DESCRIPTION]:
            self.assertIn("Telegram会员", text)
            self.assertIn("话费", text)
            self.assertIn("人工", text)
        self.assertLessEqual(len(welcome.HOME_TEXT), 200)
        self.assertIn("【我的】", welcome.HELP_TEXT)
        self.assertNotIn("【全部】→【会员充值】", welcome.HELP_TEXT)

    def test_command_menu_only_advertises_current_member_scheme(self):
        commands = {command.command for command in bot_profile.COMMANDS}
        self.assertTrue({"member", "member_sales", "paysupport"}.issubset(commands))
        self.assertFalse({"premium_sales", "stars_sales"} & commands)

    def test_start_sends_current_text_and_keyboard_without_preview(self):
        bot = Mock()
        with patch("customer_analytics.invite_link", return_value=welcome.BOT_URL + "?start=ref_" + "a" * 32):
            welcome.send_welcome(bot, 123)
        args, options = bot.send_message.call_args
        self.assertEqual(args, (123, welcome.HOME_TEXT))
        self.assertEqual(options["parse_mode"], "HTML")
        self.assertTrue(options["disable_web_page_preview"])
        markup = json.loads(options["reply_markup"].to_json())
        self.assertEqual([len(row) for row in markup["keyboard"]], [3, 3, 3, 3, 2, 3])
        self.assertTrue(markup["is_persistent"])
        self.assertTrue(markup["resize_keyboard"])
        self.assertFalse(markup["one_time_keyboard"])
        self.assertEqual({item["text"] for row in markup["keyboard"] for item in row},
                         {button.text for row in welcome.home_keyboard().keyboard for button in row})
        buttons = [item for row in markup["keyboard"] for item in row]
        self.assertEqual(sum("web_app" in button for button in buttons), 14)
        self.assertEqual({button["text"] for button in buttons if "web_app" not in button},
                         {"新闻订阅", "出勤提醒", "分享好友"})
        self.assertTrue(all("web_app" in item for item in buttons[:14]))
        by_text = {item["text"]: item for item in buttons}
        for inline in [button for row in welcome.home_keyboard().keyboard for button in row]:
            if inline.url in welcome.WEBAPP_PATHS:
                self.assertEqual(by_text[inline.text]["web_app"]["url"],
                                 welcome.WEBAPP_BASE_URL + welcome.WEBAPP_PATHS[inline.url])

    def test_menu_links_preserve_destinations_and_personal_share(self):
        bot = Mock()
        for row in welcome.home_keyboard().keyboard:
            for button in row:
                if not button.url:
                    continue
                with self.subTest(button=button.text), patch(
                        "customer_analytics.invite_link", return_value=welcome.BOT_URL + "?start=ref_test"):
                    welcome.send_menu_link(bot, 123, 123, button.text)
                sent = bot.send_message.call_args.kwargs["reply_markup"].keyboard[0][0]
                if button.text == "分享好友":
                    self.assertIn("ref_test", sent.url)
                else:
                    self.assertEqual(sent.url, button.url)

    def test_fixed_menu_precedes_relay_and_dispatches_actions(self):
        import main
        handlers = [item["function"] for item in main.bot.message_handlers]
        self.assertLess(handlers.index(main.handle_fixed_menu), handlers.index(main.handle_customer_message))
        self.assertLess(handlers.index(main.handle_fixed_menu), handlers.index(main.handle_attendance_text))
        for text, target in (("新闻订阅", "handle_news"), ("出勤提醒", "handle_remind"),
                             ("会所出勤", "send_menu_link")):
            message = SimpleNamespace(text=text, chat=SimpleNamespace(id=123),
                                      from_user=SimpleNamespace(id=123))
            self.assertTrue(welcome.is_menu_message(message))
            with patch("main.touch_user"), patch("main.menu_messages.keep_latest"), patch("main." + target) as action:
                main.handle_fixed_menu(message)
            action.assert_called_once()
        self.assertFalse(welcome.is_menu_message(SimpleNamespace(text="普通留言")))
        self.assertFalse(welcome.is_menu_message(SimpleNamespace(text="联系昊天", reply_to_message=object())))

    def test_return_home_sends_reply_keyboard_not_inline_edit(self):
        bot = Mock()
        call = SimpleNamespace(data="menu:home", id="callback",
                               message=SimpleNamespace(chat=SimpleNamespace(id=123)))
        welcome.show_screen(bot, call)
        bot.edit_message_text.assert_not_called()
        self.assertTrue(json.loads(bot.send_message.call_args.kwargs["reply_markup"].to_json())["is_persistent"])
