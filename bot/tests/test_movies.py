import unittest

from movies import parse_caption


class ParseCaptionTests(unittest.TestCase):
    def test_episode_with_footer(self):
        text = "《莫语者》第七集07 #4K #莫语者 #电视剧\n\n来这选择🎞电视剧  综艺  电影"
        self.assertEqual(
            parse_caption(text),
            {
                "title": "《莫语者》第七集07",
                "series": "莫语者",
                "category": "电视剧",
                "tags": "4K,莫语者,电视剧",
            },
        )

    def test_season_name_kept_from_title(self):
        parsed = parse_caption("《令人心动的offer第八季》20261021上 #4K #令人心动的offer #综艺")
        self.assertEqual(parsed["series"], "令人心动的offer第八季")
        self.assertEqual(parsed["category"], "综艺")

    def test_category_aliases(self):
        self.assertEqual(parse_caption("《A》1 #韩剧")["category"], "日韩剧")
        self.assertEqual(parse_caption("《A》1 #日漫")["category"], "动漫/日漫")
        self.assertEqual(parse_caption("《A》1 #美剧")["category"], "英/美剧")

    def test_series_from_tag_when_no_brackets(self):
        parsed = parse_caption("第3集 #1080P #某某剧 #网剧")
        self.assertEqual((parsed["series"], parsed["category"]), ("某某剧", "网剧"))

    def test_no_category_and_untitled(self):
        self.assertEqual(parse_caption("《A》1 #4K")["category"], "其他")
        self.assertIsNone(parse_caption("#4K #电影"))
        self.assertIsNone(parse_caption(""))


if __name__ == "__main__":
    unittest.main()


