from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import pymysql
from telebot import apihelper

from db import get_conn
import post_navigation as navigation


def message(ident=1, chat=navigation.CHANNEL_ID, user=None, album=None):
    return SimpleNamespace(chat=SimpleNamespace(id=chat), message_id=ident,
                           content_type="photo", from_user=user, media_group_id=album,
                           text=None, caption=None)


class NavigationTests(unittest.TestCase):
    def test_keyboard_preserves_all_links_and_limits_row_width(self):
        rows = navigation.keyboard().to_dict()["inline_keyboard"]
        self.assertEqual(sum(len(row) for row in rows), 18)
        self.assertEqual([len(row) for row in rows], [1, 3, 3, 4, 2, 3, 2])
        self.assertEqual(rows[0], [{"text": "活动与折扣专区", "url": "https://t.me/haotianhd99988866"}])
        self.assertEqual([button["text"] for button in rows[-2]], ["成人影音", "服务反馈", "昊天大事件"])
        self.assertEqual(rows[-2][-1]["url"], "https://t.me/haotiancm99988866")
        self.assertEqual(rows[-1], [
            {"text": "联系昊天", "url": "https://t.me/haotian99988866"},
            {"text": "西港娱乐信息", "url": "https://app.haotianzhushou.com/?module=venue"},
        ])
        urls = {button["url"] for row in rows for button in row}
        self.assertEqual(len(urls), 18)
        self.assertNotIn("https://t.me/htwssp", urls)
        self.assertNotIn("https://t.me/haotianzhaopian_bot", urls)

    def test_only_target_messages_owner_group_posts_and_supported_media(self):
        owner = SimpleNamespace(id=123, is_bot=False)
        with patch("post_navigation.admin_ids", return_value=[123]):
            for kind in ("text", "photo", "video"):
                post = message()
                post.content_type = kind
                self.assertTrue(navigation.accepts(post))
                self.assertTrue(navigation.accepts(message(chat=navigation.GROUP_ID, user=owner)))
            for post in (message(chat=-100999), message(chat=navigation.GROUP_ID),
                         message(chat=navigation.GROUP_ID, user=SimpleNamespace(id=456, is_bot=False)),
                         message(user=SimpleNamespace(id=1, is_bot=True))):
                self.assertFalse(navigation.accepts(post))
            for attr, value in (("is_automatic_forward", True), ("via_bot", owner),
                                ("text", navigation.NAVIGATION_TEXT), ("content_type", "new_chat_members")):
                post = message()
                setattr(post, attr, value)
                self.assertFalse(navigation.accepts(post))

    def test_group_replies_and_channel_companions(self):
        bot = Mock()
        bot.send_message.return_value = SimpleNamespace(message_id=9)
        bot.edit_message_reply_markup.return_value = SimpleNamespace(message_id=1)
        for chat in (navigation.CHANNEL_ID, navigation.GROUP_ID):
            row = {"chat_id": chat, "post_key": "message:1", "first_message_id": 1}
            with patch("post_navigation.update") as update:
                navigation.deliver(bot, row)
                update.assert_called_once_with(row, "sent", result_id=1 if chat == navigation.CHANNEL_ID else 9)
            if chat == navigation.GROUP_ID:
                options = bot.send_message.call_args.kwargs
                self.assertTrue(options["disable_notification"])
                self.assertEqual(options["reply_parameters"].message_id, 1)
                self.assertFalse(options["reply_parameters"].allow_sending_without_reply)
            else:
                options = bot.edit_message_reply_markup.call_args.kwargs
                self.assertEqual(options["message_id"], 1)
                self.assertNotIn("reply_parameters", options)
        row = {"chat_id": navigation.CHANNEL_ID, "post_key": "album:12", "first_message_id": 1}
        with patch("post_navigation.update") as update:
            navigation.deliver(bot, row)
            update.assert_called_once_with(row, "sent", result_id=9)
        self.assertNotIn("reply_parameters", bot.send_message.call_args.kwargs)

    def test_target_handlers_precede_existing_photo_and_text_handlers(self):
        import main
        for handlers in (main.bot.message_handlers, main.bot.channel_post_handlers):
            navigation_index = next(i for i, item in enumerate(handlers)
                                    if item["function"] == main.handle_post_navigation)
            photo_index = next(i for i, item in enumerate(handlers)
                               if item["function"] == main.handle_photo_message)
            text_index = next(i for i, item in enumerate(handlers)
                              if item["function"] == main.handle_attendance_text)
            self.assertLess(navigation_index, photo_index)
            self.assertLess(navigation_index, text_index)
        with patch("post_navigation.enqueue") as enqueue:
            main.handle_post_navigation(message())
        enqueue.assert_called_once()

    def test_rate_limit_and_unknown_send_do_not_duplicate(self):
        bot = Mock()
        row = {"chat_id": navigation.CHANNEL_ID, "post_key": "album:1", "first_message_id": 1}
        cases = (
            (apihelper.ApiTelegramException("sendMessage", None, {
                "error_code": 429, "description": "limited", "parameters": {"retry_after": 45}}),
             "queued", 45),
            (apihelper.ApiTelegramException("sendMessage", None, {
                "error_code": 403, "description": "forbidden"}), "failed", 0),
            (ConnectionError("offline"), "uncertain", 0),
        )
        for error, expected, wait in cases:
            with self.subTest(expected=expected), patch("post_navigation.update") as update, \
                    self.assertLogs("post_navigation"):
                bot.send_message.side_effect = error
                navigation.deliver(bot, row)
                self.assertEqual(update.call_args.args[1], expected)
                if wait:
                    self.assertEqual(update.call_args.kwargs["retry_seconds"], wait)

    def test_single_channel_post_only_changes_keyboard_not_original_content(self):
        bot = Mock()
        bot.edit_message_reply_markup.return_value = True
        row = {"chat_id": navigation.CHANNEL_ID, "post_key": "message:1", "first_message_id": 1}
        with patch("post_navigation.update") as update:
            navigation.deliver(bot, row)
        update.assert_called_once_with(row, "sent", result_id=1)
        bot.send_message.assert_not_called()
        self.assertNotIn("text", bot.edit_message_reply_markup.call_args.kwargs)


class NavigationQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_conn()
        cls.database = "test_navigation_" + uuid.uuid4().hex[:16]
        with cls.root.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{cls.database}` CHARACTER SET utf8mb4")
        cls.addClassCleanup(cls.cleanup_database)

        def connect():
            return pymysql.connect(host=cls.root.host, port=cls.root.port, user=cls.root.user,
                                   password=cls.root.password, database=cls.database, charset="utf8mb4",
                                   cursorclass=pymysql.cursors.DictCursor)

        cls.connect = staticmethod(connect)
        patcher = patch("post_navigation.get_conn", side_effect=connect)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        navigation.ensure_tables()
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE channel_announcements (id BIGINT PRIMARY KEY,"
                            " message_ids JSON,enabled INT,published_at DATETIME,media JSON)")
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
                rows = cur.fetchall() if cur.description else []
            conn.commit()
            return rows
        finally:
            conn.close()

    def setUp(self):
        self.sql("DELETE FROM post_navigation_jobs")
        self.sql("DELETE FROM channel_announcements")
        self.sql("UPDATE post_navigation_policy SET enabled_at=DATE_ADD(UTC_TIMESTAMP(),INTERVAL 1 DAY)")

    def test_news_backfill_new_items_and_three_buttons_are_idempotent(self):
        self.sql("INSERT INTO channel_announcements (id,message_ids,enabled,published_at) VALUES"
                 " (1,'[101]',1,UTC_TIMESTAMP()),(2,'[102,103]',1,UTC_TIMESTAMP()),"
                 " (3,'[104]',0,UTC_TIMESTAMP()),"
                 " (4,'[105]',1,DATE_SUB(UTC_TIMESTAMP(),INTERVAL 73 HOUR))")
        navigation.enqueue_recent_news()
        navigation.enqueue_recent_news()
        jobs = self.sql("SELECT * FROM post_navigation_jobs ORDER BY post_key")
        self.assertEqual(len(jobs), 2)
        bot = Mock()
        bot.edit_message_reply_markup.return_value = SimpleNamespace(message_id=101)
        bot.send_message.return_value = SimpleNamespace(message_id=200)
        navigation.deliver(bot, jobs[0])
        navigation.deliver(bot, jobs[1])
        rows = bot.edit_message_reply_markup.call_args.kwargs["reply_markup"].to_dict()["inline_keyboard"]
        self.assertEqual([button["text"] for button in rows[0]],
                         ["联系昊天", "西港娱乐", "会员充值"])
        self.assertEqual(rows[0][-1]["url"], "https://t.me/haotianzhaopian_bot?startapp=membership")
        bot.send_message.assert_not_called()

    def test_new_album_never_sends_standalone_reply(self):
        self.sql("UPDATE post_navigation_policy SET enabled_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 1 SECOND)")
        self.sql("INSERT INTO channel_announcements (id,message_ids,enabled,published_at)"
                 " VALUES (1,'[101,102]',1,UTC_TIMESTAMP())")
        navigation.enqueue_recent_news()
        row = navigation.claim()
        bot = Mock()
        bot.send_message.return_value = SimpleNamespace(message_id=200)
        navigation.deliver(bot, row)
        bot.send_message.assert_not_called()
        bot.edit_message_reply_markup.assert_not_called()
        navigation.enqueue_recent_news()
        self.assertIsNone(navigation.claim())
        self.assertEqual(self.sql("SELECT status FROM post_navigation_jobs"), [{"status": "skipped"}])

    def test_album_followed_by_full_text_targets_text_message(self):
        self.sql("INSERT INTO channel_announcements VALUES"
                 " (1,'[101,102,103]',1,UTC_TIMESTAMP(),'[{\"kind\":\"photo\"},{\"kind\":\"video\"}]')")
        navigation.enqueue_recent_news()
        row = navigation.claim()
        self.assertEqual((row["first_message_id"], row["last_message_id"]), (103, 103))
        bot = Mock()
        bot.edit_message_reply_markup.return_value = SimpleNamespace(message_id=103)
        navigation.deliver(bot, row)
        bot.send_message.assert_not_called()
        self.assertEqual(bot.edit_message_reply_markup.call_args.kwargs["message_id"], 103)

    def test_album_aggregates_out_of_order_members_and_duplicate_updates(self):
        for ident in (3, 1, 2, 3):
            navigation.enqueue(message(ident=ident, album="album1"))
        rows = self.sql("SELECT * FROM post_navigation_jobs")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["first_message_id"], rows[0]["last_message_id"]), (1, 3))
        self.assertIsNone(navigation.claim())
        self.sql("UPDATE post_navigation_jobs SET due_at=UTC_TIMESTAMP()")
        row = navigation.claim()
        self.assertIsNotNone(row)
        self.assertIsNone(navigation.claim())
        navigation.update(row, "sent", result_id=100)
        navigation.enqueue(message(ident=4, album="album1"))
        self.assertIsNone(navigation.claim())
        self.assertEqual(self.sql("SELECT status,result_id FROM post_navigation_jobs"),
                         [{"status": "sent", "result_id": 100}])

    def test_independent_posts_chats_and_restart_safety(self):
        navigation.enqueue(message(ident=1))
        navigation.enqueue(message(ident=2))
        navigation.enqueue(message(ident=1, chat=navigation.GROUP_ID))
        navigation.enqueue(message(ident=1))
        self.assertEqual(len(self.sql("SELECT * FROM post_navigation_jobs")), 3)
        self.sql("UPDATE post_navigation_jobs SET due_at=UTC_TIMESTAMP()")
        claimed = navigation.claim()
        with patch("post_navigation.threading.Thread") as thread:
            navigation.start_worker(Mock())
            thread.return_value.start.assert_called_once()
        states = self.sql("SELECT status FROM post_navigation_jobs WHERE chat_id=%s AND post_key=%s",
                          (claimed["chat_id"], claimed["post_key"]))
        self.assertEqual(states, [{"status": "uncertain"}])
        self.assertIsNotNone(navigation.claim())

    def test_duplicate_does_not_extend_album_quiet_period(self):
        navigation.enqueue(message(ident=5, album="album1"))
        self.sql("UPDATE post_navigation_jobs SET due_at=UTC_TIMESTAMP()")
        navigation.enqueue(message(ident=5, album="album1"))
        self.assertIsNotNone(navigation.claim())
