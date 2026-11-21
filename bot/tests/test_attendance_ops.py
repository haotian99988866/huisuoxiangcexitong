import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import attendance_ops


class AttendanceOpsTests(unittest.TestCase):
    def test_keywords_cover_evening_and_roster(self):
        for word in ("出勤", "晚班", "早班", "名单", "白班", "技师", "按摩部"):
            self.assertIn(word, attendance_ops.ATTENDANCE_KEYWORDS)

    def test_record_and_read_events(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "attendance-ops.sqlite3"
            with patch.object(attendance_ops, "store_path", return_value=path):
                attendance_ops.record_event(2, "名玺水汇会所", False, "❌ 未解析到有效日期")
                attendance_ops.record_event(
                    2, "名玺水汇会所", True, "ok", rematched=3, pending_left=2
                )
                events = attendance_ops.recent_events(5)
        self.assertEqual(len(events), 2)
        self.assertTrue(events[0]["ok"])
        self.assertEqual(events[0]["rematched"], 3)
        self.assertEqual(events[0]["pending_left"], 2)
        self.assertFalse(events[1]["ok"])

    def test_staff_archive_component_without_store(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.sqlite3"
            with patch.object(attendance_ops, "store_path", return_value=path):
                component = attendance_ops.staff_archive_component()
        self.assertEqual(component["key"], "staff_archive")
        self.assertEqual(component["state"], "not_started")


if __name__ == "__main__":
    unittest.main()
