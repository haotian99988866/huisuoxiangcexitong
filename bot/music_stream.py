"""Bounded Telegram audio streaming with single HTTP byte-range support."""
from dataclasses import dataclass
import logging
import threading
import tempfile
from pathlib import Path

from flask import Response, request
from werkzeug.http import parse_range_header

from music_mtproto import stream_owned_audio

logger = logging.getLogger(__name__)
stream_slots = threading.BoundedSemaphore(3)


@dataclass(frozen=True)
class RemoteAudio:
    message_id: int
    size: int
    mimetype: str
    cache_name: str | None = None


def audio_response(source):
    header = request.headers.get("Range")
    requested = parse_range_header(header) if header else None
    if header and (requested is None or requested.units != "bytes" or len(requested.ranges) != 1):
        return Response(status=416, headers={"Content-Range": f"bytes */{source.size}"})
    bounds = requested.range_for_length(source.size) if requested else (0, source.size)
    if bounds is None:
        return Response(status=416, headers={"Content-Range": f"bytes */{source.size}"})
    start, stop = bounds
    headers = {
        "Accept-Ranges": "bytes", "Content-Length": str(stop - start),
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "X-Accel-Buffering": "no", "X-Media-Cache": "STREAM",
    }
    if requested:
        headers["Content-Range"] = f"bytes {start}-{stop - 1}/{source.size}"
    if request.method == "HEAD":
        return Response(status=206 if requested else 200, mimetype=source.mimetype, headers=headers)
    if not stream_slots.acquire(blocking=False):
        return Response("音频连接繁忙，请稍后重试", status=503,
                        headers={"Retry-After": "3", "Cache-Control": "no-store"})
    upstream = stream_owned_audio(source.message_id, source.size, start, stop - 1)
    closed = False

    def close():
        nonlocal closed
        if not closed:
            closed = True
            try:
                upstream.close()
            finally:
                stream_slots.release()

    try:
        first = next(upstream)
    except BaseException:
        close()
        raise

    def body():
        temporary = None
        output = None
        try:
            if source.cache_name and start == 0 and stop == source.size:
                from music import library_dir
                folder = library_dir()
                folder.mkdir(parents=True, exist_ok=True)
                output = tempfile.NamedTemporaryFile(
                    dir=folder, suffix=Path(source.cache_name).suffix, delete=False)
                temporary = Path(output.name)
                output.write(first)
            yield first
            for chunk in upstream:
                if output:
                    output.write(chunk)
                yield chunk
            if output:
                output.close()
                from music import commit_stream_cache
                commit_stream_cache(temporary, source.cache_name, source.size)
        except Exception:
            logger.exception("Music stream interrupted message=%s; response truncated", source.message_id)
            raise
        finally:
            if output:
                output.close()
            if temporary:
                temporary.unlink(missing_ok=True)
            close()

    response = Response(body(), status=206 if requested else 200,
                        mimetype=source.mimetype, headers=headers)
    response.call_on_close(close)
    return response
