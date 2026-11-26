import asyncio
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts import mingxi_update_archive as update


def message(text="123", photo_id=1, **overrides):
    values = {"message": text, "photo": SimpleNamespace(id=photo_id), "grouped_id": None,
              "entities": None, "reply_markup": None}
    values.update(overrides)
    return SimpleNamespace(**values)


class MingxiArchiveUpdateTests(unittest.TestCase):
    def test_caption_uses_existing_board_price_and_does_not_invent_unknown_half_price(self):
        self.assertIn("工号：123", update.caption("123", "1号牌"))
        self.assertIn("半价 $139 · 45分钟", update.caption("123", "1号牌"))
        self.assertIn("会员 $269 · 90分钟", update.caption("123", "1号牌"))
        self.assertNotIn("原说明", update.caption("123", "1号牌"))
        self.assertNotIn("价格为现有价目表参考", update.caption("123", "1号牌"))
        japanese = update.caption("06", "日韩牌")
        self.assertIn("会员 $539 · 90分钟", japanese)
        self.assertNotIn("45分钟", japanese)

    def test_missing_photo_and_media_group_are_reported(self):
        self.assertIsNone(update.original_issue(message("old text", reply_markup=object())))
        self.assertEqual(update.original_issue(None), "missing_photo")
        self.assertEqual(update.original_issue(message(grouped_id=7)), "media_group_no_buttons")

    def test_ambiguous_identity_uses_generic_archive_button(self):
        markup = update.keyboard({"staff_id": 25, "exact_photo": 0}, "example_bot").to_dict()
        self.assertNotIn("staff_2_25", markup["inline_keyboard"][0][0]["url"])
        markup = update.keyboard({"staff_id": 25, "exact_photo": 1}, "example_bot").to_dict()
        self.assertIn("staff_2_25", markup["inline_keyboard"][0][0]["url"])

    def test_successful_edit_uses_compact_format_and_adds_buttons(self):
        row = {"code": "123", "msg_id": 100, "staff_id": 25, "exact_photo": 1,
               "price_tags_json": '["board:1号牌"]'}
        bot = Mock()
        bot.get_me.return_value = SimpleNamespace(id=1, username="example_bot")
        bot.get_chat_member.return_value = SimpleNamespace(can_edit_messages=True)
        reader = SimpleNamespace(get_entity=AsyncMock(return_value=SimpleNamespace(id=2323001411)),
                                 get_messages=AsyncMock(return_value=[message("旧价格", reply_markup=object())]))
        def result(text, _chat_id, message_id, **kwargs):
            return SimpleNamespace(message_id=message_id, caption=text,
                                   reply_markup=kwargs["reply_markup"])
        bot.edit_message_caption.side_effect = result
        with patch.object(update, "candidates", return_value=[row]), \
                patch.object(update, "current_prices", return_value=(update.PRICES, None)), \
                patch.object(update.asyncio, "sleep", new_callable=AsyncMock):
            counts = asyncio.run(update.run(bot, reader, 0, 1, apply=True))
        self.assertEqual(counts["updated"], 1)
        text = bot.edit_message_caption.call_args.args[0]
        self.assertNotIn("原说明", text)
        self.assertNotIn("价格为现有价目表参考", text)
        self.assertIn("半价 $139 · 45分钟", text)

    def test_preview_never_edits_and_missing_photo_is_reported(self):
        row = {"code": "123", "msg_id": 100, "staff_id": 25, "exact_photo": 1,
               "price_tags_json": '["board:1号牌"]'}
        bot = Mock()
        bot.get_me.return_value = SimpleNamespace(id=1, username="example_bot")
        bot.get_chat_member.return_value = SimpleNamespace(can_edit_messages=True)
        reader = SimpleNamespace(get_entity=AsyncMock(return_value=SimpleNamespace(id=2323001411)),
                                 get_messages=AsyncMock(return_value=[None]))
        with patch.object(update, "candidates", return_value=[row]), \
                patch.object(update, "current_prices", return_value=(update.PRICES, None)), \
                patch.object(update.asyncio, "sleep", new_callable=AsyncMock):
            result = asyncio.run(update.run(bot, reader, 0, 1, apply=True))
        self.assertEqual(result["review"], 1)
        bot.edit_message_caption.assert_not_called()

        reader.get_messages = AsyncMock(return_value=[message()])
        with patch.object(update, "candidates", return_value=[row]), \
                patch.object(update, "current_prices", return_value=(update.PRICES, None)), \
                patch.object(update.asyncio, "sleep", new_callable=AsyncMock):
            result = asyncio.run(update.run(bot, reader, 0, 1))
        self.assertEqual(result["eligible"], 1)
        bot.edit_message_caption.assert_not_called()

    def test_edit_uses_current_catalog_and_archive_category(self):
        row = {"code": "123", "msg_id": 100, "staff_id": 25, "exact_photo": 1,
               "price_tags_json": '["board:1号牌"]'}
        bot = Mock()
        bot.get_me.return_value = SimpleNamespace(id=1, username="example_bot")
        bot.get_chat_member.return_value = SimpleNamespace(can_edit_messages=True)
        reader = SimpleNamespace(get_entity=AsyncMock(return_value=SimpleNamespace(id=2323001411)),
                                 get_messages=AsyncMock(return_value=[message("原价格 $139")]))
        catalog = copy.deepcopy(update.PRICES)
        catalog["mingxiBoardPrices"][0]["half"] = 149
        bot.edit_message_caption.side_effect = lambda text, _chat_id, message_id, **kwargs: SimpleNamespace(
            message_id=message_id, caption=text, reply_markup=kwargs["reply_markup"])
        with patch.object(update, "candidates", return_value=[row]), \
                patch.object(update, "current_prices", return_value=(catalog, 2)), \
                patch.object(update.asyncio, "sleep", new_callable=AsyncMock):
            result = asyncio.run(update.run(bot, reader, 0, 1, apply=True))
        self.assertEqual(result["updated"], 1)
        self.assertIn("半价 $149 · 45分钟", bot.edit_message_caption.call_args.args[0])

    def test_only_changed_board_is_updated(self):
        rows = [
            {"code": "123", "msg_id": 100, "staff_id": 25, "exact_photo": 1,
             "price_tags_json": '["board:1号牌"]'},
            {"code": "223", "msg_id": 101, "staff_id": 26, "exact_photo": 1,
             "price_tags_json": '["board:2号牌"]'},
        ]
        bot = Mock()
        bot.get_me.return_value = SimpleNamespace(id=1, username="example_bot")
        bot.get_chat_member.return_value = SimpleNamespace(can_edit_messages=True)
        bot.edit_message_caption.side_effect = lambda text, _chat_id, message_id, **kwargs: SimpleNamespace(
            message_id=message_id, caption=text, reply_markup=kwargs["reply_markup"])
        reader = SimpleNamespace(get_entity=AsyncMock(return_value=SimpleNamespace(id=2323001411)),
                                 get_messages=AsyncMock(return_value=[message(), message()]))
        with patch.object(update, "candidates", return_value=rows), \
                patch.object(update, "current_prices", return_value=(update.PRICES, 1)), \
                patch.object(update.asyncio, "sleep", new_callable=AsyncMock):
            result = asyncio.run(update.run(bot, reader, 0, 2, apply=True, boards={"2号牌"}))
        self.assertEqual(result["updated"], 1)
        self.assertEqual(bot.edit_message_caption.call_args.args[2], 101)


if __name__ == "__main__":
    unittest.main()
