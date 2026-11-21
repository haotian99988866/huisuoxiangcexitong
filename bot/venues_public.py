"""公开会所列表/照片规则用的纯函数（api 与测试共用）。"""
from technician_photo_ocr import (
    extract_labeled_technician_code,
    extract_technician_profile,
)

DELIVERY_VENUE_IDS = {7, 8, 9, 10}
APPLE_CATEGORY_KEYWORDS = (("小苹果", "small"), ("红苹果", "red"))


def parse_venue(venue):
    venue["search_tags"] = venue["search_tags"].split(",") if venue["search_tags"] else []
    technician_codes = venue.get("technician_codes") or ""
    venue["technician_list"] = [
        code.strip() for code in technician_codes.split(",") if code.strip()
    ]
    venue["on_duty_count"] = len(venue["technician_list"])
    venue["description"] = None
    return venue


def underage_technician_codes(photos):
    underage_codes = set()
    for photo in photos:
        technician_code = photo["technician_code"] or (
            extract_labeled_technician_code(photo["ocr_text"])
        )
        if technician_code is None:
            continue
        profile = extract_technician_profile(
            [photo["ocr_text"] or ""],
            technician_code,
        )
        age = profile.get("age")
        if age and age.isdigit() and int(age) < 18:
            underage_codes.add(technician_code)
    return underage_codes


def confirmed_adult_technician_codes(photos):
    adult_codes = set()
    for photo in photos:
        technician_code = photo["technician_code"] or (
            extract_labeled_technician_code(photo["ocr_text"])
        )
        if technician_code is None:
            continue
        profile = extract_technician_profile(
            [photo["ocr_text"] or ""],
            technician_code,
        )
        age = profile.get("age")
        if age and age.isdigit() and int(age) >= 18:
            adult_codes.add(technician_code)
    return adult_codes


def apple_category(text):
    for keyword, category in APPLE_CATEGORY_KEYWORDS:
        if keyword in text:
            return category
    return None
