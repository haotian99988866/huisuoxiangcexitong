"""Single-use browser login links issued in a private bot conversation."""
import hashlib
import os
import re
import secrets
from urllib.parse import urlsplit

from db import get_conn

TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def site_url():
    value = os.getenv("MEMBER_SITE_URL", "").strip()
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.path != "/membership"):
        raise ValueError("请配置 MEMBER_SITE_URL 为 HTTPS 网站的 /membership 地址")
    return value


def site_origin():
    parsed = urlsplit(site_url())
    return f"{parsed.scheme}://{parsed.netloc}"


def token_hash(token):
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("网站登录凭证无效，请私聊机器人发送 /member 获取新链接")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS member_login_links ("
                "token_hash CHAR(64) PRIMARY KEY,user_id BIGINT NOT NULL,"
                "expires_at TIMESTAMP NOT NULL,used_at TIMESTAMP NULL,"
                "INDEX idx_member_login_user(user_id)) ENGINE=InnoDB")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS member_sessions ("
                "token_hash CHAR(64) PRIMARY KEY,user_id BIGINT NOT NULL,"
                "expires_at TIMESTAMP NOT NULL,INDEX idx_member_session_expiry(expires_at)"
                ") ENGINE=InnoDB")
        conn.commit()
    finally:
        conn.close()


def create_login_link(user_id):
    url = site_url()
    token = secrets.token_hex(32)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM member_login_links WHERE user_id=%s OR expires_at<NOW()", (user_id,))
            cur.execute(
                "INSERT INTO member_login_links(token_hash,user_id,expires_at) "
                "VALUES(%s,%s,DATE_ADD(NOW(),INTERVAL 10 MINUTE))",
                (token_hash(token), user_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return f"{url}#login={token}"


def exchange_link(token):
    digest = token_hash(token)
    session = secrets.token_hex(32)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM member_login_links WHERE token_hash=%s "
                "AND used_at IS NULL AND expires_at>NOW() FOR UPDATE", (digest,))
            link = cur.fetchone()
            if not link:
                raise ValueError("登录链接已使用或过期，请私聊机器人发送 /member 获取新链接")
            cur.execute("UPDATE member_login_links SET used_at=NOW() WHERE token_hash=%s", (digest,))
            cur.execute("DELETE FROM member_sessions WHERE expires_at<NOW()")
            cur.execute(
                "INSERT INTO member_sessions(token_hash,user_id,expires_at) "
                "VALUES(%s,%s,DATE_ADD(NOW(),INTERVAL 24 HOUR))",
                (token_hash(session), link["user_id"]))
        conn.commit()
        return session
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def authenticate(token):
    digest = token_hash(token)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM member_sessions WHERE token_hash=%s AND expires_at>NOW()",
                (digest,))
            row = cur.fetchone()
        if not row:
            raise ValueError("网站登录已过期，请私聊机器人发送 /member 重新登录")
        return row["user_id"]
    finally:
        conn.close()


def logout(token):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM member_sessions WHERE token_hash=%s", (token_hash(token),))
        conn.commit()
    finally:
        conn.close()
