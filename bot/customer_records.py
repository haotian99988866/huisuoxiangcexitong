"""Owner-managed offline consumption records, separate from the wallet."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import re
import threading
import pymysql

from db import get_conn
from visit_stats import admin_ids

logger = logging.getLogger(__name__)
HEX_ID = re.compile(r"^[0-9a-f]{32}$")


class RecordConflict(ValueError):
    pass


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS crm_customers (
                user_id BIGINT PRIMARY KEY, alias VARCHAR(128) NOT NULL DEFAULT '',
                preferences TEXT NOT NULL, avoidances TEXT NOT NULL, notes TEXT NOT NULL,
                revision INT NOT NULL DEFAULT 1, created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS crm_consumptions (
                id CHAR(32) PRIMARY KEY, user_id BIGINT NOT NULL,
                service_kind VARCHAR(16) NOT NULL DEFAULT 'venue',
                venue_id INT NULL, staff_id BIGINT NULL, delivery_person_id BIGINT NULL,
                service VARCHAR(128) NOT NULL, occurred_at DATETIME NOT NULL,
                amount DECIMAL(12,2) NOT NULL, refunded DECIMAL(12,2) NOT NULL DEFAULT 0,
                status VARCHAR(16) NOT NULL DEFAULT 'valid', note TEXT NOT NULL,
                revision INT NOT NULL DEFAULT 1, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                INDEX idx_crm_customer (user_id,occurred_at),
                INDEX idx_crm_delivery_person (user_id,delivery_person_id,status),
                FOREIGN KEY (user_id) REFERENCES crm_customers(user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            for name, definition in (
                ("service_kind", "VARCHAR(16) NOT NULL DEFAULT 'venue'"),
                ("delivery_person_id", "BIGINT NULL"),
            ):
                cur.execute(f"SHOW COLUMNS FROM crm_consumptions LIKE '{name}'")
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE crm_consumptions ADD COLUMN {name} {definition}")
            cur.execute("SHOW COLUMNS FROM crm_consumptions LIKE 'venue_id'")
            venue_column = cur.fetchone()
            if venue_column and venue_column.get("Null") != "YES":
                cur.execute("ALTER TABLE crm_consumptions MODIFY COLUMN venue_id INT NULL")
            cur.execute("SHOW INDEX FROM crm_consumptions WHERE Key_name='idx_crm_delivery_person'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE crm_consumptions ADD INDEX idx_crm_delivery_person "
                            "(user_id,delivery_person_id,status)")
            cur.execute("""CREATE TABLE IF NOT EXISTS crm_audit (
                request_id CHAR(32) PRIMARY KEY, request_hash CHAR(64) NOT NULL,
                actor_id BIGINT NOT NULL, user_id BIGINT NOT NULL, action VARCHAR(32) NOT NULL,
                record_id CHAR(32) NULL, reason VARCHAR(500) NOT NULL,
                before_json JSON NULL, after_json JSON NOT NULL, created_at DATETIME NOT NULL,
                INDEX idx_crm_audit (user_id,created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS crm_contact_deliveries (
                id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, admin_id BIGINT NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending', attempts INT NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL, next_attempt DATETIME NOT NULL, sent_at DATETIME NULL,
                INDEX idx_crm_contact_pending (status,next_attempt),
                INDEX idx_crm_contact_user (user_id,created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("SHOW COLUMNS FROM crm_customers LIKE 'telegram_user_id'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE crm_customers ADD COLUMN telegram_user_id BIGINT NULL, "
                            "ADD UNIQUE KEY crm_telegram_identity (telegram_user_id)")
            cur.execute("UPDATE crm_customers SET telegram_user_id=user_id "
                        "WHERE user_id>0 AND telegram_user_id IS NULL")
        conn.commit()
    finally:
        conn.close()


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _positive_id(value, label):
    if type(value) is not int or not 0 < value <= 9007199254740991:
        raise ValueError(f"{label}必须为有效数字编号")
    return value


def _customer_id(value):
    if type(value) is not int or value == 0 or abs(value) > 9007199254740991:
        raise ValueError("客户档案编号无效")
    return value


def _text(value, label, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{label}必须是最多{maximum}字的文字")
    value = value.strip()
    if required and not value:
        raise ValueError(f"请填写{label}")
    return value


def _money(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,10}(?:\.\d{1,2})?", value):
        raise ValueError(f"{label}须为美元金额，最多两位小数")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label}无效") from exc
    if result > Decimal("9999999999.99"):
        raise ValueError(f"{label}超过可登记范围")
    return result.quantize(Decimal("0.01"))


def _occurred(value):
    if not isinstance(value, str):
        raise ValueError("请填写消费时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("消费时间必须带时区")
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError as exc:
        raise ValueError("消费时间无效，请重新选择") from exc
    if parsed.year < 2000 or parsed > _utcnow() + timedelta(minutes=5):
        raise ValueError("只能登记已发生的消费，不能填写未来时间")
    return parsed.replace(microsecond=0)


def _json(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds") + "Z"
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unsupported record value {type(value).__name__}")


def _public(row):
    return json.loads(json.dumps(row, default=_json))


def _identity(cur, user_id):
    cur.execute("SELECT user_id,telegram_user_id,alias FROM crm_customers "
                "WHERE user_id=%s OR telegram_user_id=%s", (user_id, user_id))
    profile = cur.fetchone()
    if profile:
        user_id = profile["user_id"]
    if profile and profile["telegram_user_id"] is None:
        return {"user_id": user_id, "telegram_user_id": None, "username": "", "display_name": profile["alias"]}
    telegram_id = profile["telegram_user_id"] if profile else user_id
    cur.execute("""SELECT user_id,username,display_name FROM (
        SELECT tg_user_id AS user_id,username,display_name,1 AS priority FROM bot_users
        UNION ALL SELECT user_id,username,display_name,2 AS priority FROM analytics_customers
        ) identities WHERE user_id=%s ORDER BY priority LIMIT 1""", (telegram_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError("未找到已验证的 Telegram 客户，请让客户先打开 App 或启动机器人，再用数字 ID 建档")
    return {**row, "user_id": user_id, "telegram_user_id": telegram_id}


def _summary(cur, user_id):
    cur.execute("""SELECT COUNT(*) AS visits,COALESCE(SUM(amount),0) AS paid,
        COALESCE(SUM(refunded),0) AS refunded,COALESCE(SUM(amount-refunded),0) AS net,
        MAX(occurred_at) AS last_visit FROM crm_consumptions WHERE user_id=%s AND status='valid'""",
                (user_id,))
    return _public(cur.fetchone())


def _records(cur, user_id, page):
    cur.execute("SELECT COUNT(*) AS n FROM crm_consumptions WHERE user_id=%s", (user_id,))
    total = cur.fetchone()["n"]
    cur.execute("""SELECT c.*,COALESCE(v.name,IF(c.service_kind='delivery','西港外送','会所已下架')) AS venue_name,
        m.code AS staff_code,p.display_name AS delivery_person_name
        FROM crm_consumptions c LEFT JOIN venues v ON v.id=c.venue_id
        LEFT JOIN staff_members m ON m.id=c.staff_id
        LEFT JOIN delivery_people p ON p.id=c.delivery_person_id WHERE c.user_id=%s
        ORDER BY c.occurred_at DESC,c.id DESC LIMIT 20 OFFSET %s""", (user_id, (page - 1) * 20))
    return _public(cur.fetchall()), total


def _staff_history(cur, user_id, page=1, limit=20):
    cur.execute("""SELECT COUNT(*) AS total FROM (
        SELECT 'venue' AS person_type,staff_id AS person_id FROM crm_consumptions
        WHERE user_id=%s AND status='valid' AND staff_id IS NOT NULL GROUP BY staff_id
        UNION ALL
        SELECT 'delivery' AS person_type,delivery_person_id AS person_id FROM crm_consumptions
        WHERE user_id=%s AND status='valid' AND delivery_person_id IS NOT NULL
        GROUP BY delivery_person_id
        ) people""", (user_id, user_id))
    total = cur.fetchone()["total"]
    cur.execute("""SELECT history.* FROM (
        SELECT 'venue' AS person_type,c.staff_id AS person_id,c.staff_id,NULL AS delivery_person_id,
            m.code AS staff_code,NULL AS person_name,m.ended_at,v.name AS venue_name,
            COUNT(*) AS visits,MIN(c.occurred_at) AS first_visit,MAX(c.occurred_at) AS last_visit,
            SUM(c.amount) AS paid,SUM(c.refunded) AS refunded,SUM(c.amount-c.refunded) AS net
        FROM crm_consumptions c JOIN staff_members m ON m.id=c.staff_id
        JOIN venues v ON v.id=c.venue_id WHERE c.user_id=%s AND c.status='valid'
        GROUP BY c.staff_id,m.code,m.ended_at,v.name
        UNION ALL
        SELECT 'delivery' AS person_type,c.delivery_person_id AS person_id,NULL AS staff_id,
            c.delivery_person_id AS delivery_person_id,NULL AS staff_code,
            COALESCE(NULLIF(p.display_name,''),CONCAT('外送档案 ',p.id)) AS person_name,
            NULL AS ended_at,'西港外送' AS venue_name,COUNT(*) AS visits,
            MIN(c.occurred_at) AS first_visit,MAX(c.occurred_at) AS last_visit,
            SUM(c.amount) AS paid,SUM(c.refunded) AS refunded,SUM(c.amount-c.refunded) AS net
        FROM crm_consumptions c JOIN delivery_people p ON p.id=c.delivery_person_id
        WHERE c.user_id=%s AND c.status='valid' AND c.delivery_person_id IS NOT NULL
        GROUP BY c.delivery_person_id,p.id,p.display_name
        ) history
        ORDER BY last_visit DESC,person_id DESC LIMIT %s OFFSET %s""",
                (user_id, user_id, limit, (page - 1) * limit))
    return _public(cur.fetchall()), total


def _consumption_source(cur, data):
    kind = data.get("service_kind", "venue")
    if kind == "venue":
        venue_id = _positive_id(data.get("venue_id"), "会所")
        if venue_id not in {1, 2, 4, 5, 6}:
            raise ValueError("请选择有效会所")
        cur.execute("SELECT id FROM venues WHERE id=%s", (venue_id,))
        if cur.fetchone() is None:
            raise ValueError("会所不存在")
        staff_id = data.get("staff_id")
        if staff_id is not None:
            _positive_id(staff_id, "人员档案")
        delivery_person_id = None
    elif kind == "delivery":
        if data.get("staff_id") not in (None, ""):
            raise ValueError("外送消费不能关联会所技师档案")
        venue_id, staff_id = None, None
        delivery_person_id = data.get("delivery_person_id")
        if delivery_person_id not in (None, ""):
            _positive_id(delivery_person_id, "外送人员档案")
            cur.execute(
                "SELECT id FROM delivery_people WHERE id=%s AND venue_id IN (7,8,9,10) "
                "AND deleted_at IS NULL AND merged_into_id IS NULL",
                (delivery_person_id,),
            )
            if cur.fetchone() is None:
                raise ValueError("请选择有效的外送人员档案")
        else:
            delivery_person_id = None
    else:
        raise ValueError("消费类型无效")
    return kind, venue_id, staff_id, delivery_person_id


def _page(value):
    if type(value) is not int or not 1 <= value <= 10000:
        raise ValueError("分页无效")
    return value


def directory(data):
    page = _page(data.get("page", 1))
    query = _text(data.get("query", ""), "搜索条件", 128).lstrip("@")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            search = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            base = """FROM (
                SELECT tg_user_id AS user_id FROM bot_users UNION SELECT user_id FROM analytics_customers
                UNION SELECT user_id FROM crm_customers WHERE telegram_user_id IS NULL
                ) ids LEFT JOIN bot_users b ON b.tg_user_id=ids.user_id
                LEFT JOIN analytics_customers a ON a.user_id=ids.user_id
                LEFT JOIN crm_customers c ON c.telegram_user_id=ids.user_id
                    OR (c.telegram_user_id IS NULL AND c.user_id=ids.user_id)
                WHERE CAST(ids.user_id AS CHAR)=%s OR COALESCE(b.username,a.username,'') LIKE %s
                OR COALESCE(b.display_name,a.display_name,'') LIKE %s OR COALESCE(c.alias,'') LIKE %s"""
            args = (query, search, search, search)
            cur.execute("SELECT COUNT(*) AS total " + base, args)
            total = cur.fetchone()["total"]
            cur.execute("""SELECT COALESCE(c.user_id,ids.user_id) AS user_id,
                CASE WHEN ids.user_id>0 THEN ids.user_id ELSE NULL END AS telegram_user_id,
                COALESCE(b.username,a.username,'') AS username,
                COALESCE(b.display_name,a.display_name,'') AS display_name,COALESCE(c.alias,'') AS alias,
                c.user_id IS NOT NULL AS registered,
                (SELECT COUNT(*) FROM crm_consumptions r WHERE r.user_id=COALESCE(c.user_id,ids.user_id) AND r.status='valid') AS visits,
                (SELECT COALESCE(SUM(r.amount-r.refunded),0) FROM crm_consumptions r
                 WHERE r.user_id=COALESCE(c.user_id,ids.user_id) AND r.status='valid') AS net """ + base +
                        " ORDER BY c.updated_at DESC,ids.user_id DESC LIMIT 20 OFFSET %s",
                        (*args, (page - 1) * 20))
            rows = _public(cur.fetchall())
        return {"customers": rows, "total": total, "page": page}
    finally:
        conn.close()


def detail(data):
    user_id = _customer_id(data.get("user_id"))
    page = _page(data.get("page", 1))
    staff_page = _page(data.get("staff_page", 1))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            identity = _identity(cur, user_id)
            user_id = identity["user_id"]
            cur.execute("SELECT * FROM crm_customers WHERE user_id=%s", (user_id,))
            profile = cur.fetchone() or {"user_id": user_id, "alias": "", "preferences": "",
                                         "avoidances": "", "notes": "", "revision": 0}
            records, total = _records(cur, user_id, page)
            cur.execute("""SELECT id,name FROM venues WHERE id IN (1,2,4,5,6) AND status='active' ORDER BY id""")
            venues = cur.fetchall()
            cur.execute("""SELECT p.id,p.venue_id,COALESCE(NULLIF(p.display_name,''),CONCAT('外送档案 ',p.id))
                AS display_name,v.name AS source_name FROM delivery_people p
                JOIN venues v ON v.id=p.venue_id
                WHERE p.venue_id IN (7,8,9,10) AND p.deleted_at IS NULL AND p.merged_into_id IS NULL
                ORDER BY p.venue_id,p.display_name,p.id LIMIT 1001""")
            delivery_people = cur.fetchall()
            if len(delivery_people) > 1000:
                raise ValueError("外送人员档案超过1000位，暂时无法登记")
            cur.execute("""SELECT id,venue_id,code,ended_at FROM staff_members
                WHERE blocked=0 AND (venue_id<>6 OR adult_confirmed=1) ORDER BY venue_id,code,id LIMIT 1001""")
            staff = cur.fetchall()
            if len(staff) > 1000:
                raise ValueError("人员档案超过1000位，需升级人员选择分页后使用")
            cur.execute("""SELECT request_id,actor_id,action,record_id,reason,created_at
                FROM crm_audit WHERE user_id=%s ORDER BY created_at DESC,request_id DESC LIMIT 30""", (user_id,))
            audit = _public(cur.fetchall())
            cur.execute("""SELECT status,attempts,created_at,sent_at FROM crm_contact_deliveries
                WHERE user_id=%s ORDER BY id DESC LIMIT 10""", (user_id,))
            contacts = _public(cur.fetchall())
            summary = _summary(cur, user_id)
            staff_history, staff_total = _staff_history(cur, user_id, staff_page)
        return {"customer": {**identity, **_public(profile)}, "summary": summary, "records": records,
                "total": total, "page": page, "venues": venues, "staff": _public(staff),
                "delivery_people": _public(delivery_people),
                "audit": audit, "contacts": contacts, "staff_history": staff_history,
                "staff_total": staff_total, "staff_page": staff_page}
    finally:
        conn.close()


def mine(user_id, data):
    page = _page(data.get("page", 1))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM crm_customers WHERE telegram_user_id=%s", (user_id,))
            profile = cur.fetchone()
            user_id = profile["user_id"] if profile else user_id
            summary = _summary(cur, user_id)
            records, total = _records(cur, user_id, page)
        keys = ("id", "service_kind", "venue_name", "staff_code", "delivery_person_name",
                "service", "occurred_at", "amount", "refunded", "status")
        return {"summary": summary, "records": [{key: row[key] for key in keys} for row in records],
                "total": total, "page": page}
    finally:
        conn.close()


def save(data, actor_id):
    action = data.get("action")
    if action not in {"admin_customer_save", "admin_customer_create", "admin_customer_bind",
                      "admin_consumption_save", "admin_consumption_void"}:
        raise ValueError("客户操作无效")
    request_id = data.get("request_id")
    if not isinstance(request_id, str) or not HEX_ID.fullmatch(request_id):
        raise ValueError("缺少有效操作编号，请重新打开表单")
    # Negative keys are internal offline customer IDs, never Telegram identities.
    user_id = -(int(request_id[:13], 16) + 1) if action == "admin_customer_create" else _customer_id(data.get("user_id"))
    revision = data.get("revision")
    if type(revision) is not int or revision < 0:
        raise ValueError("记录版本无效，请刷新")
    if data.get("confirmed") is not True:
        raise ValueError("请核对并明确确认登记")
    business_data = {key: value for key, value in data.items() if key != "init_data"}
    digest = hashlib.sha256(json.dumps(business_data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    legacy_digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    reason = _text(data.get("reason", ""), "修改原因", 500, revision > 0)
    now = _utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if action != "admin_customer_create":
                user_id = _identity(cur, user_id)["user_id"]
            # Serialize all edits for a customer, including initial profile creation.
            cur.execute("SELECT user_id FROM analytics_customers WHERE user_id=%s FOR UPDATE", (user_id,))
            cur.execute("SELECT tg_user_id FROM bot_users WHERE tg_user_id=%s FOR UPDATE", (user_id,))
            cur.execute("SELECT * FROM crm_audit WHERE request_id=%s", (request_id,))
            prior = cur.fetchone()
            if prior:
                if prior["request_hash"] not in {digest, legacy_digest} or prior["actor_id"] != actor_id:
                    raise RecordConflict("操作编号已用于不同内容，请先刷新核对")
                return {"ok": True, "record_id": prior["record_id"], "user_id": prior["user_id"], "replayed": True}
            cur.execute("SELECT * FROM crm_customers WHERE user_id=%s FOR UPDATE", (user_id,))
            customer = cur.fetchone()
            if action == "admin_customer_bind":
                if not customer or revision != customer["revision"]:
                    raise RecordConflict("客户档案已变更，请刷新后绑定")
                if customer["telegram_user_id"] is not None:
                    raise ValueError("该客户已绑定 Telegram，不允许直接更换账号")
                telegram_id = _positive_id(data.get("telegram_user_id"), "Telegram ID")
                _identity(cur, telegram_id)
                cur.execute("SELECT user_id FROM crm_customers WHERE telegram_user_id=%s FOR UPDATE", (telegram_id,))
                if cur.fetchone():
                    raise RecordConflict("此 Telegram 账号已有客户档案，请人工核对，不能覆盖或自动合并")
                before, record_id = customer, None
                cur.execute("UPDATE crm_customers SET telegram_user_id=%s,revision=revision+1,updated_at=%s WHERE user_id=%s",
                            (telegram_id, now, user_id))
                cur.execute("SELECT * FROM crm_customers WHERE user_id=%s", (user_id,))
                after = cur.fetchone()
            elif action in {"admin_customer_save", "admin_customer_create"}:
                before = customer
                if user_id > 0 and customer is None:
                    cur.execute("SELECT user_id FROM crm_customers WHERE telegram_user_id=%s FOR UPDATE", (user_id,))
                    linked = cur.fetchone()
                    if linked:
                        raise RecordConflict("该账号已绑定现有客户，请刷新列表并打开对应档案")
                if revision != (customer["revision"] if customer else 0):
                    raise RecordConflict("客户档案已变更，请刷新后重新编辑")
                values = [_text(data.get(key, ""), label, maximum) for key, label, maximum in (
                    ("alias", "客户称呼", 128), ("preferences", "服务偏好", 2000),
                    ("avoidances", "避免安排", 2000), ("notes", "内部备注", 2000))]
                if user_id < 0 and not values[0]:
                    raise ValueError("请填写客户称呼")
                if customer:
                    cur.execute("""UPDATE crm_customers SET alias=%s,preferences=%s,avoidances=%s,notes=%s,
                        revision=revision+1,updated_at=%s WHERE user_id=%s""", (*values, now, user_id))
                else:
                    cur.execute("""INSERT INTO crm_customers(user_id,telegram_user_id,alias,preferences,avoidances,notes,
                        revision,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,1,%s,%s)""",
                                (user_id, user_id if user_id > 0 else None, *values, now, now))
                cur.execute("SELECT * FROM crm_customers WHERE user_id=%s", (user_id,))
                after, record_id = cur.fetchone(), None
            else:
                if customer is None:
                    raise ValueError("请先建立客户档案，再登记消费")
                record_id = data.get("record_id") if revision else request_id
                if not isinstance(record_id, str) or not HEX_ID.fullmatch(record_id):
                    raise ValueError("消费记录编号无效")
                cur.execute("SELECT * FROM crm_consumptions WHERE id=%s FOR UPDATE", (record_id,))
                before = cur.fetchone()
                if before and before["user_id"] != user_id:
                    raise ValueError("该消费记录不属于所选客户")
                if revision != (before["revision"] if before else 0) or (before and before["status"] != "valid"):
                    raise RecordConflict("消费记录已变更或作废，请刷新后核对")
                if action == "admin_consumption_void":
                    if before is None:
                        raise ValueError("消费记录不存在")
                    cur.execute("UPDATE crm_consumptions SET status='void',revision=revision+1,updated_at=%s WHERE id=%s",
                                (now, record_id))
                else:
                    service_kind, venue_id, staff_id, delivery_person_id = _consumption_source(cur, data)
                    occurred = _occurred(data.get("occurred_at"))
                    if staff_id is not None:
                        cur.execute("SELECT * FROM staff_members WHERE id=%s", (staff_id,))
                        staff = cur.fetchone()
                        if not staff or staff["venue_id"] != venue_id or staff["blocked"] or (
                                venue_id == 6 and not staff["adult_confirmed"]):
                            raise ValueError("请选择该会所允许展示的人员档案")
                        local_occurred = occurred + timedelta(hours=7)
                        if local_occurred < staff["active_from"] or (staff["ended_at"] and local_occurred >= staff["ended_at"]):
                            raise ValueError("消费时间不属于该人员档案，请核对换人时间或选择正确的历史人员")
                    service = _text(data.get("service"), "消费项目", 128, True)
                    note = _text(data.get("note", ""), "消费内部备注", 2000)
                    amount, refunded = _money(data.get("amount"), "实付金额"), _money(data.get("refunded", "0"), "已退款金额")
                    if amount <= 0 or refunded > amount:
                        raise ValueError("实付金额必须大于0，已退款金额不能超过实付金额")
                    cur.execute("""INSERT INTO crm_consumptions(id,user_id,service_kind,venue_id,staff_id,
                        delivery_person_id,service,occurred_at,
                        amount,refunded,status,note,revision,created_at,updated_at)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'valid',%s,1,%s,%s)
                        ON DUPLICATE KEY UPDATE service_kind=VALUES(service_kind),venue_id=VALUES(venue_id),
                        staff_id=VALUES(staff_id),delivery_person_id=VALUES(delivery_person_id),
                        service=VALUES(service),occurred_at=VALUES(occurred_at),amount=VALUES(amount),
                        refunded=VALUES(refunded),note=VALUES(note),revision=revision+1,updated_at=VALUES(updated_at)""",
                                (record_id, user_id, service_kind, venue_id, staff_id, delivery_person_id,
                                 service, occurred, amount, refunded, note, now, now))
                cur.execute("SELECT * FROM crm_consumptions WHERE id=%s", (record_id,))
                after = cur.fetchone()
            cur.execute("""INSERT INTO crm_audit(request_id,request_hash,actor_id,user_id,action,record_id,
                reason,before_json,after_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (request_id, digest, actor_id, user_id, action, record_id, reason,
                         json.dumps(before, default=_json) if before else None, json.dumps(after, default=_json), now))
        conn.commit()
        return {"ok": True, "record_id": record_id, "user_id": user_id}
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if exc.args[0] == 1062:
            raise RecordConflict("操作编号或账号档案已存在，请用原操作编号重试或刷新列表核对") from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def contact(user_id):
    targets = sorted(admin_ids())
    if not targets:
        raise RuntimeError("客户提醒未配置接收管理员")
    now = _utcnow()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM crm_customers WHERE telegram_user_id=%s FOR UPDATE", (user_id,))
            profile = cur.fetchone()
            if profile is None:
                return {"queued": False, "reason": "no_customer_profile"}
            user_id = profile["user_id"]
            cur.execute("SELECT MAX(created_at) AS last_at FROM crm_contact_deliveries WHERE user_id=%s", (user_id,))
            last = cur.fetchone()["last_at"]
            if last and last > now - timedelta(minutes=5):
                return {"queued": False, "reason": "recent_reminder"}
            cur.executemany("""INSERT INTO crm_contact_deliveries(user_id,admin_id,created_at,next_attempt)
                VALUES(%s,%s,%s,%s)""", [(user_id, target, now, now) for target in targets])
        conn.commit()
        return {"queued": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _contact_card(cur, user_id):
    identity = _identity(cur, user_id)
    cur.execute("SELECT * FROM crm_customers WHERE user_id=%s", (user_id,))
    profile = cur.fetchone()
    summary = _summary(cur, user_id)
    cur.execute("""SELECT c.service,c.occurred_at,c.amount-c.refunded AS net,
        COALESCE(v.name,IF(c.service_kind='delivery','西港外送','会所已下架')) AS venue,
        m.code,p.display_name AS delivery_person_name
        FROM crm_consumptions c LEFT JOIN venues v ON v.id=c.venue_id
        LEFT JOIN staff_members m ON m.id=c.staff_id
        LEFT JOIN delivery_people p ON p.id=c.delivery_person_id
        WHERE c.user_id=%s AND c.status='valid'
        ORDER BY c.occurred_at DESC,c.id DESC LIMIT 3""", (user_id,))
    records = cur.fetchall()
    staff_history, staff_total = _staff_history(cur, user_id, limit=5)
    escape = lambda value: str(value)
    lines = ["客户从 App 打开联系入口", "仅表示点击联系，不代表已发送消息或发生消费。",
             f"客户档案：{abs(user_id)} · Telegram ID：{identity['telegram_user_id'] or '未绑定'}",
             f"账号：{escape('@' + identity['username'] if identity['username'] else '无用户名记录')}",
             f"名称：{escape(identity['display_name'] or '无名称记录')}",
             f"称呼：{escape(profile['alias'] or '未设置')}",
             f"有效消费：{summary['visits']}笔 · 净累计 ${summary['net']}"]
    for field, label in (("preferences", "服务偏好"), ("avoidances", "避免安排"), ("notes", "内部备注")):
        lines.append(f"{label}：{escape(profile[field][:500] or '未登记')}")
    if any(len(profile[field]) > 500 for field in ("preferences", "avoidances", "notes")):
        lines.append("长备注已截取，完整内容请在管理台查看。")
    lines.append(f"曾消费服务人员：{staff_total}位（按有效消费记录关联）")
    for staff in staff_history:
        if staff["person_type"] == "delivery":
            lines.append(f"外送人员：{staff['person_name']} · 档案{staff['delivery_person_id']}"
                         f" · {staff['visits']}次 · 净累计 ${staff['net']}")
        else:
            lines.append(f"技师：{staff['venue_name']} · 工号{staff['staff_code']} · 档案{staff['staff_id']}"
                         f" · {staff['visits']}次 · 净累计 ${staff['net']}"
                         f"{' · 历史人员' if staff['ended_at'] else ''}")
    if staff_total > len(staff_history):
        lines.append("仅列最近消费的5位服务人员，完整关联请查看客户档案。")
    for record in records:
        when = (record["occurred_at"] + timedelta(hours=7)).strftime("%m-%d %H:%M")
        person = (f"外送人员{record['delivery_person_name']}" if record["delivery_person_name"]
                  else "未关联外送人员" if record.get("service_kind") == "delivery"
                  else f"工号{record['code'] or '未关联'}")
        lines.append(f"最近：{when} · {escape(record['venue'])} · {escape(person)}"
                     f" · {escape(record['service'])} · ${record['net']}")
    card = "\n".join(lines)
    if len(card.encode("utf-16-le")) > 7600:
        card = card.encode("utf-16-le")[:7400].decode("utf-16-le", errors="ignore") + "\n完整内容请在管理台查看。"
    return card


def deliver_contacts(bot):
    import requests
    from telebot import apihelper
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('crm_contact_delivery',0) AS acquired")
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                return
            cur.execute("""SELECT * FROM crm_contact_deliveries WHERE status='pending'
                AND next_attempt<=%s ORDER BY id LIMIT 10""", (_utcnow(),))
            jobs = cur.fetchall()
            for job in jobs:
                if job["admin_id"] not in admin_ids():
                    logger.warning("Customer reminder recipient no longer authorized delivery_id=%s", job["id"])
                    cur.execute("UPDATE crm_contact_deliveries SET status='cancelled' WHERE id=%s", (job["id"],))
                    conn.commit()
                    continue
                try:
                    card = _contact_card(cur, job["user_id"])
                    keyboard = InlineKeyboardMarkup()
                    keyboard.add(InlineKeyboardButton("查看客户档案",
                        url=f"https://t.me/haotianzhaopian_bot?startapp=customer_{job['user_id']}"))
                    bot.send_message(job["admin_id"], card, reply_markup=keyboard)
                except (apihelper.ApiException, requests.RequestException):
                    attempts = job["attempts"] + 1
                    logger.exception("Customer contact delivery failed id=%s attempt=%s", job["id"], attempts)
                    cur.execute("""UPDATE crm_contact_deliveries SET attempts=%s,status=%s,next_attempt=%s WHERE id=%s""",
                                (attempts, "failed" if attempts >= 5 else "pending",
                                 _utcnow() + timedelta(seconds=min(3600, 60 * 2 ** attempts)), job["id"]))
                else:
                    cur.execute("""UPDATE crm_contact_deliveries SET status='sent',attempts=attempts+1,sent_at=%s
                        WHERE id=%s""", (_utcnow(), job["id"]))
                conn.commit()
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK('crm_contact_delivery')")
        conn.close()


def start_worker(bot):
    def run():
        while True:
            try:
                deliver_contacts(bot)
            except (ValueError, RuntimeError, pymysql.MySQLError):
                logger.exception("Customer contact reminder worker unavailable")
            threading.Event().wait(15)
    threading.Thread(target=run, name="customer-contact-reminders", daemon=True).start()
