import unittest
from datetime import date

from mingxi_complete_attendance import (
    COMPLETE_MARKER,
    build_complete_attendance_html,
    is_complete_attendance_text,
    message_link,
)
from mingxi_channel_album import extract_code_from_caption


class MingxiCompleteAttendanceTests(unittest.TestCase):
    def test_marker_detection(self):
        self.assertTrue(is_complete_attendance_text(f"{COMPLETE_MARKER}\n名单"))
        self.assertFalse(is_complete_attendance_text("出勤日期2026年11月21日"))

    def test_message_link(self):
        self.assertEqual(
            message_link("-1002323001411", 10404),
            "https://t.me/c/2323001411/10404?single",
        )

    def test_extract_caption_codes(self):
        self.assertEqual(extract_code_from_caption("987"), "987")
        self.assertEqual(extract_code_from_caption("日本：06"), "06")
        self.assertEqual(extract_code_from_caption("工号：5555 淘宝"), "5555")
        self.assertEqual(extract_code_from_caption("名玺牌：3333 东航"), "3333")

    def test_build_html_links_and_missing(self):
        attendance = {
            "duty_date": date(2026, 11, 21),
            "duty_shift": "实时出勤",
            "codes": ["987", "820"],
            "attendance_groups": [
                {
                    "group_type": "roster",
                    "group_name": "9号牌",
                    "technician_codes": ["987", "820"],
                }
            ],
        }
        html = build_complete_attendance_html(
            "名玺水汇会所",
            attendance,
            "-1002323001411",
            album_urls={
                "987": message_link("-1002323001411", 10404),
            },
        )
        self.assertIn(COMPLETE_MARKER, html)
        self.assertIn("9号牌", html)
        self.assertIn('href="https://t.me/c/2323001411/10404?single"', html)
        self.assertIn(">987</a>", html)
        self.assertIn("暂无相册：820", html)
        self.assertNotIn("9牌987", html)


if __name__ == "__main__":
    unittest.main()
