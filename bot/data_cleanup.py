import logging
import threading
import time

from db import get_conn

logger = logging.getLogger(__name__)

RETENTION_HOURS = 48
CLEANUP_INTERVAL_SECONDS = 3600

# KTV 视频/文字表不在此列,永久保留
CLEANUP_TABLES = (
    "technician_photos",
    "attendance_daily",
    "attendance_groups",
    "service_item_technicians",
    "delivery_media",
    "venue_photos",
)


def cleanup_expired_data(retention_hours=RETENTION_HOURS):
    deleted = {}
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            for table in CLEANUP_TABLES:
                cursor.execute(
                    f"DELETE FROM {table} "
                    "WHERE created_at < DATE_SUB(NOW(), INTERVAL %s HOUR)",
                    (retention_hours,),
                )
                deleted[table] = cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    return deleted


def _cleanup_loop():
    while True:
        try:
            deleted = cleanup_expired_data()
            total = sum(deleted.values())
            if total:
                print(f"已清理超过{RETENTION_HOURS}小时的数据 {total} 条: {deleted}")
        except Exception:
            logger.exception("清理过期数据失败")
        time.sleep(CLEANUP_INTERVAL_SECONDS)


def start_cleanup_thread():
    thread = threading.Thread(target=_cleanup_loop, daemon=True)
    thread.start()
    return thread
