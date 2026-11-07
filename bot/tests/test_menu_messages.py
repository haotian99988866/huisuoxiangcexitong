import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from telebot import apihelper, types
import menu_messages


class MenuMessageTests(unittest.TestCase):
    def test_only_recorded_old_bot_menu_ids_are_deleted(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [{"message_id": 10}, {"message_id": 12}]
        bot = Mock()
        message = SimpleNamespace(chat=SimpleNamespace(id=123), message_id=20)
        with patch("menu_messages.get_conn", return_value=conn):
            self.assertIs(menu_messages.keep_latest(bot, message), message)
        self.assertEqual([call.args for call in bot.delete_message.call_args_list], [(123, 10), (123, 12)])
        self.assertIn("message_id<", cur.execute.call_args_list[1].args[0])
        self.assertIn("is_keyboard=0 OR %s=1", cur.execute.call_args_list[1].args[0])
        self.assertEqual(cur.execute.call_args_list[1].args[1], (123, 123, 0))
        conn.close.assert_called_once()

    def test_new_keyboard_can_replace_previous_keyboard(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        message = SimpleNamespace(chat=SimpleNamespace(id=123), message_id=20)
        with patch("menu_messages.get_conn", return_value=conn):
            menu_messages.keep_latest(Mock(), message, is_keyboard=True)
        self.assertEqual(cur.execute.call_args_list[0].args[1], (123, 20, 1))
        self.assertEqual(cur.execute.call_args_list[1].args[1], (123, 123, 1))

    def test_failed_deletion_is_logged_and_retained_for_next_attempt(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [{"message_id": 10}]
        bot = Mock()
        bot.delete_message.side_effect = apihelper.ApiTelegramException("deleteMessage", None, {
            "error_code": 400, "description": "message can't be deleted"})
        message = SimpleNamespace(chat=SimpleNamespace(id=123), message_id=20)
        with patch("menu_messages.get_conn", return_value=conn), self.assertLogs("menu_messages"):
            menu_messages.keep_latest(bot, message)
        self.assertEqual(cur.execute.call_count, 2)

    def test_command_cleanup_deletes_only_earlier_recorded_bot_messages(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [{"message_id": 10}, {"message_id": 12}]
        bot = Mock()
        command = SimpleNamespace(chat=SimpleNamespace(id=123), message_id=20)

        with patch("menu_messages.get_conn", return_value=conn):
            self.assertEqual(menu_messages.clear_before(bot, command), 2)

        self.assertEqual([call.args for call in bot.delete_message.call_args_list], [(123, 10), (123, 12)])
        self.assertEqual(cur.execute.call_args_list[0].args[1], (123, 20))
        self.assertIn("message_id<%s", cur.execute.call_args_list[0].args[0])

    def test_delete_command_removes_the_current_start_message(self):
        bot = Mock()
        command = SimpleNamespace(chat=SimpleNamespace(id=123), message_id=20)

        self.assertTrue(menu_messages.delete_command(bot, command))

        bot.delete_message.assert_called_once_with(123, 20, timeout=20)

    def test_delete_command_logs_and_reports_telegram_failure(self):
        bot = Mock()
        bot.delete_message.side_effect = apihelper.ApiTelegramException("deleteMessage", None, {
            "error_code": 400, "description": "message can't be deleted"})
        command = SimpleNamespace(chat=SimpleNamespace(id=123), message_id=20)

        with self.assertLogs("menu_messages"):
            self.assertFalse(menu_messages.delete_command(bot, command))

    def test_tracked_reply_removes_legacy_keyboard_before_sending_inline_menu(self):
        bot = Mock()
        bot.send_message.return_value = SimpleNamespace(
            chat=SimpleNamespace(id=123), message_id=20)
        bot.send_message.side_effect = [
            SimpleNamespace(chat=SimpleNamespace(id=123), message_id=19),
            SimpleNamespace(chat=SimpleNamespace(id=123), message_id=20),
        ]
        with patch("menu_messages.keep_latest", side_effect=lambda bot, message, **kwargs: message) as track:
            result = menu_messages.send_tracked(
                bot, 123, "Menu", reply_markup=types.InlineKeyboardMarkup())

        self.assertEqual(result.message_id, 20)
        self.assertEqual(bot.send_message.call_args_list[0].args, (123, "已更新菜单。"))
        self.assertTrue(bot.send_message.call_args_list[0].kwargs["reply_markup"].remove_keyboard)
        self.assertEqual(bot.send_message.call_args_list[1].args, (123, "Menu"))
        self.assertIsInstance(bot.send_message.call_args_list[1].kwargs["reply_markup"],
                              types.InlineKeyboardMarkup)
        bot.edit_message_reply_markup.assert_not_called()
        bot.delete_message.assert_called_once_with(123, 19, timeout=20)
        track.assert_called_once_with(bot, bot.send_message.return_value, is_keyboard=True)
