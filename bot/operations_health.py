"""Read-only operational status for local channel and media workers."""
from datetime import datetime, timezone
import logging
import sqlite3
import time

from db import get_conn
import pymysql
from news_sources import NEWS_SOURCES, announcement_job_key


logger = logging.getLogger(__name__)
HEARTBEAT_STALE_SECONDS = 120


def ensure_health_table(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS worker_health (
            worker TEXT PRIMARY KEY,
            heartbeat_at REAL NOT NULL,
            last_error_code TEXT,
            last_error_at REAL
        )
    """)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(worker_health)")}
    if "last_error_code" not in columns:
        connection.execute("ALTER TABLE worker_health ADD COLUMN last_error_code TEXT")
    if "last_error_at" not in columns:
        connection.execute("ALTER TABLE worker_health ADD COLUMN last_error_at REAL")
    connection.commit()


def record_worker_heartbeat(connection, worker, interval=30):
    try:
        now = time.time()
        row = connection.execute(
            "SELECT heartbeat_at FROM worker_health WHERE worker=?", (worker,)
        ).fetchone()
        if row and now - row["heartbeat_at"] < interval:
            return
        with connection:
            connection.execute(
                "INSERT INTO worker_health(worker,heartbeat_at) VALUES (?,?) "
                "ON CONFLICT(worker) DO UPDATE SET heartbeat_at=excluded.heartbeat_at",
                (worker, now),
            )
    except sqlite3.Error:
        logger.exception("Operational heartbeat unavailable: %s", worker)


def record_worker_failure(connection, worker, error):
    try:
        now = time.time()
        with connection:
            connection.execute(
                "INSERT INTO worker_health(worker,heartbeat_at,last_error_code,last_error_at) "
                "VALUES (?,?,?,?) ON CONFLICT(worker) DO UPDATE SET "
                "heartbeat_at=excluded.heartbeat_at,last_error_code=excluded.last_error_code,"
                "last_error_at=excluded.last_error_at",
                (worker, now, type(error).__name__, now),
            )
    except sqlite3.Error:
        logger.exception("Operational error status unavailable: %s", worker)


def _iso_timestamp(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _component(path, key, name, worker=None, enabled=True, include_queue=True, source=None):
    result = {
        "key": key,
        "name": name,
        "enabled": enabled,
        "state": "disabled" if not enabled else "not_initialized",
        "last_heartbeat": None,
        "heartbeat_age_seconds": None,
        "last_error_code": None,
        "last_error_at": None,
        "counts": {},
        "note": None,
    }
    if enabled is False:
        result["note"] = "此任务当前已关闭"
    elif enabled is None:
        result["state"] = "unavailable"
        result["note"] = "任务配置读取失败"
    if not path.is_file():
        result["state"] = "disabled" if enabled is False else "not_initialized"
        result["note"] = "本机尚未创建任务状态库"
        return result
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            tables = {row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if include_queue:
                if "jobs" not in tables:
                    result["state"] = "unavailable"
                    result["note"] = "任务队列结构不可用"
                else:
                    query = "SELECT status,COUNT(*) AS total FROM jobs"
                    parameters = ()
                    if source is not None:
                        query += " WHERE source=?"
                        parameters = (source,)
                    result["counts"] = {
                        row["status"]: row["total"] for row in connection.execute(
                            query + " GROUP BY status", parameters)
                    }
            if enabled and worker:
                if "worker_health" not in tables:
                    result["state"] = "not_started"
                    result["note"] = "尚未收到任务心跳"
                else:
                    heartbeat = connection.execute(
                        "SELECT heartbeat_at,last_error_code,last_error_at FROM worker_health "
                        "WHERE worker=?", (worker,)
                    ).fetchone()
                    if heartbeat:
                        age = max(0, int(time.time() - heartbeat["heartbeat_at"]))
                        result["last_heartbeat"] = _iso_timestamp(heartbeat["heartbeat_at"])
                        result["heartbeat_age_seconds"] = age
                        result["last_error_code"] = heartbeat["last_error_code"]
                        result["last_error_at"] = _iso_timestamp(heartbeat["last_error_at"])
                        if result["state"] != "unavailable":
                            result["state"] = "active" if age <= HEARTBEAT_STALE_SECONDS else "stale"
                    else:
                        result["state"] = "not_started"
                        result["note"] = "尚未收到任务心跳"
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        logger.exception("Operational health state unavailable: %s", key)
        result["state"] = "unavailable"
        result["note"] = "本机任务状态读取失败"
    return result


def _announcement_pending(component, path):
    if component["state"] == "unavailable" or not path.is_file():
        return
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT source,target,job_key FROM jobs WHERE status='sent'"
        ).fetchall()
        expected = {announcement_job_key(row) for row in rows}
        if not expected:
            component["counts"]["pending_index"] = 0
            return
        database = get_conn()
        try:
            indexed = set()
            keys = tuple(expected)
            with database.cursor() as cursor:
                for start in range(0, len(keys), 500):
                    batch = keys[start:start + 500]
                    cursor.execute(
                        "SELECT job_key FROM channel_announcements WHERE job_key IN ("
                        + ",".join(["%s"] * len(batch)) + ")",
                        batch,
                    )
                    indexed.update(row["job_key"] for row in cursor.fetchall())
        finally:
            database.close()
        component["counts"]["pending_index"] = len(expected - indexed)
    except (OSError, sqlite3.Error, pymysql.MySQLError, ValueError):
        logger.exception("Announcement index health unavailable")
        component["state"] = "unavailable"
        component["note"] = "公告索引队列读取失败"
    finally:
        if connection is not None:
            connection.close()


def snapshot():
    from channel_sync import state_directory
    from movie_ingest import SOURCES, store_path

    directory = state_directory()
    jobs_path = directory / "jobs.sqlite3"
    music_path = directory / "music-import.sqlite3"
    playback_path = directory / "music-playback.sqlite3"
    components = [
        _component(jobs_path, "channel_listener", "频道监听", "channel_listener", include_queue=False),
        _component(jobs_path, "channel_forwarder", "频道消息转发", "channel_forwarder"),
        _component(jobs_path, "channel_reconciliation", "频道补扫", "channel_reconciliation",
                   include_queue=False),
        _component(jobs_path, "announcement_indexer", "公告入库", "announcement_indexer",
                   include_queue=False),
    ]
    _announcement_pending(components[-1], jobs_path)
    for username, source_id in NEWS_SOURCES:
        components.append(_component(
            jobs_path, f"news_source_{source_id}", f"新闻来源 @{username}",
            f"news_source_{source_id}", source=source_id,
        ))

    music_connection = None
    music_enabled = False
    music_configured = True
    try:
        if music_path.is_file():
            music_connection = sqlite3.connect(music_path.as_uri() + "?mode=ro", uri=True, timeout=2)
            music_enabled = music_connection.execute(
                "SELECT 1 FROM settings WHERE key='enabled' AND value='1'"
            ).fetchone() is not None
    except (OSError, sqlite3.Error):
        logger.exception("Music collection configuration unavailable")
        music_configured = False
    finally:
        if music_connection is not None:
            music_connection.close()
    components.append(_component(
        music_path, "music_import", "音乐收录", "music_import",
        music_enabled if music_configured else None,
    ))

    for source in SOURCES:
        path = store_path(directory, source)
        enabled = False
        configured = True
        connection = None
        try:
            if path.is_file():
                connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
                enabled = connection.execute(
                    "SELECT 1 FROM settings WHERE key='enabled' AND value='1'"
                ).fetchone() is not None
        except (OSError, sqlite3.Error):
            logger.exception("Movie collection configuration unavailable: %s", source.id)
            configured = False
        finally:
            if connection is not None:
                connection.close()
        components.append(_component(
            path, f"movie_import_{source.id}", f"影视收录 · {source.username}",
            f"movie_import_{source.id}", enabled if configured else None,
        ))

    components.append(_component(
        playback_path, "music_playback", "音乐播放准备", "music_playback",
    ))
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "components": components,
        "stale_after_seconds": HEARTBEAT_STALE_SECONDS,
    }
