import unittest
from datetime import date

from mingxi_complete_attendance import (
    COMPLETE_MARKER,
    FULL_ROSTER_MARKER,
    build_complete_attendance_html,
    build_full_roster_html,
    classify_roster_group,
    is_complete_attendance_text,
    is_full_roster_request,
    message_link,
)
from mingxi_channel_album import extract_code_from_caption


class MingxiCompleteAttendanceTests(unittest.TestCase):
    def test_marker_detection(self):
        self.assertTrue(is_complete_attendance_text(f"{COMPLETE_MARKER}\n名单"))
        self.assertTrue(is_complete_attendance_text(f"{FULL_ROSTER_MARKER}\n全员"))
        self.assertFalse(is_complete_attendance_text("出勤日期2026年11月21日"))
        self.assertTrue(is_full_roster_request("全部名单"))
        self.assertTrue(is_full_roster_request("人员全部名单"))
        self.assertFalse(is_full_roster_request(f"{FULL_ROSTER_MARKER}\n987"))
        self.assertFalse(is_full_roster_request("出勤日期晚班全部名单"))
        self.assertTrue(is_full_roster_request("请发全部名单"))

    def test_classify_and_full_roster_html(self):
        self.assertEqual(classify_roster_group("77"), "名玺牌")
        self.assertEqual(classify_roster_group("06"), "日韩专场")
        self.assertEqual(classify_roster_group("987"), "9号牌")
        entries = [
            ("77", 10410),
            ("06", 10420),
            ("987", 10404),
            ("820", 10430),
        ]
        html = build_full_roster_html(
            "名玺水汇会所",
            "-1002323001411",
            entries=entries,
        )
        self.assertIn(FULL_ROSTER_MARKER, html)
        self.assertIn("人员全部名单", html)
        self.assertIn("共 4 人", html)
        self.assertIn("名玺牌", html)
        self.assertIn("日韩专场", html)
        self.assertIn("9号牌", html)
        self.assertIn("8号牌", html)
        self.assertIn('href="https://t.me/c/2323001411/10404?single"', html)
        self.assertIn("今日谁在请看【出勤完整版】", html)
        self.assertNotIn(COMPLETE_MARKER + "\n", html.split("\n")[0])

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
