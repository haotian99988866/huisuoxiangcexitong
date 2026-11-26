import base64
import json
import os
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

import pymysql
from telebot import apihelper

import api_server
from config.channels import STAFF_ARCHIVE_VENUES
from db import get_conn
from customer_records import RecordConflict
import finance
import footer
import massage_channel
from mingxi_complete_attendance import build_complete_attendance_html
import price_catalog
import price_management
import price_sync
import staff_archive
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
        patcher = patch.object(price_sync, "get_conn", side_effect=connect)
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
        with patch.object(price_catalog, "LISTEN_CHANNELS", {}):
            price_catalog.ensure_tables()
        self.assertEqual(price_catalog.current_catalog()["mingxiBoardPrices"][0]["half"], 145)

    def test_initial_delivery_prices_come_from_existing_channel_configuration(self):
        config = {
            "a": {"venue_id": 7, "venue_name": "西港175💲外送"},
            "b": {"venue_id": 8, "venue_name": "西港180💲外送"},
            "c": {"venue_id": 9, "venue_name": "西港160💲外送"},
        }
        with patch.object(price_catalog, "LISTEN_CHANNELS", config):
            self.assertEqual(price_catalog.initial_catalog()["deliveryPrices"]["7"], 175)
        with patch.object(price_catalog, "LISTEN_CHANNELS", {}):
            with self.assertRaisesRegex(ValueError, "初始价目不完整"):
                price_catalog.initial_catalog()

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

    def test_price_save_queues_sync_without_waiting_for_archive_worker(self):
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
            result = price_management.save({"revision": before["revision"], "changes": [change],
                                             "confirmed": True}, 104)
        finally:
            holder.close()
        self.assertIn(4, result["channel_sync_venues"])
        self.assertTrue(any(task["venue_id"] == 4 and task["revision"] == result["revision"]
                            and task["status"] == "pending"
                            for task in price_management.overview(104)["channel_sync"]))

    def test_worker_processes_saved_price_and_reports_completion(self):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM venue_price_channel_review")
            conn.commit()
        finally:
            conn.close()
        before = price_management.overview(105)
        row = next(item for item in before["items"] if item["venue_id"] == 2 and item["service_name"] == "1号牌·半价")
        change = {"venue_id": 2, "service_key": row["service_key"], "old_price": row["price"],
                  "old_commission": row["commission_amount"], "price": "153.75"}
        saved = price_management.save({"revision": before["revision"], "changes": [change], "confirmed": True}, 105)
        bot = Mock()
        with patch.object(price_sync, "sync_venue") as sync:
            self.assertTrue(price_sync.run_once(bot))
        sync.assert_called_once_with(bot, 2, [{"service_key": row["service_key"],
                                               "service_name": "1号牌·半价"}])
        task = next(item for item in price_management.overview(105)["channel_sync"]
                    if item["revision"] == saved["revision"] and item["venue_id"] == 2)
        self.assertEqual(task["status"], "done")

    def test_failed_sync_can_be_retried_without_changing_price(self):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM venue_price_channel_review")
            conn.commit()
        finally:
            conn.close()
        before = price_management.overview(106)
        row = next(item for item in before["items"] if item["venue_id"] == 2 and item["service_name"] == "1号牌·会员")
        change = {"venue_id": 2, "service_key": row["service_key"], "old_price": row["price"],
                  "old_commission": row["commission_amount"], "price": "275.00"}
        saved = price_management.save({"revision": before["revision"], "changes": [change], "confirmed": True}, 106)
        task_id = {"revision": saved["revision"], "venue_id": 2}
        with patch.object(price_sync, "sync_venue", side_effect=price_sync.SyncFailure("消息 100 更新失败")):
            self.assertTrue(price_sync.run_once(Mock()))
        task = next(item for item in price_management.overview(106)["channel_sync"]
                    if item["revision"] == saved["revision"] and item["venue_id"] == 2)
        self.assertEqual((task["status"], task["reason"]), ("failed", "消息 100 更新失败"))
        self.assertFalse(price_sync.run_once(Mock()))
        price_management.retry_sync(task_id)
        with patch.object(price_sync, "sync_venue") as sync:
            self.assertTrue(price_sync.run_once(Mock()))
        sync.assert_called_once()

    def test_delivery_price_and_commission_only_do_not_queue_channel_sync(self):
        before = price_management.overview(107)
        delivery = next(item for item in before["items"] if item["venue_id"] == 7)
        change = {"venue_id": 7, "service_key": delivery["service_key"], "old_price": delivery["price"],
                  "old_commission": delivery["commission_amount"], "price": "175.00"}
        result = price_management.save({"revision": before["revision"], "changes": [change], "confirmed": True}, 107)
        self.assertEqual(result["channel_sync_venues"], [])
        before = price_management.overview(107)
        row = next(item for item in before["items"] if item["venue_id"] == 4)
        change = {"venue_id": 4, "service_key": row["service_key"], "old_price": row["price"],
                  "old_commission": row["commission_amount"], "commission": "12.34"}
        result = price_management.save({"revision": before["revision"], "changes": [change], "confirmed": True}, 107)
        self.assertEqual(result["channel_sync_venues"], [])


class PriceAccessTests(unittest.TestCase):
    def test_price_actions_require_platform_admin_and_are_private(self):
        client = api_server.app.test_client()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token", "ADMIN_USER_IDS": "123"}), \
                patch.object(price_management, "overview", return_value={"items": []}) as overview, \
                patch.object(price_management, "save", return_value={"ok": True}) as save, \
                patch.object(price_management, "retry_sync", return_value={"ok": True}) as retry:
            for action in ("admin_price_overview", "admin_price_save", "admin_price_sync_retry"):
                for identity, expected in ((None, 401), (signed(456), 403),
                                           (signed(123, start_param="tenant_test-shop"), 403),
                                           (signed(123), 200)):
                    response = client.post("/webhook", json={"action": action, "init_data": identity})
                    self.assertEqual(response.status_code, expected)
                    if expected == 200:
                        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            overview.assert_called_once_with(123)
            self.assertEqual(save.call_args.args[1], 123)
            retry.assert_called_once()

    def test_native_download_requires_admin_and_temporary_link_expires(self):
        import price_download

        client = api_server.app.test_client()
        encoded = base64.b64encode(b"PK\x03\x04test spreadsheet").decode()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token", "ADMIN_USER_IDS": "123"}), \
                patch.object(price_download.time, "monotonic", return_value=100):
            payload = {"action": "admin_price_download", "content": encoded}
            self.assertEqual(client.post("/webhook", json=payload).status_code, 401)
            self.assertEqual(client.post("/webhook", json={**payload, "init_data": signed(456)}).status_code, 403)
            response = client.post("/webhook", json={**payload, "init_data": signed(123)})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            path = "/price-download/" + response.json["token"]
            download = client.get(path)
            self.assertEqual(download.data, b"PK\x03\x04test spreadsheet")
            self.assertEqual(download.headers["Cache-Control"], "private, no-store")
            self.assertIn("attachment", download.headers["Content-Disposition"])
            invalid = client.post("/webhook", json={**payload, "content": "bad", "init_data": signed(123)})
            self.assertEqual(invalid.status_code, 400)
        with patch.object(price_download.time, "monotonic", return_value=281):
            self.assertEqual(client.get(path).status_code, 404)


class PriceSyncTests(unittest.TestCase):
    @patch.object(price_sync.time, "sleep")
    def test_edit_obeys_telegram_retry_after(self, sleep):
        error = apihelper.ApiTelegramException(
            "editMessageCaption", Mock(), {"error_code": 429, "description": "Too Many Requests",
                                          "parameters": {"retry_after": 25}},
        )
        send = Mock(side_effect=[error, True])
        price_sync._edit(send, 42)
        self.assertEqual(send.call_count, 2)
        self.assertIn(26, [call.args[0] for call in sleep.call_args_list])

    @patch.object(price_sync.time, "sleep")
    @patch.object(footer, "_get_last", return_value=None)
    @patch.object(staff_archive, "listing")
    @patch.object(price_sync, "_rows")
    @patch.object(price_sync, "get_conn")
    def test_club_updates_affected_photos_using_current_archive_price(self, get_conn, rows, listing, _footer, _sleep):
        members = [
            {"id": 1, "code": "530", "group_name": "5牌", "price_usd": "123.00",
             "price_detail": "5牌 · 90分钟", "cover": 1},
            {"id": 2, "code": "628", "group_name": "6牌", "price_usd": "159",
             "price_detail": "6牌 · 90分钟", "cover": 2},
        ]
        listing.return_value = {"members": members}
        rows.side_effect = [
            [{"staff_id": 1, "message_id": 101}, {"staff_id": 2, "message_id": 102}],
            [{"staff_id": 1, "message_id": 101}, {"staff_id": 2, "message_id": 102}],
            [{"page_number": 1, "message_id": 201}],
        ]
        get_conn.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = {"acquired": 1}
        bot = Mock()
        bot.get_me.return_value = SimpleNamespace(username="example_bot")
        bot.edit_message_caption.return_value = SimpleNamespace(message_id=101)
        bot.edit_message_text.return_value = SimpleNamespace(message_id=201)
        price_sync._club(bot, 4, [{"service_name": "5牌"}])
        bot.edit_message_caption.assert_called_once()
        self.assertIn("$123.00", bot.edit_message_caption.call_args.args[0])
        self.assertEqual(bot.edit_message_caption.call_args.args[2], 101)
        bot.edit_message_text.assert_called_once()

    @patch.object(price_sync.time, "sleep")
    @patch.object(massage_channel, "_state_message_id", return_value=None)
    @patch.object(massage_channel, "staff_caption", return_value="工号：1\n价格：75美元")
    @patch.object(massage_channel, "_staff_services")
    @patch.object(price_sync, "_rows")
    def test_massage_updates_only_people_using_changed_service(self, rows, services, _caption, _state, _sleep):
        rows.return_value = [{"staff_id": 1, "code": "001", "msg_id": 101},
                             {"staff_id": 2, "code": "002", "msg_id": 102}]
        services.side_effect = [[{"name": "肩颈按摩"}], [{"name": "其他项目"}]]
        bot = Mock()
        bot.edit_message_caption.return_value = SimpleNamespace(message_id=101)
        price_sync._massage(bot, [{"service_key": "custom", "service_name": "肩颈按摩"}])
        bot.edit_message_caption.assert_called_once()
        self.assertEqual(bot.edit_message_caption.call_args.args[2], 101)


if __name__ == "__main__":
    unittest.main()
