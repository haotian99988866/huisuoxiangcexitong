from datetime import datetime
import unittest
from unittest.mock import Mock, patch

import staff_archive


class StaffArchiveTests(unittest.TestCase):
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
        self.assertEqual(args, (12,))

    @patch("staff_archive.sync", side_effect=RuntimeError("archive failed"))
    @patch("data_cleanup.get_conn")
    def test_cleanup_does_not_delete_sources_if_archiving_failed(self, get_conn, synchronize):
        from data_cleanup import cleanup_expired_data
        with self.assertRaisesRegex(RuntimeError, "archive failed"):
            cleanup_expired_data()
        get_conn.assert_not_called()
