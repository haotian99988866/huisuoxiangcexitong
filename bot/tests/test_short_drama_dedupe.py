import sqlite3
import os
import uuid
import unittest
from unittest.mock import MagicMock, patch

from short_drama_dedupe import VISIBLE_SQL, compilation_key, register


class IdentityTests(unittest.TestCase):
    def test_title_and_entire_version_qualifier_are_preserved(self):
        self.assertEqual(compilation_key("AI短剧《烬九州》 第27-30季 没找到第26季 #短剧"),
                         compilation_key("《烬九州》第27-30季 没找到第26季"))
        for title in ("《烬九州》第26季", "《烬九州》第27-30集", "《烬九州》完整版",
                      "《其他》第27-30季 没找到第26季"):
            self.assertNotEqual(compilation_key("《烬九州》第27-30季 没找到第26季"), compilation_key(title))
        with self.assertRaises(ValueError):
            compilation_key("没有剧名")

    def test_all_album_segments_are_registered_and_tombstones_are_not_reset(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"deleted": 0}
        for ident, size in ((1, 200), (2, 100), (3, 50)):
            self.assertTrue(register(cursor, ident, "《测试》第1季", size))
        self.assertEqual(cursor.execute.call_count, 6)
        for size in (None, 0, -1, True):
            with self.assertRaises(ValueError):
                register(cursor, 4, "《测试》第1季", size)
        cursor.fetchone.return_value = {"deleted": 1}
        self.assertFalse(register(cursor, 5, "《测试》第1季", 123456))
        self.assertNotIn("deleted=", cursor.execute.call_args_list[-2].args[0])

    def test_visibility_uses_one_latest_message_per_exact_season_range(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript("""
            CREATE TABLE movie_videos (message_id INTEGER);
            CREATE TABLE short_drama_versions (
                message_id INTEGER, compilation_key TEXT, deleted INTEGER, file_size INTEGER
            );
        """)
        for ident, title in (
            (10, "《测试》第1-4季"), (11, "《测试》第1-4季"),
            (12, "《测试》第1-4季"), (20, "《测试》第1-5季"),
        ):
            connection.execute("INSERT INTO movie_videos VALUES (?)", (ident,))
            connection.execute(
                "INSERT INTO short_drama_versions VALUES (?,?,0,100)",
                (ident, compilation_key(title)),
            )
        visible = connection.execute(
            f"SELECT message_id FROM movie_videos WHERE {VISIBLE_SQL} ORDER BY message_id"
        ).fetchall()
        self.assertEqual([row[0] for row in visible], [12, 20])
        connection.close()


@unittest.skipUnless(os.environ.get("RUN_DRAMA_MYSQL_TESTS") == "1", "Requires isolated MySQL integration database")
class MySqlSelectionTests(unittest.TestCase):
    def test_catalog_keeps_all_segments_but_shows_one_per_season_range(self):
        import db
        import movies
        import short_drama_dedupe as dedupe
        schema = "drama_dedupe_test_" + uuid.uuid4().hex
        admin = db.get_conn()
        try:
            with admin.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")

            def connect():
                connection = db.get_conn()
                connection.select_db(schema)
                return connection

            with patch.object(movies, "get_conn", side_effect=connect), \
                    patch.object(dedupe, "get_conn", side_effect=connect):
                movies.ensure_tables()
                title = "《测试合集》第1-2季"
                for ident, size in ((1, 200), (2, 50), (3, 100)):
                    self.assertTrue(movies.save_movie(movies.DRAMA_CHANNEL_ID, ident, title, file_size=size))
                movies.save_movie(movies.DRAMA_CHANNEL_ID, 4, "《测试合集》第3-4季", file_size=20)
                movies.save_movie(movies.MOVIE_CHANNEL_ID, 2, title)
                result = movies.short_drama_catalog(limit=1)
                self.assertEqual((result["total"], result["has_more"], result["items"][0]["id"]), (2, True, 4))
                self.assertEqual(movies.short_drama_catalog(page=2, limit=1)["items"][0]["id"], 3)
                movies.save_movie(movies.DRAMA_CHANNEL_ID, 5, title, file_size=60)
                result = movies.short_drama_catalog(query="测试合集")
                self.assertEqual([item["id"] for item in result["items"]], [5, 4])
                connection = connect()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT COUNT(*) AS n FROM movie_videos WHERE channel_id=%s",
                                       (movies.DRAMA_CHANNEL_ID,))
                        self.assertEqual(cursor.fetchone()["n"], 5)
                        cursor.execute("SELECT COUNT(*) AS n FROM short_drama_versions WHERE deleted=0")
                        self.assertEqual(cursor.fetchone()["n"], 4)
                finally:
                    connection.close()
        finally:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            admin.close()


if __name__ == "__main__":
    unittest.main()
