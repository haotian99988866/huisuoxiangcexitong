import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import api_server
import music_social as social
from tests.test_admin_console import signed, TOKEN
from tests.test_tenants import config
from db import get_conn


class MusicSocialTests(unittest.TestCase):
    def setUp(self):
        self.client = api_server.app.test_client()
        self.env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN})
        self.env.start()
        self.addCleanup(self.env.stop)

    def connection(self, *rows):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = rows
        cur.fetchall.return_value = []
        return conn, cur

    def profile(self, **fields):
        return {"document": json.dumps({"favorites": [], "recent": [], "resume": None,
                                       "mode": "sequential", **fields})}

    def test_personal_actions_verify_signed_identity_and_reject_tenant_sessions(self):
        with patch.object(social, "account", return_value={"profile": {}, "playlists": []}) as account:
            for action in ("music_profile", "music_update", "music_playlist_create",
                           "music_playlist_save", "music_playlist_delete"):
                for signature, status in (("", 401), ("bad", 401),
                                          (signed(start_param="tenant_test-shop"), 403)):
                    response = self.client.post("/webhook", json={"action": action, "init_data": signature})
                    self.assertEqual(response.status_code, status)
                account.assert_not_called()
            response = self.client.post("/webhook", json={"action": "music_profile", "init_data": signed(987)})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(account.call_args.args[:2], (987, "music_profile"))
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_all_music_actions_blocked_for_tenant_header_before_lookup(self):
        with patch.object(api_server, "load_tenant", return_value=config()), \
                patch.object(social, "account") as account, patch.object(social, "shared_playlist") as shared:
            for action in ("music_profile", "music_update", "music_playlist_create",
                           "music_playlist_save", "music_playlist_delete", "music_shared"):
                response = self.client.post("/webhook?tenant=test-shop", json={"action": action, "init_data": signed()})
                self.assertEqual(response.status_code, 403)
            account.assert_not_called()
            shared.assert_not_called()

    def test_shared_playlist_needs_only_token_and_never_returns_owner(self):
        conn, cur = self.connection({"id": 7, "name": "Music", "tracks": "[1, 2]",
                                    "share_token": "a" * 32, "revision": 3, "user_id": 99})
        with patch.object(social, "get_conn", return_value=conn):
            response = self.client.post("/webhook", json={"action": "music_shared", "token": "a" * 32})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["playlist"]["tracks"], [1, 2])
        self.assertNotIn("user_id", response.json["playlist"])
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertIn("WHERE share_token=%s", cur.execute.call_args.args[0])

    def test_bad_or_deleted_share_is_explicit_error(self):
        with patch.object(social, "get_conn") as db:
            for token in (None, 1, "a", "../" + "a" * 32):
                with self.assertRaises(ValueError):
                    social.shared_playlist(token)
            db.assert_not_called()
        conn, _ = self.connection(None)
        with patch.object(social, "get_conn", return_value=conn), self.assertRaisesRegex(ValueError, "已删除"):
            social.shared_playlist("a" * 32)

    def test_favorite_and_recent_dedupe_and_recent_retention(self):
        for operation in ("favorite", "recent"):
            conn, cur = self.connection(self.profile(favorites=[2, 1], recent=list(range(1, 51))), {"id": 2})
            with patch.object(social, "get_conn", return_value=conn):
                result = social.account(123, "music_update", {"operation": operation, "track_id": 2, "enabled": True})
            values = result["profile"]["favorites" if operation == "favorite" else "recent"]
            self.assertEqual(values[0], 2)
            self.assertEqual(values.count(2), 1)
            self.assertLessEqual(len(values), 50)
            conn.commit.assert_called_once()
            self.assertIn("FOR UPDATE", cur.execute.call_args_list[1].args[0])

    def test_invalid_mode_resume_and_ids_roll_back(self):
        cases = [{"operation": "mode", "mode": "invalid"},
                 {"operation": "resume", "resume": {"id": True, "position": 0}},
                 {"operation": "resume", "resume": {"id": 1, "position": float("nan")}},
                 {"operation": "resume", "resume": {"id": 1, "position": -1}},
                 {"operation": "resume", "resume": []},
                 {"operation": "favorite", "track_id": "1"}]
        for fields in cases:
            conn, _ = self.connection(self.profile(), {"id": 1})
            with patch.object(social, "get_conn", return_value=conn), self.assertRaises(ValueError):
                social.account(123, "music_update", fields)
            conn.rollback.assert_called_once()
            conn.commit.assert_not_called()

    def test_progress_and_modes_roundtrip(self):
        for fields in ({"operation": "resume", "resume": {"id": 1, "position": 12.5}},
                       {"operation": "resume", "resume": None},
                       {"operation": "mode", "mode": "shuffle"},
                       {"operation": "mode", "mode": "single"}):
            conn, _ = self.connection(self.profile(), {"id": 1})
            with patch.object(social, "get_conn", return_value=conn):
                result = social.account(123, "music_update", fields)
            key = "resume" if fields["operation"] == "resume" else "mode"
            self.assertEqual(result["profile"][key], fields[key])

    def test_nonowner_and_stale_revision_never_mutate(self):
        for row, error in ((None, ValueError), ({"id": 7, "revision": 3}, social.PlaylistConflict)):
            conn, cur = self.connection(self.profile(), row)
            with patch.object(social, "get_conn", return_value=conn), self.assertRaises(error):
                social.account(123, "music_playlist_delete", {"playlist_id": 7, "revision": 2})
            self.assertIn("AND user_id=%s FOR UPDATE", cur.execute.call_args.args[0])
            conn.rollback.assert_called_once()
            conn.commit.assert_not_called()

    def test_playlist_limits_and_enabled_song_validation(self):
        cur = MagicMock()
        for values in ([1] * 201, [True], [-1], "1"):
            with self.assertRaises(ValueError):
                social.track_ids(cur, values)
        cur.fetchall.return_value = [{"id": 1}]
        self.assertEqual(social.track_ids(cur, [1, 1]), [1])
        with self.assertRaisesRegex(ValueError, "下架"):
            social.track_ids(cur, [1, 2])
        conn, _ = self.connection(self.profile(), {"count": 50})
        with patch.object(social, "get_conn", return_value=conn), self.assertRaisesRegex(ValueError, "50"):
            social.account(123, "music_playlist_create", {"name": "Test"})
        conn.rollback.assert_called_once()

    def test_exact_collection_limits(self):
        conn, _ = self.connection(self.profile(favorites=list(range(1, 501))), {"id": 501})
        with patch.object(social, "get_conn", return_value=conn), self.assertRaisesRegex(ValueError, "500"):
            social.account(123, "music_update", {"operation": "favorite", "track_id": 501, "enabled": True})
        conn.rollback.assert_called_once()
        conn, _ = self.connection(self.profile(recent=list(range(1, 51))), {"id": 51})
        with patch.object(social, "get_conn", return_value=conn):
            result = social.account(123, "music_update", {"operation": "recent", "track_id": 51})
        self.assertEqual(result["profile"]["recent"], [51] + list(range(1, 50)))
        cur = MagicMock()
        cur.fetchall.return_value = [{"id": value} for value in range(1, 201)]
        self.assertEqual(len(social.track_ids(cur, list(range(1, 201)))), 200)
        self.assertEqual(len(social.playlist_name("歌" * 80)), 80)
        with self.assertRaises(ValueError):
            social.playlist_name("歌" * 81)

    def test_conflict_and_database_errors_have_explicit_api_status(self):
        for error, status in ((social.PlaylistConflict("Changed"), 409),
                              (api_server.pymysql.OperationalError("offline"), 503)):
            with patch.object(social, "account", side_effect=error):
                response = self.client.post("/webhook", json={"action": "music_profile", "init_data": signed()})
                self.assertEqual(response.status_code, status)


@unittest.skipUnless(os.getenv("MUSIC_INTEGRATION") == "1", "Opt-in isolated MySQL test")
class MusicSocialIntegrationTests(unittest.TestCase):
    def test_real_mysql_isolation_sharing_concurrency_and_progress(self):
        name = "music_test_" + uuid.uuid4().hex
        admin = get_conn()
        try:
            with admin.cursor() as cur:
                cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
            def connect():
                conn = get_conn()
                conn.select_db(name)
                return conn
            with patch.object(social, "get_conn", side_effect=connect):
                social.ensure_tables()
                social.ensure_tables()
                conn = connect()
                try:
                    with conn.cursor() as cur:
                        cur.execute("""CREATE TABLE music_tracks (
                            id BIGINT PRIMARY KEY, enabled BOOLEAN NOT NULL, merged_into BIGINT NULL) ENGINE=InnoDB""")
                        cur.execute("INSERT INTO music_tracks(id,enabled) VALUES(1,TRUE),(2,TRUE),(3,FALSE)")
                    conn.commit()
                finally:
                    conn.close()
                created = social.account(123, "music_playlist_create", {"name": "我的歌单", "tracks": [1, 2, 1]})
                playlist = created["playlists"][0]
                self.assertEqual(playlist["tracks"], [1, 2])
                token = playlist["share_token"]
                self.assertEqual(social.shared_playlist(token)["playlist"]["name"], "我的歌单")
                self.assertEqual(social.account(456, "music_profile", {})["playlists"], [])
                with self.assertRaisesRegex(ValueError, "不属于"):
                    social.account(456, "music_playlist_save", {"playlist_id": playlist["id"], "revision": 1,
                                                              "name": "Stolen", "tracks": []})
                with self.assertRaisesRegex(ValueError, "下架"):
                    social.account(123, "music_playlist_save", {"playlist_id": playlist["id"], "revision": 1,
                                                              "name": "Bad", "tracks": [3]})
                def rename(value):
                    try:
                        social.account(123, "music_playlist_save", {"playlist_id": playlist["id"], "revision": revision,
                                                                  "name": value, "tracks": [2, 1]})
                        return "saved"
                    except social.PlaylistConflict:
                        return "conflict"
                for revision in range(1, 11):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        self.assertCountEqual(list(pool.map(rename, ["A", "B"])), ["saved", "conflict"])
                updated = social.shared_playlist(token)["playlist"]
                self.assertEqual(updated["revision"], 11)
                self.assertEqual(updated["tracks"], [2, 1])
                social.account(123, "music_update", {"operation": "favorite", "track_id": 1, "enabled": True})
                social.account(123, "music_update", {"operation": "recent", "track_id": 2})
                social.account(123, "music_update", {"operation": "mode", "mode": "shuffle"})
                saved = social.account(123, "music_update", {"operation": "resume", "resume": {"id": 2, "position": 30.5}})
                self.assertEqual(saved["profile"], {"favorites": [1], "recent": [2], "mode": "shuffle",
                                                    "resume": {"id": 2, "position": 30.5}})
                self.assertEqual(social.account(456, "music_profile", {})["profile"]["favorites"], [])
                social.account(123, "music_playlist_delete", {"playlist_id": playlist["id"], "revision": 11})
                with self.assertRaisesRegex(ValueError, "已删除"):
                    social.shared_playlist(token)
        finally:
            with admin.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
            admin.close()


if __name__ == "__main__":
    unittest.main()
