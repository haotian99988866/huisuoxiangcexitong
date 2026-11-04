import tempfile
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from movie_ingest import DRAMA_SOURCE, MovieStore, store_path
from short_drama_dedupe import cleanup, compilation_key, register


class IdentityTests(unittest.TestCase):
    def test_title_and_entire_version_qualifier_are_preserved(self):
        self.assertEqual(compilation_key("AI短剧《烬九州》 第27-30季 没找到第26季 #短剧"),
                         compilation_key("《烬九州》第27-30季 没找到第26季"))
        for title in ("《烬九州》第26季", "《烬九州》第27-30集", "《烬九州》完整版",
                      "《其他》第27-30季 没找到第26季"):
            self.assertNotEqual(compilation_key("《烬九州》第27-30季 没找到第26季"), compilation_key(title))
        with self.assertRaises(ValueError):
            compilation_key("没有剧名")

    def test_size_is_required_and_deleted_tombstone_is_never_reset(self):
        cursor = MagicMock()
        for size in (None, 0, -1, True):
            with self.assertRaises(ValueError):
                register(cursor, 1, "《测试》第1季", size)
        cursor.execute.assert_not_called()
        cursor.fetchone.return_value = {"deleted": 1}
        self.assertFalse(register(cursor, 1, "《测试》第1季", 123456))
        self.assertNotIn("deleted=", cursor.execute.call_args_list[0].args[0])


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = MovieStore(store_path(Path(folder.name), DRAMA_SOURCE), source=DRAMA_SOURCE)
        self.addCleanup(self.store.close)
        self.store.enqueue(10, True)
        self.job = self.store.pending()
        self.store.update(self.job, "sent", result_id=5, document_id=105)
        with self.store.connection:
            self.store.connection.execute("INSERT INTO owned VALUES (105,5)")
        self.title = "《测试》第1-2季"
        self.loser = SimpleNamespace(id=5, message=self.title, document=SimpleNamespace(size=100))
        self.winner = SimpleNamespace(id=6, message=self.title, document=SimpleNamespace(size=200))
        self.user = SimpleNamespace(get_messages=AsyncMock(), delete_messages=AsyncMock())
        self.target = SimpleNamespace(id=DRAMA_SOURCE.target_id)
        for name, result in (
            ("duplicates", [{"message_id": 5, "compilation_key": compilation_key(self.title), "file_size": 100,
                             "winner_id": 6, "winner_size": 200}]),
            ("largest", {"message_id": 6, "file_size": 200}), ("mark_deleted", None),
        ):
            patcher = patch("short_drama_dedupe." + name, return_value=result)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        patcher = patch("short_drama_dedupe.asyncio.sleep", new_callable=AsyncMock)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_deletion_verifies_winner_and_keeps_source_progress_and_audit(self):
        self.user.get_messages.side_effect = [[self.loser, self.winner], [None]]
        self.assertEqual(await cleanup(self.user, self.target, self.store), 1)
        self.user.delete_messages.assert_awaited_once()
        self.assertEqual(self.user.delete_messages.call_args.args[1], [5])
        self.mark_deleted.assert_called_once_with([5])
        row = self.store.connection.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual((row["status"], row["result_id"], row["document_id"]), ("excluded", 5, 105))
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM owned").fetchone()[0], 0)

    async def test_missing_largest_or_changed_identity_stops_without_deletion(self):
        self.user.get_messages.side_effect = [[self.loser, None]]
        with self.assertRaisesRegex(ValueError, "verified"):
            await cleanup(self.user, self.target, self.store)
        self.user.delete_messages.assert_not_awaited()
        self.mark_deleted.assert_not_called()

    async def test_unconfirmed_deletion_remains_retryable(self):
        self.user.get_messages.side_effect = [[self.loser, self.winner], [self.loser]]
        with self.assertRaisesRegex(RuntimeError, "confirmed"):
            await cleanup(self.user, self.target, self.store)
        self.mark_deleted.assert_not_called()

    async def test_retry_after_remote_deletion_finishes_local_exclusion(self):
        self.user.get_messages.return_value = [None, self.winner]
        await cleanup(self.user, self.target, self.store)
        self.user.delete_messages.assert_not_awaited()
        self.mark_deleted.assert_called_once_with([5])

    async def test_wrong_target_is_never_touched(self):
        with self.assertRaises(ValueError):
            await cleanup(self.user, SimpleNamespace(id=123), self.store)
        self.user.get_messages.assert_not_awaited()

    async def test_equal_size_keeps_earlier_message_deterministically(self):
        self.duplicates.return_value[0].update(winner_id=4, winner_size=100)
        self.winner.id, self.winner.document.size = 4, 100
        self.user.get_messages.side_effect = [[self.winner, self.loser], [None]]
        await cleanup(self.user, self.target, self.store)
        self.assertEqual(self.user.delete_messages.call_args.args[1], [5])

    async def test_changed_loser_or_incomplete_read_never_deletes(self):
        self.user.get_messages.return_value = [self.winner]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            await cleanup(self.user, self.target, self.store)
        self.user.delete_messages.assert_not_awaited()


@unittest.skipUnless(os.environ.get("RUN_DRAMA_MYSQL_TESTS") == "1", "Requires isolated MySQL integration database")
class MySqlSelectionTests(unittest.TestCase):
    def test_real_catalog_max_bytes_ties_replacement_and_tombstones(self):
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
                for ident, size in ((1, 200), (2, 5_000_000_000), (3, 5_000_000_000)):
                    self.assertTrue(movies.save_movie(movies.DRAMA_CHANNEL_ID, ident, title, file_size=size))
                movies.save_movie(movies.DRAMA_CHANNEL_ID, 4, "《测试合集》第3-4季", file_size=20)
                movies.save_movie(movies.MOVIE_CHANNEL_ID, 2, title)
                result = movies.short_drama_catalog(limit=1)
                self.assertEqual((result["total"], result["has_more"], result["items"][0]["id"]), (2, True, 4))
                self.assertEqual(movies.short_drama_catalog(page=2, limit=1)["items"][0]["id"], 2)
                self.assertEqual(dedupe.largest(title), {"message_id": 2, "file_size": 5_000_000_000})
                self.assertEqual([row["message_id"] for row in dedupe.duplicates()], [1, 3])
                dedupe.mark_deleted([1, 3])
                movies.save_movie(movies.DRAMA_CHANNEL_ID, 1, title, file_size=200)
                self.assertFalse(dedupe.duplicates())
                movies.save_movie(movies.DRAMA_CHANNEL_ID, 5, title, file_size=6_000_000_000)
                self.assertEqual(dedupe.largest(title)["message_id"], 5)
                self.assertEqual([row["message_id"] for row in dedupe.duplicates()], [2])
                result = movies.short_drama_catalog(query="测试合集")
                self.assertEqual([item["id"] for item in result["items"]], [5, 4])
                connection = connect()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT COUNT(*) AS n FROM movie_videos WHERE channel_id=%s",
                                       (movies.MOVIE_CHANNEL_ID,))
                        self.assertEqual(cursor.fetchone()["n"], 1)
                        cursor.execute("SELECT deleted FROM short_drama_versions WHERE message_id=1")
                        self.assertEqual(cursor.fetchone()["deleted"], 1)
                finally:
                    connection.close()
        finally:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            admin.close()


if __name__ == "__main__":
    unittest.main()
