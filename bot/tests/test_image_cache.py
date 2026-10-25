from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image, UnidentifiedImageError

from image_cache import ImageCache
import api_server


def sample_image():
    output = BytesIO()
    Image.effect_noise((1200, 800), 60).convert("RGB").save(output, "JPEG")
    return output.getvalue()


class ImageCacheTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = ImageCache(self.temporary.name)
        self.original = sample_image()
        self.loader = Mock(return_value=self.original)

    def test_persistent_cache_and_thumbnail_preserve_original(self):
        preview, mime = self.cache.get("file", self.loader, thumbnail=True)
        self.assertEqual(mime, "image/webp")
        with Image.open(BytesIO(preview)) as image:
            self.assertEqual(image.size, (480, 320))
        self.assertLess(len(preview), len(self.original))
        cache = ImageCache(self.temporary.name)
        self.assertEqual(cache.get("file", self.loader)[0], self.original)
        self.assertEqual(cache.get("file", self.loader, thumbnail=True)[0], preview)
        self.assertEqual(self.loader.call_count, 1)

    def test_concurrent_requests_download_once(self):
        def load():
            time.sleep(0.02)
            return self.loader()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.cache.get("file", load), range(8)))
        self.assertEqual(self.loader.call_count, 1)
        self.assertTrue(all(data == self.original for data, _ in results))

    def test_expired_files_are_refetched_and_budget_is_bounded(self):
        self.cache.get("file", self.loader)
        for path in Path(self.temporary.name).glob("*.img"):
            os.utime(path, (0, 0))
        self.cache.get("file", self.loader)
        self.assertEqual(self.loader.call_count, 2)
        self.cache.max_bytes = len(self.original) + 1
        self.cache.get("other", self.loader)
        total = sum(path.stat().st_size for path in Path(self.temporary.name).glob("*.img"))
        self.assertLessEqual(total, self.cache.max_bytes)

    def test_failed_or_invalid_download_is_not_cached(self):
        with self.assertRaises(UnidentifiedImageError):
            self.cache.get("invalid", lambda: b"not an image")
        self.assertEqual(list(Path(self.temporary.name).glob("*.img")), [])
        with self.assertRaises(TimeoutError):
            self.cache.get("failed", Mock(side_effect=TimeoutError))

    def test_file_ids_are_isolated_and_not_used_as_paths(self):
        self.cache.get("../file", self.loader)
        self.cache.get("other", self.loader)
        self.assertEqual(self.loader.call_count, 2)
        self.assertEqual(len(list(Path(self.temporary.name).glob("*.img"))), 2)

    def test_http_etag_and_thumbnail_selection(self):
        client = api_server.app.test_client()
        with patch.object(api_server, "image_cache", self.cache), \
                patch.object(api_server, "movie_thumb_file_id", return_value="file"), \
                patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test"}):
            self.cache.get("file", self.loader)
            response = client.get("/movie-thumb/1?thumb=1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/webp")
            cached = client.get("/movie-thumb/1?thumb=1", headers={
                "If-None-Match": response.headers["ETag"]
            })
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(client.get("/movie-thumb/1").data, self.original)
            with patch.object(api_server, "movie_thumb_file_id", return_value=None):
                self.assertEqual(client.get("/movie-thumb/1").status_code, 404)

    def test_photo_gate_precedes_image_cache(self):
        connection = Mock()
        connection.cursor.return_value.fetchone.return_value = None
        with patch.object(api_server, "get_db_connection", return_value=connection), \
                patch.object(api_server, "_telegram_image") as image:
            response = api_server.app.test_client().get("/photos/123/image?thumb=1")
            self.assertEqual(response.status_code, 404)
            image.assert_not_called()

    def test_delivery_video_keeps_streaming_path(self):
        connection = Mock()
        connection.cursor.return_value.__enter__ = Mock(return_value=Mock(
            fetchone=Mock(return_value={"file_id": "video", "media_type": "video"})
        ))
        connection.cursor.return_value.__exit__ = Mock(return_value=False)
        with patch.object(api_server, "get_db_connection", return_value=connection), \
                patch.object(api_server, "_proxy_telegram_media", return_value="stream") as stream, \
                patch.object(api_server, "_telegram_image") as image:
            response = api_server.app.test_client().get("/delivery-media/1")
            self.assertEqual(response.data, b"stream")
            stream.assert_called_once_with("video")
            image.assert_not_called()


if __name__ == "__main__":
    unittest.main()
