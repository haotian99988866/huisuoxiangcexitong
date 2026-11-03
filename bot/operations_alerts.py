"""Independent, persistent operational reminders sent by the main bot."""
from datetime import datetime
import json
import logging
import sqlite3
import threading
import time

from operations_health import snapshot


logger = logging.getLogger(__name__)
CHECK_SECONDS = 30
ALERT_AFTER_SECONDS = 5 * 60
REPEAT_SECONDS = 30 * 60
RECENT_ERROR_SECONDS = 180
_start_lock = threading.Lock()
_started = False


def component_issues(component, now):
    key, name = component["key"], component["name"]
    if component["enabled"] is False or component["state"] == "disabled":
        return {}
    issues = {}
    if component["state"] != "active":
        labels = {
            "stale": "工作进程心跳超时",
            "not_started": "尚未收到工作进程心跳",
            "not_initialized": "任务状态库尚未建立",
            "unavailable": "任务状态读取失败",
        }
        issues[f"{key}:worker"] = (name, labels.get(component["state"], "工作状态异常"), None)
    counts = component["counts"]
    problems = [(label, counts.get(status, 0)) for status, label in (
        ("failed", "失败"), ("uncertain", "待核查"),
        ("cleanup_pending", "自动清理未完成")) if counts.get(status, 0)]
    if problems:
        issues[f"{key}:jobs"] = (name, "、".join(f"{label} {count} 条" for label, count in problems), None)
    if component["last_error_at"] and component["last_error_code"]:
        error_at = datetime.fromisoformat(component["last_error_at"].replace("Z", "+00:00")).timestamp()
        if 0 <= now - error_at <= RECENT_ERROR_SECONDS:
            issues[f"{key}:errors"] = (name, f"持续出现错误：{component['last_error_code']}", None)
    pending = sum(counts.get(status, 0) for status in ("queued", "preparing", "sending", "pending_index", "forwarded"))
    if pending:
        completed = sum(counts.get(status, 0) for status in ("sent", "ready", "skipped", "excluded"))
        issues[f"{key}:queue"] = (
            name, f"队列待处理 {pending} 条，持续未见处理进展", json.dumps((completed, pending)))
    return issues


class AlertStore:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS incidents (
                key TEXT PRIMARY KEY, name TEXT NOT NULL, reason TEXT NOT NULL,
                signature TEXT, since REAL NOT NULL, active INTEGER NOT NULL,
                notified INTEGER NOT NULL DEFAULT 0, last_attempt REAL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, keys TEXT NOT NULL,
                attempted_at REAL NOT NULL, status TEXT NOT NULL, error_code TEXT
            );
            CREATE TABLE IF NOT EXISTS monitor_health (
                id INTEGER PRIMARY KEY CHECK(id=1),
                checked_at REAL NOT NULL, snapshot_available INTEGER NOT NULL
            );
        """)

    def observe(self, issues, now, observed_keys=None):
        with self.connection:
            rows = {row["key"]: row for row in self.connection.execute("SELECT * FROM incidents")}
            for key, (name, reason, signature) in issues.items():
                previous = rows.get(key)
                if previous and not previous["active"] and previous["notified"]:
                    continue
                restart = not previous or not previous["active"] or previous["signature"] != signature
                if previous and previous["active"] and signature is not None and previous["signature"] is not None:
                    completed, pending = json.loads(signature)
                    old_completed, old_pending = json.loads(previous["signature"])
                    restart = completed != old_completed or pending < old_pending
                # A progressing backlog is no longer stalled, even if it is not empty yet.
                if previous and previous["active"] and restart and previous["notified"]:
                    self.connection.execute(
                        "UPDATE incidents SET active=0 WHERE key=?", (key,))
                    continue
                self.connection.execute(
                    "INSERT INTO incidents(key,name,reason,signature,since,active) VALUES (?,?,?,?,?,1)"
                    " ON CONFLICT(key) DO UPDATE SET name=excluded.name,reason=excluded.reason,"
                    " signature=excluded.signature,since=excluded.since,active=1,"
                    " notified=?,last_attempt=?",
                    (key, name, reason, signature, now if restart else previous["since"],
                     0 if restart else previous["notified"],
                     None if restart else previous["last_attempt"]),
                )
            for key in rows:
                if key not in issues and (observed_keys is None or key in observed_keys):
                    self.connection.execute("UPDATE incidents SET active=0 WHERE key=?", (key,))

    def due(self, now):
        rows = self.connection.execute("SELECT * FROM incidents ORDER BY key").fetchall()
        alerts = [row for row in rows if row["active"] and now - row["since"] >= ALERT_AFTER_SECONDS
                  and (row["last_attempt"] is None or now - row["last_attempt"] >= REPEAT_SECONDS)]
        recoveries = [row for row in rows if not row["active"] and row["notified"]]
        return alerts, recoveries

    def claim(self, kind, rows, now):
        with self.connection:
            for row in rows:
                self.connection.execute(
                    "UPDATE incidents SET last_attempt=?,notified=? WHERE key=?",
                    (now, 1 if kind == "alert" else 0, row["key"]),
                )
            delivery = self.connection.execute(
                "INSERT INTO deliveries(kind,keys,attempted_at,status) VALUES (?,?,?,'uncertain')",
                (kind, ",".join(row["key"] for row in rows), now),
            )
        return delivery.lastrowid

    def complete(self, delivery_id, status, error_code=None):
        with self.connection:
            self.connection.execute(
                "UPDATE deliveries SET status=?,error_code=? WHERE id=?",
                (status, error_code, delivery_id),
            )

    def close(self):
        self.connection.close()

    def record_check(self, now, available):
        with self.connection:
            self.connection.execute(
                "INSERT INTO monitor_health VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET "
                "checked_at=excluded.checked_at,snapshot_available=excluded.snapshot_available",
                (now, int(available)),
            )


def send_due(bot, owner_id, store, now):
    alerts, recoveries = store.due(now)
    for kind, rows in (("alert", alerts), ("recovery", recoveries)):
        for offset in range(0, len(rows), 12):
            batch = rows[offset:offset + 12]
            heading = "运营异常提醒" if kind == "alert" else "运营状态恢复"
            text = heading + "\n\n" + "\n".join(
                f"• {row['name']}：{row['reason']}" if kind == "alert" else
                f"• {row['name']}：{row['reason']} 已解除"
                for row in batch)
            text += ("\n\n异常持续至少5分钟，未恢复每30分钟提醒。"
                     if kind == "alert" else "\n\n此项状态已恢复，其他异常请分别核查。")
            text += "\n请打开小程序「更多管理 → 运营健康」查看。不会自动重试发布或修改业务记录。"
            if kind == "alert":
                text += "\n新闻人工核查可私聊发送 /ops_review；音乐疑问自动清理，影视疑问自动跳过，清理未完成请检查日志。"
            delivery_id = store.claim(kind, batch, now)
            try:
                bot.send_message(owner_id, text)
            except Exception as error:
                # Delivery may have succeeded before a timeout; do not blindly resend it.
                store.complete(delivery_id, "uncertain", type(error).__name__)
                logger.error("Operations %s notification unconfirmed: %s", kind, type(error).__name__)
            else:
                store.complete(delivery_id, "sent")


def check_once(bot, owner_id, store, now=None):
    from visit_stats import admin_ids
    if owner_id not in admin_ids():
        raise ValueError("Operations alert recipient is no longer a platform administrator.")
    now = time.time() if now is None else now
    try:
        components = snapshot()["components"]
        issues = {}
        for component in components:
            issues.update(component_issues(component, now))
    except Exception as error:
        logger.exception("Operations snapshot check failed")
        issues = {"monitor:snapshot": ("运营监测", f"健康检查失败：{type(error).__name__}", None)}
        store.observe(issues, now, observed_keys={"monitor:snapshot"})
        store.record_check(now, False)
    else:
        store.observe(issues, now)
        store.record_check(now, True)
    send_due(bot, owner_id, store, now)


def monitor_worker(bot):
    from channel_sync import single_instance, state_directory
    from premium_balance import manual_owner
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            with single_instance(directory, "operations-alerts.lock"):
                owner_id = manual_owner()
                chat = bot.get_chat(owner_id)
                if chat.type != "private":
                    raise ValueError("Operations alert owner must start the bot in a private chat first.")
                store = AlertStore(directory / "operations-alerts.sqlite3")
                try:
                    logger.warning("Operations alerts READY: check=30s threshold=5m repeat=30m")
                    while True:
                        try:
                            check_once(bot, owner_id, store)
                        except Exception:
                            logger.exception("Operations alert iteration failed; retry in 30 seconds")
                        time.sleep(CHECK_SECONDS)
                finally:
                    store.close()
        except Exception:
            logger.exception("Operations alert monitor unavailable; retry in 30 seconds")
            time.sleep(CHECK_SECONDS)


def start_worker(bot):
    global _started
    with _start_lock:
        if _started:
            return
        threading.Thread(target=monitor_worker, args=(bot,),
                         daemon=True, name="operations-alerts").start()
        _started = True
