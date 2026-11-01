import unittest
from decimal import Decimal
from unittest.mock import patch

from wallet import amount_value, ensure_tables, review, submit, valid_address, set_aba


class WalletTests(unittest.TestCase):
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
        cur.fetchone.return_value = {"account_number": "123456789", "account_name": "Test Account"}
        cur.lastrowid = 7
        self.assertEqual(submit(123, "BANK12345", "15.99", "aba"), 7)
        self.assertEqual(cur.execute.call_args.args[1],
                         (123, "aba:bank12345", Decimal("15.99"), "123456789 / Test Account", "aba"))
        self.assertFalse(any("wallet_accounts" in call.args[0] for call in cur.execute.call_args_list))
        conn.commit.assert_called_once()

    @patch("wallet.get_conn")
    def test_aba_closed_without_configuration(self, connection):
        cur = connection.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = None
        with self.assertRaisesRegex(ValueError, "ABA 充值尚未开放"):
            submit(123, "BANK12345", "1", "aba")
        connection.return_value.commit.assert_not_called()

    @patch("wallet.get_conn")
    def test_invalid_channel_or_aba_reference_is_rejected(self, connection):
        for channel, reference in (("other", "BANK123"), ("aba", "bad"), ("aba", "bank\n123")):
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                submit(123, reference, "1", channel)
        connection.assert_not_called()

    @patch("wallet.get_conn")
    def test_invalid_aba_config_cannot_be_saved(self, connection):
        with self.assertRaises(ValueError):
            set_aba("bad-account", "Test")
        connection.assert_not_called()

    @patch("wallet.get_conn")
    def test_interrupted_migration_still_expands_existing_columns(self, connection):
        cur = connection.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"Type": "varchar(8)"}, {"Type": "char(64)"}, {"Type": "varchar(34)"}]
        ensure_tables()
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertNotIn("ALTER TABLE wallet_deposits ADD COLUMN method VARCHAR(8) NOT NULL DEFAULT 'usdt'", queries)
        self.assertIn("ALTER TABLE wallet_deposits MODIFY txid VARCHAR(128) NOT NULL", queries)
        self.assertIn("ALTER TABLE wallet_deposits MODIFY address VARCHAR(256) NOT NULL", queries)


if __name__ == "__main__":
    unittest.main()
