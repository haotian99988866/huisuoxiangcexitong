import types
import unittest

import admin
import welcome


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append(text)
        return types.SimpleNamespace(message_id=1)

    def edit_message_text(self, text, chat_id, message_id, **kwargs):
        self.edits.append(text)

    def delete_message(self, *args, **kwargs):
        pass

    def copy_message(self, *args, **kwargs):
        return types.SimpleNamespace(message_id=2)


def msg(admin_id, text):
    return types.SimpleNamespace(
        from_user=types.SimpleNamespace(id=admin_id),
        chat=types.SimpleNamespace(id=admin_id),
        message_id=9,
        text=text,
    )


class AdminTests(unittest.TestCase):
    def setUp(self):
        admin.ensure_tables()
        admin.set_setting("promo", None)

    def tearDown(self):
        admin.set_setting("promo", None)
        admin._pending.clear()

    def test_promo_default_then_custom(self):
        self.assertEqual(welcome.screen_text("promo"), welcome.PROMO_TEXT)
        bot = FakeBot()
        admin._pending[5] = {"kind": "promo", "panel": 11}
        admin.handle_pending_message(bot, msg(5, "<b>新活动</b>"))
        self.assertEqual(welcome.screen_text("promo"), "<b>新活动</b>")
        self.assertFalse(admin.has_pending(5))
        self.assertEqual(bot.sent, [])
        admin.set_setting("promo", None)
        self.assertEqual(welcome.screen_text("promo"), welcome.PROMO_TEXT)

    def test_invalid_html_not_saved(self):
        class BadBot(FakeBot):
            def edit_message_text(self, text, chat_id, message_id, **kwargs):
                if "效果如下" in text:
                    raise ValueError("bad tag")
                super().edit_message_text(text, chat_id, message_id, **kwargs)

        bot = BadBot()
        admin._pending[5] = {"kind": "promo", "panel": 11}
        admin.handle_pending_message(bot, msg(5, "<b>坏"))
        self.assertEqual(welcome.screen_text("promo"), welcome.PROMO_TEXT)
        self.assertTrue(admin.has_pending(5))
        self.assertIn("格式有误", bot.edits[-1])


if __name__ == "__main__":
    unittest.main()
