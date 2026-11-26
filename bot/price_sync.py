"""Synchronize saved Mini App prices to existing venue channel messages."""

import asyncio
import logging
import threading
import time
from types import SimpleNamespace

import requests
from telebot import apihelper

from config.channels import STAFF_ARCHIVE_VENUES
from db import get_conn

logger = logging.getLogger(__name__)
INTERVAL = 5


class SyncFailure(RuntimeError):
    pass


def _edit(send, message_id):
    while True:
        time.sleep(INTERVAL)
        try:
            result = send()
            if result is not True and getattr(result, "message_id", None) != message_id:
                raise SyncFailure(f"消息 {message_id} 更新结果不明，请核对后重试")
            return result
        except apihelper.ApiTelegramException as error:
            if error.error_code == 400 and "message is not modified" in str(error.description).lower():
                return
            retry = (error.result_json.get("parameters") or {}).get("retry_after")
            if error.error_code != 429 or type(retry) is not int or retry <= 0:
                raise SyncFailure(f"消息 {message_id} 更新失败（Telegram {error.error_code}）") from error
            logger.warning("Price synchronization throttled for %s seconds", retry)
            time.sleep(retry + 1)
        except requests.RequestException as error:
            raise SyncFailure(f"消息 {message_id} 更新结果不明，请核对后重试") from error


def _rows(sql, args=()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def _massage(bot, services):
    import massage_channel as massage
    from price_management import _MASSAGE_NAMES

    posts = _rows("""SELECT p.staff_id,p.code,p.msg_id FROM massage_channel_posts p
                     JOIN staff_members m ON m.id=p.staff_id
                     WHERE p.channel_id=%s AND m.ended_at IS NULL
                       AND m.deleted_at IS NULL AND m.blocked=0
                     ORDER BY p.msg_id""", (massage.CHANNEL_ID,))
    for post in posts:
        names = {row["name"] for row in massage._staff_services(post["staff_id"]) if row.get("name")}
        if not names.intersection(_MASSAGE_NAMES.get(item["service_key"], item["service_name"])
                                  for item in services):
            continue
        text = massage.staff_caption(post["staff_id"], post["code"])
        markup = massage._staff_markup(post["staff_id"], post["code"])
        _edit(lambda: bot.edit_message_caption(text, int(massage.CHANNEL_ID), post["msg_id"],
                                               reply_markup=markup, timeout=20), post["msg_id"])
    attendance_id = massage._state_message_id("attendance")
    if attendance_id:
        attendance = _rows("""SELECT duty_date,duty_shift FROM attendance_daily
                               WHERE venue_id=1 ORDER BY created_at DESC,id DESC LIMIT 1""")
        if not attendance:
            raise SyncFailure("按摩频道出勤消息缺少对应名单")
        time.sleep(INTERVAL)
        try:
            massage.publish_attendance(bot, attendance[0], None)
        except apihelper.ApiTelegramException as error:
            if error.error_code != 400 or "message is not modified" not in str(error.description).lower():
                raise


def _mingxi(bot, services):
    from scripts import mingxi_update_archive

    args = SimpleNamespace(after=0, limit=5, apply=True, all=True)
    boards = {item["service_name"].split("·", 1)[0] for item in services}
    failures = asyncio.run(mingxi_update_archive.main_async(args, bot=bot, boards=boards))
    import mingxi_complete_attendance as mingxi
    from price_catalog import current_catalog

    channel_id = int(mingxi.MINGXI_CHANNEL_ID)
    roster_id = mingxi.get_channel_anchor(channel_id, mingxi.ANCHOR_ROSTER) or mingxi.FULL_ROSTER_MSG_ID
    if roster_id:
        text = mingxi.build_full_roster_html("名玺水汇会所", channel_id, catalog=current_catalog())
        _edit(lambda: bot.edit_message_text(text, channel_id, roster_id, parse_mode="HTML",
                                            disable_web_page_preview=True,
                                            reply_markup=mingxi.footer_keyboard(), timeout=20), roster_id)
    attendance_id = mingxi.get_channel_anchor(channel_id, mingxi.ANCHOR_ATTENDANCE)
    if attendance_id:
        attendance = mingxi.load_latest_mingxi_attendance()
        if not attendance:
            raise SyncFailure("名玺频道出勤消息缺少对应名单")
        codes = list(attendance.get("codes") or [])
        for group in attendance.get("attendance_groups") or []:
            codes.extend(group.get("technician_codes") or [])
        urls, _ = mingxi.resolve_album_links(mingxi.PLATE_VENUE_ID, codes, channel_id=channel_id)
        text, markup = mingxi.build_complete_attendance_content(
            "名玺水汇会所", attendance, channel_id, album_urls=urls, catalog=current_catalog())
        _edit(lambda: bot.edit_message_text(text, channel_id, attendance_id, parse_mode="HTML",
                                            disable_web_page_preview=True,
                                            reply_markup=markup, timeout=20), attendance_id)
    if failures:
        raise SyncFailure("名玺频道部分照片未能更新：" + ", ".join(str(item) for item in failures))


def _club(bot, venue_id, services):
    import footer
    import venue_archive_channel as archive
    from staff_archive import listing

    config = STAFF_ARCHIVE_VENUES[venue_id]
    members = listing({"venue_id": venue_id, "mode": "all"})["members"]
    by_id = {member["id"]: member for member in members}
    posts = _rows(f"SELECT staff_id,message_id FROM {config['table_prefix']}_posts "
                  "WHERE status='sent' AND message_id IS NOT NULL ORDER BY message_id")
    username = bot.get_me().username
    if not username:
        raise SyncFailure("机器人用户名不可用")
    changed = {item["service_name"] for item in services}
    for post in posts:
        member = by_id.get(post["staff_id"])
        if not member:
            continue
        if member["group_name"] not in changed:
            continue
        text = archive.caption(member, venue_id)
        markup = archive.photo_keyboard(member, username, venue_id)
        _edit(lambda: bot.edit_message_caption(text, config["channel_id"], post["message_id"],
                                               reply_markup=markup, timeout=20), post["message_id"])
    conn = get_conn()
    locked = False
    lock_name = f"{config['table_prefix']}_channel"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s,15) AS acquired", (lock_name,))
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                raise SyncFailure("频道正在更新，请稍后重新同步")
            members = listing({"venue_id": venue_id, "mode": "all"})["members"]
            posts = _rows(f"SELECT staff_id,message_id FROM {config['table_prefix']}_posts "
                          "WHERE status='sent' AND message_id IS NOT NULL ORDER BY message_id")
            published = {row["staff_id"]: row["message_id"] for row in posts}
            pages = _rows(f"SELECT page_number,message_id FROM {config['table_prefix']}_pages "
                          "WHERE status='sent' AND message_id IS NOT NULL ORDER BY page_number")
            texts = archive.navigation_pages(members, published, venue_id)
            page_ids = {row["page_number"]: row["message_id"] for row in pages}
            if not all(number in page_ids for number in range(1, len(texts) + 1)):
                raise SyncFailure("人员导航缺少已登记的页面")
            for number, text in enumerate(texts, 1):
                markup = archive.navigation_keyboard(number, len(texts), page_ids, venue_id)
                _edit(lambda: bot.edit_message_text(text, config["channel_id"], page_ids[number],
                                                    parse_mode="HTML", disable_web_page_preview=True,
                                                    reply_markup=markup, timeout=20), page_ids[number])
                cur.execute(f"UPDATE {config['table_prefix']}_pages SET content_hash=%s "
                            "WHERE page_number=%s AND message_id=%s AND status='sent'",
                            (archive._digest(text, markup), number, page_ids[number]))
                conn.commit()
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        conn.close()
    footer_id = footer._get_last(str(config["channel_id"]))
    if footer_id:
        text = footer.footer_text(config["channel_id"])
        _edit(lambda: bot.edit_message_text(text, config["channel_id"], footer_id, parse_mode="HTML",
                                            disable_web_page_preview=True,
                                            reply_markup=footer.footer_keyboard(), timeout=20), footer_id)


def sync_venue(bot, venue_id, services):
    if venue_id == 1:
        _massage(bot, services)
    elif venue_id == 2:
        _mingxi(bot, services)
    elif venue_id in STAFF_ARCHIVE_VENUES:
        _club(bot, venue_id, services)
    else:
        raise ValueError(f"不支持同步频道的会所：{venue_id}")


def run_once(bot):
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('venue_price_sync',0) AS acquired")
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                return False
            cur.execute("""UPDATE venue_price_channel_review
                           SET status='failed',reason='同步中断，请核对后重试'
                           WHERE status='running'""")
            cur.execute("""SELECT revision,venue_id FROM venue_price_channel_review
                           WHERE status='pending' ORDER BY revision,venue_id LIMIT 1 FOR UPDATE""")
            task = cur.fetchone()
            if not task:
                conn.commit()
                return False
            cur.execute("""UPDATE venue_price_channel_review SET status='running',reason='正在同步'
                           WHERE revision=%s AND venue_id=%s""",
                        (task["revision"], task["venue_id"]))
            conn.commit()
            try:
                cur.execute("""SELECT i.service_key,i.service_name FROM venue_price_audit a
                               JOIN finance_commission_items i ON i.owner_id=a.actor_id
                                 AND i.venue_id=a.venue_id AND i.service_key=a.service_key
                               WHERE a.revision=%s AND a.venue_id=%s
                                 AND NOT (a.old_price <=> a.new_price)""",
                            (task["revision"], task["venue_id"]))
                services = cur.fetchall()
                if not services:
                    raise SyncFailure("同步任务没有对应的价目变更")
                conn.commit()
                sync_venue(bot, task["venue_id"], services)
            except Exception as error:
                logger.exception("Price synchronization failed venue=%s revision=%s",
                                 task["venue_id"], task["revision"])
                reason = str(error)[:255] if isinstance(error, SyncFailure) else "同步失败，请检查机器人日志"
                cur.execute("""UPDATE venue_price_channel_review
                               SET status='failed',reason=%s
                               WHERE revision=%s AND venue_id=%s""",
                            (reason, task["revision"], task["venue_id"]))
            else:
                cur.execute("""UPDATE venue_price_channel_review SET status='done',reason='同步完成'
                               WHERE revision=%s AND venue_id=%s""",
                            (task["revision"], task["venue_id"]))
            conn.commit()
            return True
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK('venue_price_sync')")
        conn.close()


def start_worker(bot):
    def run():
        while True:
            try:
                worked = run_once(bot)
            except Exception:
                logger.exception("Price synchronization worker failed")
                worked = False
            time.sleep(INTERVAL if worked else 15)

    threading.Thread(target=run, name="price-sync", daemon=True).start()
