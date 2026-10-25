from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

import api_server
from video_cache import VideoCache


VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 8


class VideoCacheTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = VideoCache(self.temporary.name)

    def store(self, file_id="file"):
        return self.cache.store(file_id, [VIDEO[:100], VIDEO[100:]], ".mp4", len(VIDEO))

    def connection(self, rows):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = rows[0] if rows else None
        cursor.fetchall.return_value = rows
        return connection

    def test_persistent_complete_file_and_safe_filename(self):
        path = self.store("../file")
        self.assertEqual(path.parent, Path(self.temporary.name))
        self.assertEqual(path.read_bytes(), VIDEO)
        self.assertEqual(VideoCache(self.temporary.name).get("../file"), path)
        self.assertIsNone(self.cache.get("different"))

    def test_partial_oversize_invalid_download_never_becomes_visible(self):
        for chunks, size in [([VIDEO[:100]], len(VIDEO)), ([VIDEO], 100), ([b"bad"], 3)]:
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    self.cache.store("file", chunks, ".mp4", size)
                self.assertIsNone(self.cache.get("file"))
                self.assertEqual(list(Path(self.temporary.name).iterdir()), [])
        self.cache.max_file_bytes = 10
        with self.assertRaises(ValueError):
            self.store()

    def test_interrupted_download_cleans_temporary_file(self):
        def chunks():
            yield VIDEO[:100]
            raise OSError("download interrupted")
        with self.assertRaises(OSError):
            self.cache.store("file", chunks(), ".mp4", len(VIDEO))
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    def test_concurrent_writes_publish_one_file(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            paths = list(pool.map(lambda _: self.store(), range(6)))
        self.assertTrue(all(path == paths[0] for path in paths))
        self.assertEqual(len(list(Path(self.temporary.name).iterdir())), 1)

    def test_disk_budget_evicts_old_video(self):
        self.cache.max_bytes = len(VIDEO)
        self.store("old")
        self.store("new")
        self.assertIsNone(self.cache.get("old"))
        self.assertEqual(self.cache.get("new").read_bytes(), VIDEO)
        self.assertEqual(sum(p.stat().st_size for p in Path(self.temporary.name).iterdir()), len(VIDEO))

    def test_webm_and_unsupported_container(self):
        webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 100
        path = self.cache.store("webm", [webm], ".webm", len(webm))
        self.assertEqual(self.cache.get("webm"), path)
        with self.assertRaises(ValueError):
            self.cache.store("other", [VIDEO], ".exe", len(VIDEO))

    def test_hit_range_seek_head_etag_and_unsatisfiable_range(self):
        self.store()
        with patch.object(api_server, "video_cache", self.cache), \
                patch.object(api_server, "get_db_connection", return_value=self.connection([{"file_id": "file"}])), \
                patch.object(api_server, "_proxy_telegram_media") as proxy:
            client = api_server.app.test_client()
            for header, expected in [
                ("bytes=0-1023", VIDEO[:1024]),
                ("bytes=1024-1535", VIDEO[1024:1536]),
                ("bytes=-32", VIDEO[-32:]),
            ]:
                with client.get("/ktv-videos/1", headers={"Range": header}) as response:
                    self.assertEqual(response.status_code, 206)
                    self.assertEqual(response.data, expected)
                    self.assertEqual(response.mimetype, "video/mp4")
                    self.assertEqual(response.headers["X-Media-Cache"], "HIT")
            with client.get("/ktv-videos/1") as response:
                self.assertEqual(response.data, VIDEO)
                etag = response.headers["ETag"]
                self.assertEqual(response.headers["Cache-Control"], "private, max-age=300")
            with client.head("/ktv-videos/1") as response:
                self.assertEqual(response.data, b"")
                self.assertEqual(int(response.headers["Content-Length"]), len(VIDEO))
            with client.get("/ktv-videos/1", headers={"If-None-Match": etag}) as response:
                self.assertEqual(response.status_code, 304)
            with client.get("/ktv-videos/1", headers={"Range": "bytes=99999-"}) as response:
                self.assertEqual(response.status_code, 416)
            proxy.assert_not_called()

    def test_missing_cache_retains_stream_and_missing_row_is_not_served(self):
        with patch.object(api_server, "video_cache", self.cache), \
                patch.object(api_server, "get_db_connection", return_value=self.connection([{"file_id": "new"}])), \
                patch.object(api_server, "_proxy_telegram_media", return_value="stream") as proxy:
            with api_server.app.test_client().get("/ktv-videos/1") as response:
                self.assertEqual(response.data, b"stream")
                self.assertEqual(response.headers["X-Media-Cache"], "MISS")
            proxy.assert_called_once_with("new")
        self.store()
        with patch.object(api_server, "get_db_connection", return_value=self.connection([])), \
                patch.object(api_server, "video_cache") as cache:
            with api_server.app.test_client().get("/ktv-videos/1") as response:
                self.assertEqual(response.status_code, 404)
            cache.get.assert_not_called()

    def test_warmup_reuses_existing_files(self):
        self.store()
        with patch.object(api_server, "video_cache", self.cache), \
                patch.object(api_server, "get_db_connection", return_value=self.connection([{"id": 1, "file_id": "file"}])), \
                patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test"}), \
                patch.object(api_server.requests, "get") as download:
            api_server.warm_ktv_video_cache()
            download.assert_not_called()

    def test_warmup_downloads_missing_video(self):
        info = MagicMock()
        info.__enter__.return_value.json.return_value = {
            "ok": True, "result": {"file_path": "videos/test.mp4", "file_size": len(VIDEO)}
        }
        upstream = MagicMock()
        upstream.__enter__.return_value.iter_content.return_value = [VIDEO]
        with patch.object(api_server, "video_cache", self.cache), \
                patch.object(api_server, "get_db_connection", return_value=self.connection([{"id": 1, "file_id": "new"}])), \
                patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test"}), \
                patch.object(api_server.requests, "get", side_effect=[info, upstream]):
            api_server.warm_ktv_video_cache()
        self.assertEqual(self.cache.get("new").read_bytes(), VIDEO)

    def test_proxy_releases_upstream_when_client_stops(self):
        info = Mock()
        info.json.return_value = {"ok": True, "result": {"file_path": "video.mp4"}}
        upstream = Mock(status_code=206, headers={"Content-Range": "bytes 0-1023/2000"})
        upstream.iter_content.return_value = iter([b"a" * 1024, b"b" * 1024])
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test"}), \
                patch.object(api_server.requests, "get", side_effect=[info, upstream]), \
                api_server.app.test_request_context("/ktv-videos/1"):
            response = api_server._proxy_telegram_media("file")
            next(iter(response.response))
            response.close()
            upstream.close.assert_called()


if __name__ == "__main__":
    unittest.main()
