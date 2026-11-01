from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

import customer_records as records


class CustomerRecordTests(unittest.TestCase):
    def test_amounts_preserve_exact_cents(self):
        self.assertEqual(records._money("129.99", "实付"), Decimal("129.99"))
        self.assertEqual(records._money("0", "退款"), Decimal("0.00"))
        for value in ("-1", "1.001", "NaN", "1e2", "99999999999.99", 129.99):
            with self.assertRaises(ValueError):
                records._money(value, "金额")

    def test_consumption_time_requires_zone_and_past(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertEqual(records._occurred(past.isoformat()).tzinfo, None)
        for value in ("2026-01-01T12:00:00", "invalid",
                      (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()):
            with self.assertRaises(ValueError):
                records._occurred(value)

    def test_customer_identity_and_paging_validation(self):
        for value in (0, -1, True, "123", 9007199254740992):
            with self.assertRaises(ValueError):
                records._positive_id(value, "客户")
        for value in (0, True, "1", 10001):
            with self.assertRaises(ValueError):
                records._page(value)

    def test_customer_view_never_returns_internal_profile_notes(self):
        conn = MagicMock()
        row = {key: "value" for key in (
            "id", "venue_name", "staff_code", "service", "occurred_at", "amount", "refunded", "status")}
        row.update({"note": "internal", "user_id": 42, "revision": 2})
        with patch.object(records, "get_conn", return_value=conn), \
                patch.object(records, "_summary", return_value={"visits": 1}), \
                patch.object(records, "_records", return_value=([row], 1)):
            result = records.mine(42, {"page": 1})
        self.assertNotIn("note", result["records"][0])
        self.assertNotIn("user_id", result["records"][0])
        self.assertNotIn("preferences", result)
        self.assertNotIn("staff_history", result)
        conn.close.assert_called_once()

    def test_every_write_requires_explicit_confirmation(self):
        with self.assertRaises(ValueError):
            records.save({"action": "admin_customer_save", "user_id": 42,
                          "request_id": "a" * 32, "revision": 0}, 1)

    def test_replayed_operation_must_match_actor_and_payload(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"request_hash": "different", "actor_id": 1}
        with patch.object(records, "get_conn", return_value=conn), \
                patch.object(records, "_identity", return_value={"user_id": 42}):
            with self.assertRaises(records.RecordConflict):
                records.save({"action": "admin_customer_save", "user_id": 42,
                              "request_id": "a" * 32, "revision": 0, "confirmed": True}, 1)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_staff_history_uses_valid_consumption_and_stable_ids(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"total": 25}
        cursor.fetchall.return_value = []
        rows, total = records._staff_history(cursor, 42, page=2)
        self.assertEqual((rows, total), ([], 25))
        query, args = cursor.execute.call_args.args
        self.assertIn("c.status='valid'", query)
        self.assertIn("GROUP BY c.staff_id", query)
        self.assertIn("SUM(c.amount-c.refunded)", query)
        self.assertEqual(args, (42, 20, 20))
