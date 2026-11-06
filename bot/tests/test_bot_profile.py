import unittest

from bot_profile import DESCRIPTION, SHORT_DESCRIPTION


class BotProfileTests(unittest.TestCase):
    def test_descriptions_fit_telegram_limits(self):
        for text, limit in ((DESCRIPTION, 512), (SHORT_DESCRIPTION, 120)):
            with self.subTest(limit=limit):
                self.assertTrue(text.strip())
                self.assertLessEqual(len(text.encode("utf-16-le")) // 2, limit)

    def test_profile_explains_services_and_manual_fulfillment(self):
        for feature in ("会所出勤", "西港外送", "影视观影", "在线音乐", "昊天大事件",
                        "Telegram会员", "柬埔寨话费", "人工处理", "非即时到账"):
            with self.subTest(feature=feature):
                self.assertIn(feature, DESCRIPTION)
        self.assertIn("人工处理", SHORT_DESCRIPTION)
        self.assertIn("@haotian99988866", SHORT_DESCRIPTION)

    def test_description_stays_compact_without_command_list(self):
        self.assertLessEqual(len(DESCRIPTION.encode("utf-16-le")) // 2, 180)
        self.assertLessEqual(len(DESCRIPTION.splitlines()), 12)
        self.assertNotIn("/help", DESCRIPTION)
        self.assertNotIn("/news", DESCRIPTION)
