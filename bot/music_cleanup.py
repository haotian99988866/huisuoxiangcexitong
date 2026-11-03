"""Resumable deletion of explicitly isolated owned-channel music."""
import asyncio
import json
import sqlite3
from pathlib import Path

from db import get_conn


class ExcludedMusic(ValueError):
    pass


def require_not_excluded(channel_id, message_id):
    from channel_sync import state_directory
    from music import MUSIC_CHANNEL_ID
    if str(channel_id) != MUSIC_CHANNEL_ID or message_id is None:
        return
    for filename in ("music-playback.sqlite3", "music-import.sqlite3"):
        path = state_directory() / filename
        if not path.is_file():
            continue
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='operation_music_exclusions'").fetchone():
                if conn.execute("SELECT 1 FROM operation_music_exclusions WHERE message=?", (message_id,)).fetchone():
                    raise ExcludedMusic("疑问音乐已按负责人规则删除，不再收录")
        finally:
            conn.close()


def require_file_not_excluded(name):
    from channel_sync import state_directory
    for filename in ("music-playback.sqlite3", "music-import.sqlite3"):
        path = state_directory() / filename
        if not path.is_file():
            continue
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='operation_music_cleanups'").fetchone():
                if conn.execute(
                    "SELECT 1 FROM operation_music_cleanups,json_each(plan,'$.names') names WHERE names.value=? LIMIT 1",
                    (name,),
                ).fetchone():
                    raise ExcludedMusic("疑问音乐缓存已清理，不再保存")
        finally:
            conn.close()


async def channel_step(plan):
    from channel_sync import credentials, state_directory
    from music import MUSIC_CHANNEL_ID, MUSIC_CHANNEL_USERNAME
    from telethon import TelegramClient
    from telethon.crypto import AuthKey
    from telethon.sessions import MemorySession

    conn = sqlite3.connect((state_directory() / "user.session").as_uri() + "?mode=ro", uri=True)
    try:
        saved = conn.execute("SELECT dc_id,server_address,port,auth_key FROM sessions").fetchone()
    finally:
        conn.close()
    if not saved:
        raise ValueError("音乐清理账户尚未登录")
    session = MemorySession()
    session.set_dc(*saved[:3])
    session.auth_key = AuthKey(saved[3])
    client = TelegramClient(session, *credentials(), receive_updates=False, flood_sleep_threshold=0)
    try:
        await client.connect()
        if not await client.is_user_authorized() or (await client.get_me()).bot:
            raise ValueError("音乐清理需要已授权的来源读取账户")
        target = await client.get_entity("https://t.me/" + MUSIC_CHANNEL_USERNAME)
        if str(target.id) != MUSIC_CHANNEL_ID.removeprefix("-100"):
            raise ValueError("音乐清理目标频道身份不匹配")
        rights = await client.get_permissions(target, await client.get_me())
        if not rights.is_admin or not rights.delete_messages:
            raise ValueError("音乐清理账户缺少自有频道删除权限")
        if not plan["located"]:
            batch = await client.get_messages(target, limit=100, offset_id=plan["offset"])
            for message in batch:
                document = getattr(message, "document", None)
                if document and document.id == plan["document_id"]:
                    if message.id not in plan["messages"]:
                        plan["messages"].append(message.id)
            if batch:
                plan["offset"] = min(message.id for message in batch)
            plan["located"] = len(batch) < 100
            return plan
        if plan["phase"] != "channel":
            return plan
        for ident in plan["messages"]:
            messages = await client.get_messages(target, ids=[ident])
            message = messages[0] if messages else None
            if message is None:
                continue
            document = getattr(message, "document", None)
            if not document:
                raise ValueError("待删除的自有频道消息不再是音频文件，停止清理")
            if plan["document_id"] and document.id != plan["document_id"]:
                raise ValueError("待删除的自有频道文档身份已变化，停止清理")
            if plan["expected_size"] and document.size != plan["expected_size"]:
                raise ValueError("待删除的自有频道音频大小已变化，停止清理")
            await client.delete_messages(target, [ident])
            remaining = await client.get_messages(target, ids=[ident])
            if remaining and remaining[0]:
                raise RuntimeError("自有频道音频删除结果未确认")
        plan["phase"] = "files"
        return plan
    finally:
        await client.disconnect()


def initial_plan(module, row):
    message = row["message"] if module.key == "p" else row.get("result_id")
    if not message and not row.get("document_id"):
        if row["status"] == "uncertain":
            raise ValueError("发送结果不明且无文档标识，无法安全确定自有副本")
        return {"messages": [], "located": True, "offset": 0, "document_id": None,
                "expected_size": None, "names": [], "tracks": [], "phase": "plan"}
    return {
        "messages": [message] if message else [], "located": bool(message),
        "offset": 0, "document_id": row.get("document_id"),
        "expected_size": row.get("size"), "names": [row["name"]] if module.key == "p" else [],
        "tracks": [], "phase": "plan",
    }


def plan_tracks(plan):
    from music import MUSIC_CHANNEL_ID, STORED_NAME
    from music_dedupe import import_lock
    conn = get_conn()
    try:
        with import_lock(conn):
            with conn.cursor() as cur:
                tracks = []
                if plan["messages"]:
                    marks = ",".join(["%s"] * len(plan["messages"]))
                    cur.execute("SELECT id,file_name,channel_id,message_id,file_size FROM music_tracks "
                                f"WHERE channel_id=%s AND message_id IN ({marks}) FOR UPDATE",
                                (MUSIC_CHANNEL_ID, *plan["messages"]))
                    tracks = cur.fetchall()
                for track in tracks:
                    if not STORED_NAME.fullmatch(track["file_name"]):
                        raise ValueError("音乐缓存文件名不合法，停止清理")
                    if plan["names"] and track["file_name"] not in plan["names"]:
                        raise ValueError("疑问任务与网站音乐文件身份不匹配")
                names = sorted(set(plan["names"]) | {track["file_name"] for track in tracks})
                if any(not STORED_NAME.fullmatch(name) for name in names):
                    raise ValueError("疑问任务文件名不合法")
                plan = {**plan, "names": names, "tracks": tracks, "phase": "disable"}
            conn.commit()
        return plan
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def check_tracks(cur, plan):
    for track in plan["tracks"]:
        cur.execute("SELECT id,file_name,channel_id,message_id FROM music_tracks WHERE id=%s FOR UPDATE",
                    (track["id"],))
        current = cur.fetchone()
        if current and any(current[key] != track[key] for key in current):
            raise ValueError("音乐记录身份已变化，停止清理")


def disable_tracks(plan):
    from music_dedupe import import_lock
    conn = get_conn()
    try:
        with import_lock(conn):
            with conn.cursor() as cur:
                check_tracks(cur, plan)
                for track in plan["tracks"]:
                    cur.execute("UPDATE music_tracks SET enabled=FALSE WHERE id=%s", (track["id"],))
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_files(plan):
    from music import STORED_NAME, library_dir, download_lock
    root = library_dir()
    with download_lock:
        for name in plan["names"]:
            if not STORED_NAME.fullmatch(name):
                raise ValueError("音乐清理文件名无效")
            for relative in (
                Path(name), Path("playback") / (name + ".download"),
                Path("playback") / (Path(name).stem + ".mp3"),
                Path("playback") / (Path(name).stem + ".encoding.mp3"),
            ):
                path = root / relative
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("音乐清理路径越界，停止删除")
                path.unlink(missing_ok=True)


def remove_references(cur, removed):
    if not removed:
        return
    cur.execute("SHOW TABLES LIKE 'music_profiles'")
    if cur.fetchone():
        cur.execute("SELECT user_id,document FROM music_profiles ORDER BY user_id FOR UPDATE")
        for row in cur.fetchall():
            document = json.loads(row["document"])
            before = json.dumps(document)
            for key in ("favorites", "recent"):
                document[key] = [ident for ident in document[key] if ident not in removed]
            if document.get("resume") and document["resume"]["id"] in removed:
                document["resume"] = None
            if json.dumps(document) != before:
                cur.execute("UPDATE music_profiles SET document=%s WHERE user_id=%s",
                            (json.dumps(document), row["user_id"]))
    cur.execute("SHOW TABLES LIKE 'music_playlists'")
    if cur.fetchone():
        cur.execute("SELECT id,tracks FROM music_playlists ORDER BY id FOR UPDATE")
        for row in cur.fetchall():
            tracks = json.loads(row["tracks"])
            kept = [ident for ident in tracks if ident not in removed]
            if kept != tracks:
                cur.execute("UPDATE music_playlists SET tracks=%s,revision=revision+1 WHERE id=%s",
                            (json.dumps(kept), row["id"]))


def delete_tracks(plan):
    from music_dedupe import import_lock, remap_references
    conn = get_conn()
    try:
        with import_lock(conn):
            with conn.cursor() as cur:
                check_tracks(cur, plan)
                removed = {track["id"] for track in plan["tracks"]}
                for track in plan["tracks"]:
                    cur.execute("SELECT id,dedupe_key,merged_into FROM music_tracks WHERE id=%s FOR UPDATE",
                                (track["id"],))
                    current = cur.fetchone()
                    if not current or current["merged_into"] is not None:
                        continue
                    cur.execute("SELECT id FROM music_tracks WHERE merged_into=%s ORDER BY enabled DESC,id FOR UPDATE",
                                (track["id"],))
                    aliases = [row["id"] for row in cur.fetchall() if row["id"] not in removed]
                    if aliases:
                        replacement = aliases[0]
                        cur.execute("UPDATE music_tracks SET dedupe_key=NULL WHERE id=%s", (track["id"],))
                        cur.execute("UPDATE music_tracks SET merged_into=NULL,dedupe_key=%s WHERE id=%s",
                                    (current["dedupe_key"], replacement))
                        cur.execute("UPDATE music_tracks SET merged_into=%s WHERE merged_into=%s AND id!=%s",
                                    (replacement, track["id"], replacement))
                        remap_references(cur, {track["id"]: replacement})
                remove_references(cur, removed)
                for ident in sorted(removed):
                    cur.execute("DELETE FROM music_tracks WHERE id=%s", (ident,))
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def advance(plan):
    if not plan["located"] or plan["phase"] == "channel":
        return asyncio.run(asyncio.wait_for(channel_step(plan), timeout=60))
    if plan["phase"] == "plan":
        return plan_tracks(plan)
    if plan["phase"] == "disable":
        disable_tracks(plan)
        return {**plan, "phase": "channel"}
    if plan["phase"] == "files":
        delete_files(plan)
        return {**plan, "phase": "database"}
    if plan["phase"] == "database":
        delete_tracks(plan)
        return {**plan, "phase": "done"}
    if plan["phase"] != "done":
        raise ValueError("未知音乐清理阶段")
    return plan
