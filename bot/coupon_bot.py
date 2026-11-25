"""Telegram entry points for the invitation reward campaign."""

import logging
from datetime import datetime
from urllib.parse import quote

import pymysql
from telebot import types

import coupon_campaign as campaign
from db import get_conn
from visit_stats import admin_ids

logger = logging.getLogger(__name__)
STATES = {
    "pending": "留存中", "qualified": "已达标", "left": "未满7天退出",
    "review": "待人工核实", "invalid": "未通过核实",
}


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(campaign.LOCAL_ZONE).strftime("%Y-%m-%d %H:%M")


def show(bot, chat_id, user_id):
    try:
        is_member = campaign.participant_status(bot, user_id)
        membership = "✅ 已加入频道" if is_member else "❗尚未加入频道，请先加入"
    except ValueError as exc:
        membership = f"频道成员状态：{exc}"
    data = campaign.mine(user_id)
    lines = ["邀请好友 · 我的代金券",
             membership,
             "① 先加入频道  ② 获取专属链接邀请好友  ③ 在此查询邀请状态",
             "每位好友通过专属链接入频道后连续留存7天，每5人获得一张 $20 代金券。",
             "有效期一个月，会所及外送可用，不含名玺水汇按摩；登记新消费时自动抵用一张。",
             f"已达标待兑换：{data['eligible']} / 5 人 · 邀请记录：{data['invite_total']} 人"]
    if not data["enabled"]:
        lines.append("活动暂未开放。")
    for invite in data["invites"][:5]:
        name = " ".join(invite["display_name"].split()) or "未记录名称"
        line = f"{name} · {STATES.get(invite['state'], '待核实')}"
        if invite["left_at"]:
            line += f" · 退出 {_time(invite['left_at'])}"
        lines.append(line)
    lines.append(f"代金券共 {data['voucher_total']} 张，点击「查看优惠券」了解详情。")
    if data["invite_total"] > 5:
        lines.append("完整邀请记录请在小程序「我的」中查看。")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("① 加入频道", url="https://t.me/haotianyl99988866"))
    if data["enabled"]:
        keyboard.add(types.InlineKeyboardButton("② 获取邀请链接", callback_data="coupon:link"))
    keyboard.add(types.InlineKeyboardButton("③ 查询邀请状态", callback_data="coupon:mine"))
    keyboard.add(types.InlineKeyboardButton("查看优惠券", callback_data="coupon:vouchers"))
    keyboard.add(types.InlineKeyboardButton("我的账户",
        url="https://t.me/haotianzhaopian_bot?startapp=personal"))
    bot.send_message(chat_id, "\n".join(lines), reply_markup=keyboard)


def show_vouchers(bot, chat_id, user_id):
    data = campaign.mine(user_id)
    lines = ["我的优惠券"]
    for voucher in data["vouchers"]:
        status = {"available": "可使用", "redeemed": "已核销", "invalid": "已失效"}.get(voucher["status"], "待核实")
        expires = datetime.fromisoformat(voucher["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        if voucher["status"] == "available" and expires <= campaign._utcnow():
            status = "已过期"
        lines.append(f"$20 · {status} · 有效至 {_time(voucher['expires_at'])} · 券号 {voucher['id']}")
    if not data["vouchers"]:
        lines.append("暂无优惠券")
    if data["voucher_total"] > len(data["vouchers"]):
        lines.append("更多优惠券请在小程序「我的」中查看。")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("返回邀请进度", callback_data="coupon:mine"))
    bot.send_message(chat_id, "\n".join(lines), reply_markup=keyboard)


def _admin_records(owner_id):
    campaign._positive_id(owner_id, "Telegram ID")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT r.id,r.service,r.amount,r.occurred_at,COALESCE(v.name,'来源已下架') AS venue
                FROM crm_consumptions r JOIN crm_customers c ON c.user_id=r.user_id
                LEFT JOIN venues v ON v.id=r.venue_id WHERE c.telegram_user_id=%s AND r.status='valid'
                ORDER BY r.occurred_at DESC,r.id DESC LIMIT 10""", (owner_id,))
            return campaign._public(cur.fetchall())
    finally:
        conn.close()


def command(bot, message):
    user_id, chat_id = message.from_user.id, message.chat.id
    parts = (message.text or "").split()
    name = parts[0].split("@", 1)[0].lower()
    try:
        if name == "/coupons":
            show(bot, chat_id, user_id)
        elif name == "/coupon_admin":
            if user_id not in admin_ids():
                raise ValueError("仅平台管理员可核对优惠券")
            if len(parts) != 2 or not parts[1].isdigit():
                raise ValueError("用法：/coupon_admin 客户Telegram数字ID")
            owner_id = int(parts[1])
            data = campaign.mine(owner_id)
            records = _admin_records(owner_id)
            lines = [f"客户 {owner_id} 优惠券（最近20张）："]
            lines.extend(f"{row['id']} · {row['status']} · 至 {_time(row['expires_at'])}"
                         for row in data["vouchers"])
            if not data["vouchers"]:
                lines.append("暂无优惠券")
            lines.append("最近10笔有效消费：")
            lines.extend(f"{row['id']} · {row['venue']} · {row['service']} · 实付 ${row['amount']}"
                         for row in records)
            if not records:
                lines.append("暂无有效消费")
            lines.append("新消费登记时自动核销；异常补核销：/coupon_redeem 客户ID 券号 消费编号 消费原价")
            bot.send_message(chat_id, "\n".join(lines))
        elif name == "/coupon_redeem":
            if user_id not in admin_ids():
                raise ValueError("仅平台管理员可核销优惠券")
            if len(parts) != 5 or not parts[1].isdigit():
                raise ValueError("用法：/coupon_redeem 客户ID 券号 消费编号 消费原价")
            details = campaign.prepare_redemption(int(parts[1]), parts[2], parts[3], parts[4], user_id)
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(types.InlineKeyboardButton("确认核销", callback_data=f"coupon:confirm:{details['id']}"))
            bot.send_message(chat_id,
                f"请核对：客户 {details['owner_id']} · {details['venue']} · {details['service']}\n"
                f"消费时间 {_time(details['occurred_at'])} · 原价 ${details['original_amount']} · "
                f"实付 ${details['paid']} · 抵用 $20\n券号 {details['voucher_id']}\n"
                f"消费编号 {details['consumption_id']}\n确认按钮10分钟内有效。",
                reply_markup=keyboard)
    except ValueError as exc:
        bot.send_message(chat_id, str(exc))
    except pymysql.MySQLError:
        logger.exception("Coupon command database failure")
        bot.send_message(chat_id, "优惠券服务暂不可用；如核销结果未确认，请先在管理台核对。")


def callback(bot, call):
    if not call.message or call.message.chat.type != "private" or call.message.chat.id != call.from_user.id:
        bot.answer_callback_query(call.id, "请在机器人私聊中操作")
        return
    user_id, chat_id = call.from_user.id, call.message.chat.id
    try:
        if call.data == "coupon:mine":
            bot.answer_callback_query(call.id)
            show(bot, chat_id, user_id)
        elif call.data == "coupon:vouchers":
            bot.answer_callback_query(call.id)
            show_vouchers(bot, chat_id, user_id)
        elif call.data == "coupon:link":
            link = campaign.create_link(bot, user_id)
            keyboard = types.InlineKeyboardMarkup()
            share_text = "邀请你加入频道，一起了解最新资讯。"
            share = "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote(share_text, safe="")
            keyboard.add(types.InlineKeyboardButton("分享给好友", url=share))
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, f"你的专属频道邀请链接：\n{link}\n长按链接复制，或点击下方按钮分享。\n\n"
                             f"分享文案：{share_text}\n{link}\n\n"
                             "好友须通过此链接加入；已在频道的成员不会重复计数，留存7天后方可达标。",
                             reply_markup=keyboard)
        elif call.data.startswith("coupon:confirm:"):
            if user_id not in admin_ids():
                raise ValueError("仅平台管理员可核销优惠券")
            result = campaign.confirm_redemption(call.data.removeprefix("coupon:confirm:"), user_id)
            bot.answer_callback_query(call.id, "已核对" if result["replayed"] else "核销成功")
            bot.send_message(chat_id, "优惠券已核销，请在客户档案核对消费记录。")
    except ValueError as exc:
        bot.answer_callback_query(call.id, str(exc)[:180], show_alert=True)
    except pymysql.MySQLError:
        logger.exception("Coupon callback database failure")
        bot.answer_callback_query(call.id, "操作暂不可用，请在管理台核对后重试", show_alert=True)
