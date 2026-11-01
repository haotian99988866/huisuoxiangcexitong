"""Transactional USD wallet purchases fulfilled manually by the owner."""
import logging
import re
import threading
from decimal import Decimal

from telebot import apihelper, types

from db import get_conn

logger = logging.getLogger(__name__)
ORDER_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
PLANS = {
    3: {"price_usd": Decimal("15.99")},
    6: {"price_usd": Decimal("19.99")},
    12: {"price_usd": Decimal("33.99")},
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
            cur.execute("SHOW COLUMNS FROM member_sales_config LIKE 'owner_id'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE member_sales_config ADD COLUMN owner_id BIGINT NULL")
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
            for name, definition in (
                ("owner_id", "BIGINT NULL"),
                ("notification_id", "BIGINT NULL"),
            ):
                cur.execute(f"SHOW COLUMNS FROM member_orders LIKE '{name}'")
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE member_orders ADD COLUMN {name} {definition}")
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
        owner_id = manual_owner()
        chat = bot.get_chat(owner_id)
        if chat.type != "private":
            raise ValueError("人工开通负责人必须先私聊机器人发送 /start")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE member_sales_config SET enabled=%s WHERE id=1", (int(enabled),))
        conn.commit()
    finally:
        conn.close()


def manual_owner():
    from visit_stats import admin_ids
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_id FROM member_sales_config WHERE id=1")
            config = cur.fetchone()
        if not config or config["owner_id"] not in admin_ids():
            raise ValueError("人工开通负责人尚未配置或已无管理员权限")
        return config["owner_id"]
    finally:
        conn.close()


def require_order_owner(order, actor_id):
    from visit_stats import admin_ids
    if type(actor_id) is not int or actor_id != order.get("owner_id") or actor_id not in admin_ids():
        raise ValueError("仅此订单的开通负责人可操作")


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
    # Replays must succeed even after sales are disabled.
    existing = get_order(order_id, payer_id)
    if existing:
        if existing["months"] != months or existing["recipient_username"] != normalized:
            raise ValueError("订单编号已用于其他套餐或收件人，请先核实原订单")
        return existing
    recipient_id, normalized = validate_recipient(bot, username)
    plan = PLANS[months]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled,owner_id FROM member_sales_config WHERE id=1 FOR UPDATE")
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
            from visit_stats import admin_ids
            if not config.get("owner_id") or config["owner_id"] not in admin_ids():
                raise ValueError("人工开通负责人不可用，未扣除余额")
            cur.execute("SELECT balance FROM wallet_accounts WHERE user_id=%s FOR UPDATE", (payer_id,))
            account = cur.fetchone()
            if not account or account["balance"] < plan["price_usd"]:
                raise ValueError("账户余额不足，请先充值并等待管理员核款")
            cur.execute(
                "INSERT INTO member_orders(order_id,payer_id,recipient_id,recipient_username,"
                "months,price_usd,gift_stars,status,owner_id) VALUES(%s,%s,%s,%s,%s,%s,0,'manual_pending',%s)",
                (order_id, payer_id, recipient_id, normalized, months, plan["price_usd"], config["owner_id"]))
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
            "price_usd": str(plan["price_usd"]), "status": "manual_pending"}


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


def refund_order(order_id, actor_id=None, confirmed_not_delivered=False):
    validate_order_id(order_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM member_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            order = cur.fetchone()
            if not order:
                raise ValueError("订单不存在")
            if order.get("owner_id") is not None:
                require_order_owner(order, actor_id)
            if order["status"] == "refunded":
                conn.rollback()
                return order
            if order["status"] != "refund_pending" and not (
                    order["status"] in {"delivery_unknown", "manual_pending"}
                    and actor_id is not None and confirmed_not_delivered):
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
        return order
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
            cur.execute("SELECT * FROM member_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("订单不存在")
            if row.get("owner_id") is not None:
                require_order_owner(row, actor_id)
            if row["status"] != "delivery_unknown" and not (
                    row["status"] == "manual_pending" and row.get("owner_id") is not None):
                raise ValueError("只能确认待核实订单，请先核实官方赠送记录")
            cur.execute(
                "INSERT INTO member_wallet_ledger(order_id,user_id,event,amount,actor_id) "
                "SELECT order_id,payer_id,'confirmed',0,%s FROM member_orders WHERE order_id=%s",
                (actor_id, order_id))
            cur.execute(
                "UPDATE member_orders SET status='delivered',error=NULL,finished_at=NOW() WHERE order_id=%s",
                (order_id,))
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def manual_order_text(order):
    status = {"manual_pending": "等待昊天手动开通", "delivered": "已开通完成", "refunded": "已退回站内余额"}.get(
        order["status"], order["status"])
    return (
        f"人工会员订单 {order['order_id']}\n"
        f"付款客户 ID：{order['payer_id']}\n"
        f"开通账号：@{order['recipient_username']}\n"
        f"收件人 ID：{order['recipient_id']}\n"
        f"套餐：{order['months']}个月 · 已扣 ${order['price_usd']} USD\n"
        f"状态：{status}\n"
        "请核对用户名及数字 ID，手动开通后再确认完成；无法开通须确认未开通后退余额。"
    )


def manual_keyboard(order_id, decision=None):
    keyboard = types.InlineKeyboardMarkup()
    if decision:
        keyboard.add(types.InlineKeyboardButton(
            "确认已经开通" if decision == "done" else "确认未开通并退余额",
            callback_data=f"manual:confirm_{decision}:{order_id}",
        ))
        keyboard.add(types.InlineKeyboardButton("返回核对", callback_data=f"manual:back:{order_id}"))
    else:
        keyboard.add(types.InlineKeyboardButton("开通完成", callback_data=f"manual:done:{order_id}"))
        keyboard.add(types.InlineKeyboardButton("无法开通，退余额", callback_data=f"manual:refund:{order_id}"))
    return keyboard


def notify_manual_order(bot, order_id):
    validate_order_id(order_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM member_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if not row or row["status"] != "manual_pending" or row["notification_id"] is not None:
                conn.rollback()
                return
            require_order_owner(row, row["owner_id"])
            message = bot.send_message(
                row["owner_id"], manual_order_text(row), reply_markup=manual_keyboard(order_id),
                disable_web_page_preview=True, timeout=10,
            )
            cur.execute("UPDATE member_orders SET notification_id=%s,error=NULL WHERE order_id=%s",
                        (message.message_id, order_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def process_orders(bot):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_id,status FROM member_orders WHERE "
                "(status='manual_pending' AND notification_id IS NULL) OR status='refund_pending' "
                "ORDER BY created_at LIMIT 50"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            if row["status"] == "manual_pending":
                notify_manual_order(bot, row["order_id"])
            else:
                refund_order(row["order_id"])
        except Exception:
            logger.exception("Member order worker failed order=%s; retained for reconciliation", row["order_id"])
            if row["status"] != "manual_pending":
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
