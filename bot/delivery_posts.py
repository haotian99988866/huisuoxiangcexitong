"""Shared public delivery media grouping, deduplication and adult filtering."""
from image_hash import ImageDeduper

MIN_AGE = 18


def _message_link(row):
    channel_id = str(row.get("channel_id") or "")
    if not channel_id or not row.get("message_id"):
        return None
    numeric = channel_id[4:] if channel_id.startswith("-100") else channel_id.lstrip("-")
    return f"https://t.me/c/{numeric}/{row['message_id']}"


def build_delivery_posts(rows, categories=None, *, deduplicate=True):
    """Captioned albums and mixed photo/video albums form one post."""
    categories = categories or {}
    rows = [row for row in rows if row["media_type"] != "text"]
    group_captions = {}
    group_types = {}
    for row in rows:
        group_id = row["media_group_id"]
        if not group_id:
            continue
        group_types.setdefault(group_id, set()).add(row["media_type"])
        caption = (row["caption"] or "").strip()
        if caption and not group_captions.get(group_id):
            group_captions[group_id] = caption
    for group_id, types in group_types.items():
        if "video" in types and "photo" in types:
            group_captions.setdefault(group_id, "")

    posts = []
    grouped = {}
    for row in rows:
        media = {"id": row["id"], "type": row["media_type"]}
        group_id = row["media_group_id"]
        if group_id in group_captions:
            post = grouped.get(group_id)
            if post is None:
                post = {
                    "id": f"g{row['id']}", "description": group_captions[group_id],
                    "category": categories.get(row["id"]), "age": None,
                    "media": [], "_hashes": [],
                }
                grouped[group_id] = post
                posts.append(post)
            post["media"].insert(0, media)
            post["link"] = _message_link(row) or post.get("link")
            if row.get("image_hash"):
                post["_hashes"].append(row["image_hash"])
            post["age"] = _min_age(post["age"], row.get("age"))
            continue
        posts.append({
            "id": f"m{row['id']}", "description": (row["caption"] or "").strip(),
            "category": categories.get(row["id"]), "age": row.get("age"),
            "media": [media], "link": _message_link(row), "_ocr": row.get("ocr_text"),
            "_hashes": [row["image_hash"]] if row.get("image_hash") else [],
        })
    return dedupe_posts(posts) if deduplicate else posts


def _post_key(post):
    text = "".join((post.get("description") or "").split())
    if text:
        return ("d", text, len(post["media"]))
    ocr = "".join((post.get("_ocr") or "").split())
    return ("o", ocr, len(post["media"])) if len(ocr) >= 12 else None


def dedupe_posts(posts):
    seen = {}
    images = ImageDeduper()
    kept_by_id = {}
    unique = []
    for post in posts:
        own = id(post)
        original = None
        for image_hash in post.get("_hashes", []):
            key = images.key_for(image_hash, own)
            if key != own:
                original = kept_by_id.get(key)
        key = _post_key(post)
        if original is None and key is not None:
            original = seen.get(key)
            if original is None:
                seen[key] = post
        post.pop("_ocr", None)
        post.pop("_hashes", None)
        if original is not None:
            if original.get("age") is None:
                original["age"] = post.get("age")
            continue
        kept_by_id[own] = post
        unique.append(post)
    return unique


def _min_age(current, new):
    if current is None:
        return new
    if new is None:
        return current
    return min(current, new)


def filter_adult_posts(posts):
    return [post for post in posts if post.get("age") is not None and post["age"] >= MIN_AGE]


def public_person_media(rows, categories=None, category=None):
    albums = build_delivery_posts(rows, categories, deduplicate=False)
    posts = filter_adult_posts(dedupe_posts([dict(post) for post in albums]))
    if category:
        posts = [post for post in posts if post.get("category") in (None, category)]
    media = [item for post in posts for item in post["media"]]
    seen = {item["id"] for item in media}
    # A duplicate cover must not hide the original adult album's videos.
    for album in filter_adult_posts(albums):
        if category and album.get("category") not in (None, category):
            continue
        for item in album["media"]:
            if item["type"] == "video" and item["id"] not in seen:
                media.append(item)
                seen.add(item["id"])
    return media


def attendance_publication(rows, media, start, end, shift):
    if shift != "晚班":
        return None
    visible_ids = {item["id"] for item in media}
    return max((
        row["created_at"] for row in rows
        if row["id"] in visible_ids and row["media_type"] == "photo"
        and not row.get("archive_only", False)
        and start <= row["created_at"] < end
    ), default=None)
