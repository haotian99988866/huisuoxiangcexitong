import json
import os
import unittest
import uuid
from unittest.mock import patch

import pymysql

import api_server
from config.channels import STAFF_ARCHIVE_VENUES
from db import get_conn
from customer_records import RecordConflict
import finance
from mingxi_complete_attendance import build_complete_attendance_html
import price_catalog
import price_management
from venue_pricing import PRICES, mingxi_price_options, staff_price
from tests.test_admin_console import signed


class PriceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = patch.dict(os.environ, {"VENUE_PRICE_SAVE_ENABLED": "1"})
        env.start()
        cls.addClassCleanup(env.stop)
        cls.root = get_conn()
        cls.database = "test_prices_" + uuid.uuid4().hex[:16]
        with cls.root.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{cls.database}` CHARACTER SET utf8mb4")
        cls.addClassCleanup(cls.cleanup_database)

        def connect():
            return pymysql.connect(host=cls.root.host, port=cls.root.port, user=cls.root.user,
                                   password=cls.root.password, database=cls.database, charset="utf8mb4",
                                   cursorclass=pymysql.cursors.DictCursor)

        cls.connect = staticmethod(connect)
        patcher = patch.object(price_catalog, "get_conn", side_effect=connect)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        patcher = patch.object(price_management, "get_conn", side_effect=connect)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        patcher = patch.object(finance, "get_conn", side_effect=connect)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        price_catalog.ensure_tables()
        finance.ensure_tables()
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE venues (id INT PRIMARY KEY,name VARCHAR(128),status VARCHAR(16))")
                cur.executemany("INSERT INTO venues VALUES(%s,%s,'active')",
                                [(i, f"venue {i}") for i in (1, 2, 4, 5, 6, 7, 8, 9)])
                cur.execute("""CREATE TABLE service_items (
                    id INT AUTO_INCREMENT PRIMARY KEY,venue_id INT NOT NULL,
                    name VARCHAR(160) NOT NULL,price DECIMAL(12,2) NULL)""")
                services = [(price_management._MASSAGE_NAMES.get(key, name), price)
                            for venue, key, name, _amount, price in finance.COMMISSION_CATALOG
                            if venue == 1]
                cur.executemany("INSERT INTO service_items(venue_id,name,price) VALUES(1,%s,%s)",
                                services)
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def cleanup_database(cls):
        with cls.root.cursor() as cur:
            cur.execute(f"DROP DATABASE `{cls.database}`")
        cls.root.close()

    def test_initial_catalog_preserves_current_prices_and_never_resets_changes(self):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE venue_price_catalog SET revision=1,catalog_json=%s WHERE id=1",
                            (json.dumps(price_catalog.initial_catalog(), ensure_ascii=False),))
            conn.commit()
        finally:
            conn.close()
        price_catalog.ensure_tables()
        catalog = price_catalog.current_catalog()
        for key, value in PRICES.items():
            self.assertEqual(catalog[key], value)
        self.assertEqual(catalog["deliveryPrices"], {"7": 170, "8": 180, "9": 160})
        catalog["mingxiBoardPrices"][0]["half"] = 145
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE venue_price_catalog SET revision=2,catalog_json=%s WHERE id=1",
                            (json.dumps(catalog, ensure_ascii=False),))
            conn.commit()
        finally:
            conn.close()
        price_catalog.ensure_tables()
        self.assertEqual(price_catalog.current_catalog()["mingxiBoardPrices"][0]["half"], 145)

    def test_updated_snapshot_is_used_for_staff_prices_and_mingxi_heading(self):
        catalog = price_catalog.initial_catalog()
        catalog["mingxiBoardPrices"][0]["half"] = 145
        catalog["kaidiTechnicianCategories"][0]["price"] = 108
        self.assertEqual(mingxi_price_options(["board:1号牌"], catalog)[0]["price_usd"], "145")
        self.assertEqual(staff_price(4, "530", [], [], catalog)[0], 108)
        attendance = {"attendance_groups": [
            {"group_type": "roster", "group_name": "1号牌", "technician_codes": ["123"]}
        ]}
        text = build_complete_attendance_html("名玺", attendance, -1001, catalog=catalog)
        self.assertIn("半$145", text)

    def test_saving_price_and_personal_commission_is_atomic_and_versioned(self):
        before = price_management.overview(101)
        other = price_management.overview(102)
        row = next(item for item in before["items"] if item["venue_id"] == 4 and item["service_name"] == "5牌")
        change = {"venue_id": 4, "service_key": row["service_key"], "old_price": row["price"],
                  "old_commission": row["commission_amount"], "price": "123.00", "commission": "12.00"}
        saved = price_management.save({"revision": before["revision"], "changes": [change], "confirmed": True}, 101)
        after = price_management.overview(101)
        updated = next(item for item in after["items"] if item["venue_id"] == 4 and item["service_name"] == "5牌")
        other_after = price_management.overview(102)
        other_row = next(item for item in other_after["items"] if item["venue_id"] == 4 and item["service_name"] == "5牌")
        other_before = next(item for item in other["items"] if item["venue_id"] == 4 and item["service_name"] == "5牌")
        self.assertEqual(saved["revision"], before["revision"] + 1)
        self.assertEqual((updated["price"], updated["commission_amount"]), ("123.00", "12.00"))
        self.assertEqual(other_row["price"], "123.00")
        self.assertEqual(other_row["commission_amount"], other_before["commission_amount"])
        self.assertEqual(staff_price(4, "530", [], [], price_catalog.current_catalog())[0], 123)
        finance_item = next(item for item in finance.commission_catalog(102)
                            if item["venue_id"] == 4 and item["service_name"] == "5牌")
        self.assertEqual(finance_item["listed_price"], "123.00")
        with self.assertRaises(RecordConflict):
            price_management.save({"revision": before["revision"], "changes": [change], "confirmed": True}, 101)

    def test_stale_commission_and_unconfirmed_changes_do_not_update_prices(self):
        before = price_management.overview(103)
        row = next(item for item in before["items"] if item["venue_id"] == 7)
        change = {"venue_id": 7, "service_key": row["service_key"], "old_price": row["price"],
                  "old_commission": "999.00", "price": "175.00"}
        with self.assertRaises(RecordConflict):
            price_management.save({"revision": before["revision"], "changes": [change], "confirmed": True}, 103)
        self.assertEqual(price_management.overview(103)["revision"], before["revision"])
        with self.assertRaises(ValueError):
            price_management.save({"revision": before["revision"], "changes": [change]}, 103)

    def test_saving_is_disabled_until_explicitly_enabled(self):
        with patch.dict(os.environ, {"VENUE_PRICE_SAVE_ENABLED": "0"}):
            with self.assertRaisesRegex(ValueError, "暂未开放"):
                price_management.save({}, 103)

    def test_price_save_waits_for_channel_worker_before_creating_review(self):
        before = price_management.overview(104)
        row = next(item for item in before["items"] if item["venue_id"] == 4)
        change = {"venue_id": 4, "service_key": row["service_key"], "old_price": row["price"],
                  "old_commission": row["commission_amount"], "price": "123.45"}
        lock_name = f"{STAFF_ARCHIVE_VENUES[4]['table_prefix']}_channel"
        holder = self.connect()
        try:
            with holder.cursor() as cur:
                cur.execute("SELECT GET_LOCK(%s,0) AS acquired", (lock_name,))
                self.assertEqual(cur.fetchone()["acquired"], 1)
            with self.assertRaises(RecordConflict):
                price_management.save({"revision": before["revision"], "changes": [change],
                                       "confirmed": True}, 104)
            self.assertEqual(price_management.overview(104)["revision"], before["revision"])
        finally:
            holder.close()
        result = price_management.save({"revision": before["revision"], "changes": [change],
                                        "confirmed": True}, 104)
        self.assertIn(4, result["channel_review_venues"])
        self.assertTrue(any(review["venue_id"] == 4 and review["revision"] == result["revision"]
                            for review in price_management.overview(104)["channel_reviews"]))


class PriceAccessTests(unittest.TestCase):
    def test_price_actions_require_platform_admin_and_are_private(self):
        client = api_server.app.test_client()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token", "ADMIN_USER_IDS": "123"}), \
                patch.object(price_management, "overview", return_value={"items": []}) as overview, \
                patch.object(price_management, "save", return_value={"ok": True}) as save:
            for action in ("admin_price_overview", "admin_price_save"):
                for identity, expected in ((None, 401), (signed(456), 403),
                                           (signed(123, start_param="tenant_test-shop"), 403),
                                           (signed(123), 200)):
                    response = client.post("/webhook", json={"action": action, "init_data": identity})
                    self.assertEqual(response.status_code, expected)
                    if expected == 200:
                        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            overview.assert_called_once_with(123)
            self.assertEqual(save.call_args.args[1], 123)


if __name__ == "__main__":
    unittest.main()
