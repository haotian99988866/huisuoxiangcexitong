"""Stable title/artist identities and reference-preserving track merges."""
from contextlib import contextmanager
import hashlib
import json
import unicodedata


def song_key(title, artist):
    def normalize(value):
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    normalized_artist = normalize(artist)
    if normalized_artist in ("", "<unknown>", "unknown", "未知歌手"):
        normalized_artist = "未知歌手"
    return hashlib.sha256(json.dumps([normalize(title), normalized_artist],
                                    ensure_ascii=False).encode()).hexdigest()


@contextmanager
def import_lock(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(CONCAT(DATABASE(), ':music-import'), 90) AS acquired")
        if cur.fetchone()["acquired"] != 1:
            raise ValueError("音乐录入繁忙，请稍后编辑或重新发布歌曲")
    try:
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(CONCAT(DATABASE(), ':music-import'))")


def remap_references(cur, mapping):
    if not mapping:
        return
    def remap(values):
        return list(dict.fromkeys(mapping.get(value, value) for value in values))
    cur.execute("SHOW TABLES LIKE 'music_profiles'")
    if cur.fetchone():
        cur.execute("SELECT user_id,document FROM music_profiles ORDER BY user_id FOR UPDATE")
        for row in cur.fetchall():
            profile = json.loads(row["document"])
            before = json.dumps(profile)
            for key in ("favorites", "recent"):
                profile[key] = remap(profile[key])
            resume = profile.get("resume")
            if resume and resume["id"] in mapping:
                resume["id"] = mapping[resume["id"]]
                resume["position"] = 0
            if before != json.dumps(profile):
                cur.execute("UPDATE music_profiles SET document=%s WHERE user_id=%s",
                            (json.dumps(profile), row["user_id"]))
    cur.execute("SHOW TABLES LIKE 'music_playlists'")
    if cur.fetchone():
        cur.execute("SELECT id,tracks FROM music_playlists ORDER BY id FOR UPDATE")
        for row in cur.fetchall():
            original = json.loads(row["tracks"])
            values = remap(original)
            if values != original:
                cur.execute("UPDATE music_playlists SET tracks=%s,revision=revision+1 WHERE id=%s",
                            (json.dumps(values), row["id"]))


def merge(cur, source, target):
    cur.execute("SELECT id FROM music_tracks WHERE merged_into=%s", (source,))
    mapping = {row["id"]: target for row in cur.fetchall()}
    mapping[source] = target
    remap_references(cur, mapping)
    cur.execute("UPDATE music_tracks SET merged_into=%s,dedupe_key=NULL WHERE id=%s OR merged_into=%s",
                (target, source, source))


def ensure_schema(conn):
    with import_lock(conn):
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM music_tracks")
            columns = {row["Field"] for row in cur.fetchall()}
            for name, definition in (("dedupe_key", "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL"),
                                     ("merged_into", "BIGINT NULL")):
                if name not in columns:
                    cur.execute(f"ALTER TABLE music_tracks ADD COLUMN {name} {definition}")
            cur.execute("SELECT id,title,artist FROM music_tracks WHERE merged_into IS NULL ORDER BY id FOR UPDATE")
            seen = {}
            for row in cur.fetchall():
                key = song_key(row["title"], row["artist"])
                if key in seen:
                    merge(cur, row["id"], seen[key])
                else:
                    seen[key] = row["id"]
                    cur.execute("UPDATE music_tracks SET dedupe_key=%s WHERE id=%s", (key, row["id"]))
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SHOW INDEX FROM music_tracks WHERE Key_name='idx_music_identity'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE music_tracks ADD UNIQUE KEY idx_music_identity(dedupe_key)")
        conn.commit()


def save_track(conn, title, artist, album, filename, download=None,
               channel_id=None, message_id=None, file_id=None, size=None):
    """Caller holds import_lock; download runs only for a new or edited source."""
    key = song_key(title, artist)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM music_tracks WHERE dedupe_key=%s", (key,))
        target = cur.fetchone()
        source = None
        if channel_id is not None:
            cur.execute("SELECT id,channel_id,message_id FROM music_tracks WHERE channel_id=%s AND message_id=%s",
                        (channel_id, message_id))
            source = cur.fetchone()
        if target and (not source or target["id"] != source["id"]):
            if source:
                merge(cur, source["id"], target["id"])
            ident = target["id"]
            used_file = False
        else:
            if download:
                download()
            if source:
                ident = source["id"]
                cur.execute("UPDATE music_tracks SET title=%s,artist=%s,dedupe_key=%s,merged_into=NULL,"
                            "file_name=%s,telegram_file_id=%s,file_size=%s WHERE id=%s",
                            (title, artist, key, filename, file_id, size, ident))
                used_file = True
            else:
                cur.execute("INSERT INTO music_tracks(title,artist,album,file_name,dedupe_key,channel_id,"
                            "message_id,telegram_file_id,file_size) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (title, artist, album, filename, key, channel_id, message_id, file_id, size))
                ident = cur.lastrowid
                used_file = True
        return ident, used_file
