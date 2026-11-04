from datetime import datetime
import unittest
from unittest.mock import MagicMock, patch

import api_server


class DeliveryArchiveTests(unittest.TestCase):
    def row(self, ident, person_id, age=23, caption="", kind="photo"):
        return {
            "id": ident, "person_id": person_id, "age": age, "caption": caption,
            "media_type": kind, "message_id": ident, "channel_id": "-100123",
            "media_group_id": None, "ocr_text": "", "image_hash": None,
            "created_at": datetime(2026, 11, 4, 20),
        }

    def catalog(self, rows, venue=7, category=None):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = rows
        with patch.object(api_server, "get_db_connection", return_value=conn), \
                patch.object(api_server, "current_shift_window", return_value=(
                    datetime(2026, 11, 4, 18), datetime(2026, 11, 5, 6), "晚班",
                )):
            result = api_server.delivery_archive(venue, category)
        query = cur.execute.call_args.args[0]
        self.assertIn("p.deleted_at IS NULL", query)
        self.assertIn("p.consent_status='granted'", query)
        self.assertNotIn("delivery_person_notes", query)
        self.assertNotIn("delivery_person_attachments", query)
        return result

    def test_public_archive_filters_age_and_returns_only_public_fields(self):
        rows = [self.row(4, 4, None), self.row(3, 3, 17), self.row(2, 2, kind="video"),
                self.row(1, 1)]
        result = self.catalog(rows)
        self.assertEqual([person["id"] for person in result["people"]], [2, 1])
        self.assertFalse(result["people"][0]["on_duty"])
        self.assertTrue(result["people"][1]["on_duty"])
        self.assertEqual(set(result["people"][1]), {"id", "media", "on_duty", "attendance_overlay"})
        self.assertEqual(result["people"][1]["media"], [{"id": 1, "type": "photo"}])

    def test_historical_archive_does_not_require_current_duty(self):
        row = self.row(1, 1)
        row["created_at"] = datetime(2026, 11, 3, 20)
        result = self.catalog([row])
        self.assertEqual(len(result["people"]), 1)
        self.assertFalse(result["people"][0]["on_duty"])

    def test_apple_categories_are_separated(self):
        rows = [self.row(2, 2, caption="红苹果"), self.row(1, 1, caption="小苹果")]
        self.assertEqual([p["id"] for p in self.catalog(rows, 10, "small")["people"]], [1])
        self.assertEqual([p["id"] for p in self.catalog(rows, 10, "red")["people"]], [2])

    def test_invalid_category_or_venue_is_rejected(self):
        for venue, category in [(1, None), (True, None), (7, "small"), (10, "invalid")]:
            with self.subTest(venue=venue, category=category), self.assertRaises(ValueError):
                api_server.delivery_archive(venue, category)

    def test_attendance_details_include_matching_archive_media(self):
        rows = [self.row(4, 12)]
        posts = [{"media": [{"id": 4, "type": "photo"}], "category": None}]
        complete = [{"id": 4, "type": "photo"}, {"id": 2, "type": "video"}]
        with patch.object(api_server, "delivery_archive", return_value={"people": [
                {"id": 12, "media": complete}]}):
            api_server.attach_delivery_archive_media(7, rows, posts)
        self.assertEqual(posts[0]["media"], complete)

    def test_attendance_does_not_mix_people_or_cross_apple_categories(self):
        rows = [self.row(4, 12), self.row(3, 13)]
        posts = [{"media": [{"id": 4, "type": "photo"}, {"id": 3, "type": "video"}]},
                 {"media": [{"id": 4, "type": "photo"}], "category": "small"}]
        with patch.object(api_server, "delivery_archive", return_value={"people": []}) as archive:
            api_server.attach_delivery_archive_media(10, rows, posts)
        archive.assert_called_once_with(10, "small")
        self.assertEqual(len(posts[0]["media"]), 2)
        self.assertEqual(len(posts[1]["media"]), 1)

    def test_tenant_scope_enforced_and_public_response_not_cached(self):
        client = api_server.app.test_client()
        with patch.object(api_server, "load_tenant", return_value={"venue_ids": [7]}), \
                patch.object(api_server, "delivery_archive", return_value={"people": []}) as catalog:
            response = client.post("/webhook", headers={"X-Tenant": "test-shop"}, json={
                "action": "get_delivery_archive", "venue_id": 8,
            })
            self.assertEqual(response.status_code, 403)
            catalog.assert_not_called()
            response = client.post("/webhook", headers={"X-Tenant": "test-shop"}, json={
                "action": "get_delivery_archive", "venue_id": 7,
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
