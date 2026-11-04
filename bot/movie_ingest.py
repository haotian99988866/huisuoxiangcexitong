"""Opt-in, resumable copying from authorized movie sources."""
import argparse
import asyncio
from copy import copy
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timezone
import logging
import os
import re
import sqlite3
import time

from telethon import Button, errors, types

from channel_sync_clean import clean_text, is_advertisement
from movies import (MOVIE_CHANNEL_USERNAME, DRAMA_CHANNEL_USERNAME, DRAMA_CHANNEL_ID,
                    ensure_tables, parse_caption, save_movie)
from operations_health import ensure_health_table, record_worker_failure, record_worker_heartbeat
from welcome import BOT_URL, CONTACT_URL

logger = logging.getLogger(__name__)
SOURCE_ID = 1345733873
SOURCE_USERNAME = "luolimeimei"
TARGET_ID = 3920194464
HISTORY_START_ID = 2377
RECENT_RECHECK_LIMIT = 50
RECENT_RECHECK_INTERVAL = 300


@dataclass(frozen=True)
class MovieSource:
    id: int
    username: str
    start: int = 1
    movie_tag: bool = False
    target_id: int = TARGET_ID
    target_username: str = MOVIE_CHANNEL_USERNAME
    short_drama: bool = False


DRAMA_SOURCE = MovieSource(3068433963, "xmdm888", target_id=int(DRAMA_CHANNEL_ID.removeprefix("-100")),
                          target_username=DRAMA_CHANNEL_USERNAME, short_drama=True)


SOURCES = (
    MovieSource(SOURCE_ID, SOURCE_USERNAME, HISTORY_START_ID),
    MovieSource(1305510420, "yfqh06", movie_tag=True),
    MovieSource(2118669215, "meizhaoxiezhen"),
    DRAMA_SOURCE,
)


def store_path(directory, source):
    return directory / ("movie-import.sqlite3" if source.id == SOURCE_ID
                        else f"movie-import-{source.id}.sqlite3")


class MovieStore:
    def __init__(self, path, recover=False, source=SOURCES[0]):
        self.source = source
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS progress (
                id INTEGER PRIMARY KEY CHECK(id=1), boundary INTEGER NOT NULL,
                history INTEGER NOT NULL DEFAULT 0, live INTEGER NOT NULL,
                complete INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS jobs (
                message INTEGER PRIMARY KEY, historical INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', result_id INTEGER,
                document_id INTEGER, error TEXT
            );
            CREATE TABLE IF NOT EXISTS owned (
                document_id INTEGER PRIMARY KEY, message INTEGER NOT NULL
            );
        """)
        ensure_health_table(self.connection)
        if recover:
            with self.connection:
                self.connection.execute(
                    "UPDATE jobs SET status='uncertain',error='Interrupted during forward; inspect target'"
                    " WHERE status='sending'")

    def setting(self, key, default="0"):
        row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key, value):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))

    def progress(self, latest=None):
        if latest is not None:
            with self.connection:
                self.connection.execute(
                    "INSERT OR IGNORE INTO progress(id,boundary,live) VALUES (1,?,?)", (latest, latest))
        return self.connection.execute("SELECT * FROM progress WHERE id=1").fetchone()

    def enqueue(self, message, historical):
        if message < int(self.setting("start_message", str(self.source.start))):
            return
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO jobs(message,historical) VALUES (?,?)",
                                    (message, int(historical)))

    def reconcile(self, message):
        if message < int(self.setting("start_message", str(self.source.start))):
            return False
        with self.connection:
            added = self.connection.execute(
                "INSERT OR IGNORE INTO jobs(message,historical) VALUES (?,0)", (message,)).rowcount
            retried = self.connection.execute(
                "UPDATE jobs SET status='queued',error=NULL,historical=0 WHERE message=? "
                "AND status='skipped' AND result_id IS NULL AND document_id IS NULL", (message,)).rowcount
        return bool(added or retried)

    def advance(self, message, historical):
        column = "history" if historical else "live"
        with self.connection:
            self.connection.execute(f"UPDATE progress SET {column}=MAX({column},?) WHERE id=1", (message,))

    def update(self, job, status, error=None, result_id=None, document_id=None):
        with self.connection:
            self.connection.execute(
                "UPDATE jobs SET status=?,error=?,result_id=COALESCE(?,result_id),"
                "document_id=COALESCE(?,document_id) WHERE message=?",
                (status, error, result_id, document_id, job["message"]))

    def pending(self):
        return self.connection.execute(
            "SELECT * FROM jobs WHERE status IN ('queued','forwarded')"
            " ORDER BY CASE status WHEN 'forwarded' THEN 0 ELSE 1 END,historical,message LIMIT 1").fetchone()

    def close(self):
        self.connection.close()

    def set_start(self, message):
        if message < 1:
            raise ValueError("Movie start message must be positive")
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO settings VALUES ('start_message',?)", (str(message),))
            self.connection.execute(
                "UPDATE progress SET history=?,live=MAX(live,?),complete=0 WHERE id=1",
                (message - 1, message - 1))
            self.connection.execute(
                "UPDATE jobs SET status='excluded',error='Before configured start message'"
                " WHERE message<? AND status='queued'", (message,))


def video_metadata(message, source=SOURCES[0]):
    if not message or getattr(message, "noforwards", False) or is_advertisement(message.message):
        return None
    document = getattr(message, "document", None)
    if not document:
        return None
    if (source.movie_tag and (message.message or "").lstrip().startswith("#")
            and not re.search(r"导演|導演|类型|類型|片长|片長|剧情简介|劇情簡介|上映日期|中文名",
                              message.message or "")):
        return None
    if re.search(r"广告|廣告|推广|推廣|招商|招代理|博彩|送彩金|注册送",
                 (message.message or "").split("\n", 1)[0]):
        return None
    try:
        caption = clean_movie_caption(message.message, source)
    except ValueError as error:
        logger.warning("Movie caption ineligible message=%s: %s", message.id, error)
        return None
    parsed = parse_caption(caption)
    if (not parsed or re.search(
            r"广告|廣告|推广|推廣|招商|招代理|博彩|送彩金|注册送",
            parsed["title"] + " " + parsed["tags"])
            or ("《" not in parsed["title"] and parsed["category"] == "其他")):
        return None
    video = next((attr for attr in document.attributes if isinstance(attr, types.DocumentAttributeVideo)), None)
    if (not video or getattr(video, "round_message", False)
            or any(isinstance(attr, types.DocumentAttributeAnimated) for attr in document.attributes)):
        return None
    return {"duration": int(video.duration), "document_id": document.id}


def clean_movie_caption(text, source=SOURCES[0]):
    text = (text or "").strip()
    if source.short_drama:
        first = text.split("\n", 1)[0].strip()
        if (re.search(r"广告|廣告|推广|推廣|招商|招代理|博彩|送彩金|注册送|返水|百家乐",
                      text)
                or not re.search(
                    r"合集|全集|完整版|全剧|一口气|完结|大结局|纯享版|尊享版|"
                    r"(?:第\s*)?[零〇一二三四五六七八九十百\d]+"
                    r"(?:\s*[-~～至到]\s*[零〇一二三四五六七八九十百\d]+)?\s*季", first)):
            raise ValueError("Short drama needs an identifiable compilation title, without advertising")
        title = clean_text(first).strip()
        if not re.search(r"《[^》]+》", title):
            raise ValueError("Short drama compilation has no bracketed title")
        caption = title + ("" if "#短剧" in title.split() else " #短剧")
        if len(caption.encode("utf-16-le")) // 2 > 1024:
            raise ValueError("Short drama caption exceeds Telegram caption limit")
        return caption
    if source.movie_tag and text.startswith("#"):
        match = re.match(r"(?:\s*#\w+)+", text)
        labels = re.findall(r"#(\w+)", match.group()) if match else []
        if not labels or re.fullmatch(r"(?:4K|8K|1080P?|720P?|电影|广告|付费广告)", labels[0], re.I):
            raise ValueError("Movie caption has no identifiable title")
        text = f"《{labels[0]}》 " + " ".join(f"#{label}" for label in labels)
    first_line, _, rest = text.partition("\n")
    title, marker, tags = first_line.partition("#")
    title = clean_text(title).strip()
    # A bracketed title/episode is authoritative; discard inline navigation after it.
    bracketed = re.search(r"《[^》]+》", title)
    if bracketed:
        episode = re.match(
            r"\s*(?:第[零〇一二三四五六七八九十百千万两\d上下中终完（）()./\-]+[集话話期部季]"
            r"[零〇一二三四五六七八九十百千万两\d（）()]*|[上下中]集)", title[bracketed.end():])
        title = bracketed.group() + (episode.group().strip() if episode else "")
    if not title:
        raise ValueError("Movie caption has no title")
    tail = f"#{tags}\n{rest}" if marker else rest
    match = re.match(r"(?:\s*#\w+)+", tail)
    labels = re.findall(r"#\w+", match.group()) if match else []
    labels = [label for label in labels if not re.search(
        r"广告|廣告|推广|推廣|招商|招代理|博彩|送彩金|注册送|导航|導航|频道|頻道|订阅|訂閱|联系|聯繫", label)]
    if source.movie_tag and "#电影" not in labels:
        labels.append("#电影")
    caption = " ".join([title, *labels])
    if len(caption.encode("utf-16-le")) // 2 > 1024:
        raise ValueError("Clean movie caption exceeds Telegram caption limit")
    return caption


async def resolve_album_caption(user, source, message, config):
    if (not message or message.message or not getattr(message, "grouped_id", None)
            or getattr(message, "noforwards", False)):
        return message
    # Telegram albums contain at most ten messages; accept only the exact same group.
    siblings = await user.get_messages(source, ids=list(range(max(1, message.id - 9), message.id + 10)))
    for sibling in siblings:
        if (sibling and getattr(sibling, "grouped_id", None) == message.grouped_id
                and sibling.message and not getattr(sibling, "noforwards", False)):
            resolved = copy(message)
            resolved.message = sibling.message
            if video_metadata(resolved, config):
                return resolved
    return message


async def scan_source(user, source, store, historical):
    progress = store.progress()
    if historical and progress["complete"]:
        return
    # Bound the queue, not the daily total. Live messages always get their own scan.
    if historical and store.connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE historical=1 AND status IN ('queued','forwarded')"
    ).fetchone()[0] >= 50:
        return
    start = int(store.setting("start_message", str(store.source.start)))
    options = {"min_id": max(start - 1, progress["history" if historical else "live"]),
               "reverse": True, "limit": 50}
    if historical:
        options["max_id"] = progress["boundary"] + 1
    count = 0
    async for message in user.iter_messages(source, **options):
        count += 1
        message = await resolve_album_caption(user, source, message, store.source)
        if video_metadata(message, store.source):
            store.enqueue(message.id, historical)
        store.advance(message.id, historical)
    if historical and count < 50:
        with store.connection:
            store.connection.execute("UPDATE progress SET complete=1 WHERE id=1")


async def scan_owned(user, target, store):
    """Inventory existing channel copies before forwarding historical source files."""
    count = 0
    async for message in user.iter_messages(
        target, min_id=int(store.setting("owned_cursor")), reverse=True, limit=100
    ):
        count += 1
        document = getattr(message, "document", None)
        with store.connection:
            if document and any(isinstance(attr, types.DocumentAttributeVideo) for attr in document.attributes):
                store.connection.execute("INSERT OR IGNORE INTO owned VALUES (?,?)", (document.id, message.id))
            store.connection.execute(
                "INSERT OR REPLACE INTO settings VALUES ('owned_cursor',?)", (str(message.id),))
    return count < 100


async def reconcile_recent(user, source, store):
    now = time.time()
    if now < float(store.setting("recent_checked_at")) + RECENT_RECHECK_INTERVAL:
        return
    start = int(store.setting("start_message", str(store.source.start)))
    queued = 0
    async for message in user.iter_messages(source, min_id=start - 1, limit=RECENT_RECHECK_LIMIT):
        job = store.connection.execute("SELECT status FROM jobs WHERE message=?", (message.id,)).fetchone()
        if job and job["status"] != "skipped":
            continue
        message = await resolve_album_caption(user, source, message, store.source)
        if video_metadata(message, store.source) and store.reconcile(message.id):
            queued += 1
            logger.info("Movie recovered recent source=%s message=%s", store.source.id, message.id)
    store.set("recent_checked_at", now)
    logger.info("Movie recent recheck source=%s queued=%s limit=%s",
                store.source.id, queued, RECENT_RECHECK_LIMIT)


async def process_job(user, bot, source, target, store, job, other_stores=()):
    if job["status"] == "forwarded":
        messages = await bot.get_messages(types.PeerChannel(target.id), ids=[job["result_id"]])
        message = messages[0] if messages else None
        metadata = video_metadata(message, store.source)
        if not metadata:
            raise ValueError("Owned video unavailable or no longer eligible; channel copy retained")
        caption = clean_movie_caption(message.message, store.source)
        if not getattr(message, "fwd_from", None):
            try:
                await bot.edit_message(
                    types.PeerChannel(target.id), message.id,
                    buttons=[[
                        Button.url("🤖 进入机器人", BOT_URL),
                        Button.url("💬 联系昊天", CONTACT_URL),
                    ]])
            except errors.MessageNotModifiedError:
                pass
        saved = await asyncio.to_thread(
            save_movie, f"-100{target.id}", message.id, caption,
            duration=metadata["duration"],
            posted_at=message.date.astimezone(timezone.utc).replace(tzinfo=None))
        if not saved:
            raise ValueError("Movie metadata was not indexed")
        store.update(job, "sent")
        logger.info("Movie indexed source=%s message=%s target=%s", source.id, job["message"], message.id)
        return
    current_source = await user.get_entity(source)
    if job["message"] < int(store.setting("start_message", str(store.source.start))):
        store.update(job, "excluded", "Before configured start message")
        return
    if getattr(current_source, "noforwards", False):
        store.update(job, "skipped", "Source content protected")
        logger.warning("Movie source protected; skipped message=%s", job["message"])
        return
    messages = await user.get_messages(source, ids=[job["message"]])
    message = messages[0] if messages else None
    message = await resolve_album_caption(user, source, message, store.source)
    metadata = video_metadata(message, store.source)
    if not metadata:
        store.update(job, "skipped", "Missing, protected, advertisement or unsupported video/title")
        logger.warning("Movie ineligible; skipped message=%s", job["message"])
        return
    duplicate = None
    for candidate in (store, *(item for item in other_stores
                              if item is not store and item.source.target_id == store.source.target_id)):
        duplicate = candidate.connection.execute(
            "SELECT result_id FROM jobs WHERE document_id=? AND status IN ('forwarded','sent') LIMIT 1",
            (metadata["document_id"],)).fetchone()
        if duplicate is None:
            duplicate = candidate.connection.execute(
                "SELECT message AS result_id FROM owned WHERE document_id=?",
                (metadata["document_id"],)).fetchone()
        if duplicate:
            break
        uncertain = candidate.connection.execute(
            "SELECT 1 FROM jobs WHERE document_id=? AND status IN ('sending','uncertain') LIMIT 1",
            (metadata["document_id"],)).fetchone()
        if uncertain:
            store.update(job, "uncertain", "Matching publication outcome unknown; inspect target",
                         document_id=metadata["document_id"])
            logger.warning("Movie duplicate uncertain; not sending source=%s message=%s", source.id, message.id)
            return
    if duplicate:
        store.update(job, "forwarded", result_id=duplicate["result_id"], document_id=metadata["document_id"])
        return
    caption = clean_movie_caption(message.message, store.source)
    store.update(job, "sending", document_id=metadata["document_id"])
    try:
        # Reference Telegram's existing document; never download/reupload the video.
        sent = await user.send_file(
            target, message.document, caption=caption, parse_mode=None,
            formatting_entities=[], silent=True)
    except errors.FloodWaitError:
        # A server-declared rejection has not published this single-message request.
        store.update(job, "queued", "Telegram flood wait; retry only after persisted delay")
        raise
    if sent is None or not getattr(sent, "id", None):
        raise RuntimeError("Publication outcome unknown")
    store.update(job, "forwarded", result_id=sent.id)
    with store.connection:
        store.connection.execute("INSERT OR IGNORE INTO owned VALUES (?,?)",
                                 (metadata["document_id"], sent.id))
    logger.info("Movie stored source=%s message=%s target=%s", source.id, message.id, sent.id)


async def movie_worker(user, bot, directory, sources=SOURCES):
    from channel_sync import single_instance
    with ExitStack() as locks:
        for config in sources:
            locks.enter_context(single_instance(directory, f"movie-worker-{config.id}.lock"))
        await _movie_worker(user, bot, directory, sources)


async def _movie_worker(user, bot, directory, sources):
    from channel_sync import resolve_channel
    stores = [MovieStore(store_path(directory, config), recover=True, source=config) for config in sources]
    contexts = {}
    turn = 0
    try:
        while True:
            for item in stores:
                if item.setting("enabled") == "1":
                    record_worker_heartbeat(item.connection, f"movie_import_{item.source.id}")
            enabled = [item for item in stores if item.setting("enabled") == "1"]
            if not enabled:
                await asyncio.sleep(30)
                continue
            # One shared cooldown and one sending loop for all movie sources.
            wait = max(float(item.setting("not_before")) for item in stores) - time.time()
            if wait > 0:
                await asyncio.sleep(min(wait, 30))
                continue
            store = enabled[turn % len(enabled)]
            turn += 1
            config = store.source
            try:
                if config.id not in contexts:
                    candidate = await resolve_channel(user, config.id, config.username)
                    owned = await resolve_channel(user, config.target_id, config.target_username)
                    if candidate.id != config.id or owned.id != config.target_id:
                        raise ValueError("Movie source or target identity changed")
                    if getattr(candidate, "noforwards", False):
                        raise ValueError("Movie source is protected")
                    permissions = await user.get_permissions(owned, await user.get_me())
                    if not permissions.is_admin or not permissions.post_messages:
                        raise ValueError("Movie forwarding account needs target posting permission")
                    reader = await resolve_channel(bot, config.target_id, config.target_username)
                    if reader.id != config.target_id:
                        raise ValueError("Movie publisher target identity changed")
                    editor = await bot.get_permissions(reader, await bot.get_me())
                    if not editor.is_admin or not editor.edit_messages:
                        raise ValueError("Movie bot needs target edit_messages permission for buttons")
                    await asyncio.to_thread(ensure_tables)
                    latest = await user.get_messages(candidate, limit=1)
                    store.progress(latest[0].id if latest else 0)
                    contexts[config.id] = candidate, owned
                    logger.info("MOVIE READY: @%s -> @%s; historical+live; Telegram flood waits respected",
                                config.username, config.target_username)
                source, target = contexts[config.id]
                if not await scan_owned(user, target, store):
                    await asyncio.sleep(3)
                    continue
                await scan_source(user, source, store, historical=False)
                await scan_source(user, source, store, historical=True)
                await reconcile_recent(user, source, store)
                job = store.pending()
                if job:
                    try:
                        await process_job(user, bot, source, target, store, job, other_stores=stores)
                    except errors.FloodWaitError:
                        raise
                    except Exception as error:
                        record_worker_failure(store.connection, f"movie_import_{config.id}", error)
                        current = store.connection.execute(
                            "SELECT * FROM jobs WHERE message=?", (job["message"],)).fetchone()
                        if current["status"] == "sending":
                            store.update(current, "uncertain", type(error).__name__)
                        elif current["status"] == "forwarded" and not isinstance(error, ValueError):
                            store.update(current, "forwarded", type(error).__name__)
                        else:
                            store.update(current, "failed", type(error).__name__)
                        logger.exception("Movie job %s failed; persisted state retained", job["message"])
                        await asyncio.sleep(30)
                # Local pacing is not a claimed official allowance; no fixed daily quota.
                await asyncio.sleep(3)
            except errors.FloodWaitError as error:
                store.set("not_before", time.time() + error.seconds + 1)
                logger.warning("Movie flood wait: %s seconds; persisted across restart", error.seconds)
            except Exception as error:
                record_worker_failure(store.connection, f"movie_import_{config.id}", error)
                logger.exception("Movie synchronization unavailable; retrying in 30 seconds")
                await asyncio.sleep(30)
    finally:
        for store in stores:
            store.close()


async def run_source(directory, config=None):
    from channel_sync import credentials
    from telethon import TelegramClient
    from telethon.sessions import MemorySession
    from telethon.crypto import AuthKey
    clients = []
    try:
        for filename in ("user.session", "publisher.session"):
            conn = sqlite3.connect((directory / filename).as_uri() + "?mode=ro", uri=True)
            try:
                saved = conn.execute("SELECT dc_id,server_address,port,auth_key FROM sessions").fetchone()
            finally:
                conn.close()
            if not saved:
                raise ValueError("Authorized Telegram session is missing")
            session = MemorySession()
            session.set_dc(*saved[:3])
            session.auth_key = AuthKey(saved[3])
            conn = sqlite3.connect((directory / filename).as_uri() + "?mode=ro", uri=True)
            try:
                cached = conn.execute("SELECT id,hash,username,phone,name FROM entities").fetchall()
            finally:
                conn.close()
            session.process_entities([
                types.Channel(id=-ident - 1000000000000, access_hash=access_hash,
                              title=name or "", username=username, broadcast=True,
                              photo=types.ChatPhotoEmpty(), date=None)
                for ident, access_hash, username, _, name in cached if ident < -1000000000000
            ])
            client = TelegramClient(session, *credentials(), receive_updates=False, flood_sleep_threshold=0)
            clients.append(client)
            await client.connect()
            if not await client.is_user_authorized():
                raise ValueError("Telegram session is not authorized")
        user, bot = clients
        if (await user.get_me()).bot or str((await bot.get_me()).id) != os.environ.get(
                "TELEGRAM_BOT_TOKEN", "").partition(":")[0]:
            raise ValueError("Reader or publisher identity does not match configuration")
        if config is not None:
            await movie_worker(user, bot, directory, sources=(config,))
        else:
            from music_ingest import music_worker
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(music_worker(user, bot, directory))
                tasks.create_task(movie_worker(user, bot, directory,
                                               sources=tuple(item for item in SOURCES if not item.short_drama)))
    finally:
        for client in clients:
            await client.disconnect()


def main():
    from channel_sync import state_directory
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "enable", "disable", "run", "run-media"))
    parser.add_argument("--source", choices=[item.username for item in SOURCES],
                        default=SOURCE_USERNAME, help="Authorized source; default preserves the original source")
    parser.add_argument("--start-message", type=int, help="Inclusive source history start; resets scan cursor")
    args = parser.parse_args()
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    config = next(item for item in SOURCES if item.username == args.source)
    if args.action in ("run", "run-media"):
        if args.start_message is not None:
            parser.error("Use enable --start-message before run")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logging.getLogger("telethon").setLevel(logging.WARNING)
        asyncio.run(run_source(directory, config if args.action == "run" else None))
        return
    store = MovieStore(store_path(directory, config), source=config)
    try:
        if args.start_message is not None:
            store.set_start(args.start_message)
        if args.action != "status":
            store.set("enabled", int(args.action == "enable"))
        print(f"source={config.username} enabled={store.setting('enabled')} not_before={store.setting('not_before')}")
        print(f"start_message={store.setting('start_message', str(config.start))}")
        for row in store.connection.execute("SELECT status,COUNT(*) AS total FROM jobs GROUP BY status"):
            print(f"{row['status']}: {row['total']}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
