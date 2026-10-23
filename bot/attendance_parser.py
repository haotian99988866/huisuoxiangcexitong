import os
import re
from datetime import date

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
PLATE_CODE_VENUE_ID = 2
PLATE_CODE_PATTERN = re.compile(r"(?<!\d)(\d+)\s*牌\s*(\d{2,4})(?!\d)")
PLATE_SERVICE_PATTERN = re.compile(
    r"三通|潮喷|(?<!\w)SM(?!\w)|双仪",
    re.IGNORECASE,
)
PLATE_SERVICE_END_MARKER = "包夜简介"


def _parse_delimited_plate_groups(text):
    headings = list(re.finditer(r"✰([^✰]+)✰", text))
    if not headings:
        return None

    groups = []
    roster_codes = []
    first_roster_index = None
    for index, heading in enumerate(headings):
        title = re.sub(r"[^\w]", "", heading.group(1))
        if PLATE_SERVICE_END_MARKER in title:
            break

        section_end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(text)
        )
        section = text[heading.end() : section_end]
        plate_match = re.search(r"(\d+)(?:号)?牌", title)
        if plate_match:
            group_name = f"{plate_match.group(1)}号牌"
            codes = list(
                dict.fromkeys(
                    match.group(2)
                    for match in PLATE_CODE_PATTERN.finditer(section)
                )
            )
            if codes:
                groups.append(
                    {
                        "group_type": "roster",
                        "group_name": group_name,
                        "technician_codes": codes,
                    }
                )
                roster_codes.extend(codes)
                if first_roster_index is None:
                    first_roster_index = index
        elif first_roster_index is None and "俄罗斯" in title:
            russian_match = re.search(r"俄罗斯\s*[:：]\s*(\d{2,4})(?!\d)", section)
            if russian_match:
                code = russian_match.group(1)
                groups.append(
                    {
                        "group_type": "roster",
                        "group_name": "俄罗斯",
                        "technician_codes": [code],
                    }
                )
                roster_codes.append(code)
        elif first_roster_index is not None:
            codes = list(
                dict.fromkeys(
                    match.group(2)
                    for match in PLATE_CODE_PATTERN.finditer(section)
                )
            )
            if codes:
                group_name = title[2:] if title.startswith("名玺") else title
                groups.append(
                    {
                        "group_type": "service",
                        "group_name": group_name,
                        "technician_codes": codes,
                    }
                )

    return groups, list(dict.fromkeys(roster_codes))


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


def parse_plate_attendance_text(text):
    result = {
        "duty_date": None,
        "duty_shift": "实时出勤",
        "codes": [],
        "service_codes": {},
        "attendance_groups": [],
    }

    date_match = re.search(
        r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})\s*日|"
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
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

    delimited_groups = _parse_delimited_plate_groups(text)
    if delimited_groups is not None:
        result["attendance_groups"], result["codes"] = delimited_groups
        return result

    service_markers = list(PLATE_SERVICE_PATTERN.finditer(text))
    roster_text = text[:service_markers[0].start()] if service_markers else text
    info_start = roster_text.find(PLATE_SERVICE_END_MARKER)
    if info_start >= 0:
        roster_text = roster_text[:info_start]

    roster_groups = {}
    roster_group_positions = {}
    for match in PLATE_CODE_PATTERN.finditer(roster_text):
        plate, code = match.groups()
        group_name = f"{plate}号牌"
        roster_groups.setdefault(group_name, [])
        roster_group_positions.setdefault(group_name, match.start())
        if code not in roster_groups[group_name]:
            roster_groups[group_name].append(code)

    russian_match = re.search(r"俄罗斯\s*[:：]\s*(\d{2,4})(?!\d)", roster_text)
    if russian_match:
        roster_groups.setdefault("俄罗斯", []).append(russian_match.group(1))
        roster_group_positions.setdefault("俄罗斯", russian_match.start())
    roster_groups = dict(
        sorted(
            roster_groups.items(),
            key=lambda group: roster_group_positions[group[0]],
        )
    )

    result["attendance_groups"] = [
        {
            "group_type": "roster",
            "group_name": group_name,
            "technician_codes": codes,
        }
        for group_name, codes in roster_groups.items()
    ]
    result["codes"] = list(
        dict.fromkeys(
            code for codes in roster_groups.values() for code in codes
        )
    )

    for index, marker in enumerate(service_markers):
        section_end = (
            service_markers[index + 1].start()
            if index + 1 < len(service_markers)
            else len(text)
        )
        info_start = text.find(PLATE_SERVICE_END_MARKER, marker.end(), section_end)
        if info_start >= 0:
            section_end = info_start
        section = text[marker.end() : section_end]
        codes = list(
            dict.fromkeys(
                match.group(2)
                for match in PLATE_CODE_PATTERN.finditer(section)
            )
        )
        result["attendance_groups"].append(
            {
                "group_type": "service",
                "group_name": (
                    marker.group(0).upper()
                    if marker.group(0).upper() == "SM"
                    else marker.group(0)
                ),
                "technician_codes": codes,
            }
        )

    return result


def save_daily_attendance(venue_id: int, text: str):
    data = (
        parse_plate_attendance_text(text)
        if venue_id == PLATE_CODE_VENUE_ID
        else parse_attendance_text(text)
    )
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
        if venue_id == PLATE_CODE_VENUE_ID:
            cur.execute(
                """
                DELETE FROM attendance_groups
                WHERE venue_id = %s AND duty_date = %s
                """,
                (venue_id, data["duty_date"]),
            )
            if data["attendance_groups"]:
                cur.executemany(
                    """
                    INSERT INTO attendance_groups
                        (venue_id, duty_date, group_type, group_name,
                         technician_codes, sort_order)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            venue_id,
                            data["duty_date"],
                            group["group_type"],
                            group["group_name"],
                            ",".join(group["technician_codes"]),
                            index,
                        )
                        for index, group in enumerate(data["attendance_groups"])
                    ],
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
