"""Authenticated owner-only display tenant management."""
from datetime import datetime, timedelta, timezone
import re
import time
from urllib.parse import parse_qsl

from db import get_conn
from tenants import save_tenant, tenant_revision, tenant_snapshot
from visit_stats import admin_ids, verify_init_data, INIT_DATA_MAX_AGE_SECONDS


def authenticate(init_data, token):
    if not isinstance(init_data, str) or not init_data or len(init_data) > 4096:
        return None
    pairs = parse_qsl(init_data, keep_blank_values=True)
    if len({key for key, _ in pairs}) != len(pairs):
        return None
    fields = dict(pairs)
    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        return None
    if not time.time() - INIT_DATA_MAX_AGE_SECONDS <= auth_date <= time.time() + 60:
        return None
    try:
        user = verify_init_data(init_data, token)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0:
        return None
    return user


def is_tenant_session(init_data):
    return dict(parse_qsl(init_data)).get("start_param", "").startswith("tenant_")


def dashboard():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # TIMESTAMP reads use the session zone; expiry DATETIME values are already UTC.
            cur.execute("SET time_zone = '+00:00'")
            cur.execute("SELECT * FROM display_tenants ORDER BY created_at DESC, slug LIMIT 201")
            rows = cur.fetchall()
            cur.execute(
                "SELECT id,name FROM venues WHERE status='active' AND id IN (1,2,3,4,5,6,7,8,9,10) ORDER BY id"
            )
            venues = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS count FROM wallet_deposits WHERE status='pending'")
            pending = cur.fetchone()["count"]
            cur.execute(
                "SELECT id,tenant_slug,actor_id,source,action,created_at "
                "FROM tenant_admin_audit ORDER BY id DESC LIMIT 30"
            )
            audit = cur.fetchall()
    finally:
        conn.close()
    # Bound the first version rather than silently hiding tenants beyond the UI limit.
    if len(rows) > 200:
        raise ValueError("当前管理台最多展示 200 个租户，请使用本机管理并升级分页")
    tenants = []
    counts = {"total": len(rows), "active": 0, "expiring": 0, "expired": 0,
              "draft": 0, "suspended": 0, "pending_deposits": pending}
    for row in rows:
        state = row["status"]
        expiry = row["expires_at"]
        if state == "active":
            if expiry is None or expiry <= now:
                state = "expired"
            else:
                counts["active"] += 1
                if expiry <= now + timedelta(days=7):
                    counts["expiring"] += 1
        if state in {"expired", "draft", "suspended"}:
            counts[state] += 1
        tenants.append({**tenant_snapshot(row), "effective_status": state,
                        "revision": tenant_revision(row)})
    for row in audit:
        row["created_at"] = row["created_at"].isoformat(timespec="seconds") + "Z"
    return {"counts": counts, "tenants": tenants, "venues": venues, "audit": audit,
            "server_time": now.isoformat(timespec="seconds") + "Z"}


def save_from_request(data, actor_id):
    mode = data.get("mode")
    if not isinstance(mode, str) or mode not in {"create", "update"} or not isinstance(data.get("tenant"), dict):
        raise ValueError("请选择创建或更新，并提交有效租户配置")
    raw = data["tenant"]
    keys = ("slug", "brand_name", "contact_name", "contact_username", "venue_ids", "status", "expires_at",
            "announcements_enabled")
    if any(key not in raw for key in keys):
        raise ValueError("租户配置不完整，请刷新后重试")
    config = {key: raw[key] for key in keys}
    expiry = config["expires_at"]
    if expiry is not None:
        if not isinstance(expiry, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", expiry):
            raise ValueError("到期时间必须为有效 UTC 时间")
        try:
            config["expires_at"] = datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError("到期时间无效") from exc
    revision = data.get("revision")
    if mode == "update" and (not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision)):
        raise ValueError("缺少配置版本，请刷新后重新编辑")
    save_tenant(config, create=mode == "create", actor_id=actor_id,
                expected_revision=revision if mode == "update" else None)


def allowed_admin(user):
    return user["id"] in admin_ids()
