"""Largest-file selection and resumable cleanup for the owned short-drama channel."""
import asyncio
import hashlib
import json
import logging
import re
import unicodedata

from telethon import types

from db import get_conn

logger = logging.getLogger(__name__)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS short_drama_versions (
    message_id BIGINT PRIMARY KEY,
    compilation_key CHAR(64) NOT NULL,
    file_size BIGINT NOT NULL,
    deleted TINYINT(1) NOT NULL DEFAULT 0,
    INDEX idx_drama_compilation (compilation_key, deleted, file_size)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

VISIBLE_SQL = """
EXISTS (
    SELECT 1 FROM short_drama_versions v
    WHERE v.message_id=movie_videos.message_id AND v.deleted=0
      AND NOT EXISTS (
          SELECT 1 FROM short_drama_versions bigger
          WHERE bigger.compilation_key=v.compilation_key AND bigger.deleted=0
            AND (bigger.file_size>v.file_size
                 OR (bigger.file_size=v.file_size AND bigger.message_id<v.message_id))
      )
)
"""


def compilation_key(caption):
    from movies import parse_caption
    parsed = parse_caption(caption)
    title = unicodedata.normalize("NFKC", parsed["title"] if parsed else "")
    match = re.search(r"《([^》]+)》", title)
    if not match:
        raise ValueError("Short drama needs a bracketed title for duplicate selection")
    # Keep season/episode ranges and other version qualifiers; never merge by series alone.
    parts = [match[1], title[match.end():]]
    identity = ["".join(char for char in part.casefold() if char.isalnum() or char in "-~～至到")
                for part in parts]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()


def register(cursor, message_id, caption, file_size):
    if type(file_size) is not int or file_size <= 0:
        raise ValueError("Short drama file size is unavailable; cannot select largest version")
    key = compilation_key(caption)
    cursor.execute(
        "INSERT INTO short_drama_versions(message_id,compilation_key,file_size) VALUES (%s,%s,%s)"
        " ON DUPLICATE KEY UPDATE compilation_key=VALUES(compilation_key),file_size=VALUES(file_size)",
        (message_id, key, file_size),
    )
    cursor.execute("SELECT deleted FROM short_drama_versions WHERE message_id=%s", (message_id,))
    return not cursor.fetchone()["deleted"]


def largest(caption):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT message_id,file_size FROM short_drama_versions"
                " WHERE compilation_key=%s AND deleted=0 ORDER BY file_size DESC,message_id LIMIT 1",
                (compilation_key(caption),),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def duplicates():
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT v.message_id,v.compilation_key,v.file_size,"
                " w.message_id AS winner_id,w.file_size AS winner_size FROM short_drama_versions v"
                " JOIN short_drama_versions w ON w.compilation_key=v.compilation_key AND w.deleted=0"
                " WHERE v.deleted=0 AND v.message_id<>w.message_id"
                " AND NOT EXISTS (SELECT 1 FROM short_drama_versions bigger"
                " WHERE bigger.compilation_key=w.compilation_key AND bigger.deleted=0"
                " AND (bigger.file_size>w.file_size"
                " OR (bigger.file_size=w.file_size AND bigger.message_id<w.message_id)))"
                " ORDER BY v.message_id"
            )
            return cursor.fetchall()
    finally:
        conn.close()


def mark_deleted(message_ids):
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.executemany("UPDATE short_drama_versions SET deleted=1 WHERE message_id=%s",
                               [(message_id,) for message_id in message_ids])
        conn.commit()
    finally:
        conn.close()


async def cleanup(user, target, store):
    from movies import DRAMA_CHANNEL_ID
    if f"-100{target.id}" != DRAMA_CHANNEL_ID or not store.source.short_drama:
        raise ValueError("Short drama cleanup must use the configured owned target")
    pending = await asyncio.to_thread(duplicates)
    peer = types.PeerChannel(target.id)
    for offset in range(0, len(pending), 50):
        batch = pending[offset:offset + 50]
        ids = sorted({row[key] for row in batch for key in ("message_id", "winner_id")})
        messages = await user.get_messages(peer, ids=ids)
        if len(messages) != len(ids):
            raise ValueError("Short drama batch was incomplete; cleanup stopped")
        by_id = {message.id: message for message in messages if message is not None}
        delete_ids = []
        for row in batch:
            kept = by_id.get(row["winner_id"])
            if (not kept or not getattr(kept, "document", None)
                    or compilation_key(kept.message) != row["compilation_key"]
                    or kept.document.size != row["winner_size"]
                    or (kept.document.size, -kept.id) <= (row["file_size"], -row["message_id"])):
                raise ValueError("Largest retained video could not be verified; cleanup stopped")
            loser = by_id.get(row["message_id"])
            if loser is None:
                continue
            document = getattr(loser, "document", None)
            if (not document or compilation_key(loser.message) != row["compilation_key"]
                    or document.size != row["file_size"]):
                raise ValueError("Duplicate video identity or size changed; cleanup stopped")
            delete_ids.append(loser.id)
        if delete_ids:
            await user.delete_messages(peer, delete_ids)
            verification = await user.get_messages(peer, ids=delete_ids)
            if len(verification) != len(delete_ids) or any(message is not None for message in verification):
                raise RuntimeError("Duplicate video deletion was not confirmed")
        message_ids = [row["message_id"] for row in batch]
        with store.connection:
            store.connection.executemany("DELETE FROM owned WHERE message=?", [(ident,) for ident in message_ids])
            store.connection.executemany(
                "UPDATE jobs SET status='excluded',error=? WHERE result_id=?",
                [("Smaller duplicate removed from owned short-drama channel; largest file retained", ident)
                 for ident in message_ids])
        await asyncio.to_thread(mark_deleted, message_ids)
        logger.info("Short drama duplicate batch deleted target=%s messages=%s", target.id, message_ids)
        await asyncio.sleep(3)
    return len(pending)
