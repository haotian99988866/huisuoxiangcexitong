from datetime import datetime, timedelta
import unittest
from unittest.mock import MagicMock, patch

import staff_customer_duty as service
from customer_records import RecordConflict

START = datetime(2026, 11, 4, 6)
END = START + timedelta(hours=12)
WINDOW = (START, END, "白班")


def member(venue=1, **changes):
    return {"id": 12, "venue_id": venue, "code": "012", "venue": "Test venue",
            "ended_at": None, "deleted_at": None, "blocked": False,
            "adult_confirmed": True, "authorized": False, "employment": "", **changes}


class StaffCustomerDutyTests(unittest.TestCase):
    def test_all_public_staff_venues_are_eligible_without_individual_authorization(self):
        for venue in (1, 2, 4, 5, 6):
            with self.subTest(venue=venue):
                self.assertTrue(service.eligible(member(venue)))
        for value in (member(3), member(7), member(6, adult_confirmed=False),
                      member(ended_at=START), member(deleted_at=START), member(blocked=True)):
            self.assertFalse(service.eligible(value))

    def test_member_lookup_supports_all_staff_venues_without_permission_dependency(self):
        cur = MagicMock()
        for venue in (1, 2, 4, 5, 6):
            cur.fetchone.return_value = member(venue)
            self.assertEqual(service.member_row(cur, 12, lock=True)["venue_id"], venue)
            query = cur.execute.call_args.args[0]
            self.assertIn("FOR UPDATE", query)
            self.assertNotIn("staff_customer_permissions", query)
        cur.fetchone.return_value = member(7)
        with self.assertRaises(ValueError):
            service.member_row(cur, 12)

    @patch.object(service, "get_conn")
    @patch.object(service, "current_shift_window", return_value=WINDOW)
    def test_catalog_matches_latest_roster_and_is_batched_and_user_scoped(self, window, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"members_json": "[12]", "published_at": START + timedelta(hours=1)}
        cur.fetchall.return_value = [
            {"id": 12, "enabled": True, "revision": 3},
            {"id": 13, "enabled": None, "revision": None},
        ]
        result = service.catalog_status(2, 99)
        self.assertEqual(set(result["states"]), {"12", "13"})
        self.assertTrue(result["states"]["12"]["subscribed"])
        self.assertTrue(result["states"]["12"]["available"])
        self.assertIsNone(result["states"]["13"]["published_at"])
        self.assertEqual(result["states"]["13"]["revision"], 0)
        self.assertEqual(cur.execute.call_count, 2)
        query, args = cur.execute.call_args.args
        self.assertEqual(args, (99, 2))
        for guard in ("m.ended_at IS NULL", "m.deleted_at IS NULL", "m.blocked=0", "m.adult_confirmed=1"):
            self.assertIn(guard, query)
        self.assertNotIn("description", query)
        self.assertNotIn("employment", query)

    @patch.object(service, "get_conn")
    @patch.object(service, "current_shift_window", return_value=WINDOW)
    def test_photo_based_venues_combine_roster_and_photo_publication(self, window, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"members_json": "[12]", "published_at": START + timedelta(hours=1)}
        cur.fetchall.side_effect = [
            [{"staff_id": 12, "published": START + timedelta(hours=2)},
             {"staff_id": 13, "published": START + timedelta(hours=3)}],
            [{"id": 12, "enabled": False, "revision": 1}, {"id": 13, "enabled": None, "revision": None}],
        ]
        result = service.catalog_status(6, 99)
        self.assertEqual(result["states"]["12"]["published_at"], str(START + timedelta(hours=2)))
        self.assertEqual(result["states"]["13"]["published_at"], str(START + timedelta(hours=3)))
        self.assertEqual(cur.execute.call_count, 3)

    @patch.object(service, "get_conn")
    def test_catalog_rejects_delivery_boolean_and_unsupported_venues(self, get_conn):
        for venue in (3, 7, True, "1", None):
            with self.assertRaises(ValueError):
                service.catalog_status(venue, 99)
        get_conn.assert_not_called()

    def test_removed_staff_in_latest_roster_is_not_currently_on_duty(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"members_json": "[13]", "published_at": START + timedelta(hours=2)}
        self.assertIsNone(service.published_at(cur, member(2), START, END))
        self.assertIn("ORDER BY published_at DESC,source_id DESC LIMIT 1", cur.execute.call_args.args[0])

    def test_photo_publication_requires_new_timestamp_in_current_window(self):
        cur = MagicMock()
        photo = START + timedelta(hours=2)
        cur.fetchone.side_effect = [None, {"published": photo}]
        self.assertEqual(service.published_at(cur, member(4), START, END, START), photo)
        cur.fetchone.side_effect = [None, {"published": photo}]
        self.assertIsNone(service.published_at(cur, member(4), START, END, photo))
        query, args = cur.execute.call_args.args
        self.assertIn("last_seen>=%s AND last_seen<%s", query)
        self.assertEqual(args, (12, START, END))

    @patch.object(service, "get_conn")
    @patch.object(service, "member_row")
    @patch.object(service, "current_shift_window", return_value=WINDOW)
    def test_all_venues_allow_new_subscription_only_before_current_attendance(self, window, lookup, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        for venue in (1, 2, 4, 5, 6):
            lookup.return_value = member(venue)
            photo_reads = [{"published": None}] if venue in (4, 5, 6) else []
            # Request replay lookup, current state, then post-write state.
            cur.fetchone.side_effect = [None, None, *photo_reads, None,
                                       None, *photo_reads, {"enabled": True, "revision": 1}]
            result = service.save({"staff_id": 12, "enabled": True, "revision": 0, "request_id": "a" * 32}, 99)
            self.assertTrue(result["subscribed"])
            roster = {"members_json": "[12]", "published_at": START + timedelta(hours=1)}
            cur.fetchone.side_effect = [None, roster, *photo_reads, None]
            with self.assertRaises(RecordConflict):
                service.save({"staff_id": 12, "enabled": True, "revision": 0, "request_id": "b" * 32}, 99)

    def worker(self, *, venue=2, active=True, publication=True, shift="白班", send_error=None):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.rowcount = 1
        sub = {"staff_id": 12, "user_id": 99, "enabled": active, "subscribed_at": START + timedelta(minutes=1)}
        job = {"staff_id": 12, "user_id": 99, "duty_date": START.date(), "shift": shift,
               "published_at": START + timedelta(hours=2)}
        cur.fetchall.side_effect = [[sub], [job]]
        roster = {"members_json": "[12]" if publication else "[]", "published_at": job["published_at"]}
        cur.fetchone.side_effect = [{"acquired": 1}, sub, *([roster] if active else []), sub, roster]
        bot = MagicMock()
        bot.send_message.side_effect = send_error
        with patch.object(service, "get_conn", return_value=conn), \
                patch.object(service, "member_row", return_value=member(venue)), \
                patch.object(service, "current_shift_window", return_value=(START, END, shift)):
            service.deliver(bot)
        return cur, bot

    def test_worker_reminds_each_shift_without_disabling_continuous_subscription(self):
        for shift in ("白班", "晚班"):
            cur, bot = self.worker(shift=shift)
            bot.send_message.assert_called_once()
            self.assertIn("会所出勤发布提醒", bot.send_message.call_args.args[1])
            self.assertNotIn("正规按摩", bot.send_message.call_args.args[1])
            self.assertFalse(any("SET enabled=0" in call.args[0] for call in cur.execute.call_args_list))
            self.assertTrue(any("ON DUPLICATE KEY UPDATE staff_id=VALUES(staff_id)" in call.args[0]
                                for call in cur.execute.call_args_list))

    def test_worker_does_not_send_after_cancellation_or_roster_removal(self):
        for changes in ({"active": False}, {"publication": False}):
            _, bot = self.worker(**changes)
            bot.send_message.assert_not_called()

    def test_worker_preserves_regular_massage_notification_copy(self):
        _, bot = self.worker(venue=1)
        self.assertIn("正规按摩出勤发布提醒", bot.send_message.call_args.args[1])
