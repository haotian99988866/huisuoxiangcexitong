import hashlib
import logging
import os
from pathlib import Path
import tempfile
import threading


logger = logging.getLogger(__name__)


class VideoCache:
    def __init__(self, directory, max_bytes=512 * 1024 * 1024, max_file_bytes=32 * 1024 * 1024):
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.max_file_bytes = min(max_file_bytes, max_bytes)
        self.write_lock = threading.Lock()

    def get(self, file_id):
        key = hashlib.sha256(file_id.encode("utf-8")).hexdigest()
        for suffix in (".mp4", ".webm"):
            path = self.directory / f"{key}{suffix}"
            try:
                if path.stat().st_size > 0:
                    return path
            except FileNotFoundError:
                continue
        return None

    def _make_room(self, size):
        entries = []
        for suffix in ("*.mp4", "*.webm"):
            for path in self.directory.glob(suffix):
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                entries.append((stat.st_mtime, stat.st_size, path))
        total = sum(length for _, length, _ in entries)
        for _, length, path in sorted(entries):
            if total + size <= self.max_bytes:
                break
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                logger.warning("Cannot evict a video currently in use: %s", path.name)
                continue
            total -= length
        if total + size > self.max_bytes:
            raise OSError("Video cache has no space for another file")

    def store(self, file_id, chunks, suffix, expected_size):
        if suffix not in {".mp4", ".webm"}:
            raise ValueError("Unsupported video container")
        if expected_size <= 0 or expected_size > self.max_file_bytes:
            raise ValueError("Video exceeds cache file size limit or has no size")
        with self.write_lock:
            existing = self.get(file_id)
            if existing is not None:
                return existing
            self.directory.mkdir(parents=True, exist_ok=True)
            self._make_room(expected_size)
            key = hashlib.sha256(file_id.encode("utf-8")).hexdigest()
            destination = self.directory / f"{key}{suffix}"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as output:
                    temporary = Path(output.name)
                    size = 0
                    for chunk in chunks:
                        size += len(chunk)
                        if size > expected_size:
                            raise ValueError("Video download is larger than declared size")
                        output.write(chunk)
                    if size != expected_size:
                        raise ValueError("Incomplete video download")
                with temporary.open("rb") as video:
                    header = video.read(12)
                if (suffix == ".mp4" and header[4:8] != b"ftyp") or (
                    suffix == ".webm" and not header.startswith(b"\x1a\x45\xdf\xa3")
                ):
                    raise ValueError("Downloaded file is not the expected video container")
                os.replace(temporary, destination)
                return destination
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
