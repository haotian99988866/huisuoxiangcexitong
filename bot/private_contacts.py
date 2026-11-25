"""Opt-in reminders for new private messages to the owner's Telegram account."""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import threading
import time

import pymysql
import requests
from telebot import apihelper
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import customer_records
from db import get_conn
from visit_stats import admin_ids

logger = logging.getLogger(__name__)
LOCAL_ZONE = timezone(timedelta(hours=7))


def settings():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_id,enabled,enabled_at FROM crm_private_settings WHERE id=1")
            return cur.fetchone()
    finally:
        conn.close()


def _enabled(cur, owner_id, *, lock=False):
    query = "SELECT owner_id,enabled,enabled_at,send_not_before FROM crm_private_settings WHERE id=1"
    cur.execute(query + (" FOR UPDATE" if lock else ""))
    row = cur.fetchone()
    return row if row and row["enabled"] and row["owner_id"] == owner_id else None


def record_contact(owner_id, sender_id, username, display_name, message_date):
    """Persist identity and a daily notice; never persist message text or media."""
    customer_records._positive_id(sender_id, "Telegram ID")
    if sender_id == owner_id or owner_id not in admin_ids():
        raise ValueError("Invalid or unauthorized private-contact owner")
    if message_date.tzinfo is None:
        raise ValueError("Private message timestamp must have a timezone")
    for attempt in range(3):
        try:
            return _record_contact(owner_id, sender_id, username, display_name, message_date)
        except pymysql.err.OperationalError as exc:
            if not exc.args or exc.args[0] not in (1205, 1213) or attempt == 2:
                raise
            logger.warning("Private contact database lock conflict; retry %s", attempt + 1)
            time.sleep(0.1 * (attempt + 1))


def _record_contact(owner_id, sender_id, username, display_name, message_date):
    when = message_date.astimezone(timezone.utc).replace(tzinfo=None)
    day = message_date.astimezone(LOCAL_ZONE).date()
    now = customer_records._utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            config = _enabled(cur, owner_id, lock=True)
            if not config or when < config["enabled_at"]:
                conn.rollback()
                return False
            cur.execute("""INSERT INTO crm_private_contacts
                (telegram_user_id,username,display_name,first_seen,last_seen)
                VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                username=VALUES(username),display_name=VALUES(display_name),
                last_seen=GREATEST(last_seen,VALUES(last_seen))""",
                        (sender_id, (username or "")[:64], (display_name or "")[:128], now, now))
            cur.execute("SELECT user_id FROM crm_customers WHERE telegram_user_id=%s FOR UPDATE",
                        (sender_id,))
            profile = cur.fetchone()
            if profile:
                customer_id = profile["user_id"]
            else:
                cur.execute("SELECT user_id FROM crm_customers WHERE user_id=%s FOR UPDATE", (sender_id,))
                if cur.fetchone():
                    raise customer_records.RecordConflict("客户档案编号冲突，需人工核对")
                customer_id = sender_id
                cur.execute("""INSERT INTO crm_customers
                    (user_id,telegram_user_id,alias,preferences,avoidances,notes,revision,created_at,updated_at)
                    VALUES(%s,%s,'','','','',1,%s,%s)""", (customer_id, sender_id, now, now))
            cur.execute("""INSERT IGNORE INTO crm_private_notices
                (owner_id,telegram_user_id,customer_id,contact_day,created_at,next_attempt)
                VALUES(%s,%s,%s,%s,%s,%s)""", (owner_id, sender_id, customer_id, day, now, now))
            queued = cur.rowcount == 1
        conn.commit()
        return queued
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _plain(value, limit=128):
    return " ".join(str(value or "").split())[:limit]


def _notice_card(cur, customer_id, sender_id):
    cur.execute("""SELECT p.telegram_user_id,p.username,p.display_name
        FROM crm_private_contacts p JOIN crm_customers c ON c.telegram_user_id=p.telegram_user_id
        WHERE c.user_id=%s AND p.telegram_user_id=%s""", (customer_id, sender_id))
    identity = cur.fetchone()
    if not identity:
        raise ValueError("Private contact identity no longer matches the customer profile")
    summary = customer_records._summary(cur, customer_id)
    cur.execute("""SELECT c.service,c.occurred_at,c.amount-c.refunded AS net,
        COALESCE(v.name,IF(c.service_kind='delivery','西港外送','会所已下架')) AS venue
        FROM crm_consumptions c LEFT JOIN venues v ON v.id=c.venue_id
        WHERE c.user_id=%s AND c.status='valid'
        ORDER BY c.occurred_at DESC,c.id DESC LIMIT 3""", (customer_id,))
    rows = cur.fetchall()
    username = "@" + _plain(identity["username"], 64) if identity["username"] else "无用户名"
    lines = [
        "客户私聊提醒",
        f"客户：{_plain(identity['display_name']) or '未记录名称'}（{username}）",
        f"Telegram ID：{identity['telegram_user_id']}",
        f"有效消费：{summary['visits']} 笔 · 净累计 ${summary['net']}",
    ]
    if not rows:
        lines.append("暂无已登记消费记录。")
    for row in rows:
        when = (row["occurred_at"] + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{when} · {_plain(row['venue'])} · {_plain(row['service'])} · ${row['net']}")
    return "\n".join(lines)


def recover_uncertain():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE crm_private_notices SET status='uncertain',error='interrupted'
                WHERE status='sending'""")
        conn.commit()
    finally:
        conn.close()


def deliver_one(bot, owner_id):
    """Claim before sending. Ambiguous failures require manual review, not blind retry."""
    conn = get_conn()
    job = None
    try:
        with conn.cursor() as cur:
            config = _enabled(cur, owner_id, lock=True) if owner_id in admin_ids() else None
            if not config or (config["send_not_before"] and
                              config["send_not_before"] > customer_records._utcnow()):
                conn.rollback()
                return False
            cur.execute("""SELECT * FROM crm_private_notices
                WHERE owner_id=%s AND status='pending' AND next_attempt<=%s
                ORDER BY id LIMIT 1 FOR UPDATE""", (owner_id, customer_records._utcnow()))
            job = cur.fetchone()
            if not job:
                conn.rollback()
                return False
            card = _notice_card(cur, job["customer_id"], job["telegram_user_id"])
            cur.execute("""UPDATE crm_private_notices SET status='sending',attempts=attempts+1
                WHERE id=%s AND status='pending'""", (job["id"],))
        conn.commit()
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(
            "查看客户档案",
            url=f"https://t.me/haotianzhaopian_bot?startapp=customer_{job['customer_id']}",
        ))
        with conn.cursor() as cur:
            config = _enabled(cur, owner_id, lock=True) if owner_id in admin_ids() else None
            if not config:
                cur.execute("""UPDATE crm_private_notices SET status='cancelled',error='disabled'
                    WHERE id=%s AND status='sending'""", (job["id"],))
                conn.commit()
                return True
            if config["send_not_before"] and config["send_not_before"] > customer_records._utcnow():
                cur.execute("""UPDATE crm_private_notices SET status='pending',next_attempt=%s
                    WHERE id=%s AND status='sending'""", (config["send_not_before"], job["id"]))
                conn.commit()
                return True
            try:
                bot.send_message(owner_id, card, reply_markup=keyboard)
            except apihelper.ApiTelegramException as exc:
                params = exc.result_json.get("parameters", {})
                retry_after = params.get("retry_after") if exc.error_code == 429 else None
                if type(retry_after) is int and retry_after > 0:
                    status = "pending"
                else:
                    status = "uncertain" if exc.error_code >= 500 else "failed"
                next_attempt = customer_records._utcnow() + timedelta(seconds=(retry_after or 0) + 1)
                error = f"telegram_{exc.error_code}"
                logger.warning("Private contact notice rejected: %s", error)
                if status == "pending":
                    cur.execute("UPDATE crm_private_settings SET send_not_before=%s WHERE id=1",
                                (next_attempt,))
            except (apihelper.ApiException, requests.RequestException) as exc:
                status, error = "uncertain", type(exc).__name__
                next_attempt = customer_records._utcnow()
                logger.warning("Private contact notice delivery uncertain: %s", error)
            else:
                status, error = "sent", None
                next_attempt = customer_records._utcnow()
            cur.execute("""UPDATE crm_private_notices SET status=%s,error=%s,next_attempt=%s,
                sent_at=%s WHERE id=%s AND status='sending'""",
                        (status, error, next_attempt, customer_records._utcnow() if status == "sent" else None,
                         job["id"]))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def _listen(bot, owner_id):
    from telethon import TelegramClient, errors, events, types
    from channel_sync import credentials, single_instance, state_directory

    directory = state_directory()
    if not directory.is_dir():
        raise RuntimeError("Channel-sync state directory is missing")
    with single_instance(directory, "private-contact.lock"):
        api_id, api_hash = credentials()
        client = TelegramClient(str(directory / "private-contact"), api_id, api_hash, catch_up=False)
        client.session.save_entities = False
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("Private-contact session is not authorized")
            owner = await client.get_me()
            if not owner or owner.bot or owner.id != owner_id or owner_id not in admin_ids():
                raise RuntimeError("Private-contact account does not match the authorized owner")
            started = datetime.now(timezone.utc)
            recover_uncertain()
            sender_lookup_after = 0.0

            async def on_message(event):
                nonlocal sender_lookup_after
                try:
                    message_date = event.message.date
                    if not event.is_private or not message_date or message_date < started:
                        return
                    if time.monotonic() < sender_lookup_after:
                        logger.warning("Private contact skipped during Telegram rate limit")
                        return
                    sender = await event.get_sender()
                    if not isinstance(sender, types.User) or sender.bot or sender.deleted:
                        return
                    if sender.id == owner_id or event.chat_id != sender.id:
                        return
                    name = " ".join(part for part in (sender.first_name, sender.last_name) if part)
                    await asyncio.to_thread(record_contact, owner_id, sender.id, sender.username, name, message_date)
                except errors.FloodWaitError as exc:
                    sender_lookup_after = max(sender_lookup_after, time.monotonic() + exc.seconds + 1)
                    logger.warning("Private contact sender lookup rate limited for %s seconds", exc.seconds)
                except (pymysql.MySQLError, OSError, ValueError, RuntimeError, errors.RPCError):
                    logger.exception("Private contact could not be recorded; manual review required")

            client.add_event_handler(on_message, events.NewMessage(incoming=True))
            logger.warning("Private-contact listener ready; no history is read")
            while client.is_connected():
                config = await asyncio.to_thread(settings)
                if not config or not config["enabled"] or config["owner_id"] != owner_id:
                    logger.warning("Private-contact listener disabled")
                    break
                await asyncio.to_thread(deliver_one, bot, owner_id)
                await asyncio.sleep(5)
        finally:
            await client.disconnect()


def start_if_enabled(bot):
    config = settings()
    if not config or not config["enabled"]:
        return None

    def run():
        try:
            from telethon import errors
        except ImportError:
            logger.exception("Private-contact listener requires Telethon")
            return

        while True:
            try:
                current = settings()
                if not current or not current["enabled"] or current["owner_id"] != config["owner_id"]:
                    return
                asyncio.run(_listen(bot, config["owner_id"]))
            except errors.FloodWaitError as exc:
                delay = max(30, exc.seconds + 1)
                logger.warning("Private-contact listener rate limited; retrying in %s seconds", delay)
                time.sleep(delay)
                continue
            except (OSError, ImportError, RuntimeError, ValueError, pymysql.MySQLError, errors.RPCError):
                logger.exception("Private-contact listener stopped; retrying in 30 seconds")
            time.sleep(30)

    thread = threading.Thread(target=run, name="private-contact-listener", daemon=True)
    thread.start()
    return thread


async def _authorize(owner_username):
    from telethon import TelegramClient
    from channel_sync import credentials, single_instance, state_directory

    directory = state_directory()
    if not directory.is_dir():
        raise RuntimeError("Channel-sync state directory is missing")
    with single_instance(directory, "private-contact.lock"):
        client = TelegramClient(str(directory / "private-contact"), *credentials(), catch_up=False)
        client.session.save_entities = False
        try:
            await client.start()
            owner = await client.get_me()
            if (not owner or owner.bot or not owner.username
                    or owner.username.casefold() != owner_username.lstrip("@").casefold()
                    or owner.id not in admin_ids()):
                raise ValueError("Logged-in account does not match the selected administrator")
            now = customer_records._utcnow()
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""INSERT INTO crm_private_settings(id,owner_id,enabled,enabled_at)
                        VALUES(1,%s,TRUE,%s) ON DUPLICATE KEY UPDATE
                        owner_id=VALUES(owner_id),enabled=TRUE,enabled_at=VALUES(enabled_at)""",
                                (owner.id, now))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            print("Authorized. Restart the main bot when it is safe to do so.")
        finally:
            await client.disconnect()


def disable():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE crm_private_settings SET enabled=FALSE WHERE id=1")
            cur.execute("""UPDATE crm_private_notices SET status='cancelled',error='disabled'
                WHERE status='pending'""")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Authorized personal private-message reminders")
    parser.add_argument("action", choices=("authorize", "disable"))
    parser.add_argument("--owner", help="Expected administrator Telegram username")
    args = parser.parse_args()
    if args.action == "authorize":
        if not args.owner:
            parser.error("--owner is required")
        customer_records.ensure_tables()
        asyncio.run(_authorize(args.owner))
    else:
        disable()
