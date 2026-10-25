from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import announcements
import api_server
from channel_sync import JobStore, index_announcements, index_announcement
from tenants import tenant_revision, validate_config
from tests.test_tenants import config


class AnnouncementTests(unittest.TestCase):
    def test_summary_and_media_fallback(self):
        self.assertEqual(announcements.summary("标题\n\n正文", []), "标题 正文")
        self.assertEqual(announcements.summary("长" * 91, []), "长" * 90 + "…")
        self.assertEqual(announcements.summary("", [{"kind": "photo"}]), "图片公告")
        self.assertEqual(announcements.summary("", [{"kind": "video"}]), "视频公告")

    def test_serialization_never_exposes_storage(self):
        row = {"id": 1, "body": "<script>正文</script>", "published_at": datetime(2026, 1, 1),
               "media": '[{"kind":"photo","filename":"2-100.jpg"}]'}
        result = announcements.serialize(row, detail=True)
        self.assertEqual(result["media"], [{"kind": "photo", "url": "/announcement-media/1/0"}])
        self.assertNotIn("body", announcements.serialize(row))
        self.assertEqual(result["body"], row["body"])
        self.assertEqual(result["cover"], {"kind": "photo", "url": "/announcement-media/1/0"})
        self.assertNotIn("filename", str(result))

    def test_news_title_category_excerpt_and_content_do_not_repeat(self):
        body = "#本地资讯：这是一条完整新闻标题\n\n第一段正文。\n\n第二段正文。"
        result = announcements.reading_text(body, [])
        self.assertEqual(result["category"], "本地资讯")
        self.assertEqual(result["title"], "这是一条完整新闻标题")
        self.assertEqual(result["content"], "第一段正文。\n\n第二段正文。")
        self.assertEqual(result["excerpt"], "第一段正文。 第二段正文。")

    def test_long_single_line_keeps_full_content_and_media_only_has_title(self):
        body = "正文" * 100
        result = announcements.reading_text(body, [])
        self.assertEqual(len(result["title"]), 121)
        self.assertEqual(result["content"], body)
        self.assertEqual(len(result["excerpt"]), 101)
        for media, title in [([], "文字公告"), ([{"kind": "video"}], "视频公告"), ([{"kind": "photo"}], "图片公告")]:
            result = announcements.reading_text("", media)
            self.assertEqual(result["title"], title)
            self.assertEqual(result["content"], "")
        result = announcements.reading_text("#本地资讯：\n\n正文", [])
        self.assertEqual(result["content"], "正文")

    def test_catalog_selects_first_photo_not_video_as_cover(self):
        row = {"id": 2, "body": "标题\n正文", "published_at": datetime(2026, 1, 1),
               "media": [{"kind": "video", "filename": "2-1.mp4"}, {"kind": "photo", "filename": "2-2.jpg"}]}
        result = announcements.serialize(row)
        self.assertTrue(result["has_video"])
        self.assertEqual(result["cover"]["url"], "/announcement-media/2/1")
        self.assertEqual(result["title"], "标题")
        self.assertEqual(result["excerpt"], "正文")
        self.assertNotIn("content", result)
        row["media"] = []
        self.assertIsNone(announcements.serialize(row)["cover"])

    def test_strict_inputs_and_path_scope(self):
        for value in [None, True, -1, 0, "1", 1.5, 100001]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                announcements.catalog(value)
        for value in [None, True, -1, 0, "1", 2 ** 63]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                announcements.detail(value)
        for filename in ["..\\private", "2-100.jpg.exe", "D:\\secret.mp4", "../2-100.mp4"]:
            with self.assertRaises(ValueError):
                announcements.local_media_path(filename)
        self.assertEqual(announcements.local_media_path("2-100.mp4").name, "2-100.mp4")

    def test_paging_clamps_and_orders_stably(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"total": 21}
        cur.fetchall.return_value = []
        with patch("announcements.get_conn", return_value=conn):
            result = announcements.catalog(100)
        self.assertEqual((result["page"], result["pages"]), (2, 2))
        self.assertEqual(cur.execute.call_args.args[1], (20,))
        self.assertIn("published_at DESC,id DESC", cur.execute.call_args.args[0])
        conn.close.assert_called_once()

    def test_permission_validation_and_revision(self):
        before = config()
        enabled = {**before, "announcements_enabled": True}
        validate_config(enabled)
        self.assertNotEqual(tenant_revision(before), tenant_revision(enabled))
        for value in [1, "true", None]:
            with self.assertRaises(ValueError):
                validate_config({**before, "announcements_enabled": value})

    def test_tenant_api_and_media_permission_before_storage(self):
        client = api_server.app.test_client()
        with patch.object(api_server, "load_tenant", return_value=config()), \
                patch("announcements.catalog") as catalog, patch("announcements.media_file") as media:
            for action in ["get_announcements", "get_announcement"]:
                response = client.post("/webhook?tenant=test-shop", json={"action": action, "id": 1})
                self.assertEqual(response.status_code, 403)
            self.assertEqual(client.get("/announcement-media/1/0?tenant=test-shop").status_code, 403)
            catalog.assert_not_called()
            media.assert_not_called()
        with patch.object(api_server, "load_tenant", return_value={**config(), "announcements_enabled": True}), \
                patch("announcements.catalog", return_value={"items": []}):
            self.assertEqual(client.post("/webhook?tenant=test-shop", json={"action": "get_announcements"}).status_code, 200)

    def test_detail_missing_invalid_and_db_failure(self):
        client = api_server.app.test_client()
        with patch("announcements.detail", return_value=None):
            self.assertEqual(client.post("/webhook", json={"action": "get_announcement", "id": 1}).status_code, 404)
        self.assertEqual(client.post("/webhook", json={"action": "get_announcement", "id": True}).status_code, 400)
        with patch("announcements.catalog", side_effect=api_server.pymysql.OperationalError("offline")):
            self.assertEqual(client.post("/webhook", json={"action": "get_announcements"}).status_code, 503)

    def test_local_video_supports_range_head_and_missing(self):
        client = api_server.app.test_client()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "2-100.mp4"
            path.write_bytes(b"0123456789")
            with patch("announcements.media_file", return_value={"kind": "video", "filename": path.name}), \
                    patch("announcements.local_media_path", return_value=path):
                response = client.get("/announcement-media/1/0", headers={"Range": "bytes=2-5"})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.data, b"2345")
                self.assertEqual(response.headers["Content-Range"], "bytes 2-5/10")
                response.close()
                response = client.head("/announcement-media/1/0")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b"")
                response.close()
            with patch("announcements.media_file", return_value=None):
                self.assertEqual(client.get("/announcement-media/1/0").status_code, 404)


class AnnouncementIndexTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = JobStore(self.directory / "jobs.sqlite3")
        self.addCleanup(self.store.close)
        self.target = SimpleNamespace(id=2)
        self.bot = SimpleNamespace(get_messages=AsyncMock(), download_media=AsyncMock(),
                                   send_message=AsyncMock(), send_file=AsyncMock())

    def sent(self, source_id, target_id):
        self.store.enqueue(1, "2", [source_id])
        job = self.store.next(1, "2")
        self.store.update(job, "sent", result_ids=[target_id])
        return self.store.connection.execute("SELECT * FROM jobs WHERE job_key=?", (str(source_id),)).fetchone()

    def message(self, ident, body="", photo=False):
        return SimpleNamespace(id=ident, message=body, photo=photo, video=False, gif=False,
                               file=SimpleNamespace(size=3), date=datetime.now(timezone.utc))

    async def test_album_target_body_and_atomic_media(self):
        messages = [self.message(100, "清理后的正文", True), self.message(101, photo=True)]
        self.bot.get_messages.return_value = messages
        async def download(message, file):
            Path(file).write_bytes(b"jpg")
        self.bot.download_media.side_effect = download
        with patch("announcements.local_media_path", side_effect=lambda name: self.directory / name), \
                patch("announcements.save") as save:
            await index_announcement(self.bot, self.target, {"result_ids": "[100,101]", "job_key": "10,11"}, "2:10,11")
            self.assertEqual(save.call_args.args[1:3], ([100, 101], "清理后的正文"))
            self.assertEqual(len(save.call_args.args[3]), 2)
            self.assertFalse(list(self.directory.glob("*.part")))
        self.bot.send_file.assert_not_awaited()

    async def test_only_sent_unindexed_jobs_and_no_repeat_send_on_failure(self):
        self.sent(10, 100)
        self.sent(11, 101)
        self.store.enqueue(1, "2", [12])
        with patch("announcements.indexed_keys", return_value={"2:10"}), \
                patch("channel_sync.index_announcement", new_callable=AsyncMock, side_effect=ValueError("offline")) as index:
            with self.assertRaises(ValueError):
                await index_announcements(self.bot, self.target, self.store)
            self.assertEqual(index.await_count, 1)
        rows = self.store.connection.execute("SELECT status FROM jobs ORDER BY rowid").fetchall()
        self.assertEqual([row["status"] for row in rows], ["sent", "sent", "queued"])
        self.bot.send_message.assert_not_awaited()
        self.bot.send_file.assert_not_awaited()

    async def test_media_and_long_text_reply_index_as_one_complete_announcement(self):
        body = "完整新闻正文" * 300
        self.bot.get_messages.return_value = [self.message(100, photo=True), self.message(101, body)]
        (self.directory / "2-100.jpg").write_bytes(b"jpg")
        with patch("announcements.local_media_path", side_effect=lambda name: self.directory / name), \
                patch("announcements.save") as save:
            await index_announcement(self.bot, self.target, {
                "result_ids": "[100,101]", "job_key": "10",
            }, "2:10")
        save.assert_called_once()
        self.assertEqual(save.call_args.args[:3], ("2:10", [100, 101], body))
        self.assertEqual(len(save.call_args.args[3]), 1)
        self.bot.send_file.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_missing_target_messages_not_indexed(self):
        self.bot.get_messages.return_value = [None]
        with patch("announcements.save") as save, self.assertRaises(ValueError):
            await index_announcement(self.bot, self.target, {"result_ids": "[100]", "job_key": "10"}, "2:10")
        save.assert_not_called()

    async def test_download_incomplete_not_published_and_temporary_cleaned(self):
        self.bot.get_messages.return_value = [self.message(100, photo=True)]
        async def download(message, file):
            Path(file).write_bytes(b"x")
        self.bot.download_media.side_effect = download
        with patch("announcements.local_media_path", side_effect=lambda name: self.directory / name), \
                patch("announcements.save") as save, self.assertRaises(ValueError):
            await index_announcement(self.bot, self.target, {"result_ids": "[100]", "job_key": "10"}, "2:10")
        save.assert_not_called()
        self.assertFalse(list(self.directory.glob("*.part")))
        self.assertFalse(list(self.directory.glob("*.jpg")))

    async def test_media_storage_threshold_and_cached_retry(self):
        message = self.message(100, photo=True)
        message.file.size = 200 * 1024 * 1024 + 1
        self.bot.get_messages.return_value = [message]
        job = {"result_ids": "[100]", "job_key": "10"}
        with patch("announcements.local_media_path", side_effect=lambda name: self.directory / name), \
                patch("announcements.save") as save, self.assertRaisesRegex(ValueError, "storage limit"):
            await index_announcement(self.bot, self.target, job, "2:10")
        save.assert_not_called()
        self.bot.download_media.assert_not_awaited()
        message.file.size = 3
        (self.directory / "2-100.jpg").write_bytes(b"jpg")
        with patch("announcements.local_media_path", side_effect=lambda name: self.directory / name), \
                patch("announcements.save") as save:
            await index_announcement(self.bot, self.target, job, "2:10")
        save.assert_called_once()
        self.bot.download_media.assert_not_awaited()

    async def test_failed_announcement_does_not_block_later_jobs(self):
        self.sent(10, 100)
        self.sent(11, 101)
        with patch("announcements.indexed_keys", return_value=set()), \
                patch("channel_sync.index_announcement", new_callable=AsyncMock,
                      side_effect=[ValueError("missing"), None]) as index:
            with self.assertRaises(ValueError):
                await index_announcements(self.bot, self.target, self.store)
            self.assertEqual(index.await_count, 2)
