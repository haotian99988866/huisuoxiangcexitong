"""Persistent, single-worker preparation of small MP3 playback copies."""
import argparse
import asyncio
from contextlib import ExitStack
import logging
from pathlib import Path
import sqlite3
import subprocess
import time

from telethon import errors

logger = logging.getLogger(__name__)
PLAYBACK_BUDGET = 1024 * 1024 * 1024


def playback_directory():
    from music import library_dir
    return library_dir() / "playback"


class PlaybackStore:
    def __init__(self):
        from channel_sync import state_directory
        folder = state_directory()
        folder.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(folder / "music-playback.sqlite3", timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                name TEXT PRIMARY KEY, message INTEGER NOT NULL, size INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', priority INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0, error TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value REAL NOT NULL);
        """)

    def enqueue(self, name, message, size, priority=False):
        from music import MAX_AUDIO_BYTES, STORED_NAME
        if (not STORED_NAME.fullmatch(name) or not isinstance(message, int) or message <= 0
                or not isinstance(size, int) or not 12 <= size <= MAX_AUDIO_BYTES):
            raise ValueError("Invalid playback preparation source")
        with self.connection:
            self.connection.execute(
                "INSERT INTO jobs(name,message,size,priority) VALUES (?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET priority=MAX(priority,excluded.priority)",
                (name, message, size, int(priority)))

    def pending(self):
        # Finish an existing partial before opening another original-file download.
        partial = next(playback_directory().glob("*.download"), None)
        if partial:
            row = self.connection.execute(
                "SELECT * FROM jobs WHERE name=? AND status IN ('queued','preparing','failed')",
                (partial.name.removesuffix(".download"),)).fetchone()
            if row:
                return row if row["retry_at"] <= time.time() else None
        return self.connection.execute(
            "SELECT * FROM jobs WHERE status IN ('queued','preparing','failed') AND retry_at<=?"
            " ORDER BY priority DESC,retry_at,name LIMIT 1", (time.time(),)).fetchone()

    def update(self, name, status, error=None, delay=0):
        with self.connection:
            self.connection.execute("UPDATE jobs SET status=?,error=?,retry_at=? WHERE name=?",
                                    (status, error, time.time() + delay, name))

    def wait_until(self, deadline=None):
        with self.connection:
            if deadline is not None:
                self.connection.execute(
                    "INSERT INTO settings VALUES ('not_before',?) ON CONFLICT(key)"
                    " DO UPDATE SET value=MAX(value,excluded.value)", (deadline,))
        row = self.connection.execute("SELECT value FROM settings WHERE key='not_before'").fetchone()
        return row[0] if row else 0

    def close(self):
        self.connection.close()


def prepared_audio(name, message, size):
    """Only a verified, atomically published variant is eligible for playback."""
    store = PlaybackStore()
    try:
        store.enqueue(name, message, size, priority=True)
        row = store.connection.execute("SELECT status FROM jobs WHERE name=?", (name,)).fetchone()
        output = playback_directory() / (Path(name).stem + ".mp3")
        if row["status"] == "ready":
            if output.is_file() and output.stat().st_size >= 12:
                return output
            store.update(name, "queued", "Prepared file missing; rebuilding")
        return None
    finally:
        store.close()


def transcode(source, destination):
    import imageio_ffmpeg
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y",
         "-i", str(source), "-map", "0:a:0", "-vn", "-map_metadata", "-1",
         "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2",
         "-threads", "1", str(destination)],
        capture_output=True, timeout=300)
    if result.returncode:
        raise ValueError("Audio conversion failed; invalid or unsupported source")
    # Decode the entire result, not just its first bytes, before publishing it.
    check = subprocess.run(
        [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
         "-i", str(destination), "-map", "0:a:0", "-f", "null", "-"],
        capture_output=True, timeout=120)
    if check.returncode or destination.stat().st_size < 12:
        raise ValueError("Prepared MP3 failed decoding validation")


async def prepare(job):
    from music import library_dir, validate_audio
    from music_mtproto import _audio_chunks
    folder = playback_directory()
    folder.mkdir(parents=True, exist_ok=True)
    used = sum(path.stat().st_size for path in folder.glob("*.mp3"))
    # Reserve a conservative 16 MB for a <=8 minute, 192 kbps copy.
    if used + 16 * 1024 * 1024 > PLAYBACK_BUDGET:
        raise ValueError("Playback cache budget reached; preparation paused")
    name = job["name"]
    original = library_dir() / name
    partial = folder / (name + ".download")
    encoded = folder / (Path(name).stem + ".encoding.mp3")
    output = folder / (Path(name).stem + ".mp3")
    source = original
    try:
        if not original.is_file():
            offset = partial.stat().st_size if partial.is_file() else 0
            if offset > job["size"]:
                raise ValueError("Partial audio exceeds source size; inspect local preparation state")
            if offset < job["size"]:
                chunks = _audio_chunks(job["message"], job["size"], offset, job["size"] - 1,
                                       block_size=512 * 1024)
                try:
                    with partial.open("ab") as stream:
                        while True:
                            try:
                                chunk = await asyncio.wait_for(anext(chunks), timeout=60)
                            except StopAsyncIteration:
                                break
                            stream.write(chunk)
                            stream.flush()
                finally:
                    await chunks.aclose()
            if partial.stat().st_size != job["size"]:
                raise ValueError("Incomplete playback source")
            source = partial
        else:
            validate_audio(original, strict_suffix=False)
        try:
            await asyncio.to_thread(transcode, source, encoded)
            if encoded.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Prepared playback copy exceeds 16 MB budget; original retained")
        except ValueError:
            if source == partial:
                partial.unlink(missing_ok=True)
            raise
        encoded.replace(output)
        partial.unlink(missing_ok=True)
        logger.info("Playback MP3 prepared message=%s original=%s output=%s",
                    job["message"], job["size"], output.stat().st_size)
    finally:
        encoded.unlink(missing_ok=True)


def discover(store):
    from db import get_conn
    from music import MUSIC_CHANNEL_ID
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT file_name,message_id,file_size FROM music_tracks"
                " WHERE enabled=TRUE AND merged_into IS NULL AND channel_id=%s"
                " AND message_id IS NOT NULL AND file_size BETWEEN 12 AND %s"
                " AND (file_name LIKE '%%.flac' OR file_name LIKE '%%.wav'"
                " OR file_name LIKE '%%.ogg' OR file_size>12582912)",
                (MUSIC_CHANNEL_ID, 128 * 1024 * 1024))
            for row in cursor.fetchall():
                store.enqueue(row["file_name"], row["message_id"], row["file_size"])
    finally:
        conn.close()


async def playback_worker():
    from channel_sync import single_instance, state_directory
    while True:
        try:
            with single_instance(state_directory(), "music-playback.lock"):
                await _playback_worker()
        except Exception:
            logger.exception("Playback worker lock unavailable; retry in 30s")
            await asyncio.sleep(30)


async def _playback_worker():
    store = PlaybackStore()
    scanned = 0
    try:
        logger.info("PLAYBACK READY: single resumable worker; MP3 192kbps; originals retained")
        while True:
            wait = store.wait_until() - time.time()
            if wait > 0:
                await asyncio.sleep(min(wait, 30))
                continue
            try:
                if time.time() - scanned >= 60:
                    await asyncio.to_thread(discover_queue)
                    scanned = time.time()
                job = store.pending()
                if job:
                    store.update(job["name"], "preparing")
                    try:
                        await prepare(job)
                    except errors.FloodWaitError as error:
                        store.wait_until(time.time() + error.seconds + 1)
                        store.update(job["name"], "failed", "Telegram flood wait", error.seconds + 1)
                        logger.warning("Playback preparation flood wait %s seconds", error.seconds)
                    except Exception as error:
                        store.update(job["name"], "failed", type(error).__name__, 60)
                        logger.error("Playback preparation message=%s failed: %s; retry in 60s",
                                     job["message"], type(error).__name__)
                    else:
                        store.update(job["name"], "ready")
                await asyncio.sleep(3 if job else 10)
            except Exception:
                logger.exception("Playback worker unavailable; retry in 30s")
                await asyncio.sleep(30)
    finally:
        store.close()


def discover_queue():
    store = PlaybackStore()
    try:
        discover(store)
    finally:
        store.close()


def main():
    from channel_sync import single_instance, state_directory
    from db import get_conn
    from music import MUSIC_CHANNEL_ID
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track-id", type=int)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    store = PlaybackStore()
    lock = ExitStack()
    try:
        if args.track_id:
            conn = get_conn()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT file_name,message_id,file_size FROM music_tracks"
                        " WHERE id=%s AND channel_id=%s AND enabled=TRUE AND merged_into IS NULL",
                        (args.track_id, MUSIC_CHANNEL_ID))
                    row = cursor.fetchone()
                    if not row:
                        raise ValueError("Eligible owned-channel track not found")
                    store.enqueue(row["file_name"], row["message_id"], row["file_size"], True)
            finally:
                conn.close()
        if args.once:
            lock.enter_context(single_instance(state_directory(), "music-playback.lock"))
            job = store.pending()
            if not job:
                raise ValueError("No pending playback preparation")
            if store.wait_until() > time.time():
                raise ValueError("Telegram flood wait is still active")
            store.update(job["name"], "preparing")
            try:
                asyncio.run(prepare(job))
            except errors.FloodWaitError as error:
                store.wait_until(time.time() + error.seconds + 1)
                store.update(job["name"], "failed", "Telegram flood wait", error.seconds + 1)
                raise
            except Exception as error:
                store.update(job["name"], "failed", type(error).__name__, 60)
                raise
            store.update(job["name"], "ready")
        else:
            for row in store.connection.execute("SELECT status,COUNT(*) AS n FROM jobs GROUP BY status"):
                print(dict(row))
    finally:
        lock.close()
        store.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
