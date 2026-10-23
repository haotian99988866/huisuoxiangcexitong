import unittest
from unittest.mock import patch

from technician_photo_ocr import (
    classify_ocr_detections,
    extract_technician_profile,
    match_pending_technician_photos,
    recognize_technician_code,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.update_parameters = []
        self.rowcount = 0

    def execute(self, query, parameters=None):
        if query.lstrip().startswith("UPDATE"):
            self.update_parameters.append(parameters)
            self.rowcount = 1

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows):
        self.fake_cursor = FakeCursor(rows)

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class ClassifyOcrDetectionsTests(unittest.TestCase):
    def test_extracts_profile_fields_and_deduplicates_service_description(self):
        profile = extract_technician_profile(
            [
                "工号:288\n年龄：24\n身高:165\n胸围:C(真)\n体重:49KG\n"
                "籍贯:中国\n乖巧懂事,群P,深喉旋转",
                "工号:288\n年龄：24\n身高:165\n胸围:C(真)\n体重:49KG\n"
                "籍贯:中国\n乖巧懂事,群P,深喉旋转\n服务超群,独家口技",
            ],
            "288",
        )

        self.assertEqual(
            profile,
            {
                "technician_code": "288",
                "age": "24",
                "height": "165",
                "bust": "C(真)",
                "weight": "49KG",
                "origin": "中国",
                "service_description": "乖巧懂事,群P,深喉旋转\n服务超群,独家口技",
            },
        )

    def test_matches_one_confident_code_in_roster(self):
        result = classify_ocr_detections(
            [([[]], "098", 0.95)],
            ["098", "021"],
        )

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["technician_code"], "098")

    def test_matches_code_without_leading_zero_to_roster_format(self):
        result = classify_ocr_detections(
            [([[]], "21", 0.95)],
            ["021", "050"],
        )

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["technician_code"], "021")

    def test_matches_plate_code_without_treating_plate_number_as_code(self):
        result = classify_ocr_detections(
            [([[]], "9牌969", 0.95)],
            ["969"],
            venue_id=2,
        )

        self.assertEqual(result["candidate_codes"], ["969"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["technician_code"], "969")

    def test_prefers_labeled_plate_code_over_profile_numbers(self):
        result = classify_ocr_detections(
            [
                ([[]], "工号:288", 0.96),
                ([[]], "年龄:24", 0.99),
                ([[]], "身高:165", 0.98),
                ([[]], "体重:49KG", 0.97),
            ],
            ["288", "286"],
            venue_id=2,
        )

        self.assertEqual(result["candidate_codes"], ["288"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["technician_code"], "288")

    def test_keeps_ambiguous_normalized_roster_codes_for_review(self):
        result = classify_ocr_detections(
            [([[]], "21", 0.95)],
            ["021", "21"],
        )

        self.assertEqual(result["status"], "pending_review")
        self.assertIsNone(result["technician_code"])

    def test_leaves_low_confidence_code_for_review(self):
        result = classify_ocr_detections(
            [([[]], "098", 0.69)],
            ["098"],
        )

        self.assertEqual(result["status"], "pending_review")
        self.assertIsNone(result["technician_code"])

    def test_leaves_multiple_codes_for_review(self):
        result = classify_ocr_detections(
            [([[]], "098 021", 0.96)],
            ["098", "021"],
        )

        self.assertEqual(result["status"], "pending_review")
        self.assertEqual(result["candidate_codes"], ["021", "098"])

    def test_leaves_code_outside_roster_for_review(self):
        result = classify_ocr_detections(
            [([[]], "099", 0.96)],
            ["098"],
        )

        self.assertEqual(result["status"], "pending_review")

    def test_keeps_unreadable_photo_for_review(self):
        result = classify_ocr_detections([], ["098"])

        self.assertEqual(result["status"], "pending_review")
        self.assertEqual(result["candidate_codes"], [])

    def test_does_not_extract_digits_from_longer_numbers(self):
        result = classify_ocr_detections(
            [([[]], "20261019", 0.99)],
            ["019"],
        )

        self.assertEqual(result["candidate_codes"], [])
        self.assertEqual(result["status"], "pending_review")

    def test_invalid_image_is_saved_as_review_candidate(self):
        result = recognize_technician_code(b"not an image", ["098"])

        self.assertEqual(result["status"], "pending_review")
        self.assertEqual(result["ocr_error"], "UnidentifiedImageError")

    @patch("technician_photo_ocr.get_conn")
    def test_announcement_reconciles_confident_photo_posted_first(self, get_conn):
        connection = FakeConnection(
            [{"id": 12, "candidate_codes": "098", "ocr_confidence": 0.95}]
        )
        get_conn.return_value = connection

        matched = match_pending_technician_photos(
            1,
            "2026-10-23",
            "2026-10-24",
            ["098", "021"],
        )

        self.assertEqual(matched, 1)
        self.assertEqual(
            connection.fake_cursor.update_parameters[0],
            ("2026-10-24", "098", "matched", 12),
        )

    @patch("technician_photo_ocr.get_conn")
    def test_announcement_reconciles_labeled_plate_code_with_profile_numbers(
        self, get_conn
    ):
        connection = FakeConnection(
            [
                {
                    "id": 62,
                    "candidate_codes": "165,24,288,49",
                    "ocr_confidence": 0.96662,
                    "ocr_text": "工号:288\n年龄:24\n身高:165\n体重:49KG",
                }
            ]
        )
        get_conn.return_value = connection

        matched = match_pending_technician_photos(
            2,
            "2026-10-23",
            "2026-10-23",
            ["288", "286"],
        )

        self.assertEqual(matched, 1)
        self.assertEqual(
            connection.fake_cursor.update_parameters[0],
            ("2026-10-23", "288", "matched", 62),
        )


if __name__ == "__main__":
    unittest.main()
