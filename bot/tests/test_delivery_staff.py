import unittest
import io
from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image

import delivery_staff


class DeliveryStaffTest(unittest.TestCase):
    def test_attendance_uses_all_channel_photos_before_dedup_and_shift_boundaries(self):
        cases = [
            (datetime(2026, 11, 4, 18), datetime(2026, 11, 4, 18), "photo", True, "今晚出勤"),
            (datetime(2026, 11, 5, 1), datetime(2026, 11, 4, 23), "photo", True, "今晚出勤"),
            (datetime(2026, 11, 5, 5, 59), datetime(2026, 11, 5, 5, 59), "photo", True, "今晚出勤"),
            (datetime(2026, 11, 4, 20), datetime(2026, 11, 4, 17, 59), "photo", False, "本班未见照片"),
            (datetime(2026, 11, 4, 20), datetime(2026, 11, 5, 6), "photo", False, "本班未见照片"),
            (datetime(2026, 11, 5, 6), datetime(2026, 11, 5, 5, 59), "photo", False, "外送仅晚班"),
            (datetime(2026, 11, 5, 18), datetime(2026, 11, 4, 23), "photo", False, "本班未见照片"),
            (datetime(2026, 11, 4, 20), datetime(2026, 11, 4, 19), "video", False, "本班未见照片"),
        ]
        from duty_shift import current_shift_window
        for now, timestamp, kind, expected, label in cases:
            conn = MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            person = {"id": 7, "merged_into_id": None}
            old = {"id": 1, "person_id": 7, "media_type": "photo",
                   "created_at": datetime(2026, 11, 3, 19), "image_hash": "0000000000000000"}
            newer = {"id": 2, "person_id": 7, "media_type": kind,
                     "created_at": timestamp, "image_hash": "0000000000000001" if kind == "photo" else None}
            cur.fetchall.side_effect = [
                [person], [old, newer], [],
                [{"id": 99, "person_id": 7, "media_type": "photo", "created_at": now}],
            ]
            with self.subTest(now=now, timestamp=timestamp, kind=kind), \
                    patch.object(delivery_staff, "get_conn", return_value=conn), \
                    patch.object(delivery_staff, "current_shift_window", return_value=current_shift_window(now)):
                result = delivery_staff.admin_listing(7)
                self.assertEqual(result["people"][0]["on_duty"], expected)
                self.assertEqual(result["people"][0]["attendance_overlay"], label)
                self.assertEqual(result["on_duty_count"], int(expected))
                self.assertEqual(result["shift_date"], current_shift_window(now)[0].date().isoformat())
                self.assertIn("p.deleted_at IS NULL", cur.execute.call_args_list[0].args[0])
                self.assertEqual(cur.execute.call_args_list[0].args[1], (7,))

    def test_submit_description_and_media_commit_together(self):
        for existing in (None, {"id": 23}):
            conn = MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchone.side_effect = [{"merged_into_id": None}, existing]
            with patch.object(delivery_staff, "get_conn", return_value=conn):
                delivery_staff.add_attachment(7, self.photo_bytes(), "x.png", 123, description=" updated ")
            writes = [call.args for call in cur.execute.call_args_list
                      if call.args[0].startswith("INSERT INTO delivery_person_notes")]
            self.assertEqual(writes[0][1], (7, "updated"))
            conn.commit.assert_called_once()
            conn.rollback.assert_not_called()

    def test_submit_media_failure_rolls_back_description(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"merged_into_id": None}, None]

        def execute(sql, params):
            if sql.startswith("INSERT INTO delivery_person_attachments"):
                raise RuntimeError("storage failure")

        cur.execute.side_effect = execute
        with patch.object(delivery_staff, "get_conn", return_value=conn), self.assertRaises(RuntimeError):
            delivery_staff.add_attachment(7, self.photo_bytes(), "x.png", 123, description="test")
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    def test_delete_archive_preserves_channel_media_and_audits(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"merged_into_id": None, "deleted_at": None}
        with patch.object(delivery_staff, "get_conn", return_value=conn):
            result = delivery_staff.delete_archive_content(7, None, True, 123)
        self.assertTrue(result["deleted"])
        statements = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any("UPDATE delivery_people SET deleted_at" in sql for sql in statements))
        self.assertFalse(any("DELETE FROM delivery_media " in sql for sql in statements))
        self.assertFalse(any("DELETE FROM delivery_people " in sql for sql in statements))
        self.assertTrue(any("DELETE FROM delivery_person_notes" in sql for sql in statements))
        self.assertTrue(any("DELETE FROM delivery_person_attachments" in sql for sql in statements))
        conn.commit.assert_called_once()

    def test_delete_attachment_checks_profile_ownership(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"merged_into_id": None, "deleted_at": None}, {"id": 9}]
        with patch.object(delivery_staff, "get_conn", return_value=conn):
            delivery_staff.delete_archive_content(7, 9, True, 123)
        delete = [call.args for call in cur.execute.call_args_list if call.args[0].startswith("DELETE")]
        self.assertEqual(len(delete), 1)
        self.assertEqual(delete[0][1], (9, 7))
        cur.fetchone.side_effect = [{"merged_into_id": None, "deleted_at": None}, None]
        with patch.object(delivery_staff, "get_conn", return_value=conn), self.assertRaises(ValueError):
            delivery_staff.delete_archive_content(7, 10, True, 123)
        conn.rollback.assert_called_once()

    def test_delete_requires_confirmation_and_valid_ids(self):
        for person, attachment, confirmed in [(7, None, False), (7, None, "true"), (True, None, True), (7, 0, True)]:
            with self.subTest(person=person, attachment=attachment), self.assertRaises(ValueError):
                delivery_staff.delete_archive_content(person, attachment, confirmed, 123)

    def test_deleted_profile_cannot_be_modified_and_delete_can_be_retried(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"merged_into_id": None, "deleted_at": "2026-11-04"}
        with patch.object(delivery_staff, "get_conn", return_value=conn):
            self.assertTrue(delivery_staff.delete_archive_content(7, None, True, 123)["deleted"])
            with self.assertRaises(ValueError):
                delivery_staff.delete_archive_content(7, 9, True, 123)
        self.assertFalse(any(call.args[0].startswith("DELETE") for call in cur.execute.call_args_list))

    def photo_bytes(self):
        output = io.BytesIO()
        Image.new("RGB", (20, 20), "blue").save(output, "PNG")
        return output.getvalue()

    def test_attachment_validates_content_and_normalizes_photo(self):
        kind, mime, content = delivery_staff._validate_attachment(self.photo_bytes(), "test.png")
        self.assertEqual((kind, mime), ("photo", "image/jpeg"))
        with Image.open(io.BytesIO(content)) as image:
            self.assertEqual(image.format, "JPEG")

    def test_attachment_rejects_empty_oversize_unsupported_and_fake_photo(self):
        for raw, name in [
            (b"", "x.png"), (b"x" * (20 * 1024 * 1024 + 1), "x.mp4"),
            (b"x" * (5 * 1024 * 1024 + 1), "x.jpg"),
            (b"<script>bad</script>", "x.jpg"), (b"bad", "x.svg"),
        ]:
            with self.subTest(name=name, length=len(raw)), self.assertRaises(ValueError):
                delivery_staff._validate_attachment(raw, name)

    def test_attachment_rejects_unreadable_video(self):
        with patch("cv2.VideoCapture") as capture:
            capture.return_value.read.return_value = (False, None)
            with self.assertRaises(ValueError):
                delivery_staff._validate_attachment(b"\x00\x00\x00\x18ftypisom", "x.mp4")
            capture.return_value.release.assert_called_once()

    def test_attachment_is_private_idempotent_and_audited(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"merged_into_id": None}, None]
        cur.lastrowid = 23
        with patch.object(delivery_staff, "get_conn", return_value=conn):
            result = delivery_staff.add_attachment(7, self.photo_bytes(), "x.png", 123)
        self.assertEqual(result["attachment_id"], 23)
        self.assertFalse(result["duplicate"])
        sql = " ".join(call.args[0] for call in cur.execute.call_args_list)
        self.assertNotIn("INSERT INTO delivery_media", sql)
        self.assertNotIn("INSERT INTO delivery_people", sql)
        self.assertIn("attachment_added", sql)
        conn.commit.assert_called_once()
        cur.reset_mock()
        cur.fetchone.side_effect = [{"merged_into_id": None}, {"id": 23}]
        with patch.object(delivery_staff, "get_conn", return_value=conn):
            result = delivery_staff.add_attachment(7, self.photo_bytes(), "x.png", 123)
        self.assertTrue(result["duplicate"])
        self.assertFalse(any("INSERT" in call.args[0] for call in cur.execute.call_args_list))

    def test_description_saves_and_rejects_missing_or_merged_profiles(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"merged_into_id": None}
        with patch.object(delivery_staff, "get_conn", return_value=conn):
            result = delivery_staff.save_description(7, " test\nnote ", 123)
        self.assertEqual(result["description"], "test\nnote")
        conn.commit.assert_called_once()
        for row in (None, {"merged_into_id": 8}):
            cur.fetchone.return_value = row
            with patch.object(delivery_staff, "get_conn", return_value=conn), self.assertRaises(ValueError):
                delivery_staff.save_description(7, "test", 123)
        self.assertEqual(conn.rollback.call_count, 2)

    def test_description_validation(self):
        for ident, text in [(0, "test"), (True, "test"), (7, None), (7, "x" * 2001)]:
            with self.subTest(ident=ident), self.assertRaises(ValueError):
                delivery_staff.save_description(ident, text, 123)

    def test_each_photo_in_an_album_has_a_separate_profile_identity(self):
        first = delivery_staff._identity_key(7, "-100123", 10, "photo")
        second = delivery_staff._identity_key(7, "-100123", 11, "photo")
        self.assertNotEqual(first, second)

    def test_same_message_retry_keeps_profile_identity(self):
        first = delivery_staff._identity_key(7, "-100123", 10, "photo")
        second = delivery_staff._identity_key(7, "-100123", 10, "photo")
        separate_channel = delivery_staff._identity_key(7, "-100456", 10, "photo")
        self.assertEqual(first, second)
        self.assertNotEqual(first, separate_channel)

    def test_duplicate_photo_reuses_existing_profile(self):
        cur = MagicMock()
        cur.fetchall.return_value = [
            {"id": 5, "image_hash": "0000000000000000", "person_id": 42},
        ]
        row = {
            "id": 6, "venue_id": 7, "channel_id": "-100123",
            "message_id": 12, "media_type": "photo",
            "image_hash": "0000000000000001", "person_id": None,
        }
        with patch.object(delivery_staff, "_ensure_person") as create:
            person_id = delivery_staff._matching_photo_person(cur, row)
        self.assertEqual(person_id, 42)
        create.assert_not_called()

    def test_new_photo_gets_individual_profile(self):
        cur = MagicMock()
        cur.fetchall.return_value = [
            {"id": 5, "image_hash": "0000000000000000", "person_id": 42},
        ]
        row = {
            "id": 6, "venue_id": 7, "channel_id": "-100123",
            "message_id": 12, "media_type": "photo",
            "image_hash": "ffffffffffffffff", "person_id": None,
        }
        with patch.object(delivery_staff, "_ensure_person", return_value=43) as create:
            person_id = delivery_staff._matching_photo_person(cur, row)
        self.assertEqual(person_id, 43)
        create.assert_called_once_with(cur, row)

    def test_profile_media_hides_duplicate_images_but_keeps_videos(self):
        media = [
            {"id": 1, "media_type": "photo", "image_hash": "0000000000000000"},
            {"id": 2, "media_type": "photo", "image_hash": "0000000000000001"},
            {"id": 3, "media_type": "video", "image_hash": None},
        ]
        self.assertEqual(
            [item["id"] for item in delivery_staff._unique_profile_media(media)],
            [1, 3],
        )

    def test_existing_album_profiles_are_split_by_unique_photo(self):
        cur = MagicMock()
        cur.fetchall.return_value = [
            {
                "id": 1, "venue_id": 7, "channel_id": "-100123",
                "message_id": 10, "media_group_id": "album", "media_type": "photo",
                "image_hash": "0000000000000000", "person_id": 42,
            },
            {
                "id": 2, "venue_id": 7, "channel_id": "-100123",
                "message_id": 11, "media_group_id": "album", "media_type": "photo",
                "image_hash": "0000000000000001", "person_id": 43,
            },
            {
                "id": 3, "venue_id": 7, "channel_id": "-100123",
                "message_id": 12, "media_group_id": "album", "media_type": "photo",
                "image_hash": "ffffffffffffffff", "person_id": 42,
            },
        ]
        cur.fetchone.return_value = {"count": 0}
        with patch.object(delivery_staff, "_ensure_person", return_value=44) as create:
            delivery_staff._rebuild_photo_profiles(cur)
        create.assert_called_once_with(cur, cur.fetchall.return_value[2])
        updates = [
            call.args for call in cur.execute.call_args_list
            if call.args[0].startswith("UPDATE delivery_media SET person_id")
        ]
        self.assertIn((updates[0][0], (42, 2)), updates)
        self.assertIn((updates[1][0], (44, 3)), updates)

    def test_low_resolution_frames_are_skipped_before_model_loading(self):
        with patch.object(delivery_staff, "_face_models") as models:
            result = delivery_staff._features_from_frame(np.zeros((40, 60, 3), dtype=np.uint8))
        self.assertEqual(result, [])
        models.assert_not_called()

    def test_candidate_matching_only_suggests_and_sorts(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {
            "id": 1, "venue_id": 7, "consent_status": "granted", "merged_into_id": None,
        }
        cur.fetchall.return_value = [
            {"id": 1, "media_id": 1, "display_name": "A", "file_id": "a", "media_type": "photo"},
            {"id": 2, "media_id": 2, "display_name": "B", "file_id": "b", "media_type": "photo"},
            {"id": 3, "media_id": 3, "display_name": "C", "file_id": "c", "media_type": "photo"},
        ]
        vectors = {
            b"a": [np.array([1.0, 0.0])],
            b"b": [np.array([0.8, 0.6])],
            b"c": [np.array([0.0, 1.0])],
        }
        with patch.object(delivery_staff, "get_conn", return_value=conn), \
                patch.object(delivery_staff, "_face_models"), \
                patch.object(delivery_staff, "extract_features", side_effect=lambda _, value: vectors[value]):
            result = delivery_staff.match_candidates(1, lambda file_id: file_id.encode())
        self.assertEqual([item["person_id"] for item in result["candidates"]], [2, 3])
        self.assertEqual(result["candidates"][0]["similarity"], 0.8)
        self.assertFalse(result["auto_merged"])

    def test_face_models_require_consent_before_any_media_fetch(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {
            "id": 1, "venue_id": 7, "consent_status": "pending", "merged_into_id": None,
        }
        fetch_media = MagicMock()
        with patch.object(delivery_staff, "get_conn", return_value=conn), \
                patch.object(delivery_staff, "_face_models"):
            with self.assertRaisesRegex(ValueError, "授权"):
                delivery_staff.match_candidates(1, fetch_media)
        fetch_media.assert_not_called()

    def test_oversized_media_is_skipped_without_blocking_other_results(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {
            "id": 1, "venue_id": 7, "consent_status": "granted", "merged_into_id": None,
        }
        rows = [
            {"id": 1, "media_id": 1, "display_name": "A", "file_id": "a", "media_type": "photo"},
            {"id": 2, "media_id": 2, "display_name": "B", "file_id": "b", "media_type": "video"},
        ]
        cur.fetchall.side_effect = [rows, rows]

        def fetch_media(file_id):
            if file_id == "b":
                raise delivery_staff.MediaTooLargeError("over limit")
            return b"photo"

        with patch.object(delivery_staff, "get_conn", return_value=conn), \
                patch.object(delivery_staff, "_face_models"), \
                patch.object(delivery_staff, "extract_features", return_value=[np.array([1.0, 0.0])]):
            result = delivery_staff.match_candidates(1, fetch_media)
        self.assertEqual(result["skipped_oversize_count"], 1)
        self.assertEqual(result["candidates"], [])

    def test_public_people_includes_linked_videos(self):
        cur = MagicMock()
        cur.fetchall.side_effect = [
            [{"id": 7, "display_name": "A"}],
            [{"id": 10, "person_id": 7, "media_type": "video", "image_hash": None}],
        ]
        people = delivery_staff.public_people(cur, 7, "start", "end")
        self.assertEqual(people[0]["media"], [{"id": 10, "type": "video"}])


if __name__ == "__main__":
    unittest.main()
