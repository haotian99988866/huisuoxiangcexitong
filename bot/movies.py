import html
import logging
import re

from db import get_conn

logger = logging.getLogger(__name__)

MOVIE_CHANNEL_ID = "-1003920194464"
MOVIE_CHANNEL_USERNAME = "haotianyingyuan"

# 频道导航里的类型;标签命中其中之一就归到该类型
CATEGORY_ALIASES = {
    "电视剧": "电视剧", "剧集": "电视剧",
    "综艺": "综艺",
    "电影": "电影",
    "动漫": "动漫/日漫", "日漫": "动漫/日漫", "国漫": "动漫/日漫", "动画": "动漫/日漫",
    "英剧": "英/美剧", "美剧": "英/美剧", "英美剧": "英/美剧",
    "日韩剧": "日韩剧", "日剧": "日韩剧", "韩剧": "日韩剧",
    "网剧": "网剧",
    "纪录片": "纪录片",
}
CATEGORY_ORDER = ["电视剧", "综艺", "电影", "动漫/日漫", "英/美剧", "日韩剧", "网剧", "纪录片"]
# 没有类型标签时,按剧名关键字指定类型;再没有就看弱标签
SERIES_CATEGORY_KEYWORDS = {
    "蜡笔小新": "动漫/日漫",
    "喜羊羊": "动漫/日漫",
    "爱情公寓": "电视剧",
    "武林外传": "电视剧",
    "医馆笑传": "电视剧",
    "书卷一梦": "电视剧",
}
WEAK_TAG_CATEGORY = {"下饭剧": "电视剧", "追剧": "电视剧"}
DEFAULT_CATEGORY = "其他"
QUALITY_PATTERN = re.compile(r"^(?:4K|8K|2K|1080P?|720P?|蓝光|超清|高清|HDR|杜比.*|国语|粤语|中字|中英.*)$", re.I)
TAG_PATTERN = re.compile(r"#([^\s#]+)")
TITLE_SERIES_PATTERN = re.compile(r"《([^》]+)》")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS movie_videos (
    channel_id VARCHAR(30) NOT NULL,
    message_id BIGINT NOT NULL,
    title VARCHAR(255) NOT NULL,
    series VARCHAR(255) NOT NULL,
    category VARCHAR(50) NOT NULL,
    tags VARCHAR(500) NOT NULL DEFAULT '',
    duration INT NULL,
    thumb_file_id VARCHAR(255) NULL,
    thumb_url VARCHAR(600) NULL,
    posted_at DATETIME NULL,
    PRIMARY KEY (channel_id, message_id),
    INDEX idx_movie_category (category),
    INDEX idx_movie_series (series(100))
)
"""


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_SQL)
        conn.commit()
    finally:
        conn.close()


def parse_caption(text):
    """解析视频说明的第一行:《剧名》集标题 #4K #剧名 #类型 。返回 None 表示没有可用标题。"""
    text = (text or "").strip()
    first_line = text.split("\n", 1)[0]
    tags = TAG_PATTERN.findall(text)
    title = TAG_PATTERN.sub("", first_line).strip()
    if not title:
        return None

    category = None
    others = []
    for tag in tags:
        if tag in CATEGORY_ALIASES:
            category = category or CATEGORY_ALIASES[tag]
        elif not QUALITY_PATTERN.match(tag):
            others.append(tag)

    match = TITLE_SERIES_PATTERN.search(title)
    if match:
        series = match.group(1).strip()
    elif others:
        series = others[0]
    else:
        series = title
    if not category:
        category = next(
            (cat for key, cat in SERIES_CATEGORY_KEYWORDS.items() if key in series), None
        ) or next((WEAK_TAG_CATEGORY[t] for t in tags if t in WEAK_TAG_CATEGORY), None)
    return {
        "title": title[:255],
        "series": series[:255],
        "category": category or DEFAULT_CATEGORY,
        "tags": ",".join(tags)[:500],
    }


def save_movie(channel_id, message_id, caption, duration=None, thumb_file_id=None,
               thumb_url=None, posted_at=None):
    parsed = parse_caption(caption)
    if not parsed:
        return False
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO movie_videos
                    (channel_id, message_id, title, series, category, tags, duration,
                     thumb_file_id, thumb_url, posted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title), series = VALUES(series),
                    category = VALUES(category), tags = VALUES(tags),
                    duration = COALESCE(VALUES(duration), duration),
                    thumb_file_id = COALESCE(VALUES(thumb_file_id), thumb_file_id),
                    thumb_url = COALESCE(VALUES(thumb_url), thumb_url),
                    posted_at = COALESCE(VALUES(posted_at), posted_at)
                """,
                (
                    str(channel_id), message_id, parsed["title"], parsed["series"],
                    parsed["category"], parsed["tags"], duration, thumb_file_id,
                    thumb_url, posted_at,
                ),
            )
        conn.commit()
        return True
    finally:
        conn.close()


PROMO_PATTERNS = (
    re.compile(r"来这选择.*"),
    re.compile(r"🤗?\s*每天持续更新.*?想看什么剧.*?[）)]"),
)


def strip_promo(caption):
    """只删除推广文字本身,同一行里的 # 标签保留。"""
    text = caption or ""
    for pattern in PROMO_PATTERNS:
        text = pattern.sub("", text)
    if text == (caption or ""):
        return text
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def clean_caption(bot, message):
    """视频说明含其他频道的导航文字时,后台编辑帖子去掉(遇到限流自动等待重试)。返回是否需要清理。"""
    caption = message.caption or ""
    cleaned = strip_promo(caption)
    if cleaned == caption or not cleaned:
        return False
    message.caption = cleaned
    chat_id, message_id = message.chat.id, message.message_id

    def work():
        import re
        import time
        for _ in range(6):
            try:
                bot.edit_message_caption(caption=cleaned, chat_id=chat_id, message_id=message_id)
                return
            except Exception as exc:
                wait = re.search(r"retry after (\d+)", str(exc))
                if not wait:
                    print(f"清理视频说明失败 {message_id}: {exc}")
                    return
                time.sleep(int(wait.group(1)) + 1)

    import threading
    threading.Thread(target=work, daemon=True).start()
    return True


def save_movie_message(message):
    """频道里新发/编辑的视频帖子入库。返回是否已处理。"""
    if str(message.chat.id) != MOVIE_CHANNEL_ID:
        return False
    video = getattr(message, "video", None)
    document = getattr(message, "document", None)
    media = video or (document if (document and (document.mime_type or "").startswith("video/")) else None)
    if not media:
        return False
    thumb = getattr(media, "thumbnail", None) or getattr(media, "thumb", None)
    from datetime import datetime
    return save_movie(
        MOVIE_CHANNEL_ID,
        message.message_id,
        message.caption or "",
        duration=getattr(media, "duration", None),
        thumb_file_id=getattr(thumb, "file_id", None),
        posted_at=datetime.fromtimestamp(message.date),
    )


def _post_url(message_id):
    return f"https://t.me/{MOVIE_CHANNEL_USERNAME}/{message_id}"


def _thumb(row):
    if row.get("thumb_file_id"):
        return f"movie-thumb/{row['message_id']}"
    return row.get("thumb_url")


def movie_catalog(category=None, query=None, limit=120):
    limit = max(1, min(int(limit), 200))
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT category, COUNT(DISTINCT series) AS n FROM movie_videos GROUP BY category"
            )
            counts = {row["category"]: row["n"] for row in cursor.fetchall()}

            conditions, params = [], []
            if category:
                conditions.append("category = %s")
                params.append(category)
            if query:
                like = f"%{query}%"
                conditions.append("(series LIKE %s OR title LIKE %s OR tags LIKE %s)")
                params.extend([like, like, like])
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            cursor.execute(
                f"""
                SELECT series, category, COUNT(DISTINCT title) AS episodes,
                       MIN(message_id) AS first_id, MAX(message_id) AS last_id
                FROM movie_videos {where}
                GROUP BY series, category
                ORDER BY last_id DESC LIMIT %s
                """,
                params + [limit],
            )
            series = cursor.fetchall()

            covers = {}
            if series:
                ids = [row["first_id"] for row in series]
                marks = ",".join(["%s"] * len(ids))
                cursor.execute(
                    f"SELECT message_id, thumb_file_id, thumb_url FROM movie_videos "
                    f"WHERE channel_id = %s AND message_id IN ({marks})",
                    [MOVIE_CHANNEL_ID] + ids,
                )
                covers = {row["message_id"]: _thumb(row) for row in cursor.fetchall()}
    finally:
        conn.close()

    ordered = [name for name in CATEGORY_ORDER if name in counts]
    ordered += sorted(name for name in counts if name not in CATEGORY_ORDER)
    return {
        "categories": [{"name": name, "count": counts[name]} for name in ordered],
        "total": sum(counts.values()),
        "series": [
            {
                "name": row["series"],
                "category": row["category"],
                "episodes": row["episodes"],
                "cover": covers.get(row["first_id"]),
                "url": _post_url(row["last_id"]) if row["episodes"] == 1 else None,
            }
            for row in series
        ],
    }


def movie_episodes(series, limit=500):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            # 同一集重复发布时只保留最新一条
            cursor.execute(
                "SELECT message_id, title, duration, thumb_file_id, thumb_url "
                "FROM movie_videos WHERE channel_id = %s AND series = %s AND message_id IN ("
                "  SELECT MAX(message_id) FROM movie_videos "
                "  WHERE channel_id = %s AND series = %s GROUP BY title) "
                "ORDER BY message_id LIMIT %s",
                (MOVIE_CHANNEL_ID, series, MOVIE_CHANNEL_ID, series, limit),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        {
            "title": row["title"],
            "duration": row["duration"],
            "thumb": _thumb(row),
            "url": _post_url(row["message_id"]),
        }
        for row in rows
    ]


def thumb_file_id(message_id):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT thumb_file_id FROM movie_videos WHERE channel_id = %s AND message_id = %s",
                (MOVIE_CHANNEL_ID, message_id),
            )
            row = cursor.fetchone()
        return row["thumb_file_id"] if row else None
    finally:
        conn.close()


def plain_text(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment or "")
    return html.unescape(re.sub(r"<[^>]+>", "", text))
