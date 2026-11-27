from datetime import date
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import massage_channel
from telebot.apihelper import ApiTelegramException


def telegram_error(code, description):
    return ApiTelegramException("editMessageText", None, {
        "error_code": code, "description": description,
    })


class MassageChannelTests(unittest.TestCase):
    def test_staff_caption_contains_current_services_and_prices(self):
        with patch.object(massage_channel, "_staff_services", return_value=[
            {
                "name": "柔式风情按摩",
                "price": "129.50",
                "duration": "80分钟",
            },
            {
                "name": "貂蝉风情采耳",
                "price": "139.00",
                "duration": "80分钟",
            },
        ]):
            text = massage_channel.staff_caption(6, "029")

        self.assertIn("工号：029", text)
        self.assertIn("柔式风情按摩", text)
        self.assertIn("价格：129.5美元｜时长：80分钟", text)
        self.assertNotIn("app.haotianzhushou.com", text)

    @patch.object(massage_channel, "schedule_directory_refresh")
    @patch.object(massage_channel, "_staff_markup", return_value=MagicMock())
    @patch.object(massage_channel, "staff_caption", return_value="caption")
    @patch.object(massage_channel, "_edit_caption")
    @patch.object(massage_channel, "register_post")
    def test_staff_post_updates_existing_caption_in_place(
        self, register, edit, caption, markup, schedule
    ):
        bot = MagicMock()

        massage_channel.sync_staff_post(bot, 6, "029", 4908)

        register.assert_called_once_with(6, "029", 4908)
        edit.assert_called_once_with(
            bot, 4908, "caption", markup.return_value
        )
        schedule.assert_called_once_with(bot)
        bot.send_message.assert_not_called()

    @patch.object(massage_channel, "_directory_markup", return_value=MagicMock())
    @patch.object(massage_channel, "directory_text", return_value="目录")
    @patch.object(massage_channel, "_edit_text")
    @patch.object(massage_channel, "_state_message_id", return_value=4939)
    def test_directory_edits_existing_message_without_reposting(
        self, state, edit, text, markup
    ):
        bot = MagicMock()

        self.assertEqual(massage_channel.refresh_directory(bot), 4939)

        edit.assert_called_once_with(
            bot, 4939, "目录", markup.return_value
        )
        bot.send_message.assert_not_called()

    @patch.object(massage_channel, "schedule_directory_refresh")
    @patch.object(massage_channel, "_set_state_message_id")
    @patch.object(massage_channel, "_directory_markup", return_value=MagicMock())
    @patch.object(massage_channel, "_edit_text")
    @patch.object(massage_channel, "_state_message_id", return_value=5000)
    @patch.object(massage_channel, "get_conn")
    @patch.object(massage_channel, "_post_message_ids", return_value={
        "029": 4908,
    })
    def test_attendance_edits_complete_post_then_deletes_draft(
        self, post_message_ids, get_conn, state, edit, markup, set_state, schedule
    ):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [{
            "category": "medium",
            "category_name": "⭐️ 中项",
            "name": "柔式风情按摩",
            "price": "129.50",
            "duration": "80分钟",
            "codes": "029",
        }]
        bot = MagicMock()
        draft = SimpleNamespace(chat=SimpleNamespace(id=-1002324026180), message_id=7000)

        sent = massage_channel.publish_attendance(bot, {
            "duty_date": date(2026, 11, 23),
            "duty_shift": "白班",
            "codes": ["029"],
        }, draft)

        self.assertEqual(sent, 5000)
        self.assertIn("129.5美元", edit.call_args.args[2])
        self.assertIn(
            '<a href="https://t.me/c/2324026180/4908?single">029</a>',
            edit.call_args.args[2],
        )
        post_message_ids.assert_called_once_with(["029"])
        bot.delete_message.assert_called_once_with(
            draft.chat.id, draft.message_id, timeout=20
        )
        bot.send_message.assert_not_called()
        schedule.assert_called_once_with(bot)

    def test_attendance_code_links_leave_unarchived_codes_as_text(self):
        text = massage_channel._attendance_code_links(
            ["002", "unknown"], {"002": 4909}
        )

        self.assertIn("https://t.me/c/2324026180/4909?single", text)
        self.assertIn(">002</a>", text)
        self.assertTrue(text.endswith("unknown"))

    @patch.object(massage_channel, "schedule_directory_refresh")
    @patch.object(massage_channel, "_set_state_message_id")
    @patch.object(massage_channel, "_directory_markup", return_value=MagicMock())
    @patch.object(massage_channel, "_edit_text")
    @patch.object(massage_channel, "_state_message_id", return_value=5000)
    @patch.object(massage_channel, "get_conn")
    @patch.object(massage_channel, "_post_message_ids", return_value={})
    def test_missing_attendance_post_is_replaced_only_after_confirmed_send(
        self, posts, get_conn, state, edit, markup, set_state, schedule
    ):
        get_conn.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        edit.side_effect = telegram_error(400, "Bad Request: message to edit not found")
        bot = MagicMock()
        bot.send_message.return_value.message_id = 6000
        draft = SimpleNamespace(chat=SimpleNamespace(id=-1002324026180), message_id=7000)

        result = massage_channel.publish_attendance(
            bot, {"duty_date": date(2026, 11, 27), "duty_shift": "晚班"}, draft
        )

        self.assertEqual(result, 6000)
        self.assertEqual([call.args for call in set_state.call_args_list],
                         [("attendance", -1), ("attendance", 6000)])
        bot.send_message.assert_called_once()
        bot.delete_message.assert_called_once_with(draft.chat.id, 7000, timeout=20)

    @patch.object(massage_channel, "schedule_directory_refresh")
    @patch.object(massage_channel, "_set_state_message_id")
    @patch.object(massage_channel, "_edit_text")
    @patch.object(massage_channel, "_state_message_id", return_value=5000)
    @patch.object(massage_channel, "get_conn")
    @patch.object(massage_channel, "_post_message_ids", return_value={})
    def test_other_edit_failures_do_not_repost_or_delete_draft(
        self, posts, get_conn, state, edit, set_state, schedule
    ):
        get_conn.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        edit.side_effect = telegram_error(403, "Forbidden")
        bot = MagicMock()
        draft = SimpleNamespace(chat=SimpleNamespace(id=-1002324026180), message_id=7000)
        with self.assertRaises(ApiTelegramException):
            massage_channel.publish_attendance(
                bot, {"duty_date": date(2026, 11, 27), "duty_shift": "晚班"}, draft
            )
        bot.send_message.assert_not_called()
        bot.delete_message.assert_not_called()
        set_state.assert_not_called()

    @patch.object(massage_channel, "_state_message_id", return_value=-1)
    @patch.object(massage_channel, "get_conn")
    @patch.object(massage_channel, "_post_message_ids", return_value={})
    def test_uncertain_send_does_not_send_or_delete_again(self, posts, get_conn, state):
        get_conn.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        bot = MagicMock()
        with self.assertRaisesRegex(RuntimeError, "发送结果未确认"):
            massage_channel.publish_attendance(
                bot, {"duty_date": date(2026, 11, 27), "duty_shift": "晚班"}, None
            )
        bot.send_message.assert_not_called()
        bot.delete_message.assert_not_called()

    @patch.object(massage_channel, "schedule_directory_refresh")
    @patch.object(massage_channel, "_set_state_message_id")
    @patch.object(massage_channel, "_edit_text")
    @patch.object(massage_channel, "_state_message_id", return_value=5000)
    @patch.object(massage_channel, "get_conn")
    @patch.object(massage_channel, "_post_message_ids", return_value={})
    def test_unchanged_attendance_does_not_repost(self, posts, get_conn, state, edit, set_state, schedule):
        get_conn.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        edit.side_effect = telegram_error(400, "Bad Request: message is not modified")
        bot = MagicMock()
        draft = SimpleNamespace(chat=SimpleNamespace(id=-1002324026180), message_id=7000)
        result = massage_channel.publish_attendance(
            bot, {"duty_date": date(2026, 11, 27), "duty_shift": "晚班"}, draft
        )
        self.assertEqual(result, 5000)
        bot.send_message.assert_not_called()
        bot.delete_message.assert_called_once_with(draft.chat.id, 7000, timeout=20)
        set_state.assert_not_called()

    @patch.object(massage_channel, "_wait_for_edit_slot")
    @patch.object(massage_channel, "_set_state_message_id")
    @patch.object(massage_channel, "_state_message_id", return_value=None)
    @patch.object(massage_channel, "get_conn")
    @patch.object(massage_channel, "_post_message_ids", return_value={})
    def test_send_timeout_preserves_uncertain_state_and_draft(self, posts, get_conn, state, set_state, wait):
        get_conn.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        bot = MagicMock()
        bot.send_message.side_effect = TimeoutError("send result unknown")
        draft = SimpleNamespace(chat=SimpleNamespace(id=-1002324026180), message_id=7000)
        with self.assertRaises(TimeoutError):
            massage_channel.publish_attendance(
                bot, {"duty_date": date(2026, 11, 27), "duty_shift": "晚班"}, draft
            )
        set_state.assert_called_once_with("attendance", -1)
        bot.delete_message.assert_not_called()

    @patch.object(massage_channel, "get_conn")
    def test_seed_existing_posts_does_not_send(self, get_conn):
        cur = get_conn.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"id": 6}, {"id": 9}]

        massage_channel.seed_existing_posts([
            ("029", 4908), ("002", 4909),
        ])

        statements = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any(
            "INSERT INTO massage_channel_posts" in query
            for query in statements
        ))
        self.assertFalse(hasattr(massage_channel, "send_message"))


if __name__ == "__main__":
    unittest.main()
