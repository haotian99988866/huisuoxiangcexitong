import unittest
from decimal import Decimal
from unittest.mock import patch

from wallet import amount_value, review, submit, valid_address


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


if __name__ == "__main__":
    unittest.main()
