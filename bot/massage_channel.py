"""Idempotent massage-channel staff posts, directory and daily attendance."""
from __future__ import annotations

from datetime import datetime
import html
import logging
import re
import threading
import time

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from db import get_conn
from price_catalog import format_price

logger = logging.getLogger(__name__)

CHANNEL_ID = "-1002324026180"
VENUE_ID = 1
DIRECTORY_MESSAGE_ID = 4939
SYNC_DELAY = 75
EDIT_INTERVAL = 3.5
_timers = {}
_timers_lock = threading.Lock()
_edit_lock = threading.Lock()
_last_edit_at = 0.0


def is_complete_message(text):
    return "【按摩出勤完整版】" in (text or "")


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS massage_channel_posts (
                    staff_id BIGINT NOT NULL PRIMARY KEY,
                    channel_id VARCHAR(32) NOT NULL,
                    code VARCHAR(16) NOT NULL,
                    msg_id BIGINT NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_massage_channel_code (channel_id, code),
                    KEY idx_massage_channel_msg (channel_id, msg_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS massage_channel_state (
                    channel_id VARCHAR(32) NOT NULL,
                    state_key VARCHAR(32) NOT NULL,
                    msg_id BIGINT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (channel_id, state_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                INSERT INTO massage_channel_state(channel_id,state_key,msg_id)
                VALUES(%s,'directory',%s)
                ON DUPLICATE KEY UPDATE msg_id=VALUES(msg_id)
                """,
                (CHANNEL_ID, DIRECTORY_MESSAGE_ID),
            )
        conn.commit()
    finally:
        conn.close()


def register_post(staff_id, code, msg_id, channel_id=CHANNEL_ID):
    ensure_tables()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO massage_channel_posts(staff_id,channel_id,code,msg_id)
                VALUES(%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    channel_id=VALUES(channel_id),code=VALUES(code),msg_id=VALUES(msg_id)
                """,
                (int(staff_id), str(channel_id), str(code), int(msg_id)),
            )
        conn.commit()
    finally:
        conn.close()


def seed_existing_posts(rows, channel_id=CHANNEL_ID):
    ensure_tables()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for code, msg_id in rows:
                cur.execute(
                    """
                    SELECT id FROM staff_members
                    WHERE venue_id=%s AND code=%s
                      AND ended_at IS NULL AND deleted_at IS NULL
                    ORDER BY last_seen DESC,id DESC LIMIT 1
                    """,
                    (VENUE_ID, str(code)),
                )
                member = cur.fetchone()
                if member:
                    cur.execute(
                        """
                        INSERT INTO massage_channel_posts
                            (staff_id,channel_id,code,msg_id)
                        VALUES(%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                            channel_id=VALUES(channel_id),msg_id=VALUES(msg_id)
                        """,
                        (member["id"], str(channel_id), str(code), int(msg_id)),
                    )
        conn.commit()
    finally:
        conn.close()


def _state_message_id(key):
    ensure_tables()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT msg_id FROM massage_channel_state "
                "WHERE channel_id=%s AND state_key=%s",
                (CHANNEL_ID, key),
            )
            row = cur.fetchone()
            return int(row["msg_id"]) if row and row["msg_id"] else None
    finally:
        conn.close()


def _set_state_message_id(key, msg_id):
    ensure_tables()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO massage_channel_state(channel_id,state_key,msg_id)
                VALUES(%s,%s,%s)
                ON DUPLICATE KEY UPDATE msg_id=VALUES(msg_id)
                """,
                (CHANNEL_ID, key, int(msg_id)),
            )
        conn.commit()
    finally:
        conn.close()


def _retry_after(error):
    matched = re.search(r"retry after (\d+)", str(error), re.I)
    return int(matched.group(1)) if matched else None


def _wait_for_edit_slot():
    global _last_edit_at
    with _edit_lock:
        now = time.monotonic()
        wait = max(0.0, _last_edit_at + EDIT_INTERVAL - now)
        if wait:
            time.sleep(wait)
        _last_edit_at = time.monotonic()


def _edit_text(bot, msg_id, text, markup):
    while True:
        try:
            _wait_for_edit_slot()
            return bot.edit_message_text(
                text,
                int(CHANNEL_ID),
                int(msg_id),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=markup,
            )
        except telebot.apihelper.ApiTelegramException as exc:
            wait = _retry_after(exc)
            if wait is None:
                raise
            logger.warning("Massage channel edit throttled; waiting %ss", wait)
            time.sleep(wait + 1)


def _edit_caption(bot, msg_id, caption, markup):
    while True:
        try:
            _wait_for_edit_slot()
            return bot.edit_message_caption(
                caption,
                int(CHANNEL_ID),
                int(msg_id),
                reply_markup=markup,
            )
        except telebot.apihelper.ApiTelegramException as exc:
            wait = _retry_after(exc)
            if wait is None:
                raise
            logger.warning("Massage caption edit throttled; waiting %ss", wait)
            time.sleep(wait + 1)


def _staff_services(staff_id, duty_date=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.code,si.category,si.category_name,si.name,
                       si.price,si.duration
                FROM staff_members m
                LEFT JOIN service_item_technicians st
                  ON st.technician_code=m.code
                 AND st.duty_date=COALESCE(
                     %s,
                     (SELECT MAX(st2.duty_date)
                      FROM service_item_technicians st2
                      JOIN service_items si2 ON si2.id=st2.service_item_id
                      WHERE si2.venue_id=1
                        AND st2.technician_code=m.code)
                 )
                LEFT JOIN service_items si ON si.id=st.service_item_id
                 AND si.venue_id=1
                WHERE m.id=%s
                ORDER BY si.sort_order,si.id
                """,
                (duty_date, staff_id),
            )
            rows = cur.fetchall()
            return rows
    finally:
        conn.close()


def staff_caption(staff_id, code, duty_date=None):
    rows = _staff_services(staff_id, duty_date)
    services = [
        row for row in rows
        if row.get("name")
    ]
    lines = ["名玺水汇按摩 · 技师资料", "", f"工号：{html.escape(str(code))}"]
    if services:
        lines.extend(["", "可服务项目："])
        for service in services:
            lines.extend([
                html.escape(service["name"]),
                f"价格：{format_price(service['price'])}美元｜"
                f"时长：{html.escape(service['duration'])}",
                "",
            ])
    else:
        lines.extend(["", "可服务项目待当班初稿确认"])
    lines.append("点击下方按钮进入完整档案")
    return "\n".join(lines).strip()


def _staff_markup(staff_id, code):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            f"查看{code}详细档案",
            url=f"https://t.me/haotianzhaopian_bot?startapp=staff_1_{staff_id}",
        )
    )
    markup.add(
        InlineKeyboardButton(
            "西港会所导航",
            url="https://t.me/haotianyl99988866",
        )
    )
    markup.add(
        InlineKeyboardButton("联系昊天", url="https://t.me/haotian99988866")
    )
    return markup


def sync_staff_post(bot, staff_id, code, msg_id):
    register_post(staff_id, code, msg_id)
    _edit_caption(
        bot,
        msg_id,
        staff_caption(staff_id, code),
        _staff_markup(staff_id, code),
    )
    schedule_directory_refresh(bot)


def _directory_data():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.staff_id,p.code,p.msg_id,si.category,
                       si.category_name,si.name
                FROM massage_channel_posts p
                JOIN staff_members m ON m.id=p.staff_id
                LEFT JOIN service_item_technicians st
                  ON st.technician_code=p.code
                 AND st.duty_date=(
                     SELECT MAX(st2.duty_date)
                     FROM service_item_technicians st2
                     JOIN service_items si2 ON si2.id=st2.service_item_id
                     WHERE si2.venue_id=1 AND st2.technician_code=p.code
                 )
                LEFT JOIN service_items si ON si.id=st.service_item_id
                 AND si.venue_id=1
                WHERE p.channel_id=%s AND m.ended_at IS NULL
                  AND m.deleted_at IS NULL AND m.blocked=0
                ORDER BY
                    FIELD(si.category,'small','medium','high','tcm','pedicure'),
                    si.sort_order,si.id,LENGTH(p.code),CAST(p.code AS UNSIGNED),p.code
                """,
                (CHANNEL_ID,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def directory_text():
    groups = {}
    services = {}
    for row in _directory_data():
        key = row["category"] or "unclassified"
        groups.setdefault(key, {})
        groups[key][row["code"]] = row
        if row["name"]:
            services.setdefault(key, [])
            if row["name"] not in services[key]:
                services[key].append(row["name"])
    labels = {
        "small": "⭐️ 小项",
        "medium": "⭐️ 中项",
        "high": "⭐️ 高项",
        "tcm": "🧘 中医按摩",
        "pedicure": "🌸 修脚师",
        "unclassified": "待分项",
    }
    lines = [
        "名玺水汇按摩 · 技师档案目录",
        "",
        "点击工号，直接查看本频道对应技师资料。",
    ]
    for key in (*("small", "medium", "high", "tcm", "pedicure"), "unclassified"):
        people = list(groups.get(key, {}).values())
        if not people:
            continue
        lines += ["", f"<b>{labels[key]}</b>"]
        if services.get(key):
            lines.append("包含：" + "、".join(services[key]))
        for offset in range(0, len(people), 8):
            lines.append("  ·  ".join(
                f'<a href="https://t.me/c/2324026180/{int(item["msg_id"])}?single">'
                f'{html.escape(str(item["code"]))}</a>'
                for item in people[offset:offset + 8]
            ))
    lines += [
        "",
        "照片/视频由频道媒体消息维护；服务项目由当班初稿确认。",
        "实际出勤以当班正式公告为准。",
    ]
    return "\n".join(lines)


def _directory_markup():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(
        "查看全部技师档案",
        url="https://t.me/haotianzhaopian_bot?startapp=venue",
    ))
    markup.add(InlineKeyboardButton(
        "西港会所导航", url="https://t.me/haotianyl99988866"
    ))
    markup.add(InlineKeyboardButton(
        "联系昊天", url="https://t.me/haotian99988866"
    ))
    return markup


def _post_message_ids(codes):
    normalized = [str(code) for code in codes]
    if not normalized:
        return {}
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT code,msg_id
                FROM massage_channel_posts
                WHERE channel_id=%s AND code IN ({",".join(["%s"] * len(normalized))})
                """,
                (CHANNEL_ID, *normalized),
            )
            return {
                str(row["code"]): int(row["msg_id"])
                for row in cur.fetchall()
            }
    finally:
        conn.close()


def _attendance_code_links(codes, post_message_ids):
    return "  ".join(
        (
            f'<a href="https://t.me/c/2324026180/{post_message_ids[code]}?single">'
            f"{html.escape(code)}</a>"
            if code in post_message_ids
            else html.escape(code)
        )
        for code in codes
    )


def refresh_directory(bot):
    msg_id = _state_message_id("directory")
    text = directory_text()
    if len(text) > 4096:
        raise ValueError("Massage directory exceeds Telegram text limit")
    if msg_id:
        try:
            _edit_text(bot, msg_id, text, _directory_markup())
            return msg_id
        except telebot.apihelper.ApiTelegramException as exc:
            logger.warning("Cannot edit massage directory: %s", exc)
    sent = bot.send_message(
        int(CHANNEL_ID),
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=_directory_markup(),
        disable_notification=True,
    )
    _set_state_message_id("directory", sent.message_id)
    return sent.message_id


def _fire_directory_refresh(bot):
    try:
        refresh_directory(bot)
    except Exception:
        logger.exception("Massage directory refresh failed")


def schedule_directory_refresh(bot):
    with _timers_lock:
        old = _timers.pop(CHANNEL_ID, None)
        if old:
            old.cancel()
        timer = threading.Timer(
            SYNC_DELAY, _fire_directory_refresh, args=(bot,)
        )
        timer.daemon = True
        _timers[CHANNEL_ID] = timer
        timer.start()


def publish_attendance(bot, attendance, source_message):
    duty_date = attendance["duty_date"]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT si.category,si.category_name,si.name,si.price,si.duration,
                       GROUP_CONCAT(DISTINCT st.technician_code ORDER BY st.technician_code) AS codes
                FROM service_items si
                LEFT JOIN service_item_technicians st
                  ON st.service_item_id=si.id AND st.duty_date=%s
                WHERE si.venue_id=1
                GROUP BY si.id
                ORDER BY si.sort_order,si.id
                """,
                (duty_date,),
            )
            services = cur.fetchall()
    finally:
        conn.close()
    shift = attendance["duty_shift"]
    shift = "白班" if shift == "早班" else shift
    lines = [
        "【按摩出勤完整版】",
        f"名玺水汇按摩 · {duty_date} · {shift}",
        "",
    ]
    categories = {}
    for service in services:
        categories.setdefault(
            (service["category"], service["category_name"]), []
        ).append(service)
    attendance_codes = sorted({
        code
        for items in categories.values()
        for item in items
        for code in (item["codes"] or "").split(",")
        if code
    })
    post_message_ids = _post_message_ids(attendance_codes)
    for (_, category_name), items in categories.items():
        codes = sorted({
            code
            for item in items
            for code in (item["codes"] or "").split(",")
            if code
        })
        lines.append(f"<b>{html.escape(category_name)}</b>")
        for item in items:
            lines.append(
                f"项目：{html.escape(item['name'])}｜"
                f"{format_price(item['price'])}美元｜"
                f"{html.escape(item['duration'])}"
            )
        lines.extend([
            "工号：" + (
                _attendance_code_links(codes, post_message_ids)
                if codes else "—"
            ),
            "",
        ])
    lines += [
        "仅表示本班正式发布名单，不代表实时在岗。",
        "照片/视频技师资料已收录到频道技师档案目录。",
    ]
    text = "\n".join(lines).strip()
    old_id = _state_message_id("attendance")
    if old_id:
        _edit_text(bot, old_id, text, _directory_markup())
        sent_id = old_id
    else:
        sent = bot.send_message(
            int(CHANNEL_ID),
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=_directory_markup(),
            disable_notification=True,
        )
        sent_id = sent.message_id
        _set_state_message_id("attendance", sent_id)
    if source_message:
        bot.delete_message(
            source_message.chat.id,
            source_message.message_id,
            timeout=20,
        )
    schedule_directory_refresh(bot)
    return sent_id


def seed_current_posts():
    codes = [
        "029", "002", "005", "008", "009", "010", "011", "013", "015",
        "016", "021", "022", "026", "028", "033", "060", "066", "068",
        "070", "090", "098", "099", "107", "109", "111", "112", "113",
        "115", "116", "117", "121",
    ]
    seed_existing_posts(
        [(code, msg_id) for code, msg_id in zip(codes, range(4908, 4939))]
    )
