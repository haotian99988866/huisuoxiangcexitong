import unittest

from api_server import (
    apple_price_tables,
    attach_delivery_prices,
    build_delivery_posts,
)
from delivery_age import extract_work_no, parse_price_table, price_for_work_no

SMALL = "小苹果妹妹外送酒店今日出勤\n\n6牌    150＄\n8牌    170＄\nS牌    249＄\n豹子号    300＄"
RED = "红苹果妹妹外送酒店今日出勤\n\nA牌    150\nD牌    170\nS牌    190"


def row(id_, ocr="", media_type="photo", caption=""):
    return {
        "id": id_,
        "message_id": id_,
        "media_type": media_type,
        "media_group_id": None,
        "caption": caption,
        "ocr_text": ocr,
        "age": 20,
    }


class PriceTest(unittest.TestCase):
    def test_parse_price_table(self):
        table = parse_price_table(SMALL)
        self.assertEqual(table, {"6": "150", "8": "170", "S": "249"})
        self.assertEqual(parse_price_table(RED), {"A": "150", "D": "170", "S": "190"})

    def test_work_no_variants(self):
        self.assertEqual(extract_work_no("工号：808\n国籍"), "808")
        self.assertEqual(extract_work_no("工号：\ns39\n国籍"), "S39")
        self.assertIsNone(extract_work_no("国籍：越南"))

    def test_first_char_matches_price(self):
        table = parse_price_table(SMALL)
        self.assertEqual(price_for_work_no("808", table), "170")
        self.assertEqual(price_for_work_no("S39", table), "249")
        self.assertIsNone(price_for_work_no("D51", table))
        self.assertIsNone(price_for_work_no(None, table))

    def test_each_marker_uses_its_own_table(self):
        rows = [
            {**row(5, "", "text"), "caption": RED},
            row(4, "工号：S12"),
            {**row(3, "", "text"), "caption": SMALL},
            row(2, "工号:S39"),
            row(1, "工号:808"),
        ]
        posts = build_delivery_posts(rows)
        attach_delivery_prices(rows, posts, apple_price_tables(rows))
        got = {p["work_no"]: p["price"] for p in posts}
        self.assertEqual(got, {"S12": "190", "S39": "249", "808": "170"})


if __name__ == "__main__":
    unittest.main()
