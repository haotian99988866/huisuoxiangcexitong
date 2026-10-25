import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from flask import Flask

import music
import music_mtproto
import music_stream
from music_stream import RemoteAudio, audio_response


class MusicStreamTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.data = b"fLaC" + bytes(range(256)) * 1024
        self.source = RemoteAudio(1020, len(self.data), "audio/flac")
        self.ranges = []
        self.closed = False

    def upstream(self, message, size, start, end):
        self.ranges.append((message, size, start, end))
        try:
            for offset in range(start, end + 1, 65536):
                yield self.data[offset:min(offset + 65536, end + 1)]
        finally:
            self.closed = True

    def test_full_and_ranges_match_exact_bytes_and_headers(self):
        for header, start, stop, status in (
            (None, 0, len(self.data), 200),
            ("bytes=0-1023", 0, 1024, 206),
            ("bytes=123-456", 123, 457, 206),
            ("bytes=1000-", 1000, len(self.data), 206),
            ("bytes=-99", len(self.data) - 99, len(self.data), 206),
            ("bytes=0-99999999", 0, len(self.data), 206),
        ):
            with self.subTest(header=header), self.app.test_request_context(
                headers={"Range": header} if header else {}
            ), patch.object(music_stream, "stream_owned_audio", side_effect=self.upstream):
                response = audio_response(self.source)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.headers["Content-Length"], str(stop - start))
                self.assertEqual(response.headers["Accept-Ranges"], "bytes")
                self.assertEqual(b"".join(response.response), self.data[start:stop])
                response.close()
                if header:
                    self.assertEqual(response.headers["Content-Range"],
                                     f"bytes {start}-{stop - 1}/{len(self.data)}")

    def test_first_bytes_available_before_complete_download_and_cancel_closes(self):
        consumed = []
        def slow(*args):
            try:
                consumed.append(1)
                yield b"first"
                consumed.append(2)
                yield b"later"
            finally:
                consumed.append("closed")
        with self.app.test_request_context(), patch.object(
            music_stream, "stream_owned_audio", side_effect=slow
        ):
            response = audio_response(self.source)
            self.assertEqual(consumed, [1])
            iterator = iter(response.response)
            self.assertEqual(next(iterator), b"first")
            response.close()
            self.assertEqual(consumed, [1, "closed"])

    def test_invalid_and_multi_ranges_never_connect(self):
        for header in ("bytes=99999999-", "bytes=8-2", "bytes=0-1,3-4", "bad", "items=0-9"):
            with self.subTest(header=header), self.app.test_request_context(
                headers={"Range": header}
            ), patch.object(music_stream, "stream_owned_audio") as upstream:
                response = audio_response(self.source)
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response.headers["Content-Range"], f"bytes */{len(self.data)}")
                upstream.assert_not_called()

    def test_head_never_downloads(self):
        with self.app.test_request_context(method="HEAD"), patch.object(
            music_stream, "stream_owned_audio"
        ) as upstream:
            response = audio_response(self.source)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Content-Length"], str(len(self.data)))
            upstream.assert_not_called()

    def test_stream_limit_returns_explicit_retry_without_starting_download(self):
        with self.app.test_request_context(), patch.object(
            music_stream, "stream_slots"
        ) as slots, patch.object(music_stream, "stream_owned_audio") as upstream:
            slots.acquire.return_value = False
            response = audio_response(self.source)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["Retry-After"], "3")
            upstream.assert_not_called()
            slots.release.assert_not_called()

    def test_api_routes_uncached_audio_to_stream_and_reports_initial_failure(self):
        import api_server
        client = api_server.app.test_client()
        with patch.object(api_server, "music_audio_file", return_value=self.source), patch.object(
            music_stream, "stream_owned_audio", side_effect=self.upstream
        ):
            response = client.get("/music-audio/372", headers={"Range": "bytes=0-1023"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, self.data[:1024])
            response.close()
        def unavailable(*args):
            raise ValueError("unavailable")
            yield
        with patch.object(api_server, "music_audio_file", return_value=self.source), patch.object(
            music_stream, "stream_owned_audio", side_effect=unavailable
        ):
            self.assertEqual(client.get("/music-audio/372").status_code, 503)

    def test_upstream_failure_releases_slot_and_is_not_silenced(self):
        def broken(*args):
            raise ValueError("offline")
            yield
        for _ in range(4):
            with self.app.test_request_context(), patch.object(
                music_stream, "stream_owned_audio", side_effect=broken
            ), self.assertRaisesRegex(ValueError, "offline"):
                audio_response(self.source)

    def test_late_failure_is_truncated_not_successful_eof(self):
        def broken(*args):
            yield b"first"
            raise ValueError("offline")
        with self.app.test_request_context(), patch.object(
            music_stream, "stream_owned_audio", side_effect=broken
        ):
            response = audio_response(self.source)
            with self.assertRaisesRegex(ValueError, "offline"):
                b"".join(response.response)
            response.close()

    def test_cold_owned_audio_returns_stream_without_global_download_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            conn = MagicMock()
            conn.cursor.return_value.__enter__.return_value.fetchone.return_value = {
                "file_name": "a" * 32 + ".flac", "file_size": 26341137,
                "telegram_file_id": "id", "channel_id": music.MUSIC_CHANNEL_ID, "message_id": 1020,
            }
            with patch.object(music, "get_conn", return_value=conn), patch.object(
                music, "library_dir", return_value=Path(folder)
            ), patch.object(music, "download_owned_cached_audio") as download, patch(
                "music_playback.prepared_audio", return_value=None
            ):
                self.assertEqual(music.audio_file(372, stream=True),
                                 RemoteAudio(1020, 26341137, "audio/flac", "a" * 32 + ".flac"))
                download.assert_not_called()

    def test_completed_stream_cached_atomically_but_cancelled_stream_not_cached(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            music, "library_dir", return_value=Path(folder)
        ), patch.object(music_stream, "stream_owned_audio", side_effect=self.upstream):
            source = RemoteAudio(1020, len(self.data), "audio/flac", "a" * 32 + ".flac")
            with self.app.test_request_context():
                response = audio_response(source)
                iterator = iter(response.response)
                next(iterator)
                response.close()
            self.assertEqual(list(Path(folder).iterdir()), [])
            with self.app.test_request_context():
                response = audio_response(source)
                self.assertEqual(b"".join(response.response), self.data)
                response.close()
            self.assertEqual((Path(folder) / source.cache_name).read_bytes(), self.data)
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_sync_bridge_closes_async_source_on_cancel(self):
        closed = []
        async def chunks(*args):
            try:
                yield b"one"
                yield b"two"
            finally:
                closed.append(True)
        with patch.object(music_mtproto, "_audio_chunks", side_effect=chunks):
            stream = music_mtproto.stream_owned_audio(1, 6, 0, 5)
            self.assertEqual(next(stream), b"one")
            stream.close()
        self.assertEqual(closed, [True])

    def test_mtproto_unaligned_range_exact_and_disconnects(self):
        client = MagicMock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.is_user_authorized = AsyncMock(return_value=True)
        client.get_me = AsyncMock(return_value=SimpleNamespace(bot=True))
        client.get_entity = AsyncMock(return_value=object())
        client.get_messages = AsyncMock(return_value=[
            SimpleNamespace(document=object(), file=SimpleNamespace(size=len(self.data)))])
        async def download(document, **kwargs):
            self.assertEqual(kwargs["offset"], 65536)
            for offset in range(kwargs["offset"], len(self.data), 65536):
                yield self.data[offset:offset + 65536]
        client.iter_download = download
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (1, "address", 443, b"x" * 256)
        async def collect():
            return b"".join([part async for part in music_mtproto._audio_chunks(
                1020, len(self.data), 65540, 160000)])
        with patch("channel_sync.credentials", return_value=(1, "hash")), patch(
            "channel_sync.state_directory", return_value=Path(tempfile.gettempdir())
        ), patch.object(music_mtproto.sqlite3, "connect", return_value=conn), patch.object(
            music_mtproto, "TelegramClient", return_value=client
        ):
            self.assertEqual(asyncio.run(collect()), self.data[65540:160001])
        client.disconnect.assert_awaited_once()
