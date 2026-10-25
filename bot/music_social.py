"""Telegram-owned music preferences and link-readable playlists."""
import json
import math
import re
import uuid

from db import get_conn

MODES = ("sequential", "shuffle", "single")


class PlaylistConflict(ValueError):
    pass


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS music_profiles (
                user_id BIGINT PRIMARY KEY, document JSON NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS music_playlists (
                id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL,
                name VARCHAR(80) NOT NULL, tracks JSON NOT NULL,
                share_token CHAR(32) NOT NULL UNIQUE, revision INT NOT NULL DEFAULT 1,
                INDEX idx_music_playlist_owner(user_id,id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
    finally:
        conn.close()


def positive_id(value):
    if type(value) is not int or not 0 < value <= 9007199254740991:
        raise ValueError("歌曲或歌单编号无效")
    return value


def playlist_name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80:
        raise ValueError("歌单名称需为 1–80 个字")
    return value.strip()


def track_ids(cur, values):
    if not isinstance(values, list) or len(values) > 200:
        raise ValueError("每个歌单最多 200 首歌曲")
    ids = list(dict.fromkeys(positive_id(value) for value in values))
    if ids:
        cur.execute("SELECT source.id AS source_id,target.id FROM music_tracks source "
                    "JOIN music_tracks target ON target.id=COALESCE(source.merged_into,source.id) "
                    "WHERE target.enabled=TRUE AND source.id IN (" + ",".join(["%s"] * len(ids)) + ")", ids)
        mapping = {row.get("source_id", row["id"]): row["id"] for row in cur.fetchall()}
        if set(mapping) != set(ids):
            raise ValueError("部分歌曲已下架，请刷新歌库后重试")
        ids = list(dict.fromkeys(mapping[ident] for ident in ids))
    return ids


def resolve_track(cur, ident):
    cur.execute("SELECT target.id FROM music_tracks source "
                "JOIN music_tracks target ON target.id=COALESCE(source.merged_into,source.id) "
                "WHERE source.id=%s AND target.enabled=TRUE", (ident,))
    row = cur.fetchone()
    if not row:
        raise ValueError("歌曲不存在或已下架")
    return row["id"]


def playlist_view(row):
    return {key: (json.loads(row[key]) if key == "tracks" else row[key])
            for key in ("id", "name", "tracks", "share_token", "revision")}


def shared_playlist(token):
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("歌单分享链接无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,name,tracks,share_token,revision FROM music_playlists WHERE share_token=%s", (token,))
            row = cur.fetchone()
            if not row:
                raise ValueError("歌单不存在或已删除")
            return {"playlist": playlist_view(row)}
    finally:
        conn.close()


def account(user_id, action, data):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            initial = {"favorites": [], "recent": [], "resume": None, "mode": "sequential"}
            # Duplicate-key updates take an exclusive lock; INSERT IGNORE can deadlock on lock upgrade.
            cur.execute("INSERT INTO music_profiles(user_id,document) VALUES(%s,%s) "
                        "ON DUPLICATE KEY UPDATE user_id=VALUES(user_id)",
                        (user_id, json.dumps(initial)))
            cur.execute("SELECT document FROM music_profiles WHERE user_id=%s FOR UPDATE", (user_id,))
            profile = json.loads(cur.fetchone()["document"])
            if action == "music_update":
                operation = data.get("operation")
                if operation == "mode":
                    if data.get("mode") not in MODES:
                        raise ValueError("播放模式无效")
                    profile["mode"] = data["mode"]
                elif operation == "resume":
                    resume = data.get("resume")
                    if resume is not None:
                        ident = positive_id(resume.get("id")) if isinstance(resume, dict) else positive_id(None)
                        position = resume.get("position")
                        if type(position) not in (int, float) or not math.isfinite(position) or not 0 <= position <= 86400:
                            raise ValueError("播放进度无效")
                        canonical = resolve_track(cur, ident)
                        resume = {"id": canonical, "position": position if canonical == ident else 0}
                    profile["resume"] = resume
                elif operation in ("favorite", "recent"):
                    ident = positive_id(data.get("track_id"))
                    ident = resolve_track(cur, ident)
                    key = "favorites" if operation == "favorite" else "recent"
                    values = [value for value in profile[key] if value != ident]
                    if operation == "recent" or data.get("enabled") is True:
                        values.insert(0, ident)
                    elif type(data.get("enabled")) is not bool:
                        raise ValueError("请选择收藏或取消收藏")
                    if key == "favorites" and len(values) > 500:
                        raise ValueError("最多收藏 500 首歌曲")
                    profile[key] = values[:50] if key == "recent" else values
                else:
                    raise ValueError("音乐操作无效")
                cur.execute("UPDATE music_profiles SET document=%s WHERE user_id=%s",
                            (json.dumps(profile), user_id))
            elif action == "music_playlist_create":
                name = playlist_name(data.get("name"))
                ids = track_ids(cur, data.get("tracks", []))
                cur.execute("SELECT COUNT(*) AS count FROM music_playlists WHERE user_id=%s", (user_id,))
                if cur.fetchone()["count"] >= 50:
                    raise ValueError("每个账号最多创建 50 个歌单")
                cur.execute("INSERT INTO music_playlists(user_id,name,tracks,share_token) VALUES(%s,%s,%s,%s)",
                            (user_id, name, json.dumps(ids), uuid.uuid4().hex))
            elif action in ("music_playlist_save", "music_playlist_delete"):
                ident = positive_id(data.get("playlist_id"))
                cur.execute("SELECT id,revision FROM music_playlists WHERE id=%s AND user_id=%s FOR UPDATE",
                            (ident, user_id))
                row = cur.fetchone()
                if not row:
                    raise ValueError("歌单不存在或不属于你")
                if type(data.get("revision")) is not int or row["revision"] != data["revision"]:
                    raise PlaylistConflict("歌单已在其他设备修改，请刷新后重试")
                if action == "music_playlist_delete":
                    cur.execute("DELETE FROM music_playlists WHERE id=%s AND user_id=%s", (ident, user_id))
                else:
                    name = playlist_name(data.get("name"))
                    ids = track_ids(cur, data.get("tracks"))
                    cur.execute("UPDATE music_playlists SET name=%s,tracks=%s,revision=revision+1 WHERE id=%s AND user_id=%s",
                                (name, json.dumps(ids), ident, user_id))
            elif action != "music_profile":
                raise ValueError("音乐操作无效")
            cur.execute("SELECT id,name,tracks,share_token,revision FROM music_playlists WHERE user_id=%s ORDER BY id DESC", (user_id,))
            playlists = [playlist_view(row) for row in cur.fetchall()]
        conn.commit()
        return {"profile": profile, "playlists": playlists}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
