"""名玺频道人员相册索引：工号 → 当前仍存在的频道消息 ID。"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

import pymysql

from db import get_conn

logger = logging.getLogger(__name__)

MINGXI_CHANNEL_ID = "-1002323001411"
PLATE_VENUE_ID = 2
# 用户要求：2月21日以后的照片收录；可点链接挂出勤文末
RECENT_COLLECTION_SINCE = date(2026, 2, 21)
# 本轮频道重发后仍存活帖的大致下限（重建索引起点）
LIVE_ALBUM_MIN_MSG_ID = 10390

def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mingxi_channel_album (
                    channel_id VARCHAR(32) NOT NULL,
                    code VARCHAR(16) NOT NULL,
                    msg_id BIGINT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (channel_id, code),
                    KEY idx_mingxi_album_msg (channel_id, msg_id)
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def extract_code_from_caption(caption: str):
    text = (caption or "").strip()
    if not text:
        return None
    # 优先带标签
    labeled = re.search(r"(?:工号|名玺牌|日本|韩国)\s*[:：]?\s*(\d{1,4})", text)
    if labeled:
        return labeled.group(1)
    # 否则取首个 2–4 位独立数字（避免吃到价格）
    for match in re.finditer(r"(?<!\d)(\d{2,4})(?!\d)", text):
        code = match.group(1)
        # 跳过明显年份
        if code.startswith("20") and len(code) == 4:
            continue
        return code
    # 日韩一位补零：日本：06 已在 labeled；裸 06
    one = re.search(r"(?<!\d)(\d)(?!\d)", text)
    if one:
        return one.group(1).zfill(2)
    return None


def upsert_album_message(code, msg_id, channel_id=MINGXI_CHANNEL_ID):
    code = str(code or "").strip()
    if not code or not msg_id:
        return False
    ensure_tables()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mingxi_channel_album (channel_id, code, msg_id)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    msg_id = IF(VALUES(msg_id) >= msg_id, VALUES(msg_id), msg_id)
                """,
                (str(channel_id), code, int(msg_id)),
            )
        conn.commit()
        return True
    except pymysql.MySQLError:
        conn.rollback()
        logger.exception("Mingxi album index upsert failed code=%s msg=%s", code, msg_id)
        return False
    finally:
        conn.close()


def lookup_album_msg_ids(codes, channel_id=MINGXI_CHANNEL_ID):
    wanted = [str(c).strip() for c in codes if str(c).strip()]
    if not wanted:
        return {}
    ensure_tables()
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(wanted))
            cur.execute(
                f"""
                SELECT code, msg_id FROM mingxi_channel_album
                WHERE channel_id = %s AND code IN ({placeholders})
                """,
                (str(channel_id), *wanted),
            )
            return {row["code"]: int(row["msg_id"]) for row in cur.fetchall()}
    except pymysql.MySQLError:
        logger.exception("Mingxi album index lookup failed")
        return {}
    finally:
        if conn is not None:
            conn.close()


def list_album_entries(channel_id=MINGXI_CHANNEL_ID):
    """全部已索引人员：[(code, msg_id), ...]，工号排序。"""
    ensure_tables()
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT code, msg_id FROM mingxi_channel_album
                WHERE channel_id = %s
                ORDER BY
                    LENGTH(code),
                    CAST(code AS UNSIGNED),
                    code
                """,
                (str(channel_id),),
            )
            return [(row["code"], int(row["msg_id"])) for row in cur.fetchall()]
    except pymysql.MySQLError:
        logger.exception("Mingxi album list failed")
        return []
    finally:
        if conn is not None:
            conn.close()


def _as_since_datetime(since):
    if isinstance(since, datetime):
        return since
    if isinstance(since, date):
        return datetime(since.year, since.month, since.day)
    return datetime(RECENT_COLLECTION_SINCE.year, RECENT_COLLECTION_SINCE.month, RECENT_COLLECTION_SINCE.day)


def sync_album_from_matched_photos(
    channel_id=MINGXI_CHANNEL_ID,
    *,
    since=RECENT_COLLECTION_SINCE,
    min_msg_id=LIVE_ALBUM_MIN_MSG_ID,
):
    """把近期已匹配且消息号仍像现帖的照片写进相册索引。"""
    ensure_tables()
    since_dt = _as_since_datetime(since)
    conn = None
    synced = 0
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT technician_code AS code, MAX(msg_id) AS msg_id
                FROM technician_photos
                WHERE venue_id = %s
                  AND status = 'matched'
                  AND technician_code IS NOT NULL
                  AND technician_code != ''
                  AND created_at >= %s
                  AND msg_id >= %s
                  AND channel_id = %s
                GROUP BY technician_code
                """,
                (PLATE_VENUE_ID, since_dt, int(min_msg_id), str(channel_id)),
            )
            rows = cur.fetchall()
        for row in rows:
            if upsert_album_message(row["code"], row["msg_id"], channel_id=channel_id):
                synced += 1
        return synced
    except pymysql.MySQLError:
        logger.exception("Mingxi album sync from photos failed")
        return synced
    finally:
        if conn is not None:
            conn.close()


