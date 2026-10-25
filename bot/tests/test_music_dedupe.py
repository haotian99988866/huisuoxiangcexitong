from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import uuid
import wave

from db import get_conn
import music
import music_dedupe as dedupe
import music_social


class MusicDedupeTests(unittest.TestCase):
    def test_normalization_respects_song_and_artist_but_ignores_formatting(self):
        self.assertEqual(dedupe.song_key(" Ｈｅｌｌｏ　 World ", "ＡＤＥＬＥ"),
                         dedupe.song_key("hello world", "adele"))
        self.assertEqual(dedupe.song_key("雨天", "<unknown>"), dedupe.song_key("雨天", "未知歌手"))
        self.assertNotEqual(dedupe.song_key("雨天", "甲"), dedupe.song_key("雨天", "乙"))
        self.assertNotEqual(dedupe.song_key("雨天", "甲"), dedupe.song_key("雨天（现场版）", "甲"))
        self.assertNotEqual(dedupe.song_key("ab", "c"), dedupe.song_key("a", "bc"))

    def test_duplicate_returns_existing_id_without_downloading_or_enabling(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"id": 42}, None]
        download = MagicMock()
        self.assertEqual(dedupe.save_track(conn, "Song", "Artist", "", "a.mp3", download,
                                          channel_id="1", message_id=2), (42, False))
        download.assert_not_called()
        self.assertFalse(any("INSERT" in call.args[0] or "UPDATE" in call.args[0]
                             for call in cur.execute.call_args_list))

    def test_lock_timeout_is_explicit_and_does_not_enter(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = {"acquired": 0}
        with self.assertRaisesRegex(ValueError, "繁忙"):
            with dedupe.import_lock(conn):
                self.fail("lock must not be entered")


@unittest.skipUnless(os.getenv("MUSIC_INTEGRATION") == "1", "Opt-in isolated MySQL test")
class MusicDedupeIntegrationTests(unittest.TestCase):
    def test_existing_merges_imports_edits_hidden_and_concurrent_duplicates(self):
        database = "music_dedupe_test_" + uuid.uuid4().hex
        admin = get_conn()
        try:
            with admin.cursor() as cur:
                cur.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
            def connect():
                conn = get_conn()
                conn.select_db(database)
                return conn
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(music, "get_conn", side_effect=connect), \
                    patch.object(music_social, "get_conn", side_effect=connect), \
                    patch.object(music, "library_dir", return_value=Path(directory)):
                source = Path(directory) / "sample.wav"
                with wave.open(str(source), "wb") as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(8000)
                    stream.writeframes(b"\x00\x00" * 8000)
                conn = connect()
                try:
                    with conn.cursor() as cur:
                        cur.execute("""CREATE TABLE music_tracks (
                            id BIGINT AUTO_INCREMENT PRIMARY KEY, title VARCHAR(120) NOT NULL,
                            artist VARCHAR(120) NOT NULL, album VARCHAR(120) NOT NULL DEFAULT '',
                            file_name VARCHAR(40) NOT NULL UNIQUE, enabled BOOLEAN NOT NULL DEFAULT TRUE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
                        cur.execute("INSERT INTO music_tracks(title,artist,file_name) VALUES"
                                    "(' Song ','Artist',%s),('ＳＯＮＧ','ARTIST',%s),('Other','Artist',%s)",
                                    ("a" * 32 + ".wav", "b" * 32 + ".wav", "c" * 32 + ".wav"))
                    conn.commit()
                finally:
                    conn.close()
                (Path(directory) / ("a" * 32 + ".wav")).write_bytes(source.read_bytes())
                music_social.ensure_tables()
                conn = connect()
                try:
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO music_profiles VALUES(123,%s)", (json.dumps({
                            "favorites": [2, 1, 3], "recent": [2, 3, 1], "mode": "shuffle",
                            "resume": {"id": 2, "position": 30},
                        }),))
                        cur.execute("INSERT INTO music_playlists(user_id,name,tracks,share_token) VALUES(123,'Test',%s,%s)",
                                    ("[2,1,3]", "a" * 32))
                    conn.commit()
                finally:
                    conn.close()
                music.ensure_tables()
                music.ensure_tables()
                self.assertEqual([row["id"] for row in music.catalog()], [3, 1])
                profile = music_social.account(123, "music_profile", {})["profile"]
                self.assertEqual(profile["favorites"], [1, 3])
                self.assertEqual(profile["recent"], [1, 3])
                self.assertEqual(profile["resume"], {"id": 1, "position": 0})
                shared = music_social.shared_playlist("a" * 32)["playlist"]
                self.assertEqual(shared["tracks"], [1, 3])
                self.assertEqual(shared["revision"], 2)
                self.assertEqual(music.audio_file(2)[0].name, "a" * 32 + ".wav")
                stale = music_social.account(123, "music_update", {"operation": "resume", "resume": {"id": 2, "position": 90}})
                self.assertEqual(stale["profile"]["resume"], {"id": 1, "position": 0})
                stale = music_social.account(123, "music_update", {"operation": "favorite", "track_id": 2, "enabled": False})
                self.assertNotIn(1, stale["profile"]["favorites"])
                saved = music_social.account(123, "music_playlist_save", {
                    "playlist_id": shared["id"], "revision": 2, "name": "Test", "tracks": [2, 1, 3],
                })
                self.assertEqual(saved["playlists"][0]["tracks"], [1, 3])
                self.assertEqual(music.add_track(source, "song", "artist", rights_confirmed=True), 1)
                self.assertEqual(len(list(Path(directory).iterdir())), 2)
                music.set_enabled(1, False)
                self.assertEqual(music.add_track(source, "SONG", "ARTIST", rights_confirmed=True), 1)
                self.assertEqual([row["id"] for row in music.catalog()], [3])
                self.assertIsNone(music.audio_file(2))
                music.set_enabled(2, True)
                self.assertEqual(len(music.catalog()), 2)
                def upload(message_id, title="New Song", artist="Singer", file_id=None):
                    media = SimpleNamespace(file_id=file_id or f"file-{message_id}", file_size=source.stat().st_size,
                                            file_name="sample.wav", mime_type="audio/wav", title=title, performer=artist)
                    return SimpleNamespace(chat=SimpleNamespace(id=int(music.MUSIC_CHANNEL_ID)),
                                           message_id=message_id, audio=media, document=None)
                def download(_file, name, _size):
                    target = Path(directory) / name
                    target.write_bytes(source.read_bytes())
                    return target
                with patch.object(music, "download_channel_audio", side_effect=download) as downloader:
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        self.assertTrue(all(pool.map(music.save_channel_message, [upload(10), upload(11), upload(12)])))
                    self.assertEqual(downloader.call_count, 1)
                    self.assertEqual(len(music.catalog()), 3)
                    # Different artists and different titles are not merged, even for the same file.
                    music.save_channel_message(upload(13, artist="Other Singer", file_id="file-10"))
                    self.assertEqual(len(music.catalog()), 4)
                    music.save_channel_message(upload(14, title="Different", file_id="file-10"))
                    self.assertEqual(len(music.catalog()), 5)
                    first = next(row for row in music.catalog() if row["title"] == "New Song" and row["artist"] == "Singer")
                    music_social.account(123, "music_update", {"operation": "favorite", "track_id": first["id"], "enabled": True})
                    # Editing an existing source into a duplicate merges its users' references.
                    primary_message = None
                    conn = connect()
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SELECT message_id FROM music_tracks WHERE id=%s", (first["id"],))
                            primary_message = cur.fetchone()["message_id"]
                    finally:
                        conn.close()
                    music.save_channel_message(upload(primary_message, title="song", artist="artist"))
                    self.assertEqual(len(music.catalog()), 4)
                    self.assertNotIn(first["id"], music_social.account(123, "music_profile", {})["profile"]["favorites"])
                    self.assertEqual(music.audio_file(first["id"])[0].name, "a" * 32 + ".wav")
                    # An edited merged source may become a distinct song again.
                    music.save_channel_message(upload(primary_message, title="Unmerged", artist="Singer"))
                    self.assertEqual(len(music.catalog()), 5)
        finally:
            with admin.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
            admin.close()


if __name__ == "__main__":
    unittest.main()
