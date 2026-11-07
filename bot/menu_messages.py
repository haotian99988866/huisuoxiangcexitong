"""Track and clear recorded bot menu responses without deleting user messages."""
import logging
import threading

from telebot import apihelper, types

from db import get_conn

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS bot_menu_messages ("
                        "chat_id BIGINT NOT NULL,message_id BIGINT NOT NULL,"
                        "is_keyboard TINYINT NOT NULL DEFAULT 0,"
                        "PRIMARY KEY(chat_id,message_id)) ENGINE=InnoDB")
            cur.execute("SHOW COLUMNS FROM bot_menu_messages LIKE 'is_keyboard'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE bot_menu_messages ADD COLUMN"
                            " is_keyboard TINYINT NOT NULL DEFAULT 0")
        conn.commit()
    finally:
        conn.close()


def keep_latest(bot, message, is_keyboard=False):
    with _lock:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT IGNORE INTO bot_menu_messages"
                            " (chat_id,message_id,is_keyboard) VALUES (%s,%s,%s)",
                            (message.chat.id, message.message_id, int(is_keyboard)))
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT message_id FROM bot_menu_messages WHERE chat_id=%s"
                            " AND message_id<(SELECT latest FROM (SELECT MAX(message_id) AS latest"
                            " FROM bot_menu_messages WHERE chat_id=%s) AS newest)"
                            " AND (is_keyboard=0 OR %s=1)",
                            (message.chat.id, message.chat.id, int(is_keyboard)))
                old_ids = [row["message_id"] for row in cur.fetchall()]
            for ident in old_ids:
                try:
                    bot.delete_message(message.chat.id, ident, timeout=20)
                except apihelper.ApiTelegramException as error:
                    if not (error.error_code == 400
                            and "message to delete not found" in error.description.lower()):
                        logger.exception("Cannot delete old menu message %s:%s", message.chat.id, ident)
                        continue
                except (apihelper.ApiException, ConnectionError, TimeoutError):
                    logger.exception("Menu cleanup unavailable for %s:%s", message.chat.id, ident)
                    continue
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bot_menu_messages WHERE chat_id=%s AND message_id=%s",
                                (message.chat.id, ident))
                conn.commit()
        finally:
            conn.close()
    return message


def send_tracked(bot, chat_id, text, is_keyboard=True, **kwargs):
    markup = kwargs.pop("reply_markup", None)
    message = bot.send_message(
        chat_id, text, reply_markup=types.ReplyKeyboardRemove(), **kwargs)
    keep_latest(bot, message, is_keyboard=is_keyboard)
    if markup is not None:
        bot.edit_message_reply_markup(chat_id, message.message_id, reply_markup=markup)
    return message


def clear_before(bot, command):
    chat_id = command.chat.id
    with _lock:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT message_id FROM bot_menu_messages "
                    "WHERE chat_id=%s AND message_id<%s ORDER BY message_id",
                    (chat_id, command.message_id),
                )
                old_ids = [row["message_id"] for row in cur.fetchall()]
            deleted = 0
            for ident in old_ids:
                try:
                    bot.delete_message(chat_id, ident, timeout=20)
                except apihelper.ApiTelegramException as error:
                    if not (error.error_code == 400
                            and "message to delete not found" in error.description.lower()):
                        logger.exception("Cannot clear old bot message %s:%s", chat_id, ident)
                        continue
                except (apihelper.ApiException, ConnectionError, TimeoutError):
                    logger.exception("Command history cleanup unavailable for %s:%s", chat_id, ident)
                    continue
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bot_menu_messages WHERE chat_id=%s AND message_id=%s",
                                (chat_id, ident))
                conn.commit()
                deleted += 1
            return deleted
        finally:
            conn.close()
