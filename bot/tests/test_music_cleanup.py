import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import music
import music_cleanup as cleanup
import operations_review as review
from music_playback import PlaybackStore
from music_ingest import MusicStore
from movie_ingest import MovieStore, SOURCES, store_path


class MusicCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library = self.root / "library"
        self.library.mkdir()
        for target, value in (("channel_sync.state_directory", self.root),
                              ("channel_sync.credentials", (123, "test")),
                              ("music.library_dir", self.library),
                              ("premium_balance.manual_owner", 99)):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.playback = PlaybackStore()
        self.ingest = MusicStore(self.root / "music-import.sqlite3")
        self.movie = MovieStore(store_path(self.root, SOURCES[0]), source=SOURCES[0])
        for store in (self.playback, self.ingest, self.movie):
            self.addCleanup(store.close)
        self.name = "a" * 32 + ".flac"
        with closing(sqlite3.connect(self.root / "user.session")) as conn, conn:
            conn.execute("CREATE TABLE sessions(dc_id INTEGER,server_address TEXT,port INTEGER,auth_key BLOB)")
            conn.execute("INSERT INTO sessions VALUES(1,'test',443,?)", (b"x" * 256,))

    def failed_playback(self):
        self.playback.enqueue(self.name, 20, 100)
        self.playback.update(self.name, "failed", "PlaybackInvalidAudio", retryable=False)

    def plan(self, **fields):
        return {**cleanup.initial_plan(review.get_module("p"), {
            "message": 20, "size": 100, "name": self.name, "status": "failed",
        }), **fields}

    def client(self):
        client = AsyncMock()
        client.is_user_authorized.return_value = True
        client.get_me.return_value = SimpleNamespace(bot=False)
        client.get_entity.return_value = SimpleNamespace(id=2368924969)
        client.get_permissions.return_value = SimpleNamespace(is_admin=True, delete_messages=True)
        return client

    def test_only_exact_owned_message_is_deleted_and_absence_is_verified(self):
        client = self.client()
        client.get_messages.side_effect = [
            [SimpleNamespace(document=SimpleNamespace(id=987, size=100))], [None],
        ]
        with patch("telethon.TelegramClient", return_value=client):
            plan = asyncio.run(cleanup.channel_step(self.plan(phase="channel")))
        client.get_entity.assert_awaited_once_with("https://t.me/haotiandkhy99988866")
        client.delete_messages.assert_awaited_once_with(client.get_entity.return_value, [20])
        self.assertEqual(plan["phase"], "files")
        client.disconnect.assert_awaited_once()

    def test_wrong_channel_changed_size_or_document_and_missing_permission_never_delete(self):
        for failure in ("channel", "permission", "size", "document"):
            client = self.client()
            client.get_messages.return_value = [SimpleNamespace(document=SimpleNamespace(id=987, size=100))]
            plan = self.plan(phase="channel", document_id=987)
            if failure == "channel":
                client.get_entity.return_value.id = 123
            elif failure == "permission":
                client.get_permissions.return_value.delete_messages = False
            elif failure == "size":
                plan["expected_size"] = 101
            else:
                plan["document_id"] = 988
            with self.subTest(failure=failure), patch("telethon.TelegramClient", return_value=client):
                with self.assertRaises(ValueError):
                    asyncio.run(cleanup.channel_step(plan))
            client.delete_messages.assert_not_awaited()

    def test_already_deleted_copy_is_idempotent(self):
        client = self.client()
        client.get_messages.return_value = [None]
        with patch("telethon.TelegramClient", return_value=client):
            plan = asyncio.run(cleanup.channel_step(self.plan(phase="channel")))
        self.assertEqual(plan["phase"], "files")
        client.delete_messages.assert_not_awaited()

    def test_uncertain_import_search_matches_exact_document_not_other_music(self):
        client = self.client()
        client.get_messages.return_value = [
            SimpleNamespace(id=20, document=SimpleNamespace(id=987)),
            SimpleNamespace(id=21, document=SimpleNamespace(id=988)),
        ]
        plan = cleanup.initial_plan(review.get_module("a"), {
            "status": "uncertain", "result_id": None, "document_id": 987,
        })
        with patch("telethon.TelegramClient", return_value=client):
            result = asyncio.run(cleanup.channel_step(plan))
        self.assertEqual(result["messages"], [20])
        self.assertTrue(result["located"])
        client.delete_messages.assert_not_awaited()

    def test_deletion_unknown_is_not_success_and_next_attempt_verifies_channel(self):
        client = self.client()
        client.get_messages.return_value = [SimpleNamespace(document=SimpleNamespace(id=987, size=100))]
        client.delete_messages.side_effect = TimeoutError("unknown delivery")
        with patch("telethon.TelegramClient", return_value=client):
            with self.assertRaises(TimeoutError):
                asyncio.run(cleanup.channel_step(self.plan(phase="channel")))

    def test_deletes_only_resolved_named_files_and_preserves_other_songs(self):
        playback = self.library / "playback"
        playback.mkdir()
        paths = [self.library / self.name, playback / (self.name + ".download"),
                 playback / ("a" * 32 + ".mp3"), playback / ("a" * 32 + ".encoding.mp3")]
        for path in paths:
            path.write_bytes(b"test")
        other = self.library / ("b" * 32 + ".flac")
        other.write_bytes(b"normal")
        cleanup.delete_files(self.plan())
        cleanup.delete_files(self.plan())
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertEqual(other.read_bytes(), b"normal")
        with self.assertRaises(ValueError):
            cleanup.delete_files(self.plan(names=["..\\outside.flac"]))

    def test_auto_movie_exclusion_is_not_requeued_or_sent_for_manual_review(self):
        self.movie.enqueue(90000, False)
        self.movie.update(self.movie.pending(), "failed", "ValueError", result_id=70, document_id=987)
        review.automatic_module(review.get_module("v0"))
        row = self.movie.connection.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual((row["status"], row["result_id"]), ("excluded", 70))
        self.assertFalse(self.movie.reconcile(90000))
        bot = MagicMock()
        review.send_pending(bot, 99)
        bot.send_message.assert_not_called()

    def test_durable_cleanup_failure_resumes_and_preserves_exclusion(self):
        self.failed_playback()
        module = review.get_module("p")
        with patch.object(cleanup, "advance", side_effect=TimeoutError("offline")), \
                patch.object(review.logger, "exception"):
            review.automatic_module(module)
        row = self.playback.connection.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row["status"], "cleanup_pending")
        with self.assertRaises(cleanup.ExcludedMusic):
            cleanup.require_not_excluded(music.MUSIC_CHANNEL_ID, 20)
        with self.assertRaises(cleanup.ExcludedMusic):
            cleanup.require_file_not_excluded(self.name)
        with review.connection(module) as conn, conn:
            task = conn.execute("SELECT * FROM operation_music_cleanups").fetchone()
            self.assertEqual((task["completed"], task["error_code"]), (0, "TimeoutError"))
            conn.execute("UPDATE operation_music_cleanups SET retry_at=0")
        with patch.object(cleanup, "advance", side_effect=lambda plan: {**plan, "phase": "done"}):
            review.automatic_module(module)
        self.assertEqual(self.playback.connection.execute("SELECT status FROM jobs").fetchone()[0], "skipped")
        with self.assertRaises(cleanup.ExcludedMusic):
            cleanup.require_not_excluded(music.MUSIC_CHANNEL_ID, 20)
        cleanup.require_not_excluded(music.MUSIC_CHANNEL_ID, 21)
        cleanup.require_not_excluded("-100123", 20)

    def test_transient_music_failure_keeps_retry_and_generates_no_cleanup(self):
        self.playback.enqueue(self.name, 20, 100)
        self.playback.update(self.name, "failed", "TimeoutError", 60)
        with patch.object(cleanup, "advance") as advance:
            review.automatic_module(review.get_module("p"))
        advance.assert_not_called()
        row = self.playback.connection.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual((row["status"], row["retryable"]), ("failed", 1))

    def test_exclusion_blocks_reimport_before_mysql_or_download(self):
        self.failed_playback()
        with patch.object(cleanup, "advance", side_effect=TimeoutError), patch.object(review.logger, "exception"):
            review.automatic_module(review.get_module("p"))
        import music_dedupe
        conn = MagicMock()
        download = MagicMock()
        with self.assertRaises(cleanup.ExcludedMusic):
            music_dedupe.save_track(conn, "Title", "Artist", "", self.name, download,
                                   channel_id=music.MUSIC_CHANNEL_ID, message_id=20)
        conn.cursor.assert_not_called()
        download.assert_not_called()

    def test_inflight_stream_cannot_recreate_excluded_cache(self):
        self.failed_playback()
        with patch.object(cleanup, "advance", side_effect=TimeoutError), patch.object(review.logger, "exception"):
            review.automatic_module(review.get_module("p"))
        temporary = self.library / "temporary.flac"
        temporary.write_bytes(b"fLaC" + b"x" * 96)
        with self.assertRaises(cleanup.ExcludedMusic):
            music.commit_stream_cache(temporary, self.name, 100)
        self.assertFalse((self.library / self.name).exists())
        self.assertTrue(temporary.exists())

    def test_flood_wait_persists_and_blocks_other_cleanup_modules(self):
        from telethon.errors import FloodWaitError
        self.failed_playback()
        with patch.object(cleanup, "advance", side_effect=FloodWaitError(request=None, capture=120)), \
                patch.object(review.logger, "exception"):
            review.automatic_module(review.get_module("p"))
        import time
        self.assertGreater(review.cleanup_cooldown(), time.time() + 100)
        with patch.object(review, "automatic_module") as process:
            review.automatic_once()
        self.assertTrue(all(call.args[0].key.startswith("v") for call in process.call_args_list))

    def test_unknown_import_does_not_block_safe_tasks(self):
        self.ingest.enqueue(123, 10, False, "2026-11-04")
        self.ingest.update(self.ingest.pending(), "uncertain", "Interrupted")
        self.ingest.enqueue(123, 11, False, "2026-11-04")
        self.ingest.update(self.ingest.pending(), "failed", "ValueError")
        with patch.object(cleanup, "advance", side_effect=lambda plan: {**plan, "phase": "done"}), \
                patch.object(review.logger, "exception"):
            review.automatic_module(review.get_module("a"))
        rows = self.ingest.connection.execute("SELECT message,status FROM jobs ORDER BY message").fetchall()
        self.assertEqual([tuple(row) for row in rows], [(10, "uncertain"), (11, "skipped")])

    def test_old_non_news_manual_callback_is_rejected_without_state_change(self):
        self.failed_playback()
        module = review.get_module("p")
        with review.connection(module) as conn:
            review.scan(module, conn)
            ticket = conn.execute("SELECT * FROM operation_reviews").fetchone()
        call = SimpleNamespace(id="callback", data=review.callback(module, ticket, "ok_retry"),
                               from_user=SimpleNamespace(id=99),
                               message=SimpleNamespace(chat=SimpleNamespace(id=99, type="private")))
        bot = MagicMock()
        review.handle_callback(bot, call)
        self.assertTrue(bot.answer_callback_query.call_args.kwargs["show_alert"])
        self.assertEqual(self.playback.connection.execute("SELECT status FROM jobs").fetchone()[0], "failed")

    def test_mysql_identity_mismatch_stops_cleanup(self):
        db = MagicMock()
        cur = db.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [{"id": 1, "file_name": "b" * 32 + ".flac",
                                     "channel_id": music.MUSIC_CHANNEL_ID, "message_id": 20, "file_size": 100}]
        from contextlib import nullcontext
        with patch.object(cleanup, "get_conn", return_value=db), \
                patch("music_dedupe.import_lock", return_value=nullcontext()):
            with self.assertRaisesRegex(ValueError, "身份不匹配"):
                cleanup.plan_tracks(self.plan())
        db.rollback.assert_called_once()


@unittest.skipUnless(os.getenv("MUSIC_INTEGRATION") == "1", "Opt-in isolated MySQL test")
class CleanupIntegrationTests(unittest.TestCase):
    def test_hard_delete_prunes_references_promotes_healthy_alias_and_keeps_unrelated(self):
        from db import get_conn
        import music_social
        database = "music_cleanup_test_" + uuid.uuid4().hex
        admin = get_conn()
        try:
            with admin.cursor() as cur:
                cur.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")

            def connect():
                conn = get_conn()
                conn.select_db(database)
                return conn

            with patch.object(music, "get_conn", side_effect=connect), \
                    patch.object(music_social, "get_conn", side_effect=connect), \
                    patch.object(cleanup, "get_conn", side_effect=connect):
                music.ensure_tables()
                music_social.ensure_tables()
                conn = connect()
                try:
                    with conn.cursor() as cur:
                        for ident in range(1, 5):
                            cur.execute("INSERT INTO music_tracks(id,title,artist,file_name,channel_id,message_id,dedupe_key,merged_into)"
                                        " VALUES(%s,%s,'Artist',%s,%s,%s,%s,%s)",
                                        (ident, f"Song{ident}", str(ident) * 32 + ".flac",
                                         music.MUSIC_CHANNEL_ID, ident * 10,
                                         str(ident) * 64 if ident != 2 else None, 1 if ident == 2 else None))
                        cur.execute("INSERT INTO music_profiles VALUES(99,%s)", (json.dumps({
                            "favorites": [1, 3, 4], "recent": [3, 1, 4],
                            "resume": {"id": 3, "position": 10}, "mode": "shuffle",
                        }),))
                        cur.execute("INSERT INTO music_playlists(user_id,name,tracks,share_token) "
                                    "VALUES(99,'Test','[1,3,4]',%s)", ("a" * 32,))
                    conn.commit()
                    plan = {"tracks": [
                        {"id": ident, "file_name": str(ident) * 32 + ".flac",
                         "channel_id": music.MUSIC_CHANNEL_ID, "message_id": ident * 10}
                        for ident in (1, 3)
                    ]}
                    cleanup.disable_tracks(plan)
                    cleanup.delete_tracks(plan)
                    cleanup.delete_tracks(plan)
                    with conn.cursor() as cur:
                        cur.execute("SELECT id,merged_into,dedupe_key FROM music_tracks ORDER BY id")
                        rows = cur.fetchall()
                        self.assertEqual([row["id"] for row in rows], [2, 4])
                        self.assertIsNone(rows[0]["merged_into"])
                        self.assertEqual(rows[0]["dedupe_key"], "1" * 64)
                        cur.execute("SELECT document FROM music_profiles WHERE user_id=99")
                        profile = json.loads(cur.fetchone()["document"])
                        self.assertEqual(profile["favorites"], [2, 4])
                        self.assertEqual(profile["recent"], [2, 4])
                        self.assertIsNone(profile["resume"])
                        cur.execute("SELECT tracks,revision FROM music_playlists")
                        playlist = cur.fetchone()
                        self.assertEqual(json.loads(playlist["tracks"]), [2, 4])
                        self.assertEqual(playlist["revision"], 3)
                finally:
                    conn.close()
        finally:
            with admin.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
            admin.close()


if __name__ == "__main__":
    unittest.main()
