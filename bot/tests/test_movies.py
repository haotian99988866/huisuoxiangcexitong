import unittest
from unittest.mock import MagicMock, patch

import movies
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

class CatalogBatchTests(unittest.TestCase):
    def test_batch_counts_match_filters_and_uses_offset_with_stable_order(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"n": 49}
        cursor.fetchall.side_effect = [
            [{"category": "电影", "n": 500}],
            [{"series": "测试", "category": "电影", "episodes": 1, "first_id": 88, "last_id": 88}],
            [{"message_id": 88, "thumb_file_id": None, "thumb_url": None}],
        ]
        with patch.object(movies, "get_conn", return_value=connection):
            result = movies.movie_catalog("电影", "测试", limit=24, page=2)
        self.assertEqual((result["matching_total"], result["total"], result["has_more"]), (49, 500, True))
        batch = cursor.execute.call_args_list[2]
        self.assertIn("ORDER BY last_id DESC,series,category", batch.args[0])
        self.assertEqual(batch.args[1][-2:], [24, 24])
        self.assertEqual(cursor.execute.call_args_list[1].args[1],
                         [movies.MOVIE_CHANNEL_ID, "电影", "%测试%", "%测试%", "%测试%"])

    def test_invalid_batch_parameters_fail_before_database(self):
        with patch.object(movies, "get_conn") as connect:
            for page in (0, True, "2", 10001):
                with self.assertRaises(ValueError):
                    movies.movie_catalog(page=page)
            for limit in (0, True, 201, "24"):
                with self.assertRaises(ValueError):
                    movies.movie_catalog(limit=limit)
            connect.assert_not_called()

    def test_movie_api_accepts_batch_parameters_and_reports_invalid_values(self):
        import api_server
        client = api_server.app.test_client()
        with patch.object(api_server, "movie_catalog", return_value={"series": [], "has_more": False}) as catalog:
            response = client.post("/webhook", json={"action": "get_movies", "page": 2, "limit": 24})
            self.assertEqual(response.status_code, 200)
            catalog.assert_called_once_with(None, None, limit=24, page=2)
        with patch.object(api_server, "movie_catalog", side_effect=ValueError("页码无效")):
            self.assertEqual(client.post("/webhook", json={"action": "get_movies", "page": False}).status_code, 400)


if __name__ == "__main__":
    unittest.main()

