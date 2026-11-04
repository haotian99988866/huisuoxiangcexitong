from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import io
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

    def test_local_debug_requires_explicit_flag_and_local_unforwarded_origin(self):
        headers = {"Origin": "http://127.0.0.1:4173", "X-Local-Admin-Debug": "1"}
        cases = [
            ({}, {}, {}, False),
            ({"LOCAL_ADMIN_DEBUG": "1"}, {}, {}, True),
            ({"LOCAL_ADMIN_DEBUG": "1"}, {"Origin": "https://example.com"}, {}, False),
            ({"LOCAL_ADMIN_DEBUG": "1"}, {"Origin": ""}, {}, False),
            ({"LOCAL_ADMIN_DEBUG": "1"}, {"X-Local-Admin-Debug": ""}, {}, False),
            ({"LOCAL_ADMIN_DEBUG": "1"}, {"X-Forwarded-For": "127.0.0.1"}, {}, False),
            ({"LOCAL_ADMIN_DEBUG": "1"}, {"Forwarded": "for=127.0.0.1"}, {}, False),
            ({"LOCAL_ADMIN_DEBUG": "1"}, {"CF-Connecting-IP": "127.0.0.1"}, {}, False),
            ({"LOCAL_ADMIN_DEBUG": "1"}, {"X-Real-IP": "127.0.0.1"}, {}, False),
            ({"LOCAL_ADMIN_DEBUG": "1"}, {"X-Tenant": "test-shop"}, {}, False),
            ({"LOCAL_ADMIN_DEBUG": "1", "API_HOST": "0.0.0.0"}, {}, {}, False),
            ({"LOCAL_ADMIN_DEBUG": "1"}, {}, {"REMOTE_ADDR": "192.0.2.1"}, False),
        ]
        for env, extra_headers, environ, expected in cases:
            with self.subTest(env=env, headers=extra_headers, environ=environ), \
                    patch.dict(os.environ, {"LOCAL_ADMIN_DEBUG": "0", "API_HOST": "127.0.0.1", **env}), \
                    api_server.app.test_request_context(
                        "/webhook", base_url="http://127.0.0.1:8000",
                        headers={**headers, **extra_headers},
                        environ_base={"REMOTE_ADDR": "127.0.0.1", **environ},
                    ):
                self.assertEqual(api_server._local_admin_debug({}), expected)

    def test_local_debug_does_not_override_identity_or_public_host(self):
        with patch.dict(os.environ, {"LOCAL_ADMIN_DEBUG": "1", "API_HOST": "127.0.0.1"}):
            for url, data in [
                ("http://example.com", {}),
                ("http://127.0.0.1:8000", {"init_data": signed(999)}),
                ("http://127.0.0.1:8000", {"init_data": "bad"}),
            ]:
                with self.subTest(url=url, data=data), api_server.app.test_request_context(
                    "/webhook", base_url=url,
                    headers={"Origin": "http://127.0.0.1:4173", "X-Local-Admin-Debug": "1"},
                    environ_base={"REMOTE_ADDR": "127.0.0.1"},
                ):
                    self.assertFalse(api_server._local_admin_debug(data))

    def test_local_debug_route_uses_explicit_audit_actor(self):
        with patch.dict(os.environ, {"LOCAL_ADMIN_DEBUG": "1", "API_HOST": "127.0.0.1"}), \
                patch.object(admin_console, "save_from_request") as save:
            response = self.client.post(
                "/webhook", base_url="http://127.0.0.1:8000",
                headers={"Origin": "http://127.0.0.1:4173", "X-Local-Admin-Debug": "1"},
                json={"action": "admin_tenant_save"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(save.call_args.args[1], -1)

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

    def test_staff_archive_management_requires_admin_and_uses_private_responses(self):
        with patch("staff_archive.detail", return_value={"id": 12, "description": "internal"}) as detail, \
                patch("staff_archive.update_archive", return_value={"ok": True}) as update:
            for action in ("admin_staff_detail", "admin_staff_description", "admin_staff_delete"):
                for init_data, status in (("", 401), (signed(999), 403),
                                          (signed(start_param="tenant_test-shop"), 403)):
                    response = self.post(action, init_data, staff_id=12)
                    self.assertEqual(response.status_code, status)
                detail.assert_not_called()
                update.assert_not_called()
                response = self.post(action, staff_id=12, confirmed=True)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                if action == "admin_staff_detail":
                    detail.assert_called_once_with(unittest.mock.ANY, admin=True)
                else:
                    self.assertEqual(update.call_args.args[1], 123)
                    self.assertEqual(update.call_args.kwargs["delete"], action == "admin_staff_delete")
                detail.reset_mock()
                update.reset_mock()

    def test_staff_description_conflict_and_deleted_detail_have_explicit_status(self):
        from customer_records import RecordConflict
        with patch("staff_archive.update_archive", side_effect=RecordConflict("stale revision")):
            response = self.post("admin_staff_description", staff_id=12)
            self.assertEqual(response.status_code, 409)
        with patch("staff_archive.detail", return_value=None):
            response = self.post("admin_staff_detail", staff_id=12)
            self.assertEqual(response.status_code, 404)

    def test_delivery_private_attachments_require_admin_and_upload_endpoint(self):
        import delivery_staff
        with patch.object(delivery_staff, "attachment", return_value={
            "content": b"private-image", "content_type": "image/jpeg",
        }) as read:
            self.assertEqual(self.post("admin_delivery_staff_attachment", "", attachment_id=1).status_code, 401)
            self.assertEqual(self.post("admin_delivery_staff_attachment", signed(999), attachment_id=1).status_code, 403)
            read.assert_not_called()
            response = self.post("admin_delivery_staff_attachment", attachment_id=1)
            self.assertEqual(response.data, b"private-image")
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        with patch.object(delivery_staff, "add_attachment", return_value={"attachment_id": 1}) as add:
            for identity, expected in [("", 401), (signed(999), 403), (signed(), 200)]:
                response = self.client.post("/admin-delivery-upload", data={
                    "payload": json.dumps({"person_id": 7, "init_data": identity}),
                    "media": (io.BytesIO(b"image-bytes"), "x.png"),
                })
                self.assertEqual(response.status_code, expected)
            add.assert_called_once_with(7, b"image-bytes", "x.png", 123, description=None)
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            self.assertEqual(self.post("admin_delivery_staff_upload", person_id=7).status_code, 400)
        response = self.client.post("/admin-delivery-upload?tenant=test-shop", data={})
        self.assertEqual(response.status_code, 403)

    def test_delivery_deletion_requires_admin_and_confirmation(self):
        import delivery_staff
        with patch.object(delivery_staff, "delete_archive_content", return_value={"deleted": True}) as delete:
            for action in ("admin_delivery_staff_delete", "admin_delivery_staff_attachment_delete"):
                for identity, expected in [("", 401), (signed(999), 403)]:
                    self.assertEqual(self.post(action, identity, person_id=7, attachment_id=9,
                                               confirmed=True).status_code, expected)
            delete.assert_not_called()
            self.assertEqual(self.post("admin_delivery_staff_delete", person_id=7, confirmed=True).status_code, 200)
            delete.assert_called_once_with(7, None, True, 123)
            delete.reset_mock()
            self.assertEqual(self.post("admin_delivery_staff_attachment_delete", person_id=7, confirmed=True).status_code, 400)
            delete.assert_not_called()
            self.assertEqual(self.post("admin_delivery_staff_attachment_delete", person_id=7,
                                       attachment_id=9, confirmed=True).status_code, 200)
            delete.assert_called_once_with(7, 9, True, 123)

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
