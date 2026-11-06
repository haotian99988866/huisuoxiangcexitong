import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from telebot import apihelper
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
