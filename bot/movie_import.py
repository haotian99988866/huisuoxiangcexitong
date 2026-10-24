"""从公开频道网页预览(t.me/s/...)补录历史视频。机器人只能收到之后的新帖,历史帖用本脚本导入。

用法: python movie_import.py [--max-pages 60] [--dry-run]
"""
import argparse
import re
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

from movies import (  # noqa: E402
    MOVIE_CHANNEL_ID,
    MOVIE_CHANNEL_USERNAME,
    ensure_tables,
    parse_caption,
    plain_text,
    save_movie,
)

PREVIEW_URL = f"https://t.me/s/{MOVIE_CHANNEL_USERNAME}"
MESSAGE_SPLIT = re.compile(r'(?=<div class="tgme_widget_message_wrap)')


def parse_duration(text):
    if not text:
        return None
    seconds = 0
    for part in text.strip().split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def parse_page(content):
    items = []
    for block in MESSAGE_SPLIT.split(content)[1:]:
        post = re.search(r'data-post="[^/"]+/(\d+)"', block)
        if not post or "message_video_wrap" not in block and "message_video_duration" not in block:
            continue
        text = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
        duration = re.search(r'message_video_duration[^>]*>([^<]+)', block)
        thumb = re.search(r"video_thumb[^>]*background-image:url\('([^']+)'", block)
        posted = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        posted_at = None
        if posted:
            posted_at = datetime.fromisoformat(posted.group(1)).replace(tzinfo=None)
        items.append(
            {
                "message_id": int(post.group(1)),
                "caption": plain_text(text.group(1)) if text else "",
                "duration": parse_duration(duration.group(1)) if duration else None,
                "thumb_url": thumb.group(1) if thumb else None,
                "posted_at": posted_at,
            }
        )
    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dry_run:
        ensure_tables()
    before, saved, skipped = None, 0, 0
    for page in range(args.max_pages):
        response = requests.get(
            PREVIEW_URL, params={"before": before} if before else None, timeout=20
        )
        ids = [int(i) for i in re.findall(r'data-post="[^/"]+/(\d+)"', response.text)]
        if not ids:
            break
        for item in parse_page(response.text):
            if not parse_caption(item["caption"]):
                skipped += 1
                continue
            if args.dry_run:
                print(item["message_id"], parse_caption(item["caption"]))
            else:
                save_movie(MOVIE_CHANNEL_ID, **item)
            saved += 1
        before = min(ids)
        print(f"第 {page + 1} 页完成,已处理 {saved} 条,最早编号 {before}")
        if before <= 1:
            break
        time.sleep(1)
    print(f"导入完成:{saved} 条,跳过(无标题/非视频){skipped} 条")


if __name__ == "__main__":
    main()
