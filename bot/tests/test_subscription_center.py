from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import uuid

import pymysql

import subscription_center as center
from customer_records import RecordConflict
from db import get_conn


class SubscriptionCenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_conn()
        cls.database = "test_subscriptions_" + uuid.uuid4().hex[:16]
        with cls.root.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{cls.database}` CHARACTER SET utf8mb4")
        cls.addClassCleanup(cls.cleanup)

        def connect():
            return pymysql.connect(host=cls.root.host, port=cls.root.port, user=cls.root.user,
                                   password=cls.root.password, database=cls.database, charset="utf8mb4",
                                   cursorclass=pymysql.cursors.DictCursor)
        cls.connect = staticmethod(connect)
        patcher = patch.object(center, "get_conn", side_effect=connect)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        center.ensure_tables()
        conn = connect()
        try:
            with conn.cursor() as cur:
                for statement in (
                    "CREATE TABLE venues(id INT PRIMARY KEY,name VARCHAR(80))",
                    "CREATE TABLE bot_subscriptions(tg_user_id BIGINT,venue_id INT,"
                    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(tg_user_id,venue_id))",
                    "CREATE TABLE news_subscriptions(user_id BIGINT PRIMARY KEY,tenant_slug VARCHAR(40),active INT,start_id INT)",
                    "CREATE TABLE channel_announcements(id INT PRIMARY KEY)",
                    "CREATE TABLE news_deliveries(user_id BIGINT,status VARCHAR(20),error VARCHAR(80))",
                    "CREATE TABLE staff_members(id INT PRIMARY KEY,venue_id INT,code VARCHAR(20))",
                    "CREATE TABLE delivery_people(id INT PRIMARY KEY,venue_id INT,display_name VARCHAR(80))",
                    "CREATE TABLE staff_customer_subscriptions(staff_id INT,user_id BIGINT,enabled INT,revision INT)",
                    "CREATE TABLE delivery_customer_subscriptions(person_id INT,user_id BIGINT,enabled INT,revision INT)",
                ):
                    cur.execute(statement)
                cur.execute("INSERT INTO venues VALUES(1,'会所'),(7,'外送')")
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def cleanup(cls):
        with cls.root.cursor() as cur:
            cur.execute(f"DROP DATABASE `{cls.database}`")
        cls.root.close()

    def sql(self, statement, args=()):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(statement, args)
                rows = list(cur.fetchall()) if cur.description else []
            conn.commit()
            return rows
        finally:
            conn.close()

    def setUp(self):
        for table in ("subscription_center_requests", "delivery_scope_subscriptions", "delivery_scope_notifications",
                      "bot_subscriptions", "news_subscriptions", "channel_announcements", "news_deliveries",
                      "staff_customer_subscriptions", "delivery_customer_subscriptions", "staff_members", "delivery_people"):
            self.sql(f"DELETE FROM {table}")

    def operation(self, kind="delivery", ident=7, enabled=True):
        return {"kind": kind, "id": ident, "enabled": enabled, "request_id": uuid.uuid4().hex}

    def test_scope_multi_subscription_personal_details_and_cancellation(self):
        for payload in (self.operation("venue", 1), self.operation(), self.operation("news", 0)):
            center.save(payload, 123)
        self.sql("INSERT INTO staff_members VALUES(12,1,'012'),(13,1,'013')")
        self.sql("INSERT INTO delivery_people VALUES(14,7,'档案')")
        self.sql("INSERT INTO staff_customer_subscriptions VALUES(12,123,1,2),(13,456,1,1)")
        self.sql("INSERT INTO delivery_customer_subscriptions VALUES(14,123,1,1)")
        result = center.catalog(123)
        self.assertEqual(sum(item["subscribed"] for item in result["scopes"]), 3)
        self.assertEqual({(item["kind"], item["id"]) for item in result["subscribed_people"]},
                         {("staff", 12), ("person", 14)})
        self.assertFalse(any(item["subscribed"] for item in center.catalog(456)["scopes"]))
        center.save(self.operation(enabled=False), 123)
        self.assertFalse(next(item["subscribed"] for item in center.catalog(123)["scopes"] if item["kind"] == "delivery"))

    def test_retry_preserves_baseline_and_never_replays_old_request_after_cancel(self):
        payload = self.operation()
        center.save(payload, 123)
        first = self.sql("SELECT * FROM delivery_scope_subscriptions")
        center.save(payload, 123)
        self.assertEqual(self.sql("SELECT * FROM delivery_scope_subscriptions"), first)
        center.save(self.operation(enabled=False), 123)
        center.save(payload, 123)
        self.assertEqual(self.sql("SELECT * FROM delivery_scope_subscriptions"), [])
        with self.assertRaises(RecordConflict):
            center.save(payload, 456)

    def test_news_uses_existing_baseline_and_cancels_queued_deliveries(self):
        self.sql("INSERT INTO channel_announcements VALUES(101)")
        center.save(self.operation("news", 0), 123)
        self.sql("INSERT INTO channel_announcements VALUES(102)")
        center.save(self.operation("news", 0), 123)
        self.assertEqual(self.sql("SELECT start_id FROM news_subscriptions"), [{"start_id": 101}])
        self.sql("INSERT INTO news_deliveries VALUES(123,'queued',NULL),(456,'queued',NULL)")
        center.save(self.operation("news", 0, False), 123)
        rows = self.sql("SELECT user_id,status FROM news_deliveries ORDER BY user_id")
        self.assertEqual(rows, [{"user_id": 123, "status": "skipped"}, {"user_id": 456, "status": "queued"}])

    def test_people_use_existing_revision_and_idempotency_engine(self):
        for kind, delivery in (("staff", False), ("person", True)):
            payload = self.operation(kind, 12)
            payload["revision"] = 3
            with patch("staff_customer_duty.save", return_value={"subscribed": True}) as save:
                center.save(payload, 123)
            self.assertEqual(save.call_args.kwargs, {"delivery": delivery})
            self.assertEqual(save.call_args.args[0]["revision"], 3)
            self.assertEqual(save.call_args.args[1], 123)

    def test_catalog_selection_uses_public_eligibility_states(self):
        self.sql("INSERT INTO staff_members VALUES(12,1,'012'),(13,1,'hidden')")
        with patch("staff_customer_duty.catalog_status", return_value={
            "states": {"12": {"subscribed": False, "revision": 0, "available": True, "published_at": None, "cover": 101}},
        }) as status:
            result = center.catalog(123, 1)
        self.assertEqual([item["id"] for item in result["people"]], [12])
        self.assertEqual(result["people"][0]["cover"], 101)
        status.assert_called_once_with(1, 123)

    def test_invalid_scope_never_modifies_subscription(self):
        for kind, ident in (("venue", 7), ("delivery", 1), ("news", 1), ("other", 0), ([], 0), (True, 0)):
            with self.assertRaises(ValueError):
                center.save(self.operation(kind, ident), 123)
        self.assertEqual(self.sql("SELECT * FROM subscription_center_requests"), [])


class SubscriptionEndpointTests(unittest.TestCase):
    def test_identity_must_be_signed_and_cannot_be_supplied_in_payload(self):
        from api_server import app
        client = app.test_client()
        with patch("admin_console.authenticate", return_value=None), patch.object(center, "catalog") as catalog:
            response = client.post("/webhook", json={"action": "subscription_catalog", "user_id": 456})
        self.assertEqual(response.status_code, 401)
        catalog.assert_not_called()
        with patch("admin_console.authenticate", return_value={"id": 123}), \
                patch("admin_console.is_tenant_session", return_value=False), \
                patch.object(center, "catalog", return_value={}) as catalog:
            response = client.post("/webhook", json={
                "action": "subscription_catalog", "init_data": "signed", "user_id": 456, "venue_id": 1})
        self.assertEqual(response.status_code, 200)
        catalog.assert_called_once_with(123, 1)

    def test_tenant_sessions_cannot_modify_personal_subscriptions(self):
        from api_server import app
        with patch("admin_console.authenticate", return_value={"id": 123}), \
                patch("admin_console.is_tenant_session", return_value=True), patch.object(center, "save") as save:
            result = app.test_client().post("/webhook", json={"action": "subscription_save", "init_data": "tenant"})
        self.assertEqual(result.status_code, 403)
        save.assert_not_called()


class DeliveryScopeNotificationTests(unittest.TestCase):
    def test_publication_requires_new_public_adult_photo_after_subscription(self):
        from tests.test_delivery_customer_duty import media, START, END
        cur = MagicMock()
        for rows, expected in (([media()], True), ([media(age=17)], False),
                               ([media(kind="video")], False), ([media(created_at=START)], False)):
            cur.fetchall.return_value = rows
            with self.subTest(expected=expected):
                self.assertEqual(center.delivery_publication(cur, 7, START, END, START), expected)

    def test_daytime_never_notifies(self):
        with patch.object(center, "current_shift_window", return_value=(datetime.now(), datetime.now(), "白班")), \
                patch.object(center, "get_conn") as conn:
            center.deliver_scopes(Mock())
        conn.assert_not_called()

    def test_valid_night_publication_claims_before_send_and_rechecks_cancellation(self):
        start = datetime(2026, 11, 6, 18)
        window = (start, start + timedelta(hours=12), "晚班")
        for cancelled in (False, True):
            conn = MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = [{"venue_id": 7, "user_id": 123, "name": "外送", "subscribed_at": start}]
            cur.fetchone.side_effect = [{"acquired": 1}, None,
                                       None if cancelled else {"subscribed_at": start}]
            cur.rowcount = 1
            bot = Mock()
            with patch.object(center, "get_conn", return_value=conn), \
                    patch.object(center, "current_shift_window", return_value=window), \
                    patch.object(center, "delivery_publication", return_value=True):
                center.deliver_scopes(bot)
            self.assertEqual(bot.send_message.call_count, 0 if cancelled else 1)
            if not cancelled:
                queries = [call.args[0] for call in cur.execute.call_args_list]
                self.assertTrue(any("'sending'" in query for query in queries))
            conn.close.assert_called_once()

    def test_rate_limit_queues_with_actual_wait_and_unknown_send_is_not_resent(self):
        from telebot import apihelper
        start = datetime(2026, 11, 6, 18)
        for failure, expected in (
            (apihelper.ApiTelegramException("sendMessage", None, {
                "error_code": 429, "description": "limited", "parameters": {"retry_after": 60}}), "queued"),
            (ConnectionError("offline"), "unconfirmed"),
        ):
            conn = MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = [{"venue_id": 7, "user_id": 123, "name": "外送", "subscribed_at": start}]
            cur.fetchone.side_effect = [{"acquired": 1}, None, {"subscribed_at": start}]
            cur.rowcount = 1
            bot = Mock()
            bot.send_message.side_effect = failure
            with patch.object(center, "get_conn", return_value=conn), \
                    patch.object(center, "current_shift_window", return_value=(start, start + timedelta(hours=12), "晚班")), \
                    patch.object(center, "delivery_publication", return_value=True), self.assertLogs("subscription_center"):
                center.deliver_scopes(bot)
            updates = [call for call in cur.execute.call_args_list if call.args[0].startswith("UPDATE")]
            if expected == "queued":
                self.assertIn("status='queued',retry_at=%s", updates[-1].args[0])
                self.assertGreater(updates[-1].args[1][0], datetime.now() + timedelta(seconds=55))
            else:
                self.assertEqual(updates[-1].args[1][0], "unconfirmed")
