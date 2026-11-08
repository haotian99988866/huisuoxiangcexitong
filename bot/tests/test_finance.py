from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

import finance


class FinanceTests(unittest.TestCase):
    def test_money_and_month_validation(self):
        self.assertEqual(finance._money("0", "限额", allow_zero=True), Decimal("0.00"))
        self.assertEqual(finance._money("12.34", "金额"), Decimal("12.34"))
        for value in ("-1", "1.001", "NaN", "1e2", 12.34):
            with self.assertRaises(ValueError):
                finance._money(value, "金额")
        for value in ("2026-13", "2026-00", "26-01", None):
            with self.assertRaises(ValueError):
                finance._month(value)

    def test_refund_deducts_fixed_commission_and_floors_at_zero(self):
        self.assertEqual(
            finance.commission_after_refund(Decimal("40.00"), Decimal("12.50")),
            Decimal("27.50"),
        )
        self.assertEqual(
            finance.commission_after_refund(Decimal("10.00"), Decimal("20.00")),
            Decimal("0.00"),
        )

    def test_commission_month_uses_local_month_boundaries(self):
        start, end = finance._month_utc_bounds("2026-03")
        self.assertEqual(start.isoformat(sep=" "), "2026-02-28 17:00:00")
        self.assertEqual(end.isoformat(sep=" "), "2026-03-31 17:00:00")

    def test_commission_defaults_match_provided_project_items(self):
        items = {(venue_id, service_key): Decimal(amount)
                 for venue_id, service_key, _name, amount, _price in finance.COMMISSION_CATALOG}
        self.assertEqual(items[(1, "tcm-treatment")], Decimal("10.00"))
        self.assertEqual(items[(2, "board-9-half")], Decimal("35.00"))
        self.assertEqual(items[(2, "mingxi-half")], Decimal("0.00"))
        self.assertEqual(items[(4, "japanese-korean")], Decimal("90.00"))
        self.assertEqual(items[(6, "russian")], Decimal("70.00"))
        self.assertEqual(items[(6, "korean")], Decimal("0.00"))
        self.assertEqual(items[(10, "red-t")], Decimal("40.00"))

    def test_account_and_category_lookups_are_owner_scoped(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            {"id": 7, "name": "现金", "account_type": "cash"},
            {"id": 9, "name": "餐饮"},
        ]
        self.assertEqual(finance._account(cursor, 7, 42)["id"], 7)
        self.assertEqual(finance._category(cursor, 9, "expense", 42)["id"], 9)
        self.assertIn("owner_id=%s", cursor.execute.call_args_list[0].args[0])
        self.assertEqual(cursor.execute.call_args_list[0].args[1], (7, 42))
        self.assertIn("owner_id=%s", cursor.execute.call_args_list[1].args[0])
        self.assertEqual(cursor.execute.call_args_list[1].args[1], (9, "expense", 42))

    def test_commission_save_requires_complete_catalog(self):
        with patch.object(finance, "get_conn") as get_conn:
            with self.assertRaisesRegex(ValueError, "完整"):
                finance.save_commission_items({"items": []}, 42)
        get_conn.assert_not_called()

    def test_commission_save_updates_each_item_for_current_owner(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        items = [{
            "venue_id": venue_id, "service_key": service_key,
            "commission_amount": amount,
        } for venue_id, service_key, _name, amount, _price in finance.COMMISSION_CATALOG]
        with patch.object(finance, "get_conn", return_value=conn), \
                patch.object(finance, "_ensure_defaults"):
            result = finance.save_commission_items({"items": items}, 42)
        self.assertTrue(result["ok"])
        self.assertEqual(cursor.execute.call_count, len(items))
        query, args = cursor.execute.call_args.args
        self.assertIn("owner_id=%s", query)
        self.assertEqual(args[3:6], (42, items[-1]["venue_id"], items[-1]["service_key"]))
        conn.commit.assert_called_once()

    def test_long_goal_name_fits_generated_savings_account_name(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        cursor.lastrowid = 17
        goal_id = "a" * 32
        with patch.object(finance, "get_conn", return_value=conn), \
                patch.object(finance, "_ensure_defaults"):
            result = finance.add_goal({
                "request_id": goal_id,
                "name": "目标" * 40,
                "target": "1000",
                "target_date": "",
            }, 42)
        account_name = cursor.execute.call_args_list[1].args[1][1]
        self.assertLessEqual(len(account_name), 80)
        self.assertTrue(account_name.endswith(goal_id))
        self.assertEqual(result, {"ok": True})
        conn.commit.assert_called_once()

    def test_dashboard_commits_default_rows_before_returning_their_ids(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        cursor.fetchone.side_effect = [
            {"income": Decimal("0"), "expense": Decimal("0")},
            {"total": Decimal("0")},
        ]
        with patch.object(finance, "get_conn", return_value=conn), \
                patch.object(finance, "_ensure_defaults"):
            result = finance.dashboard({"month": "2026-03"}, 42)
        self.assertEqual(result["summary"]["income"], "0.00")
        conn.commit.assert_called_once()

    def test_expense_transaction_is_saved_with_owner_and_idempotency_hash(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        payload = {
            "request_id": "a" * 32, "kind": "expense", "happened_on": "2026-03-15",
            "amount": "4.20", "category_id": 9, "account_id": 7, "note": "",
        }
        with patch.object(finance, "get_conn", return_value=conn), \
                patch.object(finance, "_ensure_defaults"), \
                patch.object(finance, "_category", return_value={"id": 9}), \
                patch.object(finance, "_account", return_value={"id": 7}):
            result = finance.save_transaction(payload, 42)
        self.assertEqual(result["transaction_id"], "a" * 32)
        query, args = cursor.execute.call_args.args
        self.assertIn("request_hash", query)
        self.assertEqual(args[:2], ("a" * 32, 42))
        self.assertEqual(args[3], "expense")
        self.assertEqual(args[4], Decimal("4.20"))
        self.assertEqual(len(args), 15)
        conn.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
