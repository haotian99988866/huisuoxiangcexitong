import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("telebot", MagicMock())
sys.modules.setdefault("telebot.util", MagicMock())

from main import extract_room_number  # noqa: E402


class ExtractRoomNumberTests(unittest.TestCase):
    def test_labeled_room(self):
        self.assertEqual(extract_room_number("房间：a808"), "A808")
        self.assertEqual(extract_room_number("包厢 6688 视频"), "6688")

    def test_bare_number(self):
        self.assertEqual(extract_room_number("808"), "808")

    def test_trailing_room_after_hall_name(self):
        self.assertEqual(extract_room_number("👍VIP大包🫥\n名玺·玺汇厅\xa0401"), "401")
        self.assertEqual(extract_room_number("名玺·玺盈厅405"), "405")

    def test_none(self):
        self.assertIsNone(extract_room_number("今晚特价"))
        self.assertIsNone(extract_room_number(None))


if __name__ == "__main__":
    unittest.main()
