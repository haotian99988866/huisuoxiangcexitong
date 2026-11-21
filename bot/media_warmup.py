import logging
import os
from urllib.parse import quote

import pymysql
import requests

from media_caches import delivery_video_cache, video_cache

logger = logging.getLogger(__name__)


def warm_video_rows(rows, cache, label):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Cannot warm %s videos: Telegram is not configured", label)
        return
    reserved = 0
    for row in rows:
        try:
            cached = cache.get(row["file_id"])
            if cached is not None:
                reserved += cached.stat().st_size
                if reserved >= cache.max_bytes:
                    break
                continue
            with requests.get(
                f"https://api.telegram.org/bot{token}/getFile",
                params={"file_id": row["file_id"]}, timeout=(5, 15),
            ) as info:
                info.raise_for_status()
                payload = info.json()
            result = payload.get("result") or {}
            path, size = result.get("file_path"), result.get("file_size")
            if not payload.get("ok") or not path or not isinstance(size, int):
                raise ValueError("Telegram did not return a video path and size")
            if size <= 0 or size > cache.max_file_bytes:
                raise ValueError("Video exceeds cache file size limit or has no size")
            if reserved + size > cache.max_bytes:
                logger.warning(
                    "%s video cache budget reached; remaining videos use Telegram", label
                )
                break
            with requests.get(
                f"https://api.telegram.org/file/bot{token}/{quote(path, safe='/')}",
                stream=True, timeout=(5, 30),
            ) as upstream:
                upstream.raise_for_status()
                cache.store(
                    row["file_id"], upstream.iter_content(chunk_size=256 * 1024),
                    os.path.splitext(path)[1].lower(), size,
                )
            reserved += size
            logger.info("%s video %s cached (%s bytes)", label, row["id"], size)
        except (requests.RequestException, ValueError, OSError) as exc:
            logger.error(
                "Cannot cache %s video %s (%s); will retry", label, row["id"], type(exc).__name__
            )


def warm_ktv_video_cache(get_db_connection, cache=None):
    cache = video_cache if cache is None else cache
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, file_id FROM ktv_room_videos WHERE venue_id = 3 ORDER BY id DESC"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    warm_video_rows(rows, cache, "KTV")


def warm_delivery_video_cache(get_db_connection, cache=None):
    cache = delivery_video_cache if cache is None else cache
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(m.id) AS id, m.file_id
                FROM delivery_media m
                JOIN delivery_people p ON p.id=m.person_id
                LEFT JOIN (
                    SELECT person_id, MAX(created_at) AS latest_duty
                    FROM delivery_media
                    WHERE media_type='photo' AND archive_only=FALSE
                    GROUP BY person_id
                ) duty ON duty.person_id=p.id
                WHERE m.media_type='video' AND p.deleted_at IS NULL
                      AND p.merged_into_id IS NULL AND p.consent_status='granted'
                GROUP BY m.file_id
                ORDER BY MAX(duty.latest_duty) DESC, MAX(m.id) DESC
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    warm_video_rows(rows, cache, "Delivery")


def maintain_ktv_video_cache(stop, get_db_connection, *, delivery=False, ktv_cache=None, delivery_cache=None):
    ktv_cache = video_cache if ktv_cache is None else ktv_cache
    delivery_cache = delivery_video_cache if delivery_cache is None else delivery_cache
    label = "delivery" if delivery else "KTV"
    while not stop.is_set():
        try:
            if delivery:
                warm_delivery_video_cache(get_db_connection, delivery_cache)
            else:
                warm_ktv_video_cache(get_db_connection, ktv_cache)
        except pymysql.MySQLError:
            logger.exception(
                "Cannot query %s videos for cache warmup; will retry", label
            )
        if stop.wait(60):
            break