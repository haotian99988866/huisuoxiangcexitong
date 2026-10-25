"""Opt-in, resumable native forwarding for the authorized movie source."""
import argparse
import asyncio
from datetime import timezone
import logging
import re
import sqlite3
import time

from telethon import Button, errors, types

from channel_sync_clean import is_advertisement
from movies import MOVIE_CHANNEL_USERNAME, ensure_tables, parse_caption, save_movie
from welcome import BOT_URL, CONTACT_URL

logger = logging.getLogger(__name__)
SOURCE_ID = 1345733873
SOURCE_USERNAME = "luolimeimei"
TARGET_ID = 3920194464
HISTORY_START_ID = 2377


class MovieStore:
    def __init__(self, path, recover=False):
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
        if message < int(self.setting("start_message", str(HISTORY_START_ID))):
            return
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO jobs(message,historical) VALUES (?,?)",
                                    (message, int(historical)))

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


def video_metadata(message):
    if not message or getattr(message, "noforwards", False) or is_advertisement(message.message):
        return None
    document = getattr(message, "document", None)
    if not document:
        return None
    try:
        caption = clean_movie_caption(message.message) if parse_caption(message.message) else ""
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
            or any(isinstance(attr, types.DocumentAttributeAnimated) for attr in document.attributes)
            or not parse_caption(message.message)):
        return None
    return {"duration": int(video.duration), "document_id": document.id}


def clean_movie_caption(text):
    text = (text or "").strip()
    first_line, _, rest = text.partition("\n")
    title, marker, tags = first_line.partition("#")
    title = title.strip()
    if not title:
        raise ValueError("Movie caption has no title")
    tail = f"#{tags}\n{rest}" if marker else rest
    match = re.match(r"(?:\s*#\w+)+", tail)
    labels = re.findall(r"#\w+", match.group()) if match else []
    caption = " ".join([title, *labels])
    if len(caption.encode("utf-16-le")) // 2 > 1024:
        raise ValueError("Clean movie caption exceeds Telegram caption limit")
    return caption


async def scan_source(user, source, store, historical):
    progress = store.progress()
    if historical and progress["complete"]:
        return
    # Bound the queue, not the daily total. Live messages always get their own scan.
    if historical and store.connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE historical=1 AND status IN ('queued','forwarded')"
    ).fetchone()[0] >= 50:
        return
    start = int(store.setting("start_message", str(HISTORY_START_ID)))
    options = {"min_id": max(start - 1, progress["history" if historical else "live"]),
               "reverse": True, "limit": 50}
    if historical:
        options["max_id"] = progress["boundary"] + 1
    count = 0
    async for message in user.iter_messages(source, **options):
        count += 1
        if video_metadata(message):
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


async def process_job(user, bot, source, target, store, job):
    if job["status"] == "forwarded":
        messages = await bot.get_messages(types.PeerChannel(target.id), ids=[job["result_id"]])
        message = messages[0] if messages else None
        metadata = video_metadata(message)
        if not metadata:
            raise ValueError("Owned video unavailable or no longer eligible; channel copy retained")
        caption = clean_movie_caption(message.message)
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
    if job["message"] < int(store.setting("start_message", str(HISTORY_START_ID))):
        store.update(job, "excluded", "Before configured start message")
        return
    if getattr(current_source, "noforwards", False):
        store.update(job, "skipped", "Source content protected")
        logger.warning("Movie source protected; skipped message=%s", job["message"])
        return
    messages = await user.get_messages(source, ids=[job["message"]])
    message = messages[0] if messages else None
    metadata = video_metadata(message)
    if not metadata:
        store.update(job, "skipped", "Missing, protected, advertisement or unsupported video/title")
        logger.warning("Movie ineligible; skipped message=%s", job["message"])
        return
    duplicate = store.connection.execute(
        "SELECT result_id FROM jobs WHERE document_id=? AND status IN ('forwarded','sent') LIMIT 1",
        (metadata["document_id"],)).fetchone()
    if duplicate is None:
        duplicate = store.connection.execute(
            "SELECT message AS result_id FROM owned WHERE document_id=?",
            (metadata["document_id"],)).fetchone()
    if duplicate:
        store.update(job, "forwarded", result_id=duplicate["result_id"], document_id=metadata["document_id"])
        return
    caption = clean_movie_caption(message.message)
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


async def movie_worker(user, bot, directory):
    store = MovieStore(directory / "movie-import.sqlite3", recover=True)
    source = target = None
    try:
        while True:
            if store.setting("enabled") != "1":
                await asyncio.sleep(30)
                continue
            wait = float(store.setting("not_before")) - time.time()
            if wait > 0:
                await asyncio.sleep(min(wait, 30))
                continue
            try:
                if source is None:
                    candidate = await user.get_entity(f"https://t.me/{SOURCE_USERNAME}")
                    owned = await user.get_entity(f"https://t.me/{MOVIE_CHANNEL_USERNAME}")
                    if candidate.id != SOURCE_ID or owned.id != TARGET_ID:
                        raise ValueError("Movie source or target identity changed")
                    if getattr(candidate, "noforwards", False):
                        raise ValueError("Movie source is protected")
                    permissions = await user.get_permissions(owned, await user.get_me())
                    if not permissions.is_admin or not permissions.post_messages:
                        raise ValueError("Movie forwarding account needs target posting permission")
                    reader = await bot.get_entity(f"https://t.me/{MOVIE_CHANNEL_USERNAME}")
                    if reader.id != TARGET_ID:
                        raise ValueError("Movie publisher target identity changed")
                    editor = await bot.get_permissions(reader, await bot.get_me())
                    if not editor.is_admin or not editor.edit_messages:
                        raise ValueError("Movie bot needs target edit_messages permission for buttons")
                    await asyncio.to_thread(ensure_tables)
                    latest = await user.get_messages(candidate, limit=1)
                    store.progress(latest[0].id if latest else 0)
                    source, target = candidate, owned
                    logger.info("MOVIE READY: @%s -> @%s; historical+live; Telegram flood waits respected",
                                SOURCE_USERNAME, MOVIE_CHANNEL_USERNAME)
                if not await scan_owned(user, target, store):
                    await asyncio.sleep(3)
                    continue
                await scan_source(user, source, store, historical=False)
                await scan_source(user, source, store, historical=True)
                job = store.pending()
                if job:
                    try:
                        await process_job(user, bot, source, target, store, job)
                    except errors.FloodWaitError:
                        raise
                    except Exception as error:
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
            except Exception:
                logger.exception("Movie synchronization unavailable; retrying in 30 seconds")
                await asyncio.sleep(30)
    finally:
        store.close()


def main():
    from channel_sync import state_directory
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "enable", "disable"))
    parser.add_argument("--start-message", type=int, help="Inclusive source history start; resets scan cursor")
    args = parser.parse_args()
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    store = MovieStore(directory / "movie-import.sqlite3")
    try:
        if args.start_message is not None:
            store.set_start(args.start_message)
        if args.action != "status":
            store.set("enabled", int(args.action == "enable"))
        print(f"enabled={store.setting('enabled')} not_before={store.setting('not_before')}")
        print(f"start_message={store.setting('start_message', str(HISTORY_START_ID))}")
        for row in store.connection.execute("SELECT status,COUNT(*) AS total FROM jobs GROUP BY status"):
            print(f"{row['status']}: {row['total']}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
