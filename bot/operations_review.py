"""Owner-only, persistent review of isolated operational tasks."""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
import sqlite3
import threading
import time
import uuid

import pymysql
from telebot import apihelper, types

logger = logging.getLogger(__name__)
REPEAT_SECONDS = 1800
SNAPSHOT_FIELDS = (
    "source", "target", "job_key", "message_ids", "message", "name", "size",
    "status", "error", "result_ids", "result_id", "document_id", "retryable",
)


@dataclass(frozen=True)
class Module:
    key: str
    name: str
    filename: str
    columns: tuple
    source: int = 0


def modules():
    from movie_ingest import SOURCES, SOURCE_ID
    return (
        Module("n", "新闻转发", "jobs.sqlite3", ("source", "target", "job_key")),
        Module("p", "音乐播放准备", "music-playback.sqlite3", ("name",)),
        Module("a", "音乐收录", "music-import.sqlite3", ("source", "message")),
        *(Module(f"v{index}", f"影视收录 @{source.username}",
                 "movie-import.sqlite3" if source.id == SOURCE_ID else f"movie-import-{source.id}.sqlite3",
                 ("message",), source.id) for index, source in enumerate(SOURCES)),
    )


def get_module(key):
    module = next((item for item in modules() if item.key == key), None)
    if module is None:
        raise ValueError("无效的核查模块")
    return module


@contextmanager
def connection(module):
    from channel_sync import state_directory
    path = state_directory() / module.filename
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS operation_reviews (
                id TEXT PRIMARY KEY, task_key TEXT NOT NULL, snapshot TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'open', created_at REAL NOT NULL,
                last_attempt REAL, delivery TEXT, error_code TEXT,
                actor_id INTEGER, action TEXT, finished_at REAL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS operation_reviews_open
                ON operation_reviews(task_key) WHERE state='open';
            CREATE TABLE IF NOT EXISTS operation_music_cleanups (
                ticket_id TEXT PRIMARY KEY, plan TEXT NOT NULL, retry_at REAL NOT NULL DEFAULT 0,
                error_code TEXT, completed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS operation_music_exclusions (
                message INTEGER PRIMARY KEY, ticket_id TEXT NOT NULL
            );
        """)
        yield conn
    finally:
        conn.close()


def encode(row):
    return json.dumps({key: row[key] for key in SNAPSHOT_FIELDS if key in row.keys()},
                      sort_keys=True, ensure_ascii=False)


def task_where(module, row):
    return " AND ".join(f"{key}=?" for key in module.columns), tuple(row[key] for key in module.columns)


def actionable(module, row):
    return row["status"] in {"failed", "uncertain"} and (
        module.key != "p" or not row["retryable"])


def scan(module, conn):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        for ticket in conn.execute("SELECT * FROM operation_reviews WHERE state='open'").fetchall():
            saved = json.loads(ticket["snapshot"])
            where, values = task_where(module, saved)
            current = conn.execute("SELECT * FROM jobs WHERE " + where, values).fetchone()
            if current is None or encode(current) != ticket["snapshot"] or not actionable(module, current):
                conn.execute("UPDATE operation_reviews SET state='resolved',finished_at=? WHERE id=?",
                             (time.time(), ticket["id"]))
        for row in conn.execute("SELECT * FROM jobs WHERE status IN ('failed','uncertain')").fetchall():
            if not actionable(module, row):
                continue
            key = json.dumps([row[column] for column in module.columns])
            conn.execute(
                "INSERT OR IGNORE INTO operation_reviews(id,task_key,snapshot,created_at) VALUES (?,?,?,?)",
                (uuid.uuid4().hex, key, encode(row), time.time()),
            )


def retry_action(module, row):
    error = (row.get("error") or "").lower()
    if module.key == "n" and ("overlap" in error or json.loads(row.get("result_ids") or "[]")):
        return None
    if module.key.startswith("v") and "matching publication outcome unknown" in error:
        return None
    if row.get("result_id"):
        return "index" if module.key == "a" or module.key.startswith("v") else None
    return "absent" if row["status"] == "uncertain" else "retry"


def callback(module, ticket, action):
    return f"opsr:{module.key}:{ticket['id']}:{action}"


def keyboard(module, ticket):
    row = json.loads(ticket["snapshot"])
    result = types.InlineKeyboardMarkup()
    message = json.loads(row["message_ids"])[0] if module.key == "n" else row.get("message")
    source = row.get("source") or module.source
    source_url = (f"https://t.me/haotiandkhy99988866/{message}" if module.key == "p"
                  else f"https://t.me/c/{source}/{message}")
    result.add(types.InlineKeyboardButton("打开来源核查", url=source_url))
    target = row.get("target") if module.key == "n" else (
        "haotiandkhy99988866" if module.key in {"a", "p"} else "haotianyingyuan")
    ids = json.loads(row.get("result_ids") or "[]") or [row.get("result_id")]
    target_url = f"https://t.me/c/{target}" if module.key == "n" else f"https://t.me/{target}"
    if ids[0]:
        target_url += f"/{ids[0]}"
    result.add(types.InlineKeyboardButton("核对目标频道", url=target_url))
    result.add(types.InlineKeyboardButton("核查后保留跳过", callback_data=callback(module, ticket, "skip")))
    action = retry_action(module, row)
    labels = {"retry": "修复后重新排队", "index": "仅重试入库，不重发",
              "absent": "已核对未发布，申请重试"}
    if action:
        result.add(types.InlineKeyboardButton(labels[action], callback_data=callback(module, ticket, action)))
    return result


def ticket_text(module, ticket):
    row = json.loads(ticket["snapshot"])
    identity = row.get("job_key") or row.get("message")
    reason = str(row.get("error") or "未记录原因")[:600]
    return (f"运营任务待人工核查 · {module.name}\n任务：{identity}\n"
            f"状态：{row['status']}\n原因：{reason}\n\n"
            "仅此任务隔离，其他任务继续运行。请先核对来源和目标频道，再选择处理。\n"
            "跳过不会删除原文件或已发布内容；重试仍遵守原去重、权限和限流。\n"
            "发送结果不明时，必须确认目标频道未发布；部分发布或历史重叠不能直接重发。")


def require_owner(actor_id, chat_id, chat_type):
    from premium_balance import manual_owner
    owner = manual_owner()
    if actor_id != owner or chat_id != owner or chat_type != "private":
        raise ValueError("仅负责人昊天可在机器人私聊核查运营任务")
    return owner


def send_pending(bot, owner, *, force=False):
    from channel_sync import state_directory
    from premium_balance import manual_owner
    if owner != manual_owner():
        raise ValueError("运营核查接收人已变更")
    sent = 0
    failures = []
    for module in modules():
        if module.key != "n":
            continue
        if not (state_directory() / module.filename).is_file():
            continue
        try:
            with connection(module) as conn:
                scan(module, conn)
                due = conn.execute(
                    "SELECT * FROM operation_reviews WHERE state='open' AND "
                    "(last_attempt IS NULL OR last_attempt<=?) ORDER BY COALESCE(last_attempt,0),created_at LIMIT ?",
                    (time.time() if force else time.time() - REPEAT_SECONDS, 3 - sent),
                ).fetchall()
                for ticket in due:
                    with conn:
                        conn.execute("BEGIN IMMEDIATE")
                        claimed = conn.execute(
                            "UPDATE operation_reviews SET last_attempt=?,delivery='uncertain',error_code=NULL "
                            "WHERE id=? AND state='open' AND (last_attempt IS NULL OR last_attempt<=?)",
                            (time.time(), ticket["id"], time.time() if force else time.time() - REPEAT_SECONDS),
                        ).rowcount
                    if not claimed:
                        continue
                    try:
                        bot.send_message(owner, ticket_text(module, ticket),
                                         reply_markup=keyboard(module, ticket), parse_mode=None)
                    except (apihelper.ApiException, OSError) as error:
                        with conn:
                            conn.execute("UPDATE operation_reviews SET error_code=? WHERE id=?",
                                         (type(error).__name__, ticket["id"]))
                        logger.error("Operational review notification unconfirmed module=%s ticket=%s error=%s",
                                     module.key, ticket["id"], type(error).__name__)
                    else:
                        with conn:
                            conn.execute("UPDATE operation_reviews SET delivery='sent' WHERE id=?", (ticket["id"],))
                    sent += 1
        except sqlite3.Error:
            logger.exception("Operational task review unavailable module=%s", module.key)
            failures.append(module.key)
        if sent >= 3:
            break
    if failures:
        raise sqlite3.OperationalError("Operational review unavailable: " + ",".join(failures))
    return sent


def get_current(conn, module, ticket_id):
    ticket = conn.execute("SELECT * FROM operation_reviews WHERE id=?", (ticket_id,)).fetchone()
    if ticket is None or ticket["state"] != "open":
        raise ValueError("核查任务已处理或已恢复，请勿重复操作")
    saved = json.loads(ticket["snapshot"])
    where, values = task_where(module, saved)
    current = conn.execute("SELECT * FROM jobs WHERE " + where, values).fetchone()
    if current is None or encode(current) != ticket["snapshot"] or not actionable(module, current):
        raise ValueError("任务状态已变化，请使用最新核查消息")
    return ticket, saved, where, values


def apply(module, conn, ticket_id, action, actor):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        ticket, row, where, values = get_current(conn, module, ticket_id)
        if action != "skip" and action != retry_action(module, row):
            raise ValueError("此任务不允许该重试操作，请先核对发布结果")
        if action == "skip":
            status = "excluded" if module.key.startswith("v") else "skipped"
            reason = "Owner reviewed: retain existing content; skip isolated task"
        else:
            status = "forwarded" if action == "index" else "queued"
            reason = None
        extra = ",retryable=?,retry_at=0,priority=1" if module.key == "p" else ""
        parameters = (status, reason, *([int(action != "skip")] if module.key == "p" else []), *values)
        conn.execute("UPDATE jobs SET status=?,error=?" + extra + " WHERE " + where, parameters)
        conn.execute(
            "UPDATE operation_reviews SET state='applied',actor_id=?,action=?,finished_at=? WHERE id=?",
            (actor, action, time.time(), ticket["id"]),
        )
    return ("已保留跳过，其他任务继续运行。" if action == "skip" else
            "已重新排队；工作进程运行后自动处理，原限流和去重保持有效。"
            "若模块未启动，请在运营健康中核实；此操作不会启动或开启收录模块。")


def handle_callback(bot, call):
    try:
        owner = require_owner(call.from_user.id, call.message.chat.id, call.message.chat.type)
        _, key, ticket_id, action = call.data.split(":")
        module = get_module(key)
        if module.key != "n":
            raise ValueError("音乐疑问已改为自动清理、影视自动跳过，此旧核查卡不再操作；新闻仍由你核查")
        with connection(module) as conn:
            if action.startswith("ok_"):
                result = apply(module, conn, ticket_id, action[3:], owner)
            else:
                ticket, row, _, _ = get_current(conn, module, ticket_id)
                if action != "skip" and action != retry_action(module, row):
                    raise ValueError("此任务不允许该处理方式")
                labels = {
                    "skip": "我已核查，确认保留跳过",
                    "retry": "我已修复，确认重新排队",
                    "index": "确认仅重试入库，不重发",
                    "absent": "我已核对目标频道，确实未发布",
                }
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(labels[action],
                           callback_data=callback(module, ticket, "ok_" + action)))
                bot.send_message(owner, ticket_text(module, ticket) + "\n\n二次确认：" + labels[action],
                                 reply_markup=markup, parse_mode=None)
                bot.answer_callback_query(call.id, "请核对后完成二次确认")
                return
    except ValueError as error:
        bot.answer_callback_query(call.id, str(error), show_alert=True)
        return
    except (sqlite3.Error, pymysql.MySQLError, apihelper.ApiException, OSError):
        logger.exception("Operational review action failed; check persisted ticket")
        bot.answer_callback_query(call.id, "处理未确认，请重新查询核查任务，勿重复发布", show_alert=True)
        return
    try:
        bot.answer_callback_query(call.id, "核查处理已保存")
        bot.send_message(owner, result, parse_mode=None)
    except (apihelper.ApiException, OSError):
        logger.exception("Operational review persisted; result notification failed ticket=%s", ticket_id)


def handle_command(bot, message):
    try:
        owner = require_owner(message.from_user.id, message.chat.id, message.chat.type)
        count = send_pending(bot, owner, force=True)
        bot.reply_to(message, f"已尝试发送{count}条新闻待核查任务（每次最多3条；发送不明会记录日志）。可再次发送 /ops_review 查看；"
                     "音乐疑问自动清理、影视疑问自动跳过，不再等待人工确认。"
                     "已经处理或自动恢复的任务不会再次列出。")
    except ValueError as error:
        bot.reply_to(message, str(error))
    except (sqlite3.Error, pymysql.MySQLError, apihelper.ApiException, OSError):
        logger.exception("Operational review inbox unavailable")
        bot.reply_to(message, "核查任务读取或发送失败，请稍后重试并检查运行日志。")


def worker(bot):
    from channel_sync import single_instance, state_directory
    from premium_balance import manual_owner
    while True:
        try:
            with single_instance(state_directory(), "operations-review.lock"):
                logger.warning("Operations review READY: owner-only task inbox; check=30s")
                while True:
                    try:
                        send_pending(bot, manual_owner())
                    except Exception:
                        logger.exception("Operational task review scan failed; retry in 30s")
                    time.sleep(30)
        except Exception:
            logger.exception("Operational task review worker unavailable; retry in 30s")
            time.sleep(30)


def start_worker(bot):
    threading.Thread(target=worker, args=(bot,), daemon=True, name="operations-review").start()
    threading.Thread(target=automatic_worker, daemon=True, name="operations-media-cleanup").start()


def automatic_module(module):
    from music_cleanup import advance
    with connection(module) as conn:
        scan(module, conn)
        tickets = conn.execute("SELECT * FROM operation_reviews WHERE state='open' "
                               "ORDER BY COALESCE(last_attempt,0),created_at LIMIT 3").fetchall()
        for ticket in tickets:
            try:
                begin_automatic_task(module, conn, ticket["id"])
            except Exception as error:
                with conn:
                    conn.execute("UPDATE operation_reviews SET last_attempt=?,error_code=? WHERE id=?",
                                 (time.time(), type(error).__name__, ticket["id"]))
                logger.exception("Automatic task isolation failed module=%s ticket=%s; other tasks continue",
                                 module.key, ticket["id"])
        cleanups = conn.execute(
            "SELECT * FROM operation_music_cleanups WHERE completed=0 AND retry_at<=? ORDER BY rowid LIMIT 3",
            (time.time(),),
        ).fetchall()
        for cleanup in cleanups:
            try:
                plan = json.loads(cleanup["plan"])
                for _ in range(6):
                    plan = advance(plan)
                    with conn:
                        for message in plan["messages"]:
                            conn.execute("INSERT OR IGNORE INTO operation_music_exclusions VALUES (?,?)",
                                         (message, cleanup["ticket_id"]))
                        conn.execute("UPDATE operation_music_cleanups SET plan=?,error_code=NULL WHERE ticket_id=?",
                                     (json.dumps(plan), cleanup["ticket_id"]))
                    if not plan["located"]:
                        break
                    if plan["phase"] == "done":
                        with conn:
                            conn.execute("BEGIN IMMEDIATE")
                            ticket = conn.execute("SELECT * FROM operation_reviews WHERE id=?",
                                                  (cleanup["ticket_id"],)).fetchone()
                            saved = json.loads(ticket["snapshot"])
                            where, values = task_where(module, saved)
                            extra = ",retryable=0" if module.key == "p" else ""
                            conn.execute("UPDATE jobs SET status='skipped',error=?" + extra +
                                         " WHERE " + where + " AND status='cleanup_pending'",
                                         ("Owner policy: doubtful music deleted from website, disk and owned channel", *values))
                            conn.execute("UPDATE operation_music_cleanups SET completed=1 WHERE ticket_id=?",
                                         (cleanup["ticket_id"],))
                            conn.execute("UPDATE operation_reviews SET state='applied',finished_at=? WHERE id=?",
                                         (time.time(), cleanup["ticket_id"]))
                        logger.info("Doubtful music cleanup complete module=%s ticket=%s", module.key, cleanup["ticket_id"])
                        break
            except Exception as error:
                from telethon.errors import FloodWaitError
                delay = error.seconds + 1 if isinstance(error, FloodWaitError) else 60
                with conn:
                    conn.execute("UPDATE operation_music_cleanups SET retry_at=?,error_code=? WHERE ticket_id=?",
                                 (time.time() + delay, type(error).__name__, cleanup["ticket_id"]))
                logger.exception("Doubtful music cleanup incomplete module=%s ticket=%s; retry later",
                                 module.key, cleanup["ticket_id"])
                if isinstance(error, FloodWaitError):
                    break


def begin_automatic_task(module, conn, ticket_id):
    from music_cleanup import initial_plan
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        _, row, where, values = get_current(conn, module, ticket_id)
        if module.key.startswith("v"):
            conn.execute("UPDATE jobs SET status='excluded',error=? WHERE " + where,
                         ("Owner policy: doubtful movie automatically excluded; existing copy retained", *values))
            conn.execute("UPDATE operation_reviews SET state='applied',action='auto_movie_skip',"
                         "finished_at=? WHERE id=?", (time.time(), ticket_id))
        else:
            plan = initial_plan(module, row)
            conn.execute("INSERT INTO operation_music_cleanups(ticket_id,plan) VALUES (?,?)",
                         (ticket_id, json.dumps(plan)))
            for message in plan["messages"]:
                conn.execute("INSERT OR IGNORE INTO operation_music_exclusions VALUES (?,?)", (message, ticket_id))
            conn.execute("UPDATE jobs SET status='cleanup_pending' WHERE " + where, values)
            conn.execute("UPDATE operation_reviews SET state='cleaning',action='auto_music_delete' WHERE id=?",
                         (ticket_id,))
    logger.info("Automatic task isolated module=%s ticket=%s", module.key, ticket_id)


def automatic_once():
    from channel_sync import state_directory
    for module in modules():
        if module.key == "n" or not (state_directory() / module.filename).is_file():
            continue
        try:
            if module.key in {"p", "a"} and cleanup_cooldown() > time.time():
                continue
            automatic_module(module)
        except Exception:
            logger.exception("Automatic media disposition failed module=%s; task state retained", module.key)


def cleanup_cooldown():
    from channel_sync import state_directory
    deadline = 0
    for module in modules():
        if module.key not in {"p", "a"} or not (state_directory() / module.filename).is_file():
            continue
        with connection(module) as conn:
            value = conn.execute("SELECT MAX(retry_at) FROM operation_music_cleanups "
                                 "WHERE completed=0 AND error_code='FloodWaitError'").fetchone()[0]
            deadline = max(deadline, value or 0)
    return deadline


def automatic_worker():
    from channel_sync import single_instance, state_directory
    while True:
        try:
            with single_instance(state_directory(), "operations-auto-media.lock"):
                logger.warning("Automatic media policy READY: doubtful music delete; movies exclude; news manual")
                while True:
                    automatic_once()
                    time.sleep(30)
        except Exception:
            logger.exception("Automatic media policy unavailable; retry in 30s")
            time.sleep(30)
