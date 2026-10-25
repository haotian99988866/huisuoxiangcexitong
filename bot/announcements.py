"""Public announcements indexed only from successfully published sync jobs."""
import json
import re
import os
from pathlib import Path

from db import get_conn


def media_directory():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Huisuo" / "announcement-media"


def local_media_path(filename):
    if not isinstance(filename, str) or not re.fullmatch(r"\d+-\d+\.(mp4|webm|jpg)", filename):
        raise ValueError("公告媒体文件名无效")
    return media_directory() / filename


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS channel_announcements (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                job_key VARCHAR(255) NOT NULL UNIQUE,
                message_ids JSON NOT NULL,
                body MEDIUMTEXT NOT NULL,
                media JSON NOT NULL,
                published_at DATETIME NOT NULL,
                enabled TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_announcement_date (enabled,published_at,id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
    finally:
        conn.close()


def save(job_key, message_ids, body, media, published_at):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO channel_announcements (job_key,message_ids,body,media,published_at)"
                " VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE job_key=VALUES(job_key)",
                (job_key, json.dumps(message_ids), body, json.dumps(media),
                 published_at.replace(tzinfo=None)),
            )
        conn.commit()
    finally:
        conn.close()


def indexed_keys():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT job_key FROM channel_announcements")
            return {row["job_key"] for row in cur.fetchall()}
    finally:
        conn.close()


def summary(body, media):
    text = re.sub(r"\s+", " ", body).strip()
    return text[:90] + ("…" if len(text) > 90 else "") if text else (
        "视频公告" if any(item["kind"] == "video" for item in media) else "图片公告"
    )


def reading_text(body, media):
    lines = body.strip().splitlines()
    first = lines[0].strip() if lines else ""
    tag = re.match(r"^#([^\s:：]{1,20})[：:]\s*", first)
    category = tag[1] if tag else "频道资讯"
    headline = first[tag.end():].strip() if tag else first
    if not headline:
        headline = summary("", media) if media else "文字公告"
    complete_title = len(headline) <= 120
    title = headline if complete_title else headline[:120] + "…"
    content = "\n".join(lines[1:]).strip() if first and complete_title else body.strip()
    excerpt = re.sub(r"\s+", " ", content).strip()
    return {"title": title, "category": category, "content": content,
            "excerpt": excerpt[:100] + ("…" if len(excerpt) > 100 else "")}


def serialize(row, detail=False):
    media = json.loads(row["media"]) if isinstance(row["media"], str) else row["media"]
    reading = reading_text(row["body"], media)
    cover_index = next((index for index, item in enumerate(media) if item["kind"] == "photo"), None)
    result = {
        "id": row["id"], "summary": summary(row["body"], media),
        "title": reading["title"], "category": reading["category"], "excerpt": reading["excerpt"],
        "cover": {"kind": "photo", "url": f"/announcement-media/{row['id']}/{cover_index}"} if cover_index is not None else None,
        "has_video": any(item["kind"] == "video" for item in media),
        "published_at": row["published_at"].isoformat(timespec="seconds") + "Z",
        "media_count": len(media),
    }
    if detail:
        result["body"] = row["body"]
        result["content"] = reading["content"]
        result["media"] = [
            {"kind": item["kind"], "url": f"/announcement-media/{row['id']}/{index}"}
            for index, item in enumerate(media)
        ]
    return result


def catalog(page=1):
    if type(page) is not int or not 1 <= page <= 100000:
        raise ValueError("请输入有效页码")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM channel_announcements WHERE enabled=1")
            total = cur.fetchone()["total"]
            pages = max(1, (total + 19) // 20)
            page = min(page, pages)
            cur.execute(
                "SELECT * FROM channel_announcements WHERE enabled=1"
                " ORDER BY published_at DESC,id DESC LIMIT 20 OFFSET %s", ((page - 1) * 20,),
            )
            return {"items": [serialize(row) for row in cur.fetchall()],
                    "page": page, "pages": pages, "total": total}
    finally:
        conn.close()


def detail(ident):
    if type(ident) is not int or not 1 <= ident <= 9223372036854775807:
        raise ValueError("公告编号无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM channel_announcements WHERE id=%s AND enabled=1", (ident,))
            row = cur.fetchone()
            return serialize(row, detail=True) if row else None
    finally:
        conn.close()


def media_file(ident, index):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT media FROM channel_announcements WHERE id=%s AND enabled=1", (ident,))
            row = cur.fetchone()
            if not row:
                return None
            media = json.loads(row["media"]) if isinstance(row["media"], str) else row["media"]
            return media[index] if 0 <= index < len(media) else None
    finally:
        conn.close()
