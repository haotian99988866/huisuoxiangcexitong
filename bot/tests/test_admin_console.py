from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode
import unittest
from unittest.mock import MagicMock, patch

import admin_console
import api_server
from tenants import TenantConflict, save_tenant, tenant_revision, tenant_snapshot

TOKEN = "test-token"


def signed(user_id=123, **fields):
    values = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
        **fields,
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def tenant():
    return {
        "slug": "test-shop", "brand_name": "Test", "contact_name": "Support",
        "contact_username": "test_service", "venue_ids": [3], "status": "active",
        "expires_at": datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) + timedelta(days=30),
    }


class AdminConsoleTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "ADMIN_USER_IDS": "123"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = api_server.app.test_client()

    def post(self, action="admin_dashboard", init_data=None, **fields):
        return self.client.post("/webhook", json={
            **fields, "action": action, "init_data": signed() if init_data is None else init_data,
        })

    def connection(self, row=None):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = row
        return conn, cur

    def test_unsigned_invalid_nonadmin_and_tenant_sessions_are_denied_before_database(self):
        cases = [("", 401), ("bad", 401), (signed(999), 403),
                 (signed(start_param="tenant_test-shop"), 403),
                 (signed(auth_date=str(int(time.time()) + 3600)), 401),
                 (signed(auth_date="1"), 401), (signed(user_id="123"), 401),
                 (signed(user="[]"), 401),
                 (signed() + "&auth_date=1", 401)]
        with patch.object(admin_console, "dashboard") as dashboard, \
                patch.object(admin_console, "save_from_request") as save:
            for init_data, code in cases:
                for action in ("admin_dashboard", "admin_tenant_save"):
                    with self.subTest(code=code, action=action):
                        response = self.post(action, init_data)
                        self.assertEqual(response.status_code, code)
                        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            dashboard.assert_not_called()
            save.assert_not_called()

    def test_signed_data_tampering_is_denied(self):
        response = self.post(init_data=signed().replace("Test", "Changed"))
        self.assertEqual(response.status_code, 401)

    def test_tenant_header_is_rejected_even_for_owner(self):
        with patch.object(api_server, "load_tenant", return_value=tenant()), \
                patch.object(admin_console, "dashboard") as dashboard:
            response = self.client.post("/webhook", headers={"X-Tenant": "test-shop"},
                                        json={"action": "admin_dashboard", "init_data": signed()})
            self.assertEqual(response.status_code, 403)
            dashboard.assert_not_called()

    def test_dashboard_returns_only_after_authentication(self):
        with patch.object(admin_console, "dashboard", return_value={"counts": {"total": 1}}):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["counts"]["total"], 1)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_actor_comes_from_signature_and_conflicts_are_explicit(self):
        with patch.object(admin_console, "save_from_request") as save:
            response = self.post("admin_tenant_save", actor_id=999)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(save.call_args.args[1], 123)
        for exception, code in [
            (TenantConflict("conflict"), 409), (ValueError("invalid"), 400),
            (api_server.pymysql.IntegrityError("duplicate"), 409),
            (api_server.pymysql.OperationalError("offline"), 503),
        ]:
            with patch.object(admin_console, "save_from_request", side_effect=exception):
                self.assertEqual(self.post("admin_tenant_save").status_code, code)

    def test_request_schema_dates_versions_and_no_implicit_creation(self):
        row = tenant()
        payload = {"mode": "update", "tenant": tenant_snapshot(row), "revision": tenant_revision(row)}
        with patch.object(admin_console, "save_tenant") as save:
            admin_console.save_from_request(payload, 123)
            self.assertEqual(save.call_args.kwargs["actor_id"], 123)
            self.assertEqual(save.call_args.kwargs["expected_revision"], tenant_revision(row))
            self.assertFalse(save.call_args.kwargs["create"])
            self.assertEqual(save.call_args.args[0]["expires_at"], row["expires_at"])
        for changed in [
            {"mode": "other"}, {"mode": []}, {"tenant": []}, {"revision": None},
            {"tenant": {**payload["tenant"], "expires_at": "2030-02-30T00:00:00Z"}},
            {"tenant": {**payload["tenant"], "expires_at": "2030-01-01T00:00:00+07:00"}},
            {"tenant": {"slug": "test-shop"}},
            {"tenant": {key: value for key, value in payload["tenant"].items() if key != "announcements_enabled"}},
        ]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                admin_console.save_from_request({**payload, **changed}, 123)

    def test_announcement_permission_is_saved_and_audited(self):
        row = tenant()
        updated = {**row, "announcements_enabled": True}
        payload = {"mode": "update", "tenant": tenant_snapshot(updated), "revision": tenant_revision(row)}
        with patch.object(admin_console, "save_tenant") as save:
            admin_console.save_from_request(payload, 123)
            self.assertIs(save.call_args.args[0]["announcements_enabled"], True)
        conn, cur = self.connection(row)
        cur.fetchall.return_value = [{"id": 3}]
        with patch("tenants.get_conn", return_value=conn):
            save_tenant(updated, actor_id=123, expected_revision=tenant_revision(row))
        self.assertIs(json.loads(cur.execute.call_args.args[1][4])["announcements_enabled"], False)
        self.assertIs(json.loads(cur.execute.call_args.args[1][5])["announcements_enabled"], True)

    def test_revision_conflict_rolls_back_before_update_and_audit(self):
        row = tenant()
        conn, cur = self.connection(row)
        cur.fetchall.return_value = [{"id": 3}]
        with patch("tenants.get_conn", return_value=conn), self.assertRaises(TenantConflict):
            save_tenant({**row, "brand_name": "New"}, actor_id=123, expected_revision="0" * 64)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        self.assertIn("FOR UPDATE", cur.execute.call_args.args[0])

    def test_audit_failure_rolls_back_the_change(self):
        row = tenant()
        conn, cur = self.connection(row)
        cur.fetchall.return_value = [{"id": 3}]
        def execute(sql, *_):
            if "INSERT INTO tenant_admin_audit" in sql:
                raise api_server.pymysql.OperationalError("audit failed")
        cur.execute.side_effect = execute
        with patch("tenants.get_conn", return_value=conn), self.assertRaises(api_server.pymysql.OperationalError):
            save_tenant({**row, "status": "suspended"}, actor_id=123, expected_revision=tenant_revision(row))
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_successful_edit_and_audit_commit_together(self):
        row = tenant()
        conn, cur = self.connection(row)
        cur.fetchall.return_value = [{"id": 3}]
        with patch("tenants.get_conn", return_value=conn):
            save_tenant({**row, "brand_name": "New"}, actor_id=123, expected_revision=tenant_revision(row))
        conn.commit.assert_called_once()
        sql, values = cur.execute.call_args.args
        self.assertIn("tenant_admin_audit", sql)
        self.assertEqual(values[1], 123)
        self.assertEqual(json.loads(values[4])["brand_name"], "Test")
        self.assertEqual(json.loads(values[5])["brand_name"], "New")

    def test_dashboard_utc_counts_and_recent_audit(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = [
            tenant(), {**tenant(), "slug": "soon", "expires_at": now + timedelta(days=1)},
            {**tenant(), "slug": "past", "expires_at": now - timedelta(days=1)},
            {**tenant(), "slug": "draft", "status": "draft", "expires_at": None},
            {**tenant(), "slug": "paused", "status": "suspended"},
        ]
        conn, cur = self.connection({"count": 2})
        cur.fetchall.side_effect = [rows, [{"id": 3, "name": "KTV"}], [{
            "id": 1, "tenant_slug": "soon", "actor_id": 123,
            "source": "miniapp", "action": "update", "created_at": now,
        }]]
        with patch.object(admin_console, "get_conn", return_value=conn):
            result = admin_console.dashboard()
        self.assertEqual(cur.execute.call_args_list[0].args[0], "SET time_zone = '+00:00'")
        self.assertEqual(result["counts"], {
            "total": 5, "active": 2, "expiring": 1, "expired": 1,
            "draft": 1, "suspended": 1, "pending_deposits": 2,
        })
        self.assertTrue(result["audit"][0]["created_at"].endswith("Z"))
        self.assertEqual(result["tenants"][2]["effective_status"], "expired")
        self.assertTrue(all(len(row["revision"]) == 64 for row in result["tenants"]))


if __name__ == "__main__":
    unittest.main()
