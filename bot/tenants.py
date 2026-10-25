"""Public display subscriptions, configured only by the local operator."""
import argparse
import hashlib
from datetime import datetime, timezone
import json
import re

from db import get_conn

SLUG = re.compile(r"[a-z0-9][a-z0-9-]{2,31}")
USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")
CLUB_IDS = {1, 2, 3, 4, 5, 6}
DELIVERY_IDS = {7, 8, 9, 10}


class TenantUnavailable(ValueError):
    pass


class TenantConflict(ValueError):
    pass


def tenant_snapshot(row):
    result = {key: row[key] for key in (
        "slug", "brand_name", "contact_name", "contact_username", "status"
    )}
    ids = row["venue_ids"]
    result["venue_ids"] = sorted(set(json.loads(ids) if isinstance(ids, str) else ids))
    expiry = row["expires_at"]
    result["expires_at"] = expiry.isoformat(timespec="seconds") + "Z" if expiry else None
    return result


def tenant_revision(row):
    return hashlib.sha256(json.dumps(
        tenant_snapshot(row), ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS display_tenants (
                slug VARCHAR(32) PRIMARY KEY,
                brand_name VARCHAR(80) NOT NULL,
                contact_name VARCHAR(40) NOT NULL,
                contact_username VARCHAR(32) NOT NULL,
                venue_ids JSON NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'draft',
                expires_at DATETIME NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS tenant_admin_audit (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                tenant_slug VARCHAR(32) NOT NULL,
                actor_id BIGINT NULL,
                source VARCHAR(16) NOT NULL,
                action VARCHAR(16) NOT NULL,
                before_config JSON NULL,
                after_config JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_tenant_audit (tenant_slug, id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
    finally:
        conn.close()


def validate_config(config):
    if not isinstance(config.get("slug"), str) or not SLUG.fullmatch(config["slug"]):
        raise ValueError("入口标识需为 3–32 位小写字母、数字或连字符，首位不能为连字符")
    for field, limit in (("brand_name", 80), ("contact_name", 40)):
        value = config.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{field} 不能为空且不能超过 {limit} 字")
    if not isinstance(config.get("contact_username"), str) or not USERNAME.fullmatch(config["contact_username"]):
        raise ValueError("请输入有效的 Telegram 联系用户名，不包含 @")
    ids = config.get("venue_ids")
    if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in CLUB_IDS | DELIVERY_IDS for i in ids):
        raise ValueError("请选择至少一个展示项目：会所 ID 1–6 或外送 ID 7–10")
    if not isinstance(config.get("status"), str) or config["status"] not in {"draft", "active", "suspended"}:
        raise ValueError("状态仅支持 draft / active / suspended")
    expiry = config.get("expires_at")
    if expiry is not None and (not isinstance(expiry, datetime) or expiry.tzinfo is not None):
        raise ValueError("到期时间必须为 UTC 无时区 datetime")
    if config["status"] == "active" and (
        expiry is None or expiry <= datetime.now(timezone.utc).replace(tzinfo=None)
    ):
        raise ValueError("启用租户必须设置未来的 UTC 到期时间")


def load_tenant(slug):
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise TenantUnavailable("租户入口无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM display_tenants WHERE slug=%s", (slug,))
            row = cur.fetchone()
    finally:
        conn.close()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not row or row["status"] != "active":
        raise TenantUnavailable("此服务尚未开通或已暂停，请联系服务提供方")
    if not row["expires_at"] or row["expires_at"] <= now:
        raise TenantUnavailable("此服务已到期，请联系服务提供方续费")
    row["venue_ids"] = json.loads(row["venue_ids"]) if isinstance(row["venue_ids"], str) else row["venue_ids"]
    validate_config(row)
    return row


def public_config(row):
    return {key: row[key] for key in (
        "slug", "brand_name", "contact_name", "contact_username", "venue_ids"
    )}


def save_tenant(config, create=False, actor_id=None, expected_revision=None):
    validate_config(config)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            before = None
            cur.execute("SELECT id FROM venues WHERE id IN (" +
                        ",".join(["%s"] * len(config["venue_ids"])) + ") AND status='active'",
                        tuple(config["venue_ids"]))
            if {row["id"] for row in cur.fetchall()} != set(config["venue_ids"]):
                raise ValueError("所选会所不存在或未启用")
            values = tuple(config[key] for key in (
                "brand_name", "contact_name", "contact_username"
            )) + (json.dumps(sorted(set(config["venue_ids"]))), config["status"], config["expires_at"])
            if create:
                cur.execute(
                    "INSERT INTO display_tenants "
                    "(brand_name,contact_name,contact_username,venue_ids,status,expires_at,slug) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)", values + (config["slug"],),
                )
            else:
                cur.execute("SELECT * FROM display_tenants WHERE slug=%s FOR UPDATE", (config["slug"],))
                before = cur.fetchone()
                if before is None:
                    raise ValueError("租户不存在；请先使用 create")
                if expected_revision is not None and tenant_revision(before) != expected_revision:
                    raise TenantConflict("配置已被其他操作更新，请刷新后重新编辑")
                cur.execute(
                    "UPDATE display_tenants SET brand_name=%s,contact_name=%s,contact_username=%s,"
                    "venue_ids=%s,status=%s,expires_at=%s WHERE slug=%s", values + (config["slug"],),
                )
            cur.execute(
                "INSERT INTO tenant_admin_audit "
                "(tenant_slug,actor_id,source,action,before_config,after_config) VALUES (%s,%s,%s,%s,%s,%s)",
                (config["slug"], actor_id, "miniapp" if actor_id is not None else "cli",
                 "create" if create else "update",
                 json.dumps(tenant_snapshot(before), ensure_ascii=False) if before else None,
                 json.dumps(tenant_snapshot(config), ensure_ascii=False)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="本机管理员管理展示租户，人工收款后手动设置到期时间")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("list")
    for command in ("create", "update"):
        sub = commands.add_parser(command)
        sub.add_argument("slug")
        sub.add_argument("--brand", required=True)
        sub.add_argument("--contact-name", required=True)
        sub.add_argument("--contact-username", required=True)
        sub.add_argument("--venues", required=True, help="逗号分隔的会所 ID 1–6、外送 ID 7–10；未列出的外送默认不开放")
        sub.add_argument("--status", choices=["draft", "active", "suspended"], default="draft")
        sub.add_argument("--expires", help="UTC 到期时间 YYYY-MM-DDTHH:MM:SS，不含时区")
    args = parser.parse_args()
    if args.command == "init":
        ensure_tables()
        print("租户表已初始化，未创建或开放任何租户")
        return
    if args.command == "list":
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT slug,brand_name,status,expires_at,venue_ids FROM display_tenants ORDER BY slug")
                print(json.dumps(cur.fetchall(), ensure_ascii=False, default=str, indent=2))
        finally:
            conn.close()
        return
    config = {
        "slug": args.slug, "brand_name": args.brand, "contact_name": args.contact_name,
        "contact_username": args.contact_username,
        "venue_ids": [int(value) for value in args.venues.split(",")],
        "status": args.status, "expires_at": datetime.fromisoformat(args.expires) if args.expires else None,
    }
    save_tenant(config, create=args.command == "create")
    print(f"配置已保存：{args.slug}（{args.status}）")
    print(f"小程序入口：https://t.me/haotianzhaopian_bot?startapp=tenant_{args.slug}")


if __name__ == "__main__":
    main()
