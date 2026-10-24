import unittest
from attendance_parser import parse_attendance_text, parse_plate_attendance_text


class DayShiftTextTests(unittest.TestCase):
    TEXT = (
        "2026-10-24日 白班\n401选钟\n"
        "🎀9～～～962🇨🇳15.50下\n🎀9～～～919🇨🇳🈳\n"
        "🎀8～～～839🇻🇳15.40下\n🎀8～～～813🇻🇳🈳\n🎀8～～～833🇻🇳🈳"
    )

    def test_codes_and_shift(self):
        for parse in (parse_attendance_text, parse_plate_attendance_text):
            data = parse(self.TEXT)
            self.assertEqual(data["duty_shift"], "白班")
            self.assertEqual(str(data["duty_date"]), "2026-10-24")
            self.assertEqual(data["codes"], ["962", "919", "839", "813", "833"])
            self.assertEqual(
                [(g["group_name"], g["technician_codes"]) for g in data["attendance_groups"]],
                [("9号牌", ["962", "919"]), ("8号牌", ["839", "813", "833"])],
            )


if __name__ == "__main__":
    unittest.main()
