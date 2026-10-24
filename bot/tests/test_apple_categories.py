import unittest

from api_server import build_delivery_posts, classify_apple_rows


def row(id_, caption="", group=None, media_type="photo"):
    return {
        "id": id_,
        "message_id": id_,
        "media_type": media_type,
        "media_group_id": group,
        "caption": caption,
    }


def text(id_, caption):
    return row(id_, caption, media_type="text")


class ClassifyAppleRowsTest(unittest.TestCase):
    def test_text_marker_covers_media_above_it(self):
        rows = [row(1), row(2), text(3, "小苹果妹妹外送"), row(4), text(5, "红苹果外送"), row(6)]
        cats = classify_apple_rows(rows)
        self.assertEqual(
            [cats[i] for i in (1, 2, 4, 6)], ["small", "small", "red", None]
        )

    def test_media_own_caption_has_priority(self):
        rows = [row(1, "红苹果"), text(2, "小苹果")]
        self.assertEqual(classify_apple_rows(rows)[1], "red")

    def test_album_caption_applies_to_group(self):
        rows = [row(1, "红苹果", "g1"), row(2, "", "g1"), text(3, "小苹果")]
        cats = classify_apple_rows(rows)
        self.assertEqual([cats[1], cats[2]], ["red", "red"])

    def test_posts_skip_text_and_carry_category(self):
        rows = [text(3, "小苹果"), row(2), row(1)]
        posts = build_delivery_posts(rows, classify_apple_rows(rows))
        self.assertEqual([p["category"] for p in posts], ["small", "small"])


if __name__ == "__main__":
    unittest.main()
