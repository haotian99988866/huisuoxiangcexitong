import ast
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import delivery_staff
from tests.test_delivery_customer_duty import media, member, START, END
import delivery_customer_duty
from tests.test_delivery_archive_api import DeliveryArchiveTests


class DeliveryArchiveBuildTests(unittest.TestCase):
    def test_build_media_is_public_but_never_attendance_or_notification(self):
        rows = [media(3, archive_only=True, media_group_id="g"),
                media(2, kind="video", archive_only=True, media_group_id="g"),
                media(1, kind="video", archive_only=True, media_group_id="g")]
        profile = member(rows)
        self.assertTrue(delivery_customer_duty.eligible(profile))
        self.assertEqual(len(profile["public_media"]), 3)
        self.assertIsNone(delivery_customer_duty.published_at(profile, START, END))
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [
            [{"id": 12, "merged_into_id": None}], rows, [], [],
        ]
        with patch.object(delivery_staff, "get_conn", return_value=conn), \
                patch.object(delivery_staff, "current_shift_window", return_value=(START, END, "晚班")):
            result = delivery_staff.admin_listing(7)
        self.assertFalse(result["people"][0]["on_duty"])
        self.assertEqual(result["on_duty_count"], 0)
        result = DeliveryArchiveTests().catalog(rows)
        self.assertEqual(len(result["people"][0]["media"]), 3)
        self.assertFalse(result["people"][0]["on_duty"])

    def test_new_normal_photo_after_build_counts_without_changing_old_flags(self):
        rows = [media(2, archive_only=False, image_hash="1234567890abcdef"),
                media(1, archive_only=True, image_hash="1234567890abcdef")]
        self.assertIsNotNone(delivery_customer_duty.published_at(member(rows), START, END))
        self.assertIsNone(delivery_customer_duty.published_at(member(rows[1:]), START, END))

    def test_mode_is_persistent_locked_and_missing_state_fails_explicitly(self):
        cur = MagicMock()
        for enabled in (False, True):
            cur.fetchone.return_value = {"archive_build_mode": enabled}
            self.assertEqual(delivery_staff.archive_build_mode(cur), enabled)
            self.assertIn("FOR SHARE", cur.execute.call_args.args[0])
        cur.fetchone.return_value = None
        with self.assertRaises(RuntimeError):
            delivery_staff.archive_build_mode(cur)
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        with patch.object(delivery_staff, "get_conn", return_value=conn):
            delivery_staff.set_archive_build_mode(True)
        self.assertIn("FOR UPDATE", cur.execute.call_args_list[0].args[0])
        self.assertEqual(cur.execute.call_args.args[1], (True,))
        conn.commit.assert_called_once()
        with self.assertRaises(ValueError):
            delivery_staff.set_archive_build_mode("on")

    def test_ingestion_snapshots_mode_for_photos_videos_and_text_preserves_edits(self):
        source = Path(delivery_staff.__file__).with_name("main.py").read_text(encoding="utf-8")
        functions = [node for node in ast.parse(source).body
                     if isinstance(node, ast.FunctionDef) and node.name in ("save_delivery_media", "save_delivery_text")]
        self.assertEqual(len(functions), 2)
        for enabled in (False, True):
            for kind in ("photo", "video", "text"):
                with self.subTest(enabled=enabled, kind=kind):
                    conn = MagicMock()
                    cur = conn.cursor.return_value.__enter__.return_value
                    message = MagicMock()
                    message.photo = [MagicMock(file_id="photo")] if kind == "photo" else []
                    message.video = MagicMock(file_id="video") if kind == "video" else None
                    message.caption = "age 23"
                    context = {
                        "get_venue_config": lambda chat: {"venue_id": 7},
                        "DELIVERY_VENUE_IDS": {7, 8, 9, 10}, "get_conn": lambda: conn,
                        "extract_age": MagicMock(return_value=23),
                        "bot": MagicMock(), "compute_hash": MagicMock(return_value="0123456789abcdef"),
                        "ocr_image_text": MagicMock(return_value="age 23"),
                    }
                    with patch.object(delivery_staff, "archive_build_mode", return_value=enabled), \
                            patch.object(delivery_staff, "register_media"):
                        exec(compile(ast.Module(body=functions, type_ignores=[]), "<delivery ingestion>", "exec"), context)
                        context["save_delivery_text" if kind == "text" else "save_delivery_media"](message)
                    query, values = cur.execute.call_args.args
                    self.assertIn("archive_only", query.split("ON DUPLICATE KEY UPDATE")[0])
                    self.assertNotIn("archive_only", query.split("ON DUPLICATE KEY UPDATE")[1])
                    self.assertIs(values[-1], enabled)
                    conn.commit.assert_called_once()
