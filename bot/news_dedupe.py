"""Conservative, fifteen-hour content matching before news publication."""
from difflib import SequenceMatcher
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
import unicodedata


WINDOW_SECONDS = 15 * 60 * 60
MIN_SIMILAR_LENGTH = 80
SIMILARITY_THRESHOLD = 0.95


def normalized_body(body):
    return "".join(char for char in unicodedata.normalize("NFKC", body).casefold()
                   if char.isalnum() or char in ".%+-")


def similar_body(first, second):
    if not first or not second:
        return False
    if first == second:
        return True
    if min(len(first), len(second)) < MIN_SIMILAR_LENGTH:
        return False
    if re.findall(r"[+-]?\d+(?:\.\d+)?%?", first) != re.findall(r"[+-]?\d+(?:\.\d+)?%?", second):
        return False
    matcher = SequenceMatcher(None, first, second, autojunk=False)
    return matcher.quick_ratio() >= SIMILARITY_THRESHOLD and matcher.ratio() >= SIMILARITY_THRESHOLD


def media_digest(paths):
    if not paths:
        return None
    hashes = []
    for path in paths:
        with Path(path).open("rb") as stream:
            hashes.append(hashlib.file_digest(stream, "sha256").hexdigest())
    return hashlib.sha256("\n".join(sorted(hashes)).encode("ascii")).hexdigest()


class NewsDeduper:
    def __init__(self, connection):
        self.connection = connection
        connection.execute("""
            CREATE TABLE IF NOT EXISTS news_fingerprints (
                source INTEGER NOT NULL, target TEXT NOT NULL, job_key TEXT NOT NULL,
                body TEXT NOT NULL, media_hash TEXT, seen_at REAL NOT NULL,
                PRIMARY KEY(source,target,job_key)
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_fingerprint_time ON news_fingerprints(target,seen_at)"
        )
        connection.commit()

    def find(self, job, body, media_hash=None):
        normalized = normalized_body(body)
        rows = self.connection.execute(
            "SELECT f.* FROM news_fingerprints f JOIN jobs j"
            " ON j.source=f.source AND j.target=f.target AND j.job_key=f.job_key"
            " WHERE f.target=? AND f.seen_at>=? AND (j.status IN ('sent','sending','uncertain')"
            " OR (j.status='skipped' AND j.error='Owner reviewed: retain existing content; skip isolated task'))"
            " AND NOT (f.source=? AND f.job_key=?) ORDER BY f.seen_at,f.source,f.job_key",
            (job["target"], time.time() - WINDOW_SECONDS, job["source"], job["job_key"]),
        )
        for row in rows:
            if normalized:
                if not similar_body(normalized, row["body"]):
                    continue
                # Short captions alone must not merge unrelated pictures or videos.
                if min(len(normalized), len(row["body"])) < MIN_SIMILAR_LENGTH and (
                    media_hash != row["media_hash"]):
                    continue
                return row
            if not row["body"] and media_hash and media_hash == row["media_hash"]:
                return row
        return None

    def remember(self, job, body, media_hash, seen_at):
        self.connection.execute(
            "INSERT INTO news_fingerprints(source,target,job_key,body,media_hash,seen_at)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(source,target,job_key) DO UPDATE SET"
            " body=excluded.body,media_hash=excluded.media_hash,seen_at=excluded.seen_at",
            (job["source"], job["target"], job["job_key"], normalized_body(body), media_hash, seen_at),
        )

    def begin_send(self, job, body, media_hash):
        with self.connection:
            self.remember(job, body, media_hash, time.time())
            self.connection.execute(
                "UPDATE jobs SET status='sending',result_ids=NULL,error=NULL"
                " WHERE source=? AND target=? AND job_key=?",
                (job["source"], job["target"], job["job_key"]),
            )


async def seed_recent_news(bot, target, store, directory):
    """Fingerprint recent published jobs without republishing or changing their states."""
    from channel_sync_clean import clean_text
    from db import get_conn
    from news_sources import announcement_job_key
    from telethon import types

    deduper = store.news_deduper
    cutoff = time.time() - WINDOW_SECONDS
    database = get_conn()
    try:
        with database.cursor() as cursor:
            cursor.execute(
                "SELECT job_key FROM channel_announcements WHERE published_at>=%s",
                (datetime.fromtimestamp(cutoff, timezone.utc).replace(tzinfo=None),),
            )
            recent_keys = {row["job_key"] for row in cursor.fetchall()}
    finally:
        database.close()
    jobs = store.connection.execute(
        "SELECT j.*,(julianday(j.created)-2440587.5)*86400 AS created_epoch"
        " FROM jobs j LEFT JOIN news_fingerprints f"
        " ON j.source=f.source AND j.target=f.target AND j.job_key=f.job_key"
        " WHERE j.target=? AND j.status='sent' AND f.source IS NULL ORDER BY j.rowid",
        (str(target.id),),
    ).fetchall()
    for job in jobs:
        if job["created_epoch"] < cutoff and announcement_job_key(job) not in recent_keys:
            continue
        ids = json.loads(job["result_ids"] or "[]")
        if not ids:
            raise ValueError("Recent published news has no result IDs; inspect before deduplication.")
        messages = await bot.get_messages(target, ids=ids)
        if len(messages) != len(ids) or any(message is None for message in messages):
            raise ValueError("Recent published news unavailable; cannot seed deduplication history.")
        published_at = min(message.date for message in messages)
        seen_at = published_at.replace(tzinfo=timezone.utc).timestamp() if (
            published_at.tzinfo is None) else published_at.timestamp()
        if seen_at < cutoff:
            continue
        body = "\n\n".join(clean_text(message.message, message.entities) for message in messages)
        media_messages = [message for message in messages if message.media
                          and not isinstance(message.media, types.MessageMediaWebPage)]
        digest = None
        if len(normalized_body(body)) < MIN_SIMILAR_LENGTH and media_messages:
            with tempfile.TemporaryDirectory(prefix="news-history-", dir=directory) as temporary:
                paths = []
                for index, message in enumerate(media_messages):
                    path = await bot.download_media(message, file=str(Path(temporary) / str(index)))
                    if not path:
                        raise ValueError("Historical news media download failed; no fingerprint recorded.")
                    paths.append(path)
                digest = await asyncio.to_thread(media_digest, paths)
        with store.connection:
            deduper.remember(job, body, digest, seen_at)
