"""Resumable read-only collection of approved housing and rental channels."""
import argparse
import asyncio
from datetime import timedelta
import json
import logging
import sqlite3
import time

import pymysql
from telethon import TelegramClient, errors, types
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession

from channel_sync import JobStore, complete_album, credentials, download_post_media, single_instance, state_directory
from operations_health import record_worker_failure, record_worker_heartbeat
import service_posts


logger = logging.getLogger(__name__)
INTERVAL = 5
SCAN_LIMIT = 50


class ServiceStore(JobStore):
    def __init__(self, path):
        super().__init__(path)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS service_progress (
                source INTEGER PRIMARY KEY, boundary INTEGER NOT NULL,
                history INTEGER NOT NULL DEFAULT 0, live INTEGER NOT NULL,
                complete INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS service_settings (key TEXT PRIMARY KEY,value REAL NOT NULL);
        """)

    def initialize(self, source, latest):
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO service_progress(source,boundary,live) VALUES(?,?,?)",
                (source, latest, latest))

    def cursor(self, source):
        return self.connection.execute("SELECT * FROM service_progress WHERE source=?", (source,)).fetchone()

    def deadline(self):
        row = self.connection.execute(
            "SELECT value FROM service_settings WHERE key='not_before'").fetchone()
        return row[0] if row else 0

    def postpone(self, seconds):
        with self.connection:
            self.connection.execute(
                "INSERT INTO service_settings VALUES('not_before',?)"
                " ON CONFLICT(key) DO UPDATE SET value=MAX(value,excluded.value)",
                (time.time() + seconds,))

    async def request(self, operation):
        while self.deadline() > time.time():
            await asyncio.sleep(min(30, self.deadline() - time.time()))
        try:
            return await operation()
        except errors.FloodWaitError as error:
            self.postpone(error.seconds + 1)
            raise
        finally:
            self.postpone(INTERVAL)

    def pending(self, source, service):
        return self.connection.execute(
            "SELECT * FROM jobs WHERE source=? AND target=? AND status='queued'"
            " ORDER BY is_backfill,rowid LIMIT 1", (source, service)).fetchone()


async def scan(reader, channel, service, store, *, historical):
    state = store.cursor(channel.id)
    if historical and state["complete"]:
        return
    options = {"limit": SCAN_LIMIT, "reverse": True,
               "min_id": state["history"] if historical else state["live"]}
    if historical:
        options["max_id"] = state["boundary"] + 1
        if not state["history"]:
            options["offset_date"] = service_posts.START_AT - timedelta(seconds=1)

    async def fetch():
        return [message async for message in reader.iter_messages(channel, **options)]

    messages = await store.request(fetch)
    groups = []
    for message in messages:
        if message.grouped_id and groups and groups[-1][0].grouped_id == message.grouped_id:
            groups[-1].append(message)
        else:
            groups.append([message])
    column = "history" if historical else "live"
    for index, group in enumerate(groups):
        if len(messages) == SCAN_LIMIT and index == len(groups) - 1:
            break
        if time.time() - group[-1].date.timestamp() < 10:
            return
        complete = await store.request(lambda: complete_album(reader, channel, group)) if group[0].grouped_id else group
        if time.time() - complete[-1].date.timestamp() < 10:
            return
        if all(message.date >= service_posts.START_AT for message in complete):
            store.enqueue(channel.id, service, [message.id for message in complete],
                          min(message.date for message in complete).timestamp(), is_backfill=historical)
        with store.connection:
            store.connection.execute(
                f"UPDATE service_progress SET {column}=MAX({column},?) WHERE source=?",
                (complete[-1].id, channel.id))
    if historical and len(messages) < SCAN_LIMIT:
        with store.connection:
            store.connection.execute("UPDATE service_progress SET complete=1 WHERE source=?", (channel.id,))


async def collect(reader, channel, service, store, job):
    current = await store.request(lambda: reader.get_entity(channel))
    if current.id != channel.id or getattr(current, "noforwards", False):
        raise ValueError("Service source identity or content permission changed")
    ids = json.loads(job["message_ids"])
    messages = await store.request(lambda: reader.get_messages(channel, ids=ids))
    if not messages or len(messages) != len(ids) or any(message is None for message in messages):
        raise ValueError("Service source message is unavailable; not indexing a partial album")
    if getattr(channel, "noforwards", False) or any(getattr(message, "noforwards", False) for message in messages):
        raise ValueError("Protected service content cannot be collected")
    if any(message.date < service_posts.START_AT for message in messages):
        raise ValueError("Service source message precedes authorized start")
    complete = await store.request(lambda: complete_album(reader, channel, messages)) if messages[0].grouped_id else messages
    if [message.id for message in complete] != ids:
        raise ValueError("Service album membership changed; manual inspection required")
    media = await download_post_media(reader, messages, channel.id, service_posts.local_media_path,
                                     request=store.request)
    body = "\n\n".join(service_posts.clean_body(message.message, getattr(message, "entities", ()))
                      for message in messages if message.message).strip()
    if not media and not body:
        store.update(job, "skipped", error="No supported pictures, videos or text")
        logger.warning("Skipped unsupported service message category=%s ids=%s", service, ids)
        return
    await asyncio.to_thread(service_posts.save, service, f"{channel.id}:{job['job_key']}",
                            ids, body, media, min(message.date for message in messages))
    store.update(job, "sent")
    logger.info("Indexed service=%s source=%s ids=%s media=%s", service, channel.id, ids, len(media))


async def service_worker(reader, directory, *, once=False):
    with single_instance(directory, "service-ingest.lock"):
        store = ServiceStore(directory / "service-ingest.sqlite3")
        contexts = {}
        ready = False

        async def heartbeat():
            while True:
                for service in service_posts.SOURCES:
                    record_worker_heartbeat(store.connection, f"service_{service}")
                await asyncio.sleep(30)

        health_task = asyncio.create_task(heartbeat(), name="service-feed-health")
        try:
            while True:
                for service, (_, username, expected_id) in service_posts.SOURCES.items():
                    try:
                        if not ready:
                            await asyncio.to_thread(service_posts.ensure_tables)
                            ready = True
                        if service not in contexts:
                            channel = await store.request(lambda: reader.get_entity(f"https://t.me/{username}"))
                            if (not isinstance(channel, types.Channel) or not channel.broadcast
                                    or channel.id != expected_id or getattr(channel, "noforwards", False)):
                                raise ValueError(f"Service @{username} identity or content permission changed")
                            latest = await store.request(lambda: reader.get_messages(channel, limit=1))
                            store.initialize(channel.id, latest[0].id if latest else 0)
                            contexts[service] = channel
                        channel = contexts[service]
                        await scan(reader, channel, service, store, historical=False)
                        queued = store.connection.execute(
                            "SELECT COUNT(*) FROM jobs WHERE source=? AND target=? AND status='queued'",
                            (channel.id, service)).fetchone()[0]
                        if queued < 100:
                            await scan(reader, channel, service, store, historical=True)
                        job = store.pending(channel.id, service)
                        if job:
                            try:
                                await collect(reader, channel, service, store, job)
                            except ValueError as error:
                                store.update(job, "failed", error=str(error))
                                raise
                    except errors.FloodWaitError as error:
                        record_worker_failure(store.connection, f"service_{service}", error)
                        logger.warning("Service flood wait %s seconds; deadline persisted", error.seconds)
                        if once:
                            raise
                    except (errors.RPCError, OSError, ValueError, pymysql.MySQLError) as error:
                        record_worker_failure(store.connection, f"service_{service}", error)
                        logger.exception("Service collection failed category=%s; progress retained", service)
                        if once:
                            raise
                        store.postpone(30)
                if once:
                    return
                await asyncio.sleep(INTERVAL)
        finally:
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass
            store.close()


async def run(directory, *, once=False):
    connection = sqlite3.connect((directory / "user.session").as_uri() + "?mode=ro", uri=True)
    try:
        saved = connection.execute("SELECT dc_id,server_address,port,auth_key FROM sessions").fetchone()
    finally:
        connection.close()
    if not saved:
        raise ValueError("Authorized channel reader session is missing")
    session = MemorySession()
    session.set_dc(*saved[:3])
    session.auth_key = AuthKey(saved[3])
    reader = TelegramClient(session, *credentials(), receive_updates=False, flood_sleep_threshold=0)
    try:
        await reader.connect()
        if not await reader.is_user_authorized() or (await reader.get_me()).bot:
            raise ValueError("Service collection requires the existing authorized user reader")
        if once:
            await service_worker(reader, directory, once=True)
        else:
            from movie_ingest import monitor_connection
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(monitor_connection(reader, "service reader"))
                tasks.create_task(service_worker(reader, directory))
    finally:
        await reader.disconnect()


async def run_with_recovery(directory):
    from channel_sync import is_connection_failure
    while True:
        try:
            await run(directory)
        except (ConnectionError, TimeoutError, ExceptionGroup) as error:
            if not is_connection_failure(error):
                raise
            logger.exception("Service reader disconnected; reconnecting with progress retained")
            await asyncio.sleep(30)
        else:
            raise RuntimeError("Service collector stopped unexpectedly")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "once", "status"))
    args = parser.parse_args()
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(directory / "service-ingest.log", encoding="utf-8")])
    logging.getLogger("telethon").setLevel(logging.WARNING)
    if args.mode == "status":
        store = ServiceStore(directory / "service-ingest.sqlite3")
        try:
            for row in store.connection.execute("SELECT target,status,COUNT(*) AS total FROM jobs GROUP BY target,status"):
                print(dict(row))
        finally:
            store.close()
    else:
        asyncio.run(run(directory, once=True) if args.mode == "once" else run_with_recovery(directory))


if __name__ == "__main__":
    main()
