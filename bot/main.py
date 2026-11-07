import os
import re
import logging
from datetime import datetime, timezone

import pymysql
import requests
import telebot
from telebot import apihelper, types, util
from telebot.handler_backends import ContinueHandling
from dotenv import load_dotenv
from db import get_conn
from image_hash import compute_hash, ensure_columns as ensure_image_hash_columns
from config.channels import LISTEN_CHANNELS, TAG_ALIASES
from attendance_parser import (
    PLATE_CODE_VENUE_ID,
    parse_attendance_text,
    parse_plate_attendance_text,
    save_daily_attendance,
)
from technician_photo_ocr import (
    match_pending_technician_photos,
    save_technician_photo,
)
from data_cleanup import start_cleanup_thread
from movies import ensure_tables as ensure_movie_tables, save_movie_message
from music import ensure_tables as ensure_music_tables, handle_channel_message as handle_music_message
from publisher import (
    handle_publisher_command,
    handle_video as publish_video,
    publisher_ids,
    video_media,
)
from footer import ensure_tables as ensure_footer_tables, post_footer, schedule_footer
from admin import (
    BROADCAST_CONTENT_TYPES,
    PANEL_BUTTON,
    ensure_tables as ensure_admin_tables,
    handle_admin_callback,
    handle_pending_message,
    has_pending,
    send_panel,
)
from subscribe import (
    ensure_tables as ensure_sub_tables,
    notify_attendance,
    show_sub_menu,
    sub_menu_keyboard,
    sub_menu_text,
)
from welcome import is_menu_message, send_help, send_menu_link, send_welcome, show_screen
import news_subscription
import post_navigation
import menu_messages
from visit_stats import admin_ids, ensure_table, format_stats
from relay import (
    RELAY_CONTENT_TYPES,
    ensure_tables,
    is_rate_limited,
    relay_admin_reply,
    relay_to_admins,
    should_ack,
    touch_user,
    user_summary,
)
from delivery_age import extract_age, ocr_image_text

load_dotenv()
logger = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

def normalize_tag(tag):
    t = tag.strip()
    return TAG_ALIASES.get(t, t)


def get_venue_config(chat_id):
    chat_id_str = str(chat_id)
    chat_id_str_short = (
        chat_id_str
        if chat_id_str.startswith("-100")
        else f"-100{chat_id_str.replace('-', '')}"
    )
    return LISTEN_CHANNELS.get(chat_id_str) or LISTEN_CHANNELS.get(
        chat_id_str_short
    )


def record_channel_activity(message):
    venue_config = get_venue_config(message.chat.id)
    if not venue_config:
        return

    message_date = datetime.fromtimestamp(message.date, timezone.utc).replace(
        tzinfo=None
    )
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO venue_channel_activity
                (venue_id, channel_id, message_id, message_date)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                message_id = IF(
                    VALUES(message_date) >= message_date,
                    VALUES(message_id),
                    message_id
                ),
                message_date = GREATEST(message_date, VALUES(message_date))
            """,
            (
                venue_config["venue_id"],
                str(message.chat.id),
                message.message_id,
                message_date,
            ),
        )
        conn.commit()
    except pymysql.MySQLError:
        conn.rollback()
        logger.exception(
            "Failed to record channel activity for message %s",
            message.message_id,
        )
        raise
    finally:
        if cur is not None:
            cur.close()
        conn.close()


def parse_caption(caption):
    if not caption:
        return [], ""
    hashtag_pattern = r'#(\S+)'
    tags = re.findall(hashtag_pattern, caption)
    tags = [normalize_tag(t) for t in tags]
    text_without_tags = re.sub(hashtag_pattern, '', caption).strip()
    return tags, text_without_tags

def is_private_command(message):
    return (message.chat.type == "private"
            and bool(message.text)
            and message.text.startswith("/"))


@bot.message_handler(func=is_private_command, content_types=["text"], chat_types=["private"])
def handle_command_history_cleanup(message):
    try:
        menu_messages.clear_before(bot, message)
    except pymysql.MySQLError:
        logger.exception("Could not clear previous bot menu messages")
    return ContinueHandling()


# ===== 客人私聊机器人时发送欢迎信息 =====
@bot.message_handler(commands=['start'], chat_types=['private'])
def handle_start(message):
    parts = (message.text or "").split(maxsplit=1)
    from customer_analytics import bot_event
    payload = parts[1] if len(parts) == 2 else ""
    try:
        bot_event(message.from_user.to_dict(), "started", payload[4:] if payload.startswith("ref_") else None)
    except (ValueError, pymysql.MySQLError):
        logger.exception("Failed to record authenticated bot start")
    touch_user(message.from_user)
    if len(parts) == 2 and (parts[1] == "news" or parts[1].startswith("news_")):
        show_news_menu(message, "" if parts[1] == "news" else parts[1][5:])
        return
    send_welcome(bot, message.chat.id, message.from_user.id)


@bot.message_handler(func=is_menu_message, content_types=["text"], chat_types=["private"])
def handle_fixed_menu(message):
    try:
        touch_user(message.from_user)
        if message.text == "新闻订阅":
            handle_news(message)
        elif message.text == "出勤提醒":
            handle_remind(message)
        elif message.text == "使用指南":
            send_help(bot, message.chat.id)
        else:
            send_menu_link(bot, message.chat.id, message.from_user.id, message.text)
    except ValueError as error:
        bot.send_message(message.chat.id, str(error))
    except pymysql.MySQLError:
        logger.exception("Fixed menu unavailable for %s", message.text)
        bot.send_message(message.chat.id, "功能入口暂不可用，请稍后重试；专属分享请勿使用其他人的链接。")


@bot.my_chat_member_handler()
def handle_bot_membership(event):
    if event.chat.type != "private" or event.chat.id != event.from_user.id:
        return
    status = event.new_chat_member.status
    if status not in {"member", "kicked"}:
        return
    from customer_analytics import bot_event
    try:
        bot_event(event.from_user.to_dict(), "blocked" if status == "kicked" else "reachable")
    except (ValueError, pymysql.MySQLError):
        logger.exception("Failed to record observed bot availability")


def show_news_menu(message, slug=""):
    try:
        return news_subscription.show_menu(bot, message.chat.id, message.from_user.id, slug)
    except ValueError as error:
        menu_messages.send_tracked(bot, message.chat.id, str(error))
    except pymysql.MySQLError:
        logger.exception("News subscription menu unavailable")
        menu_messages.send_tracked(
            bot, message.chat.id, "订阅服务暂不可用，请稍后重试 /news。")


@bot.message_handler(commands=['news'], chat_types=['private'])
def handle_news(message):
    try:
        current = news_subscription.subscription(message.from_user.id)
        if current and current["active"]:
            keyboard = telebot.types.InlineKeyboardMarkup()
            keyboard.add(telebot.types.InlineKeyboardButton("取消新闻订阅", callback_data="news:off"))
            return menu_messages.send_tracked(
                bot, message.chat.id, "你已订阅东南亚大事件，新资讯发布后会及时提醒。",
                reply_markup=keyboard)
        else:
            return show_news_menu(message)
    except pymysql.MySQLError:
        logger.exception("News subscription status unavailable")
        menu_messages.send_tracked(
            bot, message.chat.id, "订阅状态暂不可用，请稍后重试 /news。")


@bot.message_handler(commands=['news_off'], chat_types=['private'])
def handle_news_off(message):
    try:
        news_subscription.unsubscribe(message.from_user.id)
        menu_messages.send_tracked(bot, message.chat.id, "已取消东南亚大事件新闻订阅。")
    except pymysql.MySQLError:
        logger.exception("News unsubscribe failed")
        menu_messages.send_tracked(bot, message.chat.id, "取消失败，请稍后重试 /news_off。")


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("news:"))
def handle_news_callback(call):
    news_subscription.handle_callback(bot, call)


@bot.message_handler(commands=['help'], chat_types=['private'])
def handle_help(message):
    touch_user(message.from_user)
    send_help(bot, message.chat.id)


@bot.message_handler(commands=['ops_review'], chat_types=['private'])
def handle_operations_review(message):
    from operations_review import handle_command
    handle_command(bot, message)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("opsr:"))
def handle_operations_review_callback(call):
    from operations_review import handle_callback
    handle_callback(bot, call)


@bot.message_handler(commands=['wallet'], chat_types=['private'])
def handle_wallet(message):
    from wallet import account, pending, proof_image, review, set_address, set_aba
    from telebot import types
    if message.from_user.id not in admin_ids():
        bot.reply_to(message, "请在小程序底部【我的】查看账户余额和充值记录。")
        return
    parts = (message.text or "").split()
    try:
        if len(parts) == 3 and parts[1] == "address":
            set_address(parts[2])
            bot.reply_to(message, "收款地址已配置，TRC20 余额充值申请已开放。请确认该地址由你控制。")
        elif len(parts) >= 4 and parts[1] == "aba":
            set_aba(parts[2], " ".join(parts[3:]))
            bot.reply_to(message, "ABA 美元收款账户已配置，请核对账号和收款人。")
        elif len(parts) == 3 and parts[1] == "proof":
            import io
            bot.send_photo(message.chat.id, io.BytesIO(proof_image(int(parts[2]))),
                           caption=f"申请 #{parts[2]} 的付款凭证。请独立核实真实到账，不能仅凭截图入账。")
        elif len(parts) == 4 and parts[1] == "approve":
            from wallet import amount_value
            amount = amount_value(parts[3])
            deposit_id = int(parts[2])
            if deposit_id <= 0:
                raise ValueError("申请编号必须为正整数")
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(types.InlineKeyboardButton(
                "已核实真实到账，确认 USD 入账",
                callback_data=f"wallet:approve:{deposit_id}:{amount:f}",
            ))
            bot.reply_to(
                message,
                f"申请 #{deposit_id} 将入账 {amount:f} USD。\n"
                "USDT：独立核对 TRON 主网、官方 USDT 合约、收款地址及真实到账和确认情况。\n"
                "ABA：独立核对银行交易编号、美元币种、收款账户及真实到账金额。\n"
                "不能仅凭截图；请确认金额与交易一致。确认后余额不可通过此命令撤销。",
                reply_markup=keyboard,
            )
        elif len(parts) == 6 and parts[1] == "verify" and parts[5] == "核实入账":
            customer_id = review(int(parts[2]), message.from_user.id, parts[3], reference=parts[4])
            bot.reply_to(message, f"申请 #{parts[2]} 已核实并入账 {parts[3]} USD。")
            try:
                bot.send_message(customer_id, f"充值申请 #{parts[2]} 已核实到账，请刷新账户余额。")
            except Exception:
                logger.exception("Could not notify customer of verified deposit user=%s", customer_id)
        elif len(parts) >= 4 and parts[1] == "reject":
            review(int(parts[2]), message.from_user.id, reason=" ".join(parts[3:]))
            bot.reply_to(message, "申请已拒绝，余额未变动，原因已保存并排队通知客人。")
        elif len(parts) == 1:
            rows = pending()
            wallet = account(message.from_user.id)
            address = wallet["address"] or "未配置，USDT 充值关闭"
            aba = wallet["aba"]
            text = (
                f"TRC20 收款地址：{address}\n\n"
                f"ABA：{aba['account_number'] + ' / ' + aba['account_name'] if aba else '未配置'}\n"
                "配置：/wallet address 收款地址\n"
                "ABA：/wallet aba 账号 收款人名称（仅美元）\n"
                "核款：/wallet approve 申请编号 实际到账金额\n"
                "查看凭证：/wallet proof 申请编号\n"
                "凭证／ABA金额申请核款：/wallet verify 申请编号 实际到账金额 真实交易编号 核实入账\n"
                "拒绝：/wallet reject 申请编号 拒绝原因（必填，最多300字）\n\n待审核（最多8条，处理后查看下一批）：\n"
            )
            for row in rows:
                text += (
                    f"\n#{row['id']} 用户 {row['user_id']} 申报 {row['claimed']} USD · {row['method']}\n"
                    f"收款：{row['address']}\n"
                    + ("ABA 金额申请：请核对私聊及银行真实到账，使用 /wallet verify 核实入账\n"
                       if row['txid'].startswith("manual:") else
                       f"已上传凭证：/wallet proof {row['id']}\n" if row['txid'].startswith("proof:") else f"交易编号：{row['txid']}\n")
                )
            bot.send_message(message.chat.id, text, disable_web_page_preview=True)
        else:
            raise ValueError("格式错误，请发送 /wallet 查看用法")
    except ValueError as exc:
        bot.reply_to(message, str(exc))
    except pymysql.MySQLError:
        logger.exception("充值后台操作失败")
        bot.reply_to(message, "数据库操作失败，请检查申请记录后再操作，勿重复核款。")


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("wallet:"))
def handle_wallet_confirmation(call):
    from wallet import review
    if call.from_user.id not in admin_ids() or call.message.chat.type != "private":
        bot.answer_callback_query(call.id, "仅管理员可操作", show_alert=True)
        return
    try:
        _, action, deposit_id, amount = call.data.split(":")
        if action != "approve":
            raise ValueError("无效操作")
        customer_id = review(int(deposit_id), call.from_user.id, amount)
    except ValueError as exc:
        bot.answer_callback_query(call.id, str(exc), show_alert=True)
        return
    except pymysql.MySQLError:
        logger.exception("充值确认失败")
        bot.answer_callback_query(call.id, "数据库操作失败，请先检查记录", show_alert=True)
        return
    bot.answer_callback_query(call.id, "入账成功")
    bot.send_message(call.message.chat.id, f"申请 #{deposit_id} 已入账 {amount} USD。")
    try:
        bot.send_message(customer_id, f"充值申请 #{deposit_id} 已核实到账，账户增加 {amount} USD，请刷新网站余额。")
    except apihelper.ApiException:
        logger.exception("Could not notify customer of approved deposit user=%s", customer_id)


def send_member_login(chat_id, user):
    from member_accounts import create_login_link
    from telebot import types
    try:
        touch_user(user)
        link = create_login_link(user.id)
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("打开会员网站", url=link))
        bot.send_message(
            chat_id, "请使用外部浏览器打开会员网站。登录链接 10 分钟内有效且只能使用一次，"
            "不要转发给他人；网站登录有效期为 24 小时。", reply_markup=keyboard)
    except ValueError as exc:
        bot.send_message(chat_id, str(exc))
    except Exception:
        logger.exception("Could not issue member browser login")
        bot.send_message(chat_id, "网站登录暂不可用，请稍后重试 /member。")


@bot.message_handler(commands=["member"], chat_types=["private"])
def handle_member_login(message):
    send_member_login(message.chat.id, message.from_user)


@bot.callback_query_handler(func=lambda call: call.data == "member:login")
def handle_member_login_button(call):
    if call.message.chat.type != "private":
        bot.answer_callback_query(call.id, "请私聊机器人发送 /member", show_alert=True)
        return
    bot.answer_callback_query(call.id, "正在生成登录链接")
    send_member_login(call.message.chat.id, call.from_user)


@bot.message_handler(commands=["member_sales"], chat_types=["private"])
def handle_member_sales(message):
    if message.from_user.id not in admin_ids():
        bot.reply_to(message, "仅管理员可使用此命令。")
        return
    from premium_balance import catalog, confirm_delivery, manual_owner, recent_orders, refund_order, set_enabled
    parts = (message.text or "").split()
    try:
        if message.from_user.id != manual_owner():
            raise ValueError("仅人工开通负责人可管理会员订单")
        if len(parts) == 2 and parts[1] in {"enable", "disable"}:
            set_enabled(bot, parts[1] == "enable")
            bot.reply_to(message, "会员余额购买已开放。" if parts[1] == "enable" else "会员余额购买已关闭。")
        elif len(parts) == 2 and parts[1] in {"phone_enable", "phone_disable"}:
            set_enabled(bot, parts[1] == "phone_enable", product_kind="phone")
            bot.reply_to(message, "话费代充已开放。" if parts[1] == "phone_enable" else "话费代充已关闭。")
        elif len(parts) == 3 and parts[1] == "delivered":
            confirm_delivery(parts[2], message.from_user.id)
            bot.reply_to(message, "已确认服务完成；此操作必须以实际开通或代充记录为依据。")
        elif len(parts) >= 5 and parts[1] == "refund" and parts[3] in {"NOT_GIFTED", "NOT_CHARGED"}:
            refund_order(parts[2], message.from_user.id, confirmed_not_delivered=True, reason=" ".join(parts[4:]))
            bot.reply_to(message, "购买金额已退回站内 USD 余额，原因已保存并排队通知客人（不是链上退款）。")
        elif len(parts) == 1 or (len(parts) == 2 and parts[1] == "status"):
            lines = [
                f"网站会员购买：{'开放' if catalog()['enabled'] else '关闭'}",
                f"柬埔寨话费代充：{'开放' if catalog()['phone']['enabled'] else '关闭'} · $5–$100 + $1手续费",
                "人工开通：不调用官方赠送接口，不要求机器人 Stars 库存。",
                "售价：3个月 $15.99 / 6个月 $19.99 / 12个月 $33.99",
                "/member_sales enable|disable",
                "/member_sales phone_enable|phone_disable",
                "/member_sales delivered 订单号（核实已开通或代充）",
                "/member_sales refund 订单号 NOT_GIFTED 拒绝原因（核实未开通）",
                "/member_sales refund 订单号 NOT_CHARGED 拒绝原因（核实未代充，全额退余额）",
            ]
            for order in recent_orders():
                product = (f"话费 ${order['topup_usd']} → {order['phone']}"
                           if order["product_kind"] == "phone"
                           else f"{order['months']}个月 → @{order['recipient_username']}")
                lines.append(f"{order['order_id']} | {order['status']} | ${order['price_usd']} | "
                             f"{product}")
            bot.reply_to(message, "\n".join(lines))
        else:
            raise ValueError("命令格式错误，请发送 /member_sales 查看用法")
    except ValueError as exc:
        bot.reply_to(message, str(exc))
    except Exception:
        logger.exception("Member sales administration failed")
        bot.reply_to(message, "操作异常，请先核实订单和官方交易，勿重复赠送或退款。")


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("manual:"))
def handle_manual_membership(call):
    from premium_balance import (
        confirm_delivery, get_order, manual_keyboard, manual_order_text,
        refund_order, require_order_owner,
    )
    try:
        _, action, order_id = call.data.split(":")
        if action not in {"done", "refund", "back", "confirm_done", "confirm_refund"}:
            raise ValueError("无效的订单操作")
        order = get_order(order_id)
        if not order:
            raise ValueError("订单不存在")
        require_order_owner(order, call.from_user.id)
        if call.message.chat.type != "private" or call.message.chat.id != order["owner_id"]:
            raise ValueError("请在负责人的机器人私聊中操作")
        if order["status"] != "manual_pending":
            raise ValueError("订单已处理或正在核实，不可重复确认或退款")
        phone = order.get("product_kind") == "phone"
        if action == "refund":
            prompt = bot.send_message(call.message.chat.id,
                                      f"订单 {order_id}：请回复拒绝原因（1–300字，客人可见）。发送 /cancel 取消；填写后还需确认未完成服务才能退款。",
                                      reply_markup=types.ForceReply(selective=True))
            bot.register_next_step_handler(prompt, handle_manual_refund_reason, order_id)
            bot.answer_callback_query(call.id, "请填写拒绝原因")
            return
        if action in {"done", "back"}:
            text = manual_order_text(order)
            if action == "done":
                text += ("\n\n请再次确认：上述手机号已实际收到对应话费，完成后不再允许退余额。"
                         if phone else "\n\n请再次确认：已给上述账号开通对应套餐，完成后不再允许退余额。")
            bot.edit_message_text(
                text, chat_id=call.message.chat.id, message_id=call.message.message_id,
                reply_markup=manual_keyboard(order_id, None if action == "back" else action,
                                             order.get("product_kind", "premium")),
            )
            bot.answer_callback_query(call.id, "请核对后确认" if action != "back" else "已返回核对")
            return
        if action == "confirm_done":
            confirm_delivery(order_id, call.from_user.id)
            result = "代充完成" if phone else "开通完成"
        else:
            from review_notifications import rejection_reason
            reason = rejection_reason(order.get("refund_reason"))
            if call.message.text != manual_refund_confirmation_text(order_id, reason):
                raise ValueError("拒绝原因已重新填写，请使用最新的确认消息，或返回订单重新填写")
            refund_order(order_id, call.from_user.id, confirmed_not_delivered=True, reason=reason)
            result = "未代充，话费及手续费已全额退回站内余额" if phone else "未开通，已退回站内余额"
    except ValueError as exc:
        bot.answer_callback_query(call.id, str(exc), show_alert=True)
        return
    except (pymysql.MySQLError, apihelper.ApiException):
        logger.exception("Manual membership action failed; check persisted order")
        bot.answer_callback_query(call.id, "操作未确认，请先查询订单状态，勿重复开通或退款", show_alert=True)
        return
    # The financial transaction is complete; notification failures must not retry it.
    try:
        bot.answer_callback_query(call.id, result)
        bot.edit_message_text(
            manual_order_text({**order, "status": result}),
            chat_id=call.message.chat.id, message_id=call.message.message_id,
        )
    except apihelper.ApiException:
        logger.exception("Manual membership result message failed order=%s", order_id)
    if action == "confirm_refund":
        return
    try:
        product = "话费" if phone else "会员"
        bot.send_message(order["payer_id"], f"{product}订单 {order_id}：{result}。请刷新我的订单及余额。")
    except apihelper.ApiException:
        logger.exception("Manual membership customer notification failed order=%s", order_id)


def manual_refund_confirmation_text(order_id, reason):
    return (f"订单 {order_id}\n拒绝原因：{reason}\n"
            "请再次核实确实未完成服务，确认后全额退回站内余额并通知客人。")


def handle_manual_refund_reason(message, order_id):
    from premium_balance import get_order, manual_keyboard, require_order_owner, set_refund_reason
    if (message.text or "").strip() == "/cancel":
        bot.reply_to(message, "已取消填写，订单和余额未变动。")
        return
    try:
        order = get_order(order_id)
        if not order:
            raise ValueError("订单不存在")
        require_order_owner(order, message.from_user.id)
        if message.chat.type != "private" or message.chat.id != order["owner_id"]:
            raise ValueError("请在负责人私聊中操作")
        reason = set_refund_reason(order_id, message.from_user.id, message.text)
        bot.send_message(message.chat.id,
                         manual_refund_confirmation_text(order_id, reason),
                         reply_markup=manual_keyboard(order_id, "refund", order.get("product_kind", "premium")),
                         parse_mode=None)
    except ValueError as exc:
        bot.reply_to(message, f"{exc}。请回到订单点击退款重新填写，余额未变动。")
    except (pymysql.MySQLError, apihelper.ApiException):
        logger.exception("Manual refund reason could not be confirmed order=%s", order_id)
        bot.reply_to(message, "原因保存或确认消息失败，请查询订单后重新操作，勿重复退款。")


@bot.pre_checkout_query_handler(func=lambda query: True)
def handle_stars_funding_checkout(query):
    from admin_stars import approve_checkout
    try:
        approve_checkout(query.from_user.id, query.invoice_payload, query.currency, query.total_amount)
    except ValueError as exc:
        bot.answer_pre_checkout_query(query.id, False, error_message=str(exc))
        return
    except pymysql.MySQLError:
        logger.exception("Stars funding checkout database unavailable")
        bot.answer_pre_checkout_query(query.id, False, error_message="付款核对暂不可用，请稍后刷新订单，勿重复付款")
        return
    bot.answer_pre_checkout_query(query.id, True)


@bot.message_handler(content_types=["successful_payment"])
def handle_stars_funding_payment(message):
    from admin_stars import record_payment
    try:
        result = record_payment(message.from_user.id, message.successful_payment)
    except (ValueError, pymysql.MySQLError):
        logger.exception("Stars funding payment requires reconciliation user=%s", message.from_user.id)
        bot.reply_to(message, "已收到 Telegram 付款通知，但补充记录核对失败。请核实机器人实际 Stars 余额与官方交易，勿再次付款。")
        return
    if not result["duplicate"]:
        bot.reply_to(message, f"已收到 {result['stars']} Stars，补充订单 {result['order_id']} 已记录。请在后台刷新实际库存；客户美元余额不变，会员销售不会自动开启。")


@bot.message_handler(commands=["paysupport"], chat_types=["private"])
def handle_pay_support(message):
    bot.reply_to(
        message,
        "Premium 付款、赠送或机器人 Stars 补充遇到问题，请联系管理员并提供订单编号及官方付款记录。"
        "请勿发送密码、验证码或两步验证密码。")


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("menu:"))
def handle_menu_callback(call):
    touch_user(call.from_user)
    show_screen(bot, call)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("sub:"))
def handle_sub_callback(call):
    touch_user(call.from_user)
    show_sub_menu(bot, call)


@bot.message_handler(commands=['remind'], chat_types=['private'])
def handle_remind(message):
    touch_user(message.from_user)
    return menu_messages.send_tracked(
        bot,
        message.chat.id,
        sub_menu_text(),
        parse_mode="HTML",
        reply_markup=sub_menu_keyboard(message.from_user.id),
    )


# ===== 管理后台(仅管理员) =====
def _is_admin_pending(message):
    return message.from_user.id in admin_ids() and has_pending(message.from_user.id) and not (
        message.text and message.text.startswith("/")
    )


@bot.message_handler(commands=['admin'], chat_types=['private'])
def handle_admin(message):
    if message.from_user.id in admin_ids():
        send_panel(bot, message.chat.id, message.from_user.id, message.message_id)


@bot.message_handler(
    func=lambda m: m.text == PANEL_BUTTON and m.from_user.id in admin_ids(),
    chat_types=['private'],
)
def handle_admin_button(message):
    send_panel(bot, message.chat.id, message.from_user.id, message.message_id)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("adm:"))
def handle_admin_cb(call):
    if call.from_user.id in admin_ids():
        handle_admin_callback(bot, call)


@bot.message_handler(
    func=_is_admin_pending, chat_types=['private'], content_types=BROADCAST_CONTENT_TYPES
)
def handle_admin_pending(message):
    handle_pending_message(bot, message)


# ===== 代发:管理员私聊发视频,整理说明后发到观影频道 =====
def _is_admin_video(message):
    return (
        (message.from_user.id in admin_ids() or message.from_user.id in publisher_ids())
        and message.reply_to_message is None
        and not has_pending(message.from_user.id)
        and video_media(message) is not None
    )


@bot.message_handler(commands=['publisher'], chat_types=['private'])
def handle_publisher(message):
    if message.from_user.id in admin_ids():
        handle_publisher_command(bot, message)


@bot.message_handler(
    func=_is_admin_video, chat_types=['private'], content_types=['video', 'document']
)
def handle_admin_video(message):
    publish_video(bot, message)


# ===== 客人留言转发给管理员,管理员回复后发回客人 =====
@bot.message_handler(commands=['users'], chat_types=['private'])
def handle_users(message):
    if message.from_user.id in admin_ids():
        bot.send_message(message.chat.id, user_summary())


def _is_admin_reply(message):
    return message.from_user.id in admin_ids() and message.reply_to_message is not None


@bot.message_handler(
    func=_is_admin_reply, chat_types=['private'], content_types=RELAY_CONTENT_TYPES
)
def handle_admin_reply(message):
    relay_admin_reply(bot, message)


def _is_customer_message(message):
    if message.text and message.text.startswith("/"):
        return False
    return message.from_user.id not in admin_ids()


@bot.message_handler(
    func=_is_customer_message, chat_types=['private'], content_types=RELAY_CONTENT_TYPES
)
def handle_customer_message(message):
    if touch_user(message.from_user, count_message=True):
        return
    if is_rate_limited(message.from_user.id):
        return
    if relay_to_admins(bot, message):
        if should_ack(message.from_user.id):
            bot.send_message(
                message.chat.id,
                "✅ 已收到您的留言，昊天看到后会尽快回复您。\n"
                "也可以点 /start 查看实时出勤和活动。",
            )
    else:
        bot.send_message(
            message.chat.id,
            "暂时无法转达，请直接联系 @haotian99988866。",
        )


# ===== 访问统计:仅管理员可查看 =====
@bot.message_handler(commands=['myid'], chat_types=['private'])
def handle_myid(message):
    bot.send_message(message.chat.id, f"你的 Telegram ID：{message.from_user.id}")


@bot.message_handler(commands=['stats'], chat_types=['private'])
def handle_stats(message):
    admins = admin_ids()
    if message.from_user.id not in admins:
        if not admins:
            bot.send_message(
                message.chat.id,
                f"尚未设置管理员。你的 ID 是 {message.from_user.id}，"
                "请把它填到 bot/.env 的 ADMIN_USER_IDS 后重启机器人。",
            )
        return
    bot.send_message(message.chat.id, format_stats())


# ===== 处理照片消息（更新封面） =====
@bot.message_handler(func=post_navigation.accepts, content_types=["text", "photo", "video"],
                     chat_types=["group", "supergroup"])
@bot.channel_post_handler(func=post_navigation.accepts, content_types=["text", "photo", "video"])
def handle_post_navigation(message):
    try:
        post_navigation.enqueue(message)
    except pymysql.MySQLError:
        logger.exception("Cannot queue navigation for chat %s message %s",
                         message.chat.id, message.message_id)


@bot.message_handler(content_types=['photo'])
@bot.channel_post_handler(content_types=['photo'])
def handle_photo_message(message):
    record_channel_activity(message)
    print(f"\n=== 收到照片消息 ===")
    print(f"来源类型: {'频道' if message.content_type == 'channel_post' else '普通消息'}")
    print(f"原始频道ID: {message.chat.id}")
    print(f"消息文字: {message.caption}")

    chat_id = message.chat.id
    chat_id_str = str(chat_id)
    venue_config = get_venue_config(chat_id)
    if not venue_config:
        print(f"❌ 频道ID不在监听列表中！")
        return

    save_delivery_media(message)
    if get_venue_config(message.chat.id):
        schedule_footer(bot, message.chat.id)
    if venue_config["venue_id"] in DELIVERY_VENUE_IDS:
        return

    primary_tag = venue_config["primary_tag"]
    caption = message.caption or ""
    tags, _ = parse_caption(caption)

    print(f"解析标签: {tags}")
    print(f"需要匹配: [{primary_tag}]")

    file_id = message.photo[-1].file_id
    chat_numeric = chat_id_str.replace('-100', '').replace('-', '')
    photo_link = f"https://t.me/c/{chat_numeric}/{message.message_id}"

    if primary_tag in tags:
        conn = get_conn()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO venue_photos
                    (venue_id, file_id, photo_link, caption, tags, channel_id, msg_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    venue_config["venue_id"],
                    file_id,
                    photo_link,
                    caption,
                    ",".join(tags),
                    chat_id_str,
                    message.message_id,
                ),
            )
            cur.execute(
                "UPDATE venues SET cover_image = %s WHERE id = %s",
                (photo_link, venue_config["venue_id"]),
            )
            conn.commit()
        except pymysql.MySQLError:
            conn.rollback()
            logger.exception(
                "Failed to update venue cover for message %s", message.message_id
            )
            raise
        finally:
            if cur is not None:
                cur.close()
            conn.close()

        try:
            confirmation = bot.send_message(
                message.chat.id,
                f"✅ 已更新【{venue_config['venue_name']}】封面\n链接：{photo_link}",
            )
            record_channel_activity(confirmation)
        except telebot.apihelper.ApiTelegramException:
            logger.error(
                "Failed to confirm venue cover update for message %s",
                message.message_id,
            )

        return

    try:
        telegram_file = bot.get_file(file_id)
        image_bytes = bot.download_file(telegram_file.file_path)
    except (
        telebot.apihelper.ApiTelegramException,
        requests.RequestException,
    ) as exc:
        logger.error(
            "Failed to download technician photo %s (%s)",
            message.message_id,
            type(exc).__name__,
        )
        raise

    try:
        result = save_technician_photo(
            venue_config["venue_id"],
            chat_id_str,
            message.message_id,
            file_id,
            image_bytes,
            datetime.fromtimestamp(message.date).date(),
            published_at=datetime.fromtimestamp(message.date),
        )
    except (pymysql.MySQLError, ValueError):
        logger.exception("Failed to process technician photo %s", message.message_id)
        raise


    if result["status"] == "matched":
        print(
            f"✅ OCR 匹配成功：{venue_config['venue_name']} "
            f"#{result['technician_code']}，照片记录 {result['id']}"
        )
    else:
        print(
            f"⚠️ OCR 待人工核对：{venue_config['venue_name']} "
            f"照片记录 {result['id']}，候选工号："
            f"{','.join(result['candidate_codes']) or '无'}"
        )

# ===== 处理纯文字出勤公告（新增） =====
@bot.message_handler(content_types=['text'])
@bot.channel_post_handler(content_types=['text'])
def handle_attendance_text(message):
    record_channel_activity(message)
    save_ktv_text(message)
    if save_delivery_text(message):
        if message.text and get_venue_config(message.chat.id):
            schedule_footer(bot, message.chat.id)
        return
    text = message.text or message.caption or ""

    # 只处理包含出勤关键词的消息
    if not any(keyword in text for keyword in ['出勤', '按摩部', '技师', '白班']):
        if get_venue_config(message.chat.id):
            schedule_footer(bot, message.chat.id)
        return

    print(f"\n=== 收到出勤公告 ===")
    print(f"内容: {text[:100]}...")

    chat_id = message.chat.id
    venue_config = get_venue_config(chat_id)

    if not venue_config:
        print(f"❌ 未匹配到会所")
        return

    # 解析并保存出勤
    ok, msg = save_daily_attendance(venue_config["venue_id"], text)
    print(f"出勤同步结果: {msg}")
    if ok:
        attendance = (
            parse_plate_attendance_text(text)
            if venue_config["venue_id"] == PLATE_CODE_VENUE_ID
            else parse_attendance_text(text)
        )
        photo_date = datetime.fromtimestamp(message.date).date()
        matched_count = match_pending_technician_photos(
            venue_config["venue_id"],
            photo_date,
            attendance["duty_date"],
            attendance["codes"],
        )
        if matched_count:
            print(f"✅ 出勤名单已补匹配 {matched_count} 张先到照片")
        if notify_attendance(
            bot,
            venue_config["venue_id"],
            attendance["duty_date"],
            attendance["duty_shift"],
            len(attendance["codes"]),
        ):
            print("🔔 已向订阅用户推送出勤提醒")

    # 回复确认
    confirmation = bot.send_message(message.chat.id, msg)
    record_channel_activity(confirmation)
    if ok:
        post_footer(bot, message.chat.id)


@bot.channel_post_handler(
    content_types=list(
        dict.fromkeys(util.content_type_media + util.content_type_service)
    )
)
def handle_other_channel_post(message):
    if handle_music_message(bot, message, admin_ids()):
        return
    record_channel_activity(message)
    if save_movie_message(message):
        print("🎬 观影视频已入库")
        return
    save_ktv_video(message)
    save_delivery_media(message)
    venue_config = get_venue_config(message.chat.id)
    if venue_config and (message.photo or message.video):
        schedule_footer(bot, message.chat.id)


DELIVERY_VENUE_IDS = {7, 8, 9, 10}


@bot.edited_channel_post_handler(content_types=['video', 'document', 'audio'])
def handle_edited_movie_post(message):
    if handle_music_message(bot, message, admin_ids()):
        return
    if save_movie_message(message):
        print("🎬 观影视频已更新")


def save_delivery_media(message):
    venue_config = get_venue_config(message.chat.id)
    if not venue_config or venue_config["venue_id"] not in DELIVERY_VENUE_IDS:
        return
    if message.photo:
        media_type, file_id = "photo", message.photo[-1].file_id
    elif message.video:
        media_type, file_id = "video", message.video.file_id
    else:
        return
    ocr_text, age, image_hash = None, extract_age(message.caption), None
    if media_type == "photo":
        try:
            image_bytes = bot.download_file(bot.get_file(file_id).file_path)
            image_hash = compute_hash(image_bytes)
            ocr_text = ocr_image_text(image_bytes)
            age = extract_age(message.caption, ocr_text)
        except Exception as exc:
            print(f"⚠️ 外送照片识别失败: {type(exc).__name__}")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            from delivery_staff import archive_build_mode
            archive_only = archive_build_mode(cur)
            cur.execute(
                """
                INSERT INTO delivery_media
                    (venue_id, channel_id, message_id, media_group_id,
                     media_type, file_id, caption, ocr_text, age, image_hash, archive_only)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    file_id = VALUES(file_id),
                    media_group_id = VALUES(media_group_id),
                    caption = VALUES(caption),
                    ocr_text = VALUES(ocr_text),
                    age = VALUES(age),
                    image_hash = VALUES(image_hash)
                """,
                (
                    venue_config["venue_id"],
                    str(message.chat.id),
                    message.message_id,
                    getattr(message, "media_group_id", None),
                    media_type,
                    file_id,
                    message.caption or "",
                    ocr_text,
                    age,
                    image_hash,
                    archive_only,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        from delivery_staff import register_media
        register_media(
            venue_config["venue_id"],
            str(message.chat.id),
            message.message_id,
        )
    except (pymysql.MySQLError, ValueError):
        logger.exception(
            "Failed to attach delivery media to a personnel profile message_id=%s",
            message.message_id,
        )


def save_delivery_text(message):
    """外送频道的文字消息存成 text 记录，用来给前面的图片/视频判断类别；返回 True 表示已按外送处理。"""
    venue_config = get_venue_config(message.chat.id)
    if not venue_config or venue_config["venue_id"] not in DELIVERY_VENUE_IDS:
        return False
    if not message.text:
        return True
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            from delivery_staff import archive_build_mode
            archive_only = archive_build_mode(cur)
            cur.execute(
                """
                INSERT INTO delivery_media
                    (venue_id, channel_id, message_id, media_type, file_id, caption, archive_only)
                VALUES (%s, %s, %s, 'text', '', %s, %s)
                ON DUPLICATE KEY UPDATE caption = VALUES(caption)
                """,
                (
                    venue_config["venue_id"],
                    str(message.chat.id),
                    message.message_id,
                    message.text,
                    archive_only,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return True


KTV_VENUE_ID = 3
ROOM_PATTERNS = (
    re.compile(r"(?:房间|包房|包厢|房号)\s*[:：#\-]?\s*([A-Za-z]?\d{2,5}[A-Za-z]?)"),
    re.compile(r"^\s*([A-Za-z]?\d{2,5}[A-Za-z]?)\s*$"),
    re.compile(r"(\d{3,4})\s*$"),
)


def extract_room_number(caption):
    for pattern in ROOM_PATTERNS:
        match = pattern.search(caption or "")
        if match:
            return match.group(1).upper()
    return None


def save_ktv_text(message):
    venue_config = get_venue_config(message.chat.id)
    if not venue_config or venue_config["venue_id"] != KTV_VENUE_ID or not message.text:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ktv_channel_texts (venue_id, channel_id, message_id, content)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE content = VALUES(content)
                """,
                (KTV_VENUE_ID, str(message.chat.id), message.message_id, message.text),
            )
        conn.commit()
    finally:
        conn.close()


def save_ktv_video(message):
    venue_config = get_venue_config(message.chat.id)
    if not venue_config or venue_config["venue_id"] != KTV_VENUE_ID or not message.video:
        return
    caption = message.caption or ""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ktv_room_videos
                    (venue_id, channel_id, message_id, file_id, room_number, caption)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    file_id = VALUES(file_id),
                    room_number = VALUES(room_number),
                    caption = VALUES(caption)
                """,
                (
                    KTV_VENUE_ID,
                    str(message.chat.id),
                    message.message_id,
                    message.video.file_id,
                    extract_room_number(caption),
                    caption,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    print(f"KTV视频已记录: 房间 {extract_room_number(caption) or '未识别'}")


if __name__ == "__main__":
    from wallet import ensure_tables as ensure_wallet_tables
    ensure_wallet_tables()
    from member_accounts import ensure_tables as ensure_member_account_tables
    from premium_balance import ensure_tables as ensure_member_sales_tables, start_worker as start_member_worker
    ensure_member_account_tables()
    ensure_member_sales_tables()
    from admin_stars import ensure_tables as ensure_admin_stars_tables
    ensure_admin_stars_tables()
    start_member_worker(bot)
    print("🤖 机器人启动 — 监听会所频道模式")
    print(f"📋 已监听 {len(LISTEN_CHANNELS)} 个频道")
    ensure_table()
    from customer_analytics import ensure_tables as ensure_analytics_tables
    ensure_analytics_tables()
    ensure_tables()
    ensure_sub_tables()
    ensure_admin_tables()
    ensure_footer_tables()
    ensure_movie_tables()
    ensure_music_tables()
    ensure_image_hash_columns()
    from delivery_staff import ensure_tables as ensure_delivery_staff_tables
    ensure_delivery_staff_tables()
    from staff_archive import ensure_tables as ensure_staff_tables, start_worker as start_staff_worker
    ensure_staff_tables()
    start_staff_worker()
    from staff_management import ensure_tables as ensure_staff_management_tables, start_worker as start_staff_management_worker
    ensure_staff_management_tables()
    from staff_customer_duty import ensure_tables as ensure_customer_duty_tables
    ensure_customer_duty_tables()
    ensure_customer_duty_tables(delivery=True)
    start_staff_management_worker(bot)
    from announcements import ensure_tables as ensure_announcement_tables
    from tenants import ensure_tables as ensure_tenant_tables
    ensure_tenant_tables()
    ensure_announcement_tables()
    news_subscription.ensure_tables()
    news_subscription.start_worker(bot)
    post_navigation.start_worker(bot)
    menu_messages.ensure_tables()
    from subscription_center import start_worker as start_subscription_center
    start_subscription_center(bot)
    from customer_records import ensure_tables as ensure_customer_record_tables, start_worker as start_customer_record_worker
    ensure_customer_record_tables()
    start_customer_record_worker(bot)
    logger.warning("News subscription worker ready")
    from operations_review import start_worker as start_operations_review
    start_operations_review(bot)
    from review_notifications import start_worker as start_review_notifications
    start_review_notifications(bot)
    start_cleanup_thread()
    bot.infinity_polling(allowed_updates=[])