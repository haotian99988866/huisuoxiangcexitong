import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from review_notifications import deliver_once, rejection_reason
import premium_balance
import wallet


class ReviewNotificationsTests(unittest.TestCase):
    def test_bot_refund_confirmation_rejects_changed_reason_and_uses_latest_preview(self):
        import main
        order_id = "a" * 32
        row = {"order_id": order_id, "owner_id": 99, "status": "manual_pending",
               "refund_reason": "Latest reason"}
        call = SimpleNamespace(data="manual:confirm_refund:" + order_id, id="callback",
                               from_user=SimpleNamespace(id=99),
                               message=SimpleNamespace(chat=SimpleNamespace(type="private", id=99),
                                                       message_id=7,
                                                       text=main.manual_refund_confirmation_text(order_id, "Old reason")))
        with patch.object(main, "bot") as bot, patch("premium_balance.get_order", return_value=row), \
                patch("visit_stats.admin_ids", return_value={99}), \
                patch("premium_balance.manual_order_text", return_value="Refunded"), \
                patch("premium_balance.refund_order") as refund:
            main.handle_manual_membership(call)
            refund.assert_not_called()
            self.assertTrue(bot.answer_callback_query.call_args.kwargs["show_alert"])
            call.message.text = main.manual_refund_confirmation_text(order_id, row["refund_reason"])
            main.handle_manual_membership(call)
            refund.assert_called_once_with(order_id, 99, confirmed_not_delivered=True, reason="Latest reason")
            bot.send_message.assert_not_called()

    def test_bot_refund_prompts_for_reason_without_touching_money(self):
        import main
        call = SimpleNamespace(data="manual:refund:" + "a" * 32, id="callback",
                               from_user=SimpleNamespace(id=99),
                               message=SimpleNamespace(chat=SimpleNamespace(type="private", id=99), message_id=7))
        row = {"order_id": "a" * 32, "owner_id": 99, "status": "manual_pending"}
        with patch.object(main, "bot") as bot, patch("premium_balance.get_order", return_value=row), \
                patch("visit_stats.admin_ids", return_value={99}), patch("premium_balance.refund_order") as refund:
            main.handle_manual_membership(call)
            refund.assert_not_called()
            bot.register_next_step_handler.assert_called_once()
            self.assertIn("300", bot.send_message.call_args.args[1])

    def test_bot_reason_confirmation_saves_draft_and_rechecks_owner(self):
        import main
        message = SimpleNamespace(text="Carrier unsupported", from_user=SimpleNamespace(id=99),
                                  chat=SimpleNamespace(type="private", id=99))
        row = {"owner_id": 99, "status": "manual_pending", "product_kind": "phone"}
        with patch.object(main, "bot") as bot, patch("premium_balance.get_order", return_value=row), \
                patch("visit_stats.admin_ids", return_value={99}), \
                patch("premium_balance.set_refund_reason", return_value=message.text) as save:
            main.handle_manual_refund_reason(message, "a" * 32)
            save.assert_called_once_with("a" * 32, 99, message.text)
            self.assertIn(message.text, bot.send_message.call_args.args[1])
            message.from_user.id = 88
            save.reset_mock()
            main.handle_manual_refund_reason(message, "a" * 32)
            save.assert_not_called()

    def test_reason_limits_and_normalization(self):
        self.assertEqual(rejection_reason("  Payment not found  "), "Payment not found")
        self.assertEqual(rejection_reason("x" * 300), "x" * 300)
        for value in (None, "", " \n ", "x" * 301, [], "bad\x00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rejection_reason(value)

    @patch("wallet.get_conn")
    def test_missing_reason_cannot_reject_deposit(self, connection):
        for reason in (None, "", " "):
            with self.assertRaises(ValueError):
                wallet.review(1, 99, reason=reason)
        connection.assert_not_called()

    @patch("wallet.get_conn")
    def test_deposit_notification_insert_failure_rolls_back_review(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"status": "pending", "user_id": 123}
        cur.execute.side_effect = [None, None, RuntimeError("outbox unavailable")]
        with self.assertRaises(RuntimeError):
            wallet.review(1, 99, reason="Unverified payment")
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()
        self.assertFalse(any("balance=balance+" in call.args[0] for call in cur.execute.call_args_list))

    @patch("premium_balance.get_conn")
    def test_manual_order_requires_reason_before_refund_and_keeps_draft_private(self, connection):
        cur = connection.return_value.cursor.return_value.__enter__.return_value
        row = {"order_id": "a" * 32, "status": "manual_pending", "owner_id": 99,
               "payer_id": 123, "price_usd": Decimal("6"), "months": 0,
               "recipient_username": "", "product_kind": "phone", "refund_reason": "Draft"}
        cur.fetchone.return_value = row
        with patch("visit_stats.admin_ids", return_value={99}):
            with self.assertRaises(ValueError):
                premium_balance.refund_order(row["order_id"], 99, True)
            self.assertEqual(cur.execute.call_count, 1)
            self.assertIsNone(premium_balance.order_public(row)["rejection_reason"])
            cur.reset_mock()
            premium_balance.set_refund_reason(row["order_id"], 99, "  Carrier unsupported  ")
            self.assertEqual(cur.execute.call_args.args[1], ("Carrier unsupported", row["order_id"]))
            self.assertFalse(any("wallet_accounts" in call.args[0] for call in cur.execute.call_args_list))
            cur.reset_mock()
            row["status"] = "manual_pending"
            premium_balance.refund_order(row["order_id"], 99, True, reason="Carrier unsupported")
            self.assertIn("review_notifications", cur.execute.call_args.args[0])
            self.assertIn("Carrier unsupported", cur.execute.call_args.args[1][2])
            self.assertEqual(cur.execute.call_args_list[1].args[1], (123, Decimal("6")))

    @patch("review_notifications.get_conn")
    def test_delivery_has_contact_button_plain_text_and_persists_result(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"event_key": "deposit:1", "user_id": 123, "body": "Reason <not markup>"}
        bot = MagicMock()
        deliver_once(bot)
        self.assertEqual(bot.send_message.call_args.args, (123, "Reason <not markup>"))
        self.assertIsNone(bot.send_message.call_args.kwargs["parse_mode"])
        keyboard = bot.send_message.call_args.kwargs["reply_markup"].to_dict()
        self.assertEqual(keyboard["inline_keyboard"][0][0]["url"], "https://t.me/haotian99988866")
        self.assertEqual(cur.execute.call_args.args[1], ("sent", None, "deposit:1"))
        self.assertEqual(conn.commit.call_count, 2)

    @patch("review_notifications.get_conn")
    def test_timeout_is_uncertain_not_immediately_resent(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"event_key": "order:a", "user_id": 123, "body": "Refunded"}, None]
        bot = MagicMock()
        bot.send_message.side_effect = TimeoutError("unknown delivery")
        with patch("review_notifications.logger.error"):
            deliver_once(bot)
            self.assertEqual(cur.execute.call_args.args[1], ("uncertain", "TimeoutError", "order:a"))
            deliver_once(bot)
        bot.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
