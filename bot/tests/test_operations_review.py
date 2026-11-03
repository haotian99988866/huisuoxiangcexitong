import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from channel_sync import JobStore
from movie_ingest import MovieStore, SOURCES, store_path
from music_ingest import MusicStore
from music_playback import PlaybackStore
import operations_review as review


class OperationsReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        for target, value in (("channel_sync.state_directory", self.directory),
                              ("premium_balance.manual_owner", 99)):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.news = JobStore(self.directory / "jobs.sqlite3")
        self.music = MusicStore(self.directory / "music-import.sqlite3")
        self.playback = PlaybackStore()
        self.movies = [MovieStore(store_path(self.directory, source), source=source) for source in SOURCES]
        for store in (self.news, self.music, self.playback, *self.movies):
            self.addCleanup(store.close)
        self.bot = MagicMock()

    def news_job(self, status="failed", result=None, error="ValueError"):
        self.news.enqueue(1770576689, "2402734271", [10])
        row = self.news.connection.execute("SELECT * FROM jobs WHERE job_key='10'").fetchone()
        self.news.update(row, status, result_ids=result, error=error)

    def ticket(self, module):
        review.scan(module, self.conn)
        return self.conn.execute("SELECT * FROM operation_reviews WHERE state='open'").fetchone()

    def call(self, module, ticket, action, actor=99):
        return SimpleNamespace(
            id="callback", data=review.callback(module, ticket, action),
            from_user=SimpleNamespace(id=actor),
            message=SimpleNamespace(chat=SimpleNamespace(id=actor, type="private")),
        )

    def test_scanner_only_sends_news_under_new_media_policy(self):
        self.news_job()
        self.music.enqueue(123, 20, False, "2026-11-04")
        self.music.update(self.music.pending(), "uncertain", "Interrupted")
        self.playback.enqueue("a" * 32 + ".flac", 30, 100)
        self.playback.update("a" * 32 + ".flac", "failed", "PlaybackInvalidAudio", retryable=False)
        for movie in self.movies:
            movie.enqueue(90000, False)
            movie.update(movie.pending(), "failed", "ValueError")
        self.assertEqual(review.send_pending(self.bot, 99), 1)
        self.assertEqual(review.send_pending(self.bot, 99), 0)
        self.assertEqual(self.bot.send_message.call_count, 1)
        with review.connection(review.get_module("n")) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM operation_reviews").fetchone()[0], 1)

    def test_playback_network_retries_do_not_generate_manual_tickets(self):
        self.playback.enqueue("a" * 32 + ".flac", 30, 100)
        self.playback.update("a" * 32 + ".flac", "failed", "TimeoutError")
        self.assertEqual(review.send_pending(self.bot, 99), 0)
        self.bot.send_message.assert_not_called()

    def test_owner_private_chat_rechecked_for_every_callback(self):
        self.news_job()
        module = review.get_module("n")
        with review.connection(module) as self.conn:
            ticket = self.ticket(module)
        call = self.call(module, ticket, "ok_skip", actor=88)
        review.handle_callback(self.bot, call)
        self.assertTrue(self.bot.answer_callback_query.call_args.kwargs["show_alert"])
        call.from_user.id = 99
        review.handle_callback(self.bot, call)
        self.assertEqual(self.news.connection.execute("SELECT status FROM jobs").fetchone()[0], "failed")
        call.message.chat.id = 99
        call.message.chat.type = "group"
        review.handle_callback(self.bot, call)
        self.assertEqual(self.news.connection.execute("SELECT status FROM jobs").fetchone()[0], "failed")

    def test_confirmation_does_not_change_task_then_applies_once_and_audits_owner(self):
        self.news_job("uncertain", [70])
        module = review.get_module("n")
        with review.connection(module) as self.conn:
            ticket = self.ticket(module)
        review.handle_callback(self.bot, self.call(module, ticket, "skip"))
        self.assertEqual(self.news.connection.execute("SELECT status FROM jobs").fetchone()[0], "uncertain")
        call = self.call(module, ticket, "ok_skip")
        review.handle_callback(self.bot, call)
        row = self.news.connection.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(json.loads(row["result_ids"]), [70])
        with review.connection(module) as conn:
            audit = conn.execute("SELECT * FROM operation_reviews").fetchone()
            self.assertEqual((audit["state"], audit["actor_id"], audit["action"]), ("applied", 99, "skip"))
        review.handle_callback(self.bot, call)
        self.assertTrue(self.bot.answer_callback_query.call_args.kwargs["show_alert"])

    def test_changed_task_rejects_old_confirmation(self):
        self.news_job()
        module = review.get_module("n")
        with review.connection(module) as self.conn:
            ticket = self.ticket(module)
        self.news.connection.execute("UPDATE jobs SET error='New failure'")
        self.news.connection.commit()
        review.handle_callback(self.bot, self.call(module, ticket, "ok_retry"))
        self.assertEqual(self.news.connection.execute("SELECT status FROM jobs").fetchone()[0], "failed")
        self.assertTrue(self.bot.answer_callback_query.call_args.kwargs["show_alert"])

    def test_action_and_audit_roll_back_together_on_storage_error(self):
        self.news_job()
        module = review.get_module("n")
        with review.connection(module) as self.conn:
            ticket = self.ticket(module)
            self.conn.execute("""CREATE TRIGGER reject_review_update BEFORE UPDATE ON operation_reviews
                                 BEGIN SELECT RAISE(ABORT,'test audit failure'); END""")
            self.conn.commit()
            import sqlite3
            with self.assertRaises(sqlite3.IntegrityError):
                review.apply(module, self.conn, ticket["id"], "retry", 99)
            self.assertEqual(self.conn.execute("SELECT status FROM jobs").fetchone()[0], "failed")
            self.assertEqual(self.conn.execute("SELECT state FROM operation_reviews").fetchone()[0], "open")

    def test_manually_skipped_uncertain_news_retains_duplicate_protection(self):
        import time
        self.news_job("uncertain", error="Interrupted")
        original = self.news.connection.execute("SELECT * FROM jobs").fetchone()
        body = "News report with enough repeated detail for a reliable duplicate comparison " * 3
        with self.news.connection:
            self.news.news_deduper.remember(original, body, None, time.time())
        module = review.get_module("n")
        with review.connection(module) as self.conn:
            ticket = self.ticket(module)
            review.apply(module, self.conn, ticket["id"], "skip", 99)
        self.news.enqueue(1233397726, "2402734271", [20])
        candidate = self.news.next(1233397726, "2402734271")
        self.assertIsNotNone(self.news.news_deduper.find(candidate, body))

    def test_partial_news_and_overlap_cannot_be_retried_even_with_forged_action(self):
        for result, error in (([70], "Interrupted"), (None, "Historical album overlap")):
            with self.subTest(result=result):
                self.news_job("uncertain", result, error)
                module = review.get_module("n")
                with review.connection(module) as self.conn:
                    ticket = self.ticket(module)
                    self.assertIsNone(review.retry_action(module, json.loads(ticket["snapshot"])))
                review.handle_callback(self.bot, self.call(module, ticket, "ok_absent"))
                self.assertEqual(self.news.connection.execute("SELECT status FROM jobs").fetchone()[0], "uncertain")

    def test_uncertain_without_result_requires_explicit_absent_confirmation(self):
        self.news_job("uncertain", error="Interrupted")
        module = review.get_module("n")
        with review.connection(module) as self.conn:
            ticket = self.ticket(module)
        review.handle_callback(self.bot, self.call(module, ticket, "ok_retry"))
        self.assertEqual(self.news.connection.execute("SELECT status FROM jobs").fetchone()[0], "uncertain")
        review.handle_callback(self.bot, self.call(module, ticket, "absent"))
        self.assertIn("确实未发布", self.bot.send_message.call_args.args[1])
        self.assertEqual(self.news.connection.execute("SELECT status FROM jobs").fetchone()[0], "uncertain")
        review.handle_callback(self.bot, self.call(module, ticket, "ok_absent"))
        self.assertEqual(self.news.connection.execute("SELECT status FROM jobs").fetchone()[0], "queued")

    def test_existing_import_copy_only_retries_indexing_and_never_resets_result(self):
        for key in ("a", "v0", "v1", "v2"):
            module = review.get_module(key)
            if key == "a":
                self.music.enqueue(123, 20, False, "2026-11-04")
                self.music.update(self.music.pending(), "failed", "ValueError", result_id=70, document_id=987)
            else:
                movie = self.movies[int(key[1:])]
                movie.enqueue(90000, False)
                movie.update(movie.pending(), "failed", "ValueError", result_id=70, document_id=987)
            with review.connection(module) as self.conn:
                ticket = self.ticket(module)
                review.apply(module, self.conn, ticket["id"], "index", 99)
                row = self.conn.execute("SELECT * FROM jobs").fetchone()
                self.assertEqual((row["status"], row["result_id"], row["document_id"]), ("forwarded", 70, 987))

    def test_movie_manual_skip_is_excluded_and_recent_recheck_does_not_requeue(self):
        movie = self.movies[0]
        movie.enqueue(90000, False)
        movie.update(movie.pending(), "failed", "ValueError")
        module = review.get_module("v0")
        with review.connection(module) as self.conn:
            ticket = self.ticket(module)
            review.apply(module, self.conn, ticket["id"], "skip", 99)
        self.assertFalse(movie.reconcile(90000))
        self.assertIsNone(movie.pending())

    def test_playback_retry_preserves_global_flood_wait_and_other_tasks(self):
        name = "a" * 32 + ".flac"
        self.playback.enqueue(name, 30, 100)
        self.playback.update(name, "failed", "PlaybackInvalidAudio", retryable=False)
        self.playback.enqueue("b" * 32 + ".flac", 31, 100)
        self.playback.wait_until(9999999999)
        module = review.get_module("p")
        with review.connection(module) as self.conn:
            ticket = self.ticket(module)
            review.apply(module, self.conn, ticket["id"], "retry", 99)
        self.assertEqual(self.playback.wait_until(), 9999999999)
        row = self.playback.connection.execute("SELECT * FROM jobs WHERE name=?", (name,)).fetchone()
        self.assertEqual((row["status"], row["retryable"], row["priority"]), ("queued", 1, 1))
        self.assertEqual(self.playback.connection.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)

    def test_failed_ticket_recurs_after_retry_and_is_not_silently_suppressed(self):
        self.news_job()
        module = review.get_module("n")
        with review.connection(module) as self.conn:
            ticket = self.ticket(module)
            review.apply(module, self.conn, ticket["id"], "retry", 99)
            self.conn.execute("UPDATE jobs SET status='failed',error='ValueError'")
            self.conn.commit()
            next_ticket = self.ticket(module)
            self.assertNotEqual(next_ticket["id"], ticket["id"])

    def test_automatic_recovery_closes_stale_ticket(self):
        self.news_job()
        module = review.get_module("n")
        with review.connection(module) as self.conn:
            self.ticket(module)
            self.conn.execute("UPDATE jobs SET status='sent'")
            self.conn.commit()
            review.scan(module, self.conn)
            self.assertEqual(self.conn.execute("SELECT state FROM operation_reviews").fetchone()[0], "resolved")

    def test_notification_plaintext_links_and_callback_lengths(self):
        self.news_job()
        review.send_pending(self.bot, 99)
        args = self.bot.send_message.call_args
        self.assertEqual(args.args[0], 99)
        self.assertIsNone(args.kwargs["parse_mode"])
        buttons = args.kwargs["reply_markup"].to_dict()["inline_keyboard"]
        self.assertEqual(buttons[0][0]["url"], "https://t.me/c/1770576689/10")
        for line in buttons:
            for button in line:
                if "callback_data" in button:
                    self.assertLessEqual(len(button["callback_data"].encode()), 64)

    def test_unknown_notification_delivery_is_persisted_without_immediate_duplicate(self):
        self.news_job()
        self.bot.send_message.side_effect = TimeoutError("unknown delivery")
        with patch.object(review.logger, "error"):
            self.assertEqual(review.send_pending(self.bot, 99), 1)
            self.assertEqual(review.send_pending(self.bot, 99), 0)
        module = review.get_module("n")
        with review.connection(module) as conn:
            row = conn.execute("SELECT * FROM operation_reviews").fetchone()
            self.assertEqual((row["delivery"], row["error_code"]), ("uncertain", "TimeoutError"))

    def test_result_notification_timeout_does_not_repeat_persisted_action(self):
        self.news_job()
        module = review.get_module("n")
        with review.connection(module) as self.conn:
            ticket = self.ticket(module)
        self.bot.send_message.side_effect = TimeoutError("unknown delivery")
        with patch.object(review.logger, "exception") as log:
            review.handle_callback(self.bot, self.call(module, ticket, "ok_retry"))
            log.assert_called_once()
        self.assertEqual(self.news.connection.execute("SELECT status FROM jobs").fetchone()[0], "queued")
        review.handle_callback(self.bot, self.call(module, ticket, "ok_retry"))
        self.assertTrue(self.bot.answer_callback_query.call_args.kwargs["show_alert"])

    def test_unresolved_ticket_reminds_only_after_thirty_minutes(self):
        self.news_job()
        review.send_pending(self.bot, 99)
        module = review.get_module("n")
        with review.connection(module) as conn:
            import time
            conn.execute("UPDATE operation_reviews SET last_attempt=?", (time.time() - 1790,))
            conn.commit()
        self.assertEqual(review.send_pending(self.bot, 99), 0)
        with review.connection(module) as conn:
            conn.execute("UPDATE operation_reviews SET last_attempt=?", (time.time() - 1801,))
            conn.commit()
        self.assertEqual(review.send_pending(self.bot, 99), 1)

    def test_command_paginates_unattempted_tasks_before_repeats(self):
        for message in range(10, 15):
            self.news.enqueue(1770576689, "2402734271", [message])
            self.news.update(self.news.next(1770576689, "2402734271"), "failed", error="ValueError")
        review.send_pending(self.bot, 99, force=True)
        review.send_pending(self.bot, 99, force=True)
        texts = [call.args[1] for call in self.bot.send_message.call_args_list]
        self.assertTrue(any("任务：14\n" in text for text in texts))

    def test_unrecognized_module_and_wrong_recipient_are_rejected(self):
        with self.assertRaises(ValueError):
            review.get_module("..\\outside")
        with self.assertRaises(ValueError):
            review.send_pending(self.bot, 88)
        self.bot.send_message.assert_not_called()

    def test_main_registers_command_and_callback_routes(self):
        import main
        message = SimpleNamespace()
        call = SimpleNamespace()
        with patch.object(main, "bot") as bot, \
                patch.object(review, "handle_command") as command, \
                patch.object(review, "handle_callback") as callback:
            main.handle_operations_review(message)
            main.handle_operations_review_callback(call)
            command.assert_called_once_with(bot, message)
            callback.assert_called_once_with(bot, call)


if __name__ == "__main__":
    unittest.main()
