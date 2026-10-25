import unittest
from unittest.mock import Mock

import bot_profile
import welcome


class WelcomeTests(unittest.TestCase):
    def test_main_menu_has_direct_music_and_preserves_existing_entries(self):
        buttons = [button for row in welcome.home_keyboard().keyboard for button in row]
        self.assertEqual(sum(button.url == welcome.MUSIC_URL for button in buttons), 1)
        self.assertTrue(welcome.MUSIC_URL.endswith("?startapp=music"))
        self.assertTrue({welcome.MINIAPP_URL, welcome.MOVIES_URL, welcome.RECHARGE_URL,
                         welcome.CONTACT_URL, welcome.CHANNEL_URL, welcome.SHARE_URL}
                        .issubset({button.url for button in buttons}))
        self.assertTrue({"menu:promo", "menu:help", "sub:menu", "news:menu"}
                        .issubset({button.callback_data for button in buttons}))

    def test_descriptions_fit_telegram_limits_and_cover_current_modules(self):
        self.assertLessEqual(len(bot_profile.DESCRIPTION.encode("utf-16-le")) // 2, 512)
        self.assertLessEqual(len(bot_profile.SHORT_DESCRIPTION.encode("utf-16-le")) // 2, 120)
        for text in [welcome.HOME_TEXT, welcome.HELP_TEXT, bot_profile.DESCRIPTION]:
            for feature in ["观影", "音乐", "节气", "出勤"]:
                self.assertIn(feature, text)
        self.assertIn("/news_off", welcome.HELP_TEXT)
        self.assertIn("筹备中", welcome.HELP_TEXT)

    def test_start_sends_current_text_and_keyboard_without_preview(self):
        bot = Mock()
        welcome.send_welcome(bot, 123)
        args, options = bot.send_message.call_args
        self.assertEqual(args, (123, welcome.HOME_TEXT))
        self.assertEqual(options["parse_mode"], "HTML")
        self.assertTrue(options["disable_web_page_preview"])
        self.assertTrue(any(button.url == welcome.MUSIC_URL
                            for row in options["reply_markup"].keyboard for button in row))
