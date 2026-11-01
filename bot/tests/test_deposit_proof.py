import io
import os
import unittest
from unittest.mock import patch

import api_server
from tests.test_admin_console import signed, TOKEN


class DepositProofTests(unittest.TestCase):
    def setUp(self):
        self.client = api_server.app.test_client()
        env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "MEMBER_SITE_URL": "https://members.example.com/membership"})
        env.start()
        self.addCleanup(env.stop)
        notification = patch("wallet.notify_deposit", return_value=True)
        self.notify = notification.start()
        self.addCleanup(notification.stop)

    def upload(self, init_data=None, **kwargs):
        data = {"method": "usdt", "amount": "15.99", "proof": (io.BytesIO(b"test-image"), "proof.png")}
        if init_data is not None:
            data["init_data"] = init_data
        return self.client.post("/member-deposit", data=data, **kwargs)

    @patch("wallet.submit", return_value=9)
    def test_upload_uses_verified_account_and_preserves_privacy(self, submit):
        response = self.upload(signed(321))
        self.assertEqual(response.status_code, 200)
        submit.assert_called_once_with(321, None, "15.99", "usdt", proof=b"test-image")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.notify.assert_called_once_with(9)

    @patch("wallet.submit", return_value=10)
    def test_aba_amount_only_json_uses_verified_user_and_notifies_admin(self, submit):
        response = self.client.post("/webhook", json={
            "action": "member_deposit", "method": "aba", "amount": "25",
            "init_data": signed(321), "user_id": 999,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["id"], 10)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        submit.assert_called_once_with(321, None, "25", "aba")
        self.notify.assert_called_once_with(10)

    @patch("wallet.submit")
    def test_aba_amount_only_rejects_invalid_identity_and_tenant(self, submit):
        for signature, status in (("invalid", 401), (signed(start_param="tenant_test-shop"), 403)):
            response = self.client.post("/webhook", json={
                "action": "member_deposit", "method": "aba", "amount": "25", "init_data": signature,
            })
            self.assertEqual(response.status_code, status)
        response = self.client.post("/webhook", json={
            "action": "member_deposit", "method": "aba", "amount": "25",
        }, headers={"Origin": "https://wrong.example.com"})
        self.assertEqual(response.status_code, 403)
        submit.assert_not_called()

    @patch("wallet.submit", return_value=9)
    def test_notification_failure_is_explicit_without_resubmitting_application(self, submit):
        self.notify.return_value = False
        response = self.upload(signed(321))
        self.assertEqual(response.status_code, 200)
        self.assertIn("提醒发送失败", response.json["warning"])
        submit.assert_called_once()

    @patch("wallet.submit")
    def test_invalid_signature_tenant_and_cross_origin_cannot_submit(self, submit):
        self.assertEqual(self.upload("invalid").status_code, 401)
        self.assertEqual(self.upload(signed(start_param="tenant_test-shop")).status_code, 403)
        self.assertEqual(self.upload(signed(), headers={"X-Tenant": "test-shop"}).status_code, 403)
        self.assertEqual(self.upload(headers={"Origin": "https://wrong.example.com"}).status_code, 403)
        submit.assert_not_called()

    @patch("wallet.submit", return_value=9)
    @patch("member_accounts.authenticate", return_value=321)
    def test_browser_session_upload(self, authenticate, submit):
        response = self.upload(headers={"Origin": "https://members.example.com", "Authorization": "Bearer test-session"})
        self.assertEqual(response.status_code, 200)
        authenticate.assert_called_once_with("test-session")

    @patch("wallet.submit")
    def test_missing_file_and_oversized_requests_fail(self, submit):
        response = self.client.post("/member-deposit", data={"init_data": signed(), "method": "usdt", "amount": "1"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/member-deposit", data=b"x" * (6 * 1024 * 1024 + 1), content_type="multipart/form-data")
        self.assertEqual(response.status_code, 413)
        submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
