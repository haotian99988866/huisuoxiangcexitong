import types
import unittest
from unittest import mock

import relay


class FakeBot:
    def __init__(self):
        self.sent = []
        self.copied = []
        self._next = 1000

    def _id(self):
        self._next += 1
        return self._next

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return types.SimpleNamespace(message_id=self._id())

    def copy_message(self, to_chat, from_chat, message_id, **kwargs):
        self.copied.append((to_chat, from_chat, message_id))
        return types.SimpleNamespace(message_id=self._id())


def make_user(user_id=987654321):
    return types.SimpleNamespace(
        id=user_id, username="tester", first_name="T", last_name=None
    )


class LimitTests(unittest.TestCase):
    def test_rate_limit(self):
        relay._recent.clear()
        results = [relay.is_rate_limited(1) for _ in range(relay.RATE_LIMIT_COUNT + 1)]
        self.assertEqual(results[:-1], [False] * relay.RATE_LIMIT_COUNT)
        self.assertTrue(results[-1])
        self.assertFalse(relay.is_rate_limited(2))

    def test_ack_interval(self):
        relay._last_ack.clear()
        self.assertTrue(relay.should_ack(5))
        self.assertFalse(relay.should_ack(5))


class RelayFlowTests(unittest.TestCase):
    CUSTOMER = 987654321
    ADMIN = 111222333

    def tearDown(self):
        conn = relay.get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM bot_relay WHERE customer_id = %s", (self.CUSTOMER,))
                cursor.execute("DELETE FROM bot_users WHERE tg_user_id = %s", (self.CUSTOMER,))
            conn.commit()
        finally:
            conn.close()

    def test_customer_to_admin_and_back(self):
        relay.ensure_tables()
        bot = FakeBot()
        user = make_user(self.CUSTOMER)
        message = types.SimpleNamespace(
            from_user=user, chat=types.SimpleNamespace(id=self.CUSTOMER), message_id=7
        )
        with mock.patch.object(relay, "admin_ids", return_value={self.ADMIN}):
            self.assertFalse(relay.touch_user(user, count_message=True))
            self.assertTrue(relay.relay_to_admins(bot, message))

        header_id, copied_id = 1001, 1002
        self.assertEqual(relay.find_customer(self.ADMIN, header_id), self.CUSTOMER)
        self.assertEqual(relay.find_customer(self.ADMIN, copied_id), self.CUSTOMER)

        reply = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=self.ADMIN),
            message_id=50,
            text="你好",
            reply_to_message=types.SimpleNamespace(message_id=copied_id),
        )
        self.assertTrue(relay.relay_admin_reply(bot, reply))
        self.assertIn((self.CUSTOMER, self.ADMIN, 50), bot.copied)

    def test_block_and_unrelated_reply(self):
        relay.ensure_tables()
        bot = FakeBot()
        user = make_user(self.CUSTOMER)
        relay.touch_user(user, count_message=True)
        relay.remember_relay(self.ADMIN, 60, self.CUSTOMER)
        block = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=self.ADMIN),
            message_id=61,
            text="/block",
            reply_to_message=types.SimpleNamespace(message_id=60),
        )
        self.assertTrue(relay.relay_admin_reply(bot, block))
        self.assertTrue(relay.touch_user(user))
        block.text = "/unblock"
        relay.relay_admin_reply(bot, block)
        self.assertFalse(relay.touch_user(user))

        stranger = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=self.ADMIN),
            message_id=70,
            text="hi",
            reply_to_message=types.SimpleNamespace(message_id=99999),
        )
        self.assertFalse(relay.relay_admin_reply(bot, stranger))


if __name__ == "__main__":
    unittest.main()
