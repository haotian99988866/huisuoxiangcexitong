"""Transactional USD wallet membership and phone orders fulfilled manually."""
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
PHONE_FEE = Decimal("1.00")


def normalize_phone(phone):
    if not isinstance(phone, str):
        raise ValueError("请输入柬埔寨手机号")
    phone = re.sub(r"[\s()-]", "", phone)
    if phone.startswith("+855"):
        phone = "0" + phone[4:]
    elif phone.startswith("855"):
        phone = "0" + phone[3:]
    if not re.fullmatch(r"0[1-9][0-9]{7,8}", phone):
        raise ValueError("请输入有效柬埔寨手机号，使用 0 开头或 +855 国际格式")
    return "+855" + phone[1:]


def phone_amount(amount):
    if not isinstance(amount, str) or not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,2})?", amount):
        raise ValueError("话费金额须为美元数字，最多两位小数")
    value = Decimal(amount)
    if not Decimal("5") <= value <= Decimal("100"):
        raise ValueError("每单话费金额最低 $5、最高 $100，另加 $1 手续费")
    return value


def order_public(order):
    return {
        "order_id": order["order_id"], "months": order["months"],
        "recipient_username": order["recipient_username"],
        "price_usd": str(order["price_usd"]), "status": order["status"],
        "product_kind": order.get("product_kind", "premium"),
        "phone": order.get("phone"),
        "topup_usd": str(order["topup_usd"]) if order.get("topup_usd") is not None else None,
        "fee_usd": str(order.get("fee_usd", 0)),
        "rejection_reason": order.get("refund_reason") if order["status"] == "refunded" else None,
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
    from review_notifications import ensure_tables as ensure_notifications
    ensure_notifications()
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
            cur.execute("SHOW COLUMNS FROM member_sales_config LIKE 'phone_enabled'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE member_sales_config ADD COLUMN phone_enabled TINYINT NOT NULL DEFAULT 1")
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
                ("product_kind", "VARCHAR(16) NOT NULL DEFAULT 'premium'"),
                ("phone", "VARCHAR(16) NULL"),
                ("topup_usd", "DECIMAL(20,6) NULL"),
                ("fee_usd", "DECIMAL(20,6) NOT NULL DEFAULT 0"),
                ("refund_reason", "VARCHAR(300) NULL"),
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
            cur.execute(
                "CREATE TABLE IF NOT EXISTS member_manual_inquiries ("
                "order_id CHAR(32) PRIMARY KEY,payer_id BIGINT NOT NULL,recipient_id BIGINT NULL,"
                "recipient_username VARCHAR(32) NOT NULL,months TINYINT NOT NULL,"
                "price_usd DECIMAL(20,6) NOT NULL,product_kind VARCHAR(16) NOT NULL,"
                "phone VARCHAR(16) NULL,topup_usd DECIMAL(20,6) NULL,fee_usd DECIMAL(20,6) NOT NULL DEFAULT 0,"
                "balance_usd DECIMAL(20,6) NOT NULL,owner_id BIGINT NOT NULL,"
                "status VARCHAR(24) NOT NULL DEFAULT 'pending_contact',"
                "owner_message_id BIGINT NULL,customer_message_id BIGINT NULL,"
                "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "INDEX idx_manual_inquiry_status(status,created_at),"
                "INDEX idx_manual_inquiry_payer(payer_id,created_at)) ENGINE=InnoDB")
        conn.commit()
    finally:
        conn.close()


def catalog():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled,phone_enabled FROM member_sales_config WHERE id=1")
            config = cur.fetchone()
        return {
            "enabled": bool(config and config["enabled"]), "currency": "USD",
            "plans": [{"months": months, "price_usd": str(plan["price_usd"])} for months, plan in PLANS.items()],
            "phone": {"enabled": bool(config and config["phone_enabled"]),
                      "min_usd": "5", "max_usd": "100", "fee_usd": str(PHONE_FEE)},
        }
    finally:
        conn.close()


def set_enabled(bot, enabled, product_kind="premium"):
    if product_kind not in {"premium", "phone"}:
        raise ValueError("无效服务")
    if enabled:
        owner_id = manual_owner()
        chat = bot.get_chat(owner_id)
        if chat.type != "private":
            raise ValueError("人工开通负责人必须先私聊机器人发送 /start")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            field = "phone_enabled" if product_kind == "phone" else "enabled"
            cur.execute(f"UPDATE member_sales_config SET {field}=%s WHERE id=1", (int(enabled),))
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


def create_order(bot, payer_id, username, months, order_id, *, product_kind="premium", phone=None, amount=None):
    validate_order_id(order_id)
    if type(payer_id) is not int or not 0 < payer_id < 2**63:
        raise ValueError("账户身份无效")
    if product_kind == "phone":
        phone = normalize_phone(phone)
        topup = phone_amount(amount)
        normalized, months, recipient_id = "", 0, payer_id
        price = topup + PHONE_FEE
    elif product_kind == "premium":
        if type(months) is not int or months not in PLANS:
            raise ValueError("请选择有效套餐")
        normalized = normalize_username(username)
        topup, phone, recipient_id = None, None, None
        price = PLANS[months]["price_usd"]
    else:
        raise ValueError("无效服务")

    def matches(order):
        return (order["payer_id"] == payer_id
                and order.get("product_kind", "premium") == product_kind
                and order["months"] == months and order["recipient_username"] == normalized
                and (product_kind != "phone" or (order["phone"] == phone and order["topup_usd"] == topup)))

    # Replays must succeed even after sales are disabled.
    existing = get_order(order_id, payer_id)
    if existing:
        if not matches(existing):
            raise ValueError("订单编号已用于其他服务、金额或收件人，请先核实原订单")
        return existing
    inquiry = get_manual_inquiry(order_id, payer_id)
    if inquiry:
        if not _inquiry_matches(inquiry, product_kind, months, normalized, phone, topup):
            raise ValueError("申请编号已用于其他服务、金额或收件人，请先核实原申请")
        return inquiry
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled,phone_enabled,owner_id FROM member_sales_config WHERE id=1 FOR UPDATE")
            config = cur.fetchone()
            cur.execute("SELECT * FROM member_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            existing = cur.fetchone()
            if existing:
                if not matches(existing):
                    raise ValueError("订单编号冲突，请先核实原订单")
                conn.rollback()
                return existing
            cur.execute("SELECT * FROM member_manual_inquiries WHERE order_id=%s FOR UPDATE", (order_id,))
            inquiry = cur.fetchone()
            if inquiry:
                if (inquiry["payer_id"] != payer_id
                        or not _inquiry_matches(inquiry, product_kind, months, normalized, phone, topup)):
                    raise ValueError("申请编号冲突，请先核实原申请")
                conn.rollback()
                return _payer_profile(cur, inquiry)
            if not config or not config["phone_enabled" if product_kind == "phone" else "enabled"]:
                raise ValueError("话费代充暂未开放" if product_kind == "phone" else "会员余额购买暂未开放")
            from visit_stats import admin_ids
            if not config.get("owner_id") or config["owner_id"] not in admin_ids():
                raise ValueError("人工开通负责人不可用，未扣除余额")
            cur.execute("SELECT balance FROM wallet_accounts WHERE user_id=%s FOR UPDATE", (payer_id,))
            account = cur.fetchone()
            if not account or account["balance"] < price:
                balance = account["balance"] if account else Decimal("0")
                cur.execute(
                    "INSERT INTO member_manual_inquiries(order_id,payer_id,recipient_id,recipient_username,"
                    "months,price_usd,product_kind,phone,topup_usd,fee_usd,balance_usd,owner_id) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (order_id, payer_id, recipient_id, normalized, months, price, product_kind,
                     phone, topup, PHONE_FEE if product_kind == "phone" else Decimal("0"),
                     balance, config["owner_id"]),
                )
                conn.commit()
                return {
                    "order_id": order_id, "payer_id": payer_id, "recipient_id": recipient_id,
                    "recipient_username": normalized, "months": months, "price_usd": price,
                    "status": "contact_pending", "product_kind": product_kind, "phone": phone,
                    "topup_usd": topup, "fee_usd": PHONE_FEE if product_kind == "phone" else Decimal("0"),
                    "balance_usd": balance, "owner_id": config["owner_id"],
                }
            if product_kind == "premium":
                recipient_id, normalized = validate_recipient(bot, username)
            cur.execute(
                "INSERT INTO member_orders(order_id,payer_id,recipient_id,recipient_username,"
                "months,price_usd,gift_stars,status,owner_id,product_kind,phone,topup_usd,fee_usd) "
                "VALUES(%s,%s,%s,%s,%s,%s,0,'manual_pending',%s,%s,%s,%s,%s)",
                (order_id, payer_id, recipient_id, normalized, months, price, config["owner_id"],
                 product_kind, phone, topup, PHONE_FEE if product_kind == "phone" else Decimal("0")))
            cur.execute("UPDATE wallet_accounts SET balance=balance-%s WHERE user_id=%s",
                        (price, payer_id))
            cur.execute(
                "INSERT INTO member_wallet_ledger(order_id,user_id,event,amount) VALUES(%s,%s,'purchase',%s)",
                (order_id, payer_id, -price))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"order_id": order_id, "recipient_username": normalized, "months": months,
            "price_usd": str(price), "status": "manual_pending", "product_kind": product_kind,
            "phone": phone, "topup_usd": topup, "fee_usd": PHONE_FEE if product_kind == "phone" else 0}


def _inquiry_matches(inquiry, product_kind, months, username, phone, topup):
    return (inquiry["product_kind"] == product_kind
            and inquiry["months"] == months
            and inquiry["recipient_username"] == username
            and (product_kind != "phone"
                 or (inquiry["phone"] == phone and inquiry["topup_usd"] == topup)))


def get_manual_inquiry(order_id, payer_id=None):
    validate_order_id(order_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM member_manual_inquiries WHERE order_id=%s", (order_id,))
            row = cur.fetchone()
            if not row or (payer_id is not None and row["payer_id"] != payer_id):
                return None
            return _payer_profile(cur, row)
    finally:
        conn.close()


def _payer_profile(cur, order):
    cur.execute("SELECT username,display_name FROM bot_users WHERE tg_user_id=%s", (order["payer_id"],))
    profile = cur.fetchone()
    return {**order, "payer_username": profile.get("username") if profile else None,
            "payer_name": profile.get("display_name") if profile else None}


def get_order(order_id, payer_id=None):
    validate_order_id(order_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM member_orders WHERE order_id=%s", (order_id,))
            row = cur.fetchone()
            if not row or (payer_id is not None and row["payer_id"] != payer_id):
                return None
            return _payer_profile(cur, row)
    finally:
        conn.close()


def recent_orders(payer_id=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            query = ("SELECT order_id,payer_id,recipient_username,months,price_usd,status,error,created_at,refund_reason,"
                     "product_kind,phone,topup_usd,fee_usd FROM member_orders")
            cur.execute(query + (" WHERE payer_id=%s" if payer_id is not None else "")
                        + " ORDER BY created_at DESC,order_id DESC LIMIT 30",
                        (payer_id,) if payer_id is not None else ())
            rows = cur.fetchall()
        return [{**{key: value for key, value in row.items() if key != "refund_reason"}, **order_public(row),
                 "created_at": str(row["created_at"])} for row in rows]
    finally:
        conn.close()


def recent_ledger(user_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id,reference,event,amount,created_at,product_kind FROM ("
                "SELECT id,CAST(deposit_id AS CHAR) AS reference,'deposit' AS event,amount,created_at,"
                "'deposit' AS product_kind "
                "FROM wallet_ledger WHERE user_id=%s "
                "UNION ALL "
                "SELECT l.id,l.order_id AS reference,l.event,l.amount,l.created_at,"
                "COALESCE(o.product_kind,'premium') AS product_kind "
                "FROM member_wallet_ledger l LEFT JOIN member_orders o ON o.order_id=l.order_id "
                "WHERE l.user_id=%s AND l.event IN ('purchase','refund')"
                ") AS movements ORDER BY created_at DESC,event DESC,id DESC LIMIT 30",
                (user_id, user_id))
            rows = cur.fetchall()
        return [{**row, "reference": str(row["reference"]), "amount": str(row["amount"]),
                 "created_at": str(row["created_at"])} for row in rows]
    finally:
        conn.close()


def set_refund_reason(order_id, actor_id, reason):
    from review_notifications import rejection_reason
    reason = rejection_reason(reason)
    validate_order_id(order_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM member_orders WHERE order_id=%s FOR UPDATE", (order_id,))
            order = cur.fetchone()
            if not order:
                raise ValueError("订单不存在")
            require_order_owner(order, actor_id)
            if order["status"] != "manual_pending":
                raise ValueError("订单已处理，请刷新核对")
            cur.execute("UPDATE member_orders SET refund_reason=%s WHERE order_id=%s", (reason, order_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return reason


def refund_order(order_id, actor_id=None, confirmed_not_delivered=False, reason=None):
    from review_notifications import enqueue, rejection_reason
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
                raise ValueError("必须核实未完成服务，才能对待处理或结果不明的订单退款")
            if actor_id is not None:
                reason = rejection_reason(reason)
            cur.execute(
                "INSERT INTO wallet_accounts(user_id,balance) VALUES(%s,%s) "
                "ON DUPLICATE KEY UPDATE balance=balance+VALUES(balance)",
                (order["payer_id"], order["price_usd"]))
            cur.execute(
                "INSERT INTO member_wallet_ledger(order_id,user_id,event,amount,actor_id) "
                "VALUES(%s,%s,'refund',%s,%s)",
                (order_id, order["payer_id"], order["price_usd"], actor_id))
            cur.execute("UPDATE member_orders SET status='refunded',refund_reason=%s,finished_at=NOW() WHERE order_id=%s", (reason, order_id))
            if actor_id is not None:
                product = "话费" if order.get("product_kind") == "phone" else "会员"
                enqueue(cur, f"order:{order_id}", order["payer_id"],
                        f"{product}订单 {order_id} 未通过人工处理。\n拒绝原因：{reason}\n已全额退回站内余额 ${order['price_usd']} USD（不是链上退款）。")
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
                raise ValueError("只能确认待处理或待核实订单，请先核实实际服务记录")
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
    username = order.get("payer_username")
    name = " ".join((order.get("payer_name") or "").split()) or "名称暂无记录"
    account = f"@{username}（资料记录）" if username else "暂无用户名记录"
    identity = f"付款客户 ID：{order['payer_id']}\n客户账号：{account}\n客户名称：{name}\n"
    if order.get("product_kind") == "phone":
        status = {"manual_pending": "等待昊天人工代充", "delivered": "已代充完成",
                  "refunded": "已全额退回站内余额"}.get(order["status"], order["status"])
        return (
            f"柬埔寨话费订单 {order['order_id']}\n"
            f"{identity}"
            f"充值手机号：{order['phone']}\n"
            f"话费：${order['topup_usd']} USD\n手续费：${order['fee_usd']} USD\n"
            f"已扣合计：${order['price_usd']} USD\n状态：{status}\n"
            "请核实手机号及运营商，实际代充成功后再确认；未代充则全额退回话费和手续费。"
        )
    status = {"manual_pending": "等待昊天手动开通", "delivered": "已开通完成", "refunded": "已退回站内余额"}.get(
        order["status"], order["status"])
    return (
        f"人工会员订单 {order['order_id']}\n"
        f"{identity}"
        f"开通账号：@{order['recipient_username']}\n"
        f"收件人 ID：{order['recipient_id']}\n"
        f"套餐：{order['months']}个月 · 已扣 ${order['price_usd']} USD\n"
        f"状态：{status}\n"
        "请核对用户名及数字 ID，手动开通后再确认完成；无法开通须确认未开通后退余额。"
    )


def manual_inquiry_text(inquiry):
    name = " ".join((inquiry.get("payer_name") or "").split()) or "名称暂无记录"
    username = inquiry.get("payer_username")
    payer = f"@{username}" if username else "暂无用户名"
    details = (
        f"充值手机号：{inquiry['phone']}\n"
        f"话费金额：${inquiry['topup_usd']} USD\n"
        f"手续费：${inquiry['fee_usd']} USD\n"
    ) if inquiry["product_kind"] == "phone" else (
        f"开通账号：@{inquiry['recipient_username']}\n"
        f"套餐时长：{inquiry['months']}个月\n"
    )
    product = "话费代充" if inquiry["product_kind"] == "phone" else "Telegram会员"
    return (
        f"💬 余额不足人工申请 {inquiry['order_id']}\n"
        f"服务：{product}\n"
        f"{details}"
        f"应付合计：${inquiry['price_usd']} USD\n"
        f"当前余额：${inquiry['balance_usd']} USD\n"
        f"客人：{name}（{payer}）\n"
        f"客人 ID：{inquiry['payer_id']}\n\n"
        "此申请未扣余额、不是已付款订单。直接回复本消息，即可通过机器人私聊客人确认付款和办理。"
    )


def notify_manual_inquiry(bot, order_id):
    inquiry = get_manual_inquiry(order_id)
    if not inquiry or inquiry["status"] != "pending_contact":
        return False
    if inquiry["owner_message_id"] is None:
        message = bot.send_message(
            inquiry["owner_id"], manual_inquiry_text(inquiry),
            disable_web_page_preview=True, timeout=10)
        from relay import remember_relay
        remember_relay(inquiry["owner_id"], message.message_id, inquiry["payer_id"])
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE member_manual_inquiries SET owner_message_id=%s WHERE order_id=%s",
                            (message.message_id, order_id))
            conn.commit()
        finally:
            conn.close()
    if inquiry["customer_message_id"] is None:
        customer_message = bot.send_message(
            inquiry["payer_id"],
            f"申请编号：{order_id}\n"
            "已将你的会员/话费申请转交昊天。此申请未扣除账户余额；昊天会通过机器人私聊你确认付款和办理。",
            timeout=10,
        )
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE member_manual_inquiries SET customer_message_id=%s WHERE order_id=%s",
                            (customer_message.message_id, order_id))
            conn.commit()
        finally:
            conn.close()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE member_manual_inquiries SET status='notified' "
                "WHERE order_id=%s AND owner_message_id IS NOT NULL AND customer_message_id IS NOT NULL",
                (order_id,),
            )
        conn.commit()
    finally:
        conn.close()
    return True


def process_manual_inquiries(bot):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_id FROM member_manual_inquiries "
                "WHERE status='pending_contact' ORDER BY created_at LIMIT 50")
            rows = cur.fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            notify_manual_inquiry(bot, row["order_id"])
        except Exception:
            logger.exception(
                "Manual contact request notification failed; retained for retry order=%s",
                row["order_id"],
            )


def manual_keyboard(order_id, decision=None, product_kind="premium"):
    keyboard = types.InlineKeyboardMarkup()
    phone = product_kind == "phone"
    if decision:
        keyboard.add(types.InlineKeyboardButton(
            ("确认已经代充" if phone else "确认已经开通") if decision == "done"
            else ("确认未代充并全额退款" if phone else "确认未开通并退余额"),
            callback_data=f"manual:confirm_{decision}:{order_id}",
        ))
        keyboard.add(types.InlineKeyboardButton("返回核对", callback_data=f"manual:back:{order_id}"))
    else:
        keyboard.add(types.InlineKeyboardButton("代充完成" if phone else "开通完成", callback_data=f"manual:done:{order_id}"))
        keyboard.add(types.InlineKeyboardButton("无法代充，全额退款" if phone else "无法开通，退余额", callback_data=f"manual:refund:{order_id}"))
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
            row = _payer_profile(cur, row)
            message = bot.send_message(
                row["owner_id"], manual_order_text(row),
                reply_markup=manual_keyboard(order_id, product_kind=row.get("product_kind", "premium")),
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
    process_manual_inquiries(bot)


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
