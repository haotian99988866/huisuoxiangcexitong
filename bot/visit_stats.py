import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from db import get_conn

INIT_DATA_MAX_AGE_SECONDS = 24 * 3600
DEDUPE_MINUTES = 5
RETENTION_DAYS = 90

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS page_visits (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    tg_user_id BIGINT NOT NULL,
    username VARCHAR(64) NULL,
    display_name VARCHAR(128) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_page_visits_time (created_at),
    INDEX idx_page_visits_user (tg_user_id, created_at)
)
"""


def ensure_table():
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


def verify_init_data(init_data, bot_token):
    """校验 Telegram 小程序的 initData 签名,通过则返回用户信息。"""
    if not init_data or not bot_token or len(init_data) > 4096:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    try:
        if time.time() - int(pairs.get("auth_date", "0")) > INIT_DATA_MAX_AGE_SECONDS:
            return None
        user = json.loads(pairs.get("user", ""))
        int(user["id"])
    except (ValueError, KeyError, TypeError):
        return None
    return user


def record_visit(user):
    """同一用户 5 分钟内重复打开只算一次,避免刷新造成虚高。"""
    name = " ".join(
        part for part in (user.get("first_name"), user.get("last_name")) if part
    )
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM page_visits WHERE tg_user_id = %s "
                "AND created_at > DATE_SUB(NOW(), INTERVAL %s MINUTE) LIMIT 1",
                (user["id"], DEDUPE_MINUTES),
            )
            if cursor.fetchone():
                return False
            cursor.execute(
                "INSERT INTO page_visits (tg_user_id, username, display_name) "
                "VALUES (%s, %s, %s)",
                (user["id"], (user.get("username") or "")[:64], name[:128]),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_old_visits(retention_days=RETENTION_DAYS):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM page_visits WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
                (retention_days,),
            )
            count = cursor.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def _exclude_admins():
    ids = sorted(admin_ids())
    if not ids:
        return "1=1"
    return "tg_user_id NOT IN (" + ",".join(str(i) for i in ids) + ")"


def get_stats():
    exclude = _exclude_admins()
    periods = (
        ("今日", "created_at >= CURDATE()"),
        ("昨日", "created_at >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND created_at < CURDATE()"),
        ("近7天", "created_at >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)"),
        ("近30天", "created_at >= DATE_SUB(CURDATE(), INTERVAL 29 DAY)"),
        ("累计", "1=1"),
    )
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            result = []
            for label, condition in periods:
                cursor.execute(
                    f"SELECT COUNT(*) AS visits, COUNT(DISTINCT tg_user_id) AS users "
                    f"FROM page_visits WHERE ({condition}) AND {exclude}"
                )
                row = cursor.fetchone()
                result.append((label, row["users"], row["visits"]))
            cursor.execute(
                "SELECT COUNT(DISTINCT tg_user_id) AS n FROM page_visits "
                f"WHERE created_at > DATE_SUB(NOW(), INTERVAL 30 MINUTE) AND {exclude}"
            )
            recent = cursor.fetchone()["n"]
            cursor.execute(
                "SELECT HOUR(created_at) AS h, COUNT(*) AS n FROM page_visits "
                f"WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 6 DAY) AND {exclude} "
                "GROUP BY h ORDER BY n DESC LIMIT 3"
            )
            peak = [(row["h"], row["n"]) for row in cursor.fetchall()]
            cursor.execute(
                "SELECT tg_user_id, MAX(username) AS username, "
                "MAX(display_name) AS display_name, COUNT(*) AS n "
                "FROM page_visits "
                f"WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 6 DAY) AND {exclude} "
                "GROUP BY tg_user_id ORDER BY n DESC LIMIT 5"
            )
            top = cursor.fetchall()
            cursor.execute(
                "SELECT tg_user_id, username, display_name, created_at "
                f"FROM page_visits WHERE {exclude} "
                "ORDER BY created_at DESC LIMIT 10"
            )
            latest = cursor.fetchall()
    finally:
        conn.close()
    return result, recent, peak, top, latest


def _who(row):
    if row["username"]:
        return f"@{row['username']}"
    return row["display_name"] or str(row["tg_user_id"])


def format_stats():
    result, recent, peak, top, latest = get_stats()
    lines = ["📊 小程序访问统计（不含你自己）", "", "时间段 ｜ 人数 ｜ 次数"]
    for label, users, visits in result:
        lines.append(f"{label} ｜ {users} 人 ｜ {visits} 次")
    lines += ["", f"🟢 最近30分钟活跃：{recent} 人"]
    if peak:
        lines.append(
            "⏰ 近7天高峰时段：" + "、".join(f"{h}点({n}次)" for h, n in peak)
        )
    if top:
        lines += ["", "🔥 近7天最常访问："]
        lines += [f"{_who(row)}：{row['n']} 次" for row in top]
    lines += ["", "🕒 最近浏览记录："]
    if latest:
        lines += [
            f"{row['created_at']:%m-%d %H:%M}  {_who(row)}" for row in latest
        ]
    else:
        lines.append("暂无客户访问")
    lines += ["", "说明：人数按 Telegram 账号去重；同一人5分钟内重复打开只算1次。"]
    return "\n".join(lines)


def admin_ids():
    return {
        int(value)
        for value in os.getenv("ADMIN_USER_IDS", "").replace(" ", "").split(",")
        if value.isdigit()
    }
