import asyncio
from contextlib import closing
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import MagicMock, patch
import wave

from telethon import errors

import music
import music_playback as playback


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.library = self.root / "library"
        self.library.mkdir()
        for target, value in (
            ("channel_sync.state_directory", self.root),
            ("music.library_dir", self.library),
        ):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.name = "a" * 32 + ".flac"
        self.store = playback.PlaybackStore()
        self.addCleanup(self.store.close)
        self.store.enqueue(self.name, 1020, 100)

    def test_legacy_jobs_migration_preserves_queue_and_enables_retry(self):
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        with closing(sqlite3.connect(legacy_root / "music-playback.sqlite3")) as connection, connection:
            connection.execute("""CREATE TABLE jobs (
                name TEXT PRIMARY KEY, message INTEGER NOT NULL, size INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', priority INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0, error TEXT)""")
            connection.execute("INSERT INTO jobs(name,message,size,status,error) VALUES(?,?,?,'failed','ValueError')",
                               (self.name, 1020, 100))
        with patch("channel_sync.state_directory", return_value=legacy_root):
            observer = playback.PlaybackStore()
            try:
                row = observer.pending()
                self.assertEqual((row["name"], row["status"], row["error"], row["retryable"]),
                                 (self.name, "failed", "ValueError", 1))
            finally:
                observer.close()

    def test_terminal_failure_remains_visible_and_does_not_block_queue_or_restart(self):
        folder = playback.playback_directory()
        folder.mkdir()
        (folder / (self.name + ".download")).write_bytes(b"invalid")
        job = self.store.pending()
        with patch.object(playback.logger, "error") as log:
            playback.record_preparation_failure(self.store, job, playback.PlaybackInvalidAudio("corrupt"))
        row = self.store.connection.execute("SELECT * FROM jobs WHERE name=?", (self.name,)).fetchone()
        self.assertEqual((row["status"], row["error"], row["retryable"]),
                         ("failed", "PlaybackInvalidAudio", 0))
        self.assertEqual(log.call_args.args[-1], "pending automatic cleanup; automatic retry disabled")
        self.store.enqueue(self.name, 1020, 100, True)
        self.store.enqueue("b" * 32 + ".flac", 1021, 200)
        observer = playback.PlaybackStore()
        try:
            self.assertEqual(observer.pending()["message"], 1021)
        finally:
            observer.close()

    def test_retryable_network_failure_keeps_backoff(self):
        with patch.object(playback.logger, "error"):
            playback.record_preparation_failure(self.store, self.store.pending(), TimeoutError("offline"))
        row = self.store.connection.execute("SELECT * FROM jobs WHERE name=?", (self.name,)).fetchone()
        self.assertEqual((row["status"], row["error"], row["retryable"]), ("failed", "TimeoutError", 1))
        self.assertIsNone(self.store.pending())
        self.store.enqueue("b" * 32 + ".flac", 1021, 200)
        self.assertEqual(self.store.pending()["message"], 1021)

    def test_known_terminal_errors_are_not_automatically_retried(self):
        for error in (playback.PlaybackOutputInvalid(), playback.PlaybackCopyTooLarge(),
                      playback.PlaybackPartialInvalid(), playback.AudioSourceUnavailable()):
            with self.subTest(error=type(error).__name__), patch.object(playback.logger, "error"):
                self.store.update(self.name, "queued")
                playback.record_preparation_failure(self.store, self.store.pending(), error)
                self.assertIsNone(self.store.pending())

    async def test_download_timeout_removes_empty_partial_and_unblocks_other_jobs(self):
        async def chunks(*args, **kwargs):
            raise TimeoutError("offline")
            yield
        with patch("music_mtproto._audio_chunks", side_effect=chunks):
            with self.assertRaises(TimeoutError):
                await playback.prepare(self.store.pending())
        folder = playback.playback_directory()
        self.assertEqual(list(folder.glob("*.download")), [])
        with patch.object(playback.logger, "error"):
            playback.record_preparation_failure(self.store, self.store.pending(), TimeoutError("offline"))
        self.store.enqueue("b" * 32 + ".flac", 1021, 200)
        self.assertEqual(self.store.pending()["message"], 1021)

    def test_empty_legacy_partial_does_not_hold_queue_during_backoff(self):
        folder = playback.playback_directory()
        folder.mkdir()
        (folder / (self.name + ".download")).touch()
        self.store.update(self.name, "failed", "TimeoutError", 60)
        self.store.enqueue("b" * 32 + ".flac", 1021, 200)
        self.assertEqual(self.store.pending()["message"], 1021)

    def test_real_corrupt_audio_is_classified_without_relaxing_decoder(self):
        source = self.library / self.name
        source.write_bytes(b"fLaC" + b"x" * 96)
        before = source.read_bytes()
        with self.assertRaises(playback.PlaybackInvalidAudio):
            playback.transcode(source, self.library / "invalid.mp3")
        self.assertEqual(source.read_bytes(), before)

    def test_conversion_resource_error_is_not_misclassified_as_corrupt_audio(self):
        with patch("subprocess.run", return_value=MagicMock(
            returncode=1, stderr=b"Error opening output file: No space left on device",
        )):
            with self.assertRaisesRegex(RuntimeError, "resource"):
                playback.transcode(self.library / self.name, self.library / "output.mp3")

    async def test_unavailable_source_discards_only_download_partial(self):
        folder = playback.playback_directory()
        folder.mkdir()
        partial = folder / (self.name + ".download")
        partial.write_bytes(b"x" * 40)
        async def chunks(*args, **kwargs):
            raise playback.AudioSourceUnavailable("source missing")
            yield
        with patch("music_mtproto._audio_chunks", side_effect=chunks):
            with self.assertRaises(playback.AudioSourceUnavailable):
                await playback.prepare(self.store.pending())
        self.assertFalse(partial.exists())

    def test_persistent_priority_retry_wait_and_ready_files(self):
        self.store.enqueue("b" * 32 + ".flac", 1021, 200, True)
        self.assertEqual(self.store.pending()["message"], 1021)
        self.store.update("b" * 32 + ".flac", "failed", "offline", 60)
        self.assertEqual(self.store.pending()["message"], 1020)
        self.store.update("b" * 32 + ".flac", "failed")
        self.assertEqual(self.store.pending()["message"], 1020)
        self.store.wait_until(100)
        self.store.wait_until(50)
        observer = playback.PlaybackStore()
        try:
            self.assertEqual(observer.wait_until(), 100)
            self.assertEqual(observer.pending()["message"], 1020)
        finally:
            observer.close()
        output = playback.playback_directory() / ("a" * 32 + ".mp3")
        output.parent.mkdir()
        output.write_bytes(b"ID3" + b"x" * 100)
        self.store.update(self.name, "ready")
        self.assertEqual(playback.prepared_audio(self.name, 1020, 100), output)
        output.unlink()
        self.assertIsNone(playback.prepared_audio(self.name, 1020, 100))
        self.assertEqual(self.store.pending()["status"], "queued")

    def test_invalid_source_cannot_escape_playback_directory(self):
        for name, message, size in (("..\\outside.flac", 1, 100),
                                    (self.name, 0, 100), (self.name, 1, 200 * 1024 * 1024)):
            with self.assertRaises(ValueError):
                self.store.enqueue(name, message, size)

    def test_partial_takes_precedence_and_waits_before_opening_another_download(self):
        folder = playback.playback_directory()
        folder.mkdir()
        partial = folder / (self.name + ".download")
        partial.write_bytes(b"a" * 40)
        self.store.enqueue("b" * 32 + ".flac", 1021, 200, True)
        self.assertEqual(self.store.pending()["message"], 1020)
        self.store.update(self.name, "failed", "offline", 60)
        self.assertIsNone(self.store.pending())
        self.store.update(self.name, "queued")
        self.assertEqual(self.store.pending()["message"], 1020)
        partial.unlink()
        self.assertEqual(self.store.pending()["message"], 1021)

    def test_playback_lock_excludes_another_converter_not_channel_listener(self):
        from channel_sync import single_instance
        with single_instance(self.root), single_instance(self.root, "music-playback.lock"):
            with self.assertRaises(RuntimeError):
                with single_instance(self.root, "music-playback.lock"):
                    self.fail("A second converter acquired the lock")
        with single_instance(self.root, "music-playback.lock"):
            pass

    async def test_download_resumes_partial_and_publishes_only_complete_result(self):
        folder = playback.playback_directory()
        folder.mkdir()
        partial = folder / (self.name + ".download")
        partial.write_bytes(b"a" * 40)
        async def chunks(message, size, start, end, **kwargs):
            self.assertEqual((message, size, start, end), (1020, 100, 40, 99))
            yield b"b" * 60
        def encode(source, destination):
            self.assertEqual(source.read_bytes(), b"a" * 40 + b"b" * 60)
            destination.write_bytes(b"ID3" + b"x" * 30)
        with patch("music_mtproto._audio_chunks", side_effect=chunks), patch.object(
            playback, "transcode", side_effect=encode
        ):
            await playback.prepare(self.store.pending())
        self.assertFalse(partial.exists())
        self.assertTrue((folder / ("a" * 32 + ".mp3")).is_file())
        self.assertFalse((self.library / self.name).exists())

    async def test_failed_download_retains_only_resume_data_never_ready_copy(self):
        async def chunks(*args, **kwargs):
            yield b"a" * 40
            raise ConnectionError("offline")
        with patch("music_mtproto._audio_chunks", side_effect=chunks):
            with self.assertRaises(ConnectionError):
                await playback.prepare(self.store.pending())
        folder = playback.playback_directory()
        self.assertEqual((folder / (self.name + ".download")).stat().st_size, 40)
        self.assertEqual(list(folder.glob("*.mp3")), [])

    async def test_flood_wait_propagates_and_partial_is_reusable(self):
        async def chunks(*args, **kwargs):
            raise errors.FloodWaitError(request=None, capture=90)
            yield
        with patch("music_mtproto._audio_chunks", side_effect=chunks):
            with self.assertRaises(errors.FloodWaitError):
                await playback.prepare(self.store.pending())
        self.assertIsNone(playback.prepared_audio(self.name, 1020, 100))

    async def test_conversion_failure_removes_encoding_not_original(self):
        original = self.library / self.name
        original.write_bytes(b"fLaC" + b"x" * 96)
        def fail(source, destination):
            destination.write_bytes(b"partial")
            raise ValueError("corrupt")
        with patch.object(playback, "transcode", side_effect=fail):
            with self.assertRaises(ValueError):
                await playback.prepare(self.store.pending())
        self.assertEqual(original.stat().st_size, 100)
        self.assertEqual(list(playback.playback_directory().glob("*.mp3")), [])

    async def test_disk_budget_refuses_additional_preparation(self):
        with patch.object(playback, "PLAYBACK_BUDGET", 1):
            with self.assertRaisesRegex(playback.PlaybackBudgetReached, "budget"):
                await playback.prepare(self.store.pending())

    async def test_worker_keeps_pending_jobs_queued_when_cache_budget_is_full(self):
        class StopWorker(BaseException):
            pass

        folder = playback.playback_directory()
        folder.mkdir()
        (folder / "prepared.mp3").write_bytes(b"x")
        observer = None
        try:
            with patch.object(playback, "PLAYBACK_BUDGET", 1), \
                    patch.object(playback, "PlaybackStore", return_value=self.store), \
                    patch.object(playback.asyncio, "sleep", side_effect=StopWorker):
                with self.assertRaises(StopWorker):
                    await playback._playback_worker()
            observer = playback.PlaybackStore()
            self.assertEqual(observer.pending()["status"], "queued")
            health = observer.connection.execute(
                "SELECT last_error_code FROM worker_health WHERE worker='music_playback'"
            ).fetchone()
            self.assertEqual(health["last_error_code"], "PlaybackBudgetReached")
        finally:
            if observer:
                observer.close()

    async def test_invalid_download_does_not_block_the_rest_of_the_queue(self):
        folder = playback.playback_directory()
        folder.mkdir()
        partial = folder / (self.name + ".download")
        partial.write_bytes(b"invalid" + b"x" * 93)
        with patch.object(playback, "transcode", side_effect=ValueError("corrupt")):
            with self.assertRaises(ValueError):
                await playback.prepare(self.store.pending())
        self.assertFalse(partial.exists())
        self.store.update(self.name, "failed", "ValueError", 60)
        self.store.enqueue("b" * 32 + ".flac", 1021, 200)
        self.assertEqual(self.store.pending()["message"], 1021)
        self.assertEqual(list(folder.glob("*.mp3")), [])

    async def test_oversized_result_is_never_published_and_original_is_kept(self):
        original = self.library / self.name
        original.write_bytes(b"fLaC" + b"x" * 96)
        def oversized(source, destination):
            with destination.open("wb") as stream:
                stream.truncate(16 * 1024 * 1024 + 1)
        with patch.object(playback, "transcode", side_effect=oversized):
            with self.assertRaisesRegex(ValueError, "budget"):
                await playback.prepare(self.store.pending())
        self.assertTrue(original.is_file())
        self.assertEqual(list(playback.playback_directory().glob("*.mp3")), [])

    def test_real_ffmpeg_conversion_and_validation(self):
        original = self.library / ("c" * 32 + ".wav")
        with wave.open(str(original), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\0\0" * 16000)
        before = original.read_bytes()
        output = self.library / "test.mp3"
        playback.transcode(original, output)
        self.assertEqual(music.validate_audio(output), ".mp3")
        self.assertGreater(output.stat().st_size, 40000)
        self.assertEqual(original.read_bytes(), before)

    def test_api_prefers_prepared_file_without_downloading_or_changing_track(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = {
            "file_name": self.name, "telegram_file_id": "id", "file_size": 100,
            "channel_id": music.MUSIC_CHANNEL_ID, "message_id": 1020,
        }
        output = self.library / "prepared.mp3"
        with patch.object(music, "get_conn", return_value=conn), patch.object(
            playback, "prepared_audio", return_value=output
        ), patch.object(music, "download_owned_cached_audio") as download:
            self.assertEqual(music.audio_file(372, stream=True), (output, "audio/mpeg"))
            download.assert_not_called()
