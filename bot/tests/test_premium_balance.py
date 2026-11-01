from decimal import Decimal
import unittest
from unittest.mock import Mock, patch

from telebot import apihelper
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

    def test_fixed_prices_and_official_procurement_cost(self):
        self.assertEqual(
            {months: plan["price_usd"] for months, plan in premium_balance.PLANS.items()},
            {3: Decimal("15.99"), 6: Decimal("19.99"), 12: Decimal("33.99")})
        self.assertEqual(
            {months: plan["gift_stars"] for months, plan in premium_balance.PLANS.items()},
            {3: 1000, 6: 1500, 12: 2500})

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
            {"enabled": 1}, None, {"reserved": 0}, {"balance": Decimal("20.00")},
        ]
        result = premium_balance.create_order(self.bot, 123, "@receiver", 3, ORDER_ID)
        self.assertEqual(result["price_usd"], "15.99")
        self.assertEqual(result["status"], "paid")
        calls = cur.execute.call_args_list
        self.assertIn("FOR UPDATE", calls[0].args[0])
        self.assertIn("FOR UPDATE", calls[3].args[0])
        self.assertEqual(calls[5].args[1], (Decimal("15.99"), 123))
        self.assertEqual(calls[6].args[1], (ORDER_ID, 123, Decimal("-15.99")))
        self.assertEqual(Decimal("20.00") - calls[5].args[1][0], Decimal("4.01"))
        conn.commit.assert_called_once()
        self.bot.gift_premium_subscription.assert_not_called()

    @patch("premium_balance.get_order", return_value=None)
    @patch("premium_balance.validate_recipient", return_value=(456, "receiver"))
    @patch("premium_balance.get_conn")
    def test_insufficient_wallet_stock_and_disabled_sales_never_debit(self, get_conn, recipient, get_order):
        for rows, message in (
                ([{"enabled": 0}, None], "暂未开放"),
                ([{"enabled": 1}, None, {"reserved": 9500}], "库存已被"),
                ([{"enabled": 1}, None, {"reserved": 0}, {"balance": Decimal("15.98")}], "余额不足")):
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
        cur.fetchone.side_effect = [{"enabled": 1}, None, {"reserved": 0}, {"balance": Decimal("20")}]
        cur.execute.side_effect = [None] * 6 + [RuntimeError("ledger failed")]
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

    @patch("premium_balance.begin_delivery", return_value=None)
    def test_already_claimed_order_is_not_gifted_twice(self, begin):
        premium_balance.deliver_order(self.bot, ORDER_ID)
        self.bot.gift_premium_subscription.assert_not_called()

    def test_official_api_success_uses_numeric_id_and_procurement_stars(self):
        self.bot.gift_premium_subscription.return_value = True
        with patch("premium_balance.begin_delivery", return_value=order()), \
                patch("premium_balance.finish_order") as finish, \
                patch("premium_balance._notify_user"):
            premium_balance.deliver_order(self.bot, ORDER_ID)
        kwargs = self.bot.gift_premium_subscription.call_args.kwargs
        self.assertEqual((kwargs["user_id"], kwargs["month_count"], kwargs["star_count"]), (456, 3, 1000))
        finish.assert_called_once_with(ORDER_ID, "delivered")

    def test_username_change_prevents_wrong_recipient_and_returns_balance(self):
        self.bot.get_chat.return_value.username = "changed_name"
        with patch("premium_balance.begin_delivery", return_value=order()), \
                patch("premium_balance.finish_order") as finish, \
                patch("premium_balance.refund_order") as refund, \
                patch("premium_balance._notify_user"), patch("premium_balance._notify_admins"):
            premium_balance.deliver_order(self.bot, ORDER_ID)
        self.bot.gift_premium_subscription.assert_not_called()
        finish.assert_called_once_with(ORDER_ID, "refund_pending", "RecipientChanged")
        refund.assert_called_once_with(ORDER_ID)

    def test_network_or_server_failure_does_not_retry_or_refund(self):
        failures = [
            RuntimeError("lost response"),
            apihelper.ApiTelegramException("giftPremiumSubscription", None,
                                           {"error_code": 500, "description": "Server error"}),
        ]
        for failure in failures:
            with self.subTest(failure=failure), \
                    patch("premium_balance.begin_delivery", return_value=order()), \
                    patch("premium_balance.finish_order") as finish, \
                    patch("premium_balance.refund_order") as refund, \
                    patch("premium_balance._notify_user"), patch("premium_balance._notify_admins"):
                self.bot.gift_premium_subscription.reset_mock()
                self.bot.gift_premium_subscription.side_effect = failure
                premium_balance.deliver_order(self.bot, ORDER_ID)
                self.assertEqual(finish.call_args.args[1], "delivery_unknown")
                refund.assert_not_called()
                self.bot.gift_premium_subscription.assert_called_once()

    def test_explicit_official_rejection_returns_balance(self):
        self.bot.gift_premium_subscription.side_effect = apihelper.ApiTelegramException(
            "giftPremiumSubscription", None, {"error_code": 400, "description": "Rejected"})
        with patch("premium_balance.begin_delivery", return_value=order()), \
                patch("premium_balance.finish_order") as finish, \
                patch("premium_balance.refund_order") as refund, patch("premium_balance._notify_user"):
            premium_balance.deliver_order(self.bot, ORDER_ID)
        finish.assert_called_once_with(ORDER_ID, "refund_pending", "GiftRejected")
        refund.assert_called_once_with(ORDER_ID)

    def test_unexpected_success_response_is_not_a_false_completion(self):
        self.bot.gift_premium_subscription.return_value = None
        with patch("premium_balance.begin_delivery", return_value=order()), \
                patch("premium_balance.finish_order") as finish, \
                patch("premium_balance.refund_order") as refund, patch("premium_balance._notify_admins"):
            premium_balance.deliver_order(self.bot, ORDER_ID)
        finish.assert_called_once_with(ORDER_ID, "delivery_unknown", "UnexpectedResponse")
        refund.assert_not_called()

    def test_stock_depletion_does_not_invoke_gift_api(self):
        self.bot.get_my_star_balance.return_value.amount = 999
        with patch("premium_balance.begin_delivery", return_value=order()), \
                patch("premium_balance.finish_order") as finish, \
                patch("premium_balance.refund_order") as refund, \
                patch("premium_balance._notify_user"), patch("premium_balance._notify_admins"):
            premium_balance.deliver_order(self.bot, ORDER_ID)
        finish.assert_called_once_with(ORDER_ID, "refund_pending", "InsufficientStars")
        refund.assert_called_once_with(ORDER_ID)
        self.bot.gift_premium_subscription.assert_not_called()

    @patch("premium_balance.get_conn")
    def test_worker_only_delivers_paid_orders_and_recovers_refunds(self, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [
            [{"order_id": "b" * 32}],
            [{"order_id": ORDER_ID, "status": "paid"}, {"order_id": "c" * 32, "status": "refund_pending"}],
        ]
        with patch("premium_balance.deliver_order") as deliver, \
                patch("premium_balance.refund_order") as refund, patch("premium_balance._notify_admins"):
            premium_balance.process_orders(self.bot)
        deliver.assert_called_once_with(self.bot, ORDER_ID)
        refund.assert_called_once_with("c" * 32)
        self.assertIn("INTERVAL 10 MINUTE", cur.execute.call_args_list[0].args[0])
