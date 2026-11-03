import unittest
import io
from decimal import Decimal
from datetime import datetime
from unittest.mock import patch
from PIL import Image
import pymysql

from wallet import account, amount_value, ensure_tables, normalize_proof, pending_details, rejected_deposit_today, review, submit, valid_address, set_aba


class WalletTests(unittest.TestCase):
    def screenshot(self):
        output = io.BytesIO()
        Image.new("RGB", (80, 60), "white").save(output, "PNG")
        return output.getvalue()

    def test_proof_is_reencoded_and_invalid_files_are_rejected(self):
        normalized = normalize_proof(self.screenshot())
        with Image.open(io.BytesIO(normalized)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (80, 60))
        for raw in (b"", b"<svg>not a screenshot</svg>", b"a" * (5 * 1024 * 1024 + 1)):
            with self.assertRaises(ValueError):
                normalize_proof(raw)

    @patch("wallet.get_conn")
    def test_proof_submission_stores_image_atomically_without_credit(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"balance": Decimal("0")}, None, None, {"address": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"}]
        cur.lastrowid = 9
        self.assertEqual(submit(123, None, "15.99", proof=self.screenshot()), 9)
        self.assertIn("wallet_deposit_proofs", cur.execute.call_args.args[0])
        self.assertFalse(any("wallet_ledger" in call.args[0] or "balance=balance+" in call.args[0] for call in cur.execute.call_args_list))
        conn.commit.assert_called_once()

    @patch("wallet.get_conn")
    def test_duplicate_proof_rolls_back_deposit(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"balance": Decimal("0")}, None, None, {"address": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"}]
        cur.execute.side_effect = [None, None, None, None, None, None, pymysql.IntegrityError(1062, "duplicate proof")]
        with self.assertRaisesRegex(ValueError, "已提交"):
            submit(123, None, "1", proof=self.screenshot())
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    @patch("wallet.get_conn")
    def test_proof_cannot_be_credited_without_verified_transaction(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"status": "pending", "user_id": 123, "method": "usdt", "txid": "proof:abc"}
        with self.assertRaisesRegex(ValueError, "真实 USDT"):
            review(9, 99, "15.99")
        self.assertEqual(cur.execute.call_count, 1)
        conn.commit.assert_not_called()

    @patch("wallet.get_conn")
    def test_verified_amount_overrides_claimed_amount_in_balance_ledger_and_review(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {
            "status": "pending", "user_id": 123, "method": "usdt",
            "txid": "proof:abc", "claimed": Decimal("100"),
        }
        self.assertEqual(review(9, 99, "15.123456", reference="a" * 64), 123)
        calls = cur.execute.call_args_list
        self.assertEqual(calls[1].args[1], ("a" * 64, 9))
        self.assertEqual(calls[2].args[1], (123, Decimal("15.123456")))
        self.assertEqual(calls[3].args[1], (123, 9, Decimal("15.123456"), 99))
        self.assertEqual(calls[4].args[1], ("approved", Decimal("15.123456"), 99, None, 9))
        conn.commit.assert_called_once()

    @patch("wallet.get_conn")
    def test_verified_proof_reuses_global_unique_transaction_reference(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"status": "pending", "user_id": 123, "method": "usdt", "txid": "proof:abc"}
        cur.execute.side_effect = [None, pymysql.IntegrityError(1062, "already credited transaction")]
        with self.assertRaises(pymysql.IntegrityError):
            review(9, 99, "15.99", reference="a" * 64)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_amount_precision_and_invalid_values(self):
        self.assertEqual(amount_value("1.123456"), Decimal("1.123456"))
        for value in ("NaN", "Infinity", "-1", "0", "1000001", "0.0000001", "abc", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                amount_value(value)

    def test_tron_checksum(self):
        address = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
        self.assertTrue(valid_address(address))
        self.assertFalse(valid_address(address[:-1] + "a"))
        self.assertFalse(valid_address("好的"))
        self.assertFalse(valid_address(None))

    @patch("wallet.get_conn")
    def test_closed_deposits(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = None
        with self.assertRaisesRegex(ValueError, "尚未开放"):
            submit(123, "a" * 64, "1")
        conn.commit.assert_not_called()
        conn.close.assert_called_once()

    @patch("wallet.get_conn")
    def test_repeated_review_never_credits(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"status": "approved"}
        with self.assertRaisesRegex(ValueError, "不能重复"):
            review(1, 99, "5")
        self.assertEqual(cur.execute.call_count, 1)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    @patch("wallet.get_conn")
    def test_failure_rolls_back_all_changes(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"status": "pending", "user_id": 123}
        cur.execute.side_effect = [None, None, RuntimeError("ledger write failed")]
        with self.assertRaises(RuntimeError):
            review(1, 99, "5")
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    @patch("wallet.get_conn")
    def test_aba_deposit_preserves_channel_snapshot_and_does_not_credit(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"balance": Decimal("0")}, None, None, {"account_number": "123456789", "account_name": "Test Account"}]
        cur.lastrowid = 7
        self.assertEqual(submit(123, "BANK12345", "15.99", "aba"), 7)
        self.assertEqual(cur.execute.call_args.args[1],
                         (123, "aba:bank12345", Decimal("15.99"), "123456789 / Test Account", "aba"))
        self.assertFalse(any("wallet_ledger" in call.args[0] or "balance=balance+" in call.args[0] for call in cur.execute.call_args_list))
        conn.commit.assert_called_once()

    @patch("wallet.get_conn")
    def test_aba_closed_without_configuration(self, connection):
        cur = connection.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = None
        with self.assertRaisesRegex(ValueError, "ABA 充值尚未开放"):
            submit(123, "BANK12345", "1", "aba")
        connection.return_value.commit.assert_not_called()

    @patch("wallet.get_conn")
    def test_manual_aba_amount_only_submission_without_configuration_never_credits(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"balance": Decimal("0")}, None, None, None]
        cur.lastrowid = 8
        self.assertEqual(submit(123, None, "300", "aba"), 8)
        values = cur.execute.call_args.args[1]
        self.assertTrue(values[1].startswith("manual:"))
        self.assertEqual(values[2], Decimal("300"))
        self.assertEqual(values[4], "aba")
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertFalse(any("wallet_deposit_proofs" in sql or "wallet_ledger" in sql or "balance=balance+" in sql for sql in queries))
        conn.commit.assert_called_once()

    @patch("wallet.get_conn")
    def test_manual_aba_requires_real_reference_and_credits_only_verified_amount(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {
            "status": "pending", "user_id": 123, "method": "aba",
            "txid": "manual:test", "claimed": Decimal("100"),
        }
        for reference in (None, "", "bad", "bank\n123"):
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, "真实 ABA"):
                review(8, 99, "15.123456", reference=reference)
        conn.commit.assert_not_called()
        cur.reset_mock()
        self.assertEqual(review(8, 99, "15.123456", reference="BANK12345"), 123)
        calls = cur.execute.call_args_list
        self.assertEqual(calls[1].args[1], ("aba:bank12345", 8))
        self.assertEqual(calls[2].args[1], (123, Decimal("15.123456")))
        self.assertEqual(calls[3].args[1], (123, 8, Decimal("15.123456"), 99))
        self.assertEqual(calls[4].args[1], ("approved", Decimal("15.123456"), 99, None, 8))
        conn.commit.assert_called_once()

    @patch("wallet.get_conn")
    def test_manual_aba_duplicate_bank_transaction_rolls_back_without_credit(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"status": "pending", "user_id": 123, "method": "aba", "txid": "manual:test"}
        cur.execute.side_effect = [None, pymysql.IntegrityError(1062, "duplicate bank reference")]
        with self.assertRaises(pymysql.IntegrityError):
            review(8, 99, "15", reference="BANK12345")
        self.assertEqual(cur.execute.call_count, 2)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    @patch("wallet.get_conn")
    def test_manual_aba_rejection_does_not_credit(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"status": "pending", "user_id": 123, "method": "aba", "txid": "manual:test"}
        review(8, 99, reason="Bank payment not found")
        self.assertEqual(cur.execute.call_count, 3)
        self.assertEqual(cur.execute.call_args_list[1].args[1], ("rejected", None, 99, "Bank payment not found", 8))
        self.assertIn("review_notifications", cur.execute.call_args.args[0])
        conn.commit.assert_called_once()

    @patch("wallet.pending")
    def test_manual_aba_pending_summary_does_not_expose_internal_reference(self, pending):
        pending.return_value = [{"id": 8, "user_id": 123, "method": "aba", "claimed": Decimal("5"),
                                 "address": "ABA", "created_at": "2026-11-01", "txid": "manual:test"}]
        row = pending_details()[0]
        self.assertTrue(row["manual_review"])
        self.assertFalse(row["has_proof"])
        self.assertIsNone(row["reference"])

    @patch("wallet.get_conn")
    def test_invalid_channel_or_aba_reference_is_rejected(self, connection):
        for channel, reference in (("other", "BANK123"), ("aba", "bad"), ("aba", "bank\n123")):
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                submit(123, reference, "1", channel)
        connection.assert_not_called()

    @patch("wallet.get_conn")
    def test_deposit_amount_cap_is_exact_and_applies_to_both_methods(self, connection):
        for method, reference in (("usdt", "a" * 64), ("aba", "BANK12345"), ("aba", None)):
            for amount in ("300.000001", "301", "1000000"):
                with self.subTest(method=method, amount=amount), self.assertRaisesRegex(ValueError, "300"):
                    submit(123, reference, amount, method)
        connection.assert_not_called()
        cur = connection.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"balance": Decimal("0")}, None, None, {"address": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"}]
        cur.lastrowid = 7
        self.assertEqual(submit(123, "a" * 64, "300"), 7)
        self.assertEqual(cur.execute.call_args.args[1][2], Decimal("300"))

    @patch("wallet.get_conn")
    def test_pending_application_blocks_new_claim_in_either_method_under_account_lock(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        for method, reference in (("usdt", "a" * 64), ("aba", "BANK12345"), ("aba", None)):
            cur.reset_mock()
            cur.fetchone.side_effect = [{"balance": Decimal("0")}, {"id": 7}]
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "等待审核"):
                submit(123, reference, "1", method)
            self.assertIn("FOR UPDATE", cur.execute.call_args_list[1].args[0])
            self.assertEqual(cur.execute.call_count, 3)
        conn.commit.assert_not_called()
        self.assertEqual(conn.rollback.call_count, 3)

    @patch("wallet.get_conn")
    def test_account_reports_pending_independently_of_recent_thirty_records(self, connection):
        cur = connection.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [None, None, {"balance": Decimal("1")},
                                   {"id": 7, "claimed": Decimal("2"), "method": "usdt", "created_at": "2026-11-01"}, None]
        cur.fetchall.return_value = []
        result = account(123)
        self.assertEqual(result["pending_deposit"]["id"], 7)
        self.assertEqual(result["pending_deposit"]["claimed"], "2")
        self.assertEqual(result["deposit_limit"], "300")
        self.assertEqual(result["deposits"], [])
        self.assertIsNone(result["rejected_deposit"])

    @patch("wallet.get_conn")
    def test_rejection_blocks_all_channels_under_account_lock_without_credit(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        epoch = datetime.fromisoformat("2026-11-01T23:59:59+07:00").timestamp()
        for method, reference in (("usdt", "a" * 64), ("aba", "BANK12345"), ("aba", None)):
            cur.reset_mock()
            cur.fetchone.side_effect = [
                {"balance": Decimal("10")}, None,
                {"id": 7, "reviewed_epoch": epoch, "now_epoch": epoch},
            ]
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "2026-11-02 00:00"):
                submit(123, reference, "5", method)
            self.assertIn("FOR UPDATE", cur.execute.call_args_list[1].args[0])
            self.assertEqual(cur.execute.call_count, 4)
            self.assertFalse(any("wallet_ledger" in call.args[0] or "balance=balance+" in call.args[0] for call in cur.execute.call_args_list))
        conn.commit.assert_not_called()
        self.assertEqual(conn.rollback.call_count, 3)

    @patch("wallet.get_conn")
    def test_rejection_expiry_uses_cambodia_midnight_not_rolling_twenty_four_hours(self, connection):
        cur = connection.return_value.cursor.return_value.__enter__.return_value
        reviewed = datetime.fromisoformat("2026-11-01T23:59:59+07:00").timestamp()
        for now, blocked in (
            ("2026-11-01T23:59:59+07:00", True),
            ("2026-11-02T00:00:00+07:00", False),
            ("2026-11-02T00:00:01+07:00", False),
            ("2026-11-01T16:59:59+00:00", True),
            ("2026-11-01T17:00:00+00:00", False),
        ):
            cur.fetchone.return_value = {"id": 7, "reviewed_epoch": reviewed,
                                        "now_epoch": datetime.fromisoformat(now).timestamp()}
            with self.subTest(now=now):
                result = rejected_deposit_today(cur, 123)
                self.assertEqual(result is not None, blocked)
                if blocked:
                    self.assertEqual(result["retry_at"], "2026-11-02T00:00:00+07:00")

    @patch("wallet.get_conn")
    def test_previous_day_rejection_allows_both_channels_at_midnight(self, connection):
        conn = connection.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        for method, reference, config in (
            ("usdt", "a" * 64, {"address": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"}),
            ("aba", None, None),
        ):
            cur.fetchone.side_effect = [
                {"balance": Decimal("0")}, None,
                {"id": 7, "reviewed_epoch": datetime.fromisoformat("2026-11-01T23:59:59+07:00").timestamp(),
                 "now_epoch": datetime.fromisoformat("2026-11-02T00:00:00+07:00").timestamp()}, config,
            ]
            cur.lastrowid = 8
            with self.subTest(method=method):
                self.assertEqual(submit(123, reference, "5", method), 8)
        self.assertEqual(conn.commit.call_count, 2)

    @patch("wallet.get_conn")
    def test_account_reports_rejection_independently_of_recent_records(self, connection):
        cur = connection.return_value.cursor.return_value.__enter__.return_value
        epoch = datetime.fromisoformat("2026-11-01T00:01:00+07:00").timestamp()
        cur.fetchone.side_effect = [None, None, None, None,
                                   {"id": 7, "reviewed_epoch": epoch, "now_epoch": epoch}]
        cur.fetchall.return_value = []
        result = account(123)
        self.assertEqual(result["rejected_deposit"], {"id": 7, "retry_at": "2026-11-02T00:00:00+07:00", "rejection_reason": None})
        self.assertEqual(result["deposits"], [])
        self.assertIsNone(result["pending_deposit"])

    @patch("wallet.get_conn")
    def test_invalid_aba_config_cannot_be_saved(self, connection):
        with self.assertRaises(ValueError):
            set_aba("bad-account", "Test")
        connection.assert_not_called()

    @patch("review_notifications.ensure_tables")
    @patch("wallet.get_conn")
    def test_interrupted_migration_still_expands_existing_columns(self, connection, notifications):
        cur = connection.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"Type": "varchar(8)"}, None, {"Type": "char(64)"}, {"Type": "varchar(34)"}]
        ensure_tables()
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertNotIn("ALTER TABLE wallet_deposits ADD COLUMN method VARCHAR(8) NOT NULL DEFAULT 'usdt'", queries)
        self.assertIn("ALTER TABLE wallet_deposits MODIFY txid VARCHAR(128) NOT NULL", queries)
        self.assertIn("ALTER TABLE wallet_deposits MODIFY address VARCHAR(256) NOT NULL", queries)


if __name__ == "__main__":
    unittest.main()
