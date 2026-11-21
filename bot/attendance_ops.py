"""出勤频道同步：关键词、事件落盘、失败私聊与运营只读统计。"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pymysql
import requests

from db import get_conn
from duty_shift import SHIFT_VENUE_IDS, current_shift_window

logger = logging.getLogger(__name__)

# 频道出勤正文需命中其一；晚班/名单补进以减少漏网
ATTENDANCE_KEYWORDS = ("出勤", "按摩部", "技师", "白班", "晚班", "早班", "名单")

VENUE_LABELS = {
    1: "名玺水汇按摩",
    2: "名玺水汇会所",
    4: "凯迪/大浪淘沙会所",
    5: "大西洋/五象会所",
    6: "帝豪会所",
}


def store_path() -> Path:
    from channel_sync import state_directory

    path = state_directory()
    path.mkdir(parents=True, exist_ok=True)
    return path / "attendance-ops.sqlite3"


def _connect(writable=True):
    path = store_path()
    if writable:
        connection = sqlite3.connect(path, timeout=5)
    else:
        if not path.is_file():
            return None
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_tables(connection=None):
    owned = connection is None
    if owned:
        connection = _connect(writable=True)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venue_id INTEGER NOT NULL,
                venue_name TEXT NOT NULL,
                ok INTEGER NOT NULL,
                message TEXT NOT NULL,
                rematched INTEGER NOT NULL DEFAULT 0,
                pending_left INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_health (
                worker TEXT PRIMARY KEY,
                heartbeat_at REAL NOT NULL,
                last_error_code TEXT,
                last_error_at REAL
            )
            """
        )
        connection.commit()
    finally:
        if owned:
            connection.close()


def record_event(
    venue_id,
    venue_name,
    ok,
    message,
    *,
    rematched=0,
    pending_left=0,
):
    try:
        connection = _connect(writable=True)
        try:
            ensure_tables(connection)
            with connection:
                connection.execute(
                    "INSERT INTO attendance_events"
                    "(venue_id,venue_name,ok,message,rematched,pending_left,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        int(venue_id),
                        str(venue_name or ""),
                        1 if ok else 0,
                        str(message or "")[:500],
                        int(rematched or 0),
                        int(pending_left or 0),
                        time.time(),
                    ),
                )
                connection.execute(
                    "DELETE FROM attendance_events WHERE id NOT IN ("
                    "SELECT id FROM attendance_events ORDER BY id DESC LIMIT 100)"
                )
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Attendance event log unavailable venue=%s", venue_id)


def recent_events(limit=8):
    connection = None
    try:
        connection = _connect(writable=False)
        if connection is None:
            return []
        rows = connection.execute(
            "SELECT venue_id,venue_name,ok,message,rematched,pending_left,created_at "
            "FROM attendance_events ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 50)),),
        ).fetchall()
        return [
            {
                "venue_id": row["venue_id"],
                "venue_name": row["venue_name"],
                "ok": bool(row["ok"]),
                "message": row["message"],
                "rematched": row["rematched"],
                "pending_left": row["pending_left"],
                "created_at": datetime.fromtimestamp(
                    row["created_at"], timezone.utc
                ).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            for row in rows
        ]
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        logger.exception("Attendance event read unavailable")
        return []
    finally:
        if connection is not None:
            connection.close()


def record_staff_archive_heartbeat(interval=15):
    from operations_health import record_worker_heartbeat

    connection = None
    try:
        connection = _connect(writable=True)
        ensure_tables(connection)
        record_worker_heartbeat(connection, "staff_archive", interval=interval)
    except sqlite3.Error:
        logger.exception("Staff archive heartbeat unavailable")
    finally:
        if connection is not None:
            connection.close()


def record_staff_archive_failure(error):
    from operations_health import record_worker_failure

    connection = None
    try:
        connection = _connect(writable=True)
        ensure_tables(connection)
        record_worker_failure(connection, "staff_archive", error)
    except sqlite3.Error:
        logger.exception("Staff archive failure status unavailable")
    finally:
        if connection is not None:
            connection.close()


def staff_archive_component():
    from operations_health import HEARTBEAT_STALE_SECONDS, _iso_timestamp

    path = store_path()
    result = {
        "key": "staff_archive",
        "name": "人员档案同步",
        "enabled": True,
        "state": "not_initialized",
        "last_heartbeat": None,
        "heartbeat_age_seconds": None,
        "last_error_code": None,
        "last_error_at": None,
        "counts": {},
        "note": None,
    }
    if not path.is_file():
        result["note"] = "尚未收到档案同步心跳"
        result["state"] = "not_started"
        return result
    connection = None
    try:
        connection = _connect(writable=False)
        if connection is None:
            result["state"] = "unavailable"
            result["note"] = "档案同步状态读取失败"
            return result
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "worker_health" not in tables:
            result["state"] = "not_started"
            result["note"] = "尚未收到档案同步心跳"
            return result
        heartbeat = connection.execute(
            "SELECT heartbeat_at,last_error_code,last_error_at FROM worker_health "
            "WHERE worker=?",
            ("staff_archive",),
        ).fetchone()
        if not heartbeat:
            result["state"] = "not_started"
            result["note"] = "尚未收到档案同步心跳"
            return result
        age = max(0, int(time.time() - heartbeat["heartbeat_at"]))
        result["last_heartbeat"] = _iso_timestamp(heartbeat["heartbeat_at"])
        result["heartbeat_age_seconds"] = age
        result["last_error_code"] = heartbeat["last_error_code"]
        result["last_error_at"] = _iso_timestamp(heartbeat["last_error_at"])
        result["state"] = "active" if age <= HEARTBEAT_STALE_SECONDS else "stale"
        return result
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        logger.exception("Staff archive health unavailable")
        result["state"] = "unavailable"
        result["note"] = "档案同步状态读取失败"
        return result
    finally:
        if connection is not None:
            connection.close()


def pending_photos_in_shift(venue_id, start=None, end=None):
    if start is None or end is None:
        start, end, _ = current_shift_window()
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS pending
                FROM technician_photos
                WHERE venue_id = %s AND status = 'pending_review'
                      AND created_at >= %s AND created_at < %s
                """,
                (venue_id, start, end),
            )
            return int((cur.fetchone() or {}).get("pending") or 0)
    except pymysql.MySQLError:
        logger.exception("Pending photo count unavailable venue=%s", venue_id)
        return None
    finally:
        if conn is not None:
            conn.close()


def shift_attendance_snapshot():
    """本班出勤是否入库 + 待匹配照片，供运营健康面板。"""
    start, end, shift = current_shift_window()
    conn = None
    venues = []
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            for venue_id in sorted(SHIFT_VENUE_IDS):
                cur.execute(
                    """
                    SELECT duty_date, duty_shift, technician_codes, created_at
                    FROM attendance_daily
                    WHERE venue_id = %s
                          AND created_at >= %s AND created_at < %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (venue_id, start, end),
                )
                row = cur.fetchone()
                codes = []
                if row and row.get("technician_codes"):
                    codes = [
                        code.strip()
                        for code in str(row["technician_codes"]).split(",")
                        if code.strip()
                    ]
                cur.execute(
                    """
                    SELECT COUNT(*) AS pending
                    FROM technician_photos
                    WHERE venue_id = %s AND status = 'pending_review'
                          AND created_at >= %s AND created_at < %s
                    """,
                    (venue_id, start, end),
                )
                pending = int((cur.fetchone() or {}).get("pending") or 0)
                created = row.get("created_at") if row else None
                venues.append(
                    {
                        "venue_id": venue_id,
                        "venue_name": VENUE_LABELS.get(venue_id, f"会所{venue_id}"),
                        "shift": shift,
                        "roster_available": bool(codes),
                        "code_count": len(codes),
                        "duty_date": str(row["duty_date"]) if row else None,
                        "duty_shift": row["duty_shift"] if row else None,
                        "created_at": (
                            created.isoformat(timespec="seconds")
                            if hasattr(created, "isoformat")
                            else str(created) if created else None
                        ),
                        "pending_review": pending,
                    }
                )
        return {
            "shift": shift,
            "window_start": start.isoformat(timespec="seconds"),
            "window_end": end.isoformat(timespec="seconds"),
            "venues": venues,
            "recent_events": recent_events(8),
        }
    except pymysql.MySQLError:
        logger.exception("Attendance shift snapshot unavailable")
        return {
            "shift": shift,
            "window_start": start.isoformat(timespec="seconds"),
            "window_end": end.isoformat(timespec="seconds"),
            "venues": [],
            "recent_events": recent_events(8),
            "error": "unavailable",
        }
    finally:
        if conn is not None:
            conn.close()


def notify_attendance_failure(venue_name, reason, preview=""):
    """解析失败时私聊管理员，避免频道已发、系统静默漏吃。"""
    from visit_stats import admin_ids

    recipients = admin_ids()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not recipients:
        logger.error(
            "Attendance failure notify is not configured venue=%s", venue_name
        )
        return False
    text = (
        f"出勤同步失败：{venue_name}\n"
        f"原因：{reason}\n"
        "请核对频道原文是否含日期与工号；修复后可重发或联系补匹配。"
    )
    snippet = (preview or "").strip().replace("\n", " ")
    if snippet:
        text += f"\n摘要：{snippet[:120]}"
    failed = []
    for admin_id in recipients:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": admin_id, "text": text},
                timeout=10,
            )
            if response.status_code != 200 or response.json().get("ok") is not True:
                failed.append(admin_id)
                logger.error(
                    "Attendance failure notify rejected venue=%s admin=%s status=%s",
                    venue_name,
                    admin_id,
                    response.status_code,
                )
        except (requests.RequestException, ValueError):
            failed.append(admin_id)
            logger.exception(
                "Attendance failure notify unavailable venue=%s admin=%s",
                venue_name,
                admin_id,
            )
    return not failed
