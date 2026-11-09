from decimal import Decimal
import unittest

from venue_pricing import staff_price, dihao_category_for_photo, mingxi_price_options


class VenuePricingTests(unittest.TestCase):
    def test_mingxi_duration_prices_match_recorded_board(self):
        self.assertEqual(mingxi_price_options(["board:9号牌"]), [
            {"board": "9号牌", "minutes": 45, "price_usd": "249", "label": "半价"},
            {"board": "9号牌", "minutes": 90, "price_usd": "499", "label": "会员"},
        ])
        self.assertEqual(mingxi_price_options(["board:日韩牌"]), [
            {"board": "日韩牌", "minutes": 90, "price_usd": "539", "label": "会员"},
        ])
        self.assertEqual(mingxi_price_options([]), [])
        self.assertEqual(mingxi_price_options(["board:未知牌"]), [])

    def test_shared_board_prices_use_45_minute_start_price(self):
        price, description = staff_price(2, "902", ["board:9号牌"], [])
        self.assertEqual(price, Decimal("249"))
        self.assertIn("45分钟", description)
        self.assertIsNone(staff_price(2, "902", [], [])[0])

    def test_service_start_price_uses_recorded_categories_only(self):
        services = [
            {"name": "小项", "category": "small", "price": Decimal("29")},
            {"name": "中项", "category": "medium", "price": Decimal("129")},
        ]
        self.assertEqual(staff_price(1, "001", ["service:medium"], services)[0], Decimal("129"))
        self.assertEqual(staff_price(1, "001", ["service:medium", "service:small"], services)[0], Decimal("29"))
        self.assertIsNone(staff_price(1, "001", [], services)[0])

    def test_venue_specific_categories_and_unknown_prices(self):
        self.assertEqual(staff_price(4, "6666", [], [])[0], Decimal("369"))
        self.assertEqual(staff_price(4, "022", [], [])[0], Decimal("299"))
        self.assertEqual(staff_price(4, "J01", ["category:日韩"], [])[0], Decimal("499"))
        self.assertEqual(staff_price(5, "022", ["category:皇后"], [])[0], Decimal("229"))
        self.assertIsNone(staff_price(5, "022", [], [])[0])
        self.assertEqual(staff_price(6, "822", [], [])[0], Decimal("329"))
        self.assertEqual(staff_price(6, "129", [], [])[0], Decimal("129"))
        self.assertEqual(staff_price(6, "300", [], [])[0], Decimal("169"))
        self.assertEqual(staff_price(6, "999", ["category:俄罗斯"], [])[0], Decimal("429"))
        self.assertIsNone(staff_price(6, "韩国08", [], [])[0])

    def test_unpriced_mingxi_board_is_not_used_for_technician_estimate(self):
        self.assertIsNone(staff_price(2, "000", ["board:日韩牌"], [])[0])

    def test_dihao_photo_category_preserves_existing_rules(self):
        self.assertEqual(dihao_category_for_photo("822", ""), "8牌佳丽")
        self.assertEqual(dihao_category_for_photo("822", "公主：822"), "7牌公主")
        self.assertIsNone(dihao_category_for_photo("822", "公主：822\n佳丽：822"))
