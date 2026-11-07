import unittest
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_profile
import menu_messages
import pymysql
import welcome


class WelcomeTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("menu_messages.keep_latest", side_effect=lambda bot, message, **kwargs: message)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_main_menu_groups_features_into_five_categories(self):
        buttons = [button for row in welcome.home_keyboard().keyboard for button in row]
        self.assertEqual([len(row) for row in welcome.home_keyboard().keyboard], [2, 2, 1])
        self.assertEqual([button.callback_data for button in buttons], [
            "menu:category:venue", "menu:category:media", "menu:category:news",
            "menu:category:services", "menu:category:account",
        ])
        all_destinations = {
            welcome.SHARE_URL if destination == "share" else destination
            for _, entries in welcome.MENU_CATEGORIES.values()
            for _, destination in entries
        }
        self.assertTrue({
            welcome.VENUE_URL, welcome.DELIVERY_URL, welcome.HOTELS_URL,
            welcome.SHORT_DRAMAS_URL, welcome.NEWS_URL, welcome.RECHARGE_URL,
            welcome.SERVICES_URL, welcome.MOVIES_URL, welcome.PERSONAL_URL,
            welcome.CONTACT_URL, welcome.CHANNEL_URL, welcome.MEMBERSHIP_URL,
            welcome.PHONE_URL, "callback:news:menu", "callback:sub:menu", welcome.SHARE_URL,
        }.issubset(all_destinations))
        self.assertEqual(len(buttons), 5)
        self.assertNotIn("使用指南", {button.text for button in buttons})
        self.assertIn("【新闻订阅】", welcome.HELP_TEXT)
        self.assertEqual(len(welcome.sub_keyboard().keyboard), 1)
        self.assertEqual(len(welcome.sub_keyboard().keyboard[0]), 2)
        for key, (title, _) in welcome.MENU_CATEGORIES.items():
            with self.subTest(category=key):
                category_title, keyboard = welcome.category_keyboard(key)
                self.assertEqual(category_title, title)
                self.assertEqual(keyboard.keyboard[-1][0].callback_data, "menu:home")
        rendered_destinations = {
            button.url or f"callback:{button.callback_data}"
            for key in welcome.MENU_CATEGORIES
            for row in welcome.category_keyboard(key)[1].keyboard
            for button in row
        }
        self.assertTrue(all_destinations.issubset(rendered_destinations))

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

    def test_start_removes_old_reply_keyboard_and_adds_grouped_inline_menu(self):
        bot = Mock()
        bot.send_message.return_value = SimpleNamespace(
            chat=SimpleNamespace(id=123), message_id=456)
        welcome.send_welcome(bot, 123)
        args, options = bot.send_message.call_args
        self.assertEqual(args, (123, welcome.HOME_TEXT))
        self.assertEqual(options["parse_mode"], "HTML")
        self.assertTrue(options["disable_web_page_preview"])
        remove_markup = json.loads(options["reply_markup"].to_json())
        self.assertTrue(remove_markup["remove_keyboard"])
        bot.edit_message_reply_markup.assert_called_once()
        inline_markup = json.loads(
            bot.edit_message_reply_markup.call_args.kwargs["reply_markup"].to_json())
        self.assertEqual([len(row) for row in inline_markup["inline_keyboard"]], [2, 2, 1])

    def test_menu_links_preserve_destinations_and_personal_share(self):
        bot = Mock()
        for row in welcome.legacy_home_keyboard().keyboard:
            for button in row:
                if not button.url:
                    continue
                with self.subTest(button=button.text), patch(
                        "customer_analytics.invite_link", return_value=welcome.BOT_URL + "?start=ref_test"):
                    welcome.send_menu_link(bot, 123, 123, button.text)
                sent = bot.edit_message_reply_markup.call_args.kwargs["reply_markup"].keyboard[0][0]
                if button.text == "分享好友":
                    self.assertIn("ref_test", sent.url)
                else:
                    self.assertEqual(sent.url, button.url)

    def test_category_callback_edits_the_same_message_with_compact_links(self):
        bot = Mock()
        call = SimpleNamespace(
            data="menu:category:news",
            id="callback",
            from_user=SimpleNamespace(id=123),
            message=SimpleNamespace(chat=SimpleNamespace(id=123), message_id=456),
        )

        welcome.show_screen(bot, call)

        bot.send_message.assert_not_called()
        bot.edit_message_text.assert_called_once()
        self.assertIn("资讯与提醒", bot.edit_message_text.call_args.args[0])
        markup = bot.edit_message_text.call_args.kwargs["reply_markup"]
        callbacks = [button.callback_data for row in markup.keyboard for button in row]
        self.assertIn("news:menu", callbacks)
        self.assertIn("sub:menu", callbacks)
        self.assertEqual(callbacks[-1], "menu:home")

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

    def test_return_home_edits_the_existing_message(self):
        bot = Mock()
        call = SimpleNamespace(data="menu:home", id="callback",
                               message=SimpleNamespace(chat=SimpleNamespace(id=123),
                                                       message_id=456))
        welcome.show_screen(bot, call)
        bot.send_message.assert_not_called()
        bot.edit_message_text.assert_called_once()
        markup = json.loads(bot.edit_message_text.call_args.kwargs["reply_markup"].to_json())
        self.assertEqual([len(row) for row in markup["inline_keyboard"]], [2, 2, 1])

    def test_command_history_handler_runs_before_start_and_continues_dispatch(self):
        import main
        handlers = [item["function"] for item in main.bot.message_handlers]
        self.assertLess(handlers.index(main.handle_command_history_cleanup),
                        handlers.index(main.handle_start))
        message = SimpleNamespace(
            text="/news_off", chat=SimpleNamespace(id=123, type="private"), message_id=20)
        self.assertTrue(main.is_private_command(message))
        with patch("main.menu_messages.clear_before") as clear:
            result = main.handle_command_history_cleanup(message)
        clear.assert_called_once_with(main.bot, message)
        self.assertEqual(type(result).__name__, "ContinueHandling")
        with patch("main.menu_messages.clear_before", side_effect=pymysql.MySQLError("offline")), \
                self.assertLogs("main"):
            result = main.handle_command_history_cleanup(message)
        self.assertEqual(type(result).__name__, "ContinueHandling")
