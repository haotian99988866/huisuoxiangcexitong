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
