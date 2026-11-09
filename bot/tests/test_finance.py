from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

import finance


class FinanceTests(unittest.TestCase):
    def test_money_and_month_validation(self):
        self.assertEqual(finance._money("0", "限额", allow_zero=True), Decimal("0.00"))
        self.assertEqual(finance._money("200.00", "限额", allow_zero=True), Decimal("200.00"))
        self.assertEqual(finance._money("20", "限额", allow_zero=True), Decimal("20.00"))
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

    def test_delivery_commission_uses_configured_source_default_when_service_name_differs(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            None,
            {"service_key": "delivery-order", "service_name": "外送订单",
             "commission_amount": Decimal("40.00")},
        ]
        result = finance.commission_item(cursor, 42, 8, "自由女神90分钟", "delivery")
        self.assertEqual(result["commission_amount"], Decimal("40.00"))
        self.assertEqual(cursor.execute.call_args_list[1].args[1], (42, 8, "delivery-order"))

    def test_apple_delivery_commission_uses_configured_fallback_for_unlisted_service(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            None,
            {"service_key": "small-6", "service_name": "小苹果·6牌",
             "commission_amount": Decimal("40.00")},
        ]
        result = finance.commission_item(cursor, 42, 10, "宝岛90分钟", "delivery")
        self.assertEqual(result["commission_amount"], Decimal("40.00"))
        self.assertEqual(cursor.execute.call_args_list[1].args[1], (42, 10, "small-6"))

    def test_unmatched_venue_service_does_not_use_delivery_commission_fallback(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        self.assertIsNone(finance.commission_item(cursor, 42, 8, "未配置会所项目", "venue"))
        cursor.execute.assert_called_once()

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

    def test_customer_commissions_are_added_to_daily_account_balance(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{
            "id": 7, "name": "日常账户", "account_type": "cash", "balance": Decimal("125.00"),
        }]
        accounts = finance._account_balances(cursor, 42)
        query, args = cursor.execute.call_args.args
        self.assertIn("crm_consumptions", query)
        self.assertIn("commission_owner_id=a.owner_id", query)
        self.assertIn("c.status='valid'", query)
        self.assertIn("commission_amount", query)
        self.assertIn("a.opening_balance", query)
        self.assertIn("t.kind='balance_add'", query)
        self.assertEqual(args, ("日常账户", 42))
        self.assertEqual(accounts[0]["balance"], Decimal("125.00"))

    def test_balance_add_transaction_increases_account_without_category(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        payload = {
            "request_id": "b" * 32, "kind": "balance_add", "happened_on": "2026-03-15",
            "amount": "250", "account_id": 7, "note": "补入现金",
        }
        with patch.object(finance, "get_conn", return_value=conn), \
                patch.object(finance, "_ensure_defaults"), \
                patch.object(finance, "_account", return_value={"id": 7}):
            result = finance.save_transaction(payload, 42)
        self.assertEqual(result["transaction_id"], "b" * 32)
        insert_call = next(call for call in cursor.execute.call_args_list
                           if "INSERT INTO finance_transactions" in call.args[0])
        self.assertEqual(insert_call.args[1][3:6], ("balance_add", Decimal("250.00"), Decimal("0")))
        self.assertIsNone(insert_call.args[1][8])
        conn.commit.assert_called_once()

    def test_existing_account_opening_balance_can_be_saved_without_change(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"id": 7}
        with patch.object(finance, "get_conn", return_value=conn):
            result = finance.set_account_opening_balance({
                "account_id": 7, "opening_balance": "500",
            }, 42)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertIn("owner_id=%s", cursor.execute.call_args_list[0].args[0])
        self.assertEqual(cursor.execute.call_args_list[1].args[1], (Decimal("500.00"), 7, 42))
        conn.commit.assert_called_once()

    def test_category_update_is_owner_scoped_and_changes_name_and_kind(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"id": 9, "kind": "expense", "name": "餐饮"}
        with patch.object(finance, "get_conn", return_value=conn):
            result = finance.update_category({
                "category_id": 9, "name": "食品", "kind": "expense",
            }, 42)
        self.assertEqual(result, {"ok": True})
        self.assertIn("owner_id=%s", cursor.execute.call_args_list[0].args[0])
        self.assertEqual(cursor.execute.call_args_list[1].args[1], ("食品", "expense", 9, 42))
        conn.commit.assert_called_once()

    def test_category_kind_cannot_change_when_transactions_or_budgets_reference_it(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            {"id": 9, "kind": "expense", "name": "餐饮"},
            {"n": 1},
        ]
        with patch.object(finance, "get_conn", return_value=conn):
            with self.assertRaisesRegex(ValueError, "不能更改收支类型"):
                finance.update_category({
                    "category_id": 9, "name": "餐饮", "kind": "income",
                }, 42)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_category_delete_is_soft_and_protects_interest_categories(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"id": 9, "kind": "expense", "name": "餐饮"}
        with patch.object(finance, "get_conn", return_value=conn):
            result = finance.delete_category({"category_id": 9}, 42)
        self.assertEqual(result, {"ok": True})
        self.assertIn("active=FALSE", cursor.execute.call_args_list[1].args[0])
        self.assertEqual(cursor.execute.call_args_list[1].args[1], (9, 42))
        conn.commit.assert_called_once()

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"id": 10, "kind": "expense", "name": "利息"}
        with patch.object(finance, "get_conn", return_value=conn):
            with self.assertRaisesRegex(ValueError, "不能删除"):
                finance.delete_category({"category_id": 10}, 42)
        conn.rollback.assert_called_once()

    def test_category_restore_is_owner_scoped(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"id": 9}
        with patch.object(finance, "get_conn", return_value=conn):
            result = finance.restore_category({"category_id": 9}, 42)
        self.assertEqual(result, {"ok": True})
        self.assertIn("owner_id=%s", cursor.execute.call_args_list[0].args[0])
        self.assertIn("active=TRUE", cursor.execute.call_args_list[1].args[0])
        conn.commit.assert_called_once()

    def test_commission_save_requires_complete_catalog(self):
        with patch.object(finance, "get_conn") as get_conn:
            with self.assertRaisesRegex(ValueError, "完整"):
                finance.save_commission_items({"items": []}, 42)
        get_conn.assert_not_called()

    def test_budget_save_updates_all_categories_in_one_transaction(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        items = [
            {"category_id": 3, "kind": "expense", "amount": "100"},
            {"category_id": 4, "kind": "income", "amount": "0"},
        ]
        with patch.object(finance, "get_conn", return_value=conn), \
                patch.object(finance, "_ensure_defaults"), \
                patch.object(finance, "_category") as category:
            result = finance.set_budget({"month": "2026-11", "items": items}, 42)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(category.call_args_list[0].args, (cursor, 3, "expense", 42))
        self.assertEqual(category.call_args_list[1].args, (cursor, 4, "income", 42))
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertIn("INSERT INTO finance_budgets", cursor.execute.call_args_list[0].args[0])
        self.assertIn("DELETE FROM finance_budgets", cursor.execute.call_args_list[1].args[0])
        conn.commit.assert_called_once()

    def test_budget_batch_rejects_invalid_items_before_opening_connection(self):
        with patch.object(finance, "get_conn") as get_conn:
            with self.assertRaisesRegex(ValueError, "预算项目格式无效"):
                finance.set_budget({"month": "2026-11", "items": [{"category_id": 3}]}, 42)
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
        budget_query = next(call.args[0] for call in cursor.execute.call_args_list
                            if "AS spent" in call.args[0])
        self.assertIn("c.kind IN ('income','expense')", budget_query)
        conn.commit.assert_called_once()

    def test_dashboard_net_worth_includes_account_balances_and_outstanding_debts(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [
            [],
            [],
            [{"id": 7, "name": "现金", "account_type": "cash",
              "opening_balance": Decimal("800.00"), "balance": Decimal("1000.00")}],
            [
                {"direction": "payable", "principal": Decimal("200.00"),
                 "outstanding": Decimal("200.00")},
                {"direction": "receivable", "principal": Decimal("500.00"),
                 "outstanding": Decimal("500.00")},
            ],
            [],
            [],
            [],
            [],
            [],
        ]
        cursor.fetchone.side_effect = [
            {"income": Decimal("0"), "expense": Decimal("0")},
            {"total": Decimal("0")},
        ]
        with patch.object(finance, "get_conn", return_value=conn), \
                patch.object(finance, "_ensure_defaults"):
            result = finance.dashboard({"month": "2026-03"}, 42)
        self.assertEqual(result["summary"]["net_worth"], "1300.00")
        transaction_query = next(query for query, _args in
                                 (call.args for call in cursor.execute.call_args_list)
                                 if "FROM finance_transactions t" in query)
        self.assertIn("t.status='valid' OR (t.kind='balance_add' AND t.status='void')",
                      transaction_query)
        goal_query = next(query for query, _args in
                          (call.args for call in cursor.execute.call_args_list)
                          if "FROM finance_goals g" in query)
        self.assertIn("a.opening_balance+COALESCE(SUM", goal_query)

    def test_dashboard_includes_linked_service_profiles_and_unmatched_commission_rows(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        cursor.fetchone.side_effect = [
            {"income": Decimal("0"), "expense": Decimal("0")},
            {"total": Decimal("0")},
        ]
        with patch.object(finance, "get_conn", return_value=conn), \
                patch.object(finance, "_ensure_defaults"):
            finance.dashboard({"month": "2026-03"}, 42)
        commission_query = next(
            query for query, _args in (call.args for call in cursor.execute.call_args_list)
            if "FROM crm_consumptions c JOIN venues v" in query
        )
        self.assertIn("LEFT JOIN staff_members m ON m.id=c.staff_id", commission_query)
        self.assertIn("m.code AS staff_code", commission_query)
        self.assertIn("LEFT JOIN delivery_people dp ON dp.id=c.delivery_person_id", commission_query)
        self.assertIn("c.delivery_person_id", commission_query)
        self.assertIn("dp.display_name", commission_query)
        self.assertIn("c.commission_amount>0 OR c.staff_id IS NOT NULL OR c.delivery_person_id IS NOT NULL",
                      commission_query)

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
