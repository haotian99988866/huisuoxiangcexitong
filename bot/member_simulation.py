"""Administrator-only simulation with separate balances and no Telegram calls."""
from decimal import Decimal

from db import get_conn
from premium_balance import PLANS, normalize_username, validate_order_id


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS member_test_accounts ("
                "user_id BIGINT PRIMARY KEY,balance DECIMAL(20,6) NOT NULL DEFAULT 50) ENGINE=InnoDB")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS member_test_orders ("
                "order_id CHAR(32) PRIMARY KEY,user_id BIGINT NOT NULL,"
                "username VARCHAR(32) NOT NULL,months TINYINT NOT NULL,"
                "price_usd DECIMAL(20,6) NOT NULL,scenario VARCHAR(16) NOT NULL,"
                "status VARCHAR(32) NOT NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                "INDEX idx_member_test_user(user_id,created_at)) ENGINE=InnoDB")
        conn.commit()
    finally:
        conn.close()


def account(user_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT IGNORE INTO member_test_accounts(user_id) VALUES(%s)", (user_id,))
            cur.execute("SELECT balance FROM member_test_accounts WHERE user_id=%s", (user_id,))
            balance = cur.fetchone()["balance"]
            cur.execute(
                "SELECT order_id,username,months,price_usd,status,scenario FROM member_test_orders "
                "WHERE user_id=%s ORDER BY created_at DESC,order_id DESC LIMIT 30", (user_id,))
            rows = cur.fetchall()
        conn.commit()
        return {"simulation": True, "balance": str(balance),
                "orders": [{**row, "price_usd": str(row["price_usd"])} for row in rows]}
    finally:
        conn.close()


def purchase(user_id, order_id, username, months, scenario):
    validate_order_id(order_id)
    username = normalize_username(username)
    if type(months) is not int or months not in PLANS:
        raise ValueError("请选择有效套餐")
    if not isinstance(scenario, str) or scenario not in {"success", "failure", "unknown"}:
        raise ValueError("请选择模拟成功、失败或结果不明")
    price = PLANS[months]["price_usd"]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT IGNORE INTO member_test_accounts(user_id) VALUES(%s)", (user_id,))
            cur.execute("SELECT balance FROM member_test_accounts WHERE user_id=%s FOR UPDATE", (user_id,))
            balance = cur.fetchone()["balance"]
            cur.execute("SELECT * FROM member_test_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            existing = cur.fetchone()
            if existing:
                if (existing["user_id"] != user_id or existing["username"] != username
                        or existing["months"] != months or existing["scenario"] != scenario):
                    raise ValueError("测试订单编号冲突，请核实原请求")
                conn.rollback()
                return {"simulation": True, "order_id": order_id, "status": existing["status"]}
            if balance < price:
                raise ValueError("模拟余额不足，不会动用正式余额")
            status = {"success": "simulated_success", "failure": "simulated_refunded",
                      "unknown": "simulated_unknown"}[scenario]
            debit = Decimal("0") if scenario == "failure" else price
            cur.execute(
                "UPDATE member_test_accounts SET balance=balance-%s WHERE user_id=%s", (debit, user_id))
            cur.execute(
                "INSERT INTO member_test_orders(order_id,user_id,username,months,price_usd,scenario,status) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s)", (order_id, user_id, username, months, price, scenario, status))
        conn.commit()
        return {"simulation": True, "order_id": order_id, "status": status}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
