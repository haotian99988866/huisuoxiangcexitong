"""Telegram entry points for the invitation reward campaign."""

import logging
from datetime import datetime, timedelta
from math import ceil
from urllib.parse import quote

import pymysql
from telebot import types

import coupon_campaign as campaign
from db import get_conn
from menu_messages import send_tracked
from visit_stats import admin_ids
from welcome import CONTACT_URL

logger = logging.getLogger(__name__)
STATES = {
    "pending": "留存中", "qualified": "已达标", "left": "未满7天退出",
    "review": "待人工核实", "invalid": "未通过核实",
}


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(campaign.LOCAL_ZONE).strftime("%Y-%m-%d %H:%M")


def _participated(data):
    return bool((data["link"] and data["link"]["status"] in {"ready", "creating", "uncertain"})
                or data["invite_total"] or data["voucher_total"])


def _progress(data):
    eligible, retaining = data["eligible"], data["retaining"]
    needed = max(0, 5 - eligible)
    additional = max(0, needed - retaining)
    lines = [f"已达标：{eligible}人  ·  留存中：{retaining}人",
             f"还需邀请：{additional}人  ·  还需达标：{needed}人",
             "留存好友退出或未通过核验，可能需再邀请。"]
    if not needed:
        lines.append("已满足本轮人数，等待发券。" if data["enabled"] else "已满足本轮人数，活动开放后发券。")
    elif not data["estimated_at"]:
        lines.append("完成时间待定，以核验结果为准。")
    else:
        estimated = datetime.fromisoformat(data["estimated_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        minutes = max(0, ceil((estimated - campaign._utcnow()).total_seconds() / 60))
        if not minutes:
            lines.append("留存时间已到，等待系统核验。")
        else:
            days, remainder = divmod(minutes, 24 * 60)
            hours, minutes = divmod(remainder, 60)
            lines.append(f"预计还需 {days}天{hours}小时{minutes}分，最早 {_time(data['estimated_at'])} 达标。")
    return lines


def show(bot, chat_id, user_id, page=1, data=None, tracked=False):
    if type(page) is not int or page < 1 or page > 40000:
        raise ValueError("邀请记录页码无效")
    data = data if data is not None else campaign.mine(user_id, (page - 1) // 4 + 1)
    participated = _participated(data)
    is_member = None
    lines = ["🎁 邀请好友 · 赢 $20"]
    if not participated and data["enabled"]:
        try:
            is_member = campaign.participant_status(bot, user_id)
            lines.append("✅ 已加入频道，可以获取专属邀请链接。" if is_member else "❗请先加入频道，再点击「我已加入，继续」。")
        except ValueError as exc:
            lines.append(f"频道成员状态：{exc}")
    lines.extend(["", "【活动规则】",
                  "好友通过专属链接入频道，留存7天并通过核验；每5人得 $20 券。",
                  "有效期2个月｜会所、外送可用（不含名玺水汇按摩）",
                  "符合条件的新消费自动抵扣，每次限1张，不可叠加。"])
    if not data["enabled"]:
        lines.append("活动暂未开放。")
    lines.extend(["", "【本轮进度】", *_progress(data)])
    start = ((page - 1) % 4) * 5
    if data["invites"]:
        lines.extend(["", "【邀请记录】"])
    for invite in data["invites"][start:start + 5]:
        name = (" ".join(invite["display_name"].split()) or
                (f"@{invite['username']}" if invite["username"] else "未记录名称"))[:64]
        status = STATES.get(invite["state"], "待核实")
        if invite["state"] == "qualified" and invite["voucher_id"]:
            status = "已计入代金券"
        line = f"{name} · {status}"
        if invite["state"] == "pending":
            joined = datetime.fromisoformat(invite["joined_at"].replace("Z", "+00:00")).replace(tzinfo=None)
            due = (joined + timedelta(days=7)).isoformat() + "Z"
            line += f" · 预计 {_time(due)} 可核验"
        if invite["left_at"]:
            line += f" · 退出 {_time(invite['left_at'])}"
        lines.append(line)
    lines.extend(["", "好友状态以已记录的频道事件及核验为准。"])
    if not data["invite_total"]:
        lines.append("目前尚无通过专属链接加入的好友。")
    lines.append(f"代金券：{data['voucher_total']}张")
    buttons = []
    if data["enabled"] and not participated and is_member is not True:
        buttons.append(types.InlineKeyboardButton("加入频道", url="https://t.me/haotianyl99988866"))
    if data["enabled"] and (participated or is_member is True):
        buttons.append(types.InlineKeyboardButton("获取邀请链接", callback_data="coupon:link"))
    refresh = "刷新邀请状态" if participated else (
        "我已加入，继续" if is_member is False else "重新核验频道")
    buttons.extend([types.InlineKeyboardButton(refresh, callback_data="coupon:mine"),
                    types.InlineKeyboardButton("查看优惠券", callback_data="coupon:vouchers"),
                    types.InlineKeyboardButton("联系昊天", url=CONTACT_URL)])
    if data["invite_total"] > 5:
        if page > 1:
            buttons.append(types.InlineKeyboardButton("上一页", callback_data=f"coupon:page:{page - 1}"))
        if page * 5 < data["invite_total"]:
            buttons.append(types.InlineKeyboardButton("下一页", callback_data=f"coupon:page:{page + 1}"))
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(*buttons)
    if tracked:
        return send_tracked(bot, chat_id, "\n".join(lines), reply_markup=keyboard)
    return bot.send_message(chat_id, "\n".join(lines), reply_markup=keyboard)


def show_start(bot, chat_id, user_id):
    data = campaign.mine(user_id)
    if not data["enabled"] and not _participated(data):
        return False
    show(bot, chat_id, user_id, data=data, tracked=True)
    return True


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
    answered = False
    try:
        if call.data == "coupon:mine":
            bot.answer_callback_query(call.id)
            answered = True
            show(bot, chat_id, user_id)
        elif call.data.startswith("coupon:page:"):
            page = call.data.removeprefix("coupon:page:")
            if not page.isascii() or not page.isdigit():
                raise ValueError("邀请记录页码无效")
            page = int(page)
            if not 1 <= page <= 40000:
                raise ValueError("邀请记录页码无效")
            bot.answer_callback_query(call.id)
            answered = True
            show(bot, chat_id, user_id, page=page)
        elif call.data == "coupon:vouchers":
            bot.answer_callback_query(call.id)
            answered = True
            show_vouchers(bot, chat_id, user_id)
        elif call.data == "coupon:link":
            link = campaign.create_link(bot, user_id)
            keyboard = types.InlineKeyboardMarkup(row_width=2)
            share_text = "邀请你加入频道，一起了解最新资讯。"
            share = "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote(share_text, safe="")
            keyboard.add(types.InlineKeyboardButton("分享给好友", url=share),
                         types.InlineKeyboardButton("返回邀请进度", callback_data="coupon:mine"))
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
        if answered:
            bot.send_message(chat_id, str(exc))
        else:
            bot.answer_callback_query(call.id, str(exc)[:180], show_alert=True)
    except pymysql.MySQLError:
        logger.exception("Coupon callback database failure")
        if answered:
            bot.send_message(chat_id, "操作暂不可用，请稍后重试。")
        else:
            bot.answer_callback_query(call.id, "操作暂不可用，请在管理台核对后重试", show_alert=True)
