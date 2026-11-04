"""Prepare bounded, lossless fast-start copies without changing news originals."""
import hashlib
import logging
from pathlib import Path
import struct
import subprocess
import tempfile

import pymysql

from video_cache import VideoCache


logger = logging.getLogger(__name__)


def fast_start(path):
    size = path.stat().st_size
    with path.open("rb") as source:
        while source.tell() + 8 <= size:
            offset = source.tell()
            length, kind = struct.unpack(">I4s", source.read(8))
            header = 8
            if length == 1:
                extended = source.read(8)
                if len(extended) != 8:
                    raise ValueError("Incomplete MP4 atom")
                length = struct.unpack(">Q", extended)[0]
                header = 16
            elif length == 0:
                length = size - offset
            if length < header or offset + length > size:
                raise ValueError("Invalid MP4 atom length")
            if kind == b"moov":
                return True
            if kind == b"mdat":
                return False
            source.seek(offset + length)
    raise ValueError("MP4 has no playback metadata or media")


def cache_key(path):
    stat = path.stat()
    return hashlib.sha256(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()


def video_cache(directory):
    return VideoCache(directory / "video-faststart", max_file_bytes=200 * 1024 * 1024)


def playback_path(path):
    if path.suffix.lower() != ".mp4":
        return path
    return video_cache(path.parent).get(cache_key(path)) or path


def prepare(path):
    if path.suffix.lower() != ".mp4":
        return path
    cache = video_cache(path.parent)
    key = cache_key(path)
    existing = cache.get(key)
    if existing is not None:
        return existing
    if path.stat().st_size > cache.max_file_bytes:
        raise ValueError("News video exceeds the 200 MB preparation limit")
    if fast_start(path):
        return path
    import imageio_ffmpeg
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix="news-faststart-") as directory:
        output = Path(directory) / "prepared.mp4"
        result = subprocess.run(
            [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(path), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(output)],
            capture_output=True, timeout=120,
        )
        if result.returncode:
            raise RuntimeError("News video fast-start remux failed")
        if not fast_start(output):
            raise ValueError("Prepared news video metadata is not at the start")
        if cache_key(path) != key:
            raise RuntimeError("News source changed during video preparation")
        with output.open("rb") as source:
            return cache.store(key, iter(lambda: source.read(256 * 1024), b""),
                               ".mp4", output.stat().st_size)


def maintain(stop):
    import json
    from announcements import get_conn, local_media_path
    while not stop.is_set():
        conn = None
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT media FROM channel_announcements WHERE enabled=1 "
                            "ORDER BY published_at DESC,id DESC")
                rows = cur.fetchall()
        except pymysql.MySQLError:
            logger.exception("Cannot list news videos for playback preparation")
            rows = []
        finally:
            if conn is not None:
                conn.close()
        reserved = 0
        exhausted = False
        for row in rows:
            media = json.loads(row["media"]) if isinstance(row["media"], str) else row["media"]
            for item in media:
                if stop.is_set():
                    return
                if item["kind"] != "video":
                    continue
                try:
                    source = local_media_path(item["filename"])
                    if reserved + source.stat().st_size > 512 * 1024 * 1024:
                        exhausted = True
                        break
                    result = prepare(source)
                    if result != source:
                        reserved += result.stat().st_size
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, ImportError):
                    logger.exception("Cannot prepare news video %s; original remains available", item["filename"])
            if exhausted or reserved >= 512 * 1024 * 1024:
                break
        if stop.wait(60):
            return
