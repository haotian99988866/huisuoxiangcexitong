import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telethon import errors, types

from movie_ingest import (MovieStore, SOURCE_ID, TARGET_ID, movie_worker,
                          clean_movie_caption, process_job, scan_source, scan_owned, video_metadata)


def message(ident=10, caption="《测试剧》第1集 #电视剧", document_id=100):
    return SimpleNamespace(
        id=ident, message=caption, noforwards=False, date=datetime.now(timezone.utc),
        document=SimpleNamespace(id=document_id, attributes=[
            types.DocumentAttributeVideo(duration=123.5, w=1920, h=1080)]))


class MovieIngestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "movie.sqlite3"
        self.store = MovieStore(self.path)
        self.addCleanup(self.store.close)
        self.store.set("start_message", 1)
        self.store.progress(20)
        self.source = SimpleNamespace(id=1, noforwards=False)
        self.target = SimpleNamespace(id=2)
        self.user = SimpleNamespace(get_entity=AsyncMock(return_value=self.source),
                                    get_messages=AsyncMock(return_value=[message()]),
                                    send_file=AsyncMock(return_value=SimpleNamespace(id=101)))
        self.bot = SimpleNamespace(get_messages=AsyncMock(return_value=[message(101)]),
                                   edit_message=AsyncMock())

    def test_start_is_inclusive_and_excludes_only_unsent_earlier_jobs(self):
        self.store.enqueue(100, True)
        self.store.enqueue(101, True)
        self.store.update(self.row(101), "sent", result_id=88)
        self.store.set_start(2377)
        self.store.enqueue(2376, True)
        self.store.enqueue(2377, True)
        self.assertEqual(self.row(100)["status"], "excluded")
        self.assertEqual(self.row(101)["status"], "sent")
        self.assertIsNone(self.row(2376))
        self.assertEqual(self.row(2377)["status"], "queued")
        self.assertEqual(self.store.progress()["history"], 2376)

    async def test_scan_starts_at_requested_message_and_skips_promotional_video(self):
        self.store.set_start(2377)
        with self.store.connection:
            self.store.connection.execute("UPDATE progress SET boundary=2400 WHERE id=1")
        async def iterate(source, **options):
            self.assertEqual(options["min_id"], 2376)
            self.assertEqual(options["max_id"], 2401)
            yield message(2377)
            yield message(2378, "宣传推广 #广告")
            yield SimpleNamespace(id=2379, message="普通文字", document=None)
        self.user.iter_messages = iterate
        await scan_source(self.user, self.source, self.store, historical=True)
        self.assertEqual(self.store.pending()["message"], 2377)
        self.assertIsNone(self.row(2378))
        self.assertIsNone(self.row(2379))
        self.assertEqual(self.store.progress()["history"], 2379)

    async def test_buttons_match_publisher_and_old_forwarded_posts_unchanged(self):
        from welcome import BOT_URL, CONTACT_URL
        await self.process(self.job())
        with patch("movie_ingest.save_movie", return_value=True):
            await self.process(self.store.pending())
        buttons = self.bot.edit_message.call_args.kwargs["buttons"][0]
        self.assertEqual([(b.text, b.type.url) for b in buttons],
                         [("🤖 进入机器人", BOT_URL), ("💬 联系昊天", CONTACT_URL)])
        self.store.enqueue(11, True)
        self.store.update(self.row(11), "forwarded", result_id=102)
        old = message(102)
        old.fwd_from = object()
        self.bot.get_messages.return_value = [old]
        self.bot.edit_message.reset_mock()
        with patch("movie_ingest.save_movie", return_value=True):
            await self.process(self.store.pending())
        self.bot.edit_message.assert_not_awaited()

    def test_caption_keeps_only_title_episode_and_contiguous_tags(self):
        expected = "《嫁金钗》第十八集18 #4K #嫁金钗 #电视剧"
        for original in (expected, expected + "\n频道导航 @example\n#广告",
                         expected + " 频道导航 https://t.me/example #广告",
                         "《嫁金钗》第十八集18\n#4K #嫁金钗 #电视剧\n导航"):
            self.assertEqual(clean_movie_caption(original), expected)
        self.assertEqual(clean_movie_caption("无标签剧名\n频道导航 #广告"), "无标签剧名")
        with self.assertRaises(ValueError):
            clean_movie_caption("#电视剧")
        with self.assertRaises(ValueError):
            clean_movie_caption("x" * 1025)

    async def test_clean_website_index_without_editing_existing_copies(self):
        await self.process(self.job())
        self.bot.get_messages.return_value = [message(101, "《测试剧》第1集 #电视剧\n推广 #广告")]
        with patch("movie_ingest.save_movie", return_value=True) as save:
            await self.process(self.store.pending())
        self.assertEqual(save.call_args.args[2], "《测试剧》第1集 #电视剧")
        self.assertEqual(self.user.send_file.await_count, 1)

    async def test_new_publication_uses_existing_document_and_clean_caption(self):
        item = message(caption="《测试剧》第1集 #电视剧\n频道推广 #广告")
        self.user.get_messages.return_value = [item]
        await self.process(self.job())
        self.user.send_file.assert_awaited_once_with(
            self.target, item.document, caption="《测试剧》第1集 #电视剧",
            parse_mode=None, formatting_entities=[], silent=True)

    def job(self, ident=10, historical=True):
        self.store.enqueue(ident, historical)
        return self.store.pending()

    def row(self, ident=10):
        return self.store.connection.execute("SELECT * FROM jobs WHERE message=?", (ident,)).fetchone()

    async def process(self, job):
        await process_job(self.user, self.bot, self.source, self.target, self.store, job)

    def test_default_disabled_progress_and_dedup_survive_restart(self):
        self.assertEqual(self.store.setting("enabled"), "0")
        self.store.set("enabled", 1)
        self.store.set("not_before", 123456)
        self.job()
        self.store.update(self.row(), "sending", document_id=100)
        other = MovieStore(self.path, recover=True)
        try:
            self.assertEqual(other.setting("enabled"), "1")
            self.assertEqual(other.setting("not_before"), "123456")
            self.assertEqual(other.progress(99)["boundary"], 20)
            self.assertEqual(other.connection.execute("SELECT status FROM jobs").fetchone()[0], "uncertain")
            self.assertIsNone(other.pending())
            other.enqueue(10, True)
            self.assertEqual(other.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
        finally:
            other.close()

    def test_video_eligibility_and_protected_ads(self):
        self.assertEqual(video_metadata(message())["duration"], 123)
        self.assertIsNone(video_metadata(message(caption="")))
        self.assertIsNone(video_metadata(message(caption="#付费广告\n测试")))
        self.assertIsNone(video_metadata(message(caption="宣传推广 注册送福利 #广告")))
        self.assertIsNone(video_metadata(message(caption="欢迎加入我们的频道")))
        self.assertIsNone(video_metadata(message(caption="《测试》 #电视剧 #广告")))
        item = message()
        item.noforwards = True
        self.assertIsNone(video_metadata(item))
        item = message()
        item.document.attributes = [types.DocumentAttributeAudio(duration=30)]
        self.assertIsNone(video_metadata(item))
        self.assertIsNone(video_metadata(None))

    async def test_native_forward_then_index_without_second_forward(self):
        await self.process(self.job())
        self.assertEqual((self.row()["status"], self.row()["result_id"]), ("forwarded", 101))
        with patch("movie_ingest.save_movie", return_value=True) as save:
            await self.process(self.store.pending())
        self.assertEqual(self.row()["status"], "sent")
        self.user.send_file.assert_awaited_once()
        self.assertEqual(self.user.send_file.call_args.kwargs["caption"], message().message)
        self.assertEqual(save.call_args.args[:3], ("-1002", 101, message().message))
        self.assertEqual(save.call_args.kwargs["duration"], 123)

    async def test_forwarded_index_failure_never_republishes(self):
        await self.process(self.job())
        with patch("movie_ingest.save_movie", side_effect=RuntimeError("db offline")):
            with self.assertRaises(RuntimeError):
                await self.process(self.store.pending())
        self.assertEqual(self.row()["status"], "forwarded")
        self.assertEqual(self.user.send_file.await_count, 1)

    async def test_flood_wait_requeues_but_ambiguous_network_does_not(self):
        job = self.job()
        self.user.send_file.side_effect = errors.FloodWaitError(request=None, capture=20)
        with self.assertRaises(errors.FloodWaitError):
            await self.process(job)
        self.assertEqual(self.row()["status"], "queued")
        self.user.send_file.side_effect = ConnectionError("offline")
        with self.assertRaises(ConnectionError):
            await self.process(self.store.pending())
        self.assertEqual(self.row()["status"], "sending")

    async def test_protection_rechecked_before_forward(self):
        self.source.noforwards = True
        await self.process(self.job())
        self.assertEqual(self.row()["status"], "skipped")
        self.user.send_file.assert_not_awaited()

    async def test_same_document_reuses_copy_and_live_has_priority(self):
        await self.process(self.job())
        self.store.enqueue(11, True)
        self.store.enqueue(21, False)
        self.assertEqual(self.store.pending()["status"], "forwarded")
        with patch("movie_ingest.save_movie", return_value=True):
            await self.process(self.store.pending())
        self.assertEqual(self.store.pending()["message"], 21)
        await self.process(self.store.pending())
        self.assertEqual(self.row(21)["result_id"], 101)
        self.assertEqual(self.user.send_file.await_count, 1)

    async def test_historical_scan_bounded_and_live_separate(self):
        calls = []
        async def iterate(source, **options):
            calls.append(options)
            for ident in range(1, 24):
                if ident <= options["min_id"] or ident >= options.get("max_id", 1000):
                    continue
                yield message(ident)
        self.user.iter_messages = iterate
        await scan_source(self.user, self.source, self.store, True)
        self.assertEqual(self.store.progress()["history"], 20)
        self.assertEqual(self.store.progress()["complete"], 1)
        await scan_source(self.user, self.source, self.store, False)
        self.assertEqual(self.store.progress()["live"], 23)
        self.assertEqual(calls[0]["max_id"], 21)
        self.assertNotIn("max_id", calls[1])
        self.assertEqual(self.store.pending()["message"], 21)

    async def test_live_scan_still_runs_when_history_queue_full(self):
        for ident in range(1, 51):
            self.store.enqueue(ident, True)
        async def iterate(source, **options):
            yield message(60)
        self.user.iter_messages = iterate
        await scan_source(self.user, self.source, self.store, True)
        self.assertEqual(self.store.progress()["history"], 0)
        await scan_source(self.user, self.source, self.store, False)
        self.assertEqual(self.row(60)["historical"], 0)

    async def test_existing_channel_file_is_reused_not_forwarded_again(self):
        async def iterate(target, **options):
            self.assertEqual(options["min_id"], 0)
            yield message(88)
        self.user.iter_messages = iterate
        self.assertTrue(await scan_owned(self.user, self.target, self.store))
        self.assertEqual(self.store.setting("owned_cursor"), "88")
        await self.process(self.job())
        self.assertEqual(self.row()["result_id"], 88)
        self.assertEqual(self.row()["status"], "forwarded")
        self.user.send_file.assert_not_awaited()

    async def run_worker_failure(self, error):
        self.store.set("enabled", 1)
        self.job()
        self.source.id = SOURCE_ID
        self.target.id = TARGET_ID
        self.user.get_entity.side_effect = [self.source, self.target, self.source]
        self.user.get_me = AsyncMock()
        self.user.get_permissions = AsyncMock(return_value=SimpleNamespace(is_admin=True, post_messages=True))
        self.bot.get_entity = AsyncMock(return_value=self.target)
        self.bot.get_me = AsyncMock()
        self.bot.get_permissions = AsyncMock(return_value=SimpleNamespace(is_admin=True, edit_messages=True))
        self.user.send_file.side_effect = error
        async def iterate(*args, **kwargs):
            if False:
                yield None
        self.user.iter_messages = iterate
        # Worker uses this filename and opens its own connection.
        self.store.close()
        self.path.rename(self.path.parent / "movie-import.sqlite3")
        with patch("movie_ingest.ensure_tables"), patch("movie_ingest.time.time", return_value=100), \
                patch("movie_ingest.asyncio.sleep", side_effect=asyncio.CancelledError) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await movie_worker(self.user, self.bot, self.path.parent)
        check = MovieStore(self.path.parent / "movie-import.sqlite3")
        self.addCleanup(check.close)
        return check, sleep

    async def test_worker_persists_and_obeys_actual_flood_wait(self):
        check, sleep = await self.run_worker_failure(errors.FloodWaitError(request=None, capture=20))
        self.assertEqual(check.setting("not_before"), "121")
        self.assertEqual(check.pending()["status"], "queued")
        sleep.assert_awaited_once_with(21)

    async def test_worker_marks_ambiguous_send_uncertain(self):
        check, _ = await self.run_worker_failure(ConnectionError("offline"))
        row = check.connection.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row["status"], "uncertain")
        self.assertIsNone(check.pending())
