from datetime import datetime
import unittest
from unittest.mock import MagicMock, Mock, patch

import staff_archive
from customer_records import RecordConflict


class StaffArchiveTests(unittest.TestCase):
    def test_full_staff_list_exceeds_old_page_size_and_retains_visibility_guards(self):
        for admin in (False, True):
            connection = MagicMock()
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [None, {"total": 61}]
            now = datetime.now()
            cursor.fetchall.side_effect = [[{
                "id": ident, "code": str(ident), "first_seen": now, "last_seen": now,
                "blocked": False, "price_tags_json": "[]", "last_roster_at": None,
                "last_photo_at": None, "cover": None, "photo_count": 0,
            } for ident in range(1, 62)], []]
            with self.subTest(admin=admin), patch.object(staff_archive, "get_conn", return_value=connection), \
                    patch.object(staff_archive, "staff_price", return_value=(None, None)):
                result = staff_archive.listing({"venue_id": 1, "mode": "all"}, admin=admin)
            self.assertEqual((result["total"], len(result["members"])), (61, 61))
            self.assertEqual(result["members"][0]["id"], 61)
            sql = cursor.execute.call_args_list[2].args[0]
            self.assertNotIn("OFFSET", sql)
            self.assertIn("m.deleted_at IS NULL", sql)
            if not admin:
                self.assertIn("m.blocked=0", sql)
                self.assertIn("m.adult_confirmed=1", sql)

    def test_delivery_and_invalid_inputs_are_not_staff_venues(self):
        for value in (3, 7, 8, 9, 10, True, "1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                staff_archive._venue(value)
        self.assertEqual(staff_archive._venue(1), 1)

    def test_existing_person_is_resolved_in_the_original_time_generation(self):
        cur = Mock()
        cur.fetchone.return_value = {"id": 12}
        seen = datetime(2026, 11, 1, 10)
        self.assertEqual(staff_archive._person(cur, 1, "012", seen), 12)
        sql, args = cur.execute.call_args_list[0].args
        self.assertIn("active_from<=%s", sql)
        self.assertIn("ended_at>%s", sql)
        self.assertNotIn("deleted_at IS NULL", sql)
        self.assertEqual(args, (1, "012", seen, seen))
        self.assertEqual(cur.execute.call_count, 2)

    def test_new_person_creates_a_venue_specific_slot(self):
        cur = Mock()
        cur.fetchone.return_value = None
        cur.lastrowid = 12
        seen = datetime(2026, 11, 1, 10)
        self.assertEqual(staff_archive._person(cur, 2, "012", seen), 12)
        self.assertEqual(cur.execute.call_args_list[-1].args[1], (2, "012", 12))

    @patch("staff_archive.get_conn")
    @patch("staff_archive.synchronize")
    def test_sync_failure_rolls_back_and_releases_lock(self, synchronize, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"acquired": 1}
        synchronize.side_effect = RuntimeError("archive unavailable")
        with self.assertRaisesRegex(RuntimeError, "archive unavailable"):
            staff_archive.sync()
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        self.assertIn("RELEASE_LOCK", cur.execute.call_args.args[0])
        conn.close.assert_called_once()

    @patch("staff_archive.get_conn")
    @patch("staff_archive.synchronize")
    @patch("visit_stats.admin_ids", return_value=[99])
    def test_repeated_replacement_of_same_original_id_returns_same_new_id(self, admins, synchronize, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"acquired": 1}, {"new_staff_id": 22}]
        result = staff_archive.replace({"staff_id": 12, "confirmed": True}, 99)
        self.assertEqual(result, {"staff_id": 22})
        self.assertFalse(any("INSERT" in call.args[0] for call in cur.execute.call_args_list))
        conn.commit.assert_not_called()

    @patch("staff_archive.get_conn")
    @patch("visit_stats.admin_ids", return_value=[99])
    def test_replacement_requires_authorization_and_explicit_confirmation(self, admins, get_conn):
        for data, actor in (({"staff_id": 12, "confirmed": True}, 88),
                            ({"staff_id": 12}, 99),
                            ({"staff_id": True, "confirmed": True}, 99)):
            with self.subTest(data=data, actor=actor), self.assertRaises(ValueError):
                staff_archive.replace(data, actor)
        get_conn.assert_not_called()

    @patch("staff_archive.get_conn")
    def test_photo_read_keeps_adult_and_active_guards(self, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = None
        self.assertIsNone(staff_archive.photo(12))
        sql, args = cur.execute.call_args.args
        self.assertIn("ended_at IS NULL", sql)
        self.assertIn("blocked=0", sql)
        self.assertIn("adult_confirmed=1", sql)
        self.assertIn("deleted_at IS NULL", sql)
        self.assertEqual(args, (12,))

    @patch("staff_archive.get_conn")
    def test_description_is_read_only_through_admin_detail(self, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        for admin in (False, True):
            member = {"id": 12, "venue_id": 1, "code": "012", "ended_at": None,
                      "blocked": False, "adult_confirmed": True}
            if admin:
                member.update(description="internal only", description_revision=2)
            cur.fetchone.return_value = member
            cur.fetchall.return_value = []
            result = staff_archive.detail({"staff_id": 12}, admin=admin)
            query = cur.execute.call_args_list[-2].args[0]
            self.assertIn("deleted_at IS NULL", query)
            self.assertEqual("description" in result, admin)
            self.assertEqual("description_revision" in query, admin)

    def payload(self, **changes):
        return {"staff_id": 12, "confirmed": True, "description": "  internal note  ",
                "description_revision": 2, **changes}

    @patch("staff_archive.get_conn")
    def test_description_save_trims_versions_and_audits_atomically(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"id": 12, "ended_at": None, "deleted_at": None,
                                    "description_revision": 2}
        result = staff_archive.update_archive(self.payload(), 99)
        self.assertEqual(result["description"], "internal note")
        self.assertEqual(result["description_revision"], 3)
        self.assertIn("FOR UPDATE", cur.execute.call_args_list[0].args[0])
        self.assertEqual(cur.execute.call_args_list[-1].args[1], (12, 99, "description_saved", 13))
        conn.commit.assert_called_once()

    @patch("staff_archive.get_conn")
    def test_archive_update_rejects_invalid_input_before_database(self, get_conn):
        for changes in ({"staff_id": True}, {"confirmed": False}, {"description": None},
                        {"description": "x" * 2001}, {"description_revision": True},
                        {"description_revision": -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                staff_archive.update_archive(self.payload(**changes), 99)
        get_conn.assert_not_called()

    @patch("staff_archive.get_conn")
    def test_stale_description_and_inactive_archives_are_rejected(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        for changes in ({"description_revision": 3}, {"deleted_at": datetime.now()},
                        {"ended_at": datetime.now()}):
            cur.fetchone.return_value = {
                "id": 12, "ended_at": None, "deleted_at": None, "description_revision": 2, **changes}
            with self.subTest(changes=changes), self.assertRaises(RecordConflict):
                staff_archive.update_archive(self.payload(), 99)
        conn.commit.assert_not_called()
        self.assertEqual(conn.rollback.call_count, 3)

    @patch("staff_archive.get_conn")
    def test_deletion_keeps_identity_photos_and_rosters_and_is_idempotent(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"id": 12, "ended_at": None, "deleted_at": None,
                                    "description_revision": 2}
        staff_archive.update_archive({"staff_id": 12, "confirmed": True}, 99, delete=True)
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any("SET deleted_at=" in query and "description=NULL" in query for query in queries))
        self.assertFalse(any("DELETE FROM" in query or "SET ended_at=" in query
                             or "staff_slots" in query or "staff_rosters" in query
                             or "staff_photos" in query for query in queries))
        self.assertTrue(any("staff_duty_subscriptions SET enabled=0" in query for query in queries))
        self.assertTrue(any("staff_customer_notifications SET status='cancelled'" in query for query in queries))
        self.assertEqual(cur.execute.call_args_list[-1].args[1], (12, 99, "deleted", 0))
        conn.commit.assert_called_once()
        conn.reset_mock()
        cur.fetchone.return_value = {"id": 12, "ended_at": None, "deleted_at": datetime.now()}
        staff_archive.update_archive({"staff_id": 12, "confirmed": True}, 99, delete=True)
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    @patch("staff_archive.get_conn")
    def test_audit_failure_rolls_back_description(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"id": 12, "ended_at": None, "deleted_at": None,
                                    "description_revision": 2}
        cur.execute.side_effect = [None, None, RuntimeError("audit unavailable")]
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            staff_archive.update_archive(self.payload(), 99)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_deleted_staff_is_not_eligible_for_customer_duty_notifications(self):
        from staff_customer_duty import eligible
        member = {"venue_id": 1, "ended_at": None, "blocked": False,
                  "employment": "", "authorized": True}
        self.assertTrue(eligible(member))
        self.assertFalse(eligible({**member, "deleted_at": datetime.now()}))

    @patch("staff_archive.sync", side_effect=RuntimeError("archive failed"))
    @patch("data_cleanup.get_conn")
    def test_cleanup_does_not_delete_sources_if_archiving_failed(self, get_conn, synchronize):
        from data_cleanup import cleanup_expired_data
        with self.assertRaisesRegex(RuntimeError, "archive failed"):
            cleanup_expired_data()
        get_conn.assert_not_called()
