import time
import types
import unittest

import footer

CHANNEL = "-1009999999999"


class FakeBot:
    def __init__(self):
        self.sent = []
        self.deleted = []
        self._next = 100

    def send_message(self, chat_id, text, **kwargs):
        self._next += 1
        self.sent.append((chat_id, text, kwargs))
        return types.SimpleNamespace(message_id=self._next)

    def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class FooterTests(unittest.TestCase):
    def setUp(self):
        footer.ensure_tables()

    def tearDown(self):
        conn = footer.get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM bot_footers WHERE channel_id = %s", (CHANNEL,))
            conn.commit()
        finally:
            conn.close()

    def test_replaces_previous_footer(self):
        bot = FakeBot()
        first = footer.post_footer(bot, CHANNEL)
        self.assertEqual(bot.deleted, [])
        second = footer.post_footer(bot, CHANNEL)
        self.assertEqual(bot.deleted, [(CHANNEL, first.message_id)])
        self.assertEqual(footer._get_last(CHANNEL), second.message_id)
        self.assertTrue(bot.sent[0][2]["disable_notification"])

    def test_schedule_debounces(self):
        bot = FakeBot()
        footer.schedule_footer(bot, CHANNEL, delay=0.2)
        footer.schedule_footer(bot, CHANNEL, delay=0.2)
        footer.schedule_footer(bot, CHANNEL, delay=0.2)
        time.sleep(0.8)
        self.assertEqual(len(bot.sent), 1)

    def test_send_failure_keeps_old(self):
        bot = FakeBot()
        first = footer.post_footer(bot, CHANNEL)

        def boom(*args, **kwargs):
            raise RuntimeError("x")

        bot.send_message = boom
        self.assertIsNone(footer.post_footer(bot, CHANNEL))
        self.assertEqual(footer._get_last(CHANNEL), first.message_id)


if __name__ == "__main__":
    unittest.main()
