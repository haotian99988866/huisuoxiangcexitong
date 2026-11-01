from decimal import Decimal
import unittest
from unittest.mock import Mock, patch

import premium_balance

ORDER_ID = "a" * 32


def order(status="paid"):
    return {
        "order_id": ORDER_ID, "payer_id": 123, "recipient_id": 456,
        "recipient_username": "receiver", "months": 3, "price_usd": Decimal("15.99"),
        "gift_stars": 1000, "status": status,
    }


class PremiumBalanceTests(unittest.TestCase):
    def setUp(self):
        self.bot = Mock()
        self.bot.get_my_star_balance.return_value.amount = 10000
        self.bot.get_chat.return_value = Mock(type="private", username="receiver")
        admins = patch("visit_stats.admin_ids", return_value=[99])
        admins.start()
        self.addCleanup(admins.stop)

    def test_fixed_prices_without_stars_procurement(self):
        self.assertEqual(
            {months: plan["price_usd"] for months, plan in premium_balance.PLANS.items()},
            {3: Decimal("15.99"), 6: Decimal("19.99"), 12: Decimal("33.99")})
        self.assertTrue(all("gift_stars" not in plan for plan in premium_balance.PLANS.values()))

    @patch("premium_balance.get_conn")
    def test_recent_ledger_combines_only_real_balance_movements_for_the_user(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [
            {"id": 1, "reference": ORDER_ID, "event": "purchase",
             "amount": Decimal("-15.990000"), "created_at": "2026-11-01 12:00:00"},
            {"id": 2, "reference": 7, "event": "deposit",
             "amount": Decimal("20.000000"), "created_at": "2026-11-01 11:00:00"},
        ]
        result = premium_balance.recent_ledger(123)
        query, values = cur.execute.call_args.args
        self.assertEqual(values, (123, 123))
        self.assertEqual(query.count("WHERE user_id=%s"), 2)
        self.assertIn("UNION ALL", query)
        self.assertIn("event IN ('purchase','refund')", query)
        self.assertIn("LIMIT 30", query)
        self.assertNotIn("member_test", query)
        self.assertNotIn("wallet_deposits", query)
        self.assertEqual(result[0]["amount"], "-15.990000")
        self.assertEqual(result[1]["reference"], "7")
        conn.commit.assert_not_called()
        conn.close.assert_called_once()

    @patch("premium_balance.get_conn")
    def test_recent_ledger_failure_is_not_reported_as_an_empty_history(self, get_conn):
        conn = get_conn.return_value
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("offline")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            premium_balance.recent_ledger(123)
        conn.close.assert_called_once()

    def test_normalize_username_accepts_optional_at_and_rejects_invalid_input(self):
        self.assertEqual(premium_balance.normalize_username("  @Some_User  "), "some_user")
        for username in (None, "user", "@", "bad name", "name!"):
            with self.subTest(username=username), self.assertRaises(ValueError):
                premium_balance.normalize_username(username)

    @patch("premium_balance.get_conn")
    def test_resolve_recipient_preserves_unique_known_user_lookup(self, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [{"tg_user_id": 456, "username": "Some_User"}]
        self.assertEqual(premium_balance.resolve_recipient("@Some_User"), (456, "some_user"))
        cur.execute.assert_called_once_with(
            "SELECT tg_user_id,username FROM bot_users WHERE LOWER(username)=%s "
            "ORDER BY last_active DESC LIMIT 2", ("some_user",))

    @patch("premium_balance.get_conn")
    def test_unknown_or_ambiguous_username_cannot_resolve_recipient(self, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        for rows, message in (
                ([], "先打开机器人"),
                ([{"tg_user_id": 456}, {"tg_user_id": 789}], "多个账户")):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, message):
                cur.fetchall.return_value = rows
                premium_balance.resolve_recipient("@receiver")

    def test_admin_notification_reuses_customer_notification(self):
        with patch("visit_stats.admin_ids", return_value=[11, 22]), \
                patch("premium_balance._notify_user") as notify:
            premium_balance._notify_admins(self.bot, "订单需要核实")
        self.assertEqual([call.args for call in notify.call_args_list],
                         [(self.bot, 11, "订单需要核实"), (self.bot, 22, "订单需要核实")])

    @patch("premium_balance.get_conn")
    def test_invalid_inputs_do_not_touch_wallet(self, get_conn):
        for payer, username, months, key in (
                (True, "@receiver", 3, ORDER_ID), (123, "bad!", 3, ORDER_ID),
                (123, "@receiver", True, ORDER_ID), (123, "@receiver", 9, ORDER_ID),
                (123, "@receiver", 3, "invalid")):
            with self.subTest(payer=payer, months=months), self.assertRaises(ValueError):
                premium_balance.create_order(self.bot, payer, username, months, key)
        get_conn.assert_not_called()

    @patch("premium_balance.get_order")
    def test_replay_does_not_check_stock_or_charge(self, get_order):
        get_order.return_value = order("delivered")
        result = premium_balance.create_order(self.bot, 123, "@receiver", 3, ORDER_ID)
        self.assertEqual(result["status"], "delivered")
        self.bot.get_my_star_balance.assert_not_called()
        self.bot.gift_premium_subscription.assert_not_called()

    @patch("premium_balance.get_order", return_value=order())
    def test_replay_cannot_change_package(self, get_order):
        with self.assertRaisesRegex(ValueError, "其他套餐"):
            premium_balance.create_order(self.bot, 123, "@receiver", 6, ORDER_ID)

    @patch("premium_balance.get_order", return_value=None)
    @patch("premium_balance.validate_recipient", return_value=(456, "receiver"))
    @patch("premium_balance.get_conn")
    def test_order_debit_and_ledger_share_one_transaction(self, get_conn, recipient, get_order):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [
            {"enabled": 1, "owner_id": 99}, None, {"balance": Decimal("20.00")},
        ]
        result = premium_balance.create_order(self.bot, 123, "@receiver", 3, ORDER_ID)
        self.assertEqual(result["price_usd"], "15.99")
        self.assertEqual(result["status"], "manual_pending")
        calls = cur.execute.call_args_list
        self.assertIn("FOR UPDATE", calls[0].args[0])
        self.assertIn("FOR UPDATE", calls[2].args[0])
        self.assertEqual(calls[3].args[1][-1], 99)
        self.assertEqual(calls[4].args[1], (Decimal("15.99"), 123))
        self.assertEqual(calls[5].args[1], (ORDER_ID, 123, Decimal("-15.99")))
        self.assertEqual(Decimal("20.00") - calls[4].args[1][0], Decimal("4.01"))
        conn.commit.assert_called_once()
        self.bot.gift_premium_subscription.assert_not_called()
        self.bot.get_my_star_balance.assert_not_called()

    @patch("premium_balance.get_order", return_value=None)
    @patch("premium_balance.validate_recipient", return_value=(456, "receiver"))
    @patch("premium_balance.get_conn")
    def test_insufficient_wallet_missing_owner_and_disabled_sales_never_debit(self, get_conn, recipient, get_order):
        for rows, message in (
                ([{"enabled": 0}, None], "暂未开放"),
                ([{"enabled": 1, "owner_id": None}, None], "负责人不可用"),
                ([{"enabled": 1, "owner_id": 99}, None, {"balance": Decimal("15.98")}], "余额不足")):
            with self.subTest(message=message):
                conn = get_conn.return_value
                conn.reset_mock()
                cur = conn.cursor.return_value.__enter__.return_value
                cur.fetchone.side_effect = rows
                with self.assertRaisesRegex(ValueError, message):
                    premium_balance.create_order(self.bot, 123, "@receiver", 3, ORDER_ID)
                self.assertFalse(any("UPDATE wallet_accounts" in call.args[0] for call in cur.execute.call_args_list))
                conn.commit.assert_not_called()
                conn.rollback.assert_called_once()

    @patch("premium_balance.get_order", return_value=None)
    @patch("premium_balance.validate_recipient", return_value=(456, "receiver"))
    @patch("premium_balance.get_conn")
    def test_ledger_failure_rolls_back_order_and_debit(self, get_conn, recipient, get_order):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"enabled": 1, "owner_id": 99}, None, {"balance": Decimal("20")}]
        cur.execute.side_effect = [None] * 5 + [RuntimeError("ledger failed")]
        with self.assertRaises(RuntimeError):
            premium_balance.create_order(self.bot, 123, "@receiver", 3, ORDER_ID)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    @patch("premium_balance.get_order", return_value=None)
    @patch("premium_balance.validate_recipient", return_value=(456, "receiver"))
    @patch("premium_balance.get_conn")
    def test_concurrent_replay_returns_existing_order_under_lock(self, get_conn, recipient, get_order):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"enabled": 0}, order("paid")]
        result = premium_balance.create_order(self.bot, 123, "@receiver", 3, ORDER_ID)
        self.assertEqual(result["order_id"], ORDER_ID)
        self.assertEqual(cur.execute.call_count, 2)
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    @patch("premium_balance.resolve_recipient", return_value=(456, "receiver"))
    def test_cached_username_is_checked_against_official_chat(self, resolve):
        self.bot.get_chat.return_value.username = "different"
        with self.assertRaisesRegex(ValueError, "记录已变化"):
            premium_balance.validate_recipient(self.bot, "@receiver")
        self.bot.get_chat.assert_called_once_with(456)

    @patch("premium_balance.get_conn")
    def test_refund_amount_matches_original_order_not_current_price(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = order("refund_pending")
        premium_balance.refund_order(ORDER_ID)
        self.assertEqual(cur.execute.call_args_list[1].args[1], (123, Decimal("15.99")))
        self.assertEqual(cur.execute.call_args_list[2].args[1], (ORDER_ID, 123, Decimal("15.99"), None))
        conn.commit.assert_called_once()

    @patch("premium_balance.get_conn")
    def test_duplicate_refund_does_not_credit_again(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = order("refunded")
        premium_balance.refund_order(ORDER_ID)
        self.assertEqual(cur.execute.call_count, 1)
        conn.commit.assert_not_called()

    @patch("premium_balance.get_conn")
    def test_unknown_delivery_requires_admin_confirmation_and_live_delivery_cannot_refund(self, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        for status, actor, confirmed in (
                ("delivery_unknown", None, False), ("delivery_unknown", 99, False),
                ("delivering", 99, True), ("delivered", 99, True), ("paid", 99, True)):
            with self.subTest(status=status), self.assertRaises(ValueError):
                cur.fetchone.return_value = order(status)
                premium_balance.refund_order(ORDER_ID, actor, confirmed)

    @patch("premium_balance.get_conn")
    def test_refund_ledger_failure_rolls_back_credit(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = order("refund_pending")
        cur.execute.side_effect = [None, None, RuntimeError("ledger failed")]
        with self.assertRaises(RuntimeError):
            premium_balance.refund_order(ORDER_ID)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    @patch("premium_balance.get_conn")
    def test_admin_confirmed_unknown_order_refund_is_audited(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = order("delivery_unknown")
        premium_balance.refund_order(ORDER_ID, 99, confirmed_not_delivered=True)
        self.assertEqual(cur.execute.call_args_list[2].args[1][-1], 99)
        conn.commit.assert_called_once()

    @patch("premium_balance.get_conn")
    def test_manual_completion_requires_owner_and_is_audited_without_funds(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {**order("manual_pending"), "owner_id": 99}
        premium_balance.confirm_delivery(ORDER_ID, 99)
        self.assertEqual(cur.execute.call_args_list[1].args[1], (99, ORDER_ID))
        self.assertIn("'confirmed',0", cur.execute.call_args_list[1].args[0])
        self.assertEqual(cur.execute.call_count, 3)
        conn.commit.assert_called_once()

    @patch("premium_balance.get_conn")
    def test_other_admin_cannot_complete_or_refund_manual_order(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {**order("manual_pending"), "owner_id": 99}
        with patch("visit_stats.admin_ids", return_value=[99, 88]):
            for action in (lambda: premium_balance.confirm_delivery(ORDER_ID, 88),
                           lambda: premium_balance.refund_order(ORDER_ID, 88, True)):
                cur.reset_mock()
                with self.assertRaisesRegex(ValueError, "负责人"):
                    action()
                self.assertEqual(cur.execute.call_count, 1)
        conn.commit.assert_not_called()

    @patch("premium_balance.get_conn")
    def test_manual_refund_requires_not_opened_confirmation(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {**order("manual_pending"), "owner_id": 99}
        with self.assertRaises(ValueError):
            premium_balance.refund_order(ORDER_ID, 99)
        cur.reset_mock()
        premium_balance.refund_order(ORDER_ID, 99, True)
        self.assertEqual(cur.execute.call_args_list[1].args[1], (123, Decimal("15.99")))
        self.assertEqual(cur.execute.call_args_list[2].args[1][-1], 99)
        conn.commit.assert_called_once()

    @patch("premium_balance.get_conn")
    def test_completed_manual_order_cannot_refund_or_complete_again(self, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        for status in ("delivered", "refunded"):
            cur.fetchone.return_value = {**order(status), "owner_id": 99}
            with self.subTest(status=status), self.assertRaises(ValueError):
                premium_balance.confirm_delivery(ORDER_ID, 99)
        cur.fetchone.return_value = {**order("delivered"), "owner_id": 99}
        with self.assertRaises(ValueError):
            premium_balance.refund_order(ORDER_ID, 99, True)

    @patch("premium_balance.get_conn")
    def test_notification_sends_only_owner_and_stores_message_id(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {**order("manual_pending"), "owner_id": 99, "notification_id": None}
        self.bot.send_message.return_value.message_id = 7
        premium_balance.notify_manual_order(self.bot, ORDER_ID)
        args = self.bot.send_message.call_args
        self.assertEqual(args.args[0], 99)
        self.assertIn("@receiver", args.args[1])
        self.assertIn("456", args.args[1])
        self.assertEqual(args.kwargs["timeout"], 10)
        self.assertEqual(cur.execute.call_args.args[1], (7, ORDER_ID))
        self.bot.gift_premium_subscription.assert_not_called()
        self.bot.get_my_star_balance.assert_not_called()
        conn.commit.assert_called_once()

    @patch("premium_balance.get_conn")
    def test_failed_notification_rolls_back_for_worker_retry_without_refund(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {**order("manual_pending"), "owner_id": 99, "notification_id": None}
        self.bot.send_message.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            premium_balance.notify_manual_order(self.bot, ORDER_ID)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        self.assertEqual(cur.execute.call_count, 1)

    @patch("premium_balance.get_conn")
    def test_worker_only_notifies_manual_orders_and_recovers_legacy_refunds(self, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [
            {"order_id": ORDER_ID, "status": "manual_pending"},
            {"order_id": "c" * 32, "status": "refund_pending"},
        ]
        with patch("premium_balance.notify_manual_order") as notify, patch("premium_balance.refund_order") as refund:
            premium_balance.process_orders(self.bot)
        notify.assert_called_once_with(self.bot, ORDER_ID)
        refund.assert_called_once_with("c" * 32)
        self.bot.gift_premium_subscription.assert_not_called()
        self.assertIn("notification_id IS NULL", cur.execute.call_args.args[0])
