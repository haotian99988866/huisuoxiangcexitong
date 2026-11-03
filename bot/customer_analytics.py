"""Authenticated foreground engagement and observed invitation/bot state."""
from datetime import datetime, timedelta, timezone
import logging
import re
import uuid
from urllib.parse import parse_qsl

from db import get_conn
from visit_stats import admin_ids

logger = logging.getLogger(__name__)
HEX_ID = re.compile(r"^[0-9a-f]{32}$")
PAGE_KEYS = {
    "venue", "delivery", "venue_detail", "technicians", "movies", "music", "short_dramas",
    "announcements", "announcement_detail", "services", "hotels", "personal", "recharge",
    "membership", "phone_topup", "orders", "order_detail", "wallet_history", "consumption", "admin", "other",
    "technician_detail", "movie_detail", "media_viewer", "ktv_player", "music_player",
}
LOCAL_ZONE = timezone(timedelta(hours=7))


def ensure_tables():
    from relay import ensure_tables as ensure_bot_user_tables
    ensure_bot_user_tables()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET time_zone='+00:00'")
            cur.execute("""CREATE TABLE IF NOT EXISTS analytics_customers (
                user_id BIGINT PRIMARY KEY, username VARCHAR(64) NOT NULL DEFAULT '',
                display_name VARCHAR(128) NOT NULL DEFAULT '', invite_token CHAR(32) NOT NULL UNIQUE,
                recording BOOLEAN NOT NULL DEFAULT TRUE,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                first_inviter_id BIGINT NULL, first_invited_at TIMESTAMP NULL,
                invited_existing BOOLEAN NULL,
                bot_state VARCHAR(16) NOT NULL DEFAULT 'unknown',
                bot_started_at TIMESTAMP NULL, bot_state_at TIMESTAMP NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS analytics_sessions (
                id CHAR(32) PRIMARY KEY, user_id BIGINT NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_as_user (user_id,started_at), INDEX idx_as_time (started_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS analytics_views (
                id CHAR(32) PRIMARY KEY, session_id CHAR(32) NOT NULL, user_id BIGINT NOT NULL,
                page_key VARCHAR(32) NOT NULL, active_ms BIGINT NOT NULL DEFAULT 0,
                client_ms BIGINT NOT NULL DEFAULT 0,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_av_user (user_id,started_at), INDEX idx_av_time (started_at),
                INDEX idx_av_session (session_id), INDEX idx_av_page (page_key,started_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS analytics_referrals (
                user_id BIGINT PRIMARY KEY, inviter_id BIGINT NOT NULL,
                existing_customer BOOLEAN NOT NULL, source VARCHAR(16) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_ar_inviter (inviter_id,created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS analytics_bot_events (
                id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL,
                state VARCHAR(16) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_abe_user (user_id,created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""INSERT INTO analytics_customers(user_id,username,display_name,invite_token,
                first_seen,last_seen)
                SELECT tg_user_id,COALESCE(username,''),COALESCE(display_name,''),
                    REPLACE(UUID(),'-',''),first_seen,last_active FROM bot_users
                ON DUPLICATE KEY UPDATE user_id=VALUES(user_id)""")
        conn.commit()
    finally:
        conn.close()


def _touch(cur, user):
    cur.execute("SELECT first_seen FROM analytics_customers WHERE user_id=%s", (user["id"],))
    existing = cur.fetchone() is not None
    if not existing:
        cur.execute("SELECT 1 FROM bot_users WHERE tg_user_id=%s UNION ALL "
                    "SELECT 1 FROM page_visits WHERE tg_user_id=%s LIMIT 1", (user["id"], user["id"]))
        existing = cur.fetchone() is not None
    name = " ".join(str(user.get(key) or "") for key in ("first_name", "last_name")).strip()
    cur.execute("""INSERT INTO analytics_customers(user_id,username,display_name,invite_token)
        VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE username=VALUES(username),
        display_name=VALUES(display_name),last_seen=IF(recording,CURRENT_TIMESTAMP,last_seen)""",
        (user["id"], str(user.get("username") or "")[:64], name[:128], uuid.uuid4().hex))
    cur.execute("SELECT * FROM analytics_customers WHERE user_id=%s FOR UPDATE", (user["id"],))
    return cur.fetchone(), existing


def _attribute(cur, user_id, token, existing, source):
    if not token:
        return
    if not isinstance(token, str) or not HEX_ID.fullmatch(token):
        raise ValueError("邀请入口无效")
    cur.execute("SELECT user_id FROM analytics_customers WHERE invite_token=%s", (token,))
    owner = cur.fetchone()
    if owner is None:
        logger.warning("Unknown invitation token for authenticated user %s", user_id)
        return
    if owner["user_id"] == user_id:
        return
    cur.execute("SELECT first_inviter_id FROM analytics_customers WHERE user_id=%s", (user_id,))
    if cur.fetchone()["first_inviter_id"] is not None:
        return
    cur.execute("""UPDATE analytics_customers SET first_inviter_id=%s,first_invited_at=CURRENT_TIMESTAMP,
        invited_existing=%s WHERE user_id=%s""", (owner["user_id"], existing, user_id))
    cur.execute("""INSERT INTO analytics_referrals(user_id,inviter_id,existing_customer,source)
        VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE user_id=VALUES(user_id)""",
        (user_id, owner["user_id"], existing, source))


def invitation_token(init_data):
    param = dict(parse_qsl(init_data)).get("start_param", "")
    return param[4:] if param.startswith("ref_") else None


def invite_link(user_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT invite_token FROM analytics_customers WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError("客户邀请资料尚未建立，请重新打开机器人")
        return f"https://t.me/haotianzhaopian_bot?start=ref_{row['invite_token']}"
    finally:
        conn.close()


def preferences(user, enabled=None, init_data=""):
    if enabled is not None and type(enabled) is not bool:
        raise ValueError("浏览记录开关必须为布尔值")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            profile, existing = _touch(cur, user)
            if enabled is not None:
                cur.execute("UPDATE analytics_customers SET recording=%s WHERE user_id=%s",
                            (enabled, user["id"]))
                profile["recording"] = enabled
                if not enabled:
                    cur.execute("DELETE FROM analytics_views WHERE user_id=%s", (user["id"],))
                    cur.execute("DELETE FROM analytics_sessions WHERE user_id=%s", (user["id"],))
                    cur.execute("DELETE FROM page_visits WHERE tg_user_id=%s", (user["id"],))
            if profile["recording"]:
                _attribute(cur, user["id"], invitation_token(init_data), existing, "miniapp")
        conn.commit()
        return {"recording": bool(profile["recording"]), "invite_token": profile["invite_token"]}
    finally:
        conn.close()


def record(user, data, init_data):
    for field in ("session_id", "view_id"):
        if not isinstance(data.get(field), str) or not HEX_ID.fullmatch(data[field]):
            raise ValueError("浏览记录编号无效")
    page = data.get("page_key")
    total = data.get("total_ms")
    if not isinstance(page, str) or page not in PAGE_KEYS or type(total) is not int or not 0 <= total <= 90 * 24 * 3600 * 1000:
        raise ValueError("浏览页面或时长无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            profile, existing = _touch(cur, user)
            if not profile["recording"]:
                conn.commit()
                return {"recording": False}
            _attribute(cur, user["id"], invitation_token(init_data), existing, "miniapp")
            if user["id"] in admin_ids():
                conn.commit()
                return {"recording": True}
            cur.execute("""INSERT INTO analytics_sessions(id,user_id) VALUES(%s,%s)
                ON DUPLICATE KEY UPDATE id=VALUES(id)""", (data["session_id"], user["id"]))
            new_session = cur.rowcount == 1
            cur.execute("SELECT user_id FROM analytics_sessions WHERE id=%s FOR UPDATE", (data["session_id"],))
            if cur.fetchone()["user_id"] != user["id"]:
                raise ValueError("浏览会话不属于当前账户")
            cur.execute("""INSERT INTO analytics_views(id,session_id,user_id,page_key)
                VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=VALUES(id)""",
                (data["view_id"], data["session_id"], user["id"], page))
            cur.execute("""SELECT *, UNIX_TIMESTAMP(CURRENT_TIMESTAMP)-UNIX_TIMESTAMP(last_seen) AS elapsed
                FROM analytics_views WHERE id=%s FOR UPDATE""", (data["view_id"],))
            view = cur.fetchone()
            if (view["user_id"], view["session_id"], view["page_key"]) != (
                user["id"], data["session_id"], page
            ):
                raise ValueError("浏览记录上下文冲突")
            if total > view["client_ms"]:
                delta = min(total - view["client_ms"], max(0, int(view["elapsed"]) * 1000 + 2000), 30000)
                cur.execute("""UPDATE analytics_views SET active_ms=active_ms+%s,client_ms=%s,
                    last_seen=CURRENT_TIMESTAMP WHERE id=%s""", (delta, total, data["view_id"]))
            cur.execute("UPDATE analytics_sessions SET last_seen=CURRENT_TIMESTAMP WHERE id=%s",
                        (data["session_id"],))
        conn.commit()
        return {"recording": True, "new_session": new_session}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def bot_event(user, state, token=None):
    if state not in {"started", "reachable", "blocked"}:
        raise ValueError("机器人状态无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            profile, existing = _touch(cur, user)
            if profile["recording"]:
                _attribute(cur, user["id"], token, existing, "bot")
            current = "blocked" if state == "blocked" else "reachable"
            if state == "started" or profile["bot_state"] != current:
                cur.execute("INSERT INTO analytics_bot_events(user_id,state) VALUES(%s,%s)", (user["id"], state))
            cur.execute("""UPDATE analytics_customers SET bot_state=%s,bot_state_at=CURRENT_TIMESTAMP,
                bot_started_at=IF(%s='started',COALESCE(bot_started_at,CURRENT_TIMESTAMP),bot_started_at)
                WHERE user_id=%s""", (current, state, user["id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cleanup():
    conn = get_conn()
    count = 0
    try:
        with conn.cursor() as cur:
            for table, column in (
                ("analytics_views", "started_at"), ("analytics_sessions", "started_at"),
                ("analytics_bot_events", "created_at"), ("analytics_referrals", "created_at"),
            ):
                cur.execute(f"DELETE FROM {table} WHERE {column}<DATE_SUB(NOW(),INTERVAL 90 DAY)")
                count += cur.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def _integer(data, field, default, maximum):
    value = data.get(field, default)
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{field} 参数无效")
    return value


def _public(row):
    return {key: value.isoformat() + "Z" if isinstance(value, datetime) else value
            for key, value in row.items()}


def dashboard(data):
    days = _integer(data, "days", 7, 90)
    page = _integer(data, "page", 1, 100000)
    start = datetime.now(LOCAL_ZONE).date() - timedelta(days=days - 1)
    since = datetime.combine(start, datetime.min.time(), LOCAL_ZONE).astimezone(timezone.utc).replace(tzinfo=None)
    search = data.get("search", "")
    if not isinstance(search, str) or len(search) > 64:
        raise ValueError("搜索词最多64字")
    sort = data.get("sort", "last_seen")
    sorts = {"last_seen": "c.last_seen", "duration": "COALESCE(v.active_ms,0)",
             "visits": "COALESCE(s.sessions,0)", "invites": "COALESCE(r.invites,0)"}
    if sort not in sorts:
        raise ValueError("排序方式无效")
    excluded = sorted(admin_ids()) or [0]
    mask = ",".join(["%s"] * len(excluded))
    filter_sql = f"c.user_id NOT IN ({mask})"
    args = list(excluded)
    if search.strip():
        filter_sql += " AND (c.username LIKE %s OR c.display_name LIKE %s OR CAST(c.user_id AS CHAR)=%s)"
        escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        args += [f"%{escaped}%", f"%{escaped}%", search.strip()]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET time_zone='+00:00'")
            cur.execute(f"""SELECT COUNT(*) AS visits,COUNT(DISTINCT user_id) AS customers
                FROM analytics_sessions WHERE started_at>=%s AND user_id NOT IN ({mask})""",
                [since, *excluded])
            overview = cur.fetchone()
            cur.execute(f"""SELECT COUNT(*) AS page_views,COALESCE(SUM(active_ms),0) AS active_ms
                FROM analytics_views WHERE started_at>=%s AND user_id NOT IN ({mask})""", [since, *excluded])
            overview.update(cur.fetchone())
            cur.execute(f"""SELECT COUNT(DISTINCT user_id) AS online FROM analytics_views
                WHERE last_seen>=DATE_SUB(NOW(),INTERVAL 45 SECOND) AND user_id NOT IN ({mask})""", excluded)
            overview.update(cur.fetchone())
            cur.execute(f"""SELECT COUNT(*) AS referrals,
                COALESCE(SUM(NOT existing_customer),0) AS new_referrals FROM analytics_referrals
                WHERE created_at>=%s AND user_id NOT IN ({mask})""", [since, *excluded])
            overview.update(cur.fetchone())
            cur.execute(f"""SELECT DATE(DATE_ADD(started_at,INTERVAL 7 HOUR)) AS day,
                COUNT(*) AS visits,COUNT(DISTINCT user_id) AS customers
                FROM analytics_sessions WHERE started_at>=%s AND user_id NOT IN ({mask})
                GROUP BY day ORDER BY day""", [since, *excluded])
            trend = {str(row["day"]): row for row in cur.fetchall()}
            cur.execute(f"""SELECT DATE(DATE_ADD(started_at,INTERVAL 7 HOUR)) AS day,
                COALESCE(SUM(active_ms),0) AS active_ms,COUNT(DISTINCT user_id) AS customers FROM analytics_views
                WHERE started_at>=%s AND user_id NOT IN ({mask}) GROUP BY day""", [since, *excluded])
            for row in cur.fetchall():
                trend.setdefault(str(row["day"]), {}).update(row)
            daily = []
            for offset in range(days):
                day = str(start + timedelta(days=offset))
                daily.append({"day": day, "visits": 0, "customers": 0, "active_ms": 0,
                              **trend.get(day, {}), "day": day})
            cur.execute(f"""SELECT page_key,COUNT(*) AS views,COUNT(DISTINCT user_id) AS customers,
                SUM(active_ms) AS active_ms FROM analytics_views WHERE started_at>=%s
                AND user_id NOT IN ({mask}) GROUP BY page_key ORDER BY active_ms DESC""", [since, *excluded])
            pages = cur.fetchall()
            cur.execute(f"SELECT COUNT(*) AS total FROM analytics_customers c WHERE {filter_sql}", args)
            total = cur.fetchone()["total"]
            cur.execute(f"""SELECT c.*,COALESCE(v.views,0) AS views,COALESCE(v.active_ms,0) AS active_ms,
                COALESCE(s.sessions,0) AS sessions,COALESCE(r.invites,0) AS invites,
                (SELECT page_key FROM analytics_views WHERE user_id=c.user_id AND started_at>=%s
                    GROUP BY page_key ORDER BY SUM(active_ms) DESC LIMIT 1) AS top_page,
                b.last_active AS last_interaction,c.first_inviter_id AS inviter_id,
                c.invited_existing AS existing_customer
                FROM analytics_customers c
                LEFT JOIN (SELECT user_id,COUNT(*) AS views,SUM(active_ms) AS active_ms
                    FROM analytics_views WHERE started_at>=%s GROUP BY user_id) v ON v.user_id=c.user_id
                LEFT JOIN (SELECT user_id,COUNT(*) AS sessions FROM analytics_sessions
                    WHERE started_at>=%s GROUP BY user_id) s ON s.user_id=c.user_id
                LEFT JOIN (SELECT inviter_id,COUNT(*) AS invites FROM analytics_referrals
                    WHERE created_at>=%s GROUP BY inviter_id) r ON r.inviter_id=c.user_id
                LEFT JOIN bot_users b ON b.tg_user_id=c.user_id
                WHERE {filter_sql} ORDER BY {sorts[sort]} DESC,c.user_id DESC LIMIT 20 OFFSET %s""",
                [since, since, since, since, *args, (page - 1) * 20])
            customers = [_public(row) for row in cur.fetchall()]
            cur.execute(f"""SELECT COUNT(*) AS legacy_visits FROM page_visits
                WHERE created_at>=%s AND tg_user_id NOT IN ({mask})""", [since, *excluded])
            legacy = cur.fetchone()["legacy_visits"]
        return {"overview": overview, "daily": daily, "pages": pages, "customers": customers,
                "total": total, "page": page, "days": days, "legacy_visits": legacy}
    finally:
        conn.close()


def customer_detail(data):
    user_id = data.get("user_id")
    if type(user_id) is not int or user_id <= 0:
        raise ValueError("客户编号无效")
    invite_page = _integer(data, "invite_page", 1, 100000)
    session_page = _integer(data, "session_page", 1, 100000)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET time_zone='+00:00'")
            cur.execute("""SELECT c.*,b.last_active AS last_interaction,
                b.blocked AS locally_blocked FROM analytics_customers c
                LEFT JOIN bot_users b ON b.tg_user_id=c.user_id WHERE c.user_id=%s""", (user_id,))
            profile = cur.fetchone()
            if profile is None:
                raise ValueError("客户记录不存在")
            cur.execute("""SELECT page_key,COUNT(*) AS views,SUM(active_ms) AS active_ms
                FROM analytics_views WHERE user_id=%s GROUP BY page_key ORDER BY active_ms DESC""", (user_id,))
            pages = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS total FROM analytics_sessions WHERE user_id=%s", (user_id,))
            session_total = cur.fetchone()["total"]
            cur.execute("""SELECT s.id,s.started_at,s.last_seen,COUNT(v.id) AS views,
                COALESCE(SUM(v.active_ms),0) AS active_ms FROM analytics_sessions s
                LEFT JOIN analytics_views v ON v.session_id=s.id
                WHERE s.user_id=%s GROUP BY s.id ORDER BY s.started_at DESC LIMIT 20 OFFSET %s""",
                (user_id, (session_page - 1) * 20))
            sessions = [_public(row) for row in cur.fetchall()]
            for session in sessions:
                cur.execute("""SELECT page_key,started_at,active_ms FROM analytics_views
                    WHERE session_id=%s ORDER BY started_at,id LIMIT 201""", (session["id"],))
                views = cur.fetchall()
                session["timeline"] = [_public(row) for row in views[:200]]
                session["timeline_truncated"] = len(views) > 200
            cur.execute("""SELECT owner.user_id AS inviter_id,owner.username,owner.display_name,
                c.first_invited_at AS created_at,c.invited_existing AS existing_customer
                FROM analytics_customers c JOIN analytics_customers owner
                ON owner.user_id=c.first_inviter_id WHERE c.user_id=%s""", (user_id,))
            inviter = cur.fetchone()
            cur.execute("SELECT COUNT(*) AS total FROM analytics_referrals WHERE inviter_id=%s", (user_id,))
            invite_total = cur.fetchone()["total"]
            cur.execute("""SELECT r.*,c.username,c.display_name,c.bot_state,c.bot_started_at,
                c.bot_state_at,b.last_active AS last_interaction FROM analytics_referrals r
                JOIN analytics_customers c ON c.user_id=r.user_id
                LEFT JOIN bot_users b ON b.tg_user_id=r.user_id
                WHERE r.inviter_id=%s ORDER BY r.created_at DESC LIMIT 20 OFFSET %s""",
                (user_id, (invite_page - 1) * 20))
            invited = [_public(row) for row in cur.fetchall()]
            cur.execute("""SELECT state,created_at FROM analytics_bot_events WHERE user_id=%s
                ORDER BY id DESC LIMIT 30""", (user_id,))
            events = [_public(row) for row in cur.fetchall()]
            cur.execute("""SELECT COALESCE(JSON_LENGTH(JSON_EXTRACT(document,'$.favorites')),0) AS favorites
                FROM music_profiles WHERE user_id=%s""", (user_id,))
            music = cur.fetchone()
            cur.execute("SELECT COUNT(*) AS playlists FROM music_playlists WHERE user_id=%s", (user_id,))
            playlists = cur.fetchone()["playlists"]
        return {"customer": _public(profile), "pages": pages, "sessions": sessions,
                "session_total": session_total, "session_page": session_page,
                "inviter": _public(inviter) if inviter else None, "invited": invited,
                "invite_total": invite_total, "invite_page": invite_page, "bot_events": events,
                "favorites": music["favorites"] if music else 0, "playlists": playlists}
    finally:
        conn.close()
