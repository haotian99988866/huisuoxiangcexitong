"""名玺频道人员相册索引：工号 → 当前仍存在的频道消息 ID。"""
from __future__ import annotations

import logging
import re

import pymysql

from db import get_conn

logger = logging.getLogger(__name__)

MINGXI_CHANNEL_ID = "-1002323001411"
PLATE_VENUE_ID = 2

# caption 常见：987 / 日本：06 / 工号：5555 / 名玺牌：3333 / 7026 特技
_CAPTION_CODE = re.compile(
    r"(?:工号|名玺牌|日本|韩国)\s*[:：]?\s*(\d{1,4})"
    r"|(?<!\d)(\d{2,4})(?!\d)"
)


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


def rebuild_index_from_channel(bot, *, start_msg=10390, end_msg=10488, dig_chat_id=None):
    """转发探测频道现帖，按 caption 重建索引（不依赖旧库 msg_id）。"""
    from visit_stats import admin_ids

    ensure_tables()
    target = dig_chat_id or next(iter(admin_ids() or []), None)
    if not target:
        raise RuntimeError("无可用私聊用于探测频道消息")
    channel_id = MINGXI_CHANNEL_ID
    indexed = 0
    for msg_id in range(int(start_msg), int(end_msg) + 1):
        try:
            sent = bot.forward_message(
                target, channel_id, msg_id, disable_notification=True
            )
        except Exception:
            continue
        try:
            if not (sent.photo or sent.video):
                continue
            caption = sent.caption or ""
            code = extract_code_from_caption(caption)
            if not code:
                continue
            if upsert_album_message(code, msg_id, channel_id):
                indexed += 1
                logger.info("Mingxi album index %s -> %s", code, msg_id)
        finally:
            try:
                bot.delete_message(target, sent.message_id)
            except Exception:
                pass
    return indexed
