from pathlib import Path
from datetime import datetime, timedelta, timezone
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telethon import types, errors

from channel_sync import JobStore, complete_album, deliver, enqueue_new_messages, prepare_news_sources, reconcile_messages, single_instance, split_telegram_text, worker
from news_sources import NEWS_SOURCES, announcement_job_key
from channel_sync_clean import clean_text, is_advertisement, is_news_advertisement


MISSED_NEWS_ADS = (
    "🤩🤩🤩🤩\n🤩🤩🤩🤩🤩\n东南亚大事件联合担保🤩🤩(电子)\n"
    "香港六合彩 · 独家盈利无上限！\n百家乐全网最高起步返水\n诚招代理合作：\n打码返佣",
    "🫥🏆🏆🏆\n😀云购入口: https://example.com\n😀云购网址: https://example.com\n"
    "一元云购主营商品：\n每消费1USDT即可获得1积分，积分可用于积分抽奖或者兑现成USDT\n"
    "中奖后可选择实体发货或兑现成USDT进行提现。\n官方频道: @example\n官方客服: @service",
    "在 Telegram 里找内容，先打开 SOSO 搜搜。\n\n频道、群组、帖子、贴纸都可以搜；"
    "也可以从发现页继续浏览。\n🔗 本频道专属邀请链接 https://example.com",
    "Telegram必备的搜索引擎，极搜帮你精准找到，想要的群组、频道、视频、音乐\n👉 t.me/example",
    "#频道互推 #相似推荐\n----------------\n📢 @example\n🤝 · 获得曝光，赚邀请奖励",
    "✅\n🎁 购买所有广告获得优惠\n👇 点击下方按钮进绑定优惠券\n"
    "🔍搜索导航新版最浪的人，善解人衣\n以下均为我官方机器人，广告同步展示 总月活 500W+\n详细咨询: 七哥",
    "🫤😐🫥\n支持5倍验资 【验资:】\n注册就送28.8U\n新人首存/二存/三存赠送8888U\n"
    "电子狂欢每日存款赠送10%\n充值笔笔送3%无上限\n香港六合彩48.8倍\n加拿大28全网返水最高\n"
    "官方客服：\n官方频道：\n注册网址：\n欢迎各位老板加入N9国际娱乐城！",
    "😀😀😀😀\n😀NO钱包，雄厚实力护航，官网权威验证（https://example.com）\n"
    "😀😀😀😀😀\n😀知名免费能量赞助商\n😀😀😀😀",
)


class CleanTextTests(unittest.TestCase):
    def test_reported_unlabelled_ads_and_cleaned_copies_are_blocked(self):
        for text in MISSED_NEWS_ADS:
            with self.subTest(text=text):
                self.assertFalse(is_advertisement(text))
                self.assertTrue(is_news_advertisement(text))
                self.assertTrue(is_news_advertisement(clean_text(text)))

    def test_news_promotional_detection_preserves_reports_and_unrelated_content(self):
        for text in (
            "#新闻：警方查处赌博推广\n平台以付费广告、首存赠送彩金诱导充值。",
            "警方提醒警惕一元云购\n每消费1USDT即可获得1积分，积分抽奖；官方客服是假冒身份。",
            "东南亚警方曝光联合担保骗局\n百家乐高额起步返水、诚招代理只是诈骗话术。",
            "Telegram必备的搜索引擎，极搜被警方查处\n号称精准找到频道、群组。",
            "本地搜救队找到了失踪人员", "Telegram发布搜索功能更新\n可搜索频道和群组。",
            "#频道互推诈骗案件通报\n警惕所谓获得曝光、赚邀请奖励。",
            "#新闻\n报道中提及：在 Telegram 里找内容，先打开 SOSO 搜搜。\n"
            "本频道专属邀请链接被用于诈骗，涉及多个频道、群组。",
            "#频道互推", "一元云购平台被调查", "", None,
            "警方提醒：NO钱包冒名广告涉嫌诈骗\n雄厚实力护航，官网权威验证；知名免费能量赞助商。",
            "警方查处娱乐城\n注册就送28.8U，首存赠送，香港六合彩和全网返水，注册网址。",
            "广告优惠券市场调查报道\n购买所有广告获得优惠，官方机器人广告同步展示。",
            "NO钱包发布安全更新", "免费能量公益项目启动",
            "本地商户推出注册优惠\n注册就送优惠券，官网咨询。",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_news_advertisement(text))

    def test_news_filter_normalizes_invisible_and_fullwidth_characters_without_changing_shared_filter(self):
        text = "\u200bＴｅｌｅｇｒａｍ必备的搜索引擎，极搜帮你精准找到群组、频道"
        self.assertTrue(is_news_advertisement(text))
        self.assertFalse(is_advertisement(text))
        for text in ("#付费广告\n内容", "\u200b#付費廣告：内容"):
            self.assertTrue(is_news_advertisement(text))

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
            get_input_entity=AsyncMock(side_effect=lambda peer: peer),
            get_entity=AsyncMock(return_value=SimpleNamespace(noforwards=False)),
            get_messages=AsyncMock(), download_media=AsyncMock(),
        )
        self.bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(id=100)),
            send_file=AsyncMock(return_value=SimpleNamespace(id=101)),
        )
        digest = patch("channel_sync.media_digest", side_effect=lambda paths: "|".join(paths))
        digest.start()
        self.addCleanup(digest.stop)

    def job(self, ids=(10,)):
        self.store.enqueue(1, "2", ids)
        return self.store.next(1, "2")

    def status(self):
        return self.store.connection.execute("SELECT status FROM jobs").fetchone()["status"]

    def historical(self, ident, album=None, recent=False, service=False):
        return SimpleNamespace(id=ident, grouped_id=album, action=service,
                               date=datetime.now(timezone.utc) - timedelta(seconds=0 if recent else 60))

    def history(self, messages):
        async def get_messages(*args, **kwargs):
            return [message for message in messages if message.id in kwargs["ids"]]
        self.user.get_messages.side_effect = get_messages
        async def iterate(*args, **kwargs):
            for message in messages:
                if message.id > kwargs["min_id"]:
                    yield message
        self.user.iter_messages = iterate

    async def test_partial_album_recovers_caption_bearing_first_member(self):
        album = [self.historical(i, 500) for i in range(21, 25)]
        album[0].message = "新闻正文"
        self.history(album)
        completed = await complete_album(self.user, self.source, album[1:])
        self.assertEqual([message.id for message in completed], [21, 22, 23, 24])
        self.assertEqual(completed[0].message, "新闻正文")

    async def test_incomplete_album_job_is_not_published_without_caption(self):
        photo = types.MessageMediaPhoto()
        album = [self.message("新闻正文" if i == 10 else "", photo) for i in range(10, 14)]
        for ident, message in zip(range(10, 14), album):
            message.id = ident
            message.grouped_id = 500
        self.user.get_messages.side_effect = [album[1:], album]
        with self.assertRaisesRegex(ValueError, "Incomplete album job"):
            await deliver(self.user, self.bot, self.source, self.target, self.store,
                          self.job((11, 12, 13)), self.directory)
        self.bot.send_file.assert_not_awaited()
        self.user.download_media.assert_not_awaited()

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

    async def test_overlapping_history_is_recorded_without_republishing_and_later_news_proceeds(self):
        self.store.progress(1, "2", 20)
        self.store.enqueue(1, "2", [22, 23])
        self.store.update(self.store.next(1, "2"), "sent", result_ids=[100, 101])
        self.history([self.historical(21, 500), self.historical(22, 500),
                      self.historical(23, 500), self.historical(24)])
        await reconcile_messages(self.user, self.source, self.target, self.store, 20)
        rows = self.store.connection.execute(
            "SELECT job_key,status,result_ids,error FROM jobs ORDER BY rowid").fetchall()
        self.assertEqual([(row["job_key"], row["status"]) for row in rows],
                         [("22,23", "sent"), ("21,22,23", "uncertain"), ("24", "queued")])
        self.assertEqual(rows[0]["result_ids"], "[100, 101]")
        self.assertIn("not republished", rows[1]["error"])
        self.assertEqual(self.store.progress(1, "2", 99)["scanned_id"], 24)
        await reconcile_messages(self.user, self.source, self.target, self.store, 20)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 3)

    async def test_overlap_record_failure_does_not_advance_cursor(self):
        self.store.progress(1, "2", 20)
        self.store.enqueue(1, "2", [22])
        self.history([self.historical(21, 500), self.historical(22, 500)])
        with patch.object(self.store, "record_overlap", side_effect=RuntimeError("disk failure")):
            with self.assertRaisesRegex(RuntimeError, "disk failure"):
                await reconcile_messages(self.user, self.source, self.target, self.store, 20)
        self.assertEqual(self.store.progress(1, "2", 99)["scanned_id"], 20)

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

    async def test_news_sources_start_at_independent_latest_messages_and_keep_existing_cursor(self):
        channels = [
            types.Channel(id=ident, title=username, username=username,
                          photo=types.ChatPhotoEmpty(), date=datetime.now(timezone.utc),
                          broadcast=True)
            for username, ident in NEWS_SOURCES
        ]
        self.user.get_entity.side_effect = channels
        self.user.get_messages.side_effect = [[SimpleNamespace(id=100 + index)]
                                             for index in range(len(channels))]
        self.store.progress(channels[0].id, "2", 20)
        self.store.advance(channels[0].id, "2", 25)
        sources, boundaries = await prepare_news_sources(self.user, self.target, self.store)
        self.assertEqual(self.user.get_input_entity.await_count, len(NEWS_SOURCES))
        self.assertEqual(
            [call.args[0].channel_id for call in self.user.get_input_entity.await_args_list],
            [ident for _, ident in NEWS_SOURCES],
        )
        self.assertEqual(len(sources), 7)
        self.assertEqual((channels[-1].username, channels[-1].id), ("vhhhh", 2062055062))
        self.assertEqual(boundaries[channels[0].id], 20)
        self.assertEqual(self.store.progress(channels[0].id, "2", 999)["scanned_id"], 25)
        for index, channel in enumerate(channels[1:], 1):
            self.assertEqual(boundaries[channel.id], 100 + index)
            enqueue_new_messages(self.store, channel.id, 2, boundaries[channel.id],
                                 [SimpleNamespace(id=100 + index)])
            self.assertIsNone(self.store.next(channel.id, "2"))
            enqueue_new_messages(self.store, channel.id, 2, boundaries[channel.id],
                                 [SimpleNamespace(id=101 + index)])
            self.assertIsNotNone(self.store.next(channel.id, "2"))

    async def test_protected_news_source_rejects_activation_before_any_baselines_are_written(self):
        username, ident = NEWS_SOURCES[0]
        channel = types.Channel(id=ident, title=username, username=username,
                                photo=types.ChatPhotoEmpty(), date=datetime.now(timezone.utc),
                                broadcast=True, noforwards=True)
        self.user.get_entity.return_value = channel
        with self.assertRaisesRegex(ValueError, "protected"):
            await prepare_news_sources(self.user, self.target, self.store)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM sync_progress").fetchone()[0], 0)

    async def test_changed_news_username_identity_is_rejected(self):
        channel = types.Channel(id=999, title="changed", photo=types.ChatPhotoEmpty(),
                                date=datetime.now(timezone.utc), broadcast=True)
        self.user.get_entity.return_value = channel
        with self.assertRaisesRegex(ValueError, "identity"):
            await prepare_news_sources(self.user, self.target, self.store)
        self.user.get_messages.assert_not_awaited()

    async def test_stale_cached_news_username_is_rejected_even_with_matching_numeric_id(self):
        channel = types.Channel(id=NEWS_SOURCES[0][1], title="changed", username="different",
                                photo=types.ChatPhotoEmpty(), date=datetime.now(timezone.utc), broadcast=True)
        self.user.get_entity.return_value = channel
        with self.assertRaisesRegex(ValueError, "identity"):
            await prepare_news_sources(self.user, self.target, self.store)
        self.user.get_messages.assert_not_awaited()

    async def test_news_sources_accept_active_alias_but_not_inactive_alias(self):
        channels = [
            types.Channel(id=ident, title=username, username=None,
                          usernames=[types.Username(username=username.upper(), active=True)],
                          photo=types.ChatPhotoEmpty(), date=datetime.now(timezone.utc), broadcast=True)
            for username, ident in NEWS_SOURCES
        ]
        self.user.get_entity.side_effect = channels
        self.user.get_messages.return_value = [SimpleNamespace(id=100)]
        sources, _ = await prepare_news_sources(self.user, self.target, self.store)
        self.assertEqual(len(sources), len(NEWS_SOURCES))
        channels[0].usernames[0].active = False
        self.user.get_entity.side_effect = None
        self.user.get_entity.return_value = channels[0]
        self.user.get_messages.reset_mock()
        with self.assertRaisesRegex(ValueError, "identity"):
            await prepare_news_sources(self.user, self.target, self.store)
        self.user.get_messages.assert_not_awaited()

    def test_news_queue_and_index_keys_distinguish_identical_message_numbers(self):
        legacy, added = NEWS_SOURCES[0][1], NEWS_SOURCES[1][1]
        self.store.enqueue(legacy, "2", [10])
        self.store.enqueue(added, "2", [10])
        self.store.enqueue(999, "2", [1])
        first = self.store.next_news((legacy, added), "2")
        self.assertEqual(first["source"], legacy)
        self.assertEqual(announcement_job_key(first), "2:10")
        self.store.update(first, "sent", result_ids=[100])
        second = self.store.next_news((legacy, added), "2")
        self.assertEqual(second["source"], added)
        self.assertEqual(announcement_job_key(second), f"2:{added}:10")
        self.store.update(second, "uncertain", result_ids=[101])
        self.assertIsNone(self.store.next_news((legacy, added), "2"))

    def test_operations_health_filters_news_queues_by_source(self):
        from operations_health import _component
        self.store.enqueue(1, "2", [10])
        self.store.enqueue(3, "2", [10])
        self.store.update(self.store.next(3, "2"), "failed")
        result = _component(self.directory / "test.sqlite3", "news_test", "Test news",
                            source=1)
        self.assertEqual(result["counts"], {"queued": 1})

    def test_operations_health_counts_unindexed_news_with_source_safe_keys(self):
        from operations_health import _announcement_pending
        legacy, added = NEWS_SOURCES[0][1], NEWS_SOURCES[1][1]
        for source in (legacy, added):
            self.store.enqueue(source, "2", [10])
            self.store.update(self.store.next(source, "2"), "sent", result_ids=[source])
        database = MagicMock()
        cursor = database.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{"job_key": "2:10"}]
        component = {"state": "active", "counts": {}, "note": None}
        with patch("operations_health.get_conn", return_value=database):
            _announcement_pending(component, self.directory / "test.sqlite3")
        self.assertEqual(component["counts"], {"pending_index": 1})
        self.assertEqual(set(cursor.execute.call_args.args[1]), {"2:10", f"2:{added}:10"})
        database.close.assert_called_once()

    async def test_single_news_publisher_routes_jobs_to_the_correct_source(self):
        second = SimpleNamespace(id=3)
        self.store.enqueue(3, "2", [10])
        with patch("channel_sync.deliver", new_callable=AsyncMock) as send, \
                patch("channel_sync.asyncio.sleep", side_effect=RuntimeError("stop worker")):
            send.side_effect = lambda user, bot, source, target, store, job, directory: store.update(
                job, "sent", result_ids=[100])
            with self.assertRaisesRegex(RuntimeError, "stop worker"):
                await worker(self.user, self.bot, [self.source, second], self.target,
                             self.store, self.directory)
            self.assertEqual(send.await_args.args[2], second)

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

    async def test_textless_location_is_skipped_with_reason_without_sending(self):
        await self.publish([self.message(None, types.MessageMediaGeo(types.GeoPointEmpty()))])
        self.assertEqual(self.status(), "skipped")
        row = self.store.connection.execute("SELECT error FROM jobs").fetchone()
        self.assertIn("MessageMediaGeo", row["error"])
        self.user.download_media.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()
        self.bot.send_file.assert_not_awaited()

    async def test_unsupported_media_with_news_text_is_not_silently_discarded(self):
        with self.assertRaisesRegex(ValueError, "Unsupported media"):
            await self.publish([self.message("新闻正文", types.MessageMediaGeo(types.GeoPointEmpty()))])
        self.bot.send_message.assert_not_awaited()
        self.bot.send_file.assert_not_awaited()

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

    async def test_unlabelled_promotional_media_skipped_before_download_or_deduplication(self):
        await self.publish([self.message(MISSED_NEWS_ADS[0], types.MessageMediaPhoto())])
        self.user.download_media.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()
        self.bot.send_file.assert_not_awaited()
        self.assertEqual(self.status(), "skipped")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM news_fingerprints").fetchone()[0], 0)

    async def test_unlabelled_promotion_in_album_excludes_entire_album(self):
        photo = types.MessageMediaPhoto()
        await self.publish([self.message(None, photo),
                            self.message(MISSED_NEWS_ADS[4], photo)], (10, 11))
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

    async def test_long_caption_publishes_media_and_full_text_as_one_indexable_job(self):
        text = "新闻正文" * 400
        self.user.download_media.return_value = "one.jpg"
        await self.publish([self.message(text, types.MessageMediaPhoto())])
        self.assertEqual(self.bot.send_file.await_args.kwargs["caption"], "")
        self.bot.send_message.assert_awaited_once_with(
            self.target, text, parse_mode=None, link_preview=False, reply_to=101,
        )
        import json
        row = self.store.connection.execute("SELECT status,result_ids FROM jobs").fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertEqual(json.loads(row["result_ids"]), [101, 100])

    async def test_album_long_caption_keeps_text_order_and_all_media(self):
        first, second = "甲" * 1025, "第二张正文"
        self.user.download_media.side_effect = ["one.jpg", "two.jpg"]
        self.bot.send_file.return_value = [SimpleNamespace(id=101), SimpleNamespace(id=102)]
        photo = types.MessageMediaPhoto()
        await self.publish([self.message(first, photo), self.message(second, photo)], (10, 11))
        self.assertEqual(self.bot.send_file.await_args.kwargs["caption"], ["", ""])
        self.assertEqual(self.bot.send_message.await_args.args[1], first + "\n\n" + second)

    async def test_caption_utf16_boundary_and_long_plain_text(self):
        for text in ["文" * 1024, "🎵" * 512]:
            with self.subTest(text_length=len(text)):
                self.user.get_messages.return_value = [self.message(text, types.MessageMediaPhoto())]
                self.user.download_media.return_value = "one.jpg"
                job = self.job((len(text),))
                await deliver(self.user, self.bot, self.source, self.target, self.store, job, self.directory)
                self.assertEqual(self.bot.send_file.await_args.kwargs["caption"], text)
        text = "🎵" * 3000 + "结尾"
        parts = split_telegram_text(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in parts))
        self.user.get_messages.return_value = [self.message(text)]
        job = self.job((20000,))
        await deliver(self.user, self.bot, self.source, self.target, self.store, job, self.directory)
        self.assertEqual([call.args[1] for call in self.bot.send_message.await_args_list], parts)

    async def test_partial_send_failure_retains_media_ids_for_manual_recovery(self):
        self.user.download_media.return_value = "one.jpg"
        self.bot.send_message.side_effect = ConnectionError("disconnected")
        with self.assertRaises(ConnectionError):
            await self.publish([self.message("文" * 1025, types.MessageMediaPhoto())])
        self.store.recover()
        row = self.store.connection.execute("SELECT status,result_ids FROM jobs").fetchone()
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["result_ids"], "[101]")

    async def test_rate_limit_after_media_does_not_requeue_or_erase_partial_result(self):
        self.job()
        self.user.get_messages.return_value = [self.message("文" * 1025, types.MessageMediaPhoto())]
        self.user.download_media.return_value = "one.jpg"
        self.bot.send_message.side_effect = errors.FloodWaitError(request=None, capture=2)
        with patch("channel_sync.asyncio.sleep", side_effect=RuntimeError("stop worker")):
            with self.assertRaisesRegex(RuntimeError, "stop worker"):
                await worker(self.user, self.bot, self.source, self.target, self.store, self.directory)
        row = self.store.connection.execute("SELECT status,result_ids FROM jobs").fetchone()
        self.assertEqual((row["status"], row["result_ids"]), ("uncertain", "[101]"))
        self.assertIsNone(self.store.next(1, "2"))

    async def test_connection_error_after_media_keeps_result_in_worker(self):
        self.job()
        self.user.get_messages.return_value = [self.message("文" * 1025, types.MessageMediaPhoto())]
        self.user.download_media.return_value = "one.jpg"
        self.bot.send_message.side_effect = ConnectionError("offline")
        with patch("channel_sync.asyncio.sleep", side_effect=RuntimeError("stop worker")), \
                patch("channel_sync.logger.exception"):
            with self.assertRaisesRegex(RuntimeError, "stop worker"):
                await worker(self.user, self.bot, self.source, self.target, self.store, self.directory)
        row = self.store.connection.execute("SELECT status,result_ids,error FROM jobs").fetchone()
        self.assertEqual(tuple(row), ("uncertain", "[101]", "ConnectionError"))

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
