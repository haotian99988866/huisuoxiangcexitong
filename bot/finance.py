"""Private personal-finance ledger for platform administrators."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import pymysql

from db import get_conn

MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
CENT = Decimal("0.01")
COMMISSION_CATALOG = (
    (1, "small-services", "小项·沐足/肩颈/头部/采耳/火罐/刮痧/洗耳/洗面/修脚/治脚气", "1.00", "29"),
    (1, "professional-pedicure", "专业修脚", "1.00", "29"),
    (1, "small-special", "甲沟炎/中耳炎/刺络/面部护理", "1.00", "69"),
    (1, "supreme-massage", "至尊按摩", "3.00", "99"),
    (1, "tcm-treatment", "中医理疗", "10.00", "109"),
    (1, "soft-massage", "柔式风情按摩", "10.00", "129"),
    (1, "diao-chan", "貂蝉风情采耳", "10.00", "139"),
    (1, "tcm-meridian", "中医经络", "10.00", "139"),
    (1, "prostate-care", "男士前列腺保养", "15.00", "169"),
    (1, "guben-peiyuan", "固本培元", "15.00", "179"),
    (1, "men-health", "男士魅力养生", "20.00", "229"),
    (1, "private-care", "男士私密养护", "20.00", "329"),
    (2, "board-1-member", "1号牌·会员", "50.00", "269"),
    (2, "board-1-half", "1号牌·半价", "30.00", "139"),
    (2, "board-2-member", "2号牌·会员", "50.00", "299"),
    (2, "board-2-half", "2号牌·半价", "30.00", "149"),
    (2, "board-3-member", "3号牌·会员", "50.00", "329"),
    (2, "board-3-half", "3号牌·半价", "30.00", "169"),
    (2, "board-5-member", "5号牌·会员", "60.00", "369"),
    (2, "board-5-half", "5号牌·半价", "30.00", "189"),
    (2, "board-6-member", "6号牌·会员", "60.00", "399"),
    (2, "board-6-half", "6号牌·半价", "30.00", "199"),
    (2, "board-7-member", "7号牌·会员", "60.00", "439"),
    (2, "board-7-half", "7号牌·半价", "30.00", "219"),
    (2, "board-8-member", "8号牌·会员", "60.00", "469"),
    (2, "board-8-half", "8号牌·半价", "30.00", "239"),
    (2, "board-9-member", "9号牌·会员", "70.00", "499"),
    (2, "board-9-half", "9号牌·半价", "35.00", "249"),
    (2, "mingxi-member", "名玺牌·会员", "100.00", "599"),
    (2, "mingxi-half", "名玺牌·半价", "0.00", "299"),
    (2, "japanese-korean-member", "日韩牌·会员", "80.00", "539"),
    (4, "5牌", "5牌", "10.00", "99"),
    (4, "6牌", "6牌", "20.00", "159"),
    (4, "7牌", "7牌", "25.00", "179"),
    (4, "8牌", "8牌", "35.00", "209"),
    (4, "9牌", "9牌", "40.00", "239"),
    (4, "1牌", "1牌", "45.00", "269"),
    (4, "0牌", "0牌", "50.00", "299"),
    (4, "2牌", "2牌", "60.00", "339"),
    (4, "car-model", "车模", "70.00", "369"),
    (4, "japanese-korean", "日韩", "90.00", "499"),
    (5, "supreme", "至尊", "10.00", "99"),
    (5, "beauty", "佳丽", "20.00", "139"),
    (5, "car-model", "车模", "20.00", "169"),
    (5, "empress-consort", "皇贵妃", "30.00", "199"),
    (5, "empress", "皇后", "30.00", "229"),
    (5, "1牌", "1牌", "40.00", "269"),
    (5, "3牌", "3牌", "45.00", "299"),
    (5, "0牌", "0牌", "60.00", "329"),
    (5, "leopard", "豹子", "70.00", "359"),
    (5, "japanese-korean", "日韩", "90.00", "499"),
    (6, "0牌", "0牌", "10.00", "99"),
    (6, "1牌", "1牌", "20.00", "129"),
    (6, "2牌", "2牌", "20.00", "149"),
    (6, "3牌", "3牌", "20.00", "169"),
    (6, "6牌艺人", "6牌艺人", "30.00", "199"),
    (6, "7牌公主", "7牌公主", "30.00", "229"),
    (6, "8牌佳丽", "8牌佳丽", "70.00", "329"),
    (6, "9牌贵妃", "9牌贵妃", "70.00", "369"),
    (6, "russian", "俄罗斯", "70.00", "429"),
    (6, "korean", "韩国牌", "0.00", None),
    (7, "delivery-order", "外送订单", "40.00", "170"),
    (8, "delivery-order", "外送订单", "40.00", "180"),
    (9, "delivery-order", "外送订单", "40.00", "160"),
    (10, "small-6", "小苹果·6牌", "40.00", "150"),
    (10, "small-8", "小苹果·8牌", "40.00", "170"),
    (10, "small-9", "小苹果·9牌", "40.00", "190"),
    (10, "small-q", "小苹果·Q牌", "40.00", "210"),
    (10, "small-t", "小苹果·T牌", "40.00", "230"),
    (10, "small-s", "小苹果·S牌", "40.00", "249"),
    (10, "small-leopard", "小苹果·豹子号", "40.00", "300"),
    (10, "small-beauty", "小苹果·丽人", "40.00", "330"),
    (10, "red-a", "红苹果·A牌", "40.00", "150"),
    (10, "red-d", "红苹果·D牌", "40.00", "170"),
    (10, "red-s", "红苹果·S牌", "40.00", "190"),
    (10, "red-q", "红苹果·Q牌", "40.00", "210"),
    (10, "red-t", "红苹果·T牌", "40.00", "230"),
)


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS finance_accounts (
                id BIGINT AUTO_INCREMENT PRIMARY KEY, owner_id BIGINT NOT NULL,name VARCHAR(80) NOT NULL,
                account_type VARCHAR(16) NOT NULL, opening_balance DECIMAL(12,2) NOT NULL DEFAULT 0,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at DATETIME NOT NULL, UNIQUE KEY uq_finance_account_name(owner_id,name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("SHOW COLUMNS FROM finance_accounts LIKE 'opening_balance'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE finance_accounts ADD COLUMN "
                            "opening_balance DECIMAL(12,2) NOT NULL DEFAULT 0")
            cur.execute("""CREATE TABLE IF NOT EXISTS finance_categories (
                id BIGINT AUTO_INCREMENT PRIMARY KEY, owner_id BIGINT NOT NULL,kind VARCHAR(16) NOT NULL,
                name VARCHAR(48) NOT NULL, is_default BOOLEAN NOT NULL DEFAULT FALSE,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at DATETIME NOT NULL, UNIQUE KEY uq_finance_category(owner_id,kind,name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("SHOW COLUMNS FROM finance_categories LIKE 'is_default'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE finance_categories ADD COLUMN "
                            "is_default BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("""UPDATE finance_categories SET is_default=TRUE
                WHERE (kind='income' AND name IN ('其他收入','利息收入'))
                   OR (kind='expense' AND name IN ('餐饮','交通','住房','账单','购物','娱乐','医疗','利息','其他'))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS finance_transactions (
                id CHAR(32) PRIMARY KEY, owner_id BIGINT NOT NULL,request_hash CHAR(64) NOT NULL,
                kind VARCHAR(20) NOT NULL,
                amount DECIMAL(12,2) NOT NULL, principal DECIMAL(12,2) NOT NULL DEFAULT 0,
                interest DECIMAL(12,2) NOT NULL DEFAULT 0, happened_on DATE NOT NULL,
                category_id BIGINT NULL, account_id BIGINT NULL, target_account_id BIGINT NULL,
                debt_id CHAR(32) NULL, counterparty VARCHAR(128) NOT NULL DEFAULT '',
                note VARCHAR(1000) NOT NULL DEFAULT '', status VARCHAR(16) NOT NULL DEFAULT 'valid',
                created_at DATETIME NOT NULL, INDEX idx_finance_date(happened_on,status),
                INDEX idx_finance_debt(debt_id,status), INDEX idx_finance_account(account_id,status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS finance_debts (
                id CHAR(32) PRIMARY KEY, owner_id BIGINT NOT NULL,direction VARCHAR(16) NOT NULL,
                counterparty VARCHAR(128) NOT NULL, principal DECIMAL(12,2) NOT NULL,
                due_on DATE NULL, note VARCHAR(1000) NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL, INDEX idx_finance_debt_direction(direction,created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS finance_budgets (
                owner_id BIGINT NOT NULL,month CHAR(7) NOT NULL, category_id BIGINT NOT NULL,
                amount DECIMAL(12,2) NOT NULL, updated_at DATETIME NOT NULL,
                PRIMARY KEY(owner_id,month,category_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS finance_goals (
                id CHAR(32) PRIMARY KEY, owner_id BIGINT NOT NULL,name VARCHAR(80) NOT NULL,
                target DECIMAL(12,2) NOT NULL, target_date DATE NULL,
                account_id BIGINT NOT NULL, created_at DATETIME NOT NULL,
                INDEX idx_finance_goal_date(target_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS finance_commission_items (
                owner_id BIGINT NOT NULL, venue_id INT NOT NULL, service_key VARCHAR(64) NOT NULL,
                service_name VARCHAR(160) NOT NULL, listed_price DECIMAL(12,2) NULL,
                commission_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
                updated_by BIGINT NULL, updated_at DATETIME NOT NULL,
                PRIMARY KEY(owner_id,venue_id,service_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
    finally:
        conn.close()


def _text(value, label, maximum, required=True):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{label}长度无效")
    value = value.strip()
    if required and not value:
        raise ValueError(f"请填写{label}")
    return value


def _money(value, label, *, allow_zero=False):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,10}(?:\.\d{1,2})?", value):
        raise ValueError(f"{label}须为美元金额，最多两位小数")
    try:
        amount = Decimal(value).quantize(CENT)
    except InvalidOperation as exc:
        raise ValueError(f"{label}无效") from exc
    if amount > Decimal("9999999999.99") or (amount < 0 if allow_zero else amount <= 0):
        raise ValueError(f"{label}金额范围无效")
    return amount


def _month(value):
    if not isinstance(value, str) or not MONTH_PATTERN.fullmatch(value):
        raise ValueError("月份无效")
    return value


def _month_utc_bounds(month):
    year, number = map(int, month.split("-"))
    next_month = date(year + (number == 12), 1 if number == 12 else number + 1, 1)
    start = datetime(year, number, 1) - timedelta(hours=7)
    end = datetime.combine(next_month, datetime.min.time()) - timedelta(hours=7)
    return start, end


def _date(value, label, optional=False):
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}无效")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label}无效") from exc


def _id(value, label):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValueError(f"{label}编号无效")
    return value


def _ensure_defaults(cur, owner_id):
    now = _now_utc()
    for name, kind in (("日常账户", "cash"), ("储蓄账户", "savings")):
        cur.execute("""INSERT IGNORE INTO finance_accounts(owner_id,name,account_type,created_at)
            VALUES(%s,%s,%s,%s)""", (owner_id, name, kind, now))
    defaults = (
        ("income", ("其他收入", "利息收入")),
        ("expense", ("餐饮", "交通", "住房", "账单", "购物", "娱乐", "医疗", "利息", "其他")),
    )
    for kind, names in defaults:
        cur.execute("SELECT COUNT(*) AS n FROM finance_categories "
                    "WHERE owner_id=%s AND kind=%s AND is_default=TRUE", (owner_id, kind))
        has_defaults = cur.fetchone()["n"] > 0
        if has_defaults:
            continue
        for name in names:
            cur.execute("""INSERT IGNORE INTO finance_categories
                (owner_id,kind,name,is_default,created_at) VALUES(%s,%s,%s,TRUE,%s)""",
                        (owner_id, kind, name, now))
    for venue_id, key, name, amount, price in COMMISSION_CATALOG:
        cur.execute("""INSERT IGNORE INTO finance_commission_items
            (owner_id,venue_id,service_key,service_name,listed_price,commission_amount,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (owner_id, venue_id, key, name, price, amount, now))


def _account(cur, account_id, owner_id):
    if type(account_id) is not int or account_id <= 0:
        raise ValueError("请选择有效账户")
    cur.execute("SELECT id,name,account_type FROM finance_accounts "
                "WHERE id=%s AND owner_id=%s AND active=1 FOR UPDATE", (account_id, owner_id))
    account = cur.fetchone()
    if not account:
        raise ValueError("账户不存在或已停用")
    return account


def _category(cur, category_id, kind, owner_id):
    if type(category_id) is not int or category_id <= 0:
        raise ValueError("请选择分类")
    cur.execute("SELECT id,name FROM finance_categories "
                "WHERE id=%s AND kind=%s AND owner_id=%s AND active=1",
                (category_id, kind, owner_id))
    category = cur.fetchone()
    if not category:
        raise ValueError("分类不存在或与收支类型不匹配")
    return category


def commission_item(cur, owner_id, venue_id, service_name, service_kind="venue"):
    cur.execute("""SELECT service_key,service_name,commission_amount FROM finance_commission_items
        WHERE owner_id=%s AND venue_id=%s AND service_name=%s""",
                (owner_id, venue_id, service_name))
    row = cur.fetchone()
    if row or service_kind != "delivery":
        return row
    fallback_key = "delivery-order" if venue_id in {7, 8, 9} else "small-6" if venue_id == 10 else None
    if not fallback_key:
        return None
    cur.execute("""SELECT service_key,service_name,commission_amount FROM finance_commission_items
        WHERE owner_id=%s AND venue_id=%s AND service_key=%s""",
                (owner_id, venue_id, fallback_key))
    return cur.fetchone()


def commission_after_refund(base_amount, refunded_amount):
    return max(Decimal("0"), base_amount - refunded_amount).quantize(CENT)


def _commission_items(cur, owner_id):
    cur.execute("""SELECT i.venue_id,v.name AS venue_name,i.service_key,i.service_name,
        i.listed_price,i.commission_amount FROM finance_commission_items i
        JOIN venues v ON v.id=i.venue_id
        WHERE i.owner_id=%s ORDER BY i.venue_id,i.service_key""", (owner_id,))
    return cur.fetchall()


def commission_catalog(owner_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_defaults(cur, owner_id)
            result = _commission_items(cur, owner_id)
        conn.commit()
        return [{**row, "listed_price": str(row["listed_price"]) if row["listed_price"] is not None else None,
                 "commission_amount": str(row["commission_amount"])} for row in result]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_commission_items(data, owner_id):
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("提成项目格式无效")
    parsed = {}
    for row in items:
        if not isinstance(row, dict) or not isinstance(row.get("service_key"), str):
            raise ValueError("提成项目无效")
        key = (row.get("venue_id"), row["service_key"])
        if type(key[0]) is not int or key in parsed:
            raise ValueError("提成项目重复或来源无效")
        parsed[key] = _money(row.get("commission_amount"), "固定提成", allow_zero=True)
    if set(parsed) != {(venue_id, key) for venue_id, key, *_ in COMMISSION_CATALOG}:
        raise ValueError("请提交完整提成项目清单")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_defaults(cur, owner_id)
            now = _now_utc()
            for (venue_id, key), amount in parsed.items():
                cur.execute("""UPDATE finance_commission_items SET commission_amount=%s,
                    updated_by=%s,updated_at=%s WHERE owner_id=%s AND venue_id=%s AND service_key=%s""",
                            (amount, owner_id, now, owner_id, venue_id, key))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True}


def _account_balances(cur, owner_id):
    cur.execute("""SELECT a.id,a.name,a.account_type,a.opening_balance,
        a.opening_balance +
        COALESCE(SUM(CASE
          WHEN t.kind='income' AND t.account_id=a.id THEN t.amount
          WHEN t.kind='expense' AND t.account_id=a.id THEN -t.amount
          WHEN t.kind='transfer' AND t.account_id=a.id THEN -t.amount
          WHEN t.kind='transfer' AND t.target_account_id=a.id THEN t.amount
          WHEN t.kind='borrow' AND t.account_id=a.id THEN t.principal
          WHEN t.kind='lend' AND t.account_id=a.id THEN -t.principal
          WHEN t.kind='balance_add' AND t.account_id=a.id THEN t.amount
          WHEN t.kind='debt_repay' AND t.account_id=a.id THEN -(t.principal+t.interest)
          WHEN t.kind='debt_collect' AND t.account_id=a.id THEN t.principal+t.interest
          ELSE 0 END),0)
          + CASE WHEN a.name=%s THEN COALESCE((
            SELECT SUM(c.commission_amount) FROM crm_consumptions c
            WHERE c.commission_owner_id=a.owner_id AND c.status='valid'
              AND c.commission_amount>0
          ),0) ELSE 0 END AS balance
        FROM finance_accounts a LEFT JOIN finance_transactions t
          ON t.status='valid' AND t.owner_id=a.owner_id
          AND (t.account_id=a.id OR t.target_account_id=a.id)
        WHERE a.active=1 AND a.owner_id=%s
        GROUP BY a.id,a.name,a.account_type,a.opening_balance ORDER BY a.id""",
                ("日常账户", owner_id))
    return cur.fetchall()


def _debts(cur, owner_id):
    cur.execute("""SELECT d.id,d.direction,d.counterparty,d.principal,d.due_on,d.note,
        d.principal-COALESCE(SUM(CASE WHEN t.status='valid' AND
          ((d.direction='payable' AND t.kind='debt_repay') OR
           (d.direction='receivable' AND t.kind='debt_collect')) THEN t.principal ELSE 0 END),0) AS outstanding
        FROM finance_debts d LEFT JOIN finance_transactions t ON t.debt_id=d.id AND t.owner_id=d.owner_id
        WHERE d.owner_id=%s
        GROUP BY d.id,d.direction,d.counterparty,d.principal,d.due_on,d.note
        HAVING outstanding>0 ORDER BY d.due_on IS NULL,d.due_on,d.created_at DESC""", (owner_id,))
    return cur.fetchall()


def dashboard(data, owner_id):
    month = _month(data.get("month", (_now_utc() + timedelta(hours=7)).strftime("%Y-%m")))
    commission_start, commission_end = _month_utc_bounds(month)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_defaults(cur, owner_id)
            conn.commit()
            cur.execute("""SELECT c.id,c.kind,c.name,COALESCE(b.amount,0) AS budget
                FROM finance_categories c LEFT JOIN finance_budgets b
                  ON b.category_id=c.id AND b.month=%s AND b.owner_id=c.owner_id
                WHERE c.active=1 AND c.owner_id=%s ORDER BY c.kind,c.id""", (month, owner_id))
            categories = cur.fetchall()
            cur.execute("""SELECT id,kind,name,is_default,active FROM finance_categories
                WHERE owner_id=%s ORDER BY kind,id""", (owner_id,))
            all_categories = cur.fetchall()
            accounts = _account_balances(cur, owner_id)
            debts = _debts(cur, owner_id)
            commission_items = _commission_items(cur, owner_id)
            cur.execute("""SELECT t.*,c.name AS category_name,a.name AS account_name,
                    ta.name AS target_account_name
                FROM finance_transactions t
                LEFT JOIN finance_categories c ON c.id=t.category_id AND c.owner_id=t.owner_id
                LEFT JOIN finance_accounts a ON a.id=t.account_id AND a.owner_id=t.owner_id
                LEFT JOIN finance_accounts ta ON ta.id=t.target_account_id AND ta.owner_id=t.owner_id
                WHERE (t.status='valid' OR (t.kind='balance_add' AND t.status='void'))
                  AND t.owner_id=%s AND DATE_FORMAT(t.happened_on,'%%Y-%%m')=%s
                ORDER BY t.happened_on DESC,t.created_at DESC LIMIT 100""", (owner_id, month))
            transactions = cur.fetchall()
            cur.execute("""SELECT c.id,c.service,c.service_kind,c.venue_id,c.commission_amount,
                    c.commission_service_key,c.delivery_person_id,m.code AS staff_code,
                    COALESCE(NULLIF(dp.display_name,''),CONCAT('外送档案 ',c.delivery_person_id))
                        AS delivery_person_name,
                    DATE_ADD(c.occurred_at,INTERVAL 7 HOUR) AS occurred_at,v.name AS source_name
                FROM crm_consumptions c JOIN venues v ON v.id=c.venue_id
                LEFT JOIN staff_members m ON m.id=c.staff_id
                LEFT JOIN delivery_people dp ON dp.id=c.delivery_person_id
                WHERE c.status='valid' AND c.commission_owner_id=%s
                  AND (c.commission_amount>0 OR c.staff_id IS NOT NULL OR c.delivery_person_id IS NOT NULL)
                  AND c.occurred_at >= %s AND c.occurred_at < %s
                ORDER BY c.occurred_at DESC,c.id DESC LIMIT 100""",
                        (owner_id, commission_start, commission_end))
            commissions = cur.fetchall()
            cur.execute("""SELECT
                COALESCE(SUM(CASE WHEN kind='income' THEN amount
                    WHEN kind='debt_collect' THEN interest ELSE 0 END),0) AS income,
                COALESCE(SUM(CASE WHEN kind='expense' THEN amount
                    WHEN kind='debt_repay' THEN interest ELSE 0 END),0) AS expense
                FROM finance_transactions WHERE owner_id=%s AND status='valid'
                  AND DATE_FORMAT(happened_on,'%%Y-%%m')=%s""", (owner_id, month))
            totals = cur.fetchone()
            cur.execute("""SELECT COALESCE(SUM(commission_amount),0) AS total
                FROM crm_consumptions WHERE status='valid' AND commission_owner_id=%s
                  AND occurred_at >= %s AND occurred_at < %s""",
                        (owner_id, commission_start, commission_end))
            commission_total = cur.fetchone()["total"]
            cur.execute("""SELECT c.id,c.kind,c.name,COALESCE(b.amount,0) AS budget,
                    COALESCE(SUM(CASE
                      WHEN c.kind='expense' AND t.kind='expense' THEN t.amount
                      WHEN c.kind='expense' AND t.kind='debt_repay' THEN t.interest
                      WHEN c.kind='income' AND t.kind='income' THEN t.amount
                      WHEN c.kind='income' AND t.kind='debt_collect' THEN t.interest
                      ELSE 0 END),0) AS spent
                FROM finance_categories c LEFT JOIN finance_budgets b
                  ON b.category_id=c.id AND b.month=%s AND b.owner_id=c.owner_id
                LEFT JOIN finance_transactions t ON t.category_id=c.id AND t.status='valid'
                  AND t.owner_id=c.owner_id AND t.kind IN ('income','expense','debt_repay','debt_collect')
                  AND DATE_FORMAT(t.happened_on,'%%Y-%%m')=%s
                WHERE c.owner_id=%s AND c.kind IN ('income','expense') AND c.active=1
                GROUP BY c.id,c.kind,c.name,b.amount ORDER BY c.kind,c.id""", (month, month, owner_id))
            budgets = cur.fetchall()
            cur.execute("""SELECT g.id,g.name,g.target,g.target_date,a.id AS account_id,
                    a.name AS account_name,a.opening_balance+COALESCE(SUM(CASE
                      WHEN t.kind='income' AND t.account_id=a.id THEN t.amount
                      WHEN t.kind='expense' AND t.account_id=a.id THEN -t.amount
                      WHEN t.kind='balance_add' AND t.account_id=a.id THEN t.amount
                      WHEN t.kind='transfer' AND t.account_id=a.id THEN -t.amount
                      WHEN t.kind='transfer' AND t.target_account_id=a.id THEN t.amount
                      WHEN t.kind='borrow' AND t.account_id=a.id THEN t.principal
                      WHEN t.kind='lend' AND t.account_id=a.id THEN -t.principal
                      WHEN t.kind='debt_repay' AND t.account_id=a.id THEN -(t.principal+t.interest)
                      WHEN t.kind='debt_collect' AND t.account_id=a.id THEN t.principal+t.interest
                      ELSE 0 END),0) AS saved
                FROM finance_goals g JOIN finance_accounts a ON a.id=g.account_id AND a.owner_id=g.owner_id
                LEFT JOIN finance_transactions t ON t.status='valid'
                  AND t.owner_id=g.owner_id AND (t.account_id=a.id OR t.target_account_id=a.id)
                WHERE g.owner_id=%s
                GROUP BY g.id,g.name,g.target,g.target_date,a.id,a.name,a.opening_balance
                ORDER BY g.created_at""", (owner_id,))
            goals = cur.fetchall()
        income = totals["income"] + commission_total
        expense = totals["expense"]
        total_saved = sum((row["balance"] for row in accounts if row["account_type"] == "savings"), Decimal(0))
        total_account_balance = sum((row["balance"] for row in accounts), Decimal(0))
        payable = sum((row["outstanding"] for row in debts if row["direction"] == "payable"), Decimal(0))
        receivable = sum((row["outstanding"] for row in debts if row["direction"] == "receivable"), Decimal(0))
        net_worth = total_account_balance + receivable - payable
        entries = []
        for row in transactions:
            item = {**row, "entry_kind": row["kind"], "display_date": row["happened_on"]}
            entries.append(item)
        for row in commissions:
            entries.append({
                **row, "kind": "commission", "entry_kind": "commission",
                "amount": row["commission_amount"], "category_name": "客人提成",
                "account_name": "日常账户（自动入账）", "display_date": row["occurred_at"],
            })
        entries.sort(key=lambda row: (str(row["display_date"]), str(row.get("created_at", ""))), reverse=True)
        return {
            "month": month,
            "summary": {
                "income": str(income.quantize(CENT)),
                "commission": str(commission_total.quantize(CENT)),
                "other_income": str(totals["income"].quantize(CENT)),
                "expense": str(expense.quantize(CENT)),
                "net": str((income - expense).quantize(CENT)),
                "saved": str(total_saved.quantize(CENT)),
                "account_balance": str(total_account_balance.quantize(CENT)),
                "net_worth": str(net_worth.quantize(CENT)),
                "payable": str(payable.quantize(CENT)),
                "receivable": str(receivable.quantize(CENT)),
            },
            "accounts": [{**row, "opening_balance": str(row["opening_balance"]),
                          "balance": str(row["balance"])} for row in accounts],
            "categories": [{**row, "budget": str(row["budget"])} for row in categories],
            "all_categories": all_categories,
            "budgets": [{**row, "budget": str(row["budget"]), "spent": str(row["spent"])}
                        for row in budgets],
            "goals": [{**row, "target": str(row["target"]), "saved": str(row["saved"])}
                      for row in goals],
            "debts": [{**row, "principal": str(row["principal"]), "outstanding": str(row["outstanding"])}
                      for row in debts],
            "commission_items": [{**row,
                "listed_price": str(row["listed_price"]) if row["listed_price"] is not None else None,
                "commission_amount": str(row["commission_amount"])} for row in commission_items],
            "transactions": [{**row, "amount": str(row["amount"]),
                              "principal": str(row["principal"]) if row.get("principal") is not None else "0",
                              "interest": str(row["interest"]) if row.get("interest") is not None else "0"}
                             for row in entries],
        }
    finally:
        conn.close()


def daily_summary(data, owner_id):
    day = _date(data.get("day"), "日期")
    start = datetime.combine(day, datetime.min.time()) - timedelta(hours=7)
    end = start + timedelta(days=1)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT
                COALESCE(SUM(CASE WHEN kind='income' THEN amount
                    WHEN kind='debt_collect' THEN interest ELSE 0 END),0) AS income,
                COALESCE(SUM(CASE WHEN kind='expense' THEN amount
                    WHEN kind='debt_repay' THEN interest ELSE 0 END),0) AS expense
                FROM finance_transactions
                WHERE owner_id=%s AND status='valid' AND happened_on=%s""",
                        (owner_id, day))
            totals = cur.fetchone()
            cur.execute("""SELECT COALESCE(SUM(commission_amount),0) AS total
                FROM crm_consumptions WHERE status='valid' AND commission_owner_id=%s
                  AND occurred_at >= %s AND occurred_at < %s""", (owner_id, start, end))
            commission = cur.fetchone()["total"]
            cur.execute("""SELECT c.name,
                    COALESCE(SUM(CASE
                      WHEN t.kind='expense' THEN t.amount
                      WHEN t.kind='debt_repay' THEN t.interest
                      ELSE 0 END),0) AS amount
                FROM finance_transactions t
                LEFT JOIN finance_categories c ON c.id=t.category_id AND c.owner_id=t.owner_id
                WHERE t.owner_id=%s AND t.status='valid' AND t.happened_on=%s
                  AND t.kind IN ('expense','debt_repay')
                GROUP BY c.id,c.name HAVING amount>0 ORDER BY amount DESC LIMIT 5""",
                        (owner_id, day))
            categories = cur.fetchall()
            accounts = _account_balances(cur, owner_id)
            debts = _debts(cur, owner_id)
        income = totals["income"] + commission
        expense = totals["expense"]
        account_balance = sum((row["balance"] for row in accounts), Decimal(0))
        payable = sum((row["outstanding"] for row in debts if row["direction"] == "payable"), Decimal(0))
        receivable = sum((row["outstanding"] for row in debts if row["direction"] == "receivable"), Decimal(0))
        return {
            "day": day.isoformat(),
            "income": str(income.quantize(CENT)),
            "commission": str(commission.quantize(CENT)),
            "expense": str(expense.quantize(CENT)),
            "net": str((income - expense).quantize(CENT)),
            "account_balance": str(account_balance.quantize(CENT)),
            "net_worth": str((account_balance + receivable - payable).quantize(CENT)),
            "categories": [{**row, "amount": str(row["amount"])} for row in categories],
        }
    finally:
        conn.close()


def set_budget(data, owner_id):
    month = _month(data.get("month"))
    if "items" in data:
        items = data.get("items")
        if not isinstance(items, list) or any(
                not isinstance(item, dict) or "category_id" not in item or "amount" not in item
                for item in items):
            raise ValueError("预算项目格式无效")
        updates = [
            (item["category_id"], item.get("kind", "expense"),
             _money(item["amount"], "月度限额", allow_zero=True))
            for item in items
        ]
    else:
        updates = [(data.get("category_id"), "expense",
                    _money(data.get("amount"), "月度限额", allow_zero=True))]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_defaults(cur, owner_id)
            for category_id, kind, amount in updates:
                if kind not in ("income", "expense"):
                    raise ValueError("预算类型无效")
                _category(cur, category_id, kind, owner_id)
                if amount == 0:
                    cur.execute("DELETE FROM finance_budgets WHERE owner_id=%s AND month=%s AND category_id=%s",
                                (owner_id, month, category_id))
                else:
                    cur.execute("""INSERT INTO finance_budgets(owner_id,month,category_id,amount,updated_at)
                        VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE amount=VALUES(amount),
                        updated_at=VALUES(updated_at)""",
                                (owner_id, month, category_id, amount, _now_utc()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True}


def add_goal(data, owner_id):
    goal_id = _id(data.get("request_id"), "储蓄目标")
    name = _text(data.get("name"), "目标名称", 80)
    target = _money(data.get("target"), "目标金额")
    target_date = _date(data.get("target_date"), "目标日期", optional=True)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_defaults(cur, owner_id)
            cur.execute("SELECT id,name,target,target_date,account_id FROM finance_goals "
                        "WHERE id=%s AND owner_id=%s", (goal_id, owner_id))
            prior = cur.fetchone()
            if prior:
                if prior["name"] != name or prior["target"] != target or prior["target_date"] != target_date:
                    raise ValueError("目标编号已用于其他内容")
                conn.rollback()
                return {"ok": True, "replayed": True}
            now = _now_utc()
            account_name = f"目标储蓄·{name[:42]}·{goal_id}"
            cur.execute("INSERT INTO finance_accounts(owner_id,name,account_type,created_at) "
                        "VALUES(%s,%s,'savings',%s)", (owner_id, account_name, now))
            account_id = cur.lastrowid
            cur.execute("INSERT INTO finance_goals(id,owner_id,name,target,target_date,account_id,created_at) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                        (goal_id, owner_id, name, target, target_date, account_id, now))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True}


def add_account(data, owner_id):
    name = _text(data.get("name"), "账户名称", 80)
    kind = data.get("account_type")
    if kind not in {"cash", "savings"}:
        raise ValueError("请选择日常账户或储蓄账户")
    opening_balance = _money(data.get("opening_balance", "0"), "期初余额", allow_zero=True)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_defaults(cur, owner_id)
            cur.execute("""INSERT INTO finance_accounts
                (owner_id,name,account_type,opening_balance,created_at) VALUES(%s,%s,%s,%s,%s)""",
                        (owner_id, name, kind, opening_balance, _now_utc()))
            account_id = cur.lastrowid
        conn.commit()
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if exc.args[0] == 1062:
            raise ValueError("账户名称已存在") from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True, "account_id": account_id}


def set_account_opening_balance(data, owner_id):
    account_id = data.get("account_id")
    if type(account_id) is not int or account_id <= 0:
        raise ValueError("请选择有效账户")
    opening_balance = _money(data.get("opening_balance"), "期初余额", allow_zero=True)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM finance_accounts WHERE id=%s AND owner_id=%s AND active=1",
                        (account_id, owner_id))
            if not cur.fetchone():
                raise ValueError("账户不存在或已停用")
            cur.execute("""UPDATE finance_accounts SET opening_balance=%s
                WHERE id=%s AND owner_id=%s AND active=1""",
                        (opening_balance, account_id, owner_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True}


def add_category(data, owner_id):
    kind = data.get("kind")
    if kind not in {"income", "expense"}:
        raise ValueError("分类类型无效")
    name = _text(data.get("name"), "分类名称", 48)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_defaults(cur, owner_id)
            cur.execute("INSERT INTO finance_categories(owner_id,kind,name,created_at) VALUES(%s,%s,%s,%s)",
                        (owner_id, kind, name, _now_utc()))
        conn.commit()
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if exc.args[0] == 1062:
            raise ValueError("该分类已存在") from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True}


def update_category(data, owner_id):
    category_id = data.get("category_id")
    if type(category_id) is not int or category_id <= 0:
        raise ValueError("请选择有效分类")
    name = _text(data.get("name"), "分类名称", 48)
    kind = data.get("kind")
    if kind not in {"income", "expense"}:
        raise ValueError("分类类型无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id,kind,name FROM finance_categories
                WHERE id=%s AND owner_id=%s AND active=1 FOR UPDATE""", (category_id, owner_id))
            category = cur.fetchone()
            if not category:
                raise ValueError("分类不存在或已删除")
            if (category["kind"], category["name"]) in {
                    ("expense", "利息"), ("income", "利息收入")}:
                raise ValueError("利息分类用于借款记录，不能修改")
            if kind != category["kind"]:
                cur.execute("""SELECT
                    (SELECT COUNT(*) FROM finance_transactions
                     WHERE owner_id=%s AND category_id=%s) +
                    (SELECT COUNT(*) FROM finance_budgets
                     WHERE owner_id=%s AND category_id=%s) AS n""",
                            (owner_id, category_id, owner_id, category_id))
                if cur.fetchone()["n"]:
                    raise ValueError("该分类已有流水或预算，不能更改收支类型")
            cur.execute("""UPDATE finance_categories SET name=%s,kind=%s
                WHERE id=%s AND owner_id=%s AND active=1""",
                        (name, kind, category_id, owner_id))
        conn.commit()
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if exc.args[0] == 1062:
            raise ValueError("该分类名称已存在") from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True}


def delete_category(data, owner_id):
    category_id = data.get("category_id")
    if type(category_id) is not int or category_id <= 0:
        raise ValueError("请选择有效分类")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id,kind,name FROM finance_categories
                WHERE id=%s AND owner_id=%s AND active=1 FOR UPDATE""", (category_id, owner_id))
            category = cur.fetchone()
            if not category:
                raise ValueError("分类不存在或已删除")
            if (category["kind"], category["name"]) in {
                    ("expense", "利息"), ("income", "利息收入")}:
                raise ValueError("利息分类用于借款记录，不能删除")
            cur.execute("UPDATE finance_categories SET active=FALSE "
                        "WHERE id=%s AND owner_id=%s AND active=1", (category_id, owner_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True}


def restore_category(data, owner_id):
    category_id = data.get("category_id")
    if type(category_id) is not int or category_id <= 0:
        raise ValueError("请选择有效分类")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM finance_categories WHERE id=%s AND owner_id=%s AND active=0 FOR UPDATE",
                        (category_id, owner_id))
            if not cur.fetchone():
                raise ValueError("已删除分类不存在")
            cur.execute("UPDATE finance_categories SET active=TRUE WHERE id=%s AND owner_id=%s AND active=0",
                        (category_id, owner_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True}


def save_transaction(data, owner_id):
    request_id = _id(data.get("request_id"), "记账操作")
    kind = data.get("kind")
    if kind not in {"income", "expense", "transfer", "borrow", "lend", "debt_repay",
                    "debt_collect", "balance_add"}:
        raise ValueError("请选择有效的记账类型")
    happened_on = _date(data.get("happened_on"), "记账日期")
    note = _text(data.get("note", ""), "备注", 1000, required=False)
    counterparty = _text(data.get("counterparty", ""), "往来对象", 128,
                         required=kind in {"borrow", "lend"})
    digest = hashlib.sha256(json.dumps(
        {key: value for key, value in data.items() if key != "init_data"},
        sort_keys=True, ensure_ascii=True,
    ).encode()).hexdigest()
    amount = principal = interest = Decimal("0")
    account_id = target_account_id = category_id = debt_id = None
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_defaults(cur, owner_id)
            cur.execute("SELECT * FROM finance_transactions WHERE id=%s AND owner_id=%s FOR UPDATE",
                        (request_id, owner_id))
            prior = cur.fetchone()
            if prior:
                if prior["request_hash"] != digest:
                    raise ValueError("记账编号已用于其他内容，请刷新核对")
                conn.rollback()
                return {"ok": True, "replayed": True}
            if kind in {"income", "expense"}:
                amount = _money(data.get("amount"), "金额")
                category = _category(cur, data.get("category_id"), kind, owner_id)
                category_id = category["id"]
                account_id = _account(cur, data.get("account_id"), owner_id)["id"]
            elif kind == "transfer":
                amount = _money(data.get("amount"), "转账金额")
                account_id = _account(cur, data.get("account_id"), owner_id)["id"]
                target_account_id = _account(cur, data.get("target_account_id"), owner_id)["id"]
                if account_id == target_account_id:
                    raise ValueError("转出和转入账户不能相同")
            elif kind == "balance_add":
                amount = _money(data.get("amount"), "补充金额")
                account_id = _account(cur, data.get("account_id"), owner_id)["id"]
            elif kind in {"borrow", "lend"}:
                principal = amount = _money(data.get("amount"), "本金")
                account_id = _account(cur, data.get("account_id"), owner_id)["id"]
                cur.execute("INSERT INTO finance_debts(id,owner_id,direction,counterparty,principal,due_on,note,created_at) "
                            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                            (request_id, owner_id, "payable" if kind == "borrow" else "receivable",
                             counterparty, principal, _date(data.get("due_on"), "到期日", optional=True),
                             note, _now_utc()))
            else:
                debt_id = _id(data.get("debt_id"), "借还款")
                cur.execute("SELECT * FROM finance_debts WHERE id=%s AND owner_id=%s FOR UPDATE",
                            (debt_id, owner_id))
                debt = cur.fetchone()
                expected = "payable" if kind == "debt_repay" else "receivable"
                if not debt or debt["direction"] != expected:
                    raise ValueError("借还款对象不存在或类型不匹配")
                cur.execute("""SELECT d.principal-COALESCE(SUM(CASE WHEN t.status='valid' AND
                ((%s='payable' AND t.kind='debt_repay') OR
                 (%s='receivable' AND t.kind='debt_collect')) THEN t.principal ELSE 0 END),0) AS outstanding
                FROM finance_debts d LEFT JOIN finance_transactions t
                  ON t.debt_id=d.id AND t.owner_id=d.owner_id
                WHERE d.id=%s AND d.owner_id=%s GROUP BY d.principal""",
                        (expected, expected, debt_id, owner_id))
                outstanding = cur.fetchone()["outstanding"]
                principal = _money(data.get("principal"), "归还本金")
                interest = _money(data.get("interest", "0"), "利息", allow_zero=True)
                if principal > outstanding:
                    raise ValueError("本金超过剩余借款金额")
                amount = principal + interest
                if amount > Decimal("9999999999.99"):
                    raise ValueError("本金与利息合计超过可登记范围")
                account_id = _account(cur, data.get("account_id"), owner_id)["id"]
                counterparty = debt["counterparty"]
                category_kind = "expense" if kind == "debt_repay" else "income"
                category_name = "利息" if kind == "debt_repay" else "利息收入"
                cur.execute("SELECT id FROM finance_categories WHERE owner_id=%s AND kind=%s AND name=%s",
                            (owner_id, category_kind, category_name))
                category_id = cur.fetchone()["id"]
            cur.execute("""INSERT INTO finance_transactions(id,owner_id,request_hash,kind,amount,principal,interest,happened_on,
                category_id,account_id,target_account_id,debt_id,counterparty,note,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (request_id, owner_id, digest, kind, amount, principal, interest, happened_on, category_id,
                         account_id, target_account_id, debt_id, counterparty, note,
                         _now_utc()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True, "transaction_id": request_id}


def void_transaction(data, owner_id):
    transaction_id = _id(data.get("transaction_id"), "流水")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM finance_transactions WHERE id=%s AND owner_id=%s FOR UPDATE",
                        (transaction_id, owner_id))
            row = cur.fetchone()
            if not row:
                raise ValueError("流水不存在")
            if row["status"] == "void":
                conn.rollback()
                return {"ok": True}
            if row["kind"] in {"borrow", "lend"}:
                cur.execute("SELECT COUNT(*) AS n FROM finance_transactions WHERE debt_id=%s AND owner_id=%s "
                            "AND id<>%s "
                            "AND status='valid'", (transaction_id, owner_id, transaction_id))
                if cur.fetchone()["n"]:
                    raise ValueError("已有还款或收款记录，不能撤销原借款；请先逐笔核对")
                cur.execute("DELETE FROM finance_debts WHERE id=%s AND owner_id=%s", (transaction_id, owner_id))
            elif row.get("debt_id") and row["kind"] in {"debt_repay", "debt_collect"}:
                cur.execute("SELECT direction,principal FROM finance_debts WHERE id=%s AND owner_id=%s FOR UPDATE",
                            (row["debt_id"], owner_id))
                debt = cur.fetchone()
                if not debt:
                    raise ValueError("关联借款不存在，不能撤销流水")
                if row["principal"] > debt["principal"]:
                    raise ValueError("本金异常，不能撤销流水")
            cur.execute("UPDATE finance_transactions SET status='void' WHERE id=%s AND owner_id=%s",
                        (transaction_id, owner_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True}
