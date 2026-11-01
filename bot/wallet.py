"""Manual TRC20 deposit claims and transactional balance accounting."""
import hashlib
import io
import logging
import os
import re
import uuid
import warnings
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone

import pymysql
import requests
from PIL import Image, UnidentifiedImageError

from db import get_conn

logger = logging.getLogger(__name__)


def notify_deposit(deposit_id):
    from visit_stats import admin_ids
    recipients = admin_ids()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    failed = []
    if not token or not recipients:
        logger.error("Deposit admin notification is not configured deposit=%s", deposit_id)
        return False
    for admin_id in recipients:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": admin_id,
                      "text": f"有新的充值申请 #{deposit_id} 待审核。\n请打开运营管理台 → 充值审核，或发送 /wallet。\n请核实真实到账后再入账，不能仅凭截图。",
                      "reply_markup": {"inline_keyboard": [[{
                          "text": "打开充值审核",
                          "url": "https://t.me/haotianzhaopian_bot?startapp=admin",
                      }]]}},
                timeout=10,
            )
            if response.status_code != 200 or response.json().get("ok") is not True:
                failed.append(admin_id)
                logger.error("Deposit notification rejected deposit=%s admin=%s status=%s", deposit_id, admin_id, response.status_code)
        except (requests.RequestException, ValueError):
            failed.append(admin_id)
            logger.error("Deposit notification unavailable deposit=%s admin=%s", deposit_id, admin_id)
    return not failed


def pending_details():
    rows = pending()
    return [{
        "id": row["id"], "user_id": row["user_id"], "method": row["method"],
        "claimed": str(row["claimed"]), "address": row["address"],
        "created_at": str(row["created_at"]),
        "reference": None if row["txid"].startswith(("proof:", "manual:")) else row["txid"].removeprefix("aba:"),
        "has_proof": row["txid"].startswith("proof:"),
        "manual_review": row["txid"].startswith("manual:"),
    } for row in rows]


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
        """CREATE TABLE IF NOT EXISTS wallet_deposit_proofs (
            deposit_id BIGINT PRIMARY KEY, digest CHAR(64) NOT NULL UNIQUE,
            image MEDIUMBLOB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


def rejected_deposit_today(cur, user_id):
    cur.execute(
        "SELECT id,UNIX_TIMESTAMP(reviewed_at) AS reviewed_epoch,UNIX_TIMESTAMP() AS now_epoch "
        "FROM wallet_deposits WHERE user_id=%s AND status='rejected' "
        "ORDER BY reviewed_at DESC,id DESC LIMIT 1", (user_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    cambodia = timezone(timedelta(hours=7))
    reviewed = datetime.fromtimestamp(row["reviewed_epoch"], cambodia)
    now = datetime.fromtimestamp(row["now_epoch"], cambodia)
    if reviewed.date() != now.date():
        return None
    retry_at = reviewed.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return {"id": row["id"], "retry_at": retry_at.isoformat()}


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
            cur.execute(
                "SELECT id,method,claimed,created_at FROM wallet_deposits "
                "WHERE user_id=%s AND status='pending' ORDER BY id LIMIT 1", (user_id,),
            )
            pending_deposit = cur.fetchone()
            if pending_deposit:
                pending_deposit["claimed"] = str(pending_deposit["claimed"])
                pending_deposit["created_at"] = str(pending_deposit["created_at"])
            rejected_deposit = rejected_deposit_today(cur, user_id)
        return {
            "address": config["address"] if config else None,
            "aba": aba,
            "currency": "USD",
            "balance": str(balance["balance"]) if balance else "0.000000",
            "deposits": deposits,
            "pending_deposit": pending_deposit,
            "rejected_deposit": rejected_deposit,
            "deposit_limit": "300",
        }
    finally:
        conn.close()


def normalize_proof(raw):
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise ValueError("请上传不超过 5 MB 的付款截图")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 16000000:
                    raise ValueError("请上传 JPG、PNG 或 WebP 截图，图片不得超过 1600 万像素")
                image.load()
                image = image.convert("RGB")
                image.thumbnail((1600, 1600))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=85)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("付款截图无法读取，请重新选择有效图片") from exc


def submit(user_id, txid, amount, method="usdt", proof=None):
    if not isinstance(method, str) or method not in {"usdt", "aba"}:
        raise ValueError("请选择 USDT 或 ABA")
    manual_aba = method == "aba" and txid is None and proof is None
    if manual_aba:
        reference = f"manual:{uuid.uuid4().hex}"
    elif proof is not None:
        proof = normalize_proof(proof)
        digest = hashlib.sha256(proof).hexdigest()
        reference = f"proof:{digest}"
    elif method == "usdt":
        if not isinstance(txid, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", txid):
            raise ValueError("TXID 应为 64 位十六进制交易哈希")
        reference = txid.lower()
    else:
        if not isinstance(txid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,100}", txid):
            raise ValueError("ABA 交易编号须为 6–100 位字母、数字、下划线或连字符")
        reference = f"aba:{txid.lower()}"
    amount = amount_value(amount)
    if amount > Decimal("300"):
        raise ValueError("每笔充值最多 300 USD（USDT 按 1:1），请勿超额转账")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Serialize claims for one account, including submissions from different payment methods.
            cur.execute(
                "INSERT INTO wallet_accounts(user_id,balance) VALUES(%s,0) "
                "ON DUPLICATE KEY UPDATE user_id=VALUES(user_id)", (user_id,),
            )
            cur.execute("SELECT balance FROM wallet_accounts WHERE user_id=%s FOR UPDATE", (user_id,))
            cur.fetchone()
            cur.execute(
                "SELECT id FROM wallet_deposits WHERE user_id=%s AND status='pending' ORDER BY id LIMIT 1",
                (user_id,),
            )
            if cur.fetchone():
                raise ValueError("已有充值申请等待审核，审核结束后才能再次充值；请勿重复转账")
            rejected = rejected_deposit_today(cur, user_id)
            if rejected:
                raise ValueError(
                    f"充值申请 #{rejected['id']} 已被拒绝，当天不能再次提交 USDT 或 ABA 申请；"
                    f"柬埔寨时间 {rejected['retry_at'][:10]} 00:00 恢复，请勿重复付款"
                )
            if method == "usdt":
                cur.execute("SELECT address FROM wallet_config WHERE id=1")
                config = cur.fetchone()
                if not config or not valid_address(config["address"]):
                    raise ValueError("充值尚未开放，请等待管理员配置收款地址")
                address = config["address"]
            else:
                cur.execute("SELECT account_number,account_name FROM wallet_aba_config WHERE id=1")
                config = cur.fetchone()
                if not config and not manual_aba:
                    raise ValueError("ABA 充值尚未开放，请等待管理员配置美元收款账户")
                address = f"{config['account_number']} / {config['account_name']}" if config else "ABA / 联系昊天确认美元收款"
            cur.execute(
                "INSERT INTO wallet_deposits(user_id,txid,claimed,address,method) VALUES(%s,%s,%s,%s,%s)",
                (user_id, reference, amount, address, method),
            )
            deposit_id = cur.lastrowid
            if proof is not None:
                cur.execute(
                    "INSERT INTO wallet_deposit_proofs(deposit_id,digest,image) VALUES(%s,%s,%s)",
                    (deposit_id, digest, proof),
                )
        conn.commit()
        return deposit_id
    except pymysql.IntegrityError as exc:
        conn.rollback()
        if exc.args[0] == 1062:
            raise ValueError("该交易编号或付款凭证已提交，请查看充值记录或联系管理员，勿重复付款") from exc
        raise
    except Exception:
        conn.rollback()
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


def proof_image(deposit_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT image FROM wallet_deposit_proofs WHERE deposit_id=%s", (deposit_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("该申请没有上传凭证")
            return row["image"]
    finally:
        conn.close()


def review(deposit_id, admin_id, amount=None, reference=None):
    credit = amount_value(amount) if amount is not None else None
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM wallet_deposits WHERE id=%s FOR UPDATE", (deposit_id,))
            deposit = cur.fetchone()
            if not deposit or deposit["status"] != "pending":
                raise ValueError("申请不存在或已处理，不能重复入账")
            if credit is not None:
                if deposit.get("txid", "").startswith(("proof:", "manual:")):
                    if deposit["method"] == "usdt":
                        if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", reference):
                            raise ValueError("凭证申请入账前，管理员必须填写核实的真实 USDT 交易编号（64位）")
                        reference = reference.lower()
                    else:
                        if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,100}", reference):
                            raise ValueError("ABA 申请入账前，管理员必须填写核实的真实 ABA 交易编号")
                        reference = f"aba:{reference.lower()}"
                    cur.execute("UPDATE wallet_deposits SET txid=%s WHERE id=%s", (reference, deposit_id))
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
