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
            "id", "service_kind", "venue_name", "staff_code", "delivery_person_name",
            "service", "occurred_at", "amount", "refunded", "status")}
        row.update({"note": "internal", "user_id": 42, "revision": 2})
        with patch.object(records, "get_conn", return_value=conn), \
                patch.object(records, "_summary", return_value={"visits": 1}), \
                patch.object(records, "_records", return_value=([row], 1)):
            result = records.mine(42, {"page": 1})
        self.assertNotIn("note", result["records"][0])
        self.assertNotIn("user_id", result["records"][0])
        self.assertIn("service_kind", result["records"][0])
        self.assertIn("delivery_person_name", result["records"][0])
        self.assertNotIn("preferences", result)
        self.assertNotIn("staff_history", result)
        conn.close.assert_called_once()

    def test_consumption_rows_include_delivery_person_and_external_label(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"n": 1}
        cursor.fetchall.return_value = []
        rows, total = records._records(cursor, 42, page=1)
        self.assertEqual((rows, total), ([], 1))
        query, args = cursor.execute.call_args.args
        self.assertIn("LEFT JOIN venues", query)
        self.assertIn("LEFT JOIN delivery_people", query)
        self.assertIn("西港外送", query)
        self.assertEqual(args, (42, 0))

    def test_internal_customer_id_is_not_a_telegram_id(self):
        self.assertEqual(records._customer_id(-123), -123)
        self.assertEqual(records._customer_id(123), 123)
        for value in (0, True, "123", -9007199254740992):
            with self.assertRaises(ValueError):
                records._customer_id(value)
        with self.assertRaises(ValueError):
            records._positive_id(-123, "Telegram ID")

    def test_verified_account_resolves_bound_offline_consumption(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"user_id": -123}
        with patch.object(records, "get_conn", return_value=conn), \
                patch.object(records, "_summary", return_value={}) as summary, \
                patch.object(records, "_records", return_value=([], 0)) as listing:
            records.mine(42, {"page": 1})
        summary.assert_called_once_with(cursor, -123)
        listing.assert_called_once_with(cursor, -123, 1)
        self.assertEqual(cursor.execute.call_args.args[1], (42,))

    def test_offline_identity_does_not_lookup_fake_telegram_account(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"user_id": -123, "telegram_user_id": None, "alias": "Customer"}
        identity = records._identity(cursor, -123)
        self.assertIsNone(identity["telegram_user_id"])
        self.assertEqual(identity["user_id"], -123)
        cursor.execute.assert_called_once()

    def test_create_retry_returns_original_customer_key(self):
        import hashlib
        import json
        payload = {"action": "admin_customer_create", "alias": "Customer",
                   "request_id": "a" * 32, "revision": 0, "confirmed": True}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        payload["init_data"] = "renewed-authentication"
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"request_hash": digest, "actor_id": 1, "user_id": -123, "record_id": None}
        with patch.object(records, "get_conn", return_value=conn):
            result = records.save(payload, 1)
        self.assertEqual(result["user_id"], -123)
        self.assertTrue(result["replayed"])
        conn.commit.assert_not_called()

    def test_telegram_entry_preserves_bound_internal_customer_key(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            {"user_id": -123, "telegram_user_id": 42, "alias": "Customer"},
            {"user_id": 42, "username": "customer", "display_name": "Customer"},
        ]
        identity = records._identity(cursor, 42)
        self.assertEqual(identity["user_id"], -123)
        self.assertEqual(identity["telegram_user_id"], 42)

    def test_bind_refuses_an_account_with_another_profile(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            None, {"user_id": -123, "telegram_user_id": None, "revision": 1}, {"user_id": 42},
        ]
        with patch.object(records, "get_conn", return_value=conn), \
                patch.object(records, "_identity", return_value={"user_id": -123}):
            with self.assertRaises(records.RecordConflict):
                records.save({"action": "admin_customer_bind", "user_id": -123, "telegram_user_id": 42,
                              "revision": 1, "reason": "Verified identity", "request_id": "a" * 32, "confirmed": True}, 1)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

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
        self.assertIn("GROUP BY c.delivery_person_id", query)
        self.assertIn("SUM(c.amount-c.refunded)", query)
        self.assertEqual(args, (42, 42, 20, 20))

    def test_consumption_source_keeps_venue_and_delivery_people_separate(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"id": 5}
        self.assertEqual(records._consumption_source(cursor, {
            "service_kind": "venue", "venue_id": 2, "staff_id": 5,
        }), ("venue", 2, 5, None))
        cursor.reset_mock()
        cursor.fetchone.side_effect = [{"id": 7}]
        self.assertEqual(records._consumption_source(cursor, {
            "service_kind": "delivery", "venue_id": 7, "delivery_person_id": "",
        }), ("delivery", 7, None, None))
        cursor.reset_mock()
        cursor.fetchone.side_effect = [{"id": 8}, {"id": 17}]
        self.assertEqual(records._consumption_source(cursor, {
            "service_kind": "delivery", "venue_id": 8, "delivery_person_id": 17,
        }), ("delivery", 8, None, 17))
        with self.assertRaises(ValueError):
            records._consumption_source(cursor, {
                "service_kind": "delivery", "venue_id": 7, "staff_id": 5,
            })

    def test_delivery_source_list_is_independent_of_commission_catalog(self):
        cursor = MagicMock()
        sources = [{"id": venue_id, "name": f"外送 {venue_id}"} for venue_id in (7, 8, 9, 10)]
        cursor.fetchall.return_value = sources
        self.assertEqual(records._delivery_sources(cursor), sources)
        (query,) = cursor.execute.call_args.args
        self.assertIn("status='active'", query)

    def test_delivery_consumption_source_requires_an_active_delivery_profile(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [{"id": 7}, None]
        with self.assertRaisesRegex(ValueError, "有效的外送人员档案"):
            records._consumption_source(cursor, {
                "service_kind": "delivery", "venue_id": 7, "delivery_person_id": 17,
            })
