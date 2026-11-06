"""Customer subscription management using the existing opt-in notification stores."""
from datetime import datetime, timedelta
import hashlib
import json
import logging
import re
import threading
import time

import pymysql
import requests
from telebot import apihelper, types

from db import get_conn
from duty_shift import current_shift_window
from subscribe import SUBSCRIBE_VENUES
from delivery_staff import DELIVERY_VENUE_IDS

logger = logging.getLogger(__name__)


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS delivery_scope_subscriptions (
                venue_id INT NOT NULL,user_id BIGINT NOT NULL,subscribed_at DATETIME NOT NULL,
                PRIMARY KEY(venue_id,user_id)) ENGINE=InnoDB""")
            cur.execute("""CREATE TABLE IF NOT EXISTS subscription_center_requests (
                request_id CHAR(32) PRIMARY KEY,user_id BIGINT NOT NULL,request_hash CHAR(64) NOT NULL,
                created_at DATETIME NOT NULL) ENGINE=InnoDB""")
            cur.execute("""CREATE TABLE IF NOT EXISTS delivery_scope_notifications (
                venue_id INT NOT NULL,user_id BIGINT NOT NULL,duty_date DATE NOT NULL,
                status VARCHAR(20) NOT NULL,retry_at DATETIME NULL,
                PRIMARY KEY(venue_id,user_id,duty_date)) ENGINE=InnoDB""")
        conn.commit()
    finally:
        conn.close()


def catalog(user_id, venue_id=None):
    from staff_customer_duty import catalog_status as staff_status
    from delivery_customer_duty import catalog_status as delivery_status
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,name FROM venues WHERE id IN (1,2,4,5,6,7,8,9,10) ORDER BY id")
            venues = cur.fetchall()
            cur.execute("SELECT venue_id FROM bot_subscriptions WHERE tg_user_id=%s", (user_id,))
            venue_subs = {row["venue_id"] for row in cur.fetchall()}
            cur.execute("SELECT venue_id FROM delivery_scope_subscriptions WHERE user_id=%s", (user_id,))
            delivery_subs = {row["venue_id"] for row in cur.fetchall()}
            cur.execute("SELECT active FROM news_subscriptions WHERE user_id=%s", (user_id,))
            news = cur.fetchone()
            scopes = [{"kind": "news", "id": 0, "label": "昊天大事件", "subscribed": bool(news and news["active"])}]
            scopes += [{"kind": "venue" if row["id"] in SUBSCRIBE_VENUES else "delivery",
                        "id": row["id"], "label": row["name"],
                        "subscribed": row["id"] in (venue_subs if row["id"] in SUBSCRIBE_VENUES else delivery_subs)}
                       for row in venues]
            cur.execute("""SELECT s.staff_id AS id,s.revision,m.venue_id,m.code
                FROM staff_customer_subscriptions s JOIN staff_members m ON m.id=s.staff_id
                WHERE s.user_id=%s AND s.enabled=1 ORDER BY m.venue_id,m.code""", (user_id,))
            subscribed = [{"kind": "staff", "id": row["id"], "venue_id": row["venue_id"],
                           "label": f"技师 {row['code']}", "revision": row["revision"]} for row in cur.fetchall()]
            cur.execute("""SELECT s.person_id AS id,s.revision,p.venue_id FROM delivery_customer_subscriptions s
                JOIN delivery_people p ON p.id=s.person_id WHERE s.user_id=%s AND s.enabled=1
                ORDER BY p.venue_id,p.id""", (user_id,))
            subscribed += [{"kind": "person", "id": row["id"], "venue_id": row["venue_id"],
                            "label": f"外送档案 #{row['id']}", "revision": row["revision"]} for row in cur.fetchall()]
            people = []
            if venue_id is not None:
                if type(venue_id) is not int or venue_id not in {row["id"] for row in venues}:
                    raise ValueError("请选择有效的会所或外送分类")
                delivery = venue_id in DELIVERY_VENUE_IDS
                states = (delivery_status if delivery else staff_status)(venue_id, user_id)["states"]
                if delivery:
                    cur.execute("SELECT id,display_name AS label FROM delivery_people WHERE venue_id=%s", (venue_id,))
                else:
                    cur.execute("SELECT id,code AS label FROM staff_members WHERE venue_id=%s", (venue_id,))
                people = [{"kind": "person" if delivery else "staff", "id": row["id"],
                           "label": (row["label"] or f"档案 #{row['id']}") if delivery else f"技师 {row['label']}",
                           **states[str(row["id"])]} for row in cur.fetchall() if str(row["id"]) in states]
        return {"scopes": scopes, "venues": venues, "subscribed_people": subscribed, "people": people}
    finally:
        conn.close()


def save(data, user_id):
    from customer_records import RecordConflict
    from staff_customer_duty import save as save_person
    kind, ident, enabled, request_id = (data.get(key) for key in ("kind", "id", "enabled", "request_id"))
    if (type(kind) is not str or kind not in {"venue", "delivery", "news", "staff", "person"} or type(ident) is not int
            or type(enabled) is not bool or not isinstance(request_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", request_id)):
        raise ValueError("订阅参数无效")
    if kind in {"staff", "person"}:
        return save_person({("person_id" if kind == "person" else "staff_id"): ident,
                            "enabled": enabled, "revision": data.get("revision"), "request_id": request_id},
                           user_id, delivery=kind == "person")
    if ((kind == "venue" and ident not in SUBSCRIBE_VENUES)
            or (kind == "delivery" and ident not in DELIVERY_VENUE_IDS) or (kind == "news" and ident != 0)):
        raise ValueError("订阅分类无效")
    digest = hashlib.sha256(json.dumps([kind, ident, enabled]).encode()).hexdigest()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT IGNORE INTO subscription_center_requests VALUES (%s,%s,%s,%s)",
                        (request_id, user_id, digest, datetime.now()))
            if not cur.rowcount:
                cur.execute("SELECT * FROM subscription_center_requests WHERE request_id=%s", (request_id,))
                old = cur.fetchone()
                if old["user_id"] != user_id or old["request_hash"] != digest:
                    raise RecordConflict("操作编号冲突，请刷新核对")
            elif kind == "venue":
                if enabled:
                    cur.execute("INSERT IGNORE INTO bot_subscriptions(tg_user_id,venue_id) VALUES (%s,%s)",
                                (user_id, ident))
                else:
                    cur.execute("DELETE FROM bot_subscriptions WHERE tg_user_id=%s AND venue_id=%s", (user_id, ident))
            elif kind == "delivery":
                if enabled:
                    cur.execute("INSERT IGNORE INTO delivery_scope_subscriptions VALUES (%s,%s,%s)",
                                (ident, user_id, datetime.now()))
                else:
                    cur.execute("DELETE FROM delivery_scope_subscriptions WHERE venue_id=%s AND user_id=%s", (ident, user_id))
                    cur.execute("UPDATE delivery_scope_notifications SET status='cancelled'"
                                " WHERE venue_id=%s AND user_id=%s AND status='queued'", (ident, user_id))
            elif enabled:
                cur.execute("SELECT COALESCE(MAX(id),0) AS latest FROM channel_announcements")
                latest = cur.fetchone()["latest"]
                cur.execute("""INSERT INTO news_subscriptions(user_id,tenant_slug,active,start_id)
                    VALUES (%s,'',1,%s) ON DUPLICATE KEY UPDATE
                    start_id=IF(active=1 AND tenant_slug='',start_id,VALUES(start_id)),tenant_slug='',active=1""",
                            (user_id, latest))
            else:
                cur.execute("UPDATE news_subscriptions SET active=0 WHERE user_id=%s", (user_id,))
                cur.execute("UPDATE news_deliveries SET status='skipped',error='Unsubscribed'"
                            " WHERE user_id=%s AND status='queued'", (user_id,))
        conn.commit()
        return {"saved": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delivery_publication(cur, venue_id, start, end, after):
    from delivery_customer_duty import eligible, published_at
    from delivery_posts import public_person_media
    cur.execute("""SELECT m.* FROM delivery_media m JOIN delivery_people p ON p.id=m.person_id
        WHERE p.venue_id=%s AND m.venue_id=p.venue_id AND p.deleted_at IS NULL
        AND p.merged_into_id IS NULL AND p.consent_status='granted'
        AND m.media_type IN ('photo','video') ORDER BY m.message_id DESC,m.id DESC""", (venue_id,))
    groups = {}
    for row in cur.fetchall():
        groups.setdefault(row["person_id"], []).append(row)
    for rows in groups.values():
        member = {"venue_id": venue_id, "deleted_at": None, "merged_into_id": None,
                  "consent_status": "granted", "channel_media": rows, "public_media": public_person_media(rows)}
        if eligible(member) and published_at(member, start, end, after):
            return True
    return False


def deliver_scopes(bot):
    start, end, shift = current_shift_window()
    if shift != "晚班":
        return
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('delivery_scope_notify',0) AS acquired")
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                return
            cur.execute("SELECT s.*,v.name FROM delivery_scope_subscriptions s"
                        " JOIN venues v ON v.id=s.venue_id ORDER BY s.venue_id,s.user_id")
            subscriptions = cur.fetchall()
            for sub in subscriptions:
                cur.execute("SELECT status,retry_at FROM delivery_scope_notifications"
                            " WHERE venue_id=%s AND user_id=%s AND duty_date=%s",
                            (sub["venue_id"], sub["user_id"], start.date()))
                previous = cur.fetchone()
                if previous and (previous["status"] != "queued"
                                 or (previous["retry_at"] and previous["retry_at"] > datetime.now())):
                    continue
                if not delivery_publication(cur, sub["venue_id"], start, end, sub["subscribed_at"]):
                    continue
                cur.execute("SELECT subscribed_at FROM delivery_scope_subscriptions"
                            " WHERE venue_id=%s AND user_id=%s FOR UPDATE", (sub["venue_id"], sub["user_id"]))
                latest = cur.fetchone()
                if (not latest or latest["subscribed_at"] != sub["subscribed_at"]
                        or current_shift_window()[0] != start):
                    conn.commit()
                    continue
                if previous:
                    cur.execute("UPDATE delivery_scope_notifications SET status='sending',retry_at=NULL"
                                " WHERE venue_id=%s AND user_id=%s AND duty_date=%s AND status='queued'",
                                (sub["venue_id"], sub["user_id"], start.date()))
                else:
                    cur.execute("INSERT IGNORE INTO delivery_scope_notifications"
                                " (venue_id,user_id,duty_date,status) VALUES (%s,%s,%s,'sending')",
                                (sub["venue_id"], sub["user_id"], start.date()))
                claimed = cur.rowcount == 1
                conn.commit()
                if not claimed:
                    continue
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("查看外送出勤", url="https://t.me/haotianzhaopian_bot?startapp=delivery"))
                markup.add(types.InlineKeyboardButton("管理订阅", url="https://t.me/haotianzhaopian_bot?startapp=subscriptions"))
                try:
                    bot.send_message(sub["user_id"], f"{sub['name']} · 晚班出勤已更新\n"
                                     "收到新的公开照片出勤发布，不代表实时在岗。", reply_markup=markup, timeout=20)
                except apihelper.ApiTelegramException as error:
                    if error.error_code == 429:
                        seconds = max(1, int(error.result_json.get("parameters", {}).get("retry_after", 30)))
                        cur.execute("UPDATE delivery_scope_notifications SET status='queued',retry_at=%s"
                                    " WHERE venue_id=%s AND user_id=%s AND duty_date=%s",
                                    (datetime.now() + timedelta(seconds=seconds),
                                     sub["venue_id"], sub["user_id"], start.date()))
                        conn.commit()
                        logger.warning("Delivery scope rate limited; waiting %s seconds", seconds)
                        return
                    logger.exception("Delivery scope notification rejected; not resent")
                    status = "unconfirmed"
                except (apihelper.ApiException, requests.RequestException, ConnectionError, TimeoutError):
                    logger.exception("Delivery scope notification outcome unknown; not resent")
                    status = "unconfirmed"
                else:
                    status = "sent"
                cur.execute("UPDATE delivery_scope_notifications SET status=%s"
                            " WHERE venue_id=%s AND user_id=%s AND duty_date=%s",
                            (status, sub["venue_id"], sub["user_id"], start.date()))
                conn.commit()
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK('delivery_scope_notify')")
        conn.close()


def start_worker(bot):
    ensure_tables()

    def worker():
        while True:
            try:
                deliver_scopes(bot)
            except (pymysql.MySQLError, ValueError):
                logger.exception("Delivery scope subscriptions unavailable; retrying in 15 seconds")
            time.sleep(15)

    threading.Thread(target=worker, daemon=True, name="delivery-scope-subscriptions").start()
    logger.warning("Subscription center READY: delivery categories checked every 15 seconds")
