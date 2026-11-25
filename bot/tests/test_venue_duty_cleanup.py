from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import pymysql
import requests
from telebot import apihelper

from config.channels import STAFF_ARCHIVE_VENUES
from db import get_conn
import venue_duty_cleanup as cleanup


def message(ident, when, venue_id=4, *, video=False, caption=""):
    return SimpleNamespace(
        chat=SimpleNamespace(id=STAFF_ARCHIVE_VENUES[venue_id]["channel_id"]),
        message_id=ident, date=when.timestamp(), caption=caption,
        photo=[] if video else [object()], video=object() if video else None,
    )


class DutyCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_conn()
        cls.database = "test_duty_cleanup_" + uuid.uuid4().hex[:16]
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
        patcher = patch.object(cleanup, "get_conn", side_effect=connect)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE technician_photos (
                    venue_id INT,channel_id VARCHAR(30),msg_id BIGINT,created_at DATETIME,
                    status VARCHAR(32) DEFAULT 'pending_review')""")
                cur.execute("CREATE TABLE venue_photos (channel_id VARCHAR(30),msg_id BIGINT)")
                cur.execute("""CREATE TABLE bot_footers (
                    channel_id VARCHAR(30),message_id BIGINT,updated_at DATETIME)""")
                for venue in STAFF_ARCHIVE_VENUES.values():
                    for suffix in ("posts", "pages"):
                        cur.execute(f"CREATE TABLE {venue['table_prefix']}_{suffix} (message_id BIGINT)")
            conn.commit()
        finally:
            conn.close()
        cleanup.ensure_tables()

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
                rows = cur.fetchall() if cur.description else []
            conn.commit()
            return rows
        finally:
            conn.close()

    def setUp(self):
        for table in ("venue_duty_messages", "technician_photos", "venue_photos", "bot_footers"):
            self.sql(f"DELETE FROM {table}")
        for venue in STAFF_ARCHIVE_VENUES.values():
            for suffix in ("posts", "pages"):
                self.sql(f"DELETE FROM {venue['table_prefix']}_{suffix}")
        self.sql("UPDATE venue_duty_cleanup_policy SET next_request_at=NULL")

    def seed(self, ident, when, venue_id=4, kind="photo"):
        start = cleanup.current_shift_window(when)[0]
        self.sql("""INSERT INTO venue_duty_messages
            (venue_id,message_id,published_at,shift_start,kind) VALUES(%s,%s,%s,%s,%s)""",
                 (venue_id, ident, when, start, kind))

    def statuses(self, venue_id=4):
        return {row["message_id"]: row["status"] for row in self.sql(
            "SELECT message_id,status FROM venue_duty_messages WHERE venue_id=%s", (venue_id,)
        )}

    def test_rollover_only_queues_old_media_and_footer_for_that_venue(self):
        old = datetime(2026, 11, 23, 20)
        new = datetime(2026, 11, 24, 6, 1)
        for venue_id in (4, 5, 6):
            self.seed(10, old, venue_id)
            self.seed(11, old, venue_id, "video")
            self.seed(12, new, venue_id)
            self.sql("INSERT INTO bot_footers VALUES(%s,%s,%s)",
                     (str(STAFF_ARCHIVE_VENUES[venue_id]["channel_id"]), 15, old))
        cleanup.record_media(message(20, new), 4, now=new)
        cleanup.record_media(message(21, new), 4, now=new)
        self.assertEqual(self.statuses(), {
            10: "queued", 11: "queued", 12: "retained", 15: "queued",
            20: "retained", 21: "retained",
        })
        self.assertTrue(all(status == "retained" for status in self.statuses(5).values()))
        self.assertTrue(all(status == "retained" for status in self.statuses(6).values()))

    def test_midnight_append_video_and_history_do_not_trigger_rollover(self):
        old = datetime(2026, 11, 23, 20)
        midnight = datetime(2026, 11, 24, 0, 1)
        morning = datetime(2026, 11, 24, 6, 1)
        self.seed(10, old)
        cleanup.record_media(message(11, midnight), 4, now=midnight)
        cleanup.record_media(message(12, morning, video=True), 4, now=morning)
        cleanup.record_media(message(13, old), 4, now=morning)
        self.assertTrue(all(status == "retained" for status in self.statuses().values()))
        caption = STAFF_ARCHIVE_VENUES[4]["repost_prefix"] + "test"
        cleanup.record_media(message(14, morning, caption=caption), 4, now=morning)
        self.assertNotIn(14, self.statuses())

    @patch.object(cleanup, "sync_staff_archive")
    def test_archive_pages_and_venue_covers_are_never_deleted(self, sync):
        old = datetime(2026, 11, 23, 20)
        new = datetime(2026, 11, 24, 6, 1)
        for ident in (10, 11, 12, 13):
            self.seed(ident, old)
        self.sql("INSERT INTO dalang_archive_posts VALUES(10)")
        self.sql("INSERT INTO dalang_archive_pages VALUES(11)")
        self.sql("INSERT INTO venue_photos VALUES(%s,12)",
                 (str(STAFF_ARCHIVE_VENUES[4]["channel_id"]),))
        cleanup.record_media(message(20, new), 4, now=new)
        bot = Mock()
        bot.delete_message.return_value = True
        for _ in range(4):
            cleanup.run_once(bot, 4)
        bot.delete_message.assert_called_once_with(
            STAFF_ARCHIVE_VENUES[4]["channel_id"], 13, timeout=20
        )
        sync.assert_called_once()
        self.assertEqual(self.statuses()[10], "protected")
        self.assertEqual(self.statuses()[11], "protected")
        self.assertEqual(self.statuses()[12], "protected")
        self.assertEqual(self.statuses()[13], "deleted")

    @patch.object(cleanup, "sync_staff_archive")
    def test_pending_review_photos_are_never_deleted(self, sync):
        old = datetime(2026, 11, 23, 20)
        new = datetime(2026, 11, 24, 6, 1)
        channel_id = str(STAFF_ARCHIVE_VENUES[4]["channel_id"])
        self.seed(10, old)
        self.seed(11, old)
        self.sql(
            "INSERT INTO technician_photos VALUES(4,%s,10,%s,'pending_review')",
            (channel_id, old),
        )
        cleanup.record_media(message(20, new), 4, now=new)
        bot = Mock()
        bot.delete_message.return_value = True
        cleanup.run_once(bot, 4)
        cleanup.run_once(bot, 4)
        bot.delete_message.assert_called_once_with(
            STAFF_ARCHIVE_VENUES[4]["channel_id"], 11, timeout=20
        )
        self.assertEqual(self.statuses()[10], "protected")
        self.assertEqual(self.statuses()[11], "deleted")

    @patch.object(cleanup, "sync_staff_archive")
    def test_failed_archive_sync_stops_delete_and_rate_limit_survives_restart(self, sync):
        self.seed(10, datetime(2026, 11, 23, 20))
        self.sql("UPDATE venue_duty_messages SET status='queued'")
        bot = Mock()
        sync.side_effect = RuntimeError("archive unavailable")
        with self.assertRaises(RuntimeError):
            cleanup.run_once(bot, 4)
        bot.delete_message.assert_not_called()
        sync.side_effect = None
        bot.delete_message.side_effect = apihelper.ApiTelegramException(
            "deleteMessage", None, {
                "error_code": 429, "description": "limited",
                "parameters": {"retry_after": 60},
            },
        )
        with self.assertLogs(cleanup.logger, level="WARNING"):
            cleanup.run_once(bot, 4)
        cleanup.ensure_tables()
        cleanup.run_once(bot, 4)
        self.assertEqual(bot.delete_message.call_count, 1)
        self.assertEqual(self.statuses()[10], "queued")

    @patch.object(cleanup, "sync_staff_archive")
    def test_idempotent_delete_recovers_timeout_without_removing_new_footer(self, sync):
        self.seed(10, datetime(2026, 11, 23, 20), kind="footer")
        self.sql("UPDATE venue_duty_messages SET status='queued'")
        channel_id = str(STAFF_ARCHIVE_VENUES[4]["channel_id"])
        self.sql("INSERT INTO bot_footers VALUES(%s,20,%s)",
                 (channel_id, datetime(2026, 11, 24, 6, 1)))
        bot = Mock()
        bot.delete_message.side_effect = requests.Timeout("unknown")
        with self.assertLogs(cleanup.logger, level="ERROR"):
            cleanup.run_once(bot, 4)
        self.assertEqual(self.statuses()[10], "queued")
        self.sql("UPDATE venue_duty_cleanup_policy SET next_request_at=NULL")
        bot.delete_message.side_effect = apihelper.ApiTelegramException(
            "deleteMessage", None, {"error_code": 400, "description": "message to delete not found"},
        )
        cleanup.run_once(bot, 4)
        self.assertEqual(self.statuses()[10], "deleted")
        self.assertEqual(self.sql("SELECT message_id FROM bot_footers"), [{"message_id": 20}])

    def test_bootstrap_only_registers_known_photos_without_deleting(self):
        channel_id = str(STAFF_ARCHIVE_VENUES[4]["channel_id"])
        for ident in (10, 11):
            self.sql(
                "INSERT INTO technician_photos VALUES(4,%s,%s,%s,'matched')",
                (channel_id, ident, datetime(2026, 11, 23, 20)),
            )
        self.sql("INSERT INTO venue_photos VALUES(%s,11)", (channel_id,))
        cleanup.ensure_tables()
        cleanup.ensure_tables()
        self.assertEqual(self.statuses(), {10: "retained"})

    @patch.object(cleanup, "sync_staff_archive")
    def test_permission_failure_is_persisted_without_retrying(self, sync):
        self.seed(10, datetime(2026, 11, 23, 20))
        self.sql("UPDATE venue_duty_messages SET status='queued'")
        bot = Mock()
        bot.delete_message.side_effect = apihelper.ApiTelegramException(
            "deleteMessage", None, {"error_code": 403, "description": "forbidden"},
        )
        with self.assertLogs(cleanup.logger, level="ERROR"):
            cleanup.run_once(bot, 4)
        cleanup.run_once(bot, 4)
        self.assertEqual(self.statuses()[10], "failed")
        bot.delete_message.assert_called_once()

    def test_real_photo_handler_tracks_original_but_not_permanent_archive(self):
        import main

        now = datetime.now()
        post = message(10, now)
        post.content_type = "photo"
        post.photo = [SimpleNamespace(file_id="test-file")]
        with patch.object(main, "record_channel_activity"), \
                patch.object(main, "save_delivery_media"), \
                patch.object(main, "schedule_footer"), \
                patch.object(main, "parse_caption", return_value=([], "")), \
                patch.object(main.bot, "get_file", return_value=SimpleNamespace(file_path="test")), \
                patch.object(main.bot, "download_file", return_value=b"image"), \
                patch.object(main, "save_technician_photo", return_value={"skipped": True}):
            main.handle_photo_message(post)
            post.message_id = 11
            post.caption = STAFF_ARCHIVE_VENUES[4]["repost_prefix"]
            main.handle_photo_message(post)
        self.assertEqual(self.statuses(), {10: "retained"})


if __name__ == "__main__":
    unittest.main()
