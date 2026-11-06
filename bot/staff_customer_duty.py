"""Opt-in customer duty subscriptions with separate staff and delivery identities."""
from datetime import datetime
import hashlib
import json
import logging
import re

import pymysql

from db import get_conn
from duty_shift import current_shift_window

logger = logging.getLogger(__name__)


def _storage(delivery):
    return ("delivery_customer", "person_id", "delivery_people") if delivery else (
        "staff_customer", "staff_id", "staff_members")


def ensure_tables(*, delivery=False):
    prefix, identity, members = _storage(delivery)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if not delivery:
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS staff_customer_permissions (
                        staff_id BIGINT PRIMARY KEY, enabled BOOLEAN NOT NULL,
                        actor_id BIGINT NOT NULL, updated_at DATETIME NOT NULL,
                        FOREIGN KEY(staff_id) REFERENCES staff_members(id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            for query in (
                f"""CREATE TABLE IF NOT EXISTS {prefix}_subscriptions (
                    {identity} BIGINT NOT NULL, user_id BIGINT NOT NULL, enabled BOOLEAN NOT NULL,
                    subscribed_at DATETIME NOT NULL, revision INT NOT NULL DEFAULT 1,
                    PRIMARY KEY({identity},user_id),
                    FOREIGN KEY({identity}) REFERENCES {members}(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                f"""CREATE TABLE IF NOT EXISTS {prefix}_requests (
                    request_id CHAR(32) PRIMARY KEY, user_id BIGINT NOT NULL,
                    request_hash CHAR(64) NOT NULL, created_at DATETIME NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                f"""CREATE TABLE IF NOT EXISTS {prefix}_notifications (
                    {identity} BIGINT NOT NULL, user_id BIGINT NOT NULL, duty_date DATE NOT NULL,
                    shift VARCHAR(16) NOT NULL, published_at DATETIME NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending', created_at DATETIME NOT NULL,
                    sent_at DATETIME NULL, PRIMARY KEY({identity},user_id,duty_date,shift)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            ):
                cur.execute(query)
        conn.commit()
    finally:
        conn.close()


def published_at(cur, member, start, end, after=None, *, delivery=False):
    if delivery:
        from delivery_customer_duty import published_at as delivery_published_at
        return delivery_published_at(member, start, end, after)
    cur.execute("SELECT members_json,published_at FROM staff_rosters WHERE venue_id=%s "
                "AND published_at>=%s AND published_at<%s "
                "ORDER BY published_at DESC,source_id DESC LIMIT 1", (member["venue_id"], start, end))
    roster = cur.fetchone()
    published = roster["published_at"] if roster and member["id"] in json.loads(roster["members_json"]) else None
    if member["venue_id"] in {4, 5, 6}:
        cur.execute("""SELECT MAX(last_seen) AS published FROM staff_photos WHERE staff_id=%s
            AND last_seen>=%s AND last_seen<%s""", (member["id"], start, end))
        photo_at = cur.fetchone()["published"]
        if photo_at and (not published or photo_at > published):
            published = photo_at
    return published if published and (after is None or published > after) else None


def eligible(member, *, delivery=False):
    if delivery:
        from delivery_customer_duty import eligible as delivery_eligible
        return delivery_eligible(member)
    from staff_archive import VENUE_IDS
    return (member["venue_id"] in VENUE_IDS and not member["ended_at"]
            and not member.get("deleted_at") and not member["blocked"]
            and (member["venue_id"] != 6 or member["adult_confirmed"]))


def member_row(cur, staff_id, lock=False, *, delivery=False):
    if delivery:
        from delivery_customer_duty import member_row as delivery_member_row
        return delivery_member_row(cur, staff_id, lock=lock)
    cur.execute("""SELECT m.*,v.name AS venue FROM staff_members m
        JOIN venues v ON v.id=m.venue_id
        WHERE m.id=%s""" +
                (" FOR UPDATE" if lock else ""), (staff_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError("人员档案不存在")
    from staff_archive import _venue
    _venue(row["venue_id"])
    return row


def state_result(sub, available, published, shift, end):
    return {"subscribed": bool(sub and sub["enabled"]), "revision": sub["revision"] if sub else 0,
            "available": available, "published_at": str(published) if published else None,
            "shift": shift, "shift_ends_at": int(end.timestamp() * 1000)}


def catalog_status(venue_id, user_id):
    from staff_archive import _venue
    _venue(venue_id)
    start, end, shift = current_shift_window()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT members_json,published_at FROM staff_rosters WHERE venue_id=%s "
                        "AND published_at>=%s AND published_at<%s "
                        "ORDER BY published_at DESC,source_id DESC LIMIT 1", (venue_id, start, end))
            roster = cur.fetchone()
            publications = {staff_id: roster["published_at"] for staff_id in json.loads(roster["members_json"])} if roster else {}
            if venue_id in {4, 5, 6}:
                cur.execute("SELECT p.staff_id,MAX(p.last_seen) AS published FROM staff_photos p "
                            "JOIN staff_members m ON m.id=p.staff_id WHERE m.venue_id=%s "
                            "AND p.last_seen>=%s AND p.last_seen<%s GROUP BY p.staff_id", (venue_id, start, end))
                for row in cur.fetchall():
                    previous = publications.get(row["staff_id"])
                    if previous is None or row["published"] > previous:
                        publications[row["staff_id"]] = row["published"]
            cur.execute("SELECT m.id,s.enabled,s.revision,"
                        "(SELECT p.id FROM staff_photos p WHERE p.staff_id=m.id"
                        " ORDER BY p.last_seen DESC,p.id DESC LIMIT 1) AS cover FROM staff_members m "
                        "LEFT JOIN staff_customer_subscriptions s ON s.staff_id=m.id AND s.user_id=%s "
                        "WHERE m.venue_id=%s AND m.ended_at IS NULL AND m.deleted_at IS NULL "
                        "AND m.blocked=0 AND (m.venue_id<>6 OR m.adult_confirmed=1)", (user_id, venue_id))
            states = {str(row["id"]): {**state_result(
                row if row["revision"] is not None else None, True,
                publications.get(row["id"]), shift, end), "cover": row.get("cover")}
                for row in cur.fetchall()}
        return {"states": states, "shift_ends_at": int(end.timestamp() * 1000)}
    finally:
        conn.close()


def state(cur, member, user_id, *, delivery=False):
    prefix, identity, _ = _storage(delivery)
    start, end, shift = current_shift_window()
    published = published_at(cur, member, start, end, delivery=delivery)
    cur.execute(f"SELECT enabled,revision FROM {prefix}_subscriptions WHERE {identity}=%s AND user_id=%s",
                (member["id"], user_id))
    sub = cur.fetchone()
    return state_result(sub, eligible(member, delivery=delivery), published, shift, end)


def status(data, user_id=None, *, delivery=False):
    _, identity, _ = _storage(delivery)
    staff_id = data.get(identity)
    if type(staff_id) is not int or not 0 < staff_id <= 9007199254740991:
        raise ValueError("人员档案编号无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            member = member_row(cur, staff_id, delivery=delivery)
            if (delivery and not eligible(member, delivery=True)) or (not delivery and (
                    member["ended_at"] or member.get("deleted_at") or member["blocked"]
                    or (member["venue_id"] == 6 and not member["adult_confirmed"]))):
                raise ValueError("人员档案已停用或暂不展示")
            return state(cur, member, user_id, delivery=delivery)
    finally:
        conn.close()


def save(data, user_id, *, delivery=False):
    from customer_records import RecordConflict
    prefix, identity, _ = _storage(delivery)
    staff_id, enabled, revision, request_id = (data.get(key) for key in (identity, "enabled", "revision", "request_id"))
    if type(staff_id) is not int or not 0 < staff_id <= 9007199254740991 or type(enabled) is not bool:
        raise ValueError("人员编号或订阅状态无效")
    if type(revision) is not int or revision < 0 or not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise ValueError("订阅操作编号或版本无效")
    digest = hashlib.sha256(json.dumps([staff_id, enabled, revision]).encode()).hexdigest()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            member = member_row(cur, staff_id, lock=True, delivery=delivery)
            cur.execute(f"SELECT * FROM {prefix}_requests WHERE request_id=%s", (request_id,))
            previous = cur.fetchone()
            if previous:
                if previous["user_id"] != user_id or previous["request_hash"] != digest:
                    raise RecordConflict("操作编号冲突，请刷新核对")
                return state(cur, member, user_id, delivery=delivery)
            current = state(cur, member, user_id, delivery=delivery)
            if current["revision"] != revision:
                raise RecordConflict("订阅已更新，请刷新核对")
            if enabled and (not current["available"] or current["published_at"]):
                raise RecordConflict("人员档案已停用、暂不公开或本班次已发布出勤，请刷新")
            now = datetime.now()
            cur.execute(f"""INSERT INTO {prefix}_subscriptions({identity},user_id,enabled,subscribed_at)
                VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                subscribed_at=IF(enabled=0 AND VALUES(enabled)=1,VALUES(subscribed_at),subscribed_at),
                enabled=VALUES(enabled),revision=revision+1""", (staff_id, user_id, enabled, now))
            if not enabled:
                cur.execute(f"""UPDATE {prefix}_notifications SET status='cancelled'
                    WHERE {identity}=%s AND user_id=%s AND status='pending'""", (staff_id, user_id))
            cur.execute(f"INSERT INTO {prefix}_requests VALUES(%s,%s,%s,%s)",
                        (request_id, user_id, digest, now))
            result = state(cur, member, user_id, delivery=delivery)
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


def deliver(bot, *, delivery=False):
    import requests
    from telebot import apihelper
    prefix, identity, _ = _storage(delivery)
    lock_name = f"{prefix}_duty"
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s,0) AS acquired", (lock_name,))
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                return
            start, end, shift = current_shift_window()
            cur.execute(f"""UPDATE {prefix}_notifications SET status='cancelled'
                WHERE status='pending' AND (duty_date<>%s OR shift<>%s)""", (start.date(), shift))
            cur.execute(f"SELECT * FROM {prefix}_subscriptions WHERE enabled=1")
            for sub in cur.fetchall():
                member = member_row(cur, sub[identity], lock=True, delivery=delivery)
                if not eligible(member, delivery=delivery):
                    cur.execute(f"UPDATE {prefix}_subscriptions SET enabled=0,revision=revision+1 WHERE {identity}=%s AND user_id=%s",
                                (sub[identity], sub["user_id"]))
                    conn.commit()
                    continue
                cur.execute(f"SELECT enabled,subscribed_at FROM {prefix}_subscriptions WHERE {identity}=%s AND user_id=%s FOR UPDATE",
                            (sub[identity], sub["user_id"]))
                latest = cur.fetchone()
                published = published_at(cur, member, start, end, latest["subscribed_at"], delivery=delivery) if latest["enabled"] else None
                if published:
                    cur.execute(f"""INSERT INTO {prefix}_notifications
                        ({identity},user_id,duty_date,shift,published_at,created_at) VALUES(%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE {identity}=VALUES({identity})""",
                                (member["id"], sub["user_id"], start.date(), shift, published, datetime.now()))
                conn.commit()
            cur.execute(f"SELECT * FROM {prefix}_notifications WHERE status='pending' ORDER BY created_at LIMIT 20")
            for job in cur.fetchall():
                member = member_row(cur, job[identity], lock=True, delivery=delivery)
                cur.execute(f"SELECT enabled,subscribed_at FROM {prefix}_subscriptions WHERE {identity}=%s AND user_id=%s FOR UPDATE",
                            (job[identity], job["user_id"]))
                sub = cur.fetchone()
                fresh_start, fresh_end, fresh_shift = current_shift_window()
                valid = (eligible(member, delivery=delivery) and sub and sub["enabled"] and job["published_at"] > sub["subscribed_at"]
                         and job["duty_date"] == fresh_start.date() and job["shift"] == fresh_shift
                         and fresh_start <= job["published_at"] < fresh_end)
                valid = valid and (not delivery or fresh_shift == "晚班") and published_at(
                    cur, member, fresh_start, fresh_end, sub["subscribed_at"], delivery=delivery) is not None
                key = (job[identity], job["user_id"], job["duty_date"], job["shift"])
                cur.execute(f"""UPDATE {prefix}_notifications SET status=%s WHERE {identity}=%s AND user_id=%s
                    AND duty_date=%s AND shift=%s AND status='pending'""", ("sending" if valid else "cancelled", *key))
                claimed = valid and cur.rowcount == 1
                conn.commit()
                if not claimed:
                    continue
                try:
                    label = "正规按摩出勤发布提醒" if member["venue_id"] == 1 else "会所出勤发布提醒"
                    heading = f"外送出勤发布提醒\n{member['venue']} · 档案 #{member['id']}" if delivery else (
                        f"{label}\n{member['venue']} · 工号 {member['code']}")
                    bot.send_message(job["user_id"], f"{heading}\n"
                                     f"{job['duty_date']} · {job['shift']}\n发布：{job['published_at']}\n"
                                     "仅表示收到出勤发布，不代表实时在岗。可在人员档案取消订阅。", parse_mode=None)
                except (apihelper.ApiException, requests.RequestException):
                    logger.exception("Customer duty notification outcome unconfirmed")
                    outcome = "unconfirmed"
                else:
                    outcome = "sent"
                cur.execute(f"""UPDATE {prefix}_notifications SET status=%s,sent_at=%s
                    WHERE {identity}=%s AND user_id=%s AND duty_date=%s AND shift=%s""",
                            (outcome, datetime.now() if outcome == "sent" else None, *key))
                conn.commit()
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        conn.close()
