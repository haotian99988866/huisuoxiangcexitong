"""Delete only recorded bot menu responses, never arbitrary chat history."""
import logging
import threading

from telebot import apihelper

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
