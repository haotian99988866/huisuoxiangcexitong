"""Daily historical music collection and live forwarding to the owned music channel."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import sqlite3
from types import SimpleNamespace

from telethon import errors, types, utils

from channel_sync_clean import is_advertisement
from music_metadata import identify
from music_sources import SOURCES

logger = logging.getLogger(__name__)
DAILY_LIMIT = 50
MAX_DURATION_SECONDS = 8 * 60
LOCAL_ZONE = timezone(timedelta(hours=7))


def day_key():
    return datetime.now(LOCAL_ZONE).date().isoformat()


class MusicStore:
    def __init__(self, path, recover=False):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS progress (
                source INTEGER PRIMARY KEY, boundary INTEGER NOT NULL,
                history INTEGER NOT NULL DEFAULT 0, live INTEGER NOT NULL,
                complete INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS jobs (
                source INTEGER NOT NULL, message INTEGER NOT NULL, historical INTEGER NOT NULL,
                day TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                result_id INTEGER, track_id INTEGER, document_id INTEGER, error TEXT,
                PRIMARY KEY(source,message)
            );
        """)
        if recover:
            with self.connection:
                self.connection.execute("UPDATE jobs SET status='uncertain',error='Interrupted during forward; inspect target'"
                                        " WHERE status='sending'")

    def enabled(self):
        return self.connection.execute("SELECT 1 FROM settings WHERE key='enabled' AND value='1'").fetchone() is not None

    def enable(self):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO settings VALUES ('enabled','1')")

    def disable(self):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO settings VALUES ('enabled','0')")

    def progress(self, source, latest):
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO progress(source,boundary,live) VALUES (?,?,?)",
                                    (source, latest, latest))
        return self.connection.execute("SELECT * FROM progress WHERE source=?", (source,)).fetchone()

    def remaining(self, source, day):
        used = self.connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE source=? AND historical=1"
            " AND status!='skipped' AND (day=? OR status IN ('queued','sending','forwarded'))",
            (source, day),
        ).fetchone()[0]
        return max(0, DAILY_LIMIT - used)

    def enqueue(self, source, message, historical, day):
        with self.connection:
            if historical and self.remaining(source, day) == 0:
                return False
            self.connection.execute(
                "INSERT OR IGNORE INTO jobs(source,message,historical,day) VALUES (?,?,?,?)",
                (source, message, int(historical), day),
            )
        return True

    def advance(self, source, message, historical):
        column = "history" if historical else "live"
        with self.connection:
            self.connection.execute(f"UPDATE progress SET {column}=MAX({column},?) WHERE source=?", (message, source))

    def complete(self, source):
        with self.connection:
            self.connection.execute("UPDATE progress SET complete=1 WHERE source=?", (source,))

    def update(self, job, status, error=None, result_id=None, track_id=None, document_id=None):
        with self.connection:
            self.connection.execute(
                "UPDATE jobs SET status=?,error=?,result_id=COALESCE(?,result_id),"
                "track_id=COALESCE(?,track_id),document_id=COALESCE(?,document_id) WHERE source=? AND message=?",
                (status, error, result_id, track_id, document_id, job["source"], job["message"]),
            )

    def pending(self):
        return self.connection.execute(
            "SELECT * FROM jobs WHERE status IN ('queued','forwarded')"
            " ORDER BY historical,CASE status WHEN 'forwarded' THEN 0 ELSE 1 END,"
            " (SELECT COUNT(*) FROM jobs done WHERE done.source=jobs.source AND done.status='sent'),"
            " source,message LIMIT 1",
        ).fetchone()

    def record_send_day(self, job):
        with self.connection:
            self.connection.execute("UPDATE jobs SET day=? WHERE source=? AND message=?",
                                    (day_key(), job["source"], job["message"]))

    def close(self):
        self.connection.close()


def audio_metadata(message):
    from music import MAX_AUDIO_BYTES, telegram_audio_name
    doc = getattr(message, "document", None)
    if not doc or getattr(message, "video", None) or getattr(message, "voice", None):
        return None
    attrs = doc.attributes
    if any(isinstance(attr, types.DocumentAttributeVideo) for attr in attrs):
        return None
    audio = next((attr for attr in attrs if isinstance(attr, types.DocumentAttributeAudio)), None)
    if not audio or audio.voice:
        return None
    duration = getattr(audio, "duration", None)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < duration <= MAX_DURATION_SECONDS:
        logger.info("Music message %s skipped: missing/invalid duration or exceeds %s seconds",
                    message.id, MAX_DURATION_SECONDS)
        return None
    filename = next((attr.file_name for attr in attrs if isinstance(attr, types.DocumentAttributeFilename)), "")
    media = SimpleNamespace(file_name=filename, mime_type=doc.mime_type)
    suffix = telegram_audio_name(media)
    if not 12 <= doc.size <= MAX_AUDIO_BYTES:
        raise ValueError("Audio exceeds 128 MB or has invalid size")
    title, artist = identify(getattr(audio, "title", None), getattr(audio, "performer", None),
                             filename, message.message)
    if not title or title.casefold() in {"hd", "official mv", "lyrics video", "未命名歌曲"}:
        title = "未标注歌名"
    return {"title": title, "artist": artist, "suffix": suffix, "size": doc.size, "document_id": doc.id}


async def scan_source(user, source, store, historical):
    progress = store.progress(source.id, 0)
    if historical and (progress["complete"] or not store.remaining(source.id, day_key())):
        return
    kwargs = {"min_id": progress["history" if historical else "live"], "reverse": True, "limit": 100}
    if historical:
        kwargs["max_id"] = progress["boundary"] + 1
    seen = 0
    for_message_day = day_key()
    async for message in user.iter_messages(source, **kwargs):
        seen += 1
        metadata = None
        try:
            metadata = audio_metadata(message)
        except ValueError as error:
            logger.warning("Music source %s message %s skipped: %s", source.id, message.id, error)
        if metadata and not is_advertisement(message.message) and not getattr(message, "noforwards", False):
            if not store.enqueue(source.id, message.id, historical, for_message_day):
                return
        store.advance(source.id, message.id, historical)
        if historical and not store.remaining(source.id, for_message_day):
            return
    if historical and seen < 100:
        store.complete(source.id)


def register_track(message, source_id, source_message):
    """Index the owned-channel copy without downloading it to the playback cache."""
    import hashlib
    import music
    from music_dedupe import import_lock, save_track
    from db import get_conn
    data = audio_metadata(message)
    if data is None:
        raise ValueError("Forwarded message is not an audio file")
    # Unknown artists must not cause unrelated recordings with generic titles to merge.
    if data["artist"] == "未知歌手" or data["title"] == "未标注歌名":
        data["title"] = data["title"][:70] + f"（{SOURCES[source_id][0]}·{source_message}）"
    file_id = utils.pack_bot_file_id(message.document)
    if not file_id:
        raise ValueError("Owned-channel audio has no usable file identifier")
    name = hashlib.sha256(f"{file_id}:{message.id}".encode()).hexdigest()[:32] + data["suffix"]
    conn = get_conn()
    try:
        with import_lock(conn):
            ident, _ = save_track(conn, data["title"], data["artist"], SOURCES[source_id][1], name,
                                  channel_id=music.MUSIC_CHANNEL_ID, message_id=message.id,
                                  file_id=file_id, size=data["size"])
            conn.commit()
        return ident
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def process_job(user, bot, sources, target, store, job):
    if job["status"] == "forwarded":
        messages = await bot.get_messages(types.PeerChannel(target.id), ids=[job["result_id"]])
        if not messages or not messages[0]:
            raise ValueError("Stored music copy missing; inspect before retrying")
        if audio_metadata(messages[0]) is None:
            store.update(job, "skipped", "Stored audio fails eligibility check; channel copy retained")
            logger.warning("Music stored copy %s skipped; channel copy retained", job["result_id"])
            return
        ident = await asyncio.to_thread(register_track, messages[0], job["source"], job["message"])
        store.update(job, "sent", track_id=ident)
        logger.info("Music indexed source=%s message=%s target=%s track=%s",
                    job["source"], job["message"], job["result_id"], ident)
        return
    source = await user.get_entity(sources[job["source"]])
    if getattr(source, "noforwards", False):
        store.update(job, "skipped", "Source content protected")
        return
    messages = await user.get_messages(source, ids=[job["message"]])
    message = messages[0] if messages else None
    if not message:
        store.update(job, "skipped", "Source message unavailable")
        return
    metadata = audio_metadata(message)
    if not metadata or getattr(message, "noforwards", False) or is_advertisement(message.message):
        store.update(job, "skipped", "Not an authorized unprotected audio post")
        return
    duplicate = store.connection.execute(
        "SELECT result_id,track_id FROM jobs WHERE document_id=? AND status='sent' LIMIT 1",
        (metadata["document_id"],),
    ).fetchone()
    if duplicate:
        store.record_send_day(job)
        store.update(job, "sent", result_id=duplicate["result_id"], track_id=duplicate["track_id"],
                     document_id=metadata["document_id"])
        return
    store.record_send_day(job)
    store.update(job, "sending", document_id=metadata["document_id"])
    # Telegram stores the copy in the owned channel; no local bulk media download.
    sent = await user.forward_messages(target, [message.id], from_peer=source, silent=True)
    if len(sent) != 1 or sent[0] is None:
        raise RuntimeError("Forward outcome unknown")
    store.update(job, "forwarded", result_id=sent[0].id)
    logger.info("Music stored source=%s message=%s target=%s", job["source"], job["message"], sent[0].id)


async def music_worker(user, bot, directory):
    import music
    store = MusicStore(directory / "music-import.sqlite3", recover=True)
    sources = {}
    target = None
    try:
        while True:
            delay = 5
            if not store.enabled():
                await asyncio.sleep(30)
                continue
            try:
                if target is None:
                    target = await user.get_entity(f"https://t.me/{music.MUSIC_CHANNEL_USERNAME}")
                    for client in (user, bot):
                        owned = await client.get_entity(f"https://t.me/{music.MUSIC_CHANNEL_USERNAME}")
                        permissions = await client.get_permissions(owned, await client.get_me())
                        if not permissions.is_admin or not permissions.post_messages:
                            raise ValueError("Music target requires both account and bot posting permissions")
                    await asyncio.to_thread(music.ensure_tables)
                    for ident, (username, _) in SOURCES.items():
                        source = await user.get_entity(f"https://t.me/{username}")
                        if source.id != ident:
                            raise ValueError(f"Music source identity changed: {username}")
                        sources[ident] = source
                        latest = await user.get_messages(source, limit=1)
                        store.progress(ident, latest[0].id if latest else 0)
                    logger.info("MUSIC READY: four authorized audio sources; history=50/source/day UTC+7; max_duration=480s")
                for source in sources.values():
                    if getattr(source, "noforwards", False):
                        logger.warning("Music source protected: %s; not scanned", source.id)
                        continue
                    await scan_source(user, source, store, historical=False)
                    await scan_source(user, source, store, historical=True)
                for _ in range(20):
                    if not store.enabled():
                        break
                    job = store.pending()
                    if not job:
                        break
                    try:
                        await process_job(user, bot, sources, target, store, job)
                    except errors.FloodWaitError:
                        if job["status"] == "queued":
                            store.update(job, "queued", "Telegram flood wait; retry after delay")
                        raise
                    except Exception as error:
                        current = store.connection.execute(
                            "SELECT status FROM jobs WHERE source=? AND message=?",
                            (job["source"], job["message"]),
                        ).fetchone()["status"]
                        if current == "forwarded":
                            # The channel copy exists. Retry indexing, never forward again.
                            logger.exception("Music indexing delayed source=%s message=%s", job["source"], job["message"])
                            delay = 30
                            break
                        if current == "queued" and not isinstance(error, ValueError):
                            store.update(job, "queued", type(error).__name__)
                            logger.exception("Music import unavailable before forward; retained for retry")
                            delay = 30
                            break
                        store.update(job, "uncertain" if current == "sending" else "failed", type(error).__name__)
                        logger.exception("Music import job failed source=%s message=%s status=%s",
                                         job["source"], job["message"], current)
                    await asyncio.sleep(.5)
            except errors.FloodWaitError as error:
                delay = max(30, error.seconds + 1)
                logger.warning("Music collection rate limited; waiting %s seconds", delay)
            except Exception:
                target = None
                logger.exception("Music collection failed; progress retained; retrying in 30 seconds")
                delay = 30
            await asyncio.sleep(delay)
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("enable", "disable", "status"))
    parser.add_argument("--rights-confirmed", action="store_true")
    args = parser.parse_args()
    from channel_sync import state_directory
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    store = MusicStore(directory / "music-import.sqlite3")
    try:
        if args.mode == "enable":
            if not args.rights_confirmed:
                parser.error("Explicit playback rights confirmation is required")
            store.enable()
        elif args.mode == "disable":
            store.disable()
        print("Music collection enabled:", store.enabled())
        for row in store.connection.execute("SELECT * FROM progress ORDER BY source"):
            print(dict(row))
        for row in store.connection.execute("SELECT status,COUNT(*) total FROM jobs GROUP BY status"):
            print(dict(row))
    finally:
        store.close()


if __name__ == "__main__":
    main()
