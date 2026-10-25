from pathlib import Path
from contextlib import nullcontext
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import wave

import api_server
import music
from tests.test_tenants import config


class MusicTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "test.wav"
        with wave.open(str(self.source), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * 16000)
        self.client = api_server.app.test_client()
        lock = patch.object(music, "import_lock", side_effect=lambda conn: nullcontext())
        lock.start()
        self.addCleanup(lock.stop)

    def connection(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.lastrowid = 42
        cur.fetchone.return_value = None
        return conn, cur

    def test_import_copies_and_registers_without_original_path(self):
        conn, cur = self.connection()
        target = self.root / "library"
        with patch.object(music, "library_dir", return_value=target), \
                patch.object(music, "get_conn", return_value=conn):
            ident = music.add_track(self.source, " Song ", " Artist ", rights_confirmed=True)
        self.assertEqual(ident, 42)
        files = list(target.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), self.source.read_bytes())
        self.assertTrue(self.source.exists())
        self.assertEqual(cur.execute.call_args.args[1][:3], ("Song", "Artist", ""))
        self.assertNotIn(str(self.source), str(cur.execute.call_args))
        conn.commit.assert_called_once()

    def test_import_failure_rolls_back_and_removes_copies(self):
        conn, cur = self.connection()
        cur.execute.side_effect = api_server.pymysql.OperationalError("offline")
        target = self.root / "library"
        with patch.object(music, "library_dir", return_value=target), \
                patch.object(music, "get_conn", return_value=conn), \
                self.assertRaises(api_server.pymysql.OperationalError):
            music.add_track(self.source, "Song", "Artist", rights_confirmed=True)
        self.assertEqual(list(target.iterdir()), [])
        conn.rollback.assert_called_once()
        self.assertTrue(self.source.exists())

    def test_invalid_audio_and_missing_rights_do_not_write(self):
        bad = self.root / "bad.mp3"
        bad.write_bytes(b"not an audio file at all")
        with patch.object(music, "get_conn") as db:
            for source, title, rights in ((self.source, "Song", False),
                                           (bad, "Song", True), (self.source, "", True)):
                with self.subTest(source=source, rights=rights), self.assertRaises(ValueError):
                    music.add_track(source, title, "Artist", rights_confirmed=rights)
            db.assert_not_called()

    def test_stream_range_head_and_seek(self):
        with patch.object(api_server, "music_audio_file", return_value=(self.source, "audio/wav")):
            response = self.client.get("/music-audio/42", headers={"Range": "bytes=0-99"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, self.source.read_bytes()[:100])
            self.assertEqual(response.headers["Content-Range"], f"bytes 0-99/{self.source.stat().st_size}")
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            response.close()
            head = self.client.head("/music-audio/42")
            self.assertEqual(head.status_code, 200)
            self.assertEqual(head.data, b"")
            head.close()
            seek = self.client.get("/music-audio/42", headers={"Range": "bytes=1000-1999"})
            self.assertEqual(seek.status_code, 206)
            self.assertEqual(seek.data, self.source.read_bytes()[1000:2000])
            seek.close()

    def test_tenant_catalog_and_audio_denied_before_lookup(self):
        with patch.object(api_server, "load_tenant", return_value=config()), \
                patch.object(api_server, "music_catalog") as catalog, \
                patch.object(api_server, "music_audio_file") as lookup:
            self.assertEqual(self.client.post("/webhook?tenant=test-shop",
                                             json={"action": "get_music"}).status_code, 403)
            self.assertEqual(self.client.get("/music-audio/42?tenant=test-shop").status_code, 403)
            catalog.assert_not_called()
            lookup.assert_not_called()

    def test_catalog_never_exposes_file_names_and_supports_over_500_tracks(self):
        conn, cur = self.connection()
        cur.fetchall.return_value = [{"id": 1, "title": "Song", "artist": "Artist", "album": ""}]
        with patch.object(music, "get_conn", return_value=conn):
            self.assertEqual(music.catalog()[0]["title"], "Song")
            self.assertNotIn("file_name", cur.execute.call_args.args[0])
            cur.fetchall.return_value *= 501
            self.assertEqual(len(music.catalog()), 501)

    def test_file_paths_missing_disabled_and_tampered(self):
        conn, cur = self.connection()
        with patch.object(music, "get_conn", return_value=conn), \
                patch.object(music, "library_dir", return_value=self.root):
            cur.fetchone.return_value = None
            self.assertIsNone(music.audio_file(1))
            cur.fetchone.return_value = {"file_name": "../test.wav"}
            with self.assertRaises(ValueError):
                music.audio_file(1)
            cur.fetchone.return_value = {"file_name": "a" * 32 + ".wav"}
            with self.assertRaises(FileNotFoundError):
                music.audio_file(1)
            self.assertIn("enabled=TRUE", cur.execute.call_args.args[0])

    def test_missing_and_failed_audio_return_errors(self):
        with patch.object(api_server, "music_audio_file", return_value=None):
            self.assertEqual(self.client.get("/music-audio/42").status_code, 404)
        with patch.object(api_server, "music_audio_file", side_effect=FileNotFoundError):
            self.assertEqual(self.client.get("/music-audio/42").status_code, 503)

    def test_empty_catalog_and_database_failure(self):
        with patch.object(api_server, "music_catalog", return_value=[]):
            response = self.client.post("/webhook", json={"action": "get_music"})
            self.assertEqual(response.json, {"tracks": []})
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        with patch.object(api_server, "music_catalog", side_effect=api_server.pymysql.OperationalError("offline")):
            self.assertEqual(self.client.post("/webhook", json={"action": "get_music"}).status_code, 503)

    def test_hide_and_show_require_existing_track(self):
        conn, cur = self.connection()
        cur.fetchone.return_value = {"id": 42}
        with patch.object(music, "get_conn", return_value=conn):
            music.set_enabled(42, False)
        self.assertEqual(cur.execute.call_args.args[1], (False, 42))
        conn.commit.assert_called_once()
        conn.reset_mock()
        cur.fetchone.return_value = None
        with patch.object(music, "get_conn", return_value=conn), self.assertRaises(ValueError):
            music.set_enabled(999, True)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
