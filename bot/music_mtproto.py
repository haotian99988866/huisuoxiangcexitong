"""Download an owned-channel audio copy with the existing authorized bot."""
import asyncio
import sqlite3

from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession


async def _audio_chunks(message_id, expected_size, start=0, end=None, *, block_size=64 * 1024):
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
    client = TelegramClient(session, api_id, api_hash, receive_updates=False, flood_sleep_threshold=0)
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
        end = expected_size - 1 if end is None else end
        block = block_size
        offset = start - start % block
        remaining = end - start + 1
        skip = start - offset
        async for chunk in client.iter_download(
            message.document, offset=offset, request_size=block, chunk_size=block
        ):
            data = bytes(chunk)[skip:skip + remaining]
            skip = 0
            if data:
                remaining -= len(data)
                yield data
            if remaining == 0:
                break
        if remaining:
            raise ValueError("自有频道音频流提前结束")
    finally:
        await client.disconnect()


async def _download(message_id, destination, expected_size):
    with open(destination, "wb") as output:
        async for chunk in _audio_chunks(message_id, expected_size):
            output.write(chunk)


def stream_owned_audio(message_id, expected_size, start, end):
    """Pull one bounded chunk per WSGI iteration; disconnect on cancellation."""
    loop = asyncio.new_event_loop()
    chunks = _audio_chunks(message_id, expected_size, start, end)
    try:
        while True:
            try:
                yield loop.run_until_complete(asyncio.wait_for(anext(chunks), timeout=45))
            except StopAsyncIteration:
                break
    except Exception as error:
        raise ValueError(f"自有频道音频流失败（{type(error).__name__}）") from None
    finally:
        try:
            loop.run_until_complete(chunks.aclose())
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()


def download_owned_audio(message_id, destination, expected_size):
    try:
        asyncio.run(asyncio.wait_for(_download(message_id, destination, expected_size), timeout=90))
    except Exception as error:
        # Transport errors can contain credentials; expose only the exception class.
        raise ValueError(f"自有频道音频下载失败（{type(error).__name__}），请稍后重试") from None
