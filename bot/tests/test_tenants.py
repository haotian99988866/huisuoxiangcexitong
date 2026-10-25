from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, patch

import api_server
from tenants import TenantUnavailable, load_tenant, public_config, save_tenant, validate_config


def config():
    return {
        "slug": "test-shop", "brand_name": "测试品牌", "contact_name": "测试客服",
        "contact_username": "test_service", "venue_ids": [3],
        "status": "active",
        "expires_at": datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
    }


class TenantTests(unittest.TestCase):
    def connection(self, row):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = row
        return conn

    def test_validation_and_draft_default(self):
        validate_config(config())
        draft = {**config(), "status": "draft", "expires_at": None}
        validate_config(draft)
        for fields in [
            {"slug": ""}, {"slug": "../shop"}, {"brand_name": ""},
            {"contact_username": "https://t.me/test"}, {"venue_ids": [7]},
            {"venue_ids": [True]}, {"venue_ids": []}, {"status": "unknown"}, {"status": []},
            {"expires_at": None}, {"expires_at": datetime(2000, 1, 1)},
            {"expires_at": datetime.now(timezone.utc)},
        ]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_config({**config(), **fields})

    def test_missing_suspended_draft_expired_cannot_load(self):
        for row in [None, {**config(), "status": "draft"},
                    {**config(), "status": "suspended"},
                    {**config(), "expires_at": datetime(2000, 1, 1)}]:
            with patch("tenants.get_conn", return_value=self.connection(row)):
                with self.assertRaises(TenantUnavailable):
                    load_tenant("test-shop")

    def test_load_json_and_public_configuration(self):
        row = {**config(), "venue_ids": "[3]"}
        with patch("tenants.get_conn", return_value=self.connection(row)):
            result = load_tenant("test-shop")
        self.assertEqual(result["venue_ids"], [3])
        self.assertNotIn("status", public_config(result))
        self.assertNotIn("expires_at", public_config(result))

    def test_invalid_slug_does_not_query_database(self):
        with patch("tenants.get_conn") as db:
            with self.assertRaises(TenantUnavailable):
                load_tenant("")
            db.assert_not_called()

    def test_main_entry_still_has_no_tenant(self):
        with patch.object(api_server, "load_tenant") as lookup:
            response = api_server.app.test_client().post("/webhook", json={"action": "tenant_config"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"tenant": None})
        lookup.assert_not_called()

    def test_unknown_tenant_cannot_fall_back_to_owner(self):
        with patch.object(api_server, "load_tenant", side_effect=TenantUnavailable("不可用")):
            response = api_server.app.test_client().post(
                "/webhook?tenant=missing", json={"action": "get_venues"}
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_header_query_conflict_is_rejected(self):
        response = api_server.app.test_client().post(
            "/webhook?tenant=other", headers={"X-Tenant": "test-shop"},
            json={"action": "tenant_config"},
        )
        self.assertEqual(response.status_code, 400)

    def test_tenant_config_and_wallet_visit_denials(self):
        client = api_server.app.test_client()
        with patch.object(api_server, "load_tenant", return_value=config()):
            response = client.post("/webhook", headers={"X-Tenant": "test-shop"},
                                   json={"action": "tenant_config"})
            self.assertEqual(response.json["tenant"]["contact_username"], "test_service")
            self.assertIn("X-Tenant", response.headers["Vary"])
            for action in ("wallet_account", "wallet_submit", "track_visit"):
                with self.subTest(action=action):
                    response = client.post("/webhook?tenant=test-shop", json={"action": action})
                    self.assertEqual(response.status_code, 403)

    def test_unselected_venue_and_delivery_media_are_denied(self):
        with patch.object(api_server, "load_tenant", return_value=config()):
            for action, ident in (("get_venue_detail", 1), ("get_technicians", 7)):
                response = api_server.app.test_client().post(
                    "/webhook?tenant=test-shop", json={"action": action, "id": ident}
                )
                self.assertEqual(response.status_code, 403)
            response = api_server.app.test_client().get("/delivery-media/1?tenant=test-shop")
            self.assertEqual(response.status_code, 403)

    def test_media_is_checked_before_cache_or_upstream(self):
        with patch.object(api_server, "load_tenant", return_value=config()), \
                patch.object(api_server, "get_db_connection", return_value=self.connection({"venue_id": 1})), \
                patch.object(api_server, "_telegram_image") as image, \
                patch.object(api_server, "video_cache") as video:
            for path in ("/photos/1/image?thumb=1", "/ktv-videos/1"):
                response = api_server.app.test_client().get(path + "&tenant=test-shop" if "?" in path else path + "?tenant=test-shop")
                self.assertEqual(response.status_code, 403)
            image.assert_not_called()
            video.get.assert_not_called()

    def test_scoped_movies_and_no_store(self):
        with patch.object(api_server, "load_tenant", return_value=config()), \
                patch.object(api_server, "movie_catalog", return_value={"series": []}):
            response = api_server.app.test_client().post(
                "/webhook?tenant=test-shop", json={"action": "get_movies"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_venue_list_filters_before_building_details(self):
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.return_value = [
            {"id": 1}, {"id": 7},
        ]
        with patch.object(api_server, "load_tenant", return_value=config()), \
                patch.object(api_server, "get_db_connection", return_value=conn), \
                patch.object(api_server, "parse_venue", side_effect=lambda row: row):
            response = api_server.app.test_client().post(
                "/webhook?tenant=test-shop", json={"action": "get_venues"}
            )
            self.assertEqual(response.json, {"venues": []})

    def test_database_failure_is_explicit(self):
        with patch.object(api_server, "load_tenant", side_effect=api_server.pymysql.OperationalError("unavailable")):
            response = api_server.app.test_client().post(
                "/webhook?tenant=test-shop", json={"action": "get_venues"}
            )
            self.assertEqual(response.status_code, 503)

    def test_operator_can_create_draft_and_explicitly_activate_or_suspend(self):
        for create, status in ((True, "draft"), (False, "active"), (False, "suspended")):
            conn = self.connection(config())
            cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = [{"id": 3}]
            with self.subTest(create=create, status=status), patch("tenants.get_conn", return_value=conn):
                save_tenant({**config(), "status": status}, create=create)
                conn.commit.assert_called_once()
                sql, values = cur.execute.call_args_list[-2].args
                self.assertIn("INSERT INTO" if create else "UPDATE display_tenants", sql)
                self.assertIn(status, values)
                self.assertIn("tenant_admin_audit", cur.execute.call_args.args[0])

    def test_operator_cannot_assign_missing_venue_or_update_unknown_tenant(self):
        for venues, row in (([], {"slug": "test-shop"}), ([{"id": 3}], None)):
            conn = self.connection(row)
            conn.cursor.return_value.__enter__.return_value.fetchall.return_value = venues
            with patch("tenants.get_conn", return_value=conn), self.assertRaises(ValueError):
                save_tenant(config())
            conn.rollback.assert_called_once()
            conn.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
