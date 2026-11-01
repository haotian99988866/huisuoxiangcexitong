"""Administrator-only staff labels and authorized internal duty notifications."""
from datetime import datetime
import hashlib
import json
import logging
import re
import threading

import pymysql

from db import get_conn
from duty_shift import current_shift_window
from visit_stats import admin_ids

logger = logging.getLogger(__name__)
STATES = {"", "在职", "离职"}
CIRCUMSTANCES = {"", "回国", "请假"}


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for query in (
                """CREATE TABLE IF NOT EXISTS staff_management (
                    staff_id BIGINT PRIMARY KEY, employment VARCHAR(16) NOT NULL DEFAULT '',
                    circumstance VARCHAR(16) NOT NULL DEFAULT '', revision INT NOT NULL DEFAULT 1,
                    actor_id BIGINT NOT NULL, updated_at DATETIME NOT NULL,
                    FOREIGN KEY(staff_id) REFERENCES staff_members(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                """CREATE TABLE IF NOT EXISTS staff_duty_subscriptions (
                    staff_id BIGINT NOT NULL, admin_id BIGINT NOT NULL,
                    enabled BOOLEAN NOT NULL, authorized_at DATETIME NOT NULL, subscribed_at DATETIME NOT NULL,
                    PRIMARY KEY(staff_id,admin_id),
                    FOREIGN KEY(staff_id) REFERENCES staff_members(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                """CREATE TABLE IF NOT EXISTS staff_management_audit (
                    request_id CHAR(32) PRIMARY KEY, request_hash CHAR(64) NOT NULL,
                    actor_id BIGINT NOT NULL, staff_id BIGINT NOT NULL,
                    before_json JSON NOT NULL, after_json JSON NOT NULL, created_at DATETIME NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                """CREATE TABLE IF NOT EXISTS staff_duty_notifications (
                    staff_id BIGINT NOT NULL, admin_id BIGINT NOT NULL, duty_date DATE NOT NULL,
                    shift VARCHAR(16) NOT NULL, published_at DATETIME NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending', created_at DATETIME NOT NULL,
                    sent_at DATETIME NULL,
                    PRIMARY KEY(staff_id,admin_id,duty_date,shift)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            ):
                cur.execute(query)
        conn.commit()
    finally:
        conn.close()


def _admin(actor_id):
    if actor_id not in admin_ids():
        raise ValueError("仅平台管理员可管理人员标签和内部订阅")


def overview(data, actor_id):
    from staff_archive import listing
    _admin(actor_id)
    result = listing(data, admin=True)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for member in result["members"]:
                member.pop("price_usd", None)
                member.pop("price_detail", None)
                cur.execute("SELECT employment,circumstance,revision,updated_at FROM staff_management WHERE staff_id=%s",
                            (member["id"],))
                management = cur.fetchone()
                member.update(management or {"employment": "", "circumstance": "", "revision": 0, "updated_at": None})
                if member["updated_at"]:
                    member["updated_at"] = str(member["updated_at"])
                cur.execute("SELECT enabled FROM staff_duty_subscriptions WHERE staff_id=%s AND admin_id=%s",
                            (member["id"], actor_id))
                subscription = cur.fetchone()
                member["subscribed"] = bool(subscription and subscription["enabled"])
                cur.execute("SELECT enabled FROM staff_customer_permissions WHERE staff_id=%s", (member["id"],))
                permission = cur.fetchone()
                member["customer_authorized"] = bool(permission and permission["enabled"])
                cur.execute("""SELECT status,published_at FROM staff_duty_notifications
                    WHERE staff_id=%s AND admin_id=%s ORDER BY created_at DESC LIMIT 1""", (member["id"], actor_id))
                notification = cur.fetchone()
                member["notification"] = ({**notification, "published_at": str(notification["published_at"])}
                                          if notification else None)
        return result
    finally:
        conn.close()


def save(data, actor_id):
    from customer_records import RecordConflict
    _admin(actor_id)
    staff_id = data.get("staff_id")
    request_id = data.get("request_id")
    revision = data.get("revision")
    employment, circumstance = data.get("employment"), data.get("circumstance")
    enabled = data.get("subscribed")
    customer_authorized = data.get("customer_authorized", False)
    if type(customer_authorized) is not bool:
        raise ValueError("客人订阅授权状态无效")
    if employment == "离职":
        customer_authorized = False
    if type(staff_id) is not int or not 0 < staff_id <= 9007199254740991:
        raise ValueError("人员档案编号无效")
    if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise ValueError("操作编号无效")
    if type(revision) is not int or revision < 0 or data.get("confirmed") is not True:
        raise ValueError("请核对并确认人员管理操作")
    if not isinstance(employment, str) or employment not in STATES or not isinstance(circumstance, str) or circumstance not in CIRCUMSTANCES:
        raise ValueError("请选择有效管理标签，不记录健康或私人敏感原因")
    if type(enabled) is not bool:
        raise ValueError("订阅状态无效")
    if enabled and employment == "离职":
        raise ValueError("离职人员不能订阅出勤，请先取消订阅")
    digest = hashlib.sha256(json.dumps({key: value for key, value in data.items() if key != "init_data"},
                                      sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    now = datetime.now()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,venue_id,ended_at FROM staff_members WHERE id=%s FOR UPDATE", (staff_id,))
            member = cur.fetchone()
            if not member:
                raise ValueError("人员档案不存在")
            if customer_authorized and member["venue_id"] != 1:
                raise ValueError("客人订阅授权仅用于名玺水汇按摩")
            cur.execute("SELECT request_hash,actor_id,after_json FROM staff_management_audit WHERE request_id=%s",
                        (request_id,))
            prior = cur.fetchone()
            if prior:
                if prior["request_hash"] != digest or prior["actor_id"] != actor_id:
                    raise RecordConflict("操作编号已用于其他内容，请刷新核对")
                return {"ok": True, "replayed": True}
            if member["ended_at"]:
                raise RecordConflict("该工号已经换人，原订阅不会转给新人")
            cur.execute("SELECT * FROM staff_management WHERE staff_id=%s FOR UPDATE", (staff_id,))
            previous = cur.fetchone()
            if revision != (previous["revision"] if previous else 0):
                raise RecordConflict("管理标签已被更新，请刷新后重新编辑")
            cur.execute("SELECT * FROM staff_duty_subscriptions WHERE staff_id=%s AND admin_id=%s FOR UPDATE",
                        (staff_id, actor_id))
            subscription = cur.fetchone()
            if enabled and not (subscription and subscription["enabled"]) and data.get("authorized") is not True:
                raise ValueError("请确认已取得人员授权，仅用于内部出勤管理")
            before = {"labels": previous, "subscription": subscription}
            cur.execute("SELECT * FROM staff_customer_permissions WHERE staff_id=%s", (staff_id,))
            before["customer_permission"] = cur.fetchone()
            cur.execute("""INSERT INTO staff_customer_permissions(staff_id,enabled,actor_id,updated_at)
                VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE enabled=VALUES(enabled),
                actor_id=VALUES(actor_id),updated_at=VALUES(updated_at)""",
                        (staff_id, customer_authorized, actor_id, now))
            if not customer_authorized:
                cur.execute("UPDATE staff_customer_subscriptions SET enabled=0,revision=revision+1 WHERE staff_id=%s AND enabled=1",
                            (staff_id,))
                cur.execute("UPDATE staff_customer_notifications SET status='cancelled' WHERE staff_id=%s AND status='pending'",
                            (staff_id,))
            cur.execute("""INSERT INTO staff_management(staff_id,employment,circumstance,actor_id,updated_at)
                VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE employment=VALUES(employment),
                circumstance=VALUES(circumstance),actor_id=VALUES(actor_id),updated_at=VALUES(updated_at),revision=revision+1""",
                        (staff_id, employment, circumstance, actor_id, now))
            if subscription:
                cur.execute("""UPDATE staff_duty_subscriptions SET enabled=%s,
                    subscribed_at=%s,authorized_at=%s WHERE staff_id=%s AND admin_id=%s""",
                            (enabled, now if enabled and not subscription["enabled"] else subscription["subscribed_at"],
                             now if enabled and not subscription["enabled"] else subscription["authorized_at"], staff_id, actor_id))
            elif enabled:
                cur.execute("""INSERT INTO staff_duty_subscriptions(staff_id,admin_id,enabled,authorized_at,subscribed_at)
                    VALUES(%s,%s,1,%s,%s)""", (staff_id, actor_id, now, now))
            if employment == "离职":
                cur.execute("UPDATE staff_duty_subscriptions SET enabled=0 WHERE staff_id=%s", (staff_id,))
                cur.execute("UPDATE staff_duty_notifications SET status='cancelled' WHERE staff_id=%s AND status='pending'",
                            (staff_id,))
            elif not enabled:
                cur.execute("""UPDATE staff_duty_notifications SET status='cancelled'
                    WHERE staff_id=%s AND admin_id=%s AND status='pending'""", (staff_id, actor_id))
            after = {"employment": employment, "circumstance": circumstance, "subscribed": enabled,
                     "revision": revision + 1, "authorized": data.get("authorized") is True,
                     "customer_authorized": customer_authorized}
            cur.execute("""INSERT INTO staff_management_audit
                (request_id,request_hash,actor_id,staff_id,before_json,after_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                        (request_id, digest, actor_id, staff_id, json.dumps(before, default=str, ensure_ascii=True),
                         json.dumps(after, ensure_ascii=True), now))
        conn.commit()
        return {"ok": True}
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if exc.args[0] == 1062:
            raise RecordConflict("操作编号已存在，请使用原编号重试核对") from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def deliver(bot):
    import requests
    from telebot import apihelper
    now = datetime.now()
    start, end, shift = current_shift_window(now)
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('staff_internal_duty_notifications',0) AS acquired")
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                return
            cur.execute("""SELECT s.*,m.venue_id,m.code,m.ended_at,m.blocked,v.name AS venue,
                COALESCE(g.employment,'') AS employment FROM staff_duty_subscriptions s
                JOIN staff_members m ON m.id=s.staff_id JOIN venues v ON v.id=m.venue_id
                LEFT JOIN staff_management g ON g.staff_id=m.id WHERE s.enabled=1""")
            for subscription in cur.fetchall():
                staff_id, admin_id = subscription["staff_id"], subscription["admin_id"]
                if admin_id not in admin_ids() or subscription["ended_at"] or subscription["employment"] == "离职":
                    cur.execute("UPDATE staff_duty_subscriptions SET enabled=0 WHERE staff_id=%s AND admin_id=%s",
                                (staff_id, admin_id))
                    continue
                if subscription["blocked"]:
                    continue
                cur.execute("""SELECT MAX(published_at) AS published FROM staff_rosters WHERE venue_id=%s
                    AND published_at>=%s AND published_at<%s AND published_at>%s
                    AND JSON_CONTAINS(members_json,CAST(%s AS JSON))""",
                            (subscription["venue_id"], start, end, subscription["subscribed_at"], staff_id))
                published = cur.fetchone()["published"]
                if subscription["venue_id"] in {4, 5, 6}:
                    cur.execute("""SELECT MAX(last_seen) AS published FROM staff_photos WHERE staff_id=%s
                        AND last_seen>=%s AND last_seen<%s AND last_seen>%s""",
                                (staff_id, start, end, subscription["subscribed_at"]))
                    photo_at = cur.fetchone()["published"]
                    if photo_at and (not published or photo_at > published):
                        published = photo_at
                if published:
                    cur.execute("""INSERT INTO staff_duty_notifications
                        (staff_id,admin_id,duty_date,shift,published_at,created_at) VALUES(%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE staff_id=VALUES(staff_id)""",
                                (staff_id, admin_id, start.date(), shift, published, now))
            conn.commit()
            cur.execute("""SELECT n.*,m.code,m.ended_at,m.blocked,v.name AS venue,s.enabled,
                COALESCE(g.employment,'') AS employment FROM staff_duty_notifications n
                JOIN staff_members m ON m.id=n.staff_id JOIN venues v ON v.id=m.venue_id
                LEFT JOIN staff_duty_subscriptions s ON s.staff_id=n.staff_id AND s.admin_id=n.admin_id
                LEFT JOIN staff_management g ON g.staff_id=n.staff_id
                WHERE n.status='pending' ORDER BY n.created_at LIMIT 20""")
            for job in cur.fetchall():
                key = (job["staff_id"], job["admin_id"], job["duty_date"], job["shift"])
                if job["admin_id"] not in admin_ids() or not job["enabled"] or job["ended_at"] or job["blocked"] or job["employment"] == "离职":
                    cur.execute("""UPDATE staff_duty_notifications SET status='cancelled'
                        WHERE staff_id=%s AND admin_id=%s AND duty_date=%s AND shift=%s""", key)
                    conn.commit()
                    continue
                # Persist the claim before sending: unknown network outcomes must not trigger duplicate messages.
                cur.execute("""UPDATE staff_duty_notifications SET status='sending'
                    WHERE staff_id=%s AND admin_id=%s AND duty_date=%s AND shift=%s AND status='pending'""", key)
                claimed = cur.rowcount == 1
                conn.commit()
                if not claimed:
                    continue
                try:
                    bot.send_message(job["admin_id"], f"内部出勤提醒\n{job['venue']} · 工号 {job['code']}\n"
                                     f"{job['duty_date']} · {job['shift']}\n发布：{job['published_at']}\n"
                                     "仅表示收到出勤发布，不代表实时在岗。", parse_mode=None)
                except (apihelper.ApiException, requests.RequestException):
                    logger.exception("Internal duty notification outcome unconfirmed staff_id=%s admin_id=%s", *key[:2])
                    status = "unconfirmed"
                else:
                    status = "sent"
                cur.execute("""UPDATE staff_duty_notifications SET status=%s,sent_at=%s
                    WHERE staff_id=%s AND admin_id=%s AND duty_date=%s AND shift=%s""",
                            (status, datetime.now() if status == "sent" else None, *key))
                conn.commit()
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK('staff_internal_duty_notifications')")
        conn.close()


def start_worker(bot):
    def run():
        while True:
            try:
                deliver(bot)
                from staff_customer_duty import deliver as deliver_customer_duty
                deliver_customer_duty(bot)
            except (ValueError, RuntimeError, pymysql.MySQLError):
                logger.exception("Staff duty notification worker unavailable")
            threading.Event().wait(15)
    threading.Thread(target=run, name="internal-staff-duty", daemon=True).start()
