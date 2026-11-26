"""Channel invitation rewards and consumption vouchers."""

import calendar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import logging
import secrets
import threading
import time

import pymysql
import requests
from telebot import apihelper, types
from customer_records import RecordConflict, _customer_id, _money, _occurred, _page, _positive_id, _public, _utcnow
from db import get_conn

logger = logging.getLogger(__name__)
CHANNEL_ID = -1002214279762
LOCAL_ZONE = timezone(timedelta(hours=7))
REWARD = Decimal("20.00")
RETENTION = timedelta(days=7)
MAX_POLL_GAP = timedelta(hours=23)


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS coupon_campaign_state (
                id TINYINT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT FALSE,
                enabled_at DATETIME NULL, last_poll DATETIME NULL,
                caught_up BOOLEAN NOT NULL DEFAULT FALSE, request_after DATETIME NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""INSERT IGNORE INTO coupon_campaign_state(id,enabled) VALUES(1,FALSE)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS coupon_invite_links (
                inviter_id BIGINT PRIMARY KEY, invite_link VARCHAR(255) NULL,
                status VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL, error VARCHAR(128) NULL,
                UNIQUE KEY coupon_invite_link (invite_link)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS coupon_invites (
                invitee_id BIGINT PRIMARY KEY, inviter_id BIGINT NOT NULL,
                display_name VARCHAR(128) NOT NULL DEFAULT '', username VARCHAR(64) NOT NULL DEFAULT '',
                joined_at DATETIME NOT NULL, left_at DATETIME NULL,
                state VARCHAR(16) NOT NULL, voucher_id CHAR(32) NULL,
                review_reason VARCHAR(255) NULL, reviewed_by BIGINT NULL,
                INDEX coupon_inviter (inviter_id,state,joined_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS coupon_events (
                update_id BIGINT PRIMARY KEY, recorded_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS coupon_vouchers (
                id CHAR(32) PRIMARY KEY, owner_id BIGINT NOT NULL,
                issued_at DATETIME NOT NULL, expires_at DATETIME NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'available',
                consumption_id CHAR(32) NULL, original_amount DECIMAL(12,2) NULL,
                redeemed_at DATETIME NULL, redeemed_by BIGINT NULL,
                UNIQUE KEY coupon_consumption (consumption_id),
                INDEX coupon_owner (owner_id,issued_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS coupon_notifications (
                id BIGINT AUTO_INCREMENT PRIMARY KEY, event_key VARCHAR(64) NOT NULL UNIQUE,
                owner_id BIGINT NOT NULL, invitee_id BIGINT NULL, voucher_id CHAR(32) NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending', error VARCHAR(128) NULL,
                created_at DATETIME NOT NULL, sent_at DATETIME NULL,
                INDEX coupon_notification_pending (status,id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS coupon_redemption_audit (
                id BIGINT AUTO_INCREMENT PRIMARY KEY, voucher_id CHAR(32) NOT NULL,
                consumption_id CHAR(32) NOT NULL, owner_id BIGINT NOT NULL,
                actor_id BIGINT NOT NULL, action VARCHAR(16) NOT NULL,
                original_amount DECIMAL(12,2) NOT NULL, reason VARCHAR(255) NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL, INDEX coupon_redemption_voucher (voucher_id,id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS coupon_redemption_requests (
                id CHAR(32) PRIMARY KEY, actor_id BIGINT NOT NULL, owner_id BIGINT NOT NULL,
                voucher_id CHAR(32) NOT NULL, consumption_id CHAR(32) NOT NULL,
                original_amount DECIMAL(12,2) NOT NULL, created_at DATETIME NOT NULL,
                INDEX coupon_request_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS coupon_operations (
                request_id CHAR(32) PRIMARY KEY, actor_id BIGINT NOT NULL, action VARCHAR(16) NOT NULL,
                digest CHAR(64) NOT NULL, created_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
    finally:
        conn.close()


def expiry(issued_at):
    """Two local calendar months, clamped to the last day of the target month."""
    local = issued_at.replace(tzinfo=timezone.utc).astimezone(LOCAL_ZONE)
    year, month_index = divmod(local.year * 12 + local.month + 1, 12)
    month = month_index + 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day).astimezone(timezone.utc).replace(tzinfo=None)


def _member(member):
    return member.status in {"member", "administrator", "creator"} or (
        member.status == "restricted" and bool(getattr(member, "is_member", False)))


def _event_time(value):
    return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)


def is_enabled():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled FROM coupon_campaign_state WHERE id=1")
            state = cur.fetchone()
            return bool(state and state["enabled"])
    finally:
        conn.close()


def set_enabled(enabled):
    if type(enabled) is not bool:
        raise ValueError("活动状态无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled FROM coupon_campaign_state WHERE id=1 FOR UPDATE")
            state = cur.fetchone()
            if not state:
                raise ValueError("活动表尚未初始化")
            if bool(state["enabled"]) == enabled:
                conn.rollback()
                return False
            cur.execute("""UPDATE coupon_invites SET state='review',review_reason='活动暂停，留存无法确认'
                WHERE state='pending'""")
            cur.execute("""UPDATE coupon_campaign_state SET enabled=%s,enabled_at=%s,
                last_poll=NULL,caught_up=FALSE WHERE id=1""", (enabled, _utcnow()))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def participant_status(bot, owner_id):
    _positive_id(owner_id, "Telegram ID")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM coupon_campaign_state WHERE id=1 FOR UPDATE")
            state = cur.fetchone()
            if not state:
                raise ValueError("活动表尚未初始化")
            if state["request_after"] and state["request_after"] > _utcnow():
                raise ValueError("频道成员核验暂受限，请稍后重试")
            try:
                member = bot.get_chat_member(CHANNEL_ID, owner_id)
            except apihelper.ApiTelegramException as exc:
                delay = exc.result_json.get("parameters", {}).get("retry_after") if exc.error_code == 429 else None
                delay = delay if type(delay) is int and delay > 0 else 60
                cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                            (_utcnow() + timedelta(seconds=delay + 1),))
                if exc.error_code == 403 and state["enabled"]:
                    cur.execute("""UPDATE coupon_invites SET state='review',review_reason='机器人频道权限不可用'
                        WHERE state='pending'""")
                    cur.execute("UPDATE coupon_campaign_state SET enabled=FALSE,caught_up=FALSE WHERE id=1")
                conn.commit()
                logger.warning("Coupon participant verification rejected: Telegram %s", exc.error_code)
                raise ValueError("暂时无法核实频道成员身份，请稍后重试") from exc
            except (apihelper.ApiException, requests.RequestException) as exc:
                cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                            (_utcnow() + timedelta(minutes=1),))
                conn.commit()
                logger.warning("Coupon participant verification unavailable: %s", type(exc).__name__)
                raise ValueError("暂时无法核实频道成员身份，请稍后重试") from exc
            cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                        (_utcnow() + timedelta(seconds=2),))
        conn.commit()
        return _member(member)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _link_state(cur, now):
    cur.execute("SELECT * FROM coupon_campaign_state WHERE id=1 FOR UPDATE")
    state = cur.fetchone()
    if not state or not state["enabled"]:
        raise ValueError("邀请活动尚未开放")
    if not state["caught_up"] or not state["last_poll"] or now - state["last_poll"] > timedelta(minutes=2):
        raise ValueError("频道监听尚未就绪，请稍后再试")
    return state


def create_link(bot, owner_id):
    _positive_id(owner_id, "Telegram ID")
    now = _utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            state = _link_state(cur, now)
            if state["request_after"] and state["request_after"] > now:
                delay = (state["request_after"] - now).total_seconds()
                if delay <= 2:
                    conn.rollback()
                    time.sleep(delay + 0.05)
                    now = _utcnow()
                    state = _link_state(cur, now)
            if state["request_after"] and state["request_after"] > now:
                raise ValueError("Telegram 请求暂受限制，请稍后再试")
            cur.execute("SELECT * FROM coupon_invite_links WHERE inviter_id=%s FOR UPDATE", (owner_id,))
            link = cur.fetchone()
            if link and link["status"] in {"creating", "uncertain"}:
                raise ValueError("邀请链接创建结果待核实，请联系管理员，勿重复创建")
            if not link:
                cur.execute("""INSERT INTO coupon_invite_links(inviter_id,status,created_at)
                    VALUES(%s,'creating',%s)""", (owner_id, now))
            elif link["status"] != "ready":
                cur.execute("UPDATE coupon_invite_links SET status='creating',error=NULL WHERE inviter_id=%s",
                            (owner_id,))
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM coupon_campaign_state WHERE id=1 FOR UPDATE")
            state = cur.fetchone()
            if not state or not state["enabled"]:
                if not link or link["status"] != "ready":
                    cur.execute("UPDATE coupon_invite_links SET status='failed',error='活动已关闭' WHERE inviter_id=%s",
                                (owner_id,))
                conn.commit()
                raise ValueError("邀请活动尚未开放")
            if state["request_after"] and state["request_after"] > _utcnow():
                if not link or link["status"] != "ready":
                    cur.execute("UPDATE coupon_invite_links SET status='failed',error='请求受限' WHERE inviter_id=%s",
                                (owner_id,))
                conn.commit()
                raise ValueError("Telegram 请求暂受限制，请稍后再试")
            try:
                member = bot.get_chat_member(CHANNEL_ID, owner_id)
            except apihelper.ApiTelegramException as exc:
                delay = exc.result_json.get("parameters", {}).get("retry_after") if exc.error_code == 429 else None
                delay = delay if type(delay) is int and delay > 0 else 60
                cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                            (_utcnow() + timedelta(seconds=delay + 1),))
                if exc.error_code == 403:
                    cur.execute("""UPDATE coupon_invites SET state='review',review_reason='机器人频道权限不可用'
                        WHERE state='pending'""")
                    cur.execute("UPDATE coupon_campaign_state SET enabled=FALSE,caught_up=FALSE WHERE id=1")
                if not link or link["status"] != "ready":
                    cur.execute("UPDATE coupon_invite_links SET status='failed',error=%s WHERE inviter_id=%s",
                                (f"member_check_{exc.error_code}", owner_id))
                conn.commit()
                logger.warning("Coupon participant verification rejected: Telegram %s", exc.error_code)
                raise ValueError("暂时无法核实频道成员身份，请稍后重试") from exc
            except (apihelper.ApiException, requests.RequestException) as exc:
                cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                            (_utcnow() + timedelta(minutes=1),))
                if not link or link["status"] != "ready":
                    cur.execute("UPDATE coupon_invite_links SET status='failed',error=%s WHERE inviter_id=%s",
                                (type(exc).__name__, owner_id))
                conn.commit()
                logger.warning("Coupon participant verification unavailable: %s", type(exc).__name__)
                raise ValueError("暂时无法核实频道成员身份，请稍后重试") from exc
            if not _member(member):
                if not link or link["status"] != "ready":
                    cur.execute("UPDATE coupon_invite_links SET status='failed',error='尚未加入频道' WHERE inviter_id=%s",
                                (owner_id,))
                cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                            (_utcnow() + timedelta(seconds=2),))
                conn.commit()
                raise ValueError("请先加入频道，再回来参加邀请活动")
            if link and link["status"] == "ready":
                cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                            (_utcnow() + timedelta(seconds=2),))
                conn.commit()
                return link["invite_link"]
            # Keep the shared rate-limit lock between the membership check and link creation.
            time.sleep(2)
            try:
                result = bot.create_chat_invite_link(CHANNEL_ID, name=f"reward-{owner_id}")
            except apihelper.ApiTelegramException as exc:
                params = exc.result_json.get("parameters", {})
                delay = params.get("retry_after") if exc.error_code == 429 else None
                if exc.error_code == 429:
                    delay = delay if type(delay) is int and delay > 0 else 60
                    cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                                (_utcnow() + timedelta(seconds=delay + 1),))
                status = "uncertain" if exc.error_code >= 500 else "failed"
                cur.execute("UPDATE coupon_invite_links SET status=%s,error=%s WHERE inviter_id=%s",
                            (status, f"telegram_{exc.error_code}", owner_id))
                conn.commit()
                raise ValueError("邀请链接暂时无法创建，请稍后核实") from exc
            except (apihelper.ApiException, requests.RequestException) as exc:
                cur.execute("UPDATE coupon_invite_links SET status='uncertain',error=%s WHERE inviter_id=%s",
                            (type(exc).__name__, owner_id))
                conn.commit()
                raise ValueError("邀请链接创建结果待核实，请联系管理员，勿重复创建") from exc
            if not result.invite_link:
                raise ValueError("Telegram 未返回邀请链接，请联系管理员核实")
            cur.execute("UPDATE coupon_invite_links SET invite_link=%s,status='ready' WHERE inviter_id=%s",
                        (result.invite_link, owner_id))
            cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                        (_utcnow() + timedelta(seconds=2),))
        conn.commit()
        return result.invite_link
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_updates(updates, now=None):
    """Persist membership events before the polling offset is acknowledged."""
    now = now or _utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM coupon_campaign_state WHERE id=1 FOR UPDATE")
            state = cur.fetchone()
            if not state or not state["enabled"]:
                conn.rollback()
                return
            baseline = state["last_poll"] or state["enabled_at"]
            gap = bool(baseline and now - baseline > MAX_POLL_GAP)
            uncertain_backlog = gap or not state["caught_up"]
            if gap:
                cur.execute("""UPDATE coupon_invites SET state='review',review_reason='监听中断，留存无法确认'
                    WHERE state='pending'""")
            for update in sorted(updates, key=lambda item: item.update_id):
                own_membership = update.my_chat_member
                if (own_membership and own_membership.chat.id == CHANNEL_ID and state["enabled_at"]
                        and _event_time(own_membership.date) >= state["enabled_at"]):
                    member = own_membership.new_chat_member
                    if member.status != "administrator" or not getattr(member, "can_invite_users", False):
                        cur.execute("""UPDATE coupon_invites SET state='review',
                            review_reason='机器人频道权限变更，留存无法确认' WHERE state='pending'""")
                        cur.execute("UPDATE coupon_campaign_state SET enabled=FALSE,caught_up=FALSE WHERE id=1")
                        conn.commit()
                        return
                event = update.chat_member
                if not event or event.chat.id != CHANNEL_ID:
                    continue
                cur.execute("INSERT IGNORE INTO coupon_events(update_id,recorded_at) VALUES(%s,%s)",
                            (update.update_id, now))
                if cur.rowcount != 1:
                    continue
                date = _event_time(event.date)
                if not state["enabled_at"] or date < state["enabled_at"]:
                    continue
                old_member, new_member = _member(event.old_chat_member), _member(event.new_chat_member)
                user = event.new_chat_member.user
                if user.is_bot or old_member == new_member:
                    continue
                if new_member:
                    link = getattr(event, "invite_link", None)
                    if not link:
                        continue
                    cur.execute("SELECT inviter_id FROM coupon_invite_links "
                                "WHERE invite_link=%s AND status='ready'", (link.invite_link,))
                    inviter = cur.fetchone()
                    if not inviter or inviter["inviter_id"] == user.id:
                        continue
                    name = " ".join(part for part in (user.first_name, user.last_name) if part)
                    cur.execute("""INSERT IGNORE INTO coupon_invites
                        (invitee_id,inviter_id,display_name,username,joined_at,state,review_reason)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                                (user.id, inviter["inviter_id"], name[:128], (user.username or "")[:64], date,
                                 "review" if uncertain_backlog else "pending",
                                 "监听尚未追平，留存无法确认" if uncertain_backlog else None))
                    if cur.rowcount == 1:
                        cur.execute("""INSERT IGNORE INTO coupon_notifications
                            (event_key,owner_id,invitee_id,created_at)
                            VALUES(%s,%s,%s,%s)""",
                                    (f"join_{update.update_id}", inviter["inviter_id"], user.id, now))
                else:
                    cur.execute("SELECT * FROM coupon_invites WHERE invitee_id=%s FOR UPDATE", (user.id,))
                    invitation = cur.fetchone()
                    if invitation and date >= invitation["joined_at"] and invitation["left_at"] is None:
                        next_state = invitation["state"]
                        if next_state in {"pending", "qualified"}:
                            next_state = "qualified" if date >= invitation["joined_at"] + RETENTION else "left"
                        if next_state == "left" and invitation["voucher_id"]:
                            cur.execute("SELECT status FROM coupon_vouchers WHERE id=%s FOR UPDATE",
                                        (invitation["voucher_id"],))
                            voucher = cur.fetchone()
                            if voucher and voucher["status"] == "available":
                                cur.execute("UPDATE coupon_vouchers SET status='invalid' WHERE id=%s",
                                            (invitation["voucher_id"],))
                                cur.execute("""UPDATE coupon_invites SET voucher_id=NULL
                                    WHERE voucher_id=%s AND invitee_id<>%s""",
                                            (invitation["voucher_id"], user.id))
                            else:
                                next_state = "review"
                                cur.execute("""UPDATE coupon_invites SET review_reason=%s WHERE invitee_id=%s""",
                                            ("迟到的退出记录涉及已使用优惠券，需人工核实", user.id))
                        cur.execute("UPDATE coupon_invites SET left_at=%s,state=%s WHERE invitee_id=%s",
                                    (date, next_state, user.id))
                        cur.execute("""INSERT IGNORE INTO coupon_notifications
                            (event_key,owner_id,invitee_id,created_at)
                            VALUES(%s,%s,%s,%s)""",
                                    (f"left_{update.update_id}", invitation["inviter_id"], user.id, now))
            cur.execute("UPDATE coupon_campaign_state SET last_poll=%s,caught_up=%s WHERE id=1",
                        (now, len(updates) < 100))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def issue_ready(now=None):
    """Issue at most one voucher per call from five independently qualified invitations."""
    now = now or _utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM coupon_campaign_state WHERE id=1 FOR UPDATE")
            state = cur.fetchone()
            if (not state or not state["enabled"] or not state["caught_up"] or not state["last_poll"]
                    or now - state["last_poll"] > timedelta(minutes=2)):
                conn.rollback()
                return None
            cur.execute("""SELECT inviter_id FROM coupon_invites WHERE state='qualified' AND voucher_id IS NULL
                GROUP BY inviter_id HAVING COUNT(*)>=5 ORDER BY MIN(joined_at) LIMIT 1""")
            inviter = cur.fetchone()
            if not inviter:
                conn.commit()
                return None
            cur.execute("""SELECT invitee_id FROM coupon_invites
                WHERE inviter_id=%s AND state='qualified' AND voucher_id IS NULL
                ORDER BY joined_at,invitee_id LIMIT 5 FOR UPDATE""", (inviter["inviter_id"],))
            invited = cur.fetchall()
            if len(invited) != 5:
                raise RecordConflict("邀请人数发生变化，请重试")
            voucher_id = secrets.token_hex(16)
            cur.execute("""INSERT INTO coupon_vouchers(id,owner_id,issued_at,expires_at)
                VALUES(%s,%s,%s,%s)""", (voucher_id, inviter["inviter_id"], now, expiry(now)))
            cur.executemany("UPDATE coupon_invites SET voucher_id=%s WHERE invitee_id=%s",
                            [(voucher_id, row["invitee_id"]) for row in invited])
            cur.execute("""INSERT INTO coupon_notifications(event_key,owner_id,voucher_id,created_at)
                VALUES(%s,%s,%s,%s)""", (f"voucher_{voucher_id}", inviter["inviter_id"], voucher_id, now))
        conn.commit()
        return voucher_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verify_one(bot):
    """Verify one due member at a time; Telegram failures never imply retention."""
    now = _utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM coupon_campaign_state WHERE id=1 FOR UPDATE")
            state = cur.fetchone()
            if (not state or not state["enabled"] or not state["caught_up"] or not state["last_poll"]
                    or now - state["last_poll"] > timedelta(minutes=2)
                    or (state["request_after"] and state["request_after"] > now)):
                conn.rollback()
                return False
            cur.execute("""SELECT invitee_id FROM coupon_invites
                WHERE state='pending' AND joined_at<=%s ORDER BY joined_at,invitee_id LIMIT 1 FOR UPDATE""",
                        (now - RETENTION,))
            invitation = cur.fetchone()
            if not invitation:
                conn.rollback()
                return False
            try:
                member = bot.get_chat_member(CHANNEL_ID, invitation["invitee_id"])
            except apihelper.ApiTelegramException as exc:
                delay = exc.result_json.get("parameters", {}).get("retry_after") if exc.error_code == 429 else None
                if exc.error_code == 429:
                    delay = delay if type(delay) is int and delay > 0 else 60
                    cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                                (_utcnow() + timedelta(seconds=delay + 1),))
                elif exc.error_code >= 500:
                    cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                                (_utcnow() + timedelta(minutes=1),))
                elif exc.error_code == 403:
                    cur.execute("""UPDATE coupon_invites SET state='review',review_reason='机器人频道权限不可用'
                        WHERE state='pending'""")
                    cur.execute("UPDATE coupon_campaign_state SET enabled=FALSE,caught_up=FALSE WHERE id=1")
                else:
                    cur.execute("""UPDATE coupon_invites SET state='review',review_reason=%s WHERE invitee_id=%s""",
                                (f"Telegram 成员核验失败（{exc.error_code}）", invitation["invitee_id"]))
                conn.commit()
                logger.warning("Coupon member verification rejected: Telegram %s", exc.error_code)
                return True
            except (apihelper.ApiException, requests.RequestException) as exc:
                cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                            (_utcnow() + timedelta(minutes=1),))
                conn.commit()
                logger.warning("Coupon member verification unavailable: %s", type(exc).__name__)
                return True
            if _member(member):
                cur.execute("UPDATE coupon_invites SET state='qualified' WHERE invitee_id=%s",
                            (invitation["invitee_id"],))
            else:
                cur.execute("""UPDATE coupon_invites SET state='review',review_reason='当前不是频道成员，退出时间未知'
                    WHERE invitee_id=%s""", (invitation["invitee_id"],))
            cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                        (_utcnow() + timedelta(seconds=5),))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _work_cycle(bot, notify_first):
    if notify_first:
        notify_one(bot)
    verify_one(bot)
    issue_ready()
    if not notify_first:
        notify_one(bot)


def start_worker(bot):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE coupon_notifications SET status='uncertain',error='发送中断' WHERE status='sending'")
        conn.commit()
    finally:
        conn.close()

    def run():
        notify_first = False
        while True:
            notify_first = not notify_first
            try:
                _work_cycle(bot, notify_first)
            except (pymysql.MySQLError, OSError, ValueError):
                logger.exception("Coupon campaign worker failed; retrying later")
            time.sleep(10)

    thread = threading.Thread(target=run, name="coupon-campaign", daemon=True)
    thread.start()
    return thread


def notify_one(bot):
    now = _utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM coupon_campaign_state WHERE id=1 FOR UPDATE")
            state = cur.fetchone()
            if not state or not state["enabled"] or (state["request_after"] and state["request_after"] > now):
                conn.rollback()
                return False
            cur.execute("SELECT * FROM coupon_notifications WHERE status='pending' ORDER BY id LIMIT 1 FOR UPDATE")
            job = cur.fetchone()
            if not job:
                conn.rollback()
                return False
            if job["invitee_id"]:
                cur.execute("SELECT display_name,username,joined_at,left_at,state FROM coupon_invites WHERE invitee_id=%s",
                            (job["invitee_id"],))
                invitation = cur.fetchone()
                if not invitation:
                    raise ValueError("邀请提醒缺少记录")
                name = " ".join(invitation["display_name"].split()) or "未记录名称"
                username = f"（@{invitation['username']}）" if invitation["username"] else ""
                if job["event_key"].startswith("join_"):
                    when = (invitation["joined_at"] + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M")
                    text = (f"邀请进度提醒：{name}{username} 于 {when}（柬埔寨时间）通过你的链接加入频道。"
                            "连续留存7天并通过核验后才计入奖励，当前状态请查看邀请进度。")
                else:
                    if not invitation["left_at"]:
                        raise ValueError("邀请退出提醒缺少退出时间")
                    when = (invitation["left_at"] + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M")
                    retention = {"left": "未满足连续留存7天", "qualified": "已满足连续留存7天，资格保留"}.get(
                        invitation["state"], "留存情况待人工核实")
                    text = f"邀请进度提醒：{name}{username} 于 {when}（柬埔寨时间）退出频道，{retention}。"
            else:
                cur.execute("SELECT expires_at,status FROM coupon_vouchers WHERE id=%s", (job["voucher_id"],))
                voucher = cur.fetchone()
                if not voucher:
                    raise ValueError("优惠券提醒缺少记录")
                if voucher["status"] != "available" or voucher["expires_at"] <= now:
                    cur.execute("UPDATE coupon_notifications SET status='cancelled',error='优惠券不再可用' WHERE id=%s",
                                (job["id"],))
                    conn.commit()
                    return True
                when = (voucher["expires_at"] + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M")
                text = f"恭喜获得一张 $20 代金券！有效期至 {when}（柬埔寨时间），登记符合条件的新消费时自动抵用。"
            cur.execute("UPDATE coupon_notifications SET status='sending' WHERE id=%s", (job["id"],))
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM coupon_campaign_state WHERE id=1 FOR UPDATE")
            state = cur.fetchone()
            if not state or not state["enabled"]:
                cur.execute("UPDATE coupon_notifications SET status='cancelled',error='活动已关闭' WHERE id=%s",
                            (job["id"],))
                conn.commit()
                return True
            if state["request_after"] and state["request_after"] > _utcnow():
                cur.execute("UPDATE coupon_notifications SET status='pending' WHERE id=%s", (job["id"],))
                conn.commit()
                return True
            if job["voucher_id"]:
                cur.execute("SELECT status,expires_at FROM coupon_vouchers WHERE id=%s FOR UPDATE",
                            (job["voucher_id"],))
                voucher = cur.fetchone()
                if not voucher or voucher["status"] != "available" or voucher["expires_at"] <= _utcnow():
                    cur.execute("UPDATE coupon_notifications SET status='cancelled',error='优惠券不再可用' WHERE id=%s",
                                (job["id"],))
                    conn.commit()
                    return True
            try:
                keyboard = types.InlineKeyboardMarkup()
                if job["voucher_id"]:
                    keyboard.add(types.InlineKeyboardButton("查看优惠券", callback_data="coupon:vouchers"))
                else:
                    keyboard.add(types.InlineKeyboardButton("查看邀请进度", callback_data="coupon:mine"))
                bot.send_message(job["owner_id"], text, reply_markup=keyboard)
            except apihelper.ApiTelegramException as exc:
                delay = exc.result_json.get("parameters", {}).get("retry_after") if exc.error_code == 429 else None
                status = "pending" if type(delay) is int and delay > 0 else (
                    "uncertain" if exc.error_code >= 500 else "failed")
                error = f"telegram_{exc.error_code}"
                if exc.error_code == 429:
                    delay = delay if type(delay) is int and delay > 0 else 60
                    cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                                (_utcnow() + timedelta(seconds=delay + 1),))
                logger.warning("Coupon notification rejected: %s", error)
            except (apihelper.ApiException, requests.RequestException) as exc:
                status, error = "uncertain", type(exc).__name__
                logger.warning("Coupon notification outcome uncertain: %s", error)
            else:
                status, error = "sent", None
                cur.execute("UPDATE coupon_campaign_state SET request_after=%s WHERE id=1",
                            (_utcnow() + timedelta(seconds=5),))
            cur.execute("""UPDATE coupon_notifications SET status=%s,error=%s,sent_at=%s
                WHERE id=%s AND status='sending'""",
                        (status, error, _utcnow() if status == "sent" else None, job["id"]))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _redemption_arguments(owner_id, voucher_id, consumption_id, original_amount, actor_id):
    _positive_id(owner_id, "Telegram ID")
    _positive_id(actor_id, "管理员 ID")
    for value in (voucher_id, consumption_id):
        if not isinstance(value, str) or len(value) != 32 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("优惠券或消费记录编号无效")
    original = _money(original_amount, "消费原价")
    if original < REWARD:
        raise ValueError("消费不足20美元，请暂不核销并人工处理")
    return original


def _eligible_coupon(cur, customer_id, service_kind, venue_id, occurred, now, lock=False):
    if service_kind not in {"venue", "delivery"} or type(venue_id) is not int or venue_id <= 0:
        raise ValueError("请选择有效消费来源")
    if service_kind == "venue" and venue_id == 1:
        return None
    cur.execute("SELECT telegram_user_id FROM crm_customers WHERE user_id=%s", (customer_id,))
    customer = cur.fetchone()
    if not customer or not customer["telegram_user_id"]:
        return None
    cur.execute("""SELECT id,owner_id,expires_at FROM coupon_vouchers
        WHERE owner_id=%s AND status='available' AND issued_at<=%s AND expires_at>%s AND expires_at>%s
        ORDER BY expires_at,issued_at,id LIMIT 1""" + (" FOR UPDATE" if lock else ""),
                (customer["telegram_user_id"], occurred, occurred, now))
    return cur.fetchone()


def preview(data):
    customer_id = _customer_id(data.get("user_id"))
    original = _money(data.get("original_amount"), "消费原价")
    if original <= 0:
        raise ValueError("消费原价必须大于0")
    occurred = _occurred(data.get("occurred_at"))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            coupon = _eligible_coupon(cur, customer_id, data.get("service_kind"),
                                      data.get("venue_id"), occurred, _utcnow())
        hold = bool(coupon and original < REWARD)
        if hold:
            coupon = None
        return _public({"coupon_id": coupon["id"] if coupon else None,
                        "expires_at": coupon["expires_at"] if coupon else None,
                        "original_amount": original, "payable": original - REWARD if coupon else original,
                        "manual_review": hold})
    finally:
        conn.close()


def automatic_redemption(cur, data, customer_id, service_kind, venue_id, occurred, amount, now):
    candidate = _eligible_coupon(cur, customer_id, service_kind, venue_id, occurred, now, lock=True)
    if data.get("coupon_previewed") is not True:
        if candidate:
            raise RecordConflict("客户有可用代金券，请更新管理台并重新核对消费原价")
        return None
    original = _money(data.get("original_amount"), "消费原价")
    if original <= 0:
        raise ValueError("消费原价必须大于0")
    if original < REWARD:
        candidate = None
    coupon_id = candidate["id"] if candidate else None
    if data.get("coupon_expected_id") != coupon_id:
        raise RecordConflict("优惠券状态已变化，请重新核对消费")
    if amount != original - (REWARD if coupon_id else Decimal("0")):
        raise RecordConflict("折后实付金额已变化，请重新核对消费")
    return (candidate, original) if candidate else None


def apply_automatic_redemption(cur, coupon, original, record_id, actor_id, now):
    cur.execute("""UPDATE coupon_vouchers SET status='redeemed',consumption_id=%s,
        original_amount=%s,redeemed_at=%s,redeemed_by=%s
        WHERE id=%s AND status='available'""", (record_id, original, now, actor_id, coupon["id"]))
    if cur.rowcount != 1:
        raise RecordConflict("优惠券状态已变化，请重新核对消费")
    cur.execute("""INSERT INTO coupon_redemption_audit
        (voucher_id,consumption_id,owner_id,actor_id,action,original_amount,created_at)
        VALUES(%s,%s,%s,%s,'redeem',%s,%s)""",
                (coupon["id"], record_id, coupon["owner_id"], actor_id, original, now))


def _operation(cur, request_id, actor_id, action, payload):
    if not isinstance(request_id, str) or len(request_id) != 32 or any(ch not in "0123456789abcdef" for ch in request_id):
        raise ValueError("操作编号无效，请重新核对")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    cur.execute("SELECT actor_id,action,digest FROM coupon_operations WHERE request_id=%s FOR UPDATE", (request_id,))
    prior = cur.fetchone()
    if prior and (prior["actor_id"], prior["action"], prior["digest"]) != (actor_id, action, digest):
        raise RecordConflict("操作编号已用于其他内容，请刷新核对")
    return digest, bool(prior)


def _save_operation(cur, request_id, actor_id, action, digest, now):
    cur.execute("""INSERT INTO coupon_operations(request_id,actor_id,action,digest,created_at)
        VALUES(%s,%s,%s,%s,%s)""", (request_id, actor_id, action, digest, now))


def _validate_redemption(cur, owner_id, voucher_id, consumption_id, original, actor_id, now):
    cur.execute("SELECT * FROM coupon_vouchers WHERE id=%s FOR UPDATE", (voucher_id,))
    voucher = cur.fetchone()
    if not voucher or voucher["owner_id"] != owner_id:
        raise ValueError("优惠券不存在或不属于该客户")
    if voucher["status"] == "redeemed":
        if (voucher["consumption_id"] == consumption_id and voucher["original_amount"] == original
                and voucher["redeemed_by"] == actor_id):
            return None
        raise RecordConflict("优惠券已核销，请先核对记录")
    if voucher["status"] != "available" or now >= voucher["expires_at"]:
        raise ValueError("优惠券已过期或不可使用")
    cur.execute("""SELECT r.*,c.telegram_user_id FROM crm_consumptions r
        JOIN crm_customers c ON c.user_id=r.user_id WHERE r.id=%s FOR UPDATE""", (consumption_id,))
    record = cur.fetchone()
    if not record or record["telegram_user_id"] != owner_id:
        raise ValueError("消费记录不存在或不属于该客户")
    if record["status"] != "valid" or record["refunded"] != 0:
        raise ValueError("仅可核销未退款的有效消费")
    if record["venue_id"] == 1 or record["service_kind"] not in {"venue", "delivery"}:
        raise ValueError("该消费来源不支持优惠券")
    if record["occurred_at"] < voucher["issued_at"] or record["occurred_at"] >= voucher["expires_at"]:
        raise ValueError("消费时间不在优惠券有效期内")
    if record["amount"] != original - REWARD:
        raise ValueError("消费记录的实付金额必须等于原价减20美元")
    cur.execute("SELECT id FROM coupon_vouchers WHERE consumption_id=%s FOR UPDATE", (consumption_id,))
    if cur.fetchone():
        raise RecordConflict("这笔消费已使用优惠券")
    return record


def redeem(owner_id, voucher_id, consumption_id, original_amount, actor_id, confirmed, request_id):
    if confirmed is not True:
        raise ValueError("请先确认客户、消费记录和优惠金额")
    original = _redemption_arguments(owner_id, voucher_id, consumption_id, original_amount, actor_id)
    now = _utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            digest, replayed = _operation(cur, request_id, actor_id, "redeem",
                                          [owner_id, voucher_id, consumption_id, str(original)])
            if replayed:
                conn.rollback()
                return {"ok": True, "replayed": True}
            record = _validate_redemption(cur, owner_id, voucher_id, consumption_id, original, actor_id, now)
            if record is None:
                _save_operation(cur, request_id, actor_id, "redeem", digest, now)
                conn.commit()
                return {"ok": True, "replayed": True}
            cur.execute("""UPDATE coupon_vouchers SET status='redeemed',consumption_id=%s,
                original_amount=%s,redeemed_at=%s,redeemed_by=%s WHERE id=%s""",
                        (consumption_id, original, now, actor_id, voucher_id))
            cur.execute("""INSERT INTO coupon_redemption_audit
                (voucher_id,consumption_id,owner_id,actor_id,action,original_amount,created_at)
                VALUES(%s,%s,%s,%s,'redeem',%s,%s)""",
                        (voucher_id, consumption_id, owner_id, actor_id, original, now))
            _save_operation(cur, request_id, actor_id, "redeem", digest, now)
        conn.commit()
        return {"ok": True, "replayed": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def prepare_redemption(owner_id, voucher_id, consumption_id, original_amount, actor_id):
    original = _redemption_arguments(owner_id, voucher_id, consumption_id, original_amount, actor_id)
    now = _utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            record = _validate_redemption(cur, owner_id, voucher_id, consumption_id, original, actor_id, now)
            if record is None:
                raise RecordConflict("优惠券已核销，请先核对记录")
            cur.execute("SELECT name FROM venues WHERE id=%s", (record["venue_id"],))
            venue = cur.fetchone()
            request_id = secrets.token_hex(16)
            cur.execute("DELETE FROM coupon_redemption_requests WHERE created_at<%s",
                        (now - timedelta(days=1),))
            cur.execute("""INSERT INTO coupon_redemption_requests
                (id,actor_id,owner_id,voucher_id,consumption_id,original_amount,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                        (request_id, actor_id, owner_id, voucher_id, consumption_id, original, now))
        conn.commit()
        return _public({"id": request_id, "owner_id": owner_id, "voucher_id": voucher_id,
                        "consumption_id": consumption_id, "original_amount": original,
                        "paid": record["amount"], "service": record["service"],
                        "venue": venue["name"] if venue else "来源已下架",
                        "occurred_at": record["occurred_at"]})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def confirm_redemption(request_id, actor_id):
    _positive_id(actor_id, "管理员 ID")
    if not isinstance(request_id, str) or len(request_id) != 32 or any(ch not in "0123456789abcdef" for ch in request_id):
        raise ValueError("核销请求编号无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM coupon_redemption_requests WHERE id=%s", (request_id,))
            request = cur.fetchone()
            if not request or request["actor_id"] != actor_id:
                raise ValueError("核销请求不存在或不属于当前管理员")
            if _utcnow() >= request["created_at"] + timedelta(minutes=10):
                raise ValueError("核销确认已过期，请重新核对")
    finally:
        conn.close()
    return redeem(request["owner_id"], request["voucher_id"], request["consumption_id"],
                  str(request["original_amount"]), actor_id, True, request_id)


def reverse_redemption(voucher_id, reason, actor_id, confirmed, request_id):
    _positive_id(actor_id, "管理员 ID")
    if not isinstance(voucher_id, str) or len(voucher_id) != 32 or any(ch not in "0123456789abcdef" for ch in voucher_id):
        raise ValueError("优惠券编号无效")
    if confirmed is not True:
        raise ValueError("请明确确认撤销核销")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 255:
        raise ValueError("请填写撤销原因，最多255字")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            digest, replayed = _operation(cur, request_id, actor_id, "reverse", [voucher_id, reason.strip()])
            if replayed:
                conn.rollback()
                return {"ok": True, "replayed": True}
            cur.execute("SELECT * FROM coupon_vouchers WHERE id=%s FOR UPDATE", (voucher_id,))
            voucher = cur.fetchone()
            if not voucher or voucher["status"] != "redeemed":
                raise RecordConflict("优惠券尚未核销或状态已变化，请刷新")
            cur.execute("SELECT id FROM crm_consumptions WHERE id=%s FOR UPDATE", (voucher["consumption_id"],))
            if not cur.fetchone():
                raise ValueError("原消费记录不存在，请人工核对")
            cur.execute("""INSERT INTO coupon_redemption_audit
                (voucher_id,consumption_id,owner_id,actor_id,action,original_amount,reason,created_at)
                VALUES(%s,%s,%s,%s,'reverse',%s,%s,%s)""",
                        (voucher_id, voucher["consumption_id"], voucher["owner_id"], actor_id,
                         voucher["original_amount"], reason.strip(), _utcnow()))
            cur.execute("UPDATE coupon_vouchers SET status='reversal_pending' WHERE id=%s", (voucher_id,))
            _save_operation(cur, request_id, actor_id, "reverse", digest, _utcnow())
        conn.commit()
        return {"ok": True, "replayed": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def review(invitee_id, approved, reason, actor_id, confirmed, request_id):
    _positive_id(invitee_id, "Telegram ID")
    _positive_id(actor_id, "管理员 ID")
    if type(approved) is not bool or confirmed is not True:
        raise ValueError("请明确确认人工核实结果")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 255:
        raise ValueError("请填写人工核实依据，最多255字")
    now = _utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            digest, replayed = _operation(cur, request_id, actor_id, "review",
                                          [invitee_id, approved, reason.strip()])
            if replayed:
                conn.rollback()
                return {"ok": True, "replayed": True}
            cur.execute("SELECT * FROM coupon_invites WHERE invitee_id=%s FOR UPDATE", (invitee_id,))
            invitation = cur.fetchone()
            if not invitation or invitation["state"] != "review":
                raise RecordConflict("邀请记录不在待核实状态，请刷新")
            if approved and invitation["joined_at"] + RETENTION > now:
                raise ValueError("被邀请人尚未加入满7天")
            if approved and invitation["left_at"] and invitation["left_at"] < invitation["joined_at"] + RETENTION:
                raise ValueError("已记录7天内退出，不能确认留存合格")
            cur.execute("""UPDATE coupon_invites SET state=%s,review_reason=%s,reviewed_by=%s
                WHERE invitee_id=%s""",
                        ("qualified" if approved else "invalid", reason.strip(), actor_id, invitee_id))
            _save_operation(cur, request_id, actor_id, "review", digest, now)
        conn.commit()
        return {"ok": True, "replayed": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mine(owner_id, page=1, voucher_page=1):
    _positive_id(owner_id, "Telegram ID")
    page, voucher_page = _page(page), _page(voucher_page)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled FROM coupon_campaign_state WHERE id=1")
            state = cur.fetchone()
            cur.execute("SELECT status FROM coupon_invite_links WHERE inviter_id=%s", (owner_id,))
            link = cur.fetchone()
            cur.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(state='qualified' AND voucher_id IS NULL),0) AS eligible,
                COALESCE(SUM(state='pending' AND voucher_id IS NULL AND left_at IS NULL),0) AS retaining
                FROM coupon_invites WHERE inviter_id=%s""", (owner_id,))
            counts = cur.fetchone()
            eligible, retaining = int(counts["eligible"]), int(counts["retaining"])
            needed = max(0, 5 - eligible)
            estimated_at = None
            if needed and retaining >= needed:
                cur.execute("""SELECT joined_at FROM coupon_invites WHERE inviter_id=%s
                    AND state='pending' AND voucher_id IS NULL AND left_at IS NULL
                    ORDER BY joined_at,invitee_id LIMIT %s""", (owner_id, needed))
                candidates = cur.fetchall()
                estimated_at = candidates[-1]["joined_at"] + RETENTION
            cur.execute("""SELECT invitee_id,display_name,username,joined_at,left_at,state,review_reason,voucher_id
                FROM coupon_invites WHERE inviter_id=%s ORDER BY joined_at DESC,invitee_id DESC
                LIMIT 20 OFFSET %s""", (owner_id, (page - 1) * 20))
            invites = cur.fetchall()
            now = _utcnow()
            cur.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(status='available' AND issued_at<=%s AND expires_at>%s),0) AS available
                FROM coupon_vouchers WHERE owner_id=%s""", (now, now, owner_id))
            voucher_counts = cur.fetchone()
            cur.execute("""SELECT id,issued_at,expires_at,status,consumption_id,redeemed_at
                FROM coupon_vouchers WHERE owner_id=%s ORDER BY issued_at DESC,id DESC
                LIMIT 20 OFFSET %s""", (owner_id, (voucher_page - 1) * 20))
            vouchers = cur.fetchall()
        return _public({"enabled": bool(state and state["enabled"]), "link": link,
                        "invites": invites, "vouchers": vouchers, "invite_total": counts["total"],
                        "eligible": eligible, "retaining": retaining,
                        "estimated_at": estimated_at, "voucher_total": voucher_counts["total"],
                        "available_voucher_total": int(voucher_counts["available"]),
                        "page": page, "voucher_page": voucher_page})
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    import os
    import telebot

    parser = argparse.ArgumentParser(description="Manage the invitation reward campaign")
    parser.add_argument("action", choices=("enable", "disable", "status"))
    args = parser.parse_args()
    ensure_tables()
    if args.action == "status":
        print("enabled" if is_enabled() else "disabled")
    elif args.action == "disable":
        set_enabled(False)
        print("Campaign disabled; pending retention requires manual review.")
    else:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("Telegram bot token is not configured")
        bot = telebot.TeleBot(token)
        me = bot.get_me()
        time.sleep(2)
        member = bot.get_chat_member(CHANNEL_ID, me.id)
        if member.status != "administrator" or not getattr(member, "can_invite_users", False):
            raise RuntimeError("Bot must be a channel administrator with invitation permission")
        set_enabled(True)
        print("Campaign enabled. Restart the main bot before sharing links.")
