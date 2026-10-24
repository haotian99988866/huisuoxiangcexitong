import datetime
import unittest

import subscribe

USER = 987654322
VENUE = 2
DAY = datetime.date(2000, 1, 1)


class SubscribeTests(unittest.TestCase):
    def setUp(self):
        subscribe.ensure_tables()

    def tearDown(self):
        conn = subscribe.get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM bot_subscriptions WHERE tg_user_id = %s", (USER,))
                cursor.execute("DELETE FROM bot_push_log WHERE duty_date = %s", (DAY,))
            conn.commit()
        finally:
            conn.close()

    def test_toggle(self):
        self.assertTrue(subscribe.toggle_subscription(USER, VENUE))
        self.assertIn(VENUE, subscribe.get_subscriptions(USER))
        self.assertIn(USER, subscribe.subscribers_of(VENUE))
        self.assertFalse(subscribe.toggle_subscription(USER, VENUE))
        self.assertNotIn(VENUE, subscribe.get_subscriptions(USER))
        self.assertIsNone(subscribe.toggle_subscription(USER, 3))

    def test_push_only_once(self):
        self.assertTrue(subscribe.claim_push(VENUE, DAY, "白班"))
        self.assertFalse(subscribe.claim_push(VENUE, DAY, "白班"))
        self.assertTrue(subscribe.claim_push(VENUE, DAY, "晚班"))

    def test_notify_ignores_other_venues_and_shifts(self):
        self.assertFalse(subscribe.notify_attendance(None, 3, DAY, "晚班", 5))
        self.assertFalse(subscribe.notify_attendance(None, VENUE, DAY, "实时出勤", 5))


if __name__ == "__main__":
    unittest.main()
