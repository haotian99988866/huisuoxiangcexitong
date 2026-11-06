"""Standalone, opt-in Telegram account listener; never starts with the main bot."""

import argparse
import asyncio
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile

from dotenv import load_dotenv
from telethon import TelegramClient, events, errors, types, utils

from channel_sync_clean import clean_news_text, is_news_advertisement, exceeds_news_limits, cleaned_news_emoji_count
from operations_health import ensure_health_table, record_worker_failure, record_worker_heartbeat
from news_sources import NEWS_SOURCES, announcement_job_key
from news_dedupe import NewsDeduper, MIN_SIMILAR_LENGTH, media_digest, normalized_body, seed_recent_news


logger = logging.getLogger("channel_sync")
CONFIG_PATH = Path(__file__).with_name(".channel-sync.env")


class OverlappingSyncJob(ValueError):
    pass


def split_telegram_text(text, limit=4096):
    chunks = []
    start = units = 0
    for index, character in enumerate(text):
        size = 2 if ord(character) > 0xFFFF else 1
        if units + size > limit:
            chunks.append(text[start:index])
            start, units = index, 0
        units += size
    if start < len(text):
        chunks.append(text[start:])
    return chunks


def state_directory():
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return base / "Huisuo" / "channel-sync"


def credentials():
    load_dotenv(Path(__file__).with_name(".env"))
    load_dotenv(CONFIG_PATH, override=True)
    api_id = os.environ.get("CHANNEL_SYNC_API_ID", "")
    api_hash = os.environ.get("CHANNEL_SYNC_API_HASH", "")
    if not api_id.isdecimal() or not api_hash:
        raise ValueError("Fill CHANNEL_SYNC_API_ID and CHANNEL_SYNC_API_HASH in the local config.")
    return int(api_id), api_hash


class JobStore:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                source INTEGER NOT NULL, target TEXT NOT NULL, job_key TEXT NOT NULL,
                message_ids TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                result_ids TEXT, error TEXT, created TEXT NOT NULL,
                PRIMARY KEY (source, target, job_key)
            )
        """)
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(jobs)")}
        if "source_published_at" not in columns:
            self.connection.execute("ALTER TABLE jobs ADD COLUMN source_published_at REAL")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS sync_progress (
                source INTEGER NOT NULL, target TEXT NOT NULL,
                boundary INTEGER NOT NULL, scanned_id INTEGER NOT NULL,
                PRIMARY KEY (source,target)
            )
        """)
        ensure_health_table(self.connection)
        self.news_deduper = NewsDeduper(self.connection)
        self.connection.commit()

    def enqueue(self, source, target, ids, published_at=None):
        ids = sorted(set(ids))
        key = ",".join(map(str, ids))
        with self.connection:
            existing = self.connection.execute(
                "SELECT job_key FROM jobs WHERE source=? AND target=? AND job_key=?",
                (source, target, key),
            ).fetchone()
            if existing:
                return False
            overlap = self.connection.execute(
                "SELECT job_key FROM jobs, json_each(jobs.message_ids) AS message"
                " WHERE source=? AND target=? AND message.value IN ("
                + ",".join("?" for _ in ids) + ") LIMIT 1",
                (source, target, *ids),
            ).fetchone() if ids else None
            if overlap:
                raise OverlappingSyncJob(
                    f"Overlapping sync jobs {key} and {overlap['job_key']}; inspect before sending.")
            self.connection.execute(
                "INSERT OR IGNORE INTO jobs (source,target,job_key,message_ids,created,source_published_at)"
                " VALUES (?,?,?,?,?,?)",
                (source, target, key, json.dumps(ids),
                 datetime.now(timezone.utc).isoformat(), published_at),
            )
        return True

    def record_overlap(self, source, target, messages, error):
        ids = sorted({message.id for message in messages})
        dates = [message.date for message in messages if getattr(message, "date", None)]
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO jobs"
                " (source,target,job_key,message_ids,created,source_published_at,status,error)"
                " VALUES (?,?,?,?,?,?,'uncertain',?)",
                (source, target, ",".join(map(str, ids)), json.dumps(ids),
                 datetime.now(timezone.utc).isoformat(), min(dates).timestamp() if dates else None,
                 f"Historical album overlap; not republished: {error}"),
            )

    def progress(self, source, target, latest):
        row = self.connection.execute(
            "SELECT boundary,scanned_id FROM sync_progress WHERE source=? AND target=?",
            (source, target),
        ).fetchone()
        if row:
            return row
        oldest = self.connection.execute(
            "SELECT MIN(message.value) FROM jobs,json_each(jobs.message_ids) AS message"
            " WHERE source=? AND target=?", (source, target),
        ).fetchone()[0]
        boundary = oldest - 1 if oldest is not None else latest
        with self.connection:
            self.connection.execute(
                "INSERT INTO sync_progress (source,target,boundary,scanned_id) VALUES (?,?,?,?)",
                (source, target, boundary, boundary),
            )
        return self.progress(source, target, latest)

    def advance(self, source, target, message_id):
        with self.connection:
            self.connection.execute(
                "UPDATE sync_progress SET scanned_id=MAX(scanned_id,?) WHERE source=? AND target=?",
                (message_id, source, target),
            )

    def next(self, source, target):
        return self.connection.execute(
            "SELECT * FROM jobs WHERE source=? AND target=? AND status='queued'"
            " ORDER BY rowid LIMIT 1", (source, target),
        ).fetchone()

    def next_news(self, sources, target):
        return self.connection.execute(
            "SELECT * FROM jobs WHERE target=? AND status='queued' AND source IN ("
            + ",".join("?" for _ in sources) + ") ORDER BY COALESCE(source_published_at,"
            " (julianday(created)-2440587.5)*86400),rowid LIMIT 1",
            (target, *sources),
        ).fetchone()

    def update(self, job, status, *, result_ids=None, error=None):
        with self.connection:
            self.connection.execute(
                "UPDATE jobs SET status=?,result_ids=?,error=?"
                " WHERE source=? AND target=? AND job_key=?",
                (status, json.dumps(result_ids) if result_ids is not None else None,
                 error, job["source"], job["target"], job["job_key"]),
            )

    def recover(self):
        with self.connection:
            self.connection.execute(
                "UPDATE jobs SET status='uncertain',error='Interrupted during send; inspect target manually'"
                " WHERE status='sending'"
            )

    def close(self):
        self.connection.close()


def enqueue_new_messages(store, source_id, target_id, boundary, messages):
    messages = sorted(messages, key=lambda message: message.id)
    if not messages or any(message.id <= boundary for message in messages):
        return
    if any(getattr(message, "action", None) for message in messages):
        return
    dates = [message.date for message in messages if getattr(message, "date", None)]
    published_at = min(dates).timestamp() if dates else None
    if store.enqueue(source_id, str(target_id), [message.id for message in messages], published_at):
        logger.info("Queued source message(s) %s", [message.id for message in messages])


async def complete_album(user, source, messages):
    if not messages or not getattr(messages[0], "grouped_id", None):
        return messages
    group_id = messages[0].grouped_id
    if any(message.grouped_id != group_id for message in messages):
        raise ValueError("Album job contains different media groups.")
    # Telegram albums contain up to ten messages; events can omit the caption-bearing member.
    ids = [message.id for message in messages]
    nearby = await user.get_messages(
        source, ids=list(range(max(1, min(ids) - 9), max(ids) + 10)),
    )
    album = sorted(
        (message for message in nearby if message is not None and message.grouped_id == group_id),
        key=lambda message: message.id,
    )
    if not set(ids).issubset({message.id for message in album}):
        raise ValueError("Source album member was deleted or is unavailable.")
    if len(album) > 10:
        raise ValueError("Source album exceeds Telegram's ten-item limit.")
    return album


async def reconcile_messages(user, source, target, store, boundary):
    progress = store.progress(source.id, str(target.id), boundary)
    messages = [message async for message in user.iter_messages(
        source, min_id=progress["scanned_id"], reverse=True, limit=201,
    )]
    groups = []
    for message in messages:
        if message.grouped_id and groups and groups[-1][0].grouped_id == message.grouped_id:
            groups[-1].append(message)
        else:
            groups.append([message])
    now = datetime.now(timezone.utc)
    for index, group in enumerate(groups):
        if (now - group[-1].date).total_seconds() < 10:
            break
        # A full batch may end halfway through an album. Leave its last group for the next scan.
        if len(messages) == 201 and index == len(groups) - 1:
            break
        complete = await complete_album(user, source, group)
        if (now - complete[-1].date).total_seconds() < 10:
            break
        try:
            enqueue_new_messages(store, source.id, target.id, boundary, complete)
        except OverlappingSyncJob as error:
            store.record_overlap(source.id, str(target.id), complete, error)
            logger.warning("Recorded overlapping album for manual review source=%s ids=%s: %s",
                           source.id, [message.id for message in complete], error)
        store.advance(source.id, str(target.id), complete[-1].id)
    return len(messages)


async def reconciliation_worker(user, source, target, store, boundary):
    while True:
        record_worker_heartbeat(store.connection, "channel_reconciliation")
        record_worker_heartbeat(store.connection, f"news_source_{source.id}")
        delay = 30
        try:
            count = await reconcile_messages(user, source, target, store, boundary)
            if count == 201:
                delay = 1
        except errors.FloodWaitError as error:
            delay = max(delay, error.seconds + 1)
            logger.warning("Source reconciliation rate limited; waiting %s seconds", delay)
        except Exception as error:
            record_worker_failure(store.connection, "channel_reconciliation", error)
            record_worker_failure(store.connection, f"news_source_{source.id}", error)
            logger.exception("Source reconciliation failed; cursor retained, retrying in 30 seconds")
        await asyncio.sleep(delay)


@contextmanager
def single_instance(directory, name="listener.lock"):
    # OS lock is released on crash; never delete another process's session files.
    import msvcrt

    with (directory / name).open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Another channel-sync command is running.") from error
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


async def deliver(user, bot, source, target, store, job, directory):
    current_source = await user.get_entity(source)
    if getattr(current_source, "noforwards", False):
        store.update(job, "skipped", error="Source channel has protected content.")
        logger.warning("Skipped protected-channel job %s", job["job_key"])
        return
    messages = await user.get_messages(source, ids=json.loads(job["message_ids"]))
    if not messages or any(message is None for message in messages):
        raise ValueError("Source message was deleted or is unavailable.")
    complete = await complete_album(user, source, messages)
    if getattr(messages[0], "grouped_id", None) and (
        {message.id for message in complete} != set(json.loads(job["message_ids"]))
    ):
        raise ValueError("Incomplete album job; caption may be missing. Inspect before sending.")
    messages = complete
    if any(getattr(message, "noforwards", False) for message in messages):
        store.update(job, "skipped", error="Source message has protected content.")
        logger.warning("Skipped protected job %s", job["job_key"])
        return
    if any(is_news_advertisement(message.message) for message in messages):
        store.update(job, "skipped", error="Advertisement label or promotional template; entire job excluded.")
        logger.info("Skipped advertisement job %s", job["job_key"])
        return
    captions = [clean_news_text(message.message, message.entities) for message in messages]
    if (any(exceeds_news_limits(text) for text in captions)
            or sum(cleaned_news_emoji_count(message.message, text, message.entities)
                   for message, text in zip(messages, captions)) > 5):
        store.update(job, "skipped", error="Cleaned news contains SOSO or more than 5 emojis; entire job excluded.")
        logger.info("Skipped cleaned news limits job %s", job["job_key"])
        return
    media_messages = [message for message in messages if message.media
                      and not isinstance(message.media, types.MessageMediaWebPage)]
    if media_messages and any(
        not isinstance(message.media, (types.MessageMediaPhoto, types.MessageMediaDocument))
        for message in media_messages
    ):
        if not any(captions) and all(
            not isinstance(message.media, (types.MessageMediaPhoto, types.MessageMediaDocument))
            for message in media_messages
        ):
            media_types = ",".join(sorted({type(message.media).__name__ for message in media_messages}))
            store.update(job, "skipped", error=f"Unsupported media without news text; not reposted: {media_types}")
            logger.info("Skipped unsupported textless news source=%s job=%s media=%s",
                        job["source"], job["job_key"], media_types)
            return
        raise ValueError("Unsupported media type; not reposted.")
    if media_messages and len(media_messages) != len(messages):
        raise ValueError("Mixed text and media job cannot be published as one album.")
    if not media_messages and not any(captions):
        store.update(job, "skipped", error="No text remains after contact cleanup.")
        logger.info("Skipped empty job %s", job["job_key"])
        return
    body = "\n\n".join(text for text in captions if text)
    duplicate = store.news_deduper.find(job, body) if (
        not media_messages or len(normalized_body(body)) >= MIN_SIMILAR_LENGTH) else None
    if duplicate:
        store.update(job, "skipped", error=(
            f"Duplicate news within 15 hours: {duplicate['source']}:{duplicate['job_key']}"))
        logger.info("Skipped duplicate news source=%s job=%s", job["source"], job["job_key"])
        return
    separate_album_text = len(media_messages) > 1
    long_caption = any(len(text.encode("utf-16-le")) // 2 > 1024 for text in captions)
    # Bot accounts have a smaller caption limit than premium source-channel authors.
    text_parts = split_telegram_text("\n\n".join(text for text in captions if text)) if (
        long_caption or separate_album_text or not media_messages
    ) else []
    media_captions = [""] * len(captions) if long_caption or separate_album_text else captions
    with tempfile.TemporaryDirectory(prefix="media-", dir=directory) as temporary:
        paths = []
        for message in media_messages:
            path = await user.download_media(message, file=str(Path(temporary)) + os.sep)
            if not path:
                raise ValueError("Media download did not produce a file.")
            paths.append(path)
        digest = await asyncio.to_thread(media_digest, paths) if paths else None
        duplicate = store.news_deduper.find(job, body, digest)
        if duplicate:
            store.update(job, "skipped", error=(
                f"Duplicate news within 15 hours: {duplicate['source']}:{duplicate['job_key']}"))
            logger.info("Skipped duplicate news source=%s job=%s", job["source"], job["job_key"])
            return
        store.news_deduper.begin_send(job, body, digest)
        result_ids = []
        if paths:
            if len(paths) == 1:
                message = media_messages[0]
                attributes = message.document.attributes if message.document else None
                sent = await bot.send_file(
                    target, paths[0], caption=media_captions[0], parse_mode=None,
                    attributes=attributes, force_document=bool(
                        message.document and not (message.video or message.audio or message.gif)
                    ), supports_streaming=bool(message.video),
                )
            else:
                sent = await bot.send_file(target, paths, caption=media_captions, parse_mode=None)
            sent = sent if isinstance(sent, list) else [sent]
            result_ids.extend(message.id for message in sent)
            store.update(job, "sending", result_ids=result_ids)
        for text in text_parts:
            options = {"reply_to": result_ids[0]} if paths and not separate_album_text else {}
            if separate_album_text:
                from telethon import Button
                from post_navigation import NEWS_BUTTONS
                options["buttons"] = [[Button.url(label, url) for label, url in NEWS_BUTTONS]]
            sent = await bot.send_message(target, text, parse_mode=None, link_preview=False, **options)
            result_ids.append(sent.id)
            store.update(job, "sending", result_ids=result_ids)
        store.update(job, "sent", result_ids=result_ids)
        logger.info("Published source message(s) %s", job["job_key"])


async def worker(user, bot, source, target, store, directory):
    sources = {item.id: item for item in source} if isinstance(source, (list, tuple)) else {source.id: source}
    while True:
        record_worker_heartbeat(store.connection, "channel_forwarder")
        job = store.next_news(tuple(sources), str(target.id))
        if not job:
            await asyncio.sleep(1)
            continue
        try:
            await deliver(user, bot, sources[job["source"]], target, store, job, directory)
        except errors.FloodWaitError as error:
            row = store.connection.execute(
                "SELECT result_ids FROM jobs WHERE source=? AND target=? AND job_key=?",
                (job["source"], job["target"], job["job_key"]),
            ).fetchone()
            partial = json.loads(row["result_ids"] or "[]")
            store.update(job, "uncertain" if partial else "queued", result_ids=partial or None,
                         error="Telegram rate limit after partial send; inspect before retrying."
                         if partial else "Telegram rate limit; waiting.")
            logger.warning("Rate limited; waiting %s seconds for job %s", error.seconds, job["job_key"])
            await asyncio.sleep(error.seconds + 1)
        except Exception as error:
            record_worker_failure(store.connection, "channel_forwarder", error)
            row = store.connection.execute(
                "SELECT status,result_ids FROM jobs WHERE source=? AND target=? AND job_key=?",
                (job["source"], job["target"], job["job_key"]),
            ).fetchone()
            status = "uncertain" if row["status"] == "sending" else "failed"
            store.update(job, status, result_ids=json.loads(row["result_ids"] or "[]") or None,
                         error=type(error).__name__)
            logger.exception("Job %s is %s; inspect before retrying", job["job_key"], status)


async def index_announcements(bot, target, store):
    from announcements import indexed_keys
    known = await asyncio.to_thread(indexed_keys)
    jobs = store.connection.execute(
        "SELECT * FROM jobs WHERE target=? AND status='sent' ORDER BY rowid DESC",
        (str(target.id),),
    ).fetchall()
    failed = 0
    for job in jobs:
        key = announcement_job_key(job)
        if key in known:
            continue
        try:
            await index_announcement(bot, target, job, key)
        except errors.FloodWaitError:
            raise
        except Exception:
            failed += 1
            logger.exception("Failed to index published job %s; forwarding status unchanged", job["job_key"])
    if failed:
        raise ValueError(f"{failed} announcement(s) could not be indexed; retry required.")


async def index_announcement(bot, target, job, key):
    from announcements import local_media_path, save, retention_cutoff
    ids = json.loads(job["result_ids"])
    messages = await bot.get_messages(target, ids=ids)
    if not ids or len(messages) != len(ids) or any(message is None for message in messages):
        raise ValueError(f"Published messages unavailable for announcement job {job['job_key']}")
    published_at = messages[0].date
    if published_at.astimezone(timezone.utc).replace(tzinfo=None) < retention_cutoff():
        await asyncio.to_thread(save, key, ids, "", [], published_at, enabled=False)
        logger.info("Recorded expired announcement job %s without downloading media", job["job_key"])
        return
    media = []
    for message in messages:
        if message.photo or message.video or message.gif:
            kind = "photo" if message.photo else "video"
            extension = "jpg" if message.photo else (
                "webm" if message.file.mime_type == "video/webm" else "mp4"
            )
            path = local_media_path(f"{target.id}-{message.id}.{extension}")
            size = message.file.size
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.stat().st_size != size:
                if size > 200 * 1024 * 1024:
                    raise ValueError("Announcement media storage limit exceeded (200 MB/file).")
                if shutil.disk_usage(path.parent).free < size + 1024 ** 3:
                    raise OSError("Announcement media storage requires 1 GB free disk reserve.")
                temporary = path.with_suffix(".part")
                try:
                    await bot.download_media(message, file=str(temporary))
                    if temporary.stat().st_size != size:
                        raise ValueError("Announcement media download incomplete.")
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
            media.append({"kind": kind, "filename": path.name})
    body = "\n\n".join(message.message for message in messages if message.message)
    if not body and not media:
        raise ValueError("Published announcement has no supported content.")
    await asyncio.to_thread(save, key, ids, body, media, messages[0].date)
    logger.info("Indexed announcement from published job %s", job["job_key"])


async def announcement_worker(bot, target, store):
    from announcements import ensure_tables, expire_news
    ready = False
    while True:
        record_worker_heartbeat(store.connection, "announcement_indexer")
        delay = 30
        try:
            if not ready:
                await asyncio.to_thread(ensure_tables)
                ready = True
            await asyncio.to_thread(expire_news)
            await index_announcements(bot, target, store)
        except errors.FloodWaitError as error:
            delay = max(delay, error.seconds + 1)
            logger.warning("Announcement rate limited; waiting %s seconds", delay)
        except Exception as error:
            record_worker_failure(store.connection, "announcement_indexer", error)
            logger.exception("Announcement indexing failed; retrying in 30 seconds")
        await asyncio.sleep(delay)


async def run(mode, directory, news_only=False):
    api_id, api_hash = credentials()
    user = TelegramClient(str(directory / "user"), api_id, api_hash)
    if mode == "login":
        try:
            await user.start()
            if (await user.get_me()).bot:
                raise ValueError("Log in with the subscribed user account, not a bot.")
            logger.info("User login complete. Listener has NOT been started.")
        finally:
            await user.disconnect()
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is missing from bot .env.")
    bot = TelegramClient(str(directory / "publisher"), api_id, api_hash)
    store = JobStore(directory / "jobs.sqlite3")
    try:
        await user.connect()
        if not await user.is_user_authorized():
            raise ValueError("Run channel_sync.py login in your own terminal first.")
        if (await user.get_me()).bot:
            raise ValueError("Listener session must belong to a user account.")
        await bot.start(bot_token=token)
        publisher = await bot.get_me()
        if str(publisher.id) != token.partition(":")[0]:
            raise ValueError("Publisher session does not match the configured bot.")
        target = await bot.get_entity("https://t.me/haotiancm99988866")
        if not isinstance(target, types.Channel) or not target.broadcast:
            raise ValueError("Target must be a broadcast channel.")
        permissions = await bot.get_permissions(target, publisher)
        if not permissions.is_admin or not permissions.post_messages:
            raise ValueError("Bot requires target-channel post_messages permission.")
        sources, boundaries = await prepare_news_sources(user, target, store)
        store.recover()
        from announcements import ensure_tables
        await asyncio.to_thread(ensure_tables)
        await seed_recent_news(bot, target, store, directory)

        async def receive(source, messages):
            # Allow all album members and their captions to reach the source before queuing.
            if messages and messages[0].grouped_id:
                await asyncio.sleep(10)
            complete = await complete_album(user, source, messages)
            enqueue_new_messages(store, source.id, target.id, boundaries[source.id], complete)

        async def on_message(event):
            if not event.message.grouped_id:
                await receive(source_by_chat[event.chat_id], [event.message])

        async def on_album(event):
            await receive(source_by_chat[event.chat_id], event.messages)

        source_by_chat = {utils.get_peer_id(source): source for source in sources}
        user.add_event_handler(on_message, events.NewMessage(chats=sources))
        user.add_event_handler(on_album, events.Album(chats=sources))
        async def monitor_listener():
            while True:
                record_worker_heartbeat(store.connection, "channel_listener")
                await asyncio.sleep(30)

        logger.info("READY: news sources=%s -> @haotiancm99988866; news_only=%s",
                    ",".join(name for name, _ in NEWS_SOURCES), news_only)
        async with asyncio.TaskGroup() as tasks:
            if not news_only:
                from music_ingest import music_worker
                from movie_ingest import movie_worker
                from music_playback import playback_worker
                tasks.create_task(music_worker(user, bot, directory))
                tasks.create_task(movie_worker(user, bot, directory))
                tasks.create_task(playback_worker())
            tasks.create_task(worker(user, bot, sources, target, store, directory))
            tasks.create_task(announcement_worker(bot, target, store))
            for source in sources:
                tasks.create_task(reconciliation_worker(
                    user, source, target, store, boundaries[source.id]))
            tasks.create_task(monitor_listener())
            await user.run_until_disconnected()
            raise ConnectionError("Listener disconnected; stop and inspect before restarting.")
    finally:
        try:
            await bot.disconnect()
        finally:
            try:
                await user.disconnect()
            finally:
                store.close()


def is_connection_failure(error):
    if isinstance(error, ExceptionGroup):
        return all(is_connection_failure(item) for item in error.exceptions)
    return isinstance(error, (ConnectionError, TimeoutError))


async def run_with_recovery(directory, news_only=False):
    while True:
        delay = 30
        try:
            await run("run", directory, news_only=news_only)
        except errors.FloodWaitError as error:
            delay = max(delay, error.seconds + 1)
            logger.warning("Listener startup rate limited; retrying in %s seconds", delay)
        except Exception as error:
            if not is_connection_failure(error):
                raise
            logger.exception("Listener connection lost; sessions closed, cursors retained;"
                             " reconnecting in %s seconds", delay)
        else:
            raise RuntimeError("Listener stopped unexpectedly without a connection failure.")
        await asyncio.sleep(delay)


async def resolve_channel(client, expected_id, username):
    peer = await client.get_input_entity(types.PeerChannel(expected_id))
    channel = await client.get_entity(peer)
    usernames = {value.casefold() for value in [getattr(channel, "username", None)] if value}
    usernames.update(alias.username.casefold() for alias in getattr(channel, "usernames", None) or []
                     if alias.active)
    if (not isinstance(channel, types.Channel) or not channel.broadcast or channel.id != expected_id
            or username.casefold() not in usernames):
        raise ValueError(f"Channel @{username} identity or channel type changed.")
    return channel


async def prepare_news_sources(user, target, store):
    sources = []
    latest_ids = {}
    for username, expected_id in NEWS_SOURCES:
        source = await resolve_channel(user, expected_id, username)
        if getattr(source, "noforwards", False):
            raise ValueError(f"News source @{username} has protected content; not permitted.")
        latest = await user.get_messages(source, limit=1)
        latest_ids[source.id] = latest[0].id if latest else 0
        sources.append(source)
    boundaries = {}
    for (username, _), source in zip(NEWS_SOURCES, sources):
        progress = store.progress(source.id, str(target.id), latest_ids[source.id])
        boundaries[source.id] = progress["boundary"]
        logger.info("News source @%s ready; baseline=%s", username, progress["boundary"])
    return sources, boundaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("login", "run", "status"))
    parser.add_argument("--news-only", action="store_true",
                        help="Run news workers without music/movie ingestion or playback preparation")
    args = parser.parse_args()
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(directory / "sync.log", encoding="utf-8")],
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    with single_instance(directory) if args.mode != "status" else nullcontext():
        if args.mode == "status":
            store = JobStore(directory / "jobs.sqlite3")
            try:
                for row in store.connection.execute(
                    "SELECT status, COUNT(*) AS total FROM jobs GROUP BY status"
                ):
                    print(f"{row['status']}: {row['total']}")
                for row in store.connection.execute(
                    "SELECT job_key,status,error FROM jobs WHERE status IN ('failed','uncertain','sending')"
                    " ORDER BY rowid DESC LIMIT 20"
                ):
                    print(f"{row['job_key']}: {row['status']} ({row['error']})")
            finally:
                store.close()
        else:
            asyncio.run(run("login", directory) if args.mode == "login"
                        else run_with_recovery(directory, news_only=args.news_only))


if __name__ == "__main__":
    main()
