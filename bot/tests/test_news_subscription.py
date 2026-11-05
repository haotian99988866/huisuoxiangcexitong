from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import pymysql
from telebot import apihelper

import announcements
import news_subscription as news
import tenants
from db import get_conn


class NewsSubscriptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_conn()
        cls.database = "test_news_" + uuid.uuid4().hex[:16]
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
        for module in (news, announcements, tenants):
            patcher = patch.object(module, "get_conn", side_effect=connect)
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        tenants.ensure_tables()
        announcements.ensure_tables()
        news.ensure_tables()

    @classmethod
    def cleanup_database(cls):
        with cls.root.cursor() as cur:
            cur.execute(f"DROP DATABASE `{cls.database}`")
        cls.root.close()

    def setUp(self):
        self.sql("DELETE FROM news_deliveries")
        self.sql("DELETE FROM news_subscriptions")
        self.sql("DELETE FROM channel_announcements")
        self.sql("DELETE FROM display_tenants")
        self.bot = Mock()
        self.bot.send_message.return_value = SimpleNamespace(message_id=77)

    def sql(self, sql, args=()):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                rows = cur.fetchall() if cur.description else cur.lastrowid
            conn.commit()
            return rows
        finally:
            conn.close()

    def article(self, body="#新闻：测试标题\n测试正文"):
        return self.sql("INSERT INTO channel_announcements"
                        " (job_key,message_ids,body,media,published_at) VALUES (%s,'[]',%s,'[]',%s)",
                        (uuid.uuid4().hex, body, datetime.now(timezone.utc).replace(tzinfo=None)))

    def tenant(self):
        self.sql("INSERT INTO display_tenants"
                 " (slug,brand_name,contact_name,contact_username,venue_ids,status,expires_at,announcements_enabled)"
                 " VALUES ('sample','测试','测试','test_user','[1]','active',DATE_ADD(UTC_TIMESTAMP(),INTERVAL 1 DAY),1)")

    def pending(self):
        news.enqueue_news()
        return news.claim_delivery()

    def test_opt_in_has_no_historical_push_and_is_idempotent(self):
        self.article()
        news.subscribe(123)
        self.assertIsNone(self.pending())
        ident = self.article()
        news.subscribe(123)
        row = self.pending()
        self.assertEqual(row["announcement_id"], ident)
        news.enqueue_news()
        self.assertIsNone(news.claim_delivery())
        self.assertEqual(len(self.sql("SELECT * FROM news_deliveries")), 1)

    def test_unsubscribe_skips_pending_and_resubscribe_does_not_catch_up(self):
        news.subscribe(123)
        self.article()
        news.enqueue_news()
        news.unsubscribe(123)
        self.assertFalse(news.subscription(123)["active"])
        self.assertIsNone(news.claim_delivery())
        news.subscribe(123)
        self.assertIsNone(self.pending())
        self.article()
        self.assertIsNotNone(self.pending())

    def test_disabled_news_never_pushed(self):
        news.subscribe(123)
        ident = self.article()
        news.enqueue_news()
        self.sql("UPDATE channel_announcements SET enabled=0 WHERE id=%s", (ident,))
        self.assertTrue(news.claim_delivery()["skipped"])
        self.bot.send_message.assert_not_called()

    def test_tenant_revocation_blocks_enqueue_and_pending_delivery(self):
        self.tenant()
        news.subscribe(123, "sample")
        self.article()
        row = self.pending()
        self.sql("UPDATE display_tenants SET announcements_enabled=0")
        news.send_delivery(self.bot, row)
        self.bot.send_message.assert_not_called()
        self.assertEqual(self.sql("SELECT status FROM news_deliveries")[0]["status"], "skipped")
        with self.assertRaises(tenants.TenantUnavailable):
            news.subscribe(123, "sample")
        self.article()
        self.assertIsNone(self.pending())
        news.unsubscribe(123)

    def test_expired_tenant_is_not_queued(self):
        self.tenant()
        news.subscribe(123, "sample")
        self.article()
        self.sql("UPDATE display_tenants SET expires_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 1 DAY)")
        self.assertIsNone(self.pending())

    def test_scoped_message_has_time_title_detail_and_cancel(self):
        self.tenant()
        news.subscribe(123, "sample")
        ident = self.article()
        row = self.pending()
        news.send_delivery(self.bot, row)
        args, kwargs = self.bot.send_message.call_args
        self.assertEqual(args[0], 123)
        published = self.sql("SELECT published_at FROM channel_announcements WHERE id=%s", (ident,))[0]["published_at"]
        expected = published.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=7)))
        self.assertIn(f"{expected:%m/%d %H:%M}（UTC+7）", args[1])
        self.assertIn("测试标题", args[1])
        self.assertNotIn("测试正文", args[1])
        buttons = kwargs["reply_markup"].to_dict()["inline_keyboard"]
        self.assertEqual(buttons[0][0]["web_app"]["url"],
                         f"https://app.haotianzhushou.com/announcements/{ident}?tenant=sample")
        self.assertEqual(buttons[1][0]["callback_data"], "news:off")
        self.assertEqual(self.sql("SELECT status,result_id FROM news_deliveries"),
                         [{"status": "sent", "result_id": 77}])
        self.assertIsNone(self.pending())

    def test_expired_news_hidden_on_all_app_surfaces_and_not_notified(self):
        news.subscribe(123)
        expired = self.article("expired")
        current = self.article("current")
        self.sql("UPDATE channel_announcements SET published_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 73 HOUR)"
                 " WHERE id=%s", (expired,))
        self.assertEqual(announcements.catalog()["total"], 1)
        self.assertIsNone(announcements.detail(expired))
        self.assertIsNone(announcements.media_file(expired, 0))
        self.assertEqual(self.pending()["announcement_id"], current)
        self.assertIsNone(news.claim_delivery())

    def test_queued_news_expiring_before_claim_is_skipped(self):
        news.subscribe(123)
        ident = self.article()
        news.enqueue_news()
        self.sql("UPDATE channel_announcements SET published_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 73 HOUR)"
                 " WHERE id=%s", (ident,))
        self.assertTrue(news.claim_delivery()["skipped"])

    def test_retention_boundary_and_media_cleanup_preserve_index_tombstones(self):
        import json
        from announcement_video import cache_key, video_cache
        cutoff = datetime(2026, 11, 3, 0, 0)
        expired = self.article("expired")
        current = self.article("current")
        self.sql("UPDATE channel_announcements SET published_at=%s,media=%s WHERE id=%s",
                 (cutoff - timedelta(seconds=1), json.dumps([{"kind": "video", "filename": "2-100.mp4"}]), expired))
        self.sql("UPDATE channel_announcements SET published_at=%s WHERE id=%s", (cutoff, current))
        with TemporaryDirectory() as directory, \
                patch("announcements.media_directory", return_value=Path(directory)), \
                patch("announcements.retention_cutoff", return_value=cutoff):
            path = announcements.local_media_path("2-100.mp4")
            path.write_bytes(b"\x00\x00\x00\x18ftyp" + b"0000")
            cache = video_cache(path.parent)
            cached = cache.store(cache_key(path), [path.read_bytes()], ".mp4", path.stat().st_size)
            self.assertEqual(announcements.catalog()["total"], 1)
            self.assertIsNotNone(announcements.detail(current))
            self.assertEqual(announcements.expire_news(), 1)
            self.assertFalse(path.exists())
            self.assertFalse(cached.exists())
            self.assertEqual(announcements.expire_news(), 0)
        row = self.sql("SELECT job_key,body,media,enabled FROM channel_announcements WHERE id=%s", (expired,))[0]
        self.assertEqual((row["body"], row["media"], row["enabled"]), ("", "[]", 0))
        self.assertIn(row["job_key"], announcements.indexed_keys())

    def test_cleanup_failure_keeps_metadata_for_retry_without_restoring_visibility(self):
        import json
        ident = self.article()
        self.sql("UPDATE channel_announcements SET published_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 73 HOUR),media=%s"
                 " WHERE id=%s", (json.dumps([{"kind": "photo", "filename": "2-100.jpg"}]), ident))
        with TemporaryDirectory() as directory, patch("announcements.media_directory", return_value=Path(directory)):
            path = announcements.local_media_path("2-100.jpg")
            path.write_bytes(b"jpg")
            with patch.object(Path, "unlink", side_effect=PermissionError("in use")), \
                    self.assertLogs("announcements", level="ERROR"):
                self.assertEqual(announcements.expire_news(), 0)
            self.assertIsNone(announcements.detail(ident))
            self.assertEqual(announcements.expire_news(), 1)
            self.assertFalse(path.exists())

    def test_unknown_outcome_not_automatically_resent(self):
        news.subscribe(123)
        self.article()
        self.bot.send_message.side_effect = ConnectionError("test offline")
        news.send_delivery(self.bot, self.pending())
        self.assertEqual(self.sql("SELECT status FROM news_deliveries")[0]["status"], "uncertain")
        self.assertIsNone(self.pending())

    def test_blocked_user_disabled(self):
        news.subscribe(123)
        self.article()
        self.bot.send_message.side_effect = apihelper.ApiTelegramException(
            "sendMessage", None, {"error_code": 403, "description": "blocked"})
        news.send_delivery(self.bot, self.pending())
        self.assertFalse(news.subscription(123)["active"])
        self.assertIsNone(self.pending())

    def test_rate_limit_retains_one_delivery_for_delayed_retry(self):
        news.subscribe(123)
        self.article()
        self.bot.send_message.side_effect = apihelper.ApiTelegramException(
            "sendMessage", None, {"error_code": 429, "description": "limited",
                                  "parameters": {"retry_after": 5}})
        self.assertEqual(news.send_delivery(self.bot, self.pending()), 5)
        self.assertIsNone(self.pending())
        self.assertEqual(len(self.sql("SELECT * FROM news_deliveries")), 1)
        self.sql("UPDATE news_deliveries SET retry_at=UTC_TIMESTAMP()")
        self.assertIsNotNone(news.claim_delivery())

    def test_menu_does_not_subscribe_until_confirmed(self):
        news.show_menu(self.bot, 123, 123)
        self.assertIsNone(news.subscription(123))
        call = SimpleNamespace(data="news:on:-", id="callback", from_user=SimpleNamespace(id=123),
                               message=SimpleNamespace(chat=SimpleNamespace(id=123, type="private")))
        news.handle_callback(self.bot, call)
        self.assertTrue(news.subscription(123)["active"])
        call.data = "news:off"
        news.handle_callback(self.bot, call)
        self.assertFalse(news.subscription(123)["active"])

    def test_group_callbacks_are_rejected(self):
        call = SimpleNamespace(data="news:on:-", id="callback", from_user=SimpleNamespace(id=123),
                               message=SimpleNamespace(chat=SimpleNamespace(id=-123, type="group")))
        news.handle_callback(self.bot, call)
        self.assertIsNone(news.subscription(123))

    def test_revoked_tenant_can_still_cancel_from_bot_menu(self):
        self.tenant()
        news.subscribe(123, "sample")
        self.sql("UPDATE display_tenants SET announcements_enabled=0")
        call = SimpleNamespace(data="news:menu", id="callback", from_user=SimpleNamespace(id=123),
                               message=SimpleNamespace(chat=SimpleNamespace(id=123, type="private")))
        news.handle_callback(self.bot, call)
        self.assertIn("取消新闻订阅", str(self.bot.send_message.call_args.kwargs["reply_markup"].to_dict()))
        call.data = "news:off"
        news.handle_callback(self.bot, call)
        self.assertFalse(news.subscription(123)["active"])

    def test_restart_preserves_queue_and_marks_interrupted_sends_uncertain(self):
        news.subscribe(123)
        self.article()
        self.pending()
        self.article()
        news.enqueue_news()
        with patch.object(news.threading, "Thread") as thread:
            news.start_worker(self.bot)
            thread.return_value.start.assert_called_once()
        statuses = [row["status"] for row in self.sql("SELECT status FROM news_deliveries ORDER BY id")]
        self.assertEqual(statuses, ["uncertain", "queued"])


class NewsEntryTests(unittest.TestCase):
    def test_start_deep_link_preserves_tenant_without_auto_subscribing(self):
        import main
        message = SimpleNamespace(text="/start news_sample", from_user=SimpleNamespace(id=123),
                                  chat=SimpleNamespace(id=123))
        with patch.object(main, "touch_user"), patch.object(main, "bot"), \
                patch.object(main, "show_news_menu") as menu, patch.object(news, "subscribe") as subscribe:
            main.handle_start(message)
            menu.assert_called_once_with(message, "sample")
            subscribe.assert_not_called()

    def test_normal_start_still_shows_welcome(self):
        import main
        message = SimpleNamespace(text="/start", from_user=SimpleNamespace(id=123),
                                  chat=SimpleNamespace(id=123))
        with patch.object(main, "touch_user"), patch.object(main, "send_welcome") as welcome:
            main.handle_start(message)
            welcome.assert_called_once_with(main.bot, 123)


if __name__ == "__main__":
    unittest.main()
