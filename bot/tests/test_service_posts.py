from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

import pymysql
from telethon import errors, types

import api_server
from db import get_conn
import service_posts as posts
from service_ingest import ServiceStore, collect, download_post_media, scan


class ServiceContactTests(unittest.TestCase):
    def test_listing_fields_are_explicit_complete_and_contact_free(self):
        body = ("✅位置：#星汇\n✅户型：特惠单间\n✅租金：\n"
                "视频1️⃣3-13楼260$\n联系人：测试人 @example_contact\n"
                "面积：100平方米\n面积：100平方米\n租金：260$\n租金：280$")
        attributes = posts.listing_attributes(posts.clean_body(body))
        self.assertEqual(attributes, [
            {"label": "位置", "value": "#星汇"}, {"label": "户型", "value": "特惠单间"},
            {"label": "面积", "value": "100平方米"},
            {"label": "租金", "value": "260$"}, {"label": "租金", "value": "280$"},
        ])
        self.assertEqual(posts.listing_attributes("车型未知，面积未记录"), [])

    def test_contact_fields_and_channel_promotion_are_removed_without_losing_listing(self):
        body = ("地址：TL社区\n租金：800$\n户型：4房排屋\n家具齐全，拎包入住\n"
                "联系人：测试联系人 @example_contact\n电话：+855 12 345 678\n"
                "西港房源 🏠 西港公寓🏚️\n西港租车 🚗\n"
                "✈️看房联系：@example_contact\n"
                "【西港   别墅   🏡频道】👈关注\n"
                "【西港   公寓   🏨频道】👈关注\n"
                "【西港   租车   🚗频道】👈关注")
        self.assertEqual(posts.clean_body(body),
                         "地址：TL社区\n租金：800$\n户型：4房排屋\n家具齐全，拎包入住")

    def test_standalone_contacts_and_links_are_removed_but_prices_dates_and_floors_stay(self):
        body = ("租金800$，售价20000000 USD\n面积100平方米，视频3-13楼\n"
                "更新日期2026-10-01\nfirst.last@example.test\n+855 (12) 345-678\n"
                "https://example.test/contact\nhttps://maps.app.goo.gl/example\n@some_contact")
        self.assertEqual(posts.clean_body(body),
                         "租金800$，售价20000000 USD\n面积100平方米，视频3-13楼\n更新日期2026-10-01")

    def test_hidden_contact_entities_use_original_utf16_offsets(self):
        body = "🏠租金800$\n点我联系\nfirst.last@example.test\n面积100平方米"
        email = "first.last@example.test"
        entities = [types.MessageEntityTextUrl(
            offset=len(body[:body.index(value)].encode("utf-16-le")) // 2,
            length=len(value.encode("utf-16-le")) // 2, url="https://t.me/example_contact")
            for value in ("点我联系", email)]
        self.assertEqual(posts.clean_body(body, entities), "🏠租金800$\n\n面积100平方米")

    def test_property_features_and_vehicle_names_are_not_contact_fields(self):
        self.assertEqual(posts.clean_body("带电话门禁\n车型：奇瑞QQ\n年款：2026年"),
                         "带电话门禁\n车型：奇瑞QQ\n年款：2026年")


class ServicePostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = get_conn()
        database = "test_services_" + uuid.uuid4().hex[:16]
        with root.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")

        def cleanup():
            with root.cursor() as cur:
                cur.execute(f"DROP DATABASE `{database}`")
            root.close()

        cls.addClassCleanup(cleanup)

        def connect():
            return pymysql.connect(
                host=root.host, port=root.port, user=root.user, password=root.password,
                database=database, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)

        patcher = patch.object(posts, "get_conn", side_effect=connect)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        posts.ensure_tables()

    def setUp(self):
        conn = posts.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM service_posts")
            conn.commit()
        finally:
            conn.close()

    def test_legacy_cleanup_is_persistent_and_preserves_media_and_disabled_status(self):
        conn = posts.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO service_posts
                    (category,job_key,message_ids,body,media,published_at,enabled) VALUES
                    ('villas','old','[1]',%s,'[{"kind":"photo","filename":"2-1.jpg"}]',%s,1),
                    ('apartments','old','[2]',%s,'[]',%s,1),
                    ('rentals','old','[3]',%s,'[]',%s,0)""",
                            ("租金800$\n联系人：测试人 @example_contact", posts.START_AT.replace(tzinfo=None),
                             "电话：0123456789", posts.START_AT.replace(tzinfo=None),
                             "车型：SUV\n西港租车🚗", posts.START_AT.replace(tzinfo=None)))
            conn.commit()
            self.assertEqual(posts.clean_existing(), 3)
            self.assertEqual(posts.clean_existing(), 0)
            self.assertEqual(posts.catalog("apartments")["total"], 0)
            self.assertEqual(posts.catalog("rentals")["total"], 0)
            item = posts.catalog("villas")["items"][0]
            detail = posts.detail("villas", item["id"])
            self.assertEqual(detail["body"], "租金800$")
            self.assertEqual(detail["media_count"], 1)
            with conn.cursor() as cur:
                cur.execute("SELECT body FROM service_posts WHERE id=%s", (item["id"],))
                self.assertEqual(cur.fetchone()["body"], "租金800$")
        finally:
            conn.close()

    def test_historical_posts_stay_visible_idempotently_and_categories_are_isolated(self):
        for service in posts.SOURCES:
            posts.save(service, "source:10", [10], "真实介绍\n\n完整正文 @contact",
                       [{"kind": "photo", "filename": "2-10.jpg"}], posts.START_AT)
            posts.save(service, "source:10", [10], "重复请求", [], posts.START_AT)
            result = posts.catalog(service)
            self.assertEqual(result["total"], 1)
            item = result["items"][0]
            detail = posts.detail(service, item["id"])
            self.assertEqual(detail["body"], "真实介绍\n\n完整正文")
            self.assertEqual(detail["media"][0]["url"], f"/service-media/{item['id']}/0")
            self.assertNotIn("filename", str(detail))
            other = next(key for key in posts.SOURCES if key != service)
            self.assertIsNone(posts.detail(other, item["id"]))
        with self.assertRaises(ValueError):
            posts.save("villas", "old", [1], "旧消息", [], posts.START_AT - timedelta(seconds=1))

    def test_inputs_and_tenant_access_are_checked_before_storage(self):
        for service in (None, True, "news", "villas OR 1=1"):
            with self.assertRaises(ValueError):
                posts.catalog(service)
        for page in (True, 0, "1", 100001):
            with self.assertRaises(ValueError):
                posts.catalog("villas", page)
        client = api_server.app.test_client()
        with patch.object(api_server, "load_tenant", return_value={"venue_ids": [], "announcements_enabled": False}):
            for action in ("get_service_posts", "get_service_post"):
                self.assertEqual(client.post("/webhook", json={"action": action, "category": "bad"}).status_code, 400)
                self.assertEqual(client.post("/webhook?tenant=example", json={"action": action}).status_code, 403)
        self.assertEqual(client.get("/service-media/1/0?tenant=example").status_code, 403)
        with patch.object(posts, "catalog", side_effect=pymysql.OperationalError("offline")), \
                self.assertLogs("api_server", level="ERROR"):
            response = client.post("/webhook", json={"action": "get_service_posts", "category": "villas"})
            self.assertEqual(response.status_code, 503)

    def test_service_video_range_and_missing_media(self):
        client = api_server.app.test_client()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "2-10.mp4"
            path.write_bytes(b"0123456789")
            with patch.object(posts, "media_file", return_value={"kind": "video", "filename": path.name}), \
                    patch.object(posts, "local_media_path", return_value=path):
                response = client.get("/service-media/1/0", headers={"Range": "bytes=2-5"})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.data, b"2345")
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                response.close()
                path.unlink()
                self.assertEqual(client.get("/service-media/1/0").status_code, 503)
        with patch.object(posts, "media_file", return_value=None):
            self.assertEqual(client.get("/service-media/1/0").status_code, 404)


class ServiceIngestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = ServiceStore(self.directory / "jobs.sqlite3")
        self.addCleanup(self.store.close)
        self.channel = SimpleNamespace(id=2, noforwards=False)
        self.store.initialize(2, 200)

    def message(self, ident, date=None, group=None):
        return SimpleNamespace(id=ident, date=date or posts.START_AT, grouped_id=group,
                               noforwards=False, message="完整介绍 @contact")

    async def immediate(self, operation):
        return await operation()

    async def test_history_cutoff_is_inclusive_and_restart_keeps_progress(self):
        messages = [self.message(9, posts.START_AT - timedelta(seconds=1)), self.message(10), self.message(11)]
        async def iterate(*args, **kwargs):
            self.options = kwargs
            for message in messages:
                yield message
        reader = SimpleNamespace(iter_messages=iterate)
        with patch.object(self.store, "request", side_effect=self.immediate):
            await scan(reader, self.channel, "villas", self.store, historical=True)
        job = self.store.pending(2, "villas")
        self.assertEqual(job["message_ids"], "[10]")
        self.assertEqual(self.options["offset_date"], posts.START_AT - timedelta(seconds=1))
        self.assertEqual(self.store.cursor(2)["history"], 11)
        reopened = ServiceStore(self.directory / "jobs.sqlite3")
        try:
            self.assertEqual(reopened.cursor(2)["history"], 11)
            self.assertEqual(reopened.cursor(2)["complete"], 1)
        finally:
            reopened.close()

    async def test_full_batch_defers_last_group_instead_of_truncating_album(self):
        async def iterate(*args, **kwargs):
            for ident in range(1, 51):
                yield self.message(ident, group=500 if ident == 50 else None)
        reader = SimpleNamespace(iter_messages=iterate)
        with patch.object(self.store, "request", side_effect=self.immediate):
            await scan(reader, self.channel, "villas", self.store, historical=True)
        self.assertEqual(self.store.cursor(2)["history"], 49)
        self.assertIsNone(self.store.connection.execute("SELECT * FROM jobs WHERE job_key='50'").fetchone())

    async def test_whole_album_and_original_text_saved_once_without_telegram_posts(self):
        messages = [self.message(10, group=123), self.message(11, group=123)]
        self.store.enqueue(2, "villas", [10, 11], posts.START_AT.timestamp())
        job = self.store.pending(2, "villas")
        reader = SimpleNamespace(get_messages=AsyncMock(return_value=messages),
                                 get_entity=AsyncMock(return_value=self.channel))
        with patch.object(self.store, "request", side_effect=self.immediate), \
                patch("service_ingest.complete_album", AsyncMock(return_value=messages)), \
                patch("service_ingest.download_post_media", AsyncMock(return_value=[
                    {"kind": "photo", "filename": "2-10.jpg"}, {"kind": "video", "filename": "2-11.mp4"}])), \
                patch.object(posts, "save") as save:
            await collect(reader, self.channel, "villas", self.store, job)
        self.assertEqual(save.call_args.args[2], [10, 11])
        self.assertEqual(save.call_args.args[3], "完整介绍\n\n完整介绍")
        self.assertIsNone(self.store.pending(2, "villas"))

    async def test_protected_or_missing_album_never_downloads_or_publishes(self):
        self.store.enqueue(2, "villas", [10, 11], posts.START_AT.timestamp())
        job = self.store.pending(2, "villas")
        for messages in ([self.message(10), None], [self.message(10), self.message(11)]):
            reader = SimpleNamespace(get_messages=AsyncMock(return_value=messages),
                                     get_entity=AsyncMock(return_value=self.channel))
            self.channel.noforwards = messages[1] is not None
            with patch.object(self.store, "request", side_effect=self.immediate), \
                    patch("service_ingest.download_post_media") as download, \
                    patch.object(posts, "save") as save:
                with self.assertRaises(ValueError):
                    await collect(reader, self.channel, "villas", self.store, job)
                download.assert_not_called()
                save.assert_not_called()

    async def test_flood_wait_deadline_survives_restart(self):
        operation = AsyncMock(side_effect=errors.FloodWaitError(request=None, capture=60))
        with self.assertRaises(errors.FloodWaitError):
            await self.store.request(operation)
        reopened = ServiceStore(self.directory / "jobs.sqlite3")
        try:
            import time
            self.assertGreater(reopened.deadline(), time.time() + 59)
        finally:
            reopened.close()

    async def test_new_messages_take_priority_over_historical_queue(self):
        self.store.enqueue(2, "villas", [10], posts.START_AT.timestamp(), is_backfill=True)
        async def iterate(*args, **kwargs):
            self.assertEqual(kwargs["min_id"], 200)
            yield self.message(201)
        with patch.object(self.store, "request", side_effect=self.immediate):
            await scan(SimpleNamespace(iter_messages=iterate), self.channel, "villas", self.store, historical=False)
        self.assertEqual(self.store.pending(2, "villas")["message_ids"], "[201]")
        self.assertEqual(self.store.cursor(2)["live"], 201)

    async def test_each_album_file_download_uses_persistent_pacing(self):
        messages = [SimpleNamespace(id=ident, photo=True, video=None, gif=None,
                                    file=SimpleNamespace(size=4, mime_type="image/jpeg"))
                    for ident in (10, 11)]
        async def download(message, *, file):
            Path(file).write_bytes(b"data")
        request = AsyncMock(side_effect=self.immediate)
        media = await download_post_media(SimpleNamespace(download_media=download), messages, 2,
                                          lambda name: self.directory / name, request=request)
        self.assertEqual(request.await_count, 2)
        self.assertEqual([item["filename"] for item in media], ["2-10.jpg", "2-11.jpg"])

    async def test_contact_only_post_is_skipped_instead_of_creating_empty_card(self):
        message = self.message(10)
        message.message = "联系人：测试人 @example_contact\n电话：0123456789"
        self.store.enqueue(2, "villas", [10], posts.START_AT.timestamp())
        job = self.store.pending(2, "villas")
        reader = SimpleNamespace(get_messages=AsyncMock(return_value=[message]),
                                 get_entity=AsyncMock(return_value=self.channel))
        with patch.object(self.store, "request", side_effect=self.immediate), \
                patch("service_ingest.download_post_media", AsyncMock(return_value=[])), \
                patch.object(posts, "save") as save, \
                self.assertLogs("service_ingest", level="WARNING"):
            await collect(reader, self.channel, "villas", self.store, job)
        save.assert_not_called()
        self.assertEqual(self.store.connection.execute("SELECT status FROM jobs").fetchone()[0], "skipped")
