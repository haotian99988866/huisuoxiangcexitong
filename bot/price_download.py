"""Short-lived download links for administrator-created price spreadsheets."""

import base64
import binascii
import secrets
import threading
import time

_files = {}
_lock = threading.Lock()
_MAX_BYTES = 8192
_LIFETIME = 180


def create(encoded):
    if not isinstance(encoded, str) or len(encoded) > ((_MAX_BYTES + 2) // 3) * 4:
        raise ValueError("价目表文件过大或无效")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("价目表文件无效") from None
    if not content.startswith(b"PK\x03\x04") or len(content) > _MAX_BYTES:
        raise ValueError("价目表文件无效")
    now = time.monotonic()
    with _lock:
        for token in [key for key, (expires, _) in _files.items() if expires <= now]:
            del _files[token]
        if len(_files) >= 64:
            raise ValueError("下载请求过多，请稍后重试")
        token = secrets.token_urlsafe(32)
        _files[token] = (now + _LIFETIME, content)
    return token


def get(token):
    with _lock:
        entry = _files.get(token)
        if entry and entry[0] <= time.monotonic():
            del _files[token]
            return None
        return entry[1] if entry else None
