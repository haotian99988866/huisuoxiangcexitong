from decimal import Decimal
from datetime import datetime
import unittest
from unittest.mock import MagicMock, patch

import finance
import finance_bot


class FinanceBotTests(unittest.TestCase):
    def test_daily_summary_uses_local_day_and_includes_commission(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            {"income": Decimal("12.00"), "expense": Decimal("5.00")},
            {"total": Decimal("3.00")},
        ]
        cursor.fetchall.return_value = [{"name": "餐饮", "amount": Decimal("5.00")}]
        with patch.object(finance, "get_conn", return_value=conn), \
                patch.object(finance, "_account_balances", return_value=[
                    {"id": 1, "balance": Decimal("100.00")},
                ]), patch.object(finance, "_debts", return_value=[
                    {"direction": "payable", "outstanding": Decimal("20.00")},
                    {"direction": "receivable", "outstanding": Decimal("10.00")},
                ]):
            summary = finance.daily_summary({"day": "2026-03-15"}, 42)
        self.assertEqual(summary["income"], "15.00")
        self.assertEqual(summary["expense"], "5.00")
        self.assertEqual(summary["net"], "10.00")
        self.assertEqual(summary["net_worth"], "90.00")
        commission_query, commission_args = cursor.execute.call_args_list[1].args
        self.assertIn("crm_consumptions", commission_query)
        self.assertEqual(commission_args, (42, datetime(2026, 3, 14, 17),
                                           datetime(2026, 3, 15, 17)))

    def test_daily_report_is_compact_and_includes_top_expense_categories(self):
        text = finance_bot.format_daily_report({
            "day": "2026-03-15", "income": "15.00", "commission": "3.00",
            "expense": "5.00", "net": "10.00", "account_balance": "100.00",
            "net_worth": "90.00", "categories": [{"name": "餐饮", "amount": "5.00"}],
        })
        self.assertIn("03月15日", text)
        self.assertIn("餐饮 $5.00", text)
        self.assertIn("个人净资产 $90.00", text)

    def test_month_analysis_calls_out_budget_overrun(self):
        text = finance_bot.format_monthly_report({
            "month": "2026-03",
            "summary": {
                "income": "100.00", "commission": "0.00", "expense": "120.00",
                "net": "-20.00", "account_balance": "100.00", "receivable": "0.00",
                "payable": "0.00", "net_worth": "100.00",
            },
            "budgets": [{"kind": "expense", "name": "餐饮", "budget": "80.00",
                         "spent": "120.00"}],
        }, analysis=True, previous={"summary": {"income": "90.00", "expense": "100.00"}})
        self.assertIn("超预算 $40.00", text)
        self.assertIn("餐饮 $120.00", text)
        self.assertIn("较上月收入增加 $10.00，支出增加 $20.00", text)

    def test_previous_month_handles_year_boundary(self):
        self.assertEqual(finance_bot._previous_month("2026-01"), "2025-12")

    def test_bot_is_restricted_to_private_chats_from_admins(self):
        user = MagicMock(id=42)
        private_chat = MagicMock(type="private")
        group_chat = MagicMock(type="group")
        with patch.object(finance_bot, "admin_ids", return_value={42}):
            self.assertTrue(finance_bot._is_authorized(user, private_chat, 42))
            self.assertFalse(finance_bot._is_authorized(user, group_chat, 42))
            self.assertFalse(finance_bot._is_authorized(MagicMock(id=99), private_chat, 42))

    def test_income_and_expense_choices_use_app_dashboard_categories_and_ids(self):
        dashboard_categories = [
            {"id": 11, "kind": "expense", "name": "餐饮", "budget": "50.00"},
            {"id": 12, "kind": "income", "name": "其他收入", "budget": "0.00"},
            {"id": 13, "kind": "expense", "name": "交通", "budget": "30.00"},
        ]
        reference = {"categories": dashboard_categories}
        expense_options = finance_bot.selectable_categories(reference, "expense")
        income_options = finance_bot.selectable_categories(reference, "income")
        self.assertEqual([(row["id"], row["name"]) for row in expense_options],
                         [(11, "餐饮"), (13, "交通")])
        self.assertEqual([(row["id"], row["name"]) for row in income_options],
                         [(12, "其他收入")])
        self.assertEqual(
            sorted(row["id"] for row in expense_options + income_options),
            sorted(row["id"] for row in dashboard_categories),
        )

    def test_start_keyboard_opens_each_finance_web_view_directly(self):
        keyboard = finance_bot._main_keyboard()
        buttons = [button for row in keyboard.keyboard for button in row]
        self.assertEqual(
            [button.text for button in buttons],
            ["支出", "收入", "转账", "今日", "本月", "分类分析", "总览"],
        )
        self.assertEqual(
            [button.web_app.url for button in buttons],
            [
                f"{finance_bot.FINANCE_MINIAPP_URL}?view=entry&kind=expense",
                f"{finance_bot.FINANCE_MINIAPP_URL}?view=entry&kind=income",
                f"{finance_bot.FINANCE_MINIAPP_URL}?view=entry&kind=transfer",
                f"{finance_bot.FINANCE_MINIAPP_URL}?view=today",
                f"{finance_bot.FINANCE_MINIAPP_URL}?view=month",
                f"{finance_bot.FINANCE_MINIAPP_URL}?view=analysis",
                f"{finance_bot.FINANCE_MINIAPP_URL}?view=overview",
            ],
        )

    def test_bot_registers_menu_and_callback_handlers_without_network_access(self):
        app = finance_bot.create_bot("123456:local-test-token", 42)
        self.assertGreaterEqual(len(app.message_handlers), 6)
        self.assertEqual(len(app.callback_query_handlers), 1)


if __name__ == "__main__":
    unittest.main()
