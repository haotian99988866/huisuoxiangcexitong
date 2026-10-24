"""图片指纹(dHash):同一张图片重新上传、被压缩后指纹仍然相近,用来判重。"""
import io
import logging

from PIL import Image, ImageOps, UnidentifiedImageError

from db import get_conn

logger = logging.getLogger(__name__)

HASH_SIZE = 8
SAME_IMAGE_DISTANCE = 4  # 64 位指纹里最多相差几位仍算同一张图
HASH_TABLES = ("technician_photos", "delivery_media")


def compute_hash(image_bytes):
    """返回 16 位十六进制指纹;无法解析的图片返回 None。"""
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("L")
            image = image.resize((HASH_SIZE + 1, HASH_SIZE), Image.LANCZOS)
            pixels = list(image.getdata())
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    bits = 0
    for row in range(HASH_SIZE):
        for col in range(HASH_SIZE):
            left = pixels[row * (HASH_SIZE + 1) + col]
            right = pixels[row * (HASH_SIZE + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def distance(first, second):
    return bin(int(first, 16) ^ int(second, 16)).count("1")


class ImageDeduper:
    """按顺序传入图片,返回它归属的“首张相同图片”的键;没有指纹的图片各自独立。"""

    def __init__(self, threshold=SAME_IMAGE_DISTANCE):
        self.threshold = threshold
        self._seen = []

    def key_for(self, image_hash, fallback_key):
        if not image_hash:
            return fallback_key
        for known_hash, known_key in self._seen:
            if distance(image_hash, known_hash) <= self.threshold:
                return known_key
        self._seen.append((image_hash, fallback_key))
        return fallback_key


def ensure_columns():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for table in HASH_TABLES:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s "
                    "AND column_name = 'image_hash'",
                    (table,),
                )
                if not cur.fetchone()["n"]:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN image_hash CHAR(16) NULL")
        conn.commit()
    finally:
        conn.close()
