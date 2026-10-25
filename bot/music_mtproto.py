"""Download an owned-channel audio copy with the existing authorized bot."""
import asyncio
import sqlite3

from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession


async def _download(message_id, destination, expected_size):
    from channel_sync import credentials, state_directory
    from music import MUSIC_CHANNEL_USERNAME
    api_id, api_hash = credentials()
    path = state_directory() / "publisher.session"
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        saved = conn.execute("SELECT dc_id,server_address,port,auth_key FROM sessions").fetchone()
    finally:
        conn.close()
    if not saved:
        raise ValueError("音乐下载机器人未登录")
    session = MemorySession()
    session.set_dc(saved[0], saved[1], saved[2])
    session.auth_key = AuthKey(saved[3])
    client = TelegramClient(session, api_id, api_hash, receive_updates=False)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("音乐下载机器人登录已失效")
        if not (await client.get_me()).bot:
            raise ValueError("音乐下载会话必须属于发布机器人")
        target = await client.get_entity(f"https://t.me/{MUSIC_CHANNEL_USERNAME}")
        messages = await client.get_messages(target, ids=[message_id])
        message = messages[0] if messages else None
        if not message or not message.document or message.file.size != expected_size:
            raise ValueError("自有频道音乐不存在或大小不匹配")
        await client.download_media(message, file=str(destination))
    finally:
        await client.disconnect()


def download_owned_audio(message_id, destination, expected_size):
    try:
        asyncio.run(asyncio.wait_for(_download(message_id, destination, expected_size), timeout=90))
    except Exception as error:
        # Transport errors can contain credentials; expose only the exception class.
        raise ValueError(f"自有频道音频下载失败（{type(error).__name__}），请稍后重试") from None
