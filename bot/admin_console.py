"""Authenticated owner-only display tenant management."""
from datetime import datetime, timedelta, timezone
import json
import re
import time
from urllib.parse import parse_qsl

from db import get_conn
from tenants import save_tenant, tenant_revision, tenant_snapshot
from visit_stats import admin_ids, verify_init_data, INIT_DATA_MAX_AGE_SECONDS

TENANT_PAGE_SIZE = 50
TENANT_STATUSES = {"all", "active", "expiring", "expired", "draft", "suspended"}


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


def authenticate_admin_request(init_data, action, primary_token, finance_token=None,
                               finance_owner_id=None):
    user = authenticate(init_data, primary_token)
    if user is not None:
        return user
    if not isinstance(action, str) or not action.startswith("admin_finance_"):
        return None
    if not isinstance(finance_owner_id, str) or not finance_owner_id.isdecimal():
        return None
    user = authenticate(init_data, finance_token)
    if user is None or user["id"] != int(finance_owner_id):
        return None
    return user


def is_tenant_session(init_data):
    return dict(parse_qsl(init_data)).get("start_param", "").startswith("tenant_")


def dashboard(tenant_page=1, tenant_search="", tenant_status="all"):
    if type(tenant_page) is not int or tenant_page < 1:
        raise ValueError("租户页码无效，请刷新后重试")
    if not isinstance(tenant_search, str) or len(tenant_search) > 100:
        raise ValueError("租户搜索内容不能超过 100 个字符")
    if not isinstance(tenant_status, str) or tenant_status not in TENANT_STATUSES:
        raise ValueError("租户状态筛选无效")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # TIMESTAMP reads use the session zone; expiry DATETIME values are already UTC.
            cur.execute("SET time_zone = '+00:00'")
            cur.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(status='active' AND expires_at>%s),0) AS active, "
                "COALESCE(SUM(status='active' AND expires_at>%s AND expires_at<=%s),0) AS expiring, "
                "COALESCE(SUM(status='active' AND (expires_at IS NULL OR expires_at<=%s)),0) AS expired, "
                "COALESCE(SUM(status='draft'),0) AS draft, "
                "COALESCE(SUM(status='suspended'),0) AS suspended FROM display_tenants",
                (now, now, now + timedelta(days=7), now),
            )
            tenant_counts = cur.fetchone()
            conditions = []
            parameters = []
            search = tenant_search.strip()
            if search:
                escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                conditions.append(
                    "(slug LIKE %s OR brand_name LIKE %s OR contact_name LIKE %s "
                    "OR contact_username LIKE %s)"
                )
                parameters.extend([pattern] * 4)
            if tenant_status == "active":
                conditions.append("status='active' AND expires_at>%s")
                parameters.append(now)
            elif tenant_status == "expiring":
                conditions.append("status='active' AND expires_at>%s AND expires_at<=%s")
                parameters.extend((now, now + timedelta(days=7)))
            elif tenant_status == "expired":
                conditions.append("status='active' AND (expires_at IS NULL OR expires_at<=%s)")
                parameters.append(now)
            elif tenant_status in {"draft", "suspended"}:
                conditions.append("status=%s")
                parameters.append(tenant_status)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            cur.execute("SELECT COUNT(*) AS total FROM display_tenants" + where, tuple(parameters))
            filtered_total = cur.fetchone()["total"]
            total_pages = max(1, (filtered_total + TENANT_PAGE_SIZE - 1) // TENANT_PAGE_SIZE)
            tenant_page = min(tenant_page, total_pages)
            cur.execute(
                "SELECT * FROM display_tenants" + where
                + " ORDER BY created_at DESC, slug LIMIT %s OFFSET %s",
                tuple(parameters) + (TENANT_PAGE_SIZE, (tenant_page - 1) * TENANT_PAGE_SIZE),
            )
            rows = cur.fetchall()
            cur.execute(
                "SELECT id,name FROM venues WHERE status='active' AND id IN (1,2,3,4,5,6,7,8,9,10) ORDER BY id"
            )
            venues = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS count FROM wallet_deposits WHERE status='pending'")
            pending = cur.fetchone()["count"]
            cur.execute(
                "SELECT id,tenant_slug,actor_id,source,action,before_config,after_config,created_at "
                "FROM tenant_admin_audit ORDER BY id DESC LIMIT 30"
            )
            audit = cur.fetchall()
    finally:
        conn.close()
    tenants = []
    counts = {key: int(tenant_counts[key]) for key in
              ("total", "active", "expiring", "expired", "draft", "suspended")}
    counts["pending_deposits"] = pending
    for row in rows:
        state = row["status"]
        expiry = row["expires_at"]
        expiring_soon = False
        if state == "active":
            if expiry is None or expiry <= now:
                state = "expired"
            elif expiry <= now + timedelta(days=7):
                expiring_soon = True
        tenants.append({**tenant_snapshot(row), "effective_status": state,
                        "expiring_soon": expiring_soon, "revision": tenant_revision(row)})
    for row in audit:
        for key in ("before_config", "after_config"):
            if isinstance(row.get(key), str):
                row[key] = json.loads(row[key])
        row["created_at"] = row["created_at"].isoformat(timespec="seconds") + "Z"
    return {"counts": counts, "tenants": tenants, "venues": venues, "audit": audit,
            "tenant_pagination": {"page": tenant_page, "page_size": TENANT_PAGE_SIZE,
                                  "filtered_total": filtered_total, "total_pages": total_pages,
                                  "search": tenant_search.strip(), "status": tenant_status},
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
