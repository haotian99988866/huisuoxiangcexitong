import unittest
from datetime import datetime
from unittest.mock import patch

import footer
from config.channels import STAFF_ARCHIVE_VENUES


DIHAO_CHANNEL = str(STAFF_ARCHIVE_VENUES[6]["channel_id"])


class FooterAttendanceTests(unittest.TestCase):
    @patch.object(footer, "staff_listing")
    @patch.object(footer, "current_shift_window")
    @patch.object(footer, "get_conn")
    def test_dihao_footer_matches_complete_attendance_format(
        self, get_conn, current_shift_window, staff_listing
    ):
        start = datetime(2026, 11, 23, 18)
        current_shift_window.return_value = (start, datetime(2026, 11, 24, 6), "晚班")
        staff_listing.return_value = {
            "members": [
                {"id": 1, "code": "501", "group_name": "9牌贵妃",
                 "price_usd": "379", "price_detail": "9牌贵妃 · 90分钟", "cover": 11},
                {"id": 2, "code": "502", "group_name": "9牌贵妃",
                 "price_usd": "379", "price_detail": "9牌贵妃 · 90分钟", "cover": 12},
                {"id": 3, "code": "716", "group_name": "7牌公主",
                 "price_usd": "219", "price_detail": "7牌公主 · 90分钟", "cover": 13},
                {"id": 4, "code": "800", "group_name": "8牌佳丽",
                 "price_usd": "239", "price_detail": "8牌佳丽 · 90分钟", "cover": None},
            ]
        }
        conn = get_conn.return_value
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            {"staff_id": 1, "message_id": 5001},
            {"staff_id": 2, "message_id": 5002},
        ]
        cursor.fetchone.return_value = {"message_id": 7000}

        text = footer.footer_text(DIHAO_CHANNEL)

        self.assertIn("【出勤完整版】", text)
        self.assertIn("帝豪会所</b> · 实时出勤 · 2026-11-23", text)
        self.assertIn("<b>9牌贵妃</b>  $379 · 90分钟", text)
        self.assertIn('<a href="https://t.me/dihao02/5001">501</a>', text)
        self.assertIn('<a href="https://t.me/dihao02/5002">502</a>', text)
        self.assertIn("<b>暂无相册</b>  716  800", text)
        self.assertIn("点蓝字看相册", text)
        self.assertIn("https://t.me/dihao02/7000", text)
        self.assertNotIn("本频道每日更新", text)
        self.assertNotIn("https://t.me/dihao02/716", text)
        self.assertEqual(cursor.execute.call_args_list[0].args[1], (1, 2, 3))
        conn.close.assert_called_once()

    @patch.object(footer, "staff_listing")
    @patch.object(footer, "current_shift_window")
    @patch.object(footer, "get_conn")
    def test_empty_dihao_duty_still_has_complete_format_and_navigation(
        self, get_conn, current_shift_window, staff_listing
    ):
        current_shift_window.return_value = (
            datetime(2026, 11, 24, 6), datetime(2026, 11, 24, 18), "白班"
        )
        staff_listing.return_value = {"members": []}
        cursor = get_conn.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None

        text = footer.footer_text(DIHAO_CHANNEL)

        self.assertIn("【出勤完整版】", text)
        self.assertIn("当前班次暂无公开出勤人员。", text)
        self.assertIn("导航频道", text)
        self.assertFalse(any("SELECT staff_id,message_id" in call.args[0]
                             for call in cursor.execute.call_args_list))

    @patch.object(footer, "get_setting", return_value=footer.FOOTER_TEXT)
    @patch.object(footer, "staff_listing")
    def test_other_venue_footers_keep_the_existing_format(self, staff_listing, get_setting):
        channel_id = "-1002219602447"

        text = footer.footer_text(channel_id)

        self.assertIn(footer.FOOTER_TEXT.replace("{name}", "西港170💲外送"), text)
        staff_listing.assert_not_called()

    @patch.object(footer, "staff_listing")
    @patch.object(footer, "current_shift_window")
    @patch.object(footer, "get_conn")
    def test_dalang_and_wuxiang_codes_link_to_their_own_channel_posts(
        self, get_conn, current_shift_window, staff_listing
    ):
        current_shift_window.return_value = (
            datetime(2026, 11, 23, 18), datetime(2026, 11, 24, 6), "晚班"
        )
        staff_listing.return_value = {
            "members": [
                {"id": 8, "code": "808", "group_name": "1牌",
                 "price_usd": "199", "price_detail": "1牌 · 90分钟", "cover": 88}
            ]
        }
        cursor = get_conn.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{"staff_id": 8, "message_id": 5008}]
        cursor.fetchone.return_value = {"message_id": 7000}

        for venue_id in (4, 5):
            with self.subTest(venue_id=venue_id):
                venue = STAFF_ARCHIVE_VENUES[venue_id]
                text = footer.footer_text(str(venue["channel_id"]))
                self.assertIn("【出勤完整版】", text)
                self.assertIn(
                    f'<a href="https://t.me/{venue["channel_name"]}/5008">808</a>',
                    text,
                )
                self.assertIn(f"https://t.me/{venue['channel_name']}/7000", text)
                staff_listing.assert_called_with({"venue_id": venue_id, "mode": "duty"})


if __name__ == "__main__":
    unittest.main()
