import os
import re
from datetime import date, datetime

import pymysql
from dotenv import load_dotenv

load_dotenv()

CODE_PATTERN = re.compile(
    r"(?<!\d)(\d{2,3})(?!\d)"
    r"(?:\s*(?:🇻🇳|🇨🇳|🇰🇭|🇹🇭|🇱🇦|🇲🇲|🇵🇭|🇺🇸|🇬🇧|[（(]男[）)]))?"
)
CODE_EXCLUDED_LINE = re.compile(
    r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|"
    r"[$￥¥]|\d+\s*分钟|服务项目"
)
SERVICE_SECTION_MARKERS = (
    (re.compile(r"小项"), "small"),
    (re.compile(r"中项"), "medium"),
    (re.compile(r"高项"), "high"),
    (re.compile(r"中医按摩"), "tcm"),
    (re.compile(r"修脚师"), "pedicure"),
)


def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "venue_db"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def parse_attendance_text(text):
    result = {
        "duty_date": None,
        "duty_shift": "晚班",
        "codes": [],
        "service_codes": {},
    }

    date_match = re.search(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日|(\d{4})-(\d{1,2})-(\d{1,2})",
        text,
    )
    if date_match:
        parts = date_match.groups()
        year, month, day = map(int, parts[:3] if parts[0] else parts[3:])
        try:
            result["duty_date"] = date(year, month, day)
        except ValueError:
            return result

    shift_match = re.search(r"(早班|晚班)\s*出勤", text)
    if shift_match:
        result["duty_shift"] = shift_match.group(1)

    codes = []
    seen = set()
    for line in text.splitlines():
        if CODE_EXCLUDED_LINE.search(line):
            continue

        for match in CODE_PATTERN.finditer(line):
            code = match.group(1)
            if code not in seen:
                seen.add(code)
                codes.append(code)

    result["codes"] = codes
    service_codes = {}
    current_category = None
    for line in text.splitlines():
        stripped_line = line.strip()
        category = next(
            (
                category
                for marker, category in SERVICE_SECTION_MARKERS
                if marker.search(stripped_line)
                and not CODE_PATTERN.search(stripped_line)
            ),
            None,
        )
        if category is not None:
            current_category = category
            service_codes.setdefault(category, [])
            continue
        if current_category is None or CODE_EXCLUDED_LINE.search(line):
            continue
        for match in CODE_PATTERN.finditer(line):
            code = match.group(1)
            if code not in service_codes[current_category]:
                service_codes[current_category].append(code)

    result["service_codes"] = service_codes
    return result


def save_daily_attendance(venue_id: int, text: str):
    data = parse_attendance_text(text)
    if not data["duty_date"]:
        return False, "❌ 未解析到有效日期"
    if not data["codes"]:
        return False, "❌ 未解析到任何技师工号"

    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO attendance_daily (venue_id, duty_date, duty_shift, technician_codes)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                technician_codes = VALUES(technician_codes),
                created_at = CURRENT_TIMESTAMP
            """,
            (
                venue_id,
                data["duty_date"],
                data["duty_shift"],
                ",".join(data["codes"]),
            ),
        )
        for category, technician_codes in data["service_codes"].items():
            cur.execute(
                """
                DELETE st
                FROM service_item_technicians st
                JOIN service_items si ON si.id = st.service_item_id
                WHERE si.venue_id = %s
                      AND si.category = %s
                      AND st.duty_date = %s
                """,
                (venue_id, category, data["duty_date"]),
            )
            if not technician_codes:
                continue

            cur.execute(
                "SELECT id FROM service_items WHERE venue_id = %s AND category = %s",
                (venue_id, category),
            )
            service_item_ids = [row["id"] for row in cur.fetchall()]
            mappings = [
                (service_item_id, data["duty_date"], technician_code)
                for service_item_id in service_item_ids
                for technician_code in technician_codes
            ]
            if mappings:
                cur.executemany(
                    """
                    INSERT INTO service_item_technicians
                        (service_item_id, duty_date, technician_code)
                    VALUES (%s, %s, %s)
                    """,
                    mappings,
                )
        conn.commit()
        return (
            True,
            f"✅ 已保存 {data['duty_date']} {data['duty_shift']}："
            f"共 {len(data['codes'])} 人",
        )
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        conn.close()
