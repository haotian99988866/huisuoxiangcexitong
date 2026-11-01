import os
import unittest
from unittest.mock import patch

import member_accounts
import api_server


class MemberAccountTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"MEMBER_SITE_URL": "https://members.example.com/membership"})
        env.start()
        self.addCleanup(env.stop)

    def test_site_requires_https_and_exact_page(self):
        for url in ("http://example.com/membership", "https://example.com/",
                    "https://example.com/membership#login=secret", "https://user:pass@example.com/membership"):
            with self.subTest(url=url), patch.dict(os.environ, {"MEMBER_SITE_URL": url}):
                with self.assertRaises(ValueError):
                    member_accounts.site_url()

    @patch("member_accounts.get_conn")
    def test_login_link_hashes_token_and_expires(self, get_conn):
        token = "a" * 64
        with patch("member_accounts.secrets.token_hex", return_value=token):
            link = member_accounts.create_login_link(123)
        self.assertEqual(link, f"https://members.example.com/membership#login={token}")
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        query, values = cur.execute.call_args.args
        self.assertIn("INTERVAL 10 MINUTE", query)
        self.assertEqual(values, (member_accounts.token_hash(token), 123))
        self.assertNotIn(token, values)
        get_conn.return_value.commit.assert_called_once()

    @patch("member_accounts.get_conn")
    def test_exchange_consumes_link_and_issues_hashed_session_atomically(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"user_id": 123}
        with patch("member_accounts.secrets.token_hex", return_value="b" * 64):
            self.assertEqual(member_accounts.exchange_link("a" * 64), "b" * 64)
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertIn("FOR UPDATE", queries[0])
        self.assertIn("used_at IS NULL AND expires_at>NOW()", queries[0])
        self.assertIn("used_at=NOW()", queries[1])
        self.assertIn("INTERVAL 24 HOUR", queries[-1])
        self.assertEqual(cur.execute.call_args.args[1], (member_accounts.token_hash("b" * 64), 123))
        conn.commit.assert_called_once()

    @patch("member_accounts.get_conn")
    def test_used_or_expired_link_cannot_issue_session(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = None
        with self.assertRaisesRegex(ValueError, "已使用或过期"):
            member_accounts.exchange_link("a" * 64)
        conn.rollback.assert_called_once()
        self.assertEqual(cur.execute.call_count, 1)
        conn.commit.assert_not_called()

    @patch("member_accounts.get_conn")
    def test_exchange_failure_rolls_back_link_consumption(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"user_id": 123}
        cur.execute.side_effect = [None, None, None, RuntimeError("write failed")]
        with self.assertRaises(RuntimeError):
            member_accounts.exchange_link("a" * 64)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    @patch("member_accounts.get_conn")
    def test_bad_token_rejected_without_database(self, get_conn):
        for token in (None, "", "a" * 63, "z" * 64):
            with self.subTest(token=token), self.assertRaises(ValueError):
                member_accounts.authenticate(token)
        get_conn.assert_not_called()


class MemberApiTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "MEMBER_SITE_URL": "https://members.example.com/membership",
            "TELEGRAM_BOT_TOKEN": "test-token",
        })
        env.start()
        self.addCleanup(env.stop)
        self.client = api_server.app.test_client()
        notification = patch("wallet.notify_deposit", return_value=True)
        notification.start()
        self.addCleanup(notification.stop)

    def post(self, action, origin="https://members.example.com", **fields):
        return self.client.post("/webhook", json={"action": action, **fields},
                                headers={"Origin": origin, "Authorization": "Bearer " + "a" * 64})

    def test_wrong_origin_and_invalid_miniapp_data_denied_before_browser_authentication(self):
        with patch("member_accounts.authenticate") as auth:
            for response, status in (
                (self.post("member_account", origin="https://untrusted.example.com"), 403),
                (self.post("member_purchase", init_data="telegram-data"), 401),
            ):
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            auth.assert_not_called()

    def test_signed_miniapp_identifies_account_without_browser_login(self):
        from tests.test_admin_console import signed
        with patch("member_accounts.authenticate") as auth, patch("wallet.account", return_value={
                "balance": "0", "deposits": [], "address": None, "aba": None}):
            response = self.post("member_account", init_data=signed(123), user_id=999)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["user_id"], 123)
        auth.assert_not_called()

    def test_signed_tenant_cannot_access_membership(self):
        from tests.test_admin_console import signed
        response = self.post("member_account", init_data=signed(start_param="tenant_test-shop"))
        self.assertEqual(response.status_code, 403)

    def test_removed_stars_actions_are_not_available(self):
        with patch.object(api_server, "get_db_connection") as connection:
            for action in (
                    "premium_plans", "premium_order_create", "premium_orders", "premium_order_status",
                    "stars_topup_catalog", "stars_topup_create", "stars_topup_orders", "stars_topup_status"):
                with self.subTest(action=action):
                    response = self.post(action)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json, {"error": "Unknown action"})
            connection.assert_not_called()

    def test_invalid_session_cannot_purchase(self):
        with patch("member_accounts.authenticate", side_effect=ValueError("expired")), \
                patch("premium_balance.create_order") as create:
            self.assertEqual(self.post("member_purchase").status_code, 401)
            create.assert_not_called()

    def test_purchase_uses_authenticated_user_and_server_amount(self):
        with patch("member_accounts.authenticate", return_value=123), \
                patch("premium_balance.create_order", return_value={
                    "order_id": "b" * 32, "months": 3, "recipient_username": "receiver",
                    "price_usd": "15.99", "status": "paid",
                }) as create, patch("telebot.TeleBot"):
            response = self.post("member_purchase", months=3, recipient_username="@receiver",
                                 order_id="b" * 32, user_id=999, price_usd="0.01")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(create.call_args.args[1:], (123, "@receiver", 3, "b" * 32))
        self.assertEqual(response.json["price_usd"], "15.99")

    def test_order_lookup_is_scoped_to_authenticated_user(self):
        with patch("member_accounts.authenticate", return_value=123), \
                patch("premium_balance.get_order", return_value=None) as lookup:
            response = self.post("member_order", order_id="b" * 32)
        self.assertEqual(response.status_code, 404)
        lookup.assert_called_once_with("b" * 32, 123)

    def test_ledger_uses_authenticated_identity_and_is_not_cached(self):
        with patch("member_accounts.authenticate", return_value=123), \
                patch("premium_balance.recent_ledger", return_value=[]) as ledger:
            response = self.post("member_ledger", user_id=999)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"entries": []})
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        ledger.assert_called_once_with(123)

    def test_ledger_rejects_invalid_and_tenant_identities_before_database_access(self):
        from tests.test_admin_console import signed
        with patch("premium_balance.recent_ledger") as ledger, \
                patch("member_accounts.authenticate", side_effect=ValueError("expired")):
            for fields in ({}, {"init_data": "invalid"},
                           {"init_data": signed(start_param="tenant_test-shop")}):
                with self.subTest(fields=fields):
                    response = self.post("member_ledger", **fields)
                    self.assertIn(response.status_code, (401, 403))
        ledger.assert_not_called()

    def test_aba_request_is_forwarded_without_crediting(self):
        with patch("member_accounts.authenticate", return_value=123), \
                patch("wallet.submit", return_value=7) as submit, patch("wallet.review") as review:
            response = self.post("member_deposit", method="aba", reference="BANK123", amount="15.99")
        self.assertEqual(response.json, {"id": 7})
        submit.assert_called_once_with(123, "BANK123", "15.99", "aba")
        review.assert_not_called()

    def test_login_error_and_database_failure_are_explicit_and_not_cached(self):
        with patch("member_accounts.exchange_link", side_effect=ValueError("used")):
            response = self.post("member_login", token="a" * 64)
        self.assertEqual(response.status_code, 401)
        with patch("member_accounts.authenticate", side_effect=RuntimeError("offline")):
            response = self.post("member_account")
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.json)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
