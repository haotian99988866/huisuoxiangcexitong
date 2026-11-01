from decimal import Decimal
import unittest
from unittest.mock import patch

import member_simulation
from tests import test_member_accounts


class MemberSimulationTests(unittest.TestCase):
    @patch("member_simulation.get_conn")
    def test_all_results_are_isolated_from_production_and_use_server_price(self, get_conn):
        for scenario, debit, status in (
                ("success", Decimal("15.99"), "simulated_success"),
                ("failure", Decimal("0"), "simulated_refunded"),
                ("unknown", Decimal("15.99"), "simulated_unknown")):
            with self.subTest(scenario=scenario):
                conn = get_conn.return_value
                conn.reset_mock()
                cur = conn.cursor.return_value.__enter__.return_value
                cur.fetchone.side_effect = [{"balance": Decimal("50")}, None]
                result = member_simulation.purchase(123, "a" * 32, "@test_user", 3, scenario)
                self.assertEqual(result["status"], status)
                self.assertTrue(result["simulation"])
                self.assertEqual(cur.execute.call_args_list[3].args[1], (debit, 123))
                queries = [call.args[0] for call in cur.execute.call_args_list]
                self.assertFalse(any("wallet_accounts" in sql or "member_orders" in sql for sql in queries))
                conn.commit.assert_called_once()

    @patch("member_simulation.get_conn")
    def test_duplicate_order_never_debits_twice(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [
            {"balance": Decimal("34.01")},
            {"user_id": 123, "username": "test_user", "months": 3,
             "scenario": "success", "status": "simulated_success"},
        ]
        member_simulation.purchase(123, "a" * 32, "@test_user", 3, "success")
        self.assertEqual(cur.execute.call_count, 3)
        conn.commit.assert_not_called()

    @patch("member_simulation.get_conn")
    def test_insufficient_test_balance_cannot_fall_back_to_real_money(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"balance": Decimal("1")}, None]
        with self.assertRaisesRegex(ValueError, "不会动用正式余额"):
            member_simulation.purchase(123, "a" * 32, "@test_user", 3, "success")
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    @patch("member_simulation.get_conn")
    def test_order_write_failure_rolls_back_simulated_debit(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"balance": Decimal("50")}, None]
        cur.execute.side_effect = [None] * 4 + [RuntimeError("failed")]
        with self.assertRaises(RuntimeError):
            member_simulation.purchase(123, "a" * 32, "@test_user", 3, "success")
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()


class MemberSimulationApiTests(unittest.TestCase):
    setUp = test_member_accounts.MemberApiTests.setUp
    post = test_member_accounts.MemberApiTests.post

    def test_non_admin_cannot_access_simulation(self):
        with patch("member_accounts.authenticate", return_value=999), \
                patch("visit_stats.admin_ids", return_value=[123]), \
                patch("member_simulation.account") as account, \
                patch("member_simulation.purchase") as purchase:
            for action in ("member_test_account", "member_test_purchase"):
                self.assertEqual(self.post(action).status_code, 403)
            account.assert_not_called()
            purchase.assert_not_called()

    def test_admin_simulation_never_constructs_telegram_bot(self):
        with patch("member_accounts.authenticate", return_value=123), \
                patch("visit_stats.admin_ids", return_value=[123]), \
                patch("member_simulation.purchase", return_value={"simulation": True}) as purchase, \
                patch("telebot.TeleBot") as bot:
            response = self.post("member_test_purchase", order_id="a" * 32,
                                 recipient_username="@test_user", months=3, scenario="success")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["simulation"])
        purchase.assert_called_once_with(123, "a" * 32, "@test_user", 3, "success")
        bot.assert_not_called()
