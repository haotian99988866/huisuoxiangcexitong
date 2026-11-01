"""Manual TRC20 deposit claims and transactional balance accounting."""
import hashlib
import re
from decimal import Decimal, InvalidOperation

import pymysql

from db import get_conn


def valid_address(value):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    if not isinstance(value, str) or len(value) != 34 or not value.startswith("T"):
        return False
    try:
        number = 0
        for char in value:
            number = number * 58 + alphabet.index(char)
        raw = number.to_bytes(25, "big")
    except (ValueError, OverflowError):
        return False
    return raw[0] == 65 and hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] == raw[-4:]


def amount_value(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000"):
            raise ValueError("金额必须大于 0 且不超过 1000000 U")
        if amount != amount.quantize(Decimal("0.000001")):
            raise ValueError("金额最多保留 6 位小数")
        return amount
    except InvalidOperation as exc:
        raise ValueError("请输入有效金额") from exc


def ensure_tables():
    statements = (
        """CREATE TABLE IF NOT EXISTS wallet_config (
            id INT PRIMARY KEY, address VARCHAR(34) NULL
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS wallet_accounts (
            user_id BIGINT PRIMARY KEY, balance DECIMAL(20,6) NOT NULL DEFAULT 0
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS wallet_deposits (
            id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL,
            txid CHAR(64) NOT NULL UNIQUE, claimed DECIMAL(20,6) NOT NULL,
            address VARCHAR(34) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'pending',
            credited DECIMAL(20,6) NULL, admin_id BIGINT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP NULL, INDEX idx_wallet_user(user_id, id)
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS wallet_ledger (
            id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL,
            deposit_id BIGINT NOT NULL UNIQUE, amount DECIMAL(20,6) NOT NULL,
            admin_id BIGINT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_ledger_user(user_id, id)
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS wallet_aba_config (
            id INT PRIMARY KEY, account_number VARCHAR(64) NOT NULL,
            account_name VARCHAR(128) NOT NULL
        ) ENGINE=InnoDB""",
    )
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
            cur.execute("SHOW COLUMNS FROM wallet_deposits LIKE 'method'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE wallet_deposits ADD COLUMN method VARCHAR(8) NOT NULL DEFAULT 'usdt'")
            cur.execute("SHOW COLUMNS FROM wallet_deposits LIKE 'txid'")
            if cur.fetchone()["Type"].lower() != "varchar(128)":
                cur.execute("ALTER TABLE wallet_deposits MODIFY txid VARCHAR(128) NOT NULL")
            cur.execute("SHOW COLUMNS FROM wallet_deposits LIKE 'address'")
            if cur.fetchone()["Type"].lower() != "varchar(256)":
                cur.execute("ALTER TABLE wallet_deposits MODIFY address VARCHAR(256) NOT NULL")
        conn.commit()
    finally:
        conn.close()


def set_address(address):
    if not valid_address(address):
        raise ValueError("无效的 TRON 收款地址，请核对地址及校验码")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("REPLACE INTO wallet_config (id,address) VALUES (1,%s)", (address,))
        conn.commit()
    finally:
        conn.close()


def set_aba(account_number, account_name):
    if (not isinstance(account_number, str) or not re.fullmatch(r"[0-9]{6,32}", account_number)
            or not isinstance(account_name, str) or not 1 <= len(account_name.strip()) <= 128
            or any(ord(char) < 32 for char in account_name)):
        raise ValueError("请提供有效 ABA 账号和收款人名称；只配置美元收款账户")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "REPLACE INTO wallet_aba_config(id,account_number,account_name) VALUES(1,%s,%s)",
                (account_number, account_name.strip()))
        conn.commit()
    finally:
        conn.close()


def account(user_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT address FROM wallet_config WHERE id=1")
            config = cur.fetchone()
            cur.execute("SELECT account_number,account_name FROM wallet_aba_config WHERE id=1")
            aba = cur.fetchone()
            cur.execute("SELECT balance FROM wallet_accounts WHERE user_id=%s", (user_id,))
            balance = cur.fetchone()
            cur.execute(
                "SELECT id,txid,method,claimed,status,credited,created_at FROM wallet_deposits "
                "WHERE user_id=%s ORDER BY id DESC LIMIT 30", (user_id,),
            )
            deposits = cur.fetchall()
            for row in deposits:
                for key in ("claimed", "credited", "created_at"):
                    if row[key] is not None:
                        row[key] = str(row[key])
        return {
            "address": config["address"] if config else None,
            "aba": aba,
            "currency": "USD",
            "balance": str(balance["balance"]) if balance else "0.000000",
            "deposits": deposits,
        }
    finally:
        conn.close()


def submit(user_id, txid, amount, method="usdt"):
    if not isinstance(method, str) or method not in {"usdt", "aba"}:
        raise ValueError("请选择 USDT 或 ABA")
    if method == "usdt":
        if not isinstance(txid, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", txid):
            raise ValueError("TXID 应为 64 位十六进制交易哈希")
        reference = txid.lower()
    else:
        if not isinstance(txid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,100}", txid):
            raise ValueError("ABA 交易编号须为 6–100 位字母、数字、下划线或连字符")
        reference = f"aba:{txid.lower()}"
    amount = amount_value(amount)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if method == "usdt":
                cur.execute("SELECT address FROM wallet_config WHERE id=1")
                config = cur.fetchone()
                if not config or not valid_address(config["address"]):
                    raise ValueError("充值尚未开放，请等待管理员配置收款地址")
                address = config["address"]
            else:
                cur.execute("SELECT account_number,account_name FROM wallet_aba_config WHERE id=1")
                config = cur.fetchone()
                if not config:
                    raise ValueError("ABA 充值尚未开放，请等待管理员配置美元收款账户")
                address = f"{config['account_number']} / {config['account_name']}"
            cur.execute(
                "INSERT INTO wallet_deposits(user_id,txid,claimed,address,method) VALUES(%s,%s,%s,%s,%s)",
                (user_id, reference, amount, address, method),
            )
            deposit_id = cur.lastrowid
        conn.commit()
        return deposit_id
    except pymysql.IntegrityError as exc:
        conn.rollback()
        if exc.args[0] == 1062:
            raise ValueError("该交易编号已提交，请查看充值记录或联系管理员") from exc
        raise
    finally:
        conn.close()


def pending():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM wallet_deposits WHERE status='pending' ORDER BY id LIMIT 8")
            return cur.fetchall()
    finally:
        conn.close()


def review(deposit_id, admin_id, amount=None):
    credit = amount_value(amount) if amount is not None else None
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM wallet_deposits WHERE id=%s FOR UPDATE", (deposit_id,))
            deposit = cur.fetchone()
            if not deposit or deposit["status"] != "pending":
                raise ValueError("申请不存在或已处理，不能重复入账")
            if credit is not None:
                cur.execute(
                    "INSERT INTO wallet_accounts(user_id,balance) VALUES(%s,%s) "
                    "ON DUPLICATE KEY UPDATE balance=balance+VALUES(balance)",
                    (deposit["user_id"], credit),
                )
                cur.execute(
                    "INSERT INTO wallet_ledger(user_id,deposit_id,amount,admin_id) VALUES(%s,%s,%s,%s)",
                    (deposit["user_id"], deposit_id, credit, admin_id),
                )
            cur.execute(
                "UPDATE wallet_deposits SET status=%s,credited=%s,admin_id=%s,reviewed_at=NOW() WHERE id=%s",
                ("approved" if credit is not None else "rejected", credit, admin_id, deposit_id),
            )
        conn.commit()
        return deposit["user_id"]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
