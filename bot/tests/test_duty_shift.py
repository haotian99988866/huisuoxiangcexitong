import unittest
from datetime import datetime

from duty_shift import current_shift_window


class CurrentShiftWindowTest(unittest.TestCase):
    def test_day_shift(self):
        for hour, minute in ((6, 0), (12, 30), (17, 59)):
            start, end, name = current_shift_window(datetime(2026, 10, 24, hour, minute))
            self.assertEqual(name, "白班")
            self.assertEqual(start, datetime(2026, 10, 24, 6, 0))
            self.assertEqual(end, datetime(2026, 10, 24, 18, 0))

    def test_night_shift_before_midnight(self):
        start, end, name = current_shift_window(datetime(2026, 10, 24, 18, 0))
        self.assertEqual(name, "晚班")
        self.assertEqual(start, datetime(2026, 10, 24, 18, 0))
        self.assertEqual(end, datetime(2026, 10, 25, 6, 0))

    def test_night_shift_after_midnight(self):
        start, end, name = current_shift_window(datetime(2026, 10, 25, 5, 59))
        self.assertEqual(name, "晚班")
        self.assertEqual(start, datetime(2026, 10, 24, 18, 0))
        self.assertEqual(end, datetime(2026, 10, 25, 6, 0))


if __name__ == "__main__":
    unittest.main()
