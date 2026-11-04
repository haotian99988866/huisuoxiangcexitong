import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telethon import types

import api_server
import movies
from movie_ingest import (DRAMA_SOURCE, SOURCES, MovieStore, clean_movie_caption,
                          process_job, resolve_album_caption, resolve_album_messages, scan_owned,
                          reconcile_recent, scan_source, store_path, video_metadata)


def video(ident=10, caption="原创短剧《测试短剧》完整版", document_id=100):
    return SimpleNamespace(
        id=ident, message=caption, noforwards=False, date=datetime.now(timezone.utc),
        document=SimpleNamespace(id=document_id, attributes=[
            types.DocumentAttributeVideo(duration=3983.4, w=1080, h=1920)], size=5000))


class ShortDramaCatalogTests(unittest.TestCase):
    def database(self):
        conn = MagicMock()
        return conn, conn.cursor.return_value.__enter__.return_value

    def test_paginated_catalog_is_scoped_to_owned_drama_channel(self):
        conn, cur = self.database()
        cur.fetchone.return_value = {"n": 25}
        cur.fetchall.return_value = [{
            "message_id": 88, "title": "《合集》完整版", "duration": 3983,
            "thumb_file_id": "thumb", "thumb_url": None,
        }]
        with patch.object(movies, "get_conn", return_value=conn):
            result = movies.short_drama_catalog("合集", page=2)
        self.assertEqual((result["total"], result["page"], result["has_more"]), (25, 2, False))
        self.assertEqual(result["items"][0]["url"], "https://t.me/htwssp/88")
        self.assertEqual(result["items"][0]["cover"], "short-drama-thumb/88")
        for call in cur.execute.call_args_list:
            self.assertIn("channel_id = %s", call.args[0])
            self.assertEqual(call.args[1][0], movies.DRAMA_CHANNEL_ID)
        self.assertEqual(cur.execute.call_args.args[1][-2:], [24, 24])

    def test_first_page_has_more_and_empty_catalog_is_explicit(self):
        for count, more in ((25, True), (0, False)):
            conn, cur = self.database()
            cur.fetchone.return_value = {"n": count}
            cur.fetchall.return_value = []
            with patch.object(movies, "get_conn", return_value=conn):
                result = movies.short_drama_catalog()
            self.assertEqual(result["has_more"], more)
            self.assertEqual(result["items"], [])

    def test_invalid_page_and_limit_fail_before_database(self):
        with patch.object(movies, "get_conn") as connect:
            for page in (0, -1, True, "2", 10001, None):
                with self.assertRaises(ValueError):
                    movies.short_drama_catalog(page=page)
            for limit in (0, True, 61):
                with self.assertRaises(ValueError):
                    movies.short_drama_catalog(limit=limit)
            connect.assert_not_called()

    def test_existing_movie_catalog_cannot_include_drama_records(self):
        conn, cur = self.database()
        cur.fetchall.side_effect = [[], []]
        cur.fetchone.return_value = {"n": 0}
        with patch.object(movies, "get_conn", return_value=conn):
            result = movies.movie_catalog()
        self.assertEqual(result["total"], 0)
        for call in cur.execute.call_args_list:
            self.assertIn("channel_id = %s", call.args[0])
            self.assertEqual(call.args[1][0], movies.MOVIE_CHANNEL_ID)

    def test_thumb_lookup_keeps_colliding_message_ids_isolated(self):
        conn, cur = self.database()
        cur.fetchone.return_value = {"thumb_file_id": "drama"}
        with patch.object(movies, "get_conn", return_value=conn):
            self.assertEqual(movies.thumb_file_id(10, movies.DRAMA_CHANNEL_ID), "drama")
        self.assertEqual(cur.execute.call_args.args[1], (movies.DRAMA_CHANNEL_ID, 10))

    def test_owned_drama_channel_post_is_cleaned_and_indexed_with_thumbnail(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=int(movies.DRAMA_CHANNEL_ID)), message_id=10,
            video=SimpleNamespace(duration=4000, thumbnail=SimpleNamespace(file_id="thumb")),
            caption="原创短剧《合集》完整版\nhttps://t.me/source", date=1700000000)
        with patch.object(movies, "save_movie", return_value=True) as save:
            self.assertTrue(movies.save_movie_message(message))
        self.assertEqual(save.call_args.args[:3],
                         (movies.DRAMA_CHANNEL_ID, 10, "原创短剧《合集》完整版 #短剧"))
        self.assertEqual(save.call_args.kwargs["thumb_file_id"], "thumb")

    def test_drama_source_uses_automatic_movie_policy_and_correct_channel_link(self):
        from operations_review import modules
        module = next(item for item in modules() if item.source == DRAMA_SOURCE.id)
        self.assertTrue(module.key.startswith("v"))
        self.assertIn("短剧", module.name)
        self.assertEqual(module.filename, f"movie-import-{DRAMA_SOURCE.id}.sqlite3")


class MovieStoreMigrationTests(unittest.TestCase):
    def test_existing_job_database_adds_album_result_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "movie-import.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE jobs (message INTEGER PRIMARY KEY, historical INTEGER NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'queued', result_id INTEGER, document_id INTEGER, error TEXT)"
            )
            connection.close()
            store = MovieStore(path, source=DRAMA_SOURCE)
            try:
                columns = {row["name"] for row in store.connection.execute("PRAGMA table_info(jobs)")}
                self.assertIn("result_ids", columns)
            finally:
                store.close()


class ShortDramaApiTests(unittest.TestCase):
    def setUp(self):
        self.client = api_server.app.test_client()

    def test_catalog_success_and_explicit_error_responses(self):
        with patch.object(api_server, "short_drama_catalog", return_value={"items": [], "total": 0}) as catalog:
            response = self.client.post("/webhook", json={"action": "get_short_dramas", "query": " 测试 ", "page": 2})
            self.assertEqual(response.status_code, 200)
            catalog.assert_called_once_with("测试", 2)
        with patch.object(api_server, "short_drama_catalog", side_effect=ValueError("页码不合法")):
            self.assertEqual(self.client.post("/webhook", json={"action": "get_short_dramas", "page": False}).status_code, 400)
        with patch.object(api_server, "short_drama_catalog", side_effect=api_server.pymysql.OperationalError("offline")):
            self.assertEqual(self.client.post("/webhook", json={"action": "get_short_dramas"}).status_code, 503)

    def test_tenant_entry_does_not_gain_access_to_new_module(self):
        with patch.object(api_server, "load_tenant", return_value={"venue_ids": [1]}), \
                patch.object(api_server, "short_drama_catalog") as catalog, \
                patch.object(api_server, "movie_thumb_file_id") as thumb:
            self.assertEqual(self.client.post("/webhook", headers={"X-Tenant": "shop"},
                                             json={"action": "get_short_dramas"}).status_code, 403)
            self.assertEqual(self.client.get("/short-drama-thumb/10?tenant=shop").status_code, 403)
            catalog.assert_not_called()
            thumb.assert_not_called()

    def test_thumb_route_passes_only_owned_drama_channel(self):
        with patch.object(api_server, "movie_thumb_file_id", return_value=None) as thumb:
            self.assertEqual(self.client.get("/short-drama-thumb/10").status_code, 404)
            thumb.assert_called_once_with(10, movies.DRAMA_CHANNEL_ID)


class ShortDramaIngestionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.store = MovieStore(store_path(self.directory, DRAMA_SOURCE), source=DRAMA_SOURCE)
        self.addCleanup(self.store.close)
        self.store.progress(20)
        self.source = SimpleNamespace(id=DRAMA_SOURCE.id, noforwards=False)
        self.target = SimpleNamespace(id=DRAMA_SOURCE.target_id)
        self.user = SimpleNamespace(
            get_entity=AsyncMock(return_value=self.source), get_messages=AsyncMock(return_value=[video()]),
            send_file=AsyncMock(return_value=SimpleNamespace(id=88)))
        self.bot = SimpleNamespace(get_messages=AsyncMock(return_value=[video(88)]),
                                   edit_message=AsyncMock())
    async def test_same_compilation_segments_are_copied_instead_of_deleted(self):
        self.user.get_messages.return_value = [video(document_id=101)]
        self.store.enqueue(10, True)
        await process_job(self.user, self.bot, self.source, self.target, self.store, self.store.pending())
        self.user.send_file.assert_awaited_once()
        self.assertEqual(self.store.pending()["status"], "forwarded")

    async def test_larger_new_file_is_copied_and_indexed_with_actual_bytes(self):
        self.store.enqueue(10, True)
        await process_job(self.user, self.bot, self.source, self.target, self.store, self.store.pending())
        with patch("movie_ingest.save_movie", return_value=True) as save:
            await process_job(self.user, self.bot, self.source, self.target, self.store, self.store.pending())
        self.user.send_file.assert_awaited_once()
        self.assertEqual(save.call_args.kwargs["file_size"], 5000)

    async def test_existing_owned_channel_inventory_backfills_actual_file_sizes(self):
        async def messages(*args, **kwargs):
            yield video(88)
        self.user.iter_messages = MagicMock(side_effect=messages)
        with patch("movie_ingest.save_movie", return_value=True) as save:
            self.assertTrue(await scan_owned(self.user, self.target, self.store))
        self.assertEqual(save.call_args.kwargs["file_size"], 5000)
        self.assertEqual(self.store.setting("drama_owned_cursor"), "88")
        self.assertEqual(self.store.setting("owned_cursor"), "0")

    def test_compilation_title_keeps_version_and_strips_source_promotion(self):
        raw = "AI短剧《测试短剧》第1-5集纯享合集版\n简介\nhttps://t.me/source"
        self.assertEqual(clean_movie_caption(raw, DRAMA_SOURCE),
                         "AI短剧《测试短剧》第1-5集纯享合集版 #短剧")
        self.assertEqual(video_metadata(video(caption=raw), DRAMA_SOURCE)["duration"], 3983)
        for caption in ("《测试》第3集", "《测试》预告", "注册即领送彩金 《测试》完整版",
                        "《测试》合集\n#广告", "没有片名的合集"):
            self.assertIsNone(video_metadata(video(caption=caption), DRAMA_SOURCE))

    def test_new_source_identity_and_season_compilations(self):
        self.assertEqual((DRAMA_SOURCE.id, DRAMA_SOURCE.username), (3068433963, "xmdm888"))
        for title in ("《测试短剧》 第1-2季", "《测试短剧》3~4季",
                      "《测试短剧》 第1~2季", "《测试短剧》第5季",
                      "《测试短剧》 第1-5季 已完结"):
            self.assertEqual(clean_movie_caption(title + "\n#测试短剧", DRAMA_SOURCE),
                             title + " #短剧")
            self.assertIsNotNone(video_metadata(video(caption=title), DRAMA_SOURCE))
        for title in ("《测试短剧》 01", "《测试短剧》第3集", "《测试短剧》预告",
                      "《测试短剧》第1季\n#广告"):
            self.assertIsNone(video_metadata(video(caption=title), DRAMA_SOURCE))

    async def test_forward_and_index_use_new_target_without_episode_screen(self):
        self.store.enqueue(10, True)
        await process_job(self.user, self.bot, self.source, self.target, self.store, self.store.pending())
        self.assertEqual(self.user.send_file.call_args.args[0].id, DRAMA_SOURCE.target_id)
        self.assertIn("#短剧", self.user.send_file.call_args.kwargs["caption"])
        with patch("movie_ingest.save_movie", return_value=True) as save:
            await process_job(self.user, self.bot, self.source, self.target, self.store, self.store.pending())
        self.assertEqual(save.call_args.args[:2], (movies.DRAMA_CHANNEL_ID, 88))
        self.assertIsNone(self.store.pending())
        self.user.send_file.assert_awaited_once()

    async def test_other_movie_channel_copy_cannot_replace_drama_target_copy(self):
        other = MovieStore(store_path(self.directory, SOURCES[0]), source=SOURCES[0])
        self.addCleanup(other.close)
        with other.connection:
            other.connection.execute("INSERT INTO owned VALUES (100,999)")
        self.store.enqueue(10, True)
        await process_job(self.user, self.bot, self.source, self.target, self.store,
                          self.store.pending(), other_stores=(other,))
        self.user.send_file.assert_awaited_once()
        self.assertEqual(self.store.pending()["result_id"], 88)

    async def test_protected_source_is_never_copied(self):
        self.source.noforwards = True
        self.store.enqueue(10, True)
        await process_job(self.user, self.bot, self.source, self.target, self.store, self.store.pending())
        self.user.send_file.assert_not_awaited()

    async def test_same_album_photo_caption_can_describe_captionless_video(self):
        item = video(caption="")
        item.grouped_id = 123
        photo = SimpleNamespace(id=9, grouped_id=123, message="《测试》完整版", document=None)
        unrelated = SimpleNamespace(id=8, grouped_id=124, message="《其他》完整版")
        self.user.get_messages.return_value = [unrelated, photo]
        resolved = await resolve_album_caption(self.user, self.source, item, DRAMA_SOURCE)
        self.assertEqual(resolved.message, photo.message)
        self.assertEqual(item.message, "")

    async def test_short_drama_album_is_copied_and_indexed_as_one_group(self):
        source_group = []
        target_group = []
        for index in range(4):
            source_item = video(ident=10 + index, caption="《测试短剧》第1-2季",
                                document_id=200 + index)
            source_item.grouped_id = 123
            source_item.document.size = 5000 - index * 100
            source_group.append(source_item)
            target_group.append(video(ident=90 + index, caption="《测试短剧》第1-2季",
                                      document_id=200 + index))

        async def source_messages(_source, ids):
            return source_group if len(ids) > 1 else [source_group[0]]

        self.user.get_messages.side_effect = source_messages
        self.user.send_file.return_value = target_group
        self.bot.get_messages.return_value = target_group
        self.store.enqueue(10, True)

        await process_job(self.user, self.bot, self.source, self.target, self.store, self.store.pending())
        self.user.send_file.assert_awaited_once()
        self.assertEqual(len(self.user.send_file.call_args.args[1]), 4)
        job = self.store.pending()
        self.assertEqual(job["status"], "forwarded")
        self.assertEqual(json.loads(job["result_ids"]), [90, 91, 92, 93])

        with patch("movie_ingest.save_movie", return_value=True) as save:
            await process_job(self.user, self.bot, self.source, self.target, self.store, job)
        self.assertEqual(save.call_count, 4)
        self.assertEqual([call.args[1] for call in save.call_args_list], [90, 91, 92, 93])
        self.assertEqual(self.store.pending(), None)

    async def test_source_scan_queues_only_the_first_video_in_an_album(self):
        source_group = []
        for index in range(4):
            item = video(ident=10 + index, caption="《测试短剧》第1-2季",
                         document_id=300 + index)
            item.grouped_id = 456
            source_group.append(item)
        by_id = {item.id: item for item in source_group}

        async def messages(_source, ids):
            return source_group if len(ids) > 1 else [by_id[ids[0]]]

        async def iterate(_source, **_options):
            for item in source_group:
                yield item

        self.user.get_messages.side_effect = messages
        self.user.iter_messages = iterate
        with self.store.connection:
            self.store.connection.execute("UPDATE progress SET live=0 WHERE id=1")
        await scan_source(self.user, self.source, self.store, historical=False)
        rows = self.store.connection.execute("SELECT message FROM jobs ORDER BY message").fetchall()
        self.assertEqual([row["message"] for row in rows], [10])

    def test_old_dedupe_exclusion_is_requeued_for_album_recovery(self):
        self.store.enqueue(10, True)
        self.store.update(
            self.store.pending(), "excluded",
            "Same short-drama compilation already has an equal or larger file",
            result_id=77, document_id=100,
        )
        self.store.enqueue(10, True)
        row = self.store.pending()
        self.assertEqual((row["status"], row["result_id"], row["document_id"]), ("queued", None, None))

    async def test_recent_partial_album_deletion_requeues_the_complete_group(self):
        source_group = []
        for index in range(2):
            item = video(ident=10 + index, caption="《测试短剧》第1-2季",
                         document_id=400 + index)
            item.grouped_id = 789
            source_group.append(item)

        async def messages(_source, ids):
            return source_group if len(ids) > 1 else [source_group[0]]

        async def iterate(_source, **_options):
            for item in reversed(source_group):
                yield item

        self.user.get_messages.side_effect = messages
        self.user.iter_messages = iterate
        self.store.enqueue(10, True)
        self.store.update(self.store.pending(), "sent", result_id=77, document_id=400)
        self.store.enqueue(11, True)
        self.store.update(
            self.store.connection.execute("SELECT * FROM jobs WHERE message=11").fetchone(),
            "excluded", "Smaller duplicate removed from owned short-drama channel; largest file retained",
        )
        await reconcile_recent(self.user, self.source, self.store)
        row = self.store.pending()
        self.assertEqual((row["message"], row["status"]), (10, "queued"))


if __name__ == "__main__":
    unittest.main()
