import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from api_server import build_delivery_posts, filter_adult_posts
from delivery_age import extract_age, ocr_image_data, ocr_image_text


def row(id_, age=None, group=None, media_type="photo", caption=""):
    return {
        "id": id_,
        "message_id": id_,
        "media_type": media_type,
        "media_group_id": group,
        "caption": caption,
        "age": age,
    }


class ExtractAgeTest(unittest.TestCase):
    def test_labeled_ages(self):
        self.assertEqual(extract_age("工号:S09\n芳龄: 18"), 18)
        self.assertEqual(extract_age("年龄:24\n身高:165"), 24)
        self.assertEqual(extract_age("芳龄：17岁"), 17)

    def test_suffix_age_and_minimum(self):
        self.assertEqual(extract_age("20岁"), 20)
        self.assertEqual(extract_age("年龄:19", "芳龄:17"), 17)

    def test_unknown(self):
        self.assertIsNone(extract_age("身高165 体重45kg"))
        self.assertIsNone(extract_age(None, ""))

    def test_incomplete_ocr_confidence_preserves_all_safety_text_without_reuse(self):
        image = io.BytesIO()
        Image.new("RGB", (2, 2)).save(image, format="PNG")
        output = SimpleNamespace(txts=["工号 530", "年龄 16"], scores=[0.95])
        with patch("delivery_age._get_engine", return_value=lambda _: output):
            text, detections = ocr_image_data(image.getvalue())
            self.assertEqual(text, "工号 530\n年龄 16")
            self.assertIsNone(detections)
            self.assertEqual(ocr_image_text(image.getvalue()), text)


class AdultFilterTest(unittest.TestCase):
    def test_hides_under_18_and_unknown(self):
        rows = [row(4, 17), row(3, None), row(2, 18), row(1, 25)]
        posts = filter_adult_posts(build_delivery_posts(rows))
        self.assertEqual([p["age"] for p in posts], [18, 25])

    def test_album_uses_minimum_age(self):
        rows = [row(2, 17, "g1"), row(1, 25, "g1", caption="介绍")]
        self.assertEqual(filter_adult_posts(build_delivery_posts(rows)), [])

    def test_photo_and_video_group_takes_photo_age(self):
        rows = [row(2, None, "g1", "video"), row(1, 22, "g1")]
        posts = filter_adult_posts(build_delivery_posts(rows))
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(posts[0]["media"]), 2)


if __name__ == "__main__":
    unittest.main()
