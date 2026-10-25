"""Explicit opt-in news reminders with a persistent per-recipient outbox."""
import logging
import threading
import time
from datetime import timedelta, timezone
from urllib.parse import urlencode

import pymysql
from telebot import apihelper, types

from announcements import reading_text
from db import get_conn
from tenants import TenantUnavailable, load_tenant

logger = logging.getLogger(__name__)
APP_URL = "https://app.haotianzhushou.com"


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS news_subscriptions (
                user_id BIGINT PRIMARY KEY, tenant_slug VARCHAR(32) NOT NULL DEFAULT '',
                active TINYINT(1) NOT NULL DEFAULT 1, start_id BIGINT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS news_deliveries (
                id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL,
                announcement_id BIGINT NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'queued',
                error VARCHAR(255) NULL, result_id BIGINT NULL,
                retry_at DATETIME NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY news_recipient (user_id,announcement_id),
                INDEX news_pending (status,retry_at,id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
    finally:
        conn.close()


def check_scope(slug):
    if slug and not load_tenant(slug)["announcements_enabled"]:
        raise TenantUnavailable("此入口暂未开放新闻订阅")


def subscription(user_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM news_subscriptions WHERE user_id=%s", (user_id,))
            return cur.fetchone()
    finally:
        conn.close()


def subscribe(user_id, slug=""):
    check_scope(slug)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Duplicate confirmations do not move the baseline or discard pending news.
            cur.execute("SELECT COALESCE(MAX(id),0) AS latest FROM channel_announcements")
            latest = cur.fetchone()["latest"]
            cur.execute(
                "INSERT INTO news_subscriptions (user_id,tenant_slug,active,start_id) VALUES (%s,%s,1,%s)"
                " ON DUPLICATE KEY UPDATE start_id=IF(active=1 AND tenant_slug=VALUES(tenant_slug),"
                "start_id,VALUES(start_id)),tenant_slug=VALUES(tenant_slug),active=1",
                (user_id, slug, latest),
            )
        conn.commit()
    finally:
        conn.close()


def unsubscribe(user_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE news_subscriptions SET active=0 WHERE user_id=%s", (user_id,))
            cur.execute("UPDATE news_deliveries SET status='skipped',error='Unsubscribed'"
                        " WHERE user_id=%s AND status='queued'", (user_id,))
        conn.commit()
    finally:
        conn.close()


def show_menu(bot, chat_id, user_id, slug=""):
    check_scope(slug)
    current = subscription(user_id)
    active = bool(current and current["active"])
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("订阅新资讯", callback_data=f"news:on:{slug or '-'}"))
    if active:
        keyboard.add(types.InlineKeyboardButton("取消新闻订阅", callback_data="news:off"))
    keyboard.add(types.InlineKeyboardButton("返回主菜单", callback_data="menu:home"))
    bot.send_message(
        chat_id,
        "🔔 东南亚大事件订阅\n\n"
        f"当前状态：{'已订阅' if active else '未订阅'}\n"
        "点击“订阅新资讯”确认后，机器人将逐条发送新资讯的标题、时间和详情入口。"
        "不补推已有历史，可随时取消。\n"
        + (f"本次入口：{slug}\n切换入口后以最后一次确认订阅为准。" if slug else "本次入口：主入口"),
        reply_markup=keyboard,
    )


def handle_callback(bot, call):
    if not call.message or call.message.chat.type != "private" or call.message.chat.id != call.from_user.id:
        bot.answer_callback_query(call.id, "请在机器人私聊中操作", show_alert=True)
        return
    try:
        if call.data == "news:off":
            unsubscribe(call.from_user.id)
            bot.answer_callback_query(call.id, "已取消新闻订阅")
            bot.send_message(call.message.chat.id, "已取消东南亚大事件提醒，不再发送新资讯。")
        elif call.data == "news:menu":
            current = subscription(call.from_user.id)
            # Cancellation remains available even after a tenant loses access.
            if current and current["active"]:
                keyboard = types.InlineKeyboardMarkup()
                keyboard.add(types.InlineKeyboardButton("取消新闻订阅", callback_data="news:off"))
                bot.answer_callback_query(call.id)
                bot.send_message(call.message.chat.id, "你已订阅东南亚大事件，可在这里取消。", reply_markup=keyboard)
            else:
                bot.answer_callback_query(call.id)
                show_menu(bot, call.message.chat.id, call.from_user.id)
        elif call.data.startswith("news:on:"):
            slug = call.data.removeprefix("news:on:")
            subscribe(call.from_user.id, "" if slug == "-" else slug)
            bot.answer_callback_query(call.id, "已订阅")
            bot.send_message(call.message.chat.id, "✅ 已订阅东南亚大事件，仅接收此刻之后的新资讯。\n取消请发送 /news 或 /news_off。")
        else:
            bot.answer_callback_query(call.id, "无效操作", show_alert=True)
    except ValueError as error:
        bot.answer_callback_query(call.id, str(error), show_alert=True)
    except pymysql.MySQLError:
        logger.exception("News subscription database operation failed")
        bot.send_message(call.message.chat.id, "订阅服务暂不可用，请稍后发送 /news 核对状态。")


def update_delivery(ident, status, error=None, result_id=None, retry_seconds=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE news_deliveries SET status=%s,error=%s,result_id=%s,"
                "retry_at=IF(%s IS NULL,NULL,DATE_ADD(UTC_TIMESTAMP(),INTERVAL %s SECOND)) WHERE id=%s",
                (status, error, result_id, retry_seconds, retry_seconds, ident),
            )
        conn.commit()
    finally:
        conn.close()


def enqueue_news():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Ineligible tenants are excluded again just before sending.
            cur.execute("""INSERT IGNORE INTO news_deliveries (user_id,announcement_id)
                SELECT s.user_id,a.id FROM news_subscriptions s
                JOIN channel_announcements a ON a.id>s.start_id AND a.enabled=1
                LEFT JOIN display_tenants t ON t.slug=s.tenant_slug
                WHERE s.active=1 AND (s.tenant_slug='' OR
                    (t.status='active' AND t.expires_at>UTC_TIMESTAMP() AND t.announcements_enabled=1))
                AND NOT EXISTS (SELECT 1 FROM news_deliveries d
                    WHERE d.user_id=s.user_id AND d.announcement_id=a.id)
                ORDER BY a.id,s.user_id LIMIT 100""")
        conn.commit()
    finally:
        conn.close()


def claim_delivery():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM news_deliveries WHERE status='queued'"
                        " AND (retry_at IS NULL OR retry_at<=UTC_TIMESTAMP()) ORDER BY id LIMIT 1 FOR UPDATE")
            row = cur.fetchone()
            if not row:
                return None
            cur.execute("UPDATE news_deliveries SET status='sending' WHERE id=%s", (row["id"],))
            cur.execute("""SELECT d.id,d.user_id,a.id AS announcement_id,a.body,a.media,a.published_at,
                s.tenant_slug FROM news_deliveries d
                JOIN news_subscriptions s ON s.user_id=d.user_id AND s.active=1
                JOIN channel_announcements a ON a.id=d.announcement_id AND a.enabled=1 AND a.id>s.start_id
                WHERE d.id=%s""", (row["id"],))
            result = cur.fetchone()
            if not result:
                cur.execute("UPDATE news_deliveries SET status='skipped',error='Subscription or news unavailable'"
                            " WHERE id=%s", (row["id"],))
        conn.commit()
        return result or {"skipped": True}
    finally:
        conn.close()


def send_delivery(bot, row):
    try:
        check_scope(row["tenant_slug"])
    except TenantUnavailable:
        update_delivery(row["id"], "skipped", "Tenant news access unavailable")
        return 0
    except pymysql.MySQLError:
        update_delivery(row["id"], "queued", "Tenant lookup unavailable", retry_seconds=30)
        logger.exception("News delivery tenant check failed; retrying before any send")
        return 0
    import json
    media = json.loads(row["media"]) if isinstance(row["media"], str) else row["media"]
    title = reading_text(row["body"], media)["title"]
    date = row["published_at"].replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=7)))
    url = f"{APP_URL}/announcements/{row['announcement_id']}"
    if row["tenant_slug"]:
        url += "?" + urlencode({"tenant": row["tenant_slug"]})
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("查看详情", web_app=types.WebAppInfo(url)))
    keyboard.add(types.InlineKeyboardButton("取消新闻订阅", callback_data="news:off"))
    try:
        result = bot.send_message(row["user_id"], f"📰 东南亚大事件\n{date:%m/%d %H:%M}（UTC+7）\n\n{title}",
                                  reply_markup=keyboard, disable_web_page_preview=True, timeout=20)
    except apihelper.ApiTelegramException as error:
        code = error.error_code
        if code == 429:
            delay = max(1, int(error.result_json.get("parameters", {}).get("retry_after", 30)))
            update_delivery(row["id"], "queued", "Telegram rate limit", retry_seconds=delay)
            logger.warning("News delivery rate limited; waiting %s seconds", delay)
            return delay
        if code == 403:
            unsubscribe(row["user_id"])
        update_delivery(row["id"], "failed" if code in (400, 403) else "uncertain",
                        f"Telegram error {code}")
        logger.warning("News delivery %s rejected: %s", row["id"], code)
    except Exception:
        update_delivery(row["id"], "uncertain", "Send outcome unknown; inspect before retrying")
        logger.exception("News delivery %s outcome unknown; not automatically resent", row["id"])
    else:
        update_delivery(row["id"], "sent", result_id=result.message_id)
        logger.info("News delivery %s sent", row["id"])
    return 0


def delivery_worker(bot):
    while True:
        try:
            enqueue_news()
            for _ in range(20):
                row = claim_delivery()
                if not row:
                    break
                if not row.get("skipped"):
                    time.sleep(max(.1, send_delivery(bot, row)))
        except Exception:
            logger.exception("News reminder worker failed; inspect delivery states; retrying in 5 seconds")
        time.sleep(5)


def start_worker(bot):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE news_deliveries SET status='uncertain',error='Interrupted during send; inspect manually'"
                        " WHERE status='sending'")
        conn.commit()
    finally:
        conn.close()
    threading.Thread(target=delivery_worker, args=(bot,), daemon=True, name="news-reminders").start()
