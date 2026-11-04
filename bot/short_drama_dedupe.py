"""Compilation identity and one-card selection for short-drama media groups."""
import hashlib
import json
import re
import unicodedata

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS short_drama_versions (
    message_id BIGINT PRIMARY KEY,
    compilation_key CHAR(64) NOT NULL,
    file_size BIGINT NOT NULL,
    deleted TINYINT(1) NOT NULL DEFAULT 0,
    INDEX idx_drama_compilation (compilation_key, deleted, file_size)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

VISIBLE_SQL = """
EXISTS (
    SELECT 1 FROM short_drama_versions v
    WHERE v.message_id=movie_videos.message_id AND v.deleted=0
      AND NOT EXISTS (
          SELECT 1 FROM short_drama_versions newer
          WHERE newer.compilation_key=v.compilation_key AND newer.deleted=0
            AND newer.message_id>v.message_id
      )
)
"""


def compilation_key(caption):
    from movies import parse_caption
    parsed = parse_caption(caption)
    title = unicodedata.normalize("NFKC", parsed["title"] if parsed else "")
    match = re.search(r"《([^》]+)》", title)
    if not match:
        raise ValueError("Short drama needs a bracketed title for duplicate selection")
    # Keep season/episode ranges and other version qualifiers; never merge by series alone.
    parts = [match[1], title[match.end():]]
    identity = ["".join(char for char in part.casefold() if char.isalnum() or char in "-~～至到")
                for part in parts]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()


def register(cursor, message_id, caption, file_size):
    if type(file_size) is not int or file_size <= 0:
        raise ValueError("Short drama file size is unavailable; cannot register album segment")
    key = compilation_key(caption)
    cursor.execute(
        "INSERT INTO short_drama_versions(message_id,compilation_key,file_size) VALUES (%s,%s,%s)"
        " ON DUPLICATE KEY UPDATE compilation_key=VALUES(compilation_key),file_size=VALUES(file_size)",
        (message_id, key, file_size),
    )
    cursor.execute("SELECT deleted FROM short_drama_versions WHERE message_id=%s", (message_id,))
    return not cursor.fetchone()["deleted"]
