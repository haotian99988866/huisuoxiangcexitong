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
import tempfile

from dotenv import load_dotenv
from telethon import TelegramClient, events, errors, types

from channel_sync_clean import clean_text


logger = logging.getLogger("channel_sync")
CONFIG_PATH = Path(__file__).with_name(".channel-sync.env")


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
        self.connection.commit()

    def enqueue(self, source, target, ids):
        ids = sorted(set(ids))
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO jobs (source,target,job_key,message_ids,created)"
                " VALUES (?,?,?,?,?)",
                (source, target, ",".join(map(str, ids)), json.dumps(ids),
                 datetime.now(timezone.utc).isoformat()),
            )

    def next(self, source, target):
        return self.connection.execute(
            "SELECT * FROM jobs WHERE source=? AND target=? AND status='queued'"
            " ORDER BY rowid LIMIT 1", (source, target),
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
    store.enqueue(source_id, str(target_id), [message.id for message in messages])
    logger.info("Queued source message(s) %s", [message.id for message in messages])


@contextmanager
def single_instance(directory):
    # OS lock is released on crash; never delete another process's session files.
    import msvcrt

    with (directory / "listener.lock").open("a+b") as lock:
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
    if any(getattr(message, "noforwards", False) for message in messages):
        store.update(job, "skipped", error="Source message has protected content.")
        logger.warning("Skipped protected job %s", job["job_key"])
        return
    captions = [clean_text(message.message, message.entities) for message in messages]
    media_messages = [message for message in messages if message.media
                      and not isinstance(message.media, types.MessageMediaWebPage)]
    if media_messages and any(
        not isinstance(message.media, (types.MessageMediaPhoto, types.MessageMediaDocument))
        for message in media_messages
    ):
        raise ValueError("Unsupported media type; not reposted.")
    if media_messages and len(media_messages) != len(messages):
        raise ValueError("Mixed text and media job cannot be published as one album.")
    if not media_messages and not any(captions):
        store.update(job, "skipped", error="No text remains after contact cleanup.")
        logger.info("Skipped empty job %s", job["job_key"])
        return
    with tempfile.TemporaryDirectory(prefix="media-", dir=directory) as temporary:
        paths = []
        for message in media_messages:
            path = await user.download_media(message, file=str(Path(temporary)) + os.sep)
            if not path:
                raise ValueError("Media download did not produce a file.")
            paths.append(path)
        store.update(job, "sending")
        if paths:
            if len(paths) == 1:
                message = media_messages[0]
                attributes = message.document.attributes if message.document else None
                sent = await bot.send_file(
                    target, paths[0], caption=captions[0], parse_mode=None,
                    attributes=attributes, force_document=bool(
                        message.document and not (message.video or message.audio or message.gif)
                    ), supports_streaming=bool(message.video),
                )
            else:
                sent = await bot.send_file(target, paths, caption=captions, parse_mode=None)
        else:
            sent = await bot.send_message(target, captions[0], parse_mode=None, link_preview=False)
        sent = sent if isinstance(sent, list) else [sent]
        store.update(job, "sent", result_ids=[message.id for message in sent])
        logger.info("Published source message(s) %s", job["job_key"])


async def worker(user, bot, source, target, store, directory):
    while True:
        job = store.next(source.id, str(target.id))
        if not job:
            await asyncio.sleep(1)
            continue
        try:
            await deliver(user, bot, source, target, store, job, directory)
        except errors.FloodWaitError as error:
            store.update(job, "queued", error="Telegram rate limit; waiting.")
            logger.warning("Rate limited; waiting %s seconds for job %s", error.seconds, job["job_key"])
            await asyncio.sleep(error.seconds + 1)
        except Exception as error:
            row = store.connection.execute(
                "SELECT status FROM jobs WHERE source=? AND target=? AND job_key=?",
                (job["source"], job["target"], job["job_key"]),
            ).fetchone()
            status = "uncertain" if row["status"] == "sending" else "failed"
            store.update(job, status, error=type(error).__name__)
            logger.exception("Job %s is %s; inspect before retrying", job["job_key"], status)


async def index_announcements(bot, target, store):
    from announcements import indexed_keys
    known = await asyncio.to_thread(indexed_keys)
    jobs = store.connection.execute(
        "SELECT * FROM jobs WHERE target=? AND status='sent' ORDER BY rowid",
        (str(target.id),),
    ).fetchall()
    failed = 0
    for job in jobs:
        key = f"{target.id}:{job['job_key']}"
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
    from announcements import local_media_path, save
    ids = json.loads(job["result_ids"])
    messages = await bot.get_messages(target, ids=ids)
    if not ids or len(messages) != len(ids) or any(message is None for message in messages):
        raise ValueError(f"Published messages unavailable for announcement job {job['job_key']}")
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
                total = sum(item.stat().st_size for item in path.parent.iterdir() if item.is_file())
                if size > 200 * 1024 * 1024 or total + size > 2 * 1024 ** 3:
                    raise ValueError("Announcement media storage limit exceeded (200 MB/file, 2 GB total).")
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
    from announcements import ensure_tables
    ready = False
    while True:
        delay = 30
        try:
            if not ready:
                await asyncio.to_thread(ensure_tables)
                ready = True
            await index_announcements(bot, target, store)
        except errors.FloodWaitError as error:
            delay = max(delay, error.seconds + 1)
            logger.warning("Announcement rate limited; waiting %s seconds", delay)
        except Exception:
            logger.exception("Announcement indexing failed; retrying in 30 seconds")
        await asyncio.sleep(delay)


async def run(mode, directory):
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
        source = await user.get_entity("https://t.me/svip")
        target = await bot.get_entity("https://t.me/haotiancm99988866")
        if not isinstance(source, types.Channel) or not source.broadcast:
            raise ValueError("Source must be a broadcast channel.")
        if not isinstance(target, types.Channel) or not target.broadcast:
            raise ValueError("Target must be a broadcast channel.")
        if getattr(source, "noforwards", False):
            raise ValueError("Source has protected content; synchronization is not permitted.")
        permissions = await bot.get_permissions(target, publisher)
        if not permissions.is_admin or not permissions.post_messages:
            raise ValueError("Bot requires target-channel post_messages permission.")
        latest = await user.get_messages(source, limit=1)
        boundary = latest[0].id if latest else 0
        store.recover()

        async def receive(messages):
            enqueue_new_messages(store, source.id, target.id, boundary, messages)

        async def on_message(event):
            if not event.message.grouped_id:
                await receive([event.message])

        async def on_album(event):
            await receive(event.messages)

        user.add_event_handler(on_message, events.NewMessage(chats=source))
        user.add_event_handler(on_album, events.Album(chats=source))
        logger.info("READY: @svip -> @haotiancm99988866; only messages newer than %s", boundary)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(worker(user, bot, source, target, store, directory))
            tasks.create_task(announcement_worker(bot, target, store))
            await user.run_until_disconnected()
            raise ConnectionError("Listener disconnected; stop and inspect before restarting.")
    finally:
        await bot.disconnect()
        await user.disconnect()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("login", "run", "status"))
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
            asyncio.run(run(args.mode, directory))


if __name__ == "__main__":
    main()
