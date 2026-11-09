from datetime import datetime
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image
import api_server
import venue_recommendations as recommendations
from customer_records import RecordConflict


class RecommendationTests(unittest.TestCase):
    def payload(self, **changes):
        return {
            "id": "a" * 32, "request_id": "b" * 32, "venue_id": 2, "revision": 0,
            "kind": "activity", "title": "Test recommendation", "description": "Terms apply",
            "starts_at": "2026-01-01T07:00:00+07:00", "ends_at": "2099-01-02T00:00:00Z",
            "enabled": True, "confirmed": True, **changes,
        }

    def row(self):
        value = self.payload(revision=1)
        value.pop("request_id")
        value.pop("confirmed")
        value.update(starts_at=datetime(2026, 1, 1), ends_at=datetime(2099, 1, 2),
                     updated_at=datetime(2026, 1, 1), media_type="photo", media_width=600, media_height=900)
        return value

    def connection(self):
        conn = MagicMock()
        return conn, conn.cursor.return_value.__enter__.return_value

    def test_only_integer_pilot_venue_is_accepted(self):
        for value in (1, 3, True, "2", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                recommendations._venue({"venue_id": value})
        self.assertEqual(recommendations._venue({"venue_id": 2}), 2)

    def test_time_requires_timezone_and_normalizes_to_utc(self):
        self.assertEqual(recommendations._time("2026-01-01T07:00:00+07:00"), datetime(2026, 1, 1))
        for value in (None, "bad", "2026-01-01T00:00:00", "9999-01-01T00:00:00Z"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                recommendations._time(value)

    def test_public_catalog_filters_window_and_has_no_silent_limit(self):
        conn, cur = self.connection()
        cur.fetchone.return_value = {"total": 25}
        cur.fetchall.return_value = [self.row() for _ in range(25)]
        with patch.object(recommendations, "get_conn", return_value=conn):
            result = recommendations.catalog({"venue_id": 2})
        self.assertEqual(len(result["recommendations"]), 25)
        query, args = cur.execute.call_args.args
        self.assertIn("enabled=1 AND starts_at<=%s AND ends_at>%s", query)
        self.assertNotIn("LIMIT", query)
        self.assertEqual(args[0], 2)
        self.assertEqual(args[1], args[2])
        self.assertTrue(result["recommendations"][0]["starts_at"].endswith("+00:00"))
        self.assertIn("?v=1", result["recommendations"][0]["image_url"])
        conn.close.assert_called_once()

    def test_admin_can_read_inactive_and_scheduled_with_pagination(self):
        conn, cur = self.connection()
        cur.fetchone.return_value = {"total": 25}
        cur.fetchall.return_value = []
        with patch.object(recommendations, "get_conn", return_value=conn):
            recommendations.catalog({"venue_id": 2, "page": 2}, admin=True)
        query, args = cur.execute.call_args.args
        self.assertNotIn("enabled=1", query)
        self.assertIn("LIMIT 20 OFFSET %s", query)
        self.assertEqual(args, [2, 20])

    def test_shared_pages_read_one_catalog_but_cannot_write_other_venue_ids(self):
        for venue_id in recommendations.SHARED_VENUE_IDS:
            conn, cur = self.connection()
            cur.fetchone.return_value = {"total": 0}
            cur.fetchall.return_value = []
            with patch.object(recommendations, "get_conn", return_value=conn):
                recommendations.catalog({"venue_id": venue_id})
            self.assertEqual(cur.execute.call_args.args[1][0], 2)
            if venue_id != 2:
                with self.assertRaises(ValueError):
                    recommendations.catalog({"venue_id": venue_id}, admin=True)
                with self.assertRaises(ValueError):
                    recommendations.save(self.payload(venue_id=venue_id), None, 42)
        for value in (1, 3, 11, True, "4", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                recommendations.catalog({"venue_id": value})

    def test_image_endpoint_uses_same_visibility_window(self):
        for admin in (False, True):
            conn, cur = self.connection()
            cur.fetchone.return_value = None
            with patch.object(recommendations, "get_conn", return_value=conn):
                self.assertIsNone(recommendations.image("a" * 32, admin=admin))
            query, args = cur.execute.call_args.args
            self.assertIn("venue_id=%s", query)
            self.assertEqual("ends_at>%s" in query, not admin)
            self.assertEqual(args[:2], ["a" * 32, 2])

    def test_save_validates_before_database_access(self):
        for changes in ({"confirmed": False}, {"revision": True}, {"kind": "automatic"}, {"kind": []},
                        {"title": " "}, {"description": ""}, {"enabled": 1},
                        {"ends_at": "2025-01-01T00:00:00Z"}):
            with self.subTest(changes=changes), patch.object(recommendations, "get_conn") as connect, self.assertRaises(ValueError):
                recommendations.save(self.payload(**changes), None, 42)
            connect.assert_not_called()

    def test_insert_audits_and_commits_normalized_image(self):
        conn, cur = self.connection()
        cur.fetchone.side_effect = [None, None]
        with patch.object(recommendations, "get_conn", return_value=conn), \
                patch.object(recommendations, "normalize_media", return_value=(b"jpeg", "photo", 600, 900)) as normalize:
            result = recommendations.save(self.payload(), b"raw", 42)
        normalize.assert_called_once_with(b"raw", None)
        self.assertEqual(result, {"ok": True})
        insert = cur.execute.call_args_list[2]
        self.assertIn("INSERT INTO venue_recommendations", insert.args[0])
        self.assertEqual(insert.args[1][-4:], (b"jpeg", "photo", 600, 900))
        self.assertEqual(insert.args[1][4], datetime(2026, 1, 1))
        self.assertIn("venue_recommendation_audit", cur.execute.call_args.args[0])
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_replay_is_idempotent_and_rejects_modified_content_or_actor(self):
        conn, cur = self.connection()
        cur.fetchone.side_effect = [None, None]
        with patch.object(recommendations, "get_conn", return_value=conn), \
                patch.object(recommendations, "normalize_media", return_value=(b"jpeg", "photo", 600, 900)):
            recommendations.save(self.payload(), b"raw", 42)
        digest = cur.execute.call_args.args[1][3]
        for title, actor, success in (("Test recommendation", 42, True), ("Changed", 42, False),
                                      ("Test recommendation", 43, False)):
            replay_conn, replay_cur = self.connection()
            replay_cur.fetchone.side_effect = [self.row(), {"actor_id": 42, "request_hash": digest}]
            with patch.object(recommendations, "get_conn", return_value=replay_conn), \
                    patch.object(recommendations, "normalize_media", return_value=(b"jpeg", "photo", 600, 900)):
                if success:
                    self.assertTrue(recommendations.save(self.payload(title=title), b"raw", actor)["replayed"])
                else:
                    with self.assertRaises(RecordConflict):
                        recommendations.save(self.payload(title=title), b"raw", actor)
            self.assertEqual(replay_cur.execute.call_count, 2)
            replay_conn.commit.assert_not_called()

    def test_stale_revision_and_missing_image_roll_back(self):
        for previous, exception in ((self.row(), RecordConflict), (None, ValueError)):
            conn, cur = self.connection()
            cur.fetchone.side_effect = [previous, None]
            with patch.object(recommendations, "get_conn", return_value=conn), self.assertRaises(exception):
                recommendations.save(self.payload(), None, 42)
            conn.rollback.assert_called_once()
            conn.commit.assert_not_called()
            conn.close.assert_called_once()

    def test_edit_preserves_image_when_not_uploaded_and_supports_unpublish(self):
        conn, cur = self.connection()
        cur.fetchone.side_effect = [self.row(), None]
        with patch.object(recommendations, "get_conn", return_value=conn):
            recommendations.save(self.payload(revision=1, enabled=False), None, 42)
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertFalse(any("SET image=" in query for query in queries))
        self.assertIn("revision=revision+1", queries[2])
        conn.commit.assert_called_once()

    def test_expired_cannot_republish_but_can_unpublish(self):
        for enabled in (True, False):
            conn, cur = self.connection()
            cur.fetchone.side_effect = [self.row(), None]
            with patch.object(recommendations, "get_conn", return_value=conn):
                payload = self.payload(revision=1, ends_at="2026-01-02T00:00:00Z", enabled=enabled)
                if enabled:
                    with self.assertRaisesRegex(ValueError, "已过期"):
                        recommendations.save(payload, None, 42)
                else:
                    self.assertTrue(recommendations.save(payload, None, 42)["ok"])

    def test_images_are_decoded_reencoded_and_reject_invalid_or_oversized_input(self):
        output = io.BytesIO()
        Image.new("RGB", (2000, 1000), "blue").save(output, "PNG")
        raw = recommendations.normalize_image(output.getvalue(), label="推荐")
        with Image.open(io.BytesIO(raw)) as result:
            self.assertEqual(result.format, "JPEG")
            self.assertEqual(result.size, (1600, 800))
        for raw in (b"not an image", b"x" * (recommendations.MAX_IMAGE_BYTES + 1)):
            with self.assertRaisesRegex(ValueError, "推荐"):
                recommendations.normalize_image(raw, label="推荐")


class RecommendationApiTests(unittest.TestCase):
    def setUp(self):
        self.client = api_server.app.test_client()

    def test_public_list_and_images_are_private_uncached_and_expired_returns_404(self):
        with patch.object(recommendations, "catalog", return_value={"recommendations": []}):
            response = self.client.post("/webhook", json={"action": "get_venue_recommendations", "venue_id": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        with patch.object(recommendations, "media", return_value=None):
            response = self.client.get("/venue-recommendation-images/" + "a" * 32)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_tenant_scope_for_catalog_and_image(self):
        for venues, expected in (([1], 403), ([2], 200)):
            with patch.object(api_server, "load_tenant", return_value={"venue_ids": venues}), \
                    patch.object(recommendations, "catalog", return_value={"recommendations": []}), \
                    patch.object(recommendations, "media", return_value={"image": b"jpeg", "media_type": "photo"}):
                headers = {"X-Tenant": "test-shop"}
                response = self.client.post("/webhook", headers=headers, json={"action": "get_venue_recommendations", "venue_id": 2})
                self.assertEqual(response.status_code, expected)
                self.assertEqual(self.client.get("/venue-recommendation-images/" + "a" * 32, headers=headers).status_code, expected)

    def test_shared_tenant_scope_preserves_catalog_venue_access(self):
        for venue_id in recommendations.SHARED_VENUE_IDS:
            with patch.object(api_server, "load_tenant", return_value={"venue_ids": [venue_id]}), \
                    patch.object(recommendations, "catalog", return_value={"recommendations": []}), \
                    patch.object(recommendations, "media", return_value={"image": b"jpeg", "media_type": "photo"}):
                headers = {"X-Tenant": "test-shop"}
                response = self.client.post("/webhook", headers=headers, json={
                    "action": "get_venue_recommendations", "venue_id": venue_id})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.client.get("/venue-recommendation-images/" + "a" * 32, headers=headers).status_code, 200)
                if venue_id != 2:
                    response = self.client.post("/webhook", headers=headers, json={
                        "action": "get_venue_recommendations", "venue_id": 2})
                    self.assertEqual(response.status_code, 403)

    def test_upload_requires_admin_and_tenant_upload_is_rejected(self):
        with patch("admin_console.authenticate_admin_request", return_value=None), \
                patch.object(recommendations, "save") as save:
            response = self.client.post("/admin-venue-recommendation-save", data={"payload": "{}"})
            self.assertEqual(response.status_code, 401)
            response = self.client.post("/admin-venue-recommendation-save", headers={"X-Tenant": "test"}, data={"payload": "{}"})
            self.assertEqual(response.status_code, 403)
            save.assert_not_called()

    def test_authorized_multipart_save_and_conflict_status(self):
        with patch("admin_console.authenticate_admin_request", return_value={"id": 42}), \
                patch("admin_console.allowed_admin", return_value=True), \
                patch("admin_console.is_tenant_session", return_value=False), \
                patch.object(recommendations, "save", return_value={"ok": True}) as save:
            response = self.client.post("/admin-venue-recommendation-save", data={
                "payload": '{"init_data":"test"}', "image": (io.BytesIO(b"raw"), "test.png")})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(save.call_args.args[1:], (b"raw", 42, "test.png"))
            save.side_effect = RecordConflict("stale")
            response = self.client.post("/admin-venue-recommendation-save", data={"payload": '{"init_data":"test"}'})
            self.assertEqual(response.status_code, 409)

    def test_upload_size_limit_and_database_failure_are_explicit(self):
        response = self.client.post("/admin-venue-recommendation-save", data=b"x" * (21 * 1024 * 1024 + 1),
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 413)
        self.assertIn("5MB", response.json["error"])
        with patch.object(recommendations, "catalog", side_effect=api_server.pymysql.OperationalError("offline")):
            response = self.client.post("/webhook", json={"action": "get_venue_recommendations", "venue_id": 2})
        self.assertEqual(response.status_code, 503)


class RecommendationVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.mp4"
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "color=c=green:s=180x320:r=10:d=1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, timeout=30)
            cls.video = path.read_bytes()

    def test_video_is_converted_to_mobile_mp4_and_has_dimensions_and_fast_start(self):
        from announcement_video import fast_start
        raw, kind, width, height = recommendations.normalize_media(self.video, "portrait.mp4")
        self.assertEqual((kind, width, height), ("video", 180, 320))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.mp4"
            path.write_bytes(raw)
            self.assertTrue(fast_start(path))

    def test_photo_dimensions_are_normalized_without_cropping(self):
        output = io.BytesIO()
        Image.new("RGB", (600, 900), "blue").save(output, "PNG")
        raw, kind, width, height = recommendations.normalize_media(output.getvalue(), "portrait.png")
        self.assertEqual((kind, width, height), ("photo", 600, 900))
        self.assertTrue(raw.startswith(b"\xff\xd8"))

    def test_fake_corrupt_oversized_and_timed_out_video_errors_are_explicit(self):
        for raw in (b"fake", b"\x00\x00\x00\x18ftyp" + b"bad", b"x" * (recommendations.MAX_MEDIA_BYTES + 1)):
            with self.assertRaises(ValueError):
                recommendations.normalize_media(raw, "test.mp4")
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 90)), \
                self.assertRaisesRegex(ValueError, "超时"):
            recommendations.normalize_media(self.video, "test.mp4")

    def test_video_download_supports_ranges_and_admin_blob_preview(self):
        client = api_server.app.test_client()
        row = {"image": self.video, "media_type": "video"}
        with patch.object(recommendations, "media", return_value=row):
            response = client.get("/venue-recommendation-images/" + "a" * 32, headers={"Range": "bytes=0-63"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.mimetype, "video/mp4")
            self.assertEqual(response.data, self.video[:64])
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
        with patch("admin_console.authenticate_admin_request", return_value={"id": 42}), \
                patch("admin_console.allowed_admin", return_value=True), \
                patch("admin_console.is_tenant_session", return_value=False), \
                patch.object(recommendations, "media", return_value=row):
            response = client.post("/webhook", json={"action": "admin_venue_recommendation_image",
                                                     "init_data": "test", "id": "a" * 32})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "video/mp4")
            self.assertEqual(response.data, self.video)

    def test_upload_and_photo_to_video_replace_store_media_metadata(self):
        tests = RecommendationTests()
        for previous, revision in ((None, 0), (tests.row(), 1)):
            conn, cur = tests.connection()
            cur.fetchone.side_effect = [previous, None]
            with patch.object(recommendations, "get_conn", return_value=conn), \
                    patch.object(recommendations, "normalize_media", return_value=(b"video", "video", 180, 320)):
                recommendations.save(tests.payload(revision=revision), self.video, 42, "test.mp4")
            call = cur.execute.call_args_list[3 if previous else 2]
            if previous:
                self.assertEqual(call.args[1], (b"video", "video", 180, 320, "a" * 32))
            else:
                self.assertEqual(call.args[1][-4:], (b"video", "video", 180, 320))
            conn.commit.assert_called_once()
