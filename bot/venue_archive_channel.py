"""Publish eligible venue staff archives and maintain channel indexes."""
import hashlib
import html
import json
import logging
import re
import threading

import requests
from telebot import apihelper, types

from config.channels import STAFF_ARCHIVE_VENUES
from db import get_conn
from mingxi_complete_attendance import FULL_ROSTER_MARKER
from staff_archive import detail, listing, photo
from welcome import BOT_URL, CHANNEL_URL, CONTACT_URL


logger = logging.getLogger(__name__)
CHANNEL_ID = STAFF_ARCHIVE_VENUES[4]["channel_id"]
CHANNEL_NAME = STAFF_ARCHIVE_VENUES[4]["channel_name"]
CHANNEL_URL = f"https://t.me/{CHANNEL_NAME}"
ARCHIVE_URL = "https://app.haotianzhushou.com/?module=venue&venue=4&album=1"
INTERVAL_SECONDS = 5
MAX_MESSAGE_ENTITIES = 100
_reported_blocks = {}


def _venue(venue_id):
    try:
        return STAFF_ARCHIVE_VENUES[venue_id]
    except KeyError as error:
        raise ValueError(f"Unsupported staff archive venue: {venue_id}") from error


def _table(venue_id, suffix):
    return f"{_venue(venue_id)['table_prefix']}_{suffix}"


def _channel_url(venue_id):
    return f"https://t.me/{_venue(venue_id)['channel_name']}"


def _archive_url(venue_id):
    return f"https://app.haotianzhushou.com/?module=venue&venue={venue_id}&album=1"


def ensure_tables(venue_id=4):
    prefix = _venue(venue_id)["table_prefix"]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {prefix}_policy (
                id INT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT FALSE,
                next_request_at DATETIME NULL
            ) ENGINE=InnoDB""")
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {prefix}_posts (
                staff_id BIGINT PRIMARY KEY, status VARCHAR(16) NOT NULL,
                message_id BIGINT NULL, error VARCHAR(255) NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {prefix}_pages (
                page_number INT PRIMARY KEY, status VARCHAR(16) NOT NULL,
                message_id BIGINT NULL, content_hash CHAR(64) NULL,
                error VARCHAR(255) NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute(f"INSERT IGNORE INTO {prefix}_policy(id,enabled) VALUES(1,0)")
        conn.commit()
    finally:
        conn.close()


def price_label(member, venue_id=4):
    price = member["price_usd"]
    if price is None:
        return "价格待确认 · 时长待确认"
    duration = f"{member['price_detail'] or ''}"
    if not duration.endswith("分钟"):
        raise ValueError(f"{_venue(venue_id)['venue_name']}档案价目表时长待确认")
    return f"${price} · {duration.rsplit(' · ', 1)[-1]}"


def caption(member, venue_id=4):
    price = member["price_usd"]
    label = price_label(member, venue_id)
    if price is None:
        price_text, duration = "待确认", "待确认"
    else:
        price_text, duration = label.split(" · ", 1)
    category_line = (
        f"类别：{member['group_name'] or '类别待确认'}\n" if venue_id in {5, 6} else ""
    )
    return (
        f"{_venue(venue_id)['repost_prefix']}"
        f"工号：{member['code']}\n{category_line}"
        f"价格：{price_text}\n时长：{duration}\n"
        "价格为现有价目表参考；档案不代表当前出勤，具体请联系确认。"
    )


def photo_keyboard(member, username, venue_id=4):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(
            f"查看{member['code']}详细档案",
            url=f"https://t.me/{username}?startapp=staff_{venue_id}_{member['id']}",
        ),
        types.InlineKeyboardButton("西港会所导航", url=_channel_url(venue_id)),
    )
    markup.row(
        types.InlineKeyboardButton("联系昊天", url=CONTACT_URL),
        types.InlineKeyboardButton("昊天助手机器人", url=BOT_URL),
    )
    return markup


def navigation_keyboard(page_number, page_count, page_ids, venue_id=4):
    markup = types.InlineKeyboardMarkup()
    paging = []
    for number, label in ((page_number - 1, "上一页"), (page_number + 1, "下一页")):
        if 1 <= number <= page_count and number in page_ids:
            paging.append(types.InlineKeyboardButton(
                label, url=f"https://t.me/{_venue(venue_id)['channel_name']}/{page_ids[number]}"))
    if page_number > page_count and 1 in page_ids:
        paging.append(types.InlineKeyboardButton(
            "返回人员导航", url=f"https://t.me/{_venue(venue_id)['channel_name']}/{page_ids[1]}"))
    if paging:
        markup.row(*paging)
    markup.row(
        types.InlineKeyboardButton("查看全部人员档案", url=_archive_url(venue_id)),
        types.InlineKeyboardButton("西港会所导航", url=_channel_url(venue_id)),
    )
    markup.row(
        types.InlineKeyboardButton("联系昊天", url=CONTACT_URL),
        types.InlineKeyboardButton("昊天助手机器人", url=BOT_URL),
    )
    return markup


def _text_units(value):
    return len(value.encode("utf-16-le")) // 2


def navigation_pages(members, published, venue_id=4):
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
        labels = {price_label(member, venue_id) for member in groups[group]}
        label = next(iter(labels)) if len(labels) == 1 else "价格及时长请查看档案"
        heading = f"{group} · {label}"
        for member in sorted(groups[group], key=lambda item: (item["code"], item["id"])):
            need_heading = not fragments[-1] or fragments[-1][-1][0] != group
            code_length = _text_units(member["code"]) + 4
            heading_length = _text_units(heading) + 2
            added_length = code_length + (heading_length if need_heading else 0)
            if entities + 1 + int(need_heading) > MAX_MESSAGE_ENTITIES or plain_length + added_length > 3000:
                if not fragments[-1]:
                    raise ValueError(f"{_venue(venue_id)['venue_name']}人员导航单项内容超过长度限制")
                fragments.append([])
                entities, plain_length = 1, 80
                need_heading = True
                if plain_length + code_length + heading_length > 3000:
                    raise ValueError(f"{_venue(venue_id)['venue_name']}人员导航单项内容超过长度限制")
            if need_heading:
                fragments[-1].append((group, heading, []))
                entities += 1
                plain_length += heading_length
            url = f"https://t.me/{_venue(venue_id)['channel_name']}/{published[member['id']]}"
            fragments[-1][-1][2].append(
                f'<a href="{url}">{html.escape(member["code"])}</a>')
            entities += 1
            plain_length += code_length
    total = sum(len(items) for page in fragments for _, _, items in page)
    pages = []
    for number, page in enumerate(fragments, 1):
        title = f"{_venue(venue_id)['archive_title']} · 共 {total} 人"
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


def _claim(cur, conn, table, key_column, key, venue_id=4, editing=False):
    status = "editing" if editing else "sending"
    cur.execute(f"UPDATE {table} SET status=%s,error=NULL WHERE {key_column}=%s",
                (status, key))
    cur.execute(f"UPDATE {_table(venue_id, 'policy')} SET next_request_at="
                "DATE_ADD(UTC_TIMESTAMP(),INTERVAL %s SECOND) WHERE id=1",
                (INTERVAL_SECONDS,))
    conn.commit()


def _request(cur, conn, table, key_column, key, send, validate, *,
             venue_id=4, editing=False, content_hash=None):
    _claim(cur, conn, table, key_column, key, venue_id=venue_id, editing=editing)
    try:
        result = send()
    except apihelper.ApiTelegramException as exc:
        if editing and exc.error_code == 400 and "message is not modified" in str(exc.description).lower():
            result = True
        else:
            retry = (exc.result_json.get("parameters") or {}).get("retry_after")
            if exc.error_code == 429 and type(retry) is int and retry > 0:
                cur.execute(f"UPDATE {_table(venue_id, 'policy')} SET next_request_at="
                            "DATE_ADD(UTC_TIMESTAMP(),INTERVAL %s SECOND) WHERE id=1",
                            (max(retry + 1, INTERVAL_SECONDS),))
                status = "sent" if editing else "pending"
            else:
                status = "uncertain" if exc.error_code >= 500 and not editing else "failed"
            error = f"Telegram error {exc.error_code}, retry_after={retry}"
            cur.execute(f"UPDATE {table} SET status=%s,error=%s WHERE {key_column}=%s",
                        (status, error, key))
            conn.commit()
            logger.error("%s channel request %s=%s: %s (%s)",
                         _venue(venue_id)["venue_name"], key_column, key, status, error)
            return
    except Exception as exc:
        status = "failed" if editing else "uncertain"
        error = f"request exception: {type(exc).__name__}"
        cur.execute(f"UPDATE {table} SET status=%s,error=%s WHERE {key_column}=%s",
                    (status, error, key))
        conn.commit()
        logger.error("%s channel request %s=%s: %s (%s)",
                     _venue(venue_id)["venue_name"], key_column, key, status, error)
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
        logger.error("%s channel response mismatch %s=%s message_id=%s",
                     _venue(venue_id)["venue_name"], key_column, key, message_id)
        return
    if table == _table(venue_id, "pages"):
        message_id = None if result is True else result.message_id
        cur.execute(f"UPDATE {table} SET status='sent',"
                    "message_id=COALESCE(%s,message_id),content_hash=%s,error=NULL WHERE page_number=%s",
                    (message_id, content_hash, key))
    else:
        cur.execute(f"UPDATE {table} SET status='sent',message_id=%s,error=NULL WHERE staff_id=%s",
                    (result.message_id, key))
    conn.commit()
    logger.info("%s channel %s=%s completed", _venue(venue_id)["venue_name"], key_column, key)


def _valid_message(result, message_id=None, venue_id=4):
    return (
        getattr(getattr(result, "chat", None), "id", None) == _venue(venue_id)["channel_id"]
        and type(getattr(result, "message_id", None)) is int
        and result.message_id > 0
        and (message_id is None or result.message_id == message_id)
    )


def _send_post(bot, username, cur, conn, member, *, venue_id=4):
    record = detail({"staff_id": member["id"]})
    media = photo(member["cover"])
    if (not record or record["venue_id"] != venue_id or not media
            or media["venue_id"] != venue_id or media["media_type"] != "photo"
            or member["cover"] not in {item["id"] for item in record["photos"]}):
        logger.warning("%s staff %s no longer has an eligible cover",
                       _venue(venue_id)["venue_name"], member["id"])
        return False
    text = caption(member, venue_id)
    markup = photo_keyboard(member, username, venue_id)
    _request(
        cur, conn, _table(venue_id, "posts"), "staff_id", member["id"],
        lambda: bot.send_photo(_venue(venue_id)["channel_id"], media["file_id"], caption=text,
                               reply_markup=markup, disable_notification=True),
        lambda result: (_valid_message(result, venue_id=venue_id) and bool(result.photo)
                        and result.caption == text and result.reply_markup
                        and result.reply_markup.to_dict() == markup.to_dict()),
        venue_id=venue_id,
    )
    return True


def _send_page(bot, cur, conn, number, message_id, text, markup, *, venue_id=4):
    digest = _digest(text, markup)
    expected_urls = re.findall(r'href="([^"]+)"', text)

    def valid(result):
        if result is True:
            return bool(message_id)
        actual_urls = [entity.url for entity in getattr(result, "entities", None) or []
                       if entity.type == "text_link"]
        return (_valid_message(result, message_id, venue_id)
                and (getattr(result, "text", None) or "").startswith(FULL_ROSTER_MARKER)
                and actual_urls == expected_urls
                and getattr(result, "reply_markup", None)
                and result.reply_markup.to_dict() == markup.to_dict())

    if message_id:
        send = lambda: bot.edit_message_text(
            text, chat_id=_venue(venue_id)["channel_id"], message_id=message_id, parse_mode="HTML",
            disable_web_page_preview=True, reply_markup=markup)
    else:
        send = lambda: bot.send_message(
            _venue(venue_id)["channel_id"], text, parse_mode="HTML", disable_web_page_preview=True,
            disable_notification=True, reply_markup=markup)
    _request(cur, conn, _table(venue_id, "pages"), "page_number", number, send, valid,
             venue_id=venue_id, editing=bool(message_id), content_hash=digest)


def _report_blocked(kind, rows, venue_id=4):
    state = tuple((row[kind], row["status"]) for row in rows)
    key = (venue_id, kind)
    if state != _reported_blocks.get(key):
        if state:
            logger.error("%s channel records require manual review: %s",
                         _venue(venue_id)["venue_name"], state)
        _reported_blocks[key] = state


def run_once(bot, username, venue_id=4):
    posts_table = _table(venue_id, "posts")
    pages_table = _table(venue_id, "pages")
    policy_table = _table(venue_id, "policy")
    config = _venue(venue_id)
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s,0) AS acquired",
                        (f"{config['table_prefix']}_channel",))
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                return
            cur.execute(f"UPDATE {posts_table} SET status='uncertain',"
                        "error='interrupted while sending' WHERE status='sending'")
            cur.execute(f"UPDATE {pages_table} SET status='uncertain',"
                        "error='interrupted while sending' WHERE status='sending'")
            cur.execute(f"UPDATE {pages_table} SET status='uncertain',"
                        "error='interrupted while deleting' WHERE status='deleting'")
            cur.execute(f"UPDATE {pages_table} SET status='sent',content_hash=NULL,"
                        "error='interrupted while editing' WHERE status='editing'")
            cur.execute(f"SELECT enabled,next_request_at IS NULL OR next_request_at<=UTC_TIMESTAMP() AS ready "
                        f"FROM {policy_table} WHERE id=1")
            policy = cur.fetchone()
            conn.commit()
            if not policy or not policy["enabled"] or not policy["ready"]:
                return
            cur.execute(f"SELECT * FROM {pages_table} WHERE page_number=1")
            anchor = cur.fetchone()
            if not anchor or anchor["status"] != "sent" or not anchor["message_id"]:
                raise RuntimeError(f"{config['venue_name']} navigation anchor is not initialized")
            members = listing({"venue_id": venue_id, "mode": "all"})["members"]
            current = {member["id"]: member for member in members if member["cover"]}
            if current:
                cur.executemany(f"INSERT IGNORE INTO {posts_table}(staff_id,status) "
                                "VALUES(%s,'pending')", [(ident,) for ident in current])
            cur.execute(f"SELECT * FROM {posts_table}")
            posts = cur.fetchall()
            conn.commit()
            blocked = [row for row in posts if row["status"] in {"uncertain", "failed"}]
            pending = sorted(
                (row for row in posts if row["status"] == "pending" and row["staff_id"] in current),
                key=lambda row: row["staff_id"],
            )
            _report_blocked("staff_id", blocked, venue_id)
            if not blocked and pending:
                return _send_post(bot, username, cur, conn, current[pending[0]["staff_id"]],
                                  venue_id=venue_id)
            published = {row["staff_id"]: row["message_id"] for row in posts
                         if row["status"] == "sent" and row["message_id"]}
            texts = navigation_pages(members, published, venue_id)
            cur.executemany(f"INSERT IGNORE INTO {pages_table}(page_number,status) "
                            "VALUES(%s,'pending')", [(number,) for number in range(1, len(texts) + 1)])
            cur.execute(f"SELECT * FROM {pages_table} ORDER BY page_number")
            pages = cur.fetchall()
            conn.commit()
            bad_pages = [row for row in pages if row["status"] in {"uncertain", "failed"}]
            _report_blocked("page_number", bad_pages, venue_id)
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
                markup = navigation_keyboard(number, len(texts), page_ids, venue_id)
                if row["status"] == "pending" or row["content_hash"] != _digest(text, markup):
                    _send_page(bot, cur, conn, number, row["message_id"], text, markup,
                               venue_id=venue_id)
                    return True
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (f"{config['table_prefix']}_channel",))
        conn.close()


def _enabled(venue_id=4):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT enabled FROM {_table(venue_id, 'policy')} WHERE id=1")
            row = cur.fetchone()
            return bool(row and row["enabled"])
    finally:
        conn.close()


def verify_destination(bot, venue_id=4):
    me = bot.get_me()
    config = _venue(venue_id)
    chat = bot.get_chat(config["channel_id"])
    membership = bot.get_chat_member(config["channel_id"], me.id)
    if (chat.type != "channel" or chat.username != config["channel_name"]
            or not getattr(membership, "can_post_messages", False) or not me.username):
        raise RuntimeError(f"{config['venue_name']} channel identity or publishing permission mismatch")
    return me.username


def start_worker(bot, venue_id=4):
    config = _venue(venue_id)

    def run():
        username = None
        while True:
            requested = False
            try:
                if username is None and _enabled(venue_id):
                    username = verify_destination(bot, venue_id)
                    logger.info("%s archive channel worker ready", config["venue_name"])
                if username:
                    requested = run_once(bot, username, venue_id)
            except (requests.RequestException, apihelper.ApiException) as exc:
                logger.error("%s archive channel worker failed: %s",
                             config["venue_name"], type(exc).__name__)
            except Exception:
                logger.exception("%s archive channel worker failed", config["venue_name"])
            threading.Event().wait(INTERVAL_SECONDS if requested else 15)
    threading.Thread(target=run, name=f"venue-archive-channel-{venue_id}", daemon=True).start()


def start_workers(bot):
    for venue_id in STAFF_ARCHIVE_VENUES:
        ensure_tables(venue_id)
        start_worker(bot, venue_id)
