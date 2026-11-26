import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import telebot

from mingxi_complete_attendance import (
    ANCHOR_ATTENDANCE,
    ANCHOR_ROSTER,
    COMPLETE_MARKER,
    ENV_NAV_MARKER,
    FULL_ROSTER_MARKER,
    build_complete_attendance_html,
    build_env_nav_html,
    build_full_roster_html,
    classify_roster_group,
    is_complete_attendance_text,
    is_full_roster_request,
    message_link,
    post_complete_attendance_reply,
    publish_complete_attendance,
    publish_full_roster,
)
from mingxi_channel_album import extract_code_from_caption
from footer import skip_footer


class _AnchorStore:
    """内存锚点，避免单测污染直播 mingxi_channel_anchors。"""

    def __init__(self):
        self.data = {}

    def get(self, channel_id, kind):
        return self.data.get((str(channel_id), kind))

    def set(self, channel_id, kind, msg_id):
        self.data[(str(channel_id), kind)] = int(msg_id)


_STORE = _AnchorStore()


def _fake_get(channel_id, kind):
    return _STORE.get(channel_id, kind)


def _fake_set(channel_id, kind, msg_id):
    _STORE.set(channel_id, kind, msg_id)


class MingxiCompleteAttendanceTests(unittest.TestCase):
    def setUp(self):
        _STORE.data.clear()
        self._get_patch = patch(
            "mingxi_complete_attendance.get_channel_anchor", side_effect=_fake_get
        )
        self._set_patch = patch(
            "mingxi_complete_attendance.set_channel_anchor", side_effect=_fake_set
        )
        self._get_patch.start()
        self._set_patch.start()

    def tearDown(self):
        self._get_patch.stop()
        self._set_patch.stop()
        _STORE.data.clear()

    def test_marker_detection(self):
        self.assertTrue(is_complete_attendance_text(f"{COMPLETE_MARKER}\n名单"))
        self.assertTrue(is_complete_attendance_text(f"{FULL_ROSTER_MARKER}\n全员"))
        self.assertTrue(is_complete_attendance_text("【优惠】\n今晚半价"))
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
        self.assertIn("<b>名玺牌</b>  会$599", html)
        self.assertNotIn("半$599", html)
        self.assertIn("<b>日韩专场</b>  会$539", html)
        self.assertIn("<b>9号牌</b>  半$249  会$499", html)
        self.assertIn("<b>8号牌</b>  半$239  会$469", html)
        self.assertIn('href="https://t.me/c/2323001411/10404?single"', html)
        self.assertIn("点蓝字看相册", html)
        self.assertIn("导航  ", html)
        self.assertIn(ENV_NAV_MARKER, html)
        self.assertNotIn(f"{FULL_ROSTER_MARKER}</a>", html)
        self.assertNotIn(">选秀厅</a>", html)
        self.assertNotIn(COMPLETE_MARKER + "\n", html.split("\n")[0])
        # 无出勤锚点时导航不链出勤完整版
        self.assertNotIn(COMPLETE_MARKER + "</a>", html)
        nav = build_env_nav_html()
        self.assertIn(ENV_NAV_MARKER, nav)
        self.assertIn('href="https://t.me/mingxi03/3194"', nav)
        self.assertIn(">选秀厅</a>", nav)
        self.assertIn(">KTV·玺御厅403</a>", nav)
        self.assertIn(">价目表</a>", nav)
        self.assertIn(FULL_ROSTER_MARKER, nav)

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
        self.assertIn("<b>名玺水汇会所</b> · 实时出勤 · 2026-11-21", html)
        self.assertIn("<b>9号牌</b>  半$249  会$499", html)
        self.assertIn('href="https://t.me/c/2323001411/10404?single"', html)
        self.assertIn(">987</a>", html)
        self.assertIn("987</a>  820", html)
        self.assertIn("<b>暂无相册</b>  820", html)
        self.assertIn("点蓝字看相册", html)
        self.assertNotIn("9牌987", html)
        self.assertNotIn("┄", html)
        self.assertIn("导航  ", html)
        self.assertIn(ENV_NAV_MARKER, html)
        self.assertIn(FULL_ROSTER_MARKER, html)
        self.assertNotIn(f"{COMPLETE_MARKER}</a>", html)
        self.assertIn("https://t.me/mingxi03/", html)
        self.assertNotIn(">大堂吧</a>", html)
        self.assertNotIn("照片收录", html)

    def test_mingxi_skips_generic_footer(self):
        self.assertTrue(skip_footer("-1002323001411"))
        self.assertTrue(skip_footer("-1002324026180"))

    def test_publish_attendance_hangs_bottom_without_pinning_itself(self):
        channel = "-1002323001411"
        _fake_set(channel, ANCHOR_ATTENDANCE, 10001)
        bot = MagicMock()
        bot.send_message.return_value = MagicMock(message_id=10002)
        with patch(
            "mingxi_complete_attendance.refresh_cross_nav_messages"
        ):
            publish_complete_attendance(bot, channel, f"{COMPLETE_MARKER}\nok")
        bot.send_message.assert_called_once()
        bot.delete_message.assert_called_once_with(int(channel), 10001)
        bot.unpin_chat_message.assert_called_once_with(
            int(channel), message_id=10001
        )
        pinned = [
            call.kwargs.get("message_id") or (call.args[1] if len(call.args) > 1 else None)
            for call in bot.pin_chat_message.call_args_list
        ]
        self.assertNotIn(10002, pinned)
        self.assertEqual(_fake_get(channel, ANCHOR_ATTENDANCE), 10002)

    def test_publish_full_roster_replaces_when_edit_fails(self):
        channel = "-1002323001411"
        _fake_set(channel, ANCHOR_ROSTER, 10011)
        bot = MagicMock()
        bot.edit_message_text.side_effect = telebot.apihelper.ApiTelegramException(
            "editMessageText",
            MagicMock(),
            {"error_code": 400, "description": "can't edit"},
        )
        bot.send_message.return_value = MagicMock(message_id=10012)
        with patch(
            "mingxi_complete_attendance.refresh_cross_nav_messages"
        ):
            publish_full_roster(bot, channel, f"{FULL_ROSTER_MARKER}\nok")
        bot.send_message.assert_called_once()
        bot.delete_message.assert_called_once_with(int(channel), 10011)
        self.assertEqual(_fake_get(channel, ANCHOR_ROSTER), 10012)

    @patch("mingxi_complete_attendance.sync_album_from_matched_photos")
    @patch("mingxi_complete_attendance.resolve_album_links", return_value=({}, {}))
    @patch(
        "mingxi_complete_attendance.build_complete_attendance_content",
        return_value=(f"{COMPLETE_MARKER}\nok", MagicMock()),
    )
    @patch("mingxi_complete_attendance.publish_complete_attendance")
    @patch("price_catalog.current_catalog", return_value={})
    def test_post_complete_deletes_draft_without_reply(
        self, _catalog, publish, build_content, _links, _sync
    ):
        channel = -1002323001411
        publish.return_value = MagicMock(message_id=20002)
        bot = MagicMock()
        message = MagicMock(chat=MagicMock(id=channel), message_id=19999)
        attendance = {
            "duty_date": date(2026, 11, 22),
            "duty_shift": "晚班",
            "codes": ["987"],
            "attendance_groups": [],
        }
        venue = {"venue_id": 2, "venue_name": "名玺水汇会所"}
        sent = post_complete_attendance_reply(bot, message, venue, attendance)
        self.assertEqual(sent.message_id, 20002)
        markup = build_content.return_value[1]
        publish.assert_called_once_with(
            bot, channel, f"{COMPLETE_MARKER}\nok", reply_markup=markup
        )
        bot.delete_message.assert_called_once_with(channel, 19999)


if __name__ == "__main__":
    unittest.main()
