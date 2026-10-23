import unittest
from datetime import date

from attendance_parser import parse_attendance_text, parse_plate_attendance_text


SAMPLE_ANNOUNCEMENT = """\
🌹按摩部2026年10月23日 (晚班出勤)
🎁🎁🎁🎁🎁🎁🎁🎁🎁🎁🎁

⭐️小项
💥沐足.肩颈.头部.采耳.火罐.刮痧.洗耳.洗面.修脚.治脚气 (29$.30分钟）
💥甲沟炎.中耳炎.刺络.面部护理(69$.30分钟）
💥至尊按摩 (99$.80分钟)
全身按摩，包括足底.背.肩.头部
098🇻🇳 021🇨🇳
065🇨🇳 005🇻🇳
066🇻🇳 029🇻🇳
002🇨🇳 011🇨🇳
033🇻🇳

⭐️中项
💥柔式风情按摩 (129$.80分钟）
💥貂蝉风情采耳 (139$.80分钟）
💥男士前列腺保养 《打✈️》(169$.80分钟）三起三落
090🇻🇳 069🇻🇳
107🇻🇳 068🇻🇳
050🇨🇳

⭐️高项
💥男士魅力养生《口》(229$.80分钟）
112🇨🇳 111🇻🇳
088🇻🇳

🤩::::::✰名玺中医按摩✰::::::🤩
💥中医理疗 (109$.60分钟）
全身穴位理疗按摩
💥中医经络 (139$.70分钟）
全身经络油推.穴位理疗.按摩
💥固本培元 (179$.70分钟）
腰部、臀部、腹股沟淋巴等
💥男士私密养护 (329$.100分钟)
《男性早泄.阳痿.疲软.前列腺功能障碍等》
中医 ~ ~ ~ 015🇨🇳（男）
中医 ~ ~ ~ 008🇨🇳
中医～～～060🇨🇳

🤩::::✰🌸 修脚师🌸✰；::::::🤩
修脚师～～～035🇨🇳
"""


class ParseAttendanceTextTests(unittest.TestCase):
    def test_extracts_daily_roster_and_service_category_rosters(self):
        result = parse_attendance_text(SAMPLE_ANNOUNCEMENT)

        self.assertEqual(result["duty_date"], date(2026, 10, 23))
        self.assertEqual(result["duty_shift"], "晚班")
        self.assertIn("098", result["codes"])
        self.assertIn("035", result["codes"])
        self.assertEqual(
            result["service_codes"],
            {
                "small": ["098", "021", "065", "005", "066", "029", "002", "011", "033"],
                "medium": ["090", "069", "107", "068", "050"],
                "high": ["112", "111", "088"],
                "tcm": ["015", "008", "060"],
                "pedicure": ["035"],
            },
        )

    def test_parses_plate_roster_and_services(self):
        announcement = """\
出勤日期2026年10月 22 日 实时出勤
俄罗斯：7026
名玺9号牌
9牌900 9牌939 9牌969
名玺8号牌
8牌872 8牌865
三通
9牌969
8牌
潮喷
9牌969 9牌900
SM
8牌871 7牌755
双仪
9牌969 9牌971
包夜简介
凌晨3点前4小时
"""

        result = parse_plate_attendance_text(announcement)

        self.assertEqual(result["duty_date"], date(2026, 10, 22))
        self.assertEqual(result["duty_shift"], "实时出勤")
        self.assertEqual(result["codes"], ["7026", "900", "939", "969", "872", "865"])
        self.assertEqual(
            result["attendance_groups"],
            [
                {
                    "group_type": "roster",
                    "group_name": "俄罗斯",
                    "technician_codes": ["7026"],
                },
                {
                    "group_type": "roster",
                    "group_name": "9号牌",
                    "technician_codes": ["900", "939", "969"],
                },
                {
                    "group_type": "roster",
                    "group_name": "8号牌",
                    "technician_codes": ["872", "865"],
                },
                {
                    "group_type": "service",
                    "group_name": "三通",
                    "technician_codes": ["969"],
                },
                {
                    "group_type": "service",
                    "group_name": "潮喷",
                    "technician_codes": ["969", "900"],
                },
                {
                    "group_type": "service",
                    "group_name": "SM",
                    "technician_codes": ["871", "755"],
                },
                {
                    "group_type": "service",
                    "group_name": "双仪",
                    "technician_codes": ["969", "971"],
                },
            ],
        )

    def test_parses_date_with_extra_whitespace(self):
        result = parse_plate_attendance_text(
            "出勤日期2026年10月  22 日 实时出勤\n9牌969"
        )

        self.assertEqual(result["duty_date"], date(2026, 10, 22))
        self.assertEqual(result["codes"], ["969"])

    def test_extracts_service_categories_from_delimited_headings(self):
        announcement = """\
出勤日期2026年10月 22 日 实时出勤
✰🇯🇵名玺日本🇯🇵✰
✰名玺俄罗斯✰
俄罗斯：7026
✰👑名玺牌👑✰
✰🌸名玺9号牌🌸✰
9牌900 9牌969
✰🌸三通🌸✰
9牌969
✰✨新分类✨✰
9牌900
✰✨包夜简介✨✰
不解析这里的号码 123
"""

        result = parse_plate_attendance_text(announcement)

        self.assertEqual(result["codes"], ["7026", "900", "969"])
        self.assertEqual(
            result["attendance_groups"],
            [
                {
                    "group_type": "roster",
                    "group_name": "俄罗斯",
                    "technician_codes": ["7026"],
                },
                {
                    "group_type": "roster",
                    "group_name": "9号牌",
                    "technician_codes": ["900", "969"],
                },
                {
                    "group_type": "service",
                    "group_name": "三通",
                    "technician_codes": ["969"],
                },
                {
                    "group_type": "service",
                    "group_name": "新分类",
                    "technician_codes": ["900"],
                },
            ],
        )

    def test_does_not_parse_prices_or_durations_as_technician_codes(self):
        result = parse_attendance_text(SAMPLE_ANNOUNCEMENT)

        for invalid_code in ("29", "30", "69", "80", "129"):
            self.assertNotIn(invalid_code, result["codes"])
            self.assertTrue(
                all(
                    invalid_code not in codes
                    for codes in result["service_codes"].values()
                )
            )


if __name__ == "__main__":
    unittest.main()
