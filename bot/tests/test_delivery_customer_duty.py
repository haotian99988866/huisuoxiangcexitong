from datetime import datetime, timedelta
import hashlib
import json
import unittest
from unittest.mock import MagicMock, patch

import requests

import delivery_customer_duty as delivery
import staff_customer_duty as service
from customer_records import RecordConflict
from delivery_posts import public_person_media

START = datetime(2026, 11, 4, 18)
END = datetime(2026, 11, 5, 6)
WINDOW = (START, END, "晚班")


def media(ident=1, age=23, kind="photo", created_at=None, **changes):
    return {
        "id": ident, "person_id": 12, "venue_id": 7, "message_id": ident,
        "channel_id": "-100123", "media_type": kind, "age": age, "media_group_id": None,
        "caption": "", "ocr_text": "", "image_hash": None,
        "created_at": created_at or START + timedelta(hours=2), **changes,
    }


def member(rows=None, **changes):
    rows = [media()] if rows is None else rows
    return {"id": 12, "venue_id": 7, "venue": "Delivery", "deleted_at": None,
            "merged_into_id": None, "consent_status": "granted",
            "channel_media": rows, "public_media": public_person_media(rows), **changes}


class DeliveryCustomerDutyTests(unittest.TestCase):
    def test_only_public_adult_active_authorized_delivery_identity_is_eligible(self):
        self.assertTrue(delivery.eligible(member()))
        for value in (member([media(age=None)]), member([media(age=17)]),
                      member(deleted_at=START), member(merged_into_id=13),
                      member(consent_status="withdrawn"), member(venue_id=1), member([])):
            with self.subTest(member=value):
                self.assertFalse(delivery.eligible(value))

    def test_night_photo_only_after_subscription_and_window_boundaries(self):
        subscription = START + timedelta(hours=1)
        self.assertEqual(delivery.published_at(member(), START, END, subscription), START + timedelta(hours=2))
        for rows in ([media(kind="video")], [media(age=None)], [media(age=17)],
                     [media(created_at=START - timedelta(seconds=1))],
                     [media(created_at=END)], [media(created_at=subscription)]):
            with self.subTest(rows=rows):
                self.assertIsNone(delivery.published_at(member(rows), START, END, subscription))
        self.assertEqual(delivery.published_at(member([media(created_at=START)]), START, END), START)
        day = START.replace(hour=6)
        self.assertIsNone(delivery.published_at(member([media(created_at=day + timedelta(hours=1))]), day, START))

    def test_repeated_photos_use_latest_public_photo_for_attendance(self):
        rows = [media(2, image_hash="1234567890abcdef"),
                media(1, image_hash="1234567890abcdef", created_at=START - timedelta(days=1))]
        self.assertEqual(member(rows)["public_media"], [{"id": 2, "type": "photo"}])
        self.assertEqual(delivery.published_at(member(rows), START, END), START + timedelta(hours=2))

    def test_shared_adult_album_guard_applies_to_notifications(self):
        rows = [media(2, age=23, media_group_id="g", caption="album"),
                media(1, age=17, media_group_id="g")]
        self.assertFalse(delivery.eligible(member(rows)))

    @patch.object(delivery, "get_conn")
    @patch.object(delivery, "current_shift_window", return_value=WINDOW)
    def test_catalog_status_batches_queries_and_scopes_subscription_to_user(self, window, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [
            [media(), media(2, person_id=13, age=None), media(3, person_id=14, kind="video")],
            [{"person_id": 12, "enabled": True, "revision": 4}],
        ]
        result = delivery.catalog_status(7, 99)
        self.assertEqual(set(result["states"]), {"12", "14"})
        self.assertTrue(result["states"]["12"]["subscribed"])
        self.assertEqual(result["states"]["12"]["revision"], 4)
        self.assertIsNone(result["states"]["14"]["published_at"])
        self.assertEqual(cur.execute.call_count, 2)
        self.assertEqual(cur.execute.call_args.args[1], (7, 99))
        query = cur.execute.call_args_list[0].args[0]
        for guard in ("p.deleted_at IS NULL", "p.merged_into_id IS NULL", "p.consent_status='granted'"):
            self.assertIn(guard, query)
        self.assertNotIn("attachments", query)

    @patch.object(delivery, "get_conn")
    def test_invalid_catalog_venue_never_queries_database(self, get_conn):
        for venue in (1, True, "7", None):
            with self.assertRaises(ValueError):
                delivery.catalog_status(venue, 99)
        get_conn.assert_not_called()

    def payload(self, **changes):
        return {"person_id": 12, "enabled": True, "revision": 0, "request_id": "a" * 32, **changes}

    @patch.object(service, "get_conn")
    @patch.object(service, "member_row")
    @patch.object(service, "current_shift_window", return_value=WINDOW)
    def test_save_uses_separate_tables_and_keeps_ongoing_subscription(self, window, lookup, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        lookup.return_value = member([media(created_at=START - timedelta(days=1))])
        cur.fetchone.side_effect = [None, None, {"enabled": True, "revision": 1}]
        result = service.save(self.payload(), 99, delivery=True)
        self.assertTrue(result["subscribed"])
        self.assertEqual(result["revision"], 1)
        lookup.assert_called_once_with(cur, 12, lock=True, delivery=True)
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any("INSERT INTO delivery_customer_subscriptions(person_id" in query for query in queries))
        self.assertFalse(any("staff_customer_" in query for query in queries))
        conn.commit.assert_called_once()

    @patch.object(service, "get_conn")
    @patch.object(service, "member_row")
    @patch.object(service, "current_shift_window", return_value=WINDOW)
    def test_save_rejects_already_on_duty_ineligible_or_stale_requests(self, window, lookup, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        for person, previous in ((member(), None), (member(consent_status="withdrawn"), None),
                                 (member([]), None), (member([]), {"enabled": False, "revision": 2})):
            lookup.return_value = person
            cur.fetchone.side_effect = [None, previous]
            with self.subTest(person=person), self.assertRaises(RecordConflict):
                service.save(self.payload(), 99, delivery=True)
        conn.commit.assert_not_called()
        self.assertEqual(conn.rollback.call_count, 4)

    @patch.object(service, "get_conn")
    @patch.object(service, "member_row")
    @patch.object(service, "current_shift_window", return_value=WINDOW)
    def test_cancel_is_allowed_after_attendance_and_cancels_pending_reminders(self, window, lookup, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        lookup.return_value = member()
        cur.fetchone.side_effect = [None, {"enabled": True, "revision": 1}, {"enabled": False, "revision": 2}]
        result = service.save(self.payload(enabled=False, revision=1), 99, delivery=True)
        self.assertFalse(result["subscribed"])
        self.assertTrue(any("delivery_customer_notifications SET status='cancelled'" in call.args[0]
                            for call in cur.execute.call_args_list))

    @patch.object(service, "get_conn")
    @patch.object(service, "member_row")
    @patch.object(service, "current_shift_window", return_value=WINDOW)
    def test_original_request_id_replay_returns_current_state_without_extra_write(self, window, lookup, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        lookup.return_value = member()
        digest = hashlib.sha256(json.dumps([12, True, 0]).encode()).hexdigest()
        cur.fetchone.side_effect = [{"user_id": 99, "request_hash": digest},
                                   {"enabled": True, "revision": 1}]
        result = service.save(self.payload(), 99, delivery=True)
        self.assertTrue(result["subscribed"])
        self.assertFalse(any("INSERT" in call.args[0] for call in cur.execute.call_args_list))

    @patch.object(service, "get_conn")
    def test_invalid_write_inputs_are_rejected_before_connection(self, get_conn):
        for changes in ({"person_id": True}, {"enabled": "true"}, {"revision": -1},
                        {"revision": True}, {"request_id": "invalid"}, {"person_id": 0}):
            with self.assertRaises(ValueError):
                service.save(self.payload(**changes), 99, delivery=True)
        get_conn.assert_not_called()

    def run_delivery(self, person=None, subscription=None, job=None, send_error=None):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.rowcount = 1
        sub = subscription or {"person_id": 12, "user_id": 99, "enabled": True,
                               "subscribed_at": START + timedelta(hours=1)}
        job = job or {"person_id": 12, "user_id": 99, "duty_date": START.date(),
                      "shift": "晚班", "published_at": START + timedelta(hours=2)}
        cur.fetchall.side_effect = [[sub], [job]]
        person = person or member()
        if delivery.eligible(person):
            cur.fetchone.side_effect = [{"acquired": 1}, sub, sub]
        else:
            cur.fetchone.side_effect = [{"acquired": 1}, sub]
        bot = MagicMock()
        bot.send_message.side_effect = send_error
        with patch.object(service, "get_conn", return_value=conn), \
                patch.object(service, "member_row", return_value=person), \
                patch.object(service, "current_shift_window", return_value=WINDOW):
            service.deliver(bot, delivery=True)
        return conn, cur, bot

    def test_worker_claims_before_sending_and_keeps_subscription_enabled(self):
        conn, cur, bot = self.run_delivery()
        bot.send_message.assert_called_once()
        self.assertIn("档案 #12", bot.send_message.call_args.args[1])
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any("ON DUPLICATE KEY UPDATE person_id=VALUES(person_id)" in query for query in queries))
        self.assertFalse(any("SET enabled=0" in query for query in queries))
        claim = next(call for call in cur.execute.call_args_list if "SET status=%s WHERE" in call.args[0])
        self.assertEqual(claim.args[1][0], "sending")
        status = next(call for call in cur.execute.call_args_list if "SET status=%s,sent_at" in call.args[0])
        self.assertEqual(status.args[1][0], "sent")
        self.assertGreaterEqual(conn.commit.call_count, 3)

    def test_worker_rechecks_cancelled_or_deleted_person_before_send(self):
        for person in (member(deleted_at=START), member(consent_status="withdrawn"),
                       member(merged_into_id=13), member([media(age=17)])):
            with self.subTest(person=person):
                _, cur, bot = self.run_delivery(person)
                bot.send_message.assert_not_called()
                self.assertTrue(any("SET enabled=0" in call.args[0] for call in cur.execute.call_args_list))
        cancelled = {"person_id": 12, "user_id": 99, "enabled": False,
                     "subscribed_at": START + timedelta(hours=1)}
        _, _, bot = self.run_delivery(subscription=cancelled)
        bot.send_message.assert_not_called()

    def test_worker_does_not_send_stale_shift_job_or_historical_publication(self):
        for job in ({"person_id": 12, "user_id": 99, "duty_date": (START - timedelta(days=1)).date(),
                     "shift": "晚班", "published_at": START - timedelta(days=1)},
                    {"person_id": 12, "user_id": 99, "duty_date": START.date(),
                     "shift": "晚班", "published_at": START}):
            _, _, bot = self.run_delivery(job=job)
            bot.send_message.assert_not_called()

    def test_uncertain_send_is_persisted_and_never_automatically_retried(self):
        _, cur, bot = self.run_delivery(send_error=requests.Timeout("uncertain"))
        bot.send_message.assert_called_once()
        status = next(call for call in cur.execute.call_args_list if "SET status=%s,sent_at" in call.args[0])
        self.assertEqual(status.args[1][:2], ("unconfirmed", None))
        pending_query = next(call.args[0] for call in cur.execute.call_args_list
                             if "ORDER BY created_at LIMIT" in call.args[0])
        self.assertIn("status='pending'", pending_query)

    def test_delivery_storage_has_nightly_unique_key_and_separate_identity(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        with patch.object(service, "get_conn", return_value=conn):
            service.ensure_tables(delivery=True)
        self.assertEqual(cur.execute.call_count, 3)
        ddl = "\n".join(call.args[0] for call in cur.execute.call_args_list)
        self.assertIn("PRIMARY KEY(person_id,user_id,duty_date,shift)", ddl)
        self.assertIn("REFERENCES delivery_people(id)", ddl)
        self.assertNotIn("staff_customer", ddl)

    def test_existing_staff_storage_and_identity_remain_unchanged(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        with patch.object(service, "get_conn", return_value=conn):
            service.ensure_tables()
        self.assertEqual(cur.execute.call_count, 4)
        ddl = "\n".join(call.args[0] for call in cur.execute.call_args_list)
        self.assertIn("staff_customer_permissions", ddl)
        self.assertIn("PRIMARY KEY(staff_id,user_id,duty_date,shift)", ddl)
        self.assertIn("REFERENCES staff_members(id)", ddl)
        self.assertNotIn("delivery_customer", ddl)

    def test_retention_keeps_linked_delivery_archive_media_for_long_term_subscriptions(self):
        import data_cleanup
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.rowcount = 0
        with patch("staff_archive.sync"), patch.object(data_cleanup, "get_conn", return_value=conn), \
                patch.object(data_cleanup, "delete_old_visits", return_value=0), \
                patch("customer_analytics.cleanup", return_value=0), \
                patch.object(data_cleanup, "delete_old_relays", return_value=0), \
                patch.object(data_cleanup, "delete_old_push_logs", return_value=0):
            data_cleanup.cleanup_expired_data()
        for call in cur.execute.call_args_list:
            query = call.args[0]
            self.assertEqual(call.args[1], (48,))
            if "DELETE FROM delivery_media " in query:
                self.assertIn("AND person_id IS NULL", query)
            else:
                self.assertNotIn("person_id", query)

    @patch.object(service, "get_conn")
    @patch.object(service, "member_row")
    @patch.object(service, "current_shift_window", return_value=WINDOW)
    def test_existing_staff_subscribe_and_cancel_keep_original_tables(self, window, lookup, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        lookup.return_value = {"id": 12, "venue_id": 1, "ended_at": None, "blocked": False,
                               "employment": "", "authorized": True}
        # Each state read first checks the roster publication, then the subscription.
        cur.fetchone.side_effect = [
            None, None, None, None, {"enabled": True, "revision": 1},
        ]
        result = service.save({"staff_id": 12, "enabled": True, "revision": 0, "request_id": "b" * 32}, 99)
        self.assertTrue(result["subscribed"])
        self.assertTrue(any("INSERT INTO staff_customer_subscriptions(staff_id" in call.args[0]
                            for call in cur.execute.call_args_list))
        cur.reset_mock()
        cur.fetchone.side_effect = [
            None, {"members_json": "[12]", "published_at": START}, {"enabled": True, "revision": 1},
            {"members_json": "[12]", "published_at": START}, {"enabled": False, "revision": 2},
        ]
        result = service.save({"staff_id": 12, "enabled": False, "revision": 1, "request_id": "c" * 32}, 99)
        self.assertFalse(result["subscribed"])
        self.assertTrue(any("staff_customer_notifications SET status='cancelled'" in call.args[0]
                            for call in cur.execute.call_args_list))
