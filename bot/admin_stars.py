"""Administrator-only Stars funding through Telegram invoices."""
import re
import time

from telebot import types

from db import get_conn
from visit_stats import admin_ids

PREFIX = "admin-stars:"
MAX_STARS = 10000


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS admin_star_funding ("
                "order_id CHAR(32) PRIMARY KEY,admin_id BIGINT NOT NULL,"
                "stars INT NOT NULL,status VARCHAR(16) NOT NULL DEFAULT 'pending',"
                "expires_at BIGINT NOT NULL,invoice_url VARCHAR(512) NULL,"
                "charge_id VARCHAR(256) CHARACTER SET ascii COLLATE ascii_bin NULL UNIQUE,"
                "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "paid_at TIMESTAMP NULL,INDEX idx_star_admin(admin_id,created_at)"
                ") ENGINE=InnoDB"
            )
        conn.commit()
    finally:
        conn.close()


def require_admin(user_id):
    if type(user_id) is not int or user_id not in admin_ids():
        raise ValueError("仅平台管理员可补充机器人 Stars")


def order_id_from_payload(payload):
    if not isinstance(payload, str) or not re.fullmatch(r"admin-stars:[0-9a-f]{32}", payload):
        raise ValueError("不是有效的管理员 Stars 补充订单")
    return payload[len(PREFIX):]


def dashboard(bot, user_id, order_id=None):
    require_admin(user_id)
    if order_id is not None and (not isinstance(order_id, str) or not re.fullmatch(r"[0-9a-f]{32}", order_id)):
        raise ValueError("补充订单编号无效，请核对保存的订单")
    balance = bot.get_my_star_balance()
    if balance is None:
        raise RuntimeError("Telegram 未返回机器人 Stars 余额")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_id,admin_id,stars,status,expires_at,created_at,paid_at "
                "FROM admin_star_funding ORDER BY created_at DESC,order_id DESC LIMIT 10"
            )
            rows = cur.fetchall()
            current_order = None
            if order_id is not None:
                cur.execute(
                    "SELECT order_id,admin_id,stars,status,expires_at,created_at,paid_at "
                    "FROM admin_star_funding WHERE order_id=%s AND admin_id=%s", (order_id, user_id),
                )
                current_order = cur.fetchone()
            now = int(time.time())
            for row in [*rows, *([current_order] if current_order else [])]:
                if row["status"] != "paid" and row["expires_at"] <= now:
                    row["status"] = "expired"
                for key in ("created_at", "paid_at"):
                    if row[key] is not None:
                        row[key] = str(row[key])
        return {"balance": balance.amount, "nanostar_amount": balance.nanostar_amount or 0,
                "max_stars": MAX_STARS, "orders": rows, "current_order": current_order}
    finally:
        conn.close()


def create_invoice(bot, user_id, order_id, stars):
    require_admin(user_id)
    if not isinstance(order_id, str) or not re.fullmatch(r"[0-9a-f]{32}", order_id):
        raise ValueError("补充订单编号无效，请刷新后重试")
    if type(stars) is not int or not 1 <= stars <= MAX_STARS:
        raise ValueError(f"每笔须为 1–{MAX_STARS} 个 Stars 的整数")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            expires = int(time.time()) + 900
            cur.execute(
                "INSERT INTO admin_star_funding(order_id,admin_id,stars,expires_at) VALUES(%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE order_id=VALUES(order_id)",
                (order_id, user_id, stars, expires),
            )
            cur.execute("SELECT * FROM admin_star_funding WHERE order_id=%s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if row["admin_id"] != user_id or row["stars"] != stars:
                raise ValueError("订单已用于其他账户或数量，不可修改，请核对原订单")
            if row["status"] != "pending":
                raise ValueError("订单已付款或进入付款核实阶段，请刷新记录，勿重复付款")
            if row["expires_at"] <= int(time.time()):
                raise ValueError("付款订单已过期，请刷新记录后重新填写")
            link = row["invoice_url"]
            if not link:
                link = bot.create_invoice_link(
                    title="管理员补充机器人 Stars",
                    description=f"向机器人支付 {stars} Stars；不充值客户美元余额，不购买会员，不自动开启销售。",
                    payload=PREFIX + order_id, provider_token="", currency="XTR",
                    prices=[types.LabeledPrice("机器人 Stars 补充", stars)],
                )
                if not isinstance(link, str) or not re.fullmatch(r"https://t\.me/\$[A-Za-z0-9_-]+", link):
                    raise RuntimeError("Telegram 未返回有效官方付款链接")
                cur.execute("UPDATE admin_star_funding SET invoice_url=%s WHERE order_id=%s", (link, order_id))
        conn.commit()
        return {"order_id": order_id, "invoice_url": link, "stars": stars}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def approve_checkout(user_id, payload, currency, amount):
    require_admin(user_id)
    order_id = order_id_from_payload(payload)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM admin_star_funding WHERE order_id=%s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if (not row or row["admin_id"] != user_id or currency != "XTR"
                    or type(amount) is not int or amount != row["stars"]):
                raise ValueError("付款账户、币种或 Stars 数量与订单不一致")
            if row["expires_at"] <= int(time.time()) or row["status"] != "pending":
                raise ValueError("订单已过期或正在核实付款，请刷新后台记录，勿重复付款")
            cur.execute("UPDATE admin_star_funding SET status='checkout' WHERE order_id=%s", (order_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_payment(user_id, payment):
    order_id = order_id_from_payload(payment.invoice_payload)
    if (payment.currency != "XTR" or type(payment.total_amount) is not int
            or not isinstance(payment.telegram_payment_charge_id, str)
            or not 1 <= len(payment.telegram_payment_charge_id) <= 256):
        raise ValueError("Telegram 付款通知字段无效，请核实官方 Stars 交易")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM admin_star_funding WHERE order_id=%s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if not row or row["admin_id"] != user_id or row["stars"] != payment.total_amount:
                raise ValueError("Telegram 付款通知与补充订单不一致")
            if row["status"] == "paid":
                if row["charge_id"] != payment.telegram_payment_charge_id:
                    raise ValueError("同一补充订单出现不同付款编号，请核实官方交易")
                return {"order_id": order_id, "stars": row["stars"], "duplicate": True}
            if row["status"] != "checkout":
                raise ValueError("补充订单未通过付款核对，请核实官方交易")
            cur.execute(
                "UPDATE admin_star_funding SET status='paid',charge_id=%s,paid_at=NOW() WHERE order_id=%s",
                (payment.telegram_payment_charge_id, order_id),
            )
        conn.commit()
        return {"order_id": order_id, "stars": row["stars"], "duplicate": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
