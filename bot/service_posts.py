"""Authorized housing and vehicle feeds, separate from expiring news."""
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from announcements import local_media_path as announcement_media_path, serialize as serialize_announcement
from db import get_conn
from channel_sync_clean import LINK, MENTION, clean_text


SOURCES = {
    "villas": ("西港别墅", "xixi09665", 2234736926),
    "apartments": ("西港公寓", "xixi2619", 4286862752),
    "rentals": ("金边/西港租车", "xixi08846", 2428587310),
}
START_AT = datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=7))).astimezone(timezone.utc)
EMAIL = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.IGNORECASE)
PHONE = re.compile(r"(?<![a-z0-9])\+?\d[\d \t().-]*\d(?![a-z0-9])", re.IGNORECASE)
CONTACT_FIELD = re.compile(
    r"(?:(?:看房|租房|预约|預約)[ \t]*)?"
    r"(?:联系人|聯繫人|联系方式|聯繫方式|联系电话|聯繫電話|联络人|聯絡人|"
    r"微信|微訊|联系|聯繫|联络|聯絡)"
    r"|(?:电话|電話|手机|手機|邮箱|郵箱|QQ)(?=\s*[:：]|\s+\S|\s*\+?\d)"
    r"|\b(?:contact|telephone|tel|phone|mobile|e-?mail|wechat|whatsapp|telegram|tg|line)\s*[:：]",
    re.IGNORECASE,
)
PROMO_LINE = re.compile(
    r"(?:(?:西港(?:房源|别墅|別墅|公寓|租车|租車))|频道|頻道|关注|關注|点击|點擊|"
    r"查看|进入|進入|导航|導航|链接|鏈接)+"
)
LISTING_FIELD = re.compile(
    r"^[^\w]*(地址|位置|户型|面积|租金|售价|出租|价格|车型|年款|年份|年龄|"
    r"里程|公里数|变速箱|排量|燃料|押金|租期)\s*[:：]\s*(\S.*?)\s*$"
)


def clean_body(text, entities=()):
    text = clean_text(text, entities, additional_contacts=(EMAIL,))
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    text = EMAIL.sub("", LINK.sub("", MENTION.sub("", text)))
    lines = []
    for line in text.splitlines():
        if match := CONTACT_FIELD.search(line):
            line = line[:match.start()].rstrip()
        compact = re.sub(r"[\W_]", "", line)
        if compact in {"请", "请直接", "详情", "更多详情"} or PROMO_LINE.fullmatch(compact):
            continue

        def remove_phone(match):
            digits = re.sub(r"\D", "", match[0])
            if not 7 <= len(digits) <= 15:
                return match[0]
            if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", match[0]):
                return match[0]
            before, after = line[:match.start()], line[match.end():]
            if (re.search(r"(?:[$€£¥]|USD|KHR|VND)\s*$", before, re.IGNORECASE)
                    or re.match(r"\s*(?:[$€£¥]|USD|KHR|VND|美金|美元|平方米|平米|㎡)", after, re.IGNORECASE)):
                return match[0]
            return ""

        line = PHONE.sub(remove_phone, line).strip()
        if line and not any(char.isalnum() for char in line):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def category(value):
    if not isinstance(value, str) or value not in SOURCES:
        raise ValueError("请选择有效的服务分类")
    return value


def local_media_path(filename):
    path = announcement_media_path(filename)
    return path.parent.parent / "service-media" / path.name


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS service_posts (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                category VARCHAR(16) NOT NULL,
                job_key VARCHAR(255) NOT NULL,
                message_ids JSON NOT NULL,
                body MEDIUMTEXT NOT NULL,
                media JSON NOT NULL,
                published_at DATETIME NOT NULL,
                enabled TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY service_source (category,job_key),
                INDEX service_date (category,enabled,published_at,id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
    finally:
        conn.close()


def save(service, key, ids, body, media, published_at):
    category(service)
    body = clean_body(body)
    if published_at.astimezone(timezone.utc) < START_AT:
        raise ValueError("服务消息早于已授权收录起点")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO service_posts
                (category,job_key,message_ids,body,media,published_at,enabled)
                VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE job_key=VALUES(job_key)""",
                        (service, key, json.dumps(ids), body, json.dumps(media),
                         published_at.astimezone(timezone.utc).replace(tzinfo=None), bool(body or media)))
        conn.commit()
    finally:
        conn.close()


def serialize(row, detail=False):
    row = {**row, "body": clean_body(row["body"])}
    result = serialize_announcement(row, detail, media_base="/service-media")
    result["category"] = SOURCES[row["category"]][0]
    result["attributes"] = listing_attributes(row["body"])
    if not row["body"]:
        result["title"] = "视频介绍" if result["has_video"] else "图片介绍"
    return result


def listing_attributes(body):
    attributes = []
    known = set()
    for line in body.splitlines():
        match = LISTING_FIELD.match(line)
        if match and any(char.isalnum() for char in match[2]):
            pair = (match[1], match[2])
            if pair not in known:
                known.add(pair)
                attributes.append({"label": pair[0], "value": pair[1]})
    return attributes


def clean_existing():
    conn = get_conn()
    changed = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,body,media,enabled FROM service_posts FOR UPDATE")
            for row in cur.fetchall():
                body = clean_body(row["body"])
                media = json.loads(row["media"]) if isinstance(row["media"], str) else row["media"]
                enabled = row["enabled"] if body or media else 0
                if body != row["body"] or enabled != row["enabled"]:
                    cur.execute("UPDATE service_posts SET body=%s,enabled=%s WHERE id=%s",
                                (body, enabled, row["id"]))
                    changed += 1
        conn.commit()
        return changed
    finally:
        conn.close()


def catalog(service, page=1):
    category(service)
    if type(page) is not int or not 1 <= page <= 100000:
        raise ValueError("请输入有效页码")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM service_posts WHERE category=%s AND enabled=1",
                        (service,))
            total = cur.fetchone()["total"]
            pages = max(1, (total + 19) // 20)
            page = min(page, pages)
            cur.execute("""SELECT * FROM service_posts WHERE category=%s AND enabled=1
                ORDER BY published_at DESC,id DESC LIMIT 20 OFFSET %s""", (service, (page - 1) * 20))
            return {"items": [serialize(row) for row in cur.fetchall()],
                    "page": page, "pages": pages, "total": total}
    finally:
        conn.close()


def detail(service, ident):
    category(service)
    if type(ident) is not int or not 1 <= ident <= 9223372036854775807:
        raise ValueError("服务消息编号无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM service_posts WHERE category=%s AND id=%s AND enabled=1",
                        (service, ident))
            row = cur.fetchone()
            return serialize(row, detail=True) if row else None
    finally:
        conn.close()


def media_file(ident, index):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT media FROM service_posts WHERE id=%s AND enabled=1", (ident,))
            row = cur.fetchone()
            if row is None:
                return None
            media = json.loads(row["media"]) if isinstance(row["media"], str) else row["media"]
            return media[index] if 0 <= index < len(media) else None
    finally:
        conn.close()
