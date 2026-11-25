import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

import pymysql
import requests
from telebot import apihelper
from telethon import types

import channel_sync
import customer_records
from db import get_conn
import private_contacts


class PrivateContactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_conn()
        cls.database = "test_private_contacts_" + uuid.uuid4().hex[:16]
        with cls.root.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{cls.database}` CHARACTER SET utf8mb4")
        cls.addClassCleanup(cls.cleanup_database)

        def connect():
            return pymysql.connect(
                host=cls.root.host, port=cls.root.port, user=cls.root.user,
                password=cls.root.password, database=cls.database, charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
            )

        cls.connect = staticmethod(connect)
        patcher = patch.object(private_contacts, "get_conn", side_effect=connect)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        patcher = patch.object(customer_records, "get_conn", side_effect=connect)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        patcher = patch.object(private_contacts, "admin_ids", return_value={9000})
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        customer_records.ensure_tables()
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE bot_users
                    (tg_user_id BIGINT PRIMARY KEY,username VARCHAR(64),display_name VARCHAR(128))""")
                cur.execute("""CREATE TABLE analytics_customers
                    (user_id BIGINT PRIMARY KEY,username VARCHAR(64),display_name VARCHAR(128))""")
                cur.execute("CREATE TABLE venues (id INT PRIMARY KEY,name VARCHAR(128))")
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def cleanup_database(cls):
        with cls.root.cursor() as cur:
            cur.execute(f"DROP DATABASE `{cls.database}`")
        cls.root.close()

    def sql(self, query, args=()):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(query, args)
                result = cur.fetchall() if cur.description else []
            conn.commit()
            return result
        finally:
            conn.close()

    def setUp(self):
        for table in ("crm_private_notices", "crm_private_contacts", "crm_consumptions",
                      "crm_customers", "crm_private_settings", "bot_users", "analytics_customers",
                      "venues"):
            self.sql(f"DELETE FROM {table}")
        self.sql("INSERT INTO crm_private_settings(id,owner_id,enabled,enabled_at) VALUES(1,9000,TRUE,%s)",
                 (datetime(2026, 1, 1),))
        self.now = datetime(2026, 11, 25, 16, 59, tzinfo=timezone.utc)

    def test_create_profile_and_once_per_local_day_without_fabricating_bot_user(self):
        self.assertTrue(private_contacts.record_contact(9000, 42, "customer", "Customer", self.now))
        self.assertFalse(private_contacts.record_contact(9000, 42, "customer", "Customer", self.now))
        self.assertTrue(private_contacts.record_contact(9000, 42, "customer", "Customer",
                                                       self.now + timedelta(minutes=2)))
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM crm_private_notices")[0]["n"], 2)
        self.assertEqual(self.sql("SELECT user_id,telegram_user_id,alias FROM crm_customers"),
                         [{"user_id": 42, "telegram_user_id": 42, "alias": ""}])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM bot_users")[0]["n"], 0)

    def test_bound_offline_profile_is_used_without_merging_or_creating_another(self):
        self.sql("""INSERT INTO crm_customers
            (user_id,telegram_user_id,alias,preferences,avoidances,notes,revision,created_at,updated_at)
            VALUES(-123,42,'offline','','','',1,%s,%s)""",
                 (datetime(2026, 1, 1), datetime(2026, 1, 1)))
        private_contacts.record_contact(9000, 42, "customer", "Customer", self.now)
        self.assertEqual(self.sql("SELECT customer_id FROM crm_private_notices"), [{"customer_id": -123}])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM crm_customers")[0]["n"], 1)

    def test_disabled_or_old_messages_do_not_create_records(self):
        self.assertFalse(private_contacts.record_contact(
            9000, 42, "", "", datetime(2025, 1, 1, tzinfo=timezone.utc)))
        self.sql("UPDATE crm_private_settings SET enabled=FALSE")
        self.assertFalse(private_contacts.record_contact(9000, 42, "", "", self.now))
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM crm_customers")[0]["n"], 0)
        with self.assertRaises(ValueError):
            private_contacts.record_contact(9001, 42, "", "", self.now)

    def test_notice_sends_only_summary_and_three_valid_records_to_authorized_owner(self):
        private_contacts.record_contact(9000, 42, "customer", "Customer", self.now)
        self.sql("UPDATE crm_private_notices SET next_attempt=%s", (datetime(2026, 1, 1),))
        self.sql("INSERT INTO bot_users VALUES(42,'old_username','Old Name')")
        self.sql("UPDATE crm_customers SET notes='private-note',preferences='private-preference'")
        self.sql("INSERT INTO venues VALUES(2,'Venue')")
        for index in range(4):
            self.sql("""INSERT INTO crm_consumptions
                (id,user_id,service_kind,venue_id,service,occurred_at,amount,refunded,status,
                 note,revision,created_at,updated_at)
                VALUES(%s,42,'venue',2,%s,%s,100,10,'valid','private-note',1,%s,%s)""",
                     (f"{index:032x}", f"Service {index}", datetime(2026, 11, 20 + index),
                      datetime(2026, 11, 25), datetime(2026, 11, 25)))
        bot = Mock()
        self.assertTrue(private_contacts.deliver_one(bot, 9000))
        args = bot.send_message.call_args.args
        self.assertEqual(args[0], 9000)
        self.assertIn("有效消费：4 笔", args[1])
        self.assertIn("Customer（@customer）", args[1])
        self.assertNotIn("old_username", args[1])
        self.assertIn("Service 3", args[1])
        self.assertNotIn("Service 0", args[1])
        self.assertNotIn("private-note", args[1])
        self.assertNotIn("private-preference", args[1])
        self.assertEqual(self.sql("SELECT status FROM crm_private_notices"), [{"status": "sent"}])
        self.assertFalse(private_contacts.deliver_one(bot, 9000))
        bot.send_message.assert_called_once()

    def test_no_consumption_and_uncertain_send_are_explicit_and_not_retried(self):
        private_contacts.record_contact(9000, 42, "customer", "Customer", self.now)
        self.sql("UPDATE crm_private_notices SET next_attempt=%s", (datetime(2026, 1, 1),))
        bot = Mock()
        bot.send_message.side_effect = requests.Timeout("unknown")
        with self.assertLogs(private_contacts.logger, level="WARNING"):
            self.assertTrue(private_contacts.deliver_one(bot, 9000))
        self.assertIn("暂无已登记消费记录", bot.send_message.call_args.args[1])
        self.assertEqual(self.sql("SELECT status FROM crm_private_notices"), [{"status": "uncertain"}])
        self.assertFalse(private_contacts.deliver_one(bot, 9000))
        bot.send_message.assert_called_once()

    def test_rate_limit_is_persisted_and_does_not_send_early(self):
        private_contacts.record_contact(9000, 42, "", "Customer", self.now)
        private_contacts.record_contact(9000, 43, "", "Customer", self.now)
        self.sql("UPDATE crm_private_notices SET next_attempt=%s", (datetime(2026, 1, 1),))
        bot = Mock()
        bot.send_message.side_effect = apihelper.ApiTelegramException(
            "sendMessage", None,
            {"error_code": 429, "description": "limited", "parameters": {"retry_after": 60}},
        )
        with self.assertLogs(private_contacts.logger, level="WARNING"):
            private_contacts.deliver_one(bot, 9000)
        row = self.sql("SELECT status,next_attempt FROM crm_private_notices ORDER BY id")[0]
        self.assertEqual(row["status"], "pending")
        self.assertGreater(row["next_attempt"], customer_records._utcnow() + timedelta(seconds=50))
        self.assertFalse(private_contacts.deliver_one(bot, 9000))
        bot.send_message.assert_called_once()
        self.assertGreater(self.sql("SELECT send_not_before FROM crm_private_settings")[0]["send_not_before"],
                           customer_records._utcnow() + timedelta(seconds=50))
        self.sql("UPDATE crm_private_settings SET send_not_before=%s", (datetime(2026, 1, 1),))
        self.sql("UPDATE crm_private_notices SET next_attempt=%s", (datetime(2026, 1, 1),))
        bot.send_message.side_effect = None
        self.assertTrue(private_contacts.deliver_one(bot, 9000))
        self.assertTrue(private_contacts.deliver_one(bot, 9000))
        self.assertEqual(self.sql("SELECT status FROM crm_private_notices ORDER BY id"),
                         [{"status": "sent"}, {"status": "sent"}])

    def test_interrupted_send_is_uncertain_and_disable_cancels_pending(self):
        private_contacts.record_contact(9000, 42, "", "", self.now)
        private_contacts.record_contact(9000, 43, "", "", self.now)
        self.sql("UPDATE crm_private_notices SET status='sending' WHERE telegram_user_id=42")
        private_contacts.recover_uncertain()
        private_contacts.disable()
        self.assertEqual(self.sql("SELECT telegram_user_id,status FROM crm_private_notices ORDER BY telegram_user_id"),
                         [{"telegram_user_id": 42, "status": "uncertain"},
                          {"telegram_user_id": 43, "status": "cancelled"}])

    def test_disable_waits_for_inflight_send_and_blocks_future_sends(self):
        private_contacts.record_contact(9000, 42, "", "", self.now)
        private_contacts.record_contact(9000, 43, "", "", self.now)
        self.sql("UPDATE crm_private_notices SET next_attempt=%s", (datetime(2026, 1, 1),))
        entered, release = threading.Event(), threading.Event()
        bot = Mock()

        def send(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test send was not released")

        bot.send_message.side_effect = send
        with ThreadPoolExecutor(max_workers=2) as pool:
            delivery = pool.submit(private_contacts.deliver_one, bot, 9000)
            try:
                self.assertTrue(entered.wait(5))
                disabling = threading.Event()

                def disable():
                    disabling.set()
                    private_contacts.disable()

                disabled = pool.submit(disable)
                self.assertTrue(disabling.wait(5))
                with self.assertRaises(TimeoutError):
                    disabled.result(timeout=0.2)
            finally:
                release.set()
            self.assertTrue(delivery.result(timeout=5))
            disabled.result(timeout=5)
        self.assertFalse(private_contacts.deliver_one(bot, 9000))
        self.assertEqual(self.sql("SELECT status FROM crm_private_notices ORDER BY telegram_user_id"),
                         [{"status": "sent"}, {"status": "cancelled"}])
        bot.send_message.assert_called_once()

    def test_listener_only_records_new_human_private_messages(self):
        client = Mock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.is_user_authorized = AsyncMock(return_value=True)
        client.get_me = AsyncMock(return_value=types.User(id=9000))
        client.is_connected.side_effect = [True, False]

        def event(sender, *, private=True, chat_id=None, when=None):
            result = Mock()
            result.is_private = private
            result.chat_id = sender.id if chat_id is None else chat_id
            result.message.date = when or datetime.now(timezone.utc) + timedelta(seconds=1)
            result.get_sender = AsyncMock(return_value=sender)
            return result

        async def exercise(_seconds):
            callback = client.add_event_handler.call_args.args[0]
            await callback(event(types.User(id=41), private=False))
            await callback(event(types.User(id=42), when=datetime(2025, 1, 1, tzinfo=timezone.utc)))
            await callback(event(types.User(id=43, bot=True)))
            await callback(event(types.User(id=9000)))
            await callback(event(types.User(id=44), chat_id=45))
            await callback(event(types.User(id=46, first_name="Customer", username="customer")))

        with patch("telethon.TelegramClient", return_value=client), \
             patch.object(channel_sync, "state_directory", return_value=Path(".")), \
             patch.object(channel_sync, "single_instance", return_value=nullcontext()), \
             patch.object(channel_sync, "credentials", return_value=(1, "fake")), \
             patch.object(private_contacts, "deliver_one", return_value=False), \
             patch.object(private_contacts.asyncio, "sleep", side_effect=exercise):
            asyncio.run(private_contacts._listen(Mock(), 9000))
        self.assertEqual(self.sql("SELECT telegram_user_id FROM crm_private_contacts"),
                         [{"telegram_user_id": 46}])
        client.disconnect.assert_awaited_once()

    def test_send_requires_current_administrator_authorization(self):
        private_contacts.record_contact(9000, 42, "", "", self.now)
        bot = Mock()
        with patch.object(private_contacts, "admin_ids", return_value=set()):
            self.assertFalse(private_contacts.deliver_one(bot, 9000))
        bot.send_message.assert_not_called()
        self.assertEqual(self.sql("SELECT status FROM crm_private_notices"), [{"status": "pending"}])

    def test_changed_profile_binding_cannot_send_wrong_customers_data(self):
        private_contacts.record_contact(9000, 42, "", "", self.now)
        self.sql("UPDATE crm_private_notices SET next_attempt=%s", (datetime(2026, 1, 1),))
        self.sql("UPDATE crm_customers SET telegram_user_id=43 WHERE user_id=42")
        bot = Mock()
        with self.assertRaises(ValueError):
            private_contacts.deliver_one(bot, 9000)
        bot.send_message.assert_not_called()
        self.assertEqual(self.sql("SELECT status FROM crm_private_notices"), [{"status": "pending"}])

    def test_listener_refuses_a_different_logged_in_account(self):
        client = Mock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.is_user_authorized = AsyncMock(return_value=True)
        client.get_me = AsyncMock(return_value=types.User(id=9001))
        with patch("telethon.TelegramClient", return_value=client), \
             patch.object(channel_sync, "state_directory", return_value=Path(".")), \
             patch.object(channel_sync, "single_instance", return_value=nullcontext()), \
             patch.object(channel_sync, "credentials", return_value=(1, "fake")):
            with self.assertRaises(RuntimeError):
                asyncio.run(private_contacts._listen(Mock(), 9000))
        client.add_event_handler.assert_not_called()
        client.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
