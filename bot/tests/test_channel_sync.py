from pathlib import Path
from datetime import datetime, timedelta, timezone
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telethon import types

from channel_sync import JobStore, deliver, enqueue_new_messages, reconcile_messages, single_instance
from channel_sync_clean import clean_text, is_advertisement


class CleanTextTests(unittest.TestCase):
    def test_advertisement_labels_and_news_distinguished(self):
        for text in ["#付费广告\n推广内容", "**#付费广告**\n推广内容",
                     "\n 【商业广告】\n推广内容", "#付費廣告：推广内容", "#广告 推广内容"]:
            with self.subTest(text=text):
                self.assertTrue(is_advertisement(text))
        for text in [None, "", "#新闻：广告诈骗被查处", "广告公司被查处",
                     "警方提醒\n不要相信付费广告", "#广告诈骗案件通报"]:
            with self.subTest(text=text):
                self.assertFalse(is_advertisement(text))

    def test_promotional_footer_and_links_removed(self):
        original = (
            "正文保留\n关注大事件频道➡️ @Svip 投稿：@tx188\n"
            "https://example.com/path www.example.org t.me/someone @other_contact"
        )
        self.assertEqual(clean_text(original), "正文保留")

    def test_entities_use_utf16_offsets_and_remove_hidden_contact_label(self):
        text = "🎵正文 点击联系 结尾"
        start = len("🎵正文 ".encode("utf-16-le")) // 2
        self.assertEqual(clean_text(
            text, [types.MessageEntityTextUrl(start, 4, "https://t.me/someone")]
        ), "🎵正文 结尾")

    def test_entity_mentions_and_footer_labels_removed(self):
        text = "正文\n关注大事件频道➡️ @Svip 投稿：@tx188"
        entities = [
            types.MessageEntityMention(len(text[:text.index(name)].encode("utf-16-le")) // 2, len(name))
            for name in ("@Svip", "@tx188")
        ]
        self.assertEqual(clean_text(text, entities), "正文")

    def test_plain_content_and_line_breaks_remain(self):
        text = "中文标题 🎬\n第一段\n\n第二段 12345 #新闻"
        self.assertEqual(clean_text(text), text)
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text("example.com/test @username"), "")


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.store = JobStore(self.directory / "test.sqlite3")
        self.addCleanup(self.store.close)
        self.source = SimpleNamespace(id=1)
        self.target = SimpleNamespace(id=2)
        self.user = SimpleNamespace(
            get_entity=AsyncMock(return_value=SimpleNamespace(noforwards=False)),
            get_messages=AsyncMock(), download_media=AsyncMock(),
        )
        self.bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(id=100)),
            send_file=AsyncMock(return_value=SimpleNamespace(id=101)),
        )

    def job(self, ids=(10,)):
        self.store.enqueue(1, "2", ids)
        return self.store.next(1, "2")

    def status(self):
        return self.store.connection.execute("SELECT status FROM jobs").fetchone()["status"]

    def historical(self, ident, album=None, recent=False, service=False):
        return SimpleNamespace(id=ident, grouped_id=album, action=service,
                               date=datetime.now(timezone.utc) - timedelta(seconds=0 if recent else 60))

    def history(self, messages):
        async def iterate(*args, **kwargs):
            for message in messages:
                if message.id > kwargs["min_id"]:
                    yield message
        self.user.iter_messages = iterate

    async def test_reconciliation_recovers_missing_album_without_repeating_sent_jobs(self):
        self.store.progress(1, "2", 20)
        for ids in [[21], [24]]:
            self.store.enqueue(1, "2", ids)
            self.store.update(self.store.next(1, "2"), "sent", result_ids=[100 + ids[0]])
        self.history([self.historical(21), self.historical(22, 500),
                      self.historical(23, 500), self.historical(24)])
        await reconcile_messages(self.user, self.source, self.target, self.store, 20)
        rows = self.store.connection.execute("SELECT job_key,status FROM jobs ORDER BY rowid").fetchall()
        self.assertEqual([(row["job_key"], row["status"]) for row in rows],
                         [("21", "sent"), ("24", "sent"), ("22,23", "queued")])
        self.assertEqual(self.store.progress(1, "2", 999)["scanned_id"], 24)
        await reconcile_messages(self.user, self.source, self.target, self.store, 20)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 3)

    async def test_reconciliation_waits_for_album_to_settle(self):
        self.store.progress(1, "2", 20)
        self.history([self.historical(21, 500), self.historical(22, 500, recent=True)])
        await reconcile_messages(self.user, self.source, self.target, self.store, 20)
        self.assertIsNone(self.store.next(1, "2"))
        self.assertEqual(self.store.progress(1, "2", 20)["scanned_id"], 20)

    async def test_full_scan_batch_never_splits_album(self):
        self.store.progress(1, "2", 20)
        self.history([self.historical(i) for i in range(21, 220)] +
                     [self.historical(220, 500), self.historical(221, 500)])
        with patch("channel_sync.logger.info"):
            await reconcile_messages(self.user, self.source, self.target, self.store, 20)
        self.assertEqual(self.store.progress(1, "2", 20)["scanned_id"], 219)
        self.history([self.historical(i, 500) for i in range(220, 223)])
        await reconcile_messages(self.user, self.source, self.target, self.store, 20)
        self.assertEqual(self.store.connection.execute(
            "SELECT job_key FROM jobs ORDER BY rowid DESC LIMIT 1").fetchone()[0], "220,221,222")

    def test_persistent_progress_initial_baseline_and_upgrade(self):
        progress = self.store.progress(1, "2", 20)
        self.assertEqual((progress["boundary"], progress["scanned_id"]), (20, 20))
        self.store.advance(1, "2", 22)
        another = JobStore(self.directory / "test.sqlite3")
        try:
            self.assertEqual(another.progress(1, "2", 99)["boundary"], 20)
            self.assertEqual(another.progress(1, "2", 99)["scanned_id"], 22)
        finally:
            another.close()
        self.store.enqueue(3, "2", [32, 33])
        self.assertEqual(self.store.progress(3, "2", 99)["boundary"], 31)

    async def test_enqueue_failure_retains_cursor_and_uncertain_not_requeued(self):
        self.store.progress(1, "2", 20)
        self.store.enqueue(1, "2", [21])
        self.store.update(self.store.next(1, "2"), "uncertain", error="manual review")
        self.history([self.historical(21), self.historical(22)])
        with patch.object(self.store, "advance", side_effect=RuntimeError("disk failure")):
            with self.assertRaises(RuntimeError):
                await reconcile_messages(self.user, self.source, self.target, self.store, 20)
        self.assertEqual(self.store.progress(1, "2", 99)["scanned_id"], 20)
        await reconcile_messages(self.user, self.source, self.target, self.store, 20)
        rows = self.store.connection.execute("SELECT job_key,status FROM jobs ORDER BY rowid").fetchall()
        self.assertEqual([(row["job_key"], row["status"]) for row in rows], [("21", "uncertain"), ("22", "queued")])

    def test_overlapping_album_jobs_are_rejected_instead_of_resent(self):
        self.store.enqueue(1, "2", [21])
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            self.store.enqueue(1, "2", [21, 22])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)

    def message(self, text="正文 @contact", media=None):
        return SimpleNamespace(
            message=text, entities=[], media=media, noforwards=False,
            document=None, video=None, audio=None, gif=None,
        )

    async def publish(self, messages, ids=(10,)):
        self.user.get_messages.return_value = messages
        await deliver(self.user, self.bot, self.source, self.target, self.store,
                      self.job(ids), self.directory)

    def test_deduplication_and_recovery_persist(self):
        job = self.job((11, 10))
        self.store.enqueue(1, "2", [10, 11])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
        self.store.update(job, "sending")
        self.store.recover()
        self.assertEqual(self.status(), "uncertain")
        self.assertIsNone(self.store.next(1, "2"))

    def test_only_new_messages_queued_and_album_order_retained(self):
        enqueue_new_messages(self.store, 1, 2, 20, [SimpleNamespace(id=20)])
        enqueue_new_messages(self.store, 1, 2, 20, [SimpleNamespace(id=19)])
        self.assertIsNone(self.store.next(1, "2"))
        enqueue_new_messages(self.store, 1, 2, 20,
                             [SimpleNamespace(id=22), SimpleNamespace(id=21)])
        self.assertEqual(self.store.next(1, "2")["message_ids"], "[21, 22]")

    def test_single_instance_prevents_two_listeners(self):
        with single_instance(self.directory):
            with self.assertRaisesRegex(RuntimeError, "Another"):
                with single_instance(self.directory):
                    self.fail("Second listener must not acquire lock")

    def test_queued_work_survives_restart(self):
        self.job()
        another = JobStore(self.directory / "test.sqlite3")
        try:
            another.recover()
            self.assertEqual(another.next(1, "2")["job_key"], "10")
        finally:
            another.close()

    async def test_text_uses_bot_and_disables_formatting(self):
        await self.publish([self.message()])
        self.bot.send_message.assert_awaited_once_with(
            self.target, "正文", parse_mode=None, link_preview=False,
        )
        self.assertEqual(self.status(), "sent")

    async def test_empty_cleaned_text_is_skipped(self):
        await self.publish([self.message("https://example.com @contact")])
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(self.status(), "skipped")

    async def test_paid_ad_text_is_skipped_before_cleaning(self):
        await self.publish([self.message(
            "**#付费广告**\n首存5000 赠送彩金588\n"
            "实力盘口出款有保障 https://example.com @service"
        )])
        self.bot.send_message.assert_not_awaited()
        self.bot.send_file.assert_not_awaited()
        self.assertEqual(self.status(), "skipped")

    async def test_paid_ad_album_is_skipped_as_a_whole(self):
        photo = types.MessageMediaPhoto()
        await self.publish([self.message(None, photo),
                            self.message("#付费广告\n首存活动", photo)], (10, 11))
        self.user.download_media.assert_not_awaited()
        self.bot.send_file.assert_not_awaited()
        self.assertEqual(self.status(), "skipped")

    async def test_news_reporting_advertising_is_not_skipped(self):
        text = "#新闻：警方查处赌博推广\n平台以付费广告、首存赠送彩金诱导充值。"
        await self.publish([self.message(text)])
        self.bot.send_message.assert_awaited_once_with(
            self.target, text, parse_mode=None, link_preview=False,
        )
        self.assertEqual(self.status(), "sent")

    async def test_protected_message_not_downloaded(self):
        message = self.message()
        message.noforwards = True
        await self.publish([message])
        self.user.download_media.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(self.status(), "skipped")

    async def test_channel_becoming_protected_is_respected(self):
        self.user.get_entity.return_value.noforwards = True
        await self.publish([self.message()])
        self.user.get_messages.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(self.status(), "skipped")

    async def test_album_sent_once_with_clean_captions_and_temp_cleanup(self):
        self.user.download_media.side_effect = ["one.jpg", "two.jpg"]
        self.bot.send_file.return_value = [SimpleNamespace(id=100), SimpleNamespace(id=101)]
        photo = types.MessageMediaPhoto()
        await self.publish([self.message("第一张 @contact", photo),
                            self.message("第二张 https://example.com", photo)], (10, 11))
        self.bot.send_file.assert_awaited_once_with(
            self.target, ["one.jpg", "two.jpg"], caption=["第一张", "第二张"], parse_mode=None,
        )
        self.assertEqual(self.status(), "sent")
        self.assertEqual(list(self.directory.glob("media-*")), [])

    async def test_photo_without_caption_still_published(self):
        self.user.download_media.return_value = "one.jpg"
        await self.publish([self.message("@contact", types.MessageMediaPhoto())])
        self.assertEqual(self.bot.send_file.await_args.kwargs["caption"], "")
        self.assertEqual(self.status(), "sent")

    async def test_video_attributes_preserved(self):
        message = self.message("视频正文", types.MessageMediaDocument())
        message.document = SimpleNamespace(attributes=["video metadata"])
        message.video = True
        self.user.download_media.return_value = "video.mp4"
        await self.publish([message])
        kwargs = self.bot.send_file.await_args.kwargs
        self.assertEqual(kwargs["attributes"], ["video metadata"])
        self.assertTrue(kwargs["supports_streaming"])
        self.assertFalse(kwargs["force_document"])

    async def test_unsupported_media_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            await self.publish([self.message("投票", types.MessageMediaPoll(poll=None, results=None))])
        self.bot.send_file.assert_not_awaited()

    async def test_send_failure_remains_sending_for_uncertain_recovery(self):
        self.bot.send_message.side_effect = ConnectionError("disconnected")
        with self.assertRaises(ConnectionError):
            await self.publish([self.message()])
        self.assertEqual(self.status(), "sending")
        self.store.recover()
        self.assertEqual(self.status(), "uncertain")


if __name__ == "__main__":
    unittest.main()
