"""Explicitly authorized music sources; never follows links to other channels."""
import os
from pathlib import Path
import sqlite3


SOURCES = {
    2388479001: ("SYNChina", "中文英文"),
    2439864039: ("daige2005", "傣族音乐"),
    2340095017: ("syn119", "缅甸音乐"),
    2250135836: ("yingwen2005", "英文音乐"),
}


def external_forward(message, store_path=None):
    origin = getattr(message, "forward_origin", None)
    chat = getattr(origin, "chat", None) or getattr(message, "forward_from_chat", None)
    ident = str(getattr(chat, "id", ""))
    if ident in {f"-100{value}" for value in SOURCES}:
        return True
    if origin is None and not getattr(message, "forward_date", None):
        return False
    path = Path(store_path) if store_path is not None else (
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Huisuo" / "channel-sync" / "music-import.sqlite3"
    )
    if not path.is_file():
        return False
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        if conn.execute("SELECT 1 FROM jobs WHERE result_id=? LIMIT 1",
                        (getattr(message, "message_id", None),)).fetchone():
            return True
        media = getattr(message, "audio", None) or getattr(message, "document", None)
        file_id = getattr(media, "file_id", None)
        if not file_id:
            return False
        from telethon import utils
        document = utils.resolve_bot_file_id(file_id)
        if document is None:
            raise ValueError("Forwarded music has an invalid Telegram file identifier")
        # Bot updates can arrive before forward_messages returns the target message ID.
        return conn.execute(
            "SELECT 1 FROM jobs WHERE document_id=?"
            " AND status IN ('sending','forwarded','sent','uncertain') LIMIT 1",
            (document.id,),
        ).fetchone() is not None
    finally:
        conn.close()
