import asyncio
from pathlib import Path
import tempfile
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

    def test_persistent_priority_retry_wait_and_ready_files(self):
        self.store.enqueue("b" * 32 + ".flac", 1021, 200, True)
        self.assertEqual(self.store.pending()["message"], 1021)
        self.store.update("b" * 32 + ".flac", "failed", "offline", 60)
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
            with self.assertRaisesRegex(ValueError, "budget"):
                await playback.prepare(self.store.pending())

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
