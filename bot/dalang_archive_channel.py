"""Publish eligible staff archives and maintain the existing Dalang channel index."""
import hashlib
import html
import json
import logging
import re
import threading

import requests
from telebot import apihelper, types

from config.channels import DALANG_ARCHIVE_REPOST_PREFIX
from db import get_conn
from mingxi_complete_attendance import FULL_ROSTER_MARKER
from staff_archive import detail, listing, photo
from welcome import BOT_URL, CHANNEL_URL, CONTACT_URL


logger = logging.getLogger(__name__)
CHANNEL_ID = -1002219327758
CHANNEL_NAME = "dlts02"
ARCHIVE_URL = "https://app.haotianzhushou.com/?module=venue&venue=4&album=1"
INTERVAL_SECONDS = 5
MAX_MESSAGE_ENTITIES = 100
_reported_blocks = {}


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS dalang_archive_policy (
                id INT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT FALSE,
                next_request_at DATETIME NULL
            ) ENGINE=InnoDB""")
            cur.execute("""CREATE TABLE IF NOT EXISTS dalang_archive_posts (
                staff_id BIGINT PRIMARY KEY, status VARCHAR(16) NOT NULL,
                message_id BIGINT NULL, error VARCHAR(255) NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS dalang_archive_pages (
                page_number INT PRIMARY KEY, status VARCHAR(16) NOT NULL,
                message_id BIGINT NULL, content_hash CHAR(64) NULL,
                error VARCHAR(255) NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("INSERT IGNORE INTO dalang_archive_policy(id,enabled) VALUES(1,0)")
        conn.commit()
    finally:
        conn.close()


def price_label(member):
    price = member["price_usd"]
    if price is None:
        return "价格待确认 · 时长待确认"
    if not (member["price_detail"] or "").endswith(" · 90分钟"):
        raise ValueError("大浪档案价目表时长不符合预期")
    return f"${price} · 90分钟"


def caption(member):
    price = member["price_usd"]
    label = price_label(member)
    if price is None:
        price_text, duration = "待确认", "待确认"
    else:
        price_text, duration = label.split(" · ", 1)
    return (
        f"{DALANG_ARCHIVE_REPOST_PREFIX}"
        f"工号：{member['code']}\n价格：{price_text}\n时长：{duration}\n"
        "价格为现有价目表参考；档案不代表当前出勤，具体请联系确认。"
    )


def photo_keyboard(member, username):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(
            f"查看{member['code']}详细档案",
            url=f"https://t.me/{username}?startapp=staff_4_{member['id']}",
        ),
        types.InlineKeyboardButton("西港会所导航", url=CHANNEL_URL),
    )
    markup.row(
        types.InlineKeyboardButton("联系昊天", url=CONTACT_URL),
        types.InlineKeyboardButton("昊天助手机器人", url=BOT_URL),
    )
    return markup


def navigation_keyboard(page_number, page_count, page_ids):
    markup = types.InlineKeyboardMarkup()
    paging = []
    for number, label in ((page_number - 1, "上一页"), (page_number + 1, "下一页")):
        if 1 <= number <= page_count and number in page_ids:
            paging.append(types.InlineKeyboardButton(
                label, url=f"https://t.me/{CHANNEL_NAME}/{page_ids[number]}"))
    if page_number > page_count and 1 in page_ids:
        paging.append(types.InlineKeyboardButton(
            "返回人员导航", url=f"https://t.me/{CHANNEL_NAME}/{page_ids[1]}"))
    if paging:
        markup.row(*paging)
    markup.row(
        types.InlineKeyboardButton("查看全部人员档案", url=ARCHIVE_URL),
        types.InlineKeyboardButton("西港会所导航", url=CHANNEL_URL),
    )
    markup.row(
        types.InlineKeyboardButton("联系昊天", url=CONTACT_URL),
        types.InlineKeyboardButton("昊天助手机器人", url=BOT_URL),
    )
    return markup


def _text_units(value):
    return len(value.encode("utf-16-le")) // 2


def navigation_pages(members, published):
    """Create bounded pages containing only current public, published staff."""
    groups = {}
    for member in members:
        if member["id"] in published:
            groups.setdefault(member["group_name"] or "类别待确认", []).append(member)
    order = lambda group: (group == "类别待确认", group == "车模", group)
    fragments = [[]]
    entities = 1
    plain_length = 80
    for group in sorted(groups, key=order):
        labels = {price_label(member) for member in groups[group]}
        label = next(iter(labels)) if len(labels) == 1 else "价格及时长请查看档案"
        heading = f"{group} · {label}"
        for member in sorted(groups[group], key=lambda item: (item["code"], item["id"])):
            need_heading = not fragments[-1] or fragments[-1][-1][0] != group
            code_length = _text_units(member["code"]) + 4
            heading_length = _text_units(heading) + 2
            added_length = code_length + (heading_length if need_heading else 0)
            if entities + 1 + int(need_heading) > MAX_MESSAGE_ENTITIES or plain_length + added_length > 3000:
                if not fragments[-1]:
                    raise ValueError("大浪人员导航单项内容超过长度限制")
                fragments.append([])
                entities, plain_length = 1, 80
                need_heading = True
                if plain_length + code_length + heading_length > 3000:
                    raise ValueError("大浪人员导航单项内容超过长度限制")
            if need_heading:
                fragments[-1].append((group, heading, []))
                entities += 1
                plain_length += heading_length
            url = f"https://t.me/{CHANNEL_NAME}/{published[member['id']]}"
            fragments[-1][-1][2].append(
                f'<a href="{url}">{html.escape(member["code"])}</a>')
            entities += 1
            plain_length += code_length
    total = sum(len(items) for page in fragments for _, _, items in page)
    pages = []
    for number, page in enumerate(fragments, 1):
        title = f"大浪淘沙会所 · 共 {total} 人"
        if len(fragments) > 1:
            title += f" · {number}/{len(fragments)}"
        lines = [FULL_ROSTER_MARKER, f"<b>{html.escape(title)}</b>", ""]
        for _, heading, links in page:
            lines.extend([f"<b>{html.escape(heading)}</b>", "  ".join(links), ""])
        pages.append("\n".join(lines).strip())
    return pages


def _digest(text, markup):
    payload = json.dumps([text, markup.to_dict()], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _claim(cur, conn, table, key_column, key, editing=False):
    status = "editing" if editing else "sending"
    cur.execute(f"UPDATE {table} SET status=%s,error=NULL WHERE {key_column}=%s",
                (status, key))
    cur.execute("UPDATE dalang_archive_policy SET next_request_at="
                "DATE_ADD(UTC_TIMESTAMP(),INTERVAL %s SECOND) WHERE id=1",
                (INTERVAL_SECONDS,))
    conn.commit()


def _request(cur, conn, table, key_column, key, send, validate, *,
             editing=False, content_hash=None):
    _claim(cur, conn, table, key_column, key, editing=editing)
    try:
        result = send()
    except apihelper.ApiTelegramException as exc:
        if editing and exc.error_code == 400 and "message is not modified" in str(exc.description).lower():
            result = True
        else:
            retry = (exc.result_json.get("parameters") or {}).get("retry_after")
            if exc.error_code == 429 and type(retry) is int and retry > 0:
                cur.execute("UPDATE dalang_archive_policy SET next_request_at="
                            "DATE_ADD(UTC_TIMESTAMP(),INTERVAL %s SECOND) WHERE id=1",
                            (max(retry + 1, INTERVAL_SECONDS),))
                status = "sent" if editing else "pending"
            else:
                status = "uncertain" if exc.error_code >= 500 and not editing else "failed"
            error = f"Telegram error {exc.error_code}, retry_after={retry}"
            cur.execute(f"UPDATE {table} SET status=%s,error=%s WHERE {key_column}=%s",
                        (status, error, key))
            conn.commit()
            logger.error("Dalang channel request %s=%s: %s (%s)", key_column, key, status, error)
            return
    except Exception as exc:
        status = "failed" if editing else "uncertain"
        error = f"request exception: {type(exc).__name__}"
        cur.execute(f"UPDATE {table} SET status=%s,error=%s WHERE {key_column}=%s",
                    (status, error, key))
        conn.commit()
        logger.error("Dalang channel request %s=%s: %s (%s)", key_column, key, status, error)
        return

    if not validate(result):
        message_id = getattr(result, "message_id", None)
        if editing:
            cur.execute(f"UPDATE {table} SET status='uncertain',error=%s WHERE {key_column}=%s",
                        ("Telegram response did not match request", key))
        else:
            cur.execute(f"UPDATE {table} SET status='uncertain',message_id=%s,error=%s "
                        f"WHERE {key_column}=%s",
                        (message_id, "Telegram response did not match request", key))
        conn.commit()
        logger.error("Dalang channel response mismatch %s=%s message_id=%s", key_column, key, message_id)
        return
    if table == "dalang_archive_pages":
        message_id = None if result is True else result.message_id
        cur.execute("UPDATE dalang_archive_pages SET status='sent',"
                    "message_id=COALESCE(%s,message_id),content_hash=%s,error=NULL WHERE page_number=%s",
                    (message_id, content_hash, key))
    else:
        cur.execute("UPDATE dalang_archive_posts SET status='sent',message_id=%s,error=NULL WHERE staff_id=%s",
                    (result.message_id, key))
    conn.commit()
    logger.info("Dalang channel %s=%s completed", key_column, key)


def _valid_message(result, message_id=None):
    return (
        getattr(getattr(result, "chat", None), "id", None) == CHANNEL_ID
        and type(getattr(result, "message_id", None)) is int
        and result.message_id > 0
        and (message_id is None or result.message_id == message_id)
    )


def _send_post(bot, username, cur, conn, member):
    record = detail({"staff_id": member["id"]})
    media = photo(member["cover"])
    if (not record or record["venue_id"] != 4 or not media
            or media["venue_id"] != 4 or media["media_type"] != "photo"
            or member["cover"] not in {item["id"] for item in record["photos"]}):
        logger.warning("Dalang staff %s no longer has an eligible cover", member["id"])
        return False
    text = caption(member)
    markup = photo_keyboard(member, username)
    _request(
        cur, conn, "dalang_archive_posts", "staff_id", member["id"],
        lambda: bot.send_photo(CHANNEL_ID, media["file_id"], caption=text,
                               reply_markup=markup, disable_notification=True),
        lambda result: (_valid_message(result) and bool(result.photo)
                        and result.caption == text and result.reply_markup
                        and result.reply_markup.to_dict() == markup.to_dict()),
    )
    return True


def _send_page(bot, cur, conn, number, message_id, text, markup):
    digest = _digest(text, markup)
    expected_urls = re.findall(r'href="([^"]+)"', text)

    def valid(result):
        if result is True:
            return bool(message_id)
        actual_urls = [entity.url for entity in getattr(result, "entities", None) or []
                       if entity.type == "text_link"]
        return (_valid_message(result, message_id)
                and (getattr(result, "text", None) or "").startswith(FULL_ROSTER_MARKER)
                and actual_urls == expected_urls
                and getattr(result, "reply_markup", None)
                and result.reply_markup.to_dict() == markup.to_dict())

    if message_id:
        send = lambda: bot.edit_message_text(
            text, chat_id=CHANNEL_ID, message_id=message_id, parse_mode="HTML",
            disable_web_page_preview=True, reply_markup=markup)
    else:
        send = lambda: bot.send_message(
            CHANNEL_ID, text, parse_mode="HTML", disable_web_page_preview=True,
            disable_notification=True, reply_markup=markup)
    _request(cur, conn, "dalang_archive_pages", "page_number", number, send, valid,
             editing=bool(message_id), content_hash=digest)


def _report_blocked(kind, rows):
    state = tuple((row[kind], row["status"]) for row in rows)
    if state != _reported_blocks.get(kind):
        if state:
            logger.error("Dalang channel records require manual review: %s", state)
        _reported_blocks[kind] = state


def run_once(bot, username):
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('dalang_archive_channel',0) AS acquired")
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                return
            cur.execute("UPDATE dalang_archive_posts SET status='uncertain',"
                        "error='interrupted while sending' WHERE status='sending'")
            cur.execute("UPDATE dalang_archive_pages SET status='uncertain',"
                        "error='interrupted while sending' WHERE status='sending'")
            cur.execute("UPDATE dalang_archive_pages SET status='uncertain',"
                        "error='interrupted while deleting' WHERE status='deleting'")
            cur.execute("UPDATE dalang_archive_pages SET status='sent',content_hash=NULL,"
                        "error='interrupted while editing' WHERE status='editing'")
            cur.execute("SELECT enabled,next_request_at IS NULL OR next_request_at<=UTC_TIMESTAMP() AS ready "
                        "FROM dalang_archive_policy WHERE id=1")
            policy = cur.fetchone()
            conn.commit()
            if not policy or not policy["enabled"] or not policy["ready"]:
                return
            cur.execute("SELECT * FROM dalang_archive_pages WHERE page_number=1")
            anchor = cur.fetchone()
            if not anchor or anchor["status"] != "sent" or not anchor["message_id"]:
                raise RuntimeError("Dalang navigation anchor is not initialized")
            members = listing({"venue_id": 4, "mode": "all"})["members"]
            current = {member["id"]: member for member in members if member["cover"]}
            if current:
                cur.executemany("INSERT IGNORE INTO dalang_archive_posts(staff_id,status) "
                                "VALUES(%s,'pending')", [(ident,) for ident in current])
            cur.execute("SELECT * FROM dalang_archive_posts")
            posts = cur.fetchall()
            conn.commit()
            blocked = [row for row in posts if row["status"] in {"uncertain", "failed"}]
            pending = sorted(
                (row for row in posts if row["status"] == "pending" and row["staff_id"] in current),
                key=lambda row: row["staff_id"],
            )
            _report_blocked("staff_id", blocked)
            if not blocked and pending:
                return _send_post(bot, username, cur, conn, current[pending[0]["staff_id"]])
            published = {row["staff_id"]: row["message_id"] for row in posts
                         if row["status"] == "sent" and row["message_id"]}
            texts = navigation_pages(members, published)
            cur.executemany("INSERT IGNORE INTO dalang_archive_pages(page_number,status) "
                            "VALUES(%s,'pending')", [(number,) for number in range(1, len(texts) + 1)])
            cur.execute("SELECT * FROM dalang_archive_pages ORDER BY page_number")
            pages = cur.fetchall()
            conn.commit()
            bad_pages = [row for row in pages if row["status"] in {"uncertain", "failed"}]
            _report_blocked("page_number", bad_pages)
            if bad_pages:
                return
            page_ids = {row["page_number"]: row["message_id"] for row in pages
                        if row["status"] == "sent" and row["message_id"]}
            for row in pages:
                number = row["page_number"]
                if number > len(texts) and not row["message_id"]:
                    continue
                text = (texts[number - 1] if number <= len(texts) else
                        FULL_ROSTER_MARKER + "\n此页已合并，请返回人员导航。")
                markup = navigation_keyboard(number, len(texts), page_ids)
                if row["status"] == "pending" or row["content_hash"] != _digest(text, markup):
                    _send_page(bot, cur, conn, number, row["message_id"], text, markup)
                    return True
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK('dalang_archive_channel')")
        conn.close()


def _enabled():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled FROM dalang_archive_policy WHERE id=1")
            row = cur.fetchone()
            return bool(row and row["enabled"])
    finally:
        conn.close()


def verify_destination(bot):
    me = bot.get_me()
    chat = bot.get_chat(CHANNEL_ID)
    membership = bot.get_chat_member(CHANNEL_ID, me.id)
    if (chat.type != "channel" or chat.username != CHANNEL_NAME
            or not getattr(membership, "can_post_messages", False) or not me.username):
        raise RuntimeError("Dalang channel identity or publishing permission mismatch")
    return me.username


def start_worker(bot):
    def run():
        username = None
        while True:
            requested = False
            try:
                if username is None and _enabled():
                    username = verify_destination(bot)
                    logger.info("Dalang archive channel worker ready")
                if username:
                    requested = run_once(bot, username)
            except (requests.RequestException, apihelper.ApiException) as exc:
                logger.error("Dalang archive channel worker failed: %s", type(exc).__name__)
            except Exception:
                logger.exception("Dalang archive channel worker failed")
            threading.Event().wait(INTERVAL_SECONDS if requested else 15)
    threading.Thread(target=run, name="dalang-archive-channel", daemon=True).start()
