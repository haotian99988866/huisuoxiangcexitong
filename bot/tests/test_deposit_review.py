import os
import unittest
from unittest.mock import patch

import api_server
import pymysql
from tests.test_admin_console import signed, TOKEN
from wallet import notify_deposit


class DepositReviewTests(unittest.TestCase):
    def setUp(self):
        self.client = api_server.app.test_client()
        env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "ADMIN_USER_IDS": "123"})
        env.start()
        self.addCleanup(env.stop)

    def post(self, action, init_data=None, **fields):
        return self.client.post("/webhook", json={
            "action": action, "init_data": signed() if init_data is None else init_data, **fields,
        })

    @patch("wallet.pending_details")
    @patch("wallet.proof_image")
    @patch("wallet.review")
    def test_all_review_surfaces_require_owner_signature(self, review, proof, pending):
        for action in ("admin_deposits", "admin_deposit_proof", "admin_deposit_review"):
            for signature, status in (("invalid", 401), (signed(999), 403),
                                      (signed(start_param="tenant_test-shop"), 403)):
                response = self.post(action, signature, deposit_id=1)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        pending.assert_not_called()
        proof.assert_not_called()
        review.assert_not_called()

    @patch("wallet.pending_details", return_value=[{"id": 1}])
    @patch("wallet.proof_image", return_value=b"image")
    def test_owner_can_read_pending_and_private_proof(self, proof, pending):
        response = self.post("admin_deposits")
        self.assertEqual(response.json["deposits"], [{"id": 1}])
        response = self.post("admin_deposit_proof", deposit_id=1)
        self.assertEqual(response.json["image"], "data:image/jpeg;base64,aW1hZ2U=")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    @patch("wallet.review")
    def test_approval_requires_confirmation_amount_and_valid_id(self, review):
        for fields in ({}, {"confirmed": False}, {"confirmed": True},
                       {"confirmed": True, "amount": "1", "deposit_id": True},
                       {"confirmed": True, "amount": "1", "decision": []}):
            values = {"deposit_id": 1, "decision": "approve", **fields}
            self.assertEqual(self.post("admin_deposit_review", **values).status_code, 400)
        review.assert_not_called()
        response = self.post("admin_deposit_review", deposit_id=1, decision="approve",
                             confirmed=True, amount="15.99", reference="a" * 64, actor_id=999)
        self.assertEqual(response.status_code, 200)
        review.assert_called_once_with(1, 123, "15.99", reference="a" * 64)

    @patch("wallet.review")
    def test_reject_ignores_client_amount_and_duplicate_reference_returns_conflict(self, review):
        response = self.post("admin_deposit_review", deposit_id=1, decision="reject", confirmed=True, amount="100")
        self.assertEqual(response.status_code, 200)
        review.assert_called_once_with(1, 123, None, reference=None)
        review.side_effect = pymysql.IntegrityError(1062, "duplicate")
        response = self.post("admin_deposit_review", deposit_id=1, decision="approve", confirmed=True, amount="1")
        self.assertEqual(response.status_code, 409)

    @patch("wallet.requests.post")
    def test_new_deposit_notification_contains_no_customer_proof(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"ok": True}
        self.assertTrue(notify_deposit(7))
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["chat_id"], 123)
        self.assertIn("#7", payload["text"])
        self.assertNotIn("photo", payload)
        self.assertEqual(post.call_args.kwargs["timeout"], 10)
        post.return_value.json.return_value = {"ok": False}
        self.assertFalse(notify_deposit(7))


if __name__ == "__main__":
    unittest.main()
