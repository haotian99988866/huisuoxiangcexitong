"""USD wallet purchases fulfilled through Telegram's official Premium gift API."""
import logging
import re
import threading
from decimal import Decimal

from telebot import apihelper

from db import get_conn

logger = logging.getLogger(__name__)
ORDER_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
PLANS = {
    3: {"price_usd": Decimal("15.99"), "gift_stars": 1000},
    6: {"price_usd": Decimal("19.99"), "gift_stars": 1500},
    12: {"price_usd": Decimal("33.99"), "gift_stars": 2500},
}


def normalize_username(username):
    if not isinstance(username, str):
        raise ValueError("请输入 Telegram 用户名")
    username = username.strip()
    if username.startswith("@"):
        username = username[1:]
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        raise ValueError("请输入有效的 Telegram 用户名，例如 @username")
    return username.lower()


def resolve_recipient(username):
    normalized = normalize_username(username)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT tg_user_id,username FROM bot_users WHERE LOWER(username)=%s "
                "ORDER BY last_active DESC LIMIT 2", (normalized,))
            rows = cursor.fetchall()
        if not rows:
            raise ValueError(
                f"机器人暂时无法识别 @{normalized}。请让对方先打开机器人并发送 /start 后再试")
        if len(rows) > 1:
            raise ValueError(
                f"机器人记录中 @{normalized} 对应多个账户，暂时无法安全下单，请联系管理员")
        return rows[0]["tg_user_id"], normalized
    finally:
        conn.close()


def _notify_user(bot, user_id, text):
    try:
        bot.send_message(user_id, text)
    except apihelper.ApiException:
        logger.exception("Could not notify Premium customer user=%s", user_id)


def _notify_admins(bot, text):
    from visit_stats import admin_ids
    for admin_id in admin_ids():
        _notify_user(bot, admin_id, text)


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS member_sales_config (id INT PRIMARY KEY,"
                "enabled TINYINT NOT NULL DEFAULT 0) ENGINE=InnoDB")
            cur.execute("INSERT IGNORE INTO member_sales_config(id,enabled) VALUES(1,0)")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS member_orders ("
                "order_id CHAR(32) PRIMARY KEY,payer_id BIGINT NOT NULL,recipient_id BIGINT NOT NULL,"
                "recipient_username VARCHAR(32) NOT NULL,months TINYINT NOT NULL,"
                "price_usd DECIMAL(20,6) NOT NULL,gift_stars INT NOT NULL,"
                "status VARCHAR(32) NOT NULL,error VARCHAR(64) NULL,"
                "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "delivery_at TIMESTAMP NULL,finished_at TIMESTAMP NULL,"
                "INDEX idx_member_payer(payer_id,created_at),INDEX idx_member_status(status)"
                ") ENGINE=InnoDB")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS member_wallet_ledger ("
                "id BIGINT AUTO_INCREMENT PRIMARY KEY,order_id CHAR(32) NOT NULL,"
                "user_id BIGINT NOT NULL,event VARCHAR(16) NOT NULL,amount DECIMAL(20,6) NOT NULL,"
                "actor_id BIGINT NULL,created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "UNIQUE KEY idx_member_event(order_id,event)) ENGINE=InnoDB")
        conn.commit()
    finally:
        conn.close()


def catalog():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled FROM member_sales_config WHERE id=1")
            config = cur.fetchone()
        return {
            "enabled": bool(config and config["enabled"]), "currency": "USD",
            "plans": [{"months": months, "price_usd": str(plan["price_usd"])} for months, plan in PLANS.items()],
        }
    finally:
        conn.close()


def set_enabled(bot, enabled):
    if enabled:
        balance = bot.get_my_star_balance()
        if not balance or balance.amount < 2500:
            raise ValueError("机器人至少需备足 2500 Stars 才能开放全部套餐")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE member_sales_config SET enabled=%s WHERE id=1", (int(enabled),))
        conn.commit()
    finally:
        conn.close()


def validate_recipient(bot, username):
    recipient_id, normalized = resolve_recipient(username)
    chat = bot.get_chat(recipient_id)
    if chat.type != "private" or normalize_username(chat.username) != normalized:
        raise ValueError("收件人用户名记录已变化，请让对方重新发送 /start 后再购买")
    return recipient_id, normalized


def validate_order_id(order_id):
    if not isinstance(order_id, str) or not ORDER_ID_PATTERN.fullmatch(order_id):
        raise ValueError("订单编号无效，请刷新页面")


def create_order(bot, payer_id, username, months, order_id):
    validate_order_id(order_id)
    if type(payer_id) is not int or not 0 < payer_id < 2**63:
        raise ValueError("账户身份无效")
    if type(months) is not int or months not in PLANS:
        raise ValueError("请选择有效套餐")
    normalized = normalize_username(username)
    # Replays must succeed even after the sale is disabled or stock is depleted.
    existing = get_order(order_id, payer_id)
    if existing:
        if existing["months"] != months or existing["recipient_username"] != normalized:
            raise ValueError("订单编号已用于其他套餐或收件人，请先核实原订单")
        return existing
    recipient_id, normalized = validate_recipient(bot, username)
    balance = bot.get_my_star_balance()
    if not balance or balance.amount < PLANS[months]["gift_stars"]:
        raise ValueError("机器人 Stars 库存不足，未扣除余额，请稍后再试")
    plan = PLANS[months]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled FROM member_sales_config WHERE id=1 FOR UPDATE")
            config = cur.fetchone()
            cur.execute("SELECT * FROM member_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            existing = cur.fetchone()
            if existing:
                if (existing["payer_id"] != payer_id or existing["months"] != months
                        or existing["recipient_username"] != normalized):
                    raise ValueError("订单编号冲突，请先核实原订单")
                conn.rollback()
                return existing
            if not config or not config["enabled"]:
                raise ValueError("会员余额购买暂未开放")
            cur.execute(
                "SELECT COALESCE(SUM(gift_stars),0) AS reserved FROM member_orders "
                "WHERE status IN ('paid','delivering','delivery_unknown')")
            if balance.amount - cur.fetchone()["reserved"] < plan["gift_stars"]:
                raise ValueError("机器人 Stars 库存已被其他订单占用，未扣除余额")
            cur.execute("SELECT balance FROM wallet_accounts WHERE user_id=%s FOR UPDATE", (payer_id,))
            account = cur.fetchone()
            if not account or account["balance"] < plan["price_usd"]:
                raise ValueError("账户余额不足，请先充值并等待管理员核款")
            cur.execute(
                "INSERT INTO member_orders(order_id,payer_id,recipient_id,recipient_username,"
                "months,price_usd,gift_stars,status) VALUES(%s,%s,%s,%s,%s,%s,%s,'paid')",
                (order_id, payer_id, recipient_id, normalized, months, plan["price_usd"], plan["gift_stars"]))
            cur.execute("UPDATE wallet_accounts SET balance=balance-%s WHERE user_id=%s",
                        (plan["price_usd"], payer_id))
            cur.execute(
                "INSERT INTO member_wallet_ledger(order_id,user_id,event,amount) VALUES(%s,%s,'purchase',%s)",
                (order_id, payer_id, -plan["price_usd"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"order_id": order_id, "recipient_username": normalized, "months": months,
            "price_usd": str(plan["price_usd"]), "status": "paid"}


def get_order(order_id, payer_id=None):
    validate_order_id(order_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM member_orders WHERE order_id=%s", (order_id,))
            row = cur.fetchone()
        return row if row and (payer_id is None or row["payer_id"] == payer_id) else None
    finally:
        conn.close()


def recent_orders(payer_id=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            query = "SELECT order_id,payer_id,recipient_username,months,price_usd,status,error,created_at FROM member_orders"
            cur.execute(query + (" WHERE payer_id=%s" if payer_id is not None else "")
                        + " ORDER BY created_at DESC,order_id DESC LIMIT 30",
                        (payer_id,) if payer_id is not None else ())
            rows = cur.fetchall()
        return [{**row, "price_usd": str(row["price_usd"]),
                 "created_at": str(row["created_at"])} for row in rows]
    finally:
        conn.close()


def recent_ledger(user_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id,reference,event,amount,created_at FROM ("
                "SELECT id,CAST(deposit_id AS CHAR) AS reference,'deposit' AS event,amount,created_at "
                "FROM wallet_ledger WHERE user_id=%s "
                "UNION ALL "
                "SELECT id,order_id AS reference,event,amount,created_at "
                "FROM member_wallet_ledger WHERE user_id=%s AND event IN ('purchase','refund')"
                ") AS movements ORDER BY created_at DESC,event DESC,id DESC LIMIT 30",
                (user_id, user_id))
            rows = cur.fetchall()
        return [{**row, "reference": str(row["reference"]), "amount": str(row["amount"]),
                 "created_at": str(row["created_at"])} for row in rows]
    finally:
        conn.close()


def begin_delivery(order_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM member_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if not row or row["status"] != "paid":
                conn.rollback()
                return None
            cur.execute("UPDATE member_orders SET status='delivering',delivery_at=NOW() WHERE order_id=%s",
                        (order_id,))
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_order(order_id, status, error=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE member_orders SET status=%s,error=%s,finished_at=NOW() "
                "WHERE order_id=%s AND status='delivering'", (status, error, order_id))
            if cur.rowcount != 1:
                raise ValueError("订单状态已变化，请核实赠送结果")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def refund_order(order_id, actor_id=None, confirmed_not_delivered=False):
    validate_order_id(order_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM member_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            order = cur.fetchone()
            if not order:
                raise ValueError("订单不存在")
            if order["status"] == "refunded":
                conn.rollback()
                return
            if order["status"] != "refund_pending" and not (
                    order["status"] == "delivery_unknown" and actor_id is not None and confirmed_not_delivered):
                raise ValueError("必须核实未赠送，才能对结果不明的订单退款")
            cur.execute(
                "INSERT INTO wallet_accounts(user_id,balance) VALUES(%s,%s) "
                "ON DUPLICATE KEY UPDATE balance=balance+VALUES(balance)",
                (order["payer_id"], order["price_usd"]))
            cur.execute(
                "INSERT INTO member_wallet_ledger(order_id,user_id,event,amount,actor_id) "
                "VALUES(%s,%s,'refund',%s,%s)",
                (order_id, order["payer_id"], order["price_usd"], actor_id))
            cur.execute("UPDATE member_orders SET status='refunded',finished_at=NOW() WHERE order_id=%s", (order_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def confirm_delivery(order_id, actor_id):
    validate_order_id(order_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM member_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if not row or row["status"] != "delivery_unknown":
                raise ValueError("只能确认待核实订单，请先核实官方赠送记录")
            cur.execute(
                "INSERT INTO member_wallet_ledger(order_id,user_id,event,amount,actor_id) "
                "SELECT order_id,payer_id,'confirmed',0,%s FROM member_orders WHERE order_id=%s",
                (actor_id, order_id))
            cur.execute(
                "UPDATE member_orders SET status='delivered',error=NULL,finished_at=NOW() WHERE order_id=%s",
                (order_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def deliver_order(bot, order_id):
    order = begin_delivery(order_id)
    if not order:
        return
    preflight_error = None
    try:
        chat = bot.get_chat(order["recipient_id"])
        if chat.type != "private" or normalize_username(chat.username) != order["recipient_username"]:
            preflight_error = "RecipientChanged"
        else:
            balance = bot.get_my_star_balance()
            if not balance or balance.amount < order["gift_stars"]:
                preflight_error = "InsufficientStars"
    except (apihelper.ApiException, ValueError):
        logger.exception("Member gift preflight failed order=%s", order_id)
        preflight_error = "PreflightFailed"
    if preflight_error:
        logger.warning("Member order not sent to gift API order=%s reason=%s", order_id, preflight_error)
        finish_order(order_id, "refund_pending", preflight_error)
        refund_order(order_id)
        _notify_user(bot, order["payer_id"], "暂时无法核实收件人或库存，金额已退回 USD 账户余额。")
        _notify_admins(bot, f"会员订单 {order_id} 未赠送，已退回站内余额；原因：{preflight_error}。")
        return
    try:
        result = bot.gift_premium_subscription(
            user_id=order["recipient_id"], month_count=order["months"],
            star_count=order["gift_stars"], text="感谢购买 Telegram Premium")
    except apihelper.ApiTelegramException as exc:
        if exc.error_code >= 500:
            logger.exception("Member gift server outcome unknown order=%s", order_id)
            finish_order(order_id, "delivery_unknown", "TelegramServerError")
            _notify_admins(bot, f"会员订单 {order_id} 赠送结果不明，请核实，勿重复赠送或退款。")
            return
        logger.exception("Member gift rejected order=%s", order_id)
        finish_order(order_id, "refund_pending", "GiftRejected")
        refund_order(order_id)
        _notify_user(bot, order["payer_id"], "会员赠送失败，购买金额已退回 USD 账户余额。")
        return
    except Exception as exc:
        logger.exception("Member gift outcome unknown order=%s", order_id)
        finish_order(order_id, "delivery_unknown", type(exc).__name__)
        _notify_admins(bot, f"会员订单 {order_id} 赠送结果不明，请核实，勿重复赠送或退款。")
        _notify_user(bot, order["payer_id"], "会员开通结果正在核实，请勿重复购买，保留订单编号联系管理员。")
        return
    if result is not True:
        logger.error("Unexpected Premium gift response order=%s", order_id)
        finish_order(order_id, "delivery_unknown", "UnexpectedResponse")
        _notify_admins(bot, f"会员订单 {order_id} 返回异常，请核实官方交易，勿重试。")
        return
    finish_order(order_id, "delivered")
    _notify_user(bot, order["payer_id"],
                 f"Premium 已赠送给 @{order['recipient_username']}，有效期 {order['months']}个月。")


def process_orders(bot):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_id FROM member_orders WHERE status='delivering' "
                "AND delivery_at<DATE_SUB(NOW(),INTERVAL 10 MINUTE) LIMIT 50")
            stale = [row["order_id"] for row in cur.fetchall()]
            for order_id in stale:
                cur.execute(
                    "UPDATE member_orders SET status='delivery_unknown',error='WorkerInterrupted' "
                    "WHERE order_id=%s AND status='delivering' "
                    "AND delivery_at<DATE_SUB(NOW(),INTERVAL 10 MINUTE)", (order_id,))
            cur.execute(
                "SELECT order_id,status FROM member_orders WHERE status IN ('paid','refund_pending') "
                "ORDER BY created_at LIMIT 50")
            rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    for order_id in stale:
        _notify_admins(bot, f"会员订单 {order_id} 处理曾中断，需核实赠送结果，勿重复赠送。")
    for row in rows:
        try:
            if row["status"] == "paid":
                deliver_order(bot, row["order_id"])
            else:
                refund_order(row["order_id"])
        except Exception:
            logger.exception("Member order worker failed order=%s; retained for reconciliation", row["order_id"])
            _notify_admins(bot, f"会员订单 {row['order_id']} 处理异常，请检查订单及官方交易记录。")


def start_worker(bot):
    def run():
        while True:
            try:
                process_orders(bot)
            except Exception:
                logger.exception("Member order queue unavailable")
            threading.Event().wait(10)
    thread = threading.Thread(target=run, name="member-orders", daemon=True)
    thread.start()
