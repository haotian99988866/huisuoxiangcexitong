import hashlib
from io import BytesIO
from pathlib import Path
import os
import tempfile
import threading
import time

from PIL import Image, ImageOps


class ImageCache:
    def __init__(self, directory, max_bytes=256 * 1024 * 1024, ttl=7 * 86400):
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.ttl = ttl
        self.locks = [threading.Lock() for _ in range(32)]
        self.cleanup_lock = threading.Lock()

    def _read(self, path):
        try:
            if time.time() - path.stat().st_mtime < self.ttl:
                return path.read_bytes()
        except FileNotFoundError:
            pass
        return None

    def _write(self, path, data):
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(data)
            except OSError:
                handle.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        self._prune()

    def _prune(self):
        with self.cleanup_lock:
            entries = []
            for path in self.directory.glob("*.img"):
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                entries.append((stat.st_mtime, stat.st_size, path))
            total = sum(size for _, size, _ in entries)
            for modified, size, path in sorted(entries):
                if time.time() - modified >= self.ttl or total > self.max_bytes:
                    path.unlink(missing_ok=True)
                    total -= size

    @staticmethod
    def _content_type(data):
        with Image.open(BytesIO(data)) as image:
            content_type = Image.MIME.get(image.format)
            image.verify()
        if not content_type:
            raise ValueError("Unsupported image format")
        return content_type

    def get(self, file_id, loader, thumbnail=False):
        key = hashlib.sha256(file_id.encode("utf-8")).hexdigest()
        original = self.directory / f"{key}.img"
        preview = self.directory / f"{key}-480-v1.img"
        with self.locks[int(key[:8], 16) % len(self.locks)]:
            if thumbnail:
                data = self._read(preview)
                if data is not None:
                    return data, self._content_type(data)
            data = self._read(original)
            if data is None:
                data = loader()
                content_type = self._content_type(data)
                self._write(original, data)
            else:
                content_type = self._content_type(data)
            if not thumbnail:
                return data, content_type
            with Image.open(BytesIO(data)) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((480, 480), Image.Resampling.LANCZOS)
                output = BytesIO()
                image.convert("RGB").save(output, format="WEBP", quality=78)
            data = output.getvalue()
            self._write(preview, data)
            return data, "image/webp"
