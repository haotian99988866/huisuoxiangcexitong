import unittest
from datetime import date

from unittest.mock import MagicMock

from mingxi_complete_attendance import (
    ANCHOR_ATTENDANCE,
    COMPLETE_MARKER,
    ENV_NAV_MARKER,
    ENV_NAV_URL,
    FULL_ROSTER_MARKER,
    PROMO_MARKER,
    build_complete_attendance_html,
    build_env_nav_html,
    build_full_roster_html,
    build_promo_html,
    classify_roster_group,
    get_channel_anchor,
    is_complete_attendance_text,
    is_full_roster_request,
    message_link,
    publish_complete_attendance,
    set_channel_anchor,
)
from mingxi_channel_album import extract_code_from_caption


class MingxiCompleteAttendanceTests(unittest.TestCase):
    def test_marker_detection(self):
        self.assertTrue(is_complete_attendance_text(f"{COMPLETE_MARKER}\n名单"))
        self.assertTrue(is_complete_attendance_text(f"{FULL_ROSTER_MARKER}\n全员"))
        self.assertTrue(is_complete_attendance_text(f"{PROMO_MARKER}\n今晚半价"))
        self.assertTrue(is_complete_attendance_text(f"{ENV_NAV_MARKER}\n导航"))
        self.assertFalse(is_complete_attendance_text("出勤日期2026年11月21日"))
        self.assertTrue(is_full_roster_request("全部名单"))
        self.assertTrue(is_full_roster_request("人员全部名单"))
        self.assertFalse(is_full_roster_request(f"{FULL_ROSTER_MARKER}\n987"))
        self.assertFalse(is_full_roster_request("出勤日期晚班全部名单"))
        self.assertTrue(is_full_roster_request("请发全部名单"))

    def test_classify_and_full_roster_html(self):
        self.assertEqual(classify_roster_group("77"), "日韩专场")
        self.assertEqual(classify_roster_group("06"), "日韩专场")
        self.assertEqual(classify_roster_group("3333"), "名玺牌")
        self.assertEqual(classify_roster_group("987"), "9号牌")
        entries = [
            ("3333", 10410),
            ("77", 10415),
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
        self.assertIn("共 5 人", html)
        self.assertIn("名玺牌 · 会员$599", html)
        self.assertNotIn("名玺牌 · 半价", html)
        self.assertIn("日韩专场 · 会员$539", html)
        self.assertIn("9号牌 · 半价$249 · 会员$499", html)
        self.assertIn("8号牌 · 半价$239 · 会员$469", html)
        self.assertIn('href="https://t.me/c/2323001411/10404?single"', html)
        self.assertIn("本名单为频道相册全员目录", html)
        self.assertIn("导航：", html)
        self.assertIn(ENV_NAV_MARKER, html)
        self.assertIn(COMPLETE_MARKER, html)
        self.assertNotIn(f">{FULL_ROSTER_MARKER}</a>", html)
        self.assertNotIn(">选秀厅</a>", html)
        self.assertNotIn(COMPLETE_MARKER + "\n", html.split("\n")[0])
        nav = build_env_nav_html()
        self.assertIn(ENV_NAV_MARKER, nav)
        self.assertIn('href="https://t.me/mingxi03/3194"', nav)
        self.assertIn(">选秀厅</a>", nav)
        self.assertIn(">KTV·玺御厅403</a>", nav)
        self.assertIn(">价目表</a>", nav)
        self.assertIn(COMPLETE_MARKER, nav)
        self.assertIn(FULL_ROSTER_MARKER, nav)
        promo = build_promo_html("名玺水汇会所", "今晚满赠", valid_until="2026-11-30")
        self.assertIn(PROMO_MARKER, promo)
        self.assertIn("今晚满赠", promo)
        self.assertIn(ENV_NAV_MARKER, promo)
        self.assertIn(COMPLETE_MARKER, promo)
        self.assertIn(FULL_ROSTER_MARKER, promo)

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
        self.assertIn("9号牌 · 半价$249 · 会员$499", html)
        self.assertIn('href="https://t.me/c/2323001411/10404?single"', html)
        self.assertIn(">987</a>", html)
        self.assertIn("暂无相册：820", html)
        self.assertNotIn("9牌987", html)
        self.assertIn("导航：", html)
        self.assertIn(ENV_NAV_MARKER, html)
        self.assertIn(FULL_ROSTER_MARKER, html)
        self.assertNotIn(f">{COMPLETE_MARKER}</a>", html)
        self.assertIn("https://t.me/mingxi03/", html)
        self.assertNotIn(">大堂吧</a>", html)
        self.assertNotIn("照片收录", html)

    def test_publish_deletes_old_attendance_and_pins(self):
        channel = "-1002323001411"
        set_channel_anchor(channel, ANCHOR_ATTENDANCE, 10001)
        bot = MagicMock()
        bot.send_message.return_value = MagicMock(message_id=10002)
        publish_complete_attendance(bot, channel, f"{COMPLETE_MARKER}\nok")
        bot.send_message.assert_called_once()
        bot.delete_message.assert_called_once_with(int(channel), 10001)
        self.assertTrue(bot.pin_chat_message.called)
        self.assertEqual(get_channel_anchor(channel, ANCHOR_ATTENDANCE), 10002)


if __name__ == "__main__":
    unittest.main()
