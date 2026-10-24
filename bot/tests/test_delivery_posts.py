import unittest

from api_server import build_delivery_posts


def row(id_, media_type, group=None, caption=""):
    return {"id": id_, "media_type": media_type, "media_group_id": group, "caption": caption}


class BuildDeliveryPostsTest(unittest.TestCase):
    def test_photo_and_video_group_without_caption_merge(self):
        posts = build_delivery_posts([row(2, "video", "g1"), row(1, "photo", "g1")])
        self.assertEqual(len(posts), 1)
        self.assertEqual([m["type"] for m in posts[0]["media"]], ["photo", "video"])

    def test_photos_only_group_without_caption_stay_separate(self):
        posts = build_delivery_posts([row(2, "photo", "g1"), row(1, "photo", "g1")])
        self.assertEqual(len(posts), 2)

    def test_captioned_group_merges_with_caption(self):
        posts = build_delivery_posts([row(2, "photo", "g1"), row(1, "photo", "g1", "hi")])
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["description"], "hi")


if __name__ == "__main__":
    unittest.main()
