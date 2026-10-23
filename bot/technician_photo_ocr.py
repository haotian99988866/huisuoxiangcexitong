import io
import logging
import re
import threading

import numpy as np
import pymysql
import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from rapidocr import RapidOCR

from db import get_conn

logger = logging.getLogger(__name__)
MIN_OCR_CONFIDENCE = 0.7
_engine = None
_engine_lock = threading.Lock()
_code_pattern = re.compile(r"(?<!\d)(\d{2,3})(?!\d)")
_plate_code_pattern = re.compile(r"(?<!\d)(\d{2,4})(?!\d)")
_labeled_plate_code_pattern = re.compile(
    r"(?:工号|编号)\s*[:：]?\s*(\d{2,4})(?!\d)"
)
_profile_patterns = {
    "technician_code": re.compile(r"^\s*(?:工号|编号)\s*[:：]?\s*(\d{2,4})(?!\d)"),
    "age": re.compile(r"^\s*年龄\s*[:：]?\s*(\d{1,2})(?:\s*岁)?"),
    "height": re.compile(r"^\s*身高\s*[:：]?\s*(\d{2,3})(?:\s*cm)?", re.IGNORECASE),
    "bust": re.compile(r"^\s*胸围\s*[:：]?\s*([A-D](?:\s*\([^)]*\))?)", re.IGNORECASE),
    "weight": re.compile(r"^\s*体重\s*[:：]?\s*(\d{2,3}\s*(?:kg|公斤)?)", re.IGNORECASE),
    "origin": re.compile(r"^\s*籍贯\s*[:：]?\s*(.+?)\s*$"),
}
_profile_metadata_line = re.compile(
    r"^\s*(?:名玺水汇|特技名玺|轻度收费|中度收费|重度收费|时间)\b"
)


def _normalize_code(code):
    return str(int(code))


def extract_technician_profile(ocr_texts, technician_code):
    profile = {"technician_code": technician_code}
    service_lines = []
    seen_service_lines = set()

    for text in ocr_texts:
        lines = (text or "").splitlines()
        origin_index = None
        for index, line in enumerate(lines):
            stripped_line = line.strip()
            for field, pattern in _profile_patterns.items():
                match = pattern.search(stripped_line)
                if match and field not in profile:
                    profile[field] = match.group(1).strip()
                if field == "origin" and match:
                    origin_index = index

        if origin_index is None:
            continue

        for line in lines[origin_index + 1:]:
            service_line = line.strip()
            if not service_line or _profile_metadata_line.search(service_line):
                continue
            if service_line not in seen_service_lines:
                seen_service_lines.add(service_line)
                service_lines.append(service_line)

    description = "\n".join(service_lines)
    return {
        **profile,
        "service_description": description[:2000],
    }


def _get_engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = RapidOCR()
    return _engine


def classify_ocr_detections(detections, roster_codes, venue_id=None):
    candidates = {}
    labeled_candidates = {}
    recognized_text = []
    code_pattern = _plate_code_pattern if venue_id == 2 else _code_pattern
    for detection in detections or []:
        text = str(detection[1]).strip()
        confidence = float(detection[2])
        if text:
            recognized_text.append(text)
        if venue_id == 2:
            for code in _labeled_plate_code_pattern.findall(text):
                labeled_candidates[code] = max(
                    labeled_candidates.get(code, 0.0),
                    confidence,
                )
        for code in code_pattern.findall(text):
            candidates[code] = max(candidates.get(code, 0.0), confidence)

    if labeled_candidates:
        candidates = labeled_candidates

    roster_by_normalized = {}
    for code in roster_codes:
        code = str(code).strip()
        if code:
            roster_by_normalized.setdefault(_normalize_code(code), set()).add(code)
    normalized_candidates = {_normalize_code(code) for code in candidates}
    roster_matches = normalized_candidates.intersection(roster_by_normalized)
    unique_roster_codes = (
        roster_by_normalized[next(iter(roster_matches))]
        if len(roster_matches) == 1
        else set()
    )
    matching_confidence = max(
        (
            confidence
            for code, confidence in candidates.items()
            if _normalize_code(code) in roster_matches
        ),
        default=0.0,
    )
    if (
        len(normalized_candidates) == 1
        and len(roster_matches) == 1
        and len(unique_roster_codes) == 1
        and matching_confidence >= MIN_OCR_CONFIDENCE
    ):
        technician_code = next(iter(unique_roster_codes))
        status = "matched"
    else:
        technician_code = None
        status = "pending_review"

    return {
        "ocr_text": "\n".join(recognized_text),
        "candidate_codes": sorted(candidates),
        "ocr_confidence": max(candidates.values(), default=None),
        "ocr_error": None,
        "technician_code": technician_code,
        "status": status,
    }


def recognize_technician_code(image_bytes, roster_codes, venue_id=None):
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image_array = np.asarray(image)
        output = _get_engine()(image_array)
        texts = output.txts if output.txts is not None else []
        scores = output.scores if output.scores is not None else []
        detections = [
            (None, text, confidence)
            for text, confidence in zip(texts, scores)
        ]
    except (
        UnidentifiedImageError,
        OSError,
        RuntimeError,
        ValueError,
        requests.RequestException,
    ) as exc:
        logger.error("Local OCR failed (%s)", type(exc).__name__)
        return {
            **classify_ocr_detections([], roster_codes, venue_id),
            "ocr_error": type(exc).__name__,
        }

    return classify_ocr_detections(detections, roster_codes, venue_id)


def save_technician_photo(
    venue_id: int,
    channel_id: str,
    msg_id: int,
    file_id: str,
    image_bytes: bytes,
    photo_date,
):
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT duty_date, technician_codes
            FROM attendance_daily
            WHERE venue_id = %s AND (duty_date = %s OR DATE(created_at) = %s)
            ORDER BY (duty_date = %s) DESC, created_at DESC
            LIMIT 1
            """,
            (venue_id, photo_date, photo_date, photo_date),
        )
        attendance = cur.fetchone()
    finally:
        if cur is not None:
            cur.close()
        conn.close()

    roster_codes = []
    duty_date = photo_date
    if attendance:
        duty_date = attendance["duty_date"]
        roster_codes = [
            code.strip()
            for code in (attendance["technician_codes"] or "").split(",")
            if code.strip()
        ]

    recognition = recognize_technician_code(image_bytes, roster_codes, venue_id)

    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO technician_photos (
                venue_id, duty_date, technician_code, file_id, channel_id, msg_id,
                ocr_text, candidate_codes, ocr_confidence, ocr_error, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
            """,
            (
                venue_id,
                duty_date,
                recognition["technician_code"],
                file_id,
                channel_id,
                msg_id,
                recognition["ocr_text"],
                ",".join(recognition["candidate_codes"]),
                recognition["ocr_confidence"],
                recognition["ocr_error"],
                recognition["status"],
            ),
        )
        photo_id = cur.lastrowid
        conn.commit()
        return {
            "id": photo_id,
            "duty_date": duty_date,
            **recognition,
        }
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        conn.close()


def match_pending_technician_photos(
    venue_id: int, photo_date, duty_date, roster_codes
):
    roster_by_normalized = {}
    for code in roster_codes:
        code = str(code).strip()
        if code:
            roster_by_normalized.setdefault(_normalize_code(code), set()).add(code)
    if not roster_by_normalized:
        return 0

    conn = get_conn()
    cur = None
    matched_count = 0
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, candidate_codes, ocr_confidence, ocr_text
            FROM technician_photos
            WHERE venue_id = %s AND duty_date = %s
                  AND status = 'pending_review'
            FOR UPDATE
            """,
            (venue_id, photo_date),
        )
        for photo in cur.fetchall():
            candidates = [
                code for code in photo["candidate_codes"].split(",") if code
            ]
            if venue_id == 2:
                labeled_candidates = _labeled_plate_code_pattern.findall(
                    photo["ocr_text"] or ""
                )
                if labeled_candidates:
                    candidates = list(dict.fromkeys(labeled_candidates))
            technician_code = None
            normalized_candidates = {_normalize_code(code) for code in candidates}
            roster_matches = normalized_candidates.intersection(roster_by_normalized)
            if (
                len(normalized_candidates) == 1
                and len(roster_matches) == 1
                and len(roster_by_normalized[next(iter(roster_matches))]) == 1
                and photo["ocr_confidence"] is not None
                and float(photo["ocr_confidence"]) >= MIN_OCR_CONFIDENCE
            ):
                technician_code = next(
                    iter(roster_by_normalized[next(iter(roster_matches))])
                )

            cur.execute(
                """
                UPDATE technician_photos
                SET duty_date = %s, technician_code = %s, status = %s
                WHERE id = %s AND status = 'pending_review'
                """,
                (
                    duty_date,
                    technician_code,
                    "matched" if technician_code else "pending_review",
                    photo["id"],
                ),
            )
            if cur.rowcount and technician_code is not None:
                matched_count += 1
        conn.commit()
        return matched_count
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        conn.close()


def review_pending_photo(photo_id: int, technician_code: str):
    if not re.fullmatch(r"\d{2,3}", technician_code):
        raise ValueError("Technician code must contain 2 or 3 digits")

    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT venue_id, duty_date, status
            FROM technician_photos
            WHERE id = %s
            FOR UPDATE
            """,
            (photo_id,),
        )
        photo = cur.fetchone()
        if photo is None or photo["status"] != "pending_review":
            return False

        cur.execute(
            """
            SELECT technician_codes
            FROM attendance_daily
            WHERE venue_id = %s AND duty_date = %s
            ORDER BY FIELD(duty_shift, '晚班', '早班'), created_at DESC
            LIMIT 1
            """,
            (photo["venue_id"], photo["duty_date"]),
        )
        attendance = cur.fetchone()
        roster_codes = {
            code.strip()
            for code in (attendance["technician_codes"] if attendance else "").split(",")
            if code.strip()
        }
        matching_roster_codes = {
            code for code in roster_codes
            if _normalize_code(code) == _normalize_code(technician_code)
        }
        if len(matching_roster_codes) != 1:
            raise ValueError("Technician code is not in that attendance roster")
        technician_code = next(iter(matching_roster_codes))

        cur.execute(
            """
            UPDATE technician_photos
            SET technician_code = %s, status = 'matched', reviewed_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'pending_review'
            """,
            (technician_code, photo_id),
        )
        conn.commit()
        return cur.rowcount == 1
    except (pymysql.MySQLError, ValueError):
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        conn.close()
