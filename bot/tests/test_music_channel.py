from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import wave

import requests
import music
import api_server


class MusicChannelTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        state = patch.dict("os.environ", {"LOCALAPPDATA": str(self.root)})
        state.start()
        self.addCleanup(state.stop)
        source = self.root / "tone.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * 8000)
        self.data = source.read_bytes()
        self.media = SimpleNamespace(
            file_id="telegram-audio-id", file_size=len(self.data), file_name="song.wav",
            mime_type="audio/wav", title="Test song", performer="Test artist",
        )
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=int(music.MUSIC_CHANNEL_ID)),
            message_id=35, audio=self.media, document=None,
        )
        self.name = "a" * 32 + ".wav"
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchone.return_value = None
        lock = patch.object(music, "import_lock", side_effect=lambda conn: nullcontext())
        lock.start()
        self.addCleanup(lock.stop)

    def responses(self, content=None):
        info = MagicMock()
        info.ok = True
        info.json.return_value = {"ok": True, "result": {
            "file_path": "music/file.wav", "file_size": len(self.data),
        }}
        media = MagicMock()
        media.ok = True
        media.iter_content.return_value = [self.data if content is None else content]
        return [MagicMock(__enter__=MagicMock(return_value=info)),
                MagicMock(__enter__=MagicMock(return_value=media))]

    def test_download_validates_atomic_file_and_cache_hit(self):
        with patch.object(music, "library_dir", return_value=self.root / "cache"), \
                patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test-only"}), \
                patch.object(music.requests, "get", side_effect=self.responses()) as get:
            path = music.download_channel_audio("id", self.name, len(self.data))
            self.assertEqual(path.read_bytes(), self.data)
            self.assertEqual(list(path.parent.iterdir()), [path])
            self.assertEqual(music.download_channel_audio("id", self.name, len(self.data)), path)
            self.assertEqual(get.call_count, 2)

    def test_incomplete_bad_header_and_oversize_never_publish(self):
        for data in (b"partial", b"x" * len(self.data)):
            with self.subTest(length=len(data)), \
                    patch.object(music, "library_dir", return_value=self.root / "cache"), \
                    patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test-only"}), \
                    patch.object(music.requests, "get", side_effect=self.responses(data)), \
                    self.assertRaises(ValueError):
                music.download_channel_audio("id", self.name, len(self.data))
            self.assertEqual(list((self.root / "cache").iterdir()), [])
        with patch.object(music.requests, "get") as get, self.assertRaises(ValueError):
            music.download_channel_audio("id", self.name, music.TELEGRAM_MAX_BYTES + 1)
        get.assert_not_called()

    def test_network_error_does_not_reveal_token_url(self):
        with patch.object(music, "library_dir", return_value=self.root / "cache"), \
                patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "secret-test-token"}), \
                patch.object(music.requests, "get", side_effect=requests.ConnectionError("https://bot/secret-test-token")), \
                self.assertRaises(ValueError) as error:
            music.download_channel_audio("id", self.name, len(self.data))
        self.assertNotIn("secret-test-token", str(error.exception))

    def test_only_configured_channel_imports_and_updates_preserve_hidden_state(self):
        wrong = SimpleNamespace(chat=SimpleNamespace(id=-1001), audio=self.media)
        with patch.object(music, "get_conn") as db, patch.object(music, "download_channel_audio") as download:
            self.assertFalse(music.save_channel_message(wrong))
            db.assert_not_called()
            download.assert_not_called()
        with patch.object(music, "get_conn", return_value=self.conn), \
                patch.object(music, "download_channel_audio") as download:
            self.assertTrue(music.save_channel_message(self.message))
            self.assertTrue(music.save_channel_message(self.message))
        sql, values = self.cur.execute.call_args.args
        self.assertIn("INSERT INTO music_tracks", sql)
        self.assertNotIn("enabled", sql)
        self.assertEqual(values[0:2], ("Test song", "Test artist"))
        self.assertEqual(values[5:7], (music.MUSIC_CHANNEL_ID, 35))
        self.assertEqual(download.call_count, 2)

    def test_document_metadata_fallback_and_failure_notification(self):
        self.media.title = None
        self.media.performer = None
        self.message.audio = None
        self.message.document = self.media
        with patch.object(music, "get_conn", return_value=self.conn), \
                patch.object(music, "download_channel_audio"):
            self.assertTrue(music.save_channel_message(self.message))
        self.assertEqual(self.cur.execute.call_args.args[1][:2], ("song", "未知歌手"))
        bot = MagicMock()
        with patch.object(music, "get_conn", return_value=self.conn), \
                patch.object(music, "download_channel_audio", side_effect=ValueError("测试下载失败")):
            self.assertTrue(music.handle_channel_message(bot, self.message, [123]))
        self.assertIn("测试下载失败", bot.send_message.call_args.args[1])

    def test_metadata_repair_only_updates_labels_and_identity(self):
        self.cur.fetchone.side_effect = [{"id": 8, "title": "Old", "artist": "<unknown>"}, None]
        with patch.object(music, "get_conn", return_value=self.conn):
            self.assertTrue(music.refresh_channel_metadata(self.message))
        sql, args = self.cur.execute.call_args.args
        self.assertEqual(args[:2], ("Test song", "Test artist"))
        self.assertNotIn("file_name", sql)
        self.assertNotIn("enabled", sql)
        self.assertNotIn("merged_into", sql)
        self.conn.commit.assert_called_once()

    def test_metadata_repair_conflict_is_not_silently_merged(self):
        self.cur.fetchone.side_effect = [{"id": 8, "title": "Old", "artist": "<unknown>"}, {"id": 9}]
        with patch.object(music, "get_conn", return_value=self.conn), self.assertRaisesRegex(ValueError, "人工核对"):
            music.refresh_channel_metadata(self.message)
        self.conn.rollback.assert_called_once()
        self.assertFalse(any(call.args[0].startswith("UPDATE") for call in self.cur.execute.call_args_list))

    def test_cache_miss_redownload_and_range_afterwards(self):
        target = self.root / self.name
        self.cur.fetchone.return_value = {
            "file_name": self.name, "telegram_file_id": "id", "file_size": len(self.data),
        }
        with patch.object(music, "get_conn", return_value=self.conn), \
                patch.object(music, "library_dir", return_value=self.root), \
                patch.object(music, "download_channel_audio", return_value=target) as download:
            download.side_effect = lambda *_: (target.write_bytes(self.data), target)[1]
            path, mime = music.audio_file(1)
            download.assert_called_once_with("id", self.name, len(self.data))
        with patch.object(api_server, "music_audio_file", return_value=(path, mime)):
            response = api_server.app.test_client().get("/music-audio/1", headers={"Range": "bytes=100-199"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, self.data[100:200])
            response.close()

    def test_external_forward_waits_for_collector_without_bulk_download(self):
        from music_sources import SOURCES
        source = next(iter(SOURCES))
        self.message.forward_origin = SimpleNamespace(chat=SimpleNamespace(id=int(f"-100{source}")))
        with patch.object(music, "get_conn") as db, patch.object(music, "download_channel_audio") as download:
            self.assertTrue(music.save_channel_message(self.message))
        db.assert_not_called()
        download.assert_not_called()

    def test_owned_channel_cache_miss_uses_owned_message_not_original_source(self):
        target = self.root / self.name
        self.cur.fetchone.return_value = {
            "file_name": self.name, "telegram_file_id": "id", "file_size": len(self.data),
            "channel_id": music.MUSIC_CHANNEL_ID, "message_id": 1234,
        }
        def download(*args):
            target.write_bytes(self.data)
            return target
        with patch.object(music, "get_conn", return_value=self.conn), \
                patch.object(music, "library_dir", return_value=self.root), \
                patch.object(music, "download_owned_cached_audio", side_effect=download) as cached:
            path, _ = music.audio_file(1)
        self.assertEqual(path, target)
        cached.assert_called_once_with(1234, self.name, len(self.data))

    def test_lazy_cache_download_is_atomic_validated_and_incomplete_data_removed(self):
        import music_mtproto
        name = "b" * 32 + ".wav"
        with patch.object(music, "library_dir", return_value=self.root / "lazy"), \
                patch.object(music_mtproto, "download_owned_audio",
                             side_effect=lambda ident, path, size: path.write_bytes(self.data)):
            path = music.download_owned_cached_audio(1234, name, len(self.data))
            self.assertEqual(path.read_bytes(), self.data)
            self.assertEqual(list(path.parent.iterdir()), [path])
            self.assertEqual(music.download_owned_cached_audio(1234, name, len(self.data)), path)
        with patch.object(music, "library_dir", return_value=self.root / "broken"), \
                patch.object(music_mtproto, "download_owned_audio",
                             side_effect=lambda ident, path, size: path.write_bytes(b"partial")), \
                self.assertRaisesRegex(ValueError, "不完整"):
            music.download_owned_cached_audio(1234, name, len(self.data))
        self.assertEqual(list((self.root / "broken").iterdir()), [])

    def test_cache_eviction_never_deletes_unrecoverable_local_import(self):
        import music_mtproto
        target_dir = self.root / "evict"
        target_dir.mkdir()
        old = target_dir / ("c" * 32 + ".wav")
        manual = target_dir / ("d" * 32 + ".wav")
        old.write_bytes(self.data)
        manual.write_bytes(self.data)
        self.cur.fetchall.return_value = [{"file_name": old.name}]
        with patch.object(music, "library_dir", return_value=target_dir), \
                patch.object(music, "get_conn", return_value=self.conn), \
                patch.object(music, "CHANNEL_CACHE_BYTES", len(self.data) * 2), \
                patch.object(music_mtproto, "download_owned_audio",
                             side_effect=lambda ident, path, size: path.write_bytes(self.data)):
            result = music.download_owned_cached_audio(1234, self.name, len(self.data))
        self.assertFalse(old.exists())
        self.assertTrue(manual.exists())
        self.assertTrue(result.exists())

    def test_owned_cache_supports_over_bot_api_20_mb_limit(self):
        import music_mtproto
        size = 21 * 1024 * 1024
        def download(ident, path, expected):
            with path.open("wb") as stream:
                stream.write(self.data)
                stream.truncate(expected)
        with patch.object(music, "library_dir", return_value=self.root), \
                patch.object(music_mtproto, "download_owned_audio", side_effect=download):
            result = music.download_owned_cached_audio(1234, self.name, size)
        self.assertEqual(result.stat().st_size, size)

    def test_mislabeled_channel_mp3_plays_with_real_mp4_mime_but_manual_import_stays_strict(self):
        import music_mtproto
        data = b"\0\0\0\x18ftypdash" + b"\0" * 40
        name = "e" * 32 + ".mp3"
        with patch.object(music, "library_dir", return_value=self.root), \
                patch.object(music_mtproto, "download_owned_audio",
                             side_effect=lambda ident, path, size: path.write_bytes(data)):
            path = music.download_owned_cached_audio(1234, name, len(data))
        with self.assertRaisesRegex(ValueError, "文件头"):
            music.validate_audio(path)
        self.cur.fetchone.return_value = {"file_name": name}
        with patch.object(music, "library_dir", return_value=self.root), \
                patch.object(music, "get_conn", return_value=self.conn):
            self.assertEqual(music.audio_file(1), (path, "audio/mp4"))

    def test_channel_flac_and_flac_mislabeled_as_mp3(self):
        import music_mtproto
        data = b"fLaC" + b"\0" * 40
        self.assertEqual(music.telegram_audio_name(SimpleNamespace(file_name="song.flac")), ".flac")
        self.assertEqual(music.telegram_audio_name(SimpleNamespace(mime_type="audio/flac")), ".flac")
        for name in ("f" * 32 + ".flac", "f" * 32 + ".mp3"):
            with patch.object(music, "library_dir", return_value=self.root), \
                    patch.object(music_mtproto, "download_owned_audio",
                                 side_effect=lambda ident, path, size: path.write_bytes(data)):
                path = music.download_owned_cached_audio(1234, name, len(data))
            self.assertEqual(music.validate_audio(path, strict_suffix=False), ".flac")
    def test_schema_migration_adds_source_and_unique_message_columns(self):
        self.cur.fetchall.return_value = [{"Field": name} for name in (
            "id", "title", "artist", "album", "file_name", "enabled", "created_at",
        )]
        self.cur.fetchone.return_value = None
        with patch.object(music, "get_conn", return_value=self.conn), patch.object(music, "ensure_schema") as dedupe:
            music.ensure_tables()
        dedupe.assert_called_once_with(self.conn)
        statements = [call.args[0] for call in self.cur.execute.call_args_list]
        self.assertTrue(any("ADD COLUMN telegram_file_id" in sql for sql in statements))
        self.assertTrue(any("UNIQUE KEY idx_music_message" in sql for sql in statements))


if __name__ == "__main__":
    unittest.main()
