import unittest
from types import SimpleNamespace
from unittest import mock

import publisher


class BuildCaptionTests(unittest.TestCase):
    def test_keeps_title_and_tags_only(self):
        raw = (
            "《蜡笔小新第六季》第八集08\n#下饭剧 #追剧 #蜡笔小新 #搞笑 🤗每天持续更新\n\n来这选择🎞电视剧 综艺"
        )
        caption = publisher.build_caption(raw)
        self.assertEqual(
            caption,
            "《蜡笔小新第六季》第八集08\n#下饭剧 #追剧 #蜡笔小新 #搞笑\n\n" + publisher.FOOTER_TEXT,
        )

    def test_no_title_returns_none(self):
        self.assertIsNone(publisher.build_caption("#电视剧 #4K"))
        self.assertIsNone(publisher.build_caption(""))

    def test_dedupes_tags_and_limits_length(self):
        caption = publisher.build_caption("《A》\n#x #x " + " ".join(f"#t{i}" for i in range(500)))
        self.assertEqual(caption.count("#x"), 1)
        self.assertLessEqual(len(caption), publisher.CAPTION_LIMIT)


class PublishTests(unittest.TestCase):
    def test_publish_copies_with_new_caption_and_saves(self):
        bot = mock.Mock()
        bot.copy_message.return_value = SimpleNamespace(message_id=500)
        media = SimpleNamespace(duration=60, thumbnail=SimpleNamespace(file_id="t1"))
        with mock.patch.object(publisher, "save_movie") as save:
            publisher._publish(bot, (1, 2, "《A》第1集\n#电视剧", media))
        args, kwargs = bot.copy_message.call_args
        self.assertEqual(args[1:], (1, 2))
        self.assertEqual(kwargs["caption"], "《A》第1集\n#电视剧")
        self.assertEqual(save.call_args.args[1], 500)

    def test_handle_video_without_title_asks_admin(self):
        bot = mock.Mock()
        message = SimpleNamespace(
            video=SimpleNamespace(), document=None, caption="#电视剧", chat=SimpleNamespace(id=1),
            message_id=3,
        )
        self.assertTrue(publisher.handle_video(bot, message))
        bot.reply_to.assert_called_once()


class PublisherAuthTests(unittest.TestCase):
    def test_grant_and_revoke(self):
        store = {}
        with mock.patch("admin.get_setting", side_effect=lambda k, d=None: store.get(k, d)), \
             mock.patch("admin.set_setting", side_effect=lambda k, v: store.__setitem__(k, v)):
            publisher.set_publisher(111, True)
            publisher.set_publisher(222, True)
            self.assertEqual(publisher.publisher_ids(), {111, 222})
            publisher.set_publisher(111, False)
            self.assertEqual(publisher.publisher_ids(), {222})

    def test_command_rejects_non_numeric(self):
        bot = mock.Mock()
        message = SimpleNamespace(text="/publisher abc")
        publisher.handle_publisher_command(bot, message)
        self.assertIn("数字", bot.reply_to.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
