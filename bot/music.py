"""Licensed channel audio, cached locally for in-app playback."""
import argparse
import json
import hashlib
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import uuid
import requests
import pymysql

from db import get_conn
from music_dedupe import ensure_schema, import_lock, save_track, song_key
from music_metadata import identify
from music_sources import external_forward

MAX_AUDIO_BYTES = 128 * 1024 * 1024
TELEGRAM_MAX_BYTES = 20 * 1024 * 1024
CHANNEL_CACHE_BYTES = 1024 * 1024 * 1024
MUSIC_CHANNEL_ID = "-1002368924969"
MUSIC_CHANNEL_USERNAME = "haotiandkhy99988866"
logger = logging.getLogger(__name__)
download_lock = threading.Lock()
MIME_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg",
              ".flac": "audio/flac"}
STORED_NAME = re.compile(r"[0-9a-f]{32}\.(?:mp3|m4a|wav|ogg|flac)")


def library_dir():
    return Path(os.getenv("MUSIC_LIBRARY_DIR") or
                Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "Huisuo" / "music").resolve()


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS music_tracks (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(120) NOT NULL,
                artist VARCHAR(120) NOT NULL,
                album VARCHAR(120) NOT NULL DEFAULT '',
                file_name VARCHAR(40) NOT NULL UNIQUE,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("SHOW COLUMNS FROM music_tracks")
            columns = {row["Field"] for row in cur.fetchall()}
            additions = {
                "channel_id": "VARCHAR(30) NULL",
                "message_id": "BIGINT NULL",
                "telegram_file_id": "VARCHAR(512) NULL",
                "file_size": "BIGINT NULL",
            }
            for name, definition in additions.items():
                if name not in columns:
                    cur.execute(f"ALTER TABLE music_tracks ADD COLUMN {name} {definition}")
            cur.execute("SHOW INDEX FROM music_tracks WHERE Key_name='idx_music_message'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE music_tracks ADD UNIQUE KEY idx_music_message(channel_id,message_id)")
        conn.commit()
        ensure_schema(conn)
    finally:
        conn.close()


def validate_audio(path, *, strict_suffix=True):
    suffix = path.suffix.lower()
    if suffix not in MIME_TYPES:
        raise ValueError("仅支持 MP3、M4A、WAV、OGG、FLAC 音频")
    if not path.is_file() or not 12 <= path.stat().st_size <= MAX_AUDIO_BYTES:
        raise ValueError("音频文件不存在、过小或超过 128 MB")
    with path.open("rb") as stream:
        header = stream.read(16)
    signatures = {
        ".mp3": header.startswith(b"ID3") or (header[0] == 0xFF and header[1] & 0xE0 == 0xE0),
        ".m4a": header[4:8] == b"ftyp",
        ".wav": header[:4] == b"RIFF" and header[8:12] == b"WAVE",
        ".ogg": header[:4] == b"OggS",
        ".flac": header[:4] == b"fLaC",
    }
    actual = next((extension for extension, matches in signatures.items() if matches), None)
    if actual is None or (strict_suffix and actual != suffix):
        raise ValueError("文件头与音频扩展名不符，请使用有效音频文件")
    return actual


def add_track(source, title, artist, album="", rights_confirmed=False):
    if not rights_confirmed:
        raise ValueError("请先确认拥有音频展示和播放授权，再使用 --rights-confirmed")
    for name, value in (("歌名", title), ("歌手", artist), ("专辑", album)):
        if not isinstance(value, str) or len(value) > 120 or (name != "专辑" and not value.strip()):
            raise ValueError(f"{name}需为不超过 120 字的有效文本")
    source = Path(source).resolve()
    suffix = validate_audio(source)
    directory = library_dir()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (uuid.uuid4().hex + suffix)
    pending = destination.with_suffix(".part")
    conn = None
    try:
        shutil.copyfile(source, pending)
        if pending.stat().st_size != source.stat().st_size:
            raise ValueError("音频复制不完整")
        pending.replace(destination)
        validate_audio(destination)
        conn = get_conn()
        with import_lock(conn):
            ident, used_file = save_track(conn, title.strip(), artist.strip(), album.strip(), destination.name)
            conn.commit()
        if not used_file:
            destination.unlink(missing_ok=True)
        return ident
    except Exception:
        if conn is not None:
            conn.rollback()
        destination.unlink(missing_ok=True)
        raise
    finally:
        pending.unlink(missing_ok=True)
        if conn is not None:
            conn.close()


def catalog():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,title,artist,album FROM music_tracks WHERE enabled=TRUE AND merged_into IS NULL ORDER BY id DESC")
            rows = list(cur.fetchall())
        return rows
    finally:
        conn.close()


def telegram_audio_name(media):
    filename = getattr(media, "file_name", None) or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in MIME_TYPES:
        suffix = {
            "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
            "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg",
            "audio/flac": ".flac", "audio/x-flac": ".flac",
        }.get(getattr(media, "mime_type", None))
    if suffix is None:
        raise ValueError("频道歌曲仅支持 MP3、M4A、WAV、OGG、FLAC，建议 MP3/M4A")
    return suffix


def download_channel_audio(file_id, name, expected_size):
    if not isinstance(expected_size, int) or not 12 <= expected_size <= TELEGRAM_MAX_BYTES:
        raise ValueError("Telegram 歌曲需有有效文件大小且不超过 20 MB")
    if not STORED_NAME.fullmatch(name):
        raise ValueError("频道音乐缓存文件名无效")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("机器人 Token 尚未配置")
    destination = library_dir() / name
    with download_lock:
        if destination.is_file() and destination.stat().st_size == expected_size:
            validate_audio(destination)
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        used = sum(path.stat().st_size for path in destination.parent.iterdir()
                   if path.is_file() and path.suffix in MIME_TYPES)
        if used + expected_size > CHANNEL_CACHE_BYTES:
            raise ValueError("本机音乐缓存已达 1 GB，请管理员备份并清理不用的缓存后重试")
        temporary = None
        try:
            with requests.get(f"https://api.telegram.org/bot{token}/getFile",
                              params={"file_id": file_id}, timeout=(5, 20)) as info:
                if not info.ok:
                    raise ValueError(f"Telegram getFile 失败（HTTP {info.status_code}），请检查机器人权限和音频大小")
                payload = info.json()
            result = payload.get("result") or {}
            remote = result.get("file_path")
            if not payload.get("ok") or not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", remote) or ".." in remote:
                raise ValueError("Telegram 未提供有效音频下载路径")
            if result.get("file_size") != expected_size:
                raise ValueError("Telegram 音频大小与消息不一致，请重新发布歌曲")
            with requests.get(f"https://api.telegram.org/file/bot{token}/{remote}",
                              stream=True, timeout=(5, 60)) as response:
                if not response.ok:
                    raise ValueError(f"Telegram 音频下载失败（HTTP {response.status_code}）")
                with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=destination.suffix, delete=False) as output:
                    temporary = Path(output.name)
                    size = 0
                    for chunk in response.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > expected_size:
                            raise ValueError("Telegram 音频超过声明大小")
                        output.write(chunk)
                if size != expected_size:
                    raise ValueError("Telegram 音频下载不完整")
            validate_audio(temporary)
            temporary.replace(destination)
            return destination
        except requests.RequestException:
            # Request exceptions include token-bearing URLs; never propagate them into logs.
            raise ValueError("Telegram 音频请求失败，请稍后编辑歌曲消息以重试") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def download_owned_cached_audio(message_id, name, expected_size):
    """Bounded cache for collector copies; Telegram remains the durable store."""
    from music_mtproto import download_owned_audio
    if not isinstance(expected_size, int) or not 12 <= expected_size <= MAX_AUDIO_BYTES:
        raise ValueError("音乐大小无效或超过 128 MB")
    if not isinstance(message_id, int) or message_id <= 0 or not STORED_NAME.fullmatch(name):
        raise ValueError("自有频道音乐配置无效")
    destination = library_dir() / name
    with download_lock:
        if destination.is_file() and destination.stat().st_size == expected_size:
            validate_audio(destination, strict_suffix=False)
            destination.touch()
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        used = sum(path.stat().st_size for path in destination.parent.iterdir()
                   if path.is_file() and path.suffix in MIME_TYPES)
        if used + expected_size > CHANNEL_CACHE_BYTES:
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT file_name FROM music_tracks WHERE telegram_file_id IS NOT NULL"
                                " AND channel_id=%s", (MUSIC_CHANNEL_ID,))
                    recoverable = {row["file_name"] for row in cur.fetchall()}
            finally:
                conn.close()
            candidates = sorted((path for path in destination.parent.iterdir()
                                 if path.is_file() and path.name in recoverable and STORED_NAME.fullmatch(path.name)),
                                key=lambda path: path.stat().st_mtime)
            for path in candidates:
                if used + expected_size <= CHANNEL_CACHE_BYTES:
                    break
                size = path.stat().st_size
                try:
                    path.unlink()
                except PermissionError:
                    logger.warning("Music cache file in use; cannot evict %s", path.name)
                    continue
                used -= size
            if used + expected_size > CHANNEL_CACHE_BYTES:
                raise ValueError("音乐缓存空间不足，现有文件正在使用或不能安全淘汰")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=destination.suffix, delete=False) as stream:
                temporary = Path(stream.name)
            download_owned_audio(message_id, temporary, expected_size)
            if temporary.stat().st_size != expected_size:
                raise ValueError("自有频道音频下载不完整")
            actual = validate_audio(temporary, strict_suffix=False)
            if actual != destination.suffix:
                logger.warning("Owned audio %s declares %s but contains %s; serving actual audio type",
                               message_id, destination.suffix, actual)
            temporary.replace(destination)
            return destination
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def save_channel_message(message):
    if str(message.chat.id) != MUSIC_CHANNEL_ID:
        return False
    media = getattr(message, "audio", None) or getattr(message, "document", None)
    if media is None:
        return False
    if external_forward(message):
        logger.info("External music copy %s will be indexed by the persistent collector", message.message_id)
        return True
    suffix = telegram_audio_name(media)
    file_id = media.file_id
    size = getattr(media, "file_size", None)
    name = hashlib.sha256(f"{file_id}:{message.message_id}".encode()).hexdigest()[:32] + suffix
    title, artist = identify(getattr(media, "title", None), getattr(media, "performer", None),
                             getattr(media, "file_name", None), getattr(message, "caption", None))
    conn = get_conn()
    try:
        with import_lock(conn):
            ident, used_file = save_track(
                conn, title or "未命名歌曲", artist or "未知歌手", "", name,
                download=lambda: download_channel_audio(file_id, name, size),
                channel_id=MUSIC_CHANNEL_ID, message_id=message.message_id, file_id=file_id, size=size,
            )
            conn.commit()
        if not used_file:
            logger.info("Music channel message %s reused track %s", message.message_id, ident)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return True


def handle_channel_message(bot, message, admins):
    if str(message.chat.id) != MUSIC_CHANNEL_ID:
        return False
    if not (getattr(message, "audio", None) or getattr(message, "document", None)):
        return False
    try:
        save_channel_message(message)
        logger.info("Music channel message %s imported and cached", message.message_id)
    except (ValueError, OSError, pymysql.MySQLError) as exc:
        logger.error("Music channel message %s import failed: %s", message.message_id, type(exc).__name__)
        detail = str(exc) if isinstance(exc, ValueError) else "数据库或本机文件操作失败，请检查服务日志和磁盘"
        for admin_id in admins:
            try:
                bot.send_message(admin_id, f"音乐消息 {message.message_id} 录入失败：{detail}。修复后请编辑或重新发布该消息。")
            except Exception as notification_error:
                logger.error("Music import notification failed: %s", type(notification_error).__name__)
    return True


def refresh_channel_metadata(message):
    """Repair labels without replacing audio, changing IDs or merging user references."""
    if str(message.chat.id) != MUSIC_CHANNEL_ID:
        raise ValueError("只能修正已配置音乐频道的歌曲")
    media = getattr(message, "audio", None) or getattr(message, "document", None)
    if media is None:
        raise ValueError("原消息没有可用音频")
    title, artist = identify(getattr(media, "title", None), getattr(media, "performer", None),
                             getattr(media, "file_name", None), getattr(message, "caption", None))
    key = song_key(title, artist)
    conn = get_conn()
    try:
        with import_lock(conn), conn.cursor() as cur:
            cur.execute("SELECT id,title,artist FROM music_tracks WHERE channel_id=%s AND message_id=%s"
                        " AND merged_into IS NULL FOR UPDATE", (MUSIC_CHANNEL_ID, message.message_id))
            row = cur.fetchone()
            if not row:
                raise ValueError("原歌曲不存在或已合并，请人工核对")
            if (row["title"], row["artist"]) == (title, artist):
                return False
            cur.execute("SELECT id FROM music_tracks WHERE dedupe_key=%s AND id<>%s", (key, row["id"]))
            if cur.fetchone():
                raise ValueError("修正后与其他歌曲同名同歌手，请人工核对版本，不自动合并")
            cur.execute("UPDATE music_tracks SET title=%s,artist=%s,dedupe_key=%s WHERE id=%s",
                        (title, artist, key, row["id"]))
        conn.commit()
        logger.info("Music track %s metadata refreshed; audio and references unchanged", row["id"])
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def commit_stream_cache(temporary, name, expected_size):
    """Only complete verified streams become cache hits; never hold the lock over network I/O."""
    if not STORED_NAME.fullmatch(name) or temporary.stat().st_size != expected_size:
        raise ValueError("音乐流缓存不完整或名称无效")
    validate_audio(temporary, strict_suffix=False)
    destination = library_dir() / name
    with download_lock:
        if destination.is_file():
            return
        used = sum(path.stat().st_size for path in destination.parent.iterdir()
                   if path.is_file() and path.name != temporary.name and STORED_NAME.fullmatch(path.name))
        if used + expected_size > CHANNEL_CACHE_BYTES:
            logger.warning("Music stream cache budget reached; completed stream not retained")
            return
        temporary.replace(destination)


def audio_file(track_id, *, stream=False):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT target.file_name,target.telegram_file_id,target.file_size,"
                        "target.channel_id,target.message_id FROM music_tracks source "
                        "JOIN music_tracks target ON target.id=COALESCE(source.merged_into,source.id) "
                        "WHERE source.id=%s AND target.enabled=TRUE", (track_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    name = row["file_name"]
    if not isinstance(name, str) or not STORED_NAME.fullmatch(name):
        raise ValueError("音乐文件配置无效")
    path = library_dir() / name
    if path.resolve().parent != library_dir():
        raise ValueError("音乐文件位置无效")
    if (stream and row.get("channel_id") == MUSIC_CHANNEL_ID and row.get("message_id")
            and (path.suffix in {".flac", ".wav", ".ogg"} or (row.get("file_size") or 0) > 12 * 1024 * 1024)):
        from music_playback import prepared_audio
        prepared = prepared_audio(name, row["message_id"], row["file_size"])
        if prepared:
            return prepared, "audio/mpeg"
    if not path.is_file() and row.get("telegram_file_id"):
        if row.get("channel_id") == MUSIC_CHANNEL_ID and row.get("message_id"):
            if stream:
                from music_stream import RemoteAudio
                size = row["file_size"]
                if (not isinstance(size, int) or isinstance(size, bool)
                        or not 12 <= size <= MAX_AUDIO_BYTES
                        or not isinstance(row["message_id"], int) or row["message_id"] <= 0):
                    raise ValueError("自有频道音乐流配置无效")
                return RemoteAudio(row["message_id"], size, MIME_TYPES[path.suffix], name)
            path = download_owned_cached_audio(row["message_id"], name, row["file_size"])
        else:
            path = download_channel_audio(row["telegram_file_id"], name, row["file_size"])
    if not path.is_file():
        raise FileNotFoundError("本机音频文件缺失或位置无效")
    path.touch()
    return path, MIME_TYPES[validate_audio(path, strict_suffix=False)]


def set_enabled(track_id, enabled):
    conn = get_conn()
    try:
        with import_lock(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(merged_into,id) AS id FROM music_tracks WHERE id=%s FOR UPDATE", (track_id,))
                row = cur.fetchone()
                if row is None:
                    raise ValueError("歌曲不存在")
                cur.execute("UPDATE music_tracks SET enabled=%s WHERE id=%s", (enabled, row["id"]))
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="本机授权音乐管理")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("list")
    add = commands.add_parser("add")
    add.add_argument("file")
    add.add_argument("--title", required=True)
    add.add_argument("--artist", required=True)
    add.add_argument("--album", default="")
    add.add_argument("--rights-confirmed", action="store_true")
    for command in ("hide", "show"):
        commands.add_parser(command).add_argument("id", type=int)
    args = parser.parse_args()
    try:
        if args.command == "init":
            ensure_tables()
            print("音乐表及歌曲去重索引已初始化")
        elif args.command == "list":
            print(json.dumps(catalog(), ensure_ascii=False, indent=2))
        elif args.command == "add":
            ident = add_track(args.file, args.title, args.artist, args.album, args.rights_confirmed)
            print(f"已录入歌曲 ID {ident}，原文件不改动")
        else:
            set_enabled(args.id, args.command == "show")
            print("歌曲已上架" if args.command == "show" else "歌曲已下架")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"音乐操作失败：{exc}\n")


if __name__ == "__main__":
    main()
