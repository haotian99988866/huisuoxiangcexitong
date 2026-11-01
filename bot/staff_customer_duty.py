"""Opt-in customer notifications for authorized, ordinary massage staff."""
from datetime import datetime
import hashlib
import json
import logging
import re

import pymysql

from db import get_conn
from duty_shift import current_shift_window

logger = logging.getLogger(__name__)


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for query in (
                """CREATE TABLE IF NOT EXISTS staff_customer_permissions (
                    staff_id BIGINT PRIMARY KEY, enabled BOOLEAN NOT NULL,
                    actor_id BIGINT NOT NULL, updated_at DATETIME NOT NULL,
                    FOREIGN KEY(staff_id) REFERENCES staff_members(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                """CREATE TABLE IF NOT EXISTS staff_customer_subscriptions (
                    staff_id BIGINT NOT NULL, user_id BIGINT NOT NULL, enabled BOOLEAN NOT NULL,
                    subscribed_at DATETIME NOT NULL, revision INT NOT NULL DEFAULT 1,
                    PRIMARY KEY(staff_id,user_id),
                    FOREIGN KEY(staff_id) REFERENCES staff_members(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                """CREATE TABLE IF NOT EXISTS staff_customer_requests (
                    request_id CHAR(32) PRIMARY KEY, user_id BIGINT NOT NULL,
                    request_hash CHAR(64) NOT NULL, created_at DATETIME NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                """CREATE TABLE IF NOT EXISTS staff_customer_notifications (
                    staff_id BIGINT NOT NULL, user_id BIGINT NOT NULL, duty_date DATE NOT NULL,
                    shift VARCHAR(16) NOT NULL, published_at DATETIME NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending', created_at DATETIME NOT NULL,
                    sent_at DATETIME NULL, PRIMARY KEY(staff_id,user_id,duty_date,shift)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            ):
                cur.execute(query)
        conn.commit()
    finally:
        conn.close()


def published_at(cur, member, start, end, after=None):
    cur.execute("""SELECT MAX(published_at) AS published FROM staff_rosters WHERE venue_id=%s
        AND published_at>=%s AND published_at<%s
        AND JSON_CONTAINS(members_json,CAST(%s AS JSON))""",
                (member["venue_id"], start, end, member["id"]))
    published = cur.fetchone()["published"]
    if member["venue_id"] in {4, 5, 6}:
        cur.execute("""SELECT MAX(last_seen) AS published FROM staff_photos WHERE staff_id=%s
            AND last_seen>=%s AND last_seen<%s""", (member["id"], start, end))
        photo_at = cur.fetchone()["published"]
        if photo_at and (not published or photo_at > published):
            published = photo_at
    return published if published and (after is None or published > after) else None


def eligible(member):
    return (member["venue_id"] == 1 and not member["ended_at"] and not member["blocked"]
            and (member["venue_id"] != 6 or member["adult_confirmed"])
            and member["employment"] != "离职" and bool(member["authorized"]))


def member_row(cur, staff_id, lock=False):
    cur.execute("""SELECT m.*,COALESCE(g.employment,'') AS employment,
        COALESCE(p.enabled,0) AS authorized,v.name AS venue FROM staff_members m
        JOIN venues v ON v.id=m.venue_id
        LEFT JOIN staff_management g ON g.staff_id=m.id
        LEFT JOIN staff_customer_permissions p ON p.staff_id=m.id WHERE m.id=%s""" +
                (" FOR UPDATE" if lock else ""), (staff_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError("人员档案不存在")
    if row["venue_id"] != 1:
        raise ValueError("客人出勤订阅仅开放名玺水汇按摩")
    return row


def state(cur, member, user_id):
    start, end, shift = current_shift_window()
    published = published_at(cur, member, start, end)
    cur.execute("SELECT enabled,revision FROM staff_customer_subscriptions WHERE staff_id=%s AND user_id=%s",
                (member["id"], user_id))
    sub = cur.fetchone()
    return {"subscribed": bool(sub and sub["enabled"]), "revision": sub["revision"] if sub else 0,
            "available": eligible(member), "published_at": str(published) if published else None,
            "shift": shift, "shift_ends_at": int(end.timestamp() * 1000)}


def status(data, user_id=None):
    staff_id = data.get("staff_id")
    if type(staff_id) is not int or not 0 < staff_id <= 9007199254740991:
        raise ValueError("人员档案编号无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            member = member_row(cur, staff_id)
            if member["ended_at"] or member["blocked"] or (member["venue_id"] == 6 and not member["adult_confirmed"]):
                raise ValueError("人员档案已停用或暂不展示")
            return state(cur, member, user_id)
    finally:
        conn.close()


def save(data, user_id):
    from customer_records import RecordConflict
    staff_id, enabled, revision, request_id = (data.get(key) for key in ("staff_id", "enabled", "revision", "request_id"))
    if type(staff_id) is not int or not 0 < staff_id <= 9007199254740991 or type(enabled) is not bool:
        raise ValueError("人员编号或订阅状态无效")
    if type(revision) is not int or revision < 0 or not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise ValueError("订阅操作编号或版本无效")
    digest = hashlib.sha256(json.dumps([staff_id, enabled, revision]).encode()).hexdigest()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            member = member_row(cur, staff_id, lock=True)
            cur.execute("SELECT * FROM staff_customer_requests WHERE request_id=%s", (request_id,))
            previous = cur.fetchone()
            if previous:
                if previous["user_id"] != user_id or previous["request_hash"] != digest:
                    raise RecordConflict("操作编号冲突，请刷新核对")
                return state(cur, member, user_id)
            current = state(cur, member, user_id)
            if current["revision"] != revision:
                raise RecordConflict("订阅已更新，请刷新核对")
            if enabled and (not current["available"] or current["published_at"]):
                raise RecordConflict("人员未开放授权订阅或本班次已发布出勤，请刷新")
            now = datetime.now()
            cur.execute("""INSERT INTO staff_customer_subscriptions(staff_id,user_id,enabled,subscribed_at)
                VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                subscribed_at=IF(enabled=0 AND VALUES(enabled)=1,VALUES(subscribed_at),subscribed_at),
                enabled=VALUES(enabled),revision=revision+1""", (staff_id, user_id, enabled, now))
            if not enabled:
                cur.execute("""UPDATE staff_customer_notifications SET status='cancelled'
                    WHERE staff_id=%s AND user_id=%s AND status='pending'""", (staff_id, user_id))
            cur.execute("INSERT INTO staff_customer_requests VALUES(%s,%s,%s,%s)",
                        (request_id, user_id, digest, now))
            result = state(cur, member, user_id)
        conn.commit()
        return result
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if exc.args[0] == 1062:
            raise RecordConflict("操作编号已使用，请核对后重试") from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def deliver(bot):
    import requests
    from telebot import apihelper
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('staff_customer_duty',0) AS acquired")
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                return
            start, end, shift = current_shift_window()
            cur.execute("""UPDATE staff_customer_notifications SET status='cancelled'
                WHERE status='pending' AND (duty_date<>%s OR shift<>%s)""", (start.date(), shift))
            cur.execute("SELECT * FROM staff_customer_subscriptions WHERE enabled=1")
            for sub in cur.fetchall():
                member = member_row(cur, sub["staff_id"], lock=True)
                if not eligible(member):
                    cur.execute("UPDATE staff_customer_subscriptions SET enabled=0,revision=revision+1 WHERE staff_id=%s AND user_id=%s",
                                (sub["staff_id"], sub["user_id"]))
                    conn.commit()
                    continue
                cur.execute("SELECT enabled,subscribed_at FROM staff_customer_subscriptions WHERE staff_id=%s AND user_id=%s FOR UPDATE",
                            (sub["staff_id"], sub["user_id"]))
                latest = cur.fetchone()
                published = published_at(cur, member, start, end, latest["subscribed_at"]) if latest["enabled"] else None
                if published:
                    cur.execute("""INSERT INTO staff_customer_notifications
                        (staff_id,user_id,duty_date,shift,published_at,created_at) VALUES(%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE staff_id=VALUES(staff_id)""",
                                (member["id"], sub["user_id"], start.date(), shift, published, datetime.now()))
                conn.commit()
            cur.execute("SELECT * FROM staff_customer_notifications WHERE status='pending' ORDER BY created_at LIMIT 20")
            for job in cur.fetchall():
                member = member_row(cur, job["staff_id"], lock=True)
                cur.execute("SELECT enabled,subscribed_at FROM staff_customer_subscriptions WHERE staff_id=%s AND user_id=%s FOR UPDATE",
                            (job["staff_id"], job["user_id"]))
                sub = cur.fetchone()
                fresh_start, fresh_end, fresh_shift = current_shift_window()
                valid = (eligible(member) and sub and sub["enabled"] and job["published_at"] > sub["subscribed_at"]
                         and job["duty_date"] == fresh_start.date() and job["shift"] == fresh_shift
                         and fresh_start <= job["published_at"] < fresh_end)
                key = (job["staff_id"], job["user_id"], job["duty_date"], job["shift"])
                cur.execute("""UPDATE staff_customer_notifications SET status=%s WHERE staff_id=%s AND user_id=%s
                    AND duty_date=%s AND shift=%s AND status='pending'""", ("sending" if valid else "cancelled", *key))
                claimed = valid and cur.rowcount == 1
                conn.commit()
                if not claimed:
                    continue
                try:
                    bot.send_message(job["user_id"], f"正规按摩出勤发布提醒\n{member['venue']} · 工号 {member['code']}\n"
                                     f"{job['duty_date']} · {job['shift']}\n发布：{job['published_at']}\n"
                                     "仅表示收到出勤发布，不代表实时在岗。可在人员档案取消订阅。", parse_mode=None)
                except (apihelper.ApiException, requests.RequestException):
                    logger.exception("Customer duty notification outcome unconfirmed")
                    outcome = "unconfirmed"
                else:
                    outcome = "sent"
                cur.execute("""UPDATE staff_customer_notifications SET status=%s,sent_at=%s
                    WHERE staff_id=%s AND user_id=%s AND duty_date=%s AND shift=%s""",
                            (outcome, datetime.now() if outcome == "sent" else None, *key))
                conn.commit()
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK('staff_customer_duty')")
        conn.close()
