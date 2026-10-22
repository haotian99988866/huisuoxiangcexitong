import os
import re
from datetime import date, datetime

import pymysql
from dotenv import load_dotenv

load_dotenv()


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

    code_pattern = re.compile(
        r"(?<!\d)(\d{2,3})(?!\d)"
        r"(?:\s*(?:🇻🇳|🇨🇳|🇰🇭|🇹🇭|🇱🇦|🇲🇲|🇵🇭|🇺🇸|🇬🇧|[（(]男[）)]))?"
    )
    codes = []
    seen = set()
    for line in text.splitlines():
        if re.search(
            r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|"
            r"[$￥¥]|\d+\s*分钟|小项|中项|高项|服务项目",
            line,
        ):
            continue

        for match in code_pattern.finditer(line):
            code = match.group(1)
            if code not in seen:
                seen.add(code)
                codes.append(code)

    result["codes"] = codes
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
            ON DUPLICATE KEY UPDATE technician_codes = VALUES(technician_codes)
            """,
            (
                venue_id,
                data["duty_date"],
                data["duty_shift"],
                ",".join(data["codes"]),
            ),
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
