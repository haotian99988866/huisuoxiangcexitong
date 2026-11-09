import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import telebot
from dotenv import load_dotenv
from telebot import types

import finance
from visit_stats import admin_ids

load_dotenv(Path(__file__).with_name(".env"))
logger = logging.getLogger(__name__)
BOT_NAME = "阿兄的账房"
LOCAL_TZ = timezone(timedelta(hours=7))
FINANCE_MINIAPP_URL = "https://app.haotianzhushou.com/personal-finance"
KIND_LABELS = {
    "income": "收入",
    "expense": "支出",
    "borrow": "借入",
    "lend": "借出",
    "debt_repay": "偿还借款",
    "debt_collect": "收回借款",
    "balance_add": "补充账户金额",
    "transfer": "账户转账",
}
MENU_LABELS = {
    "支出": ("entry", "expense"),
    "收入": ("entry", "income"),
    "转账": ("entry", "transfer"),
    "今日": ("today", None),
    "本月": ("month", None),
    "分类分析": ("analysis", None),
    "总览": ("overview", None),
}
sessions = {}


def _today():
    return datetime.now(LOCAL_TZ).date()


def _money(value):
    return f"${Decimal(str(value or 0)):,.2f}"


def _dashboard(owner_id, month=None):
    return finance.dashboard({"month": month or _today().strftime("%Y-%m")}, owner_id)


def _previous_month(month):
    year, number = map(int, month.split("-"))
    previous = datetime(year, number, 1) - timedelta(days=1)
    return previous.strftime("%Y-%m")


def format_daily_report(summary):
    day = datetime.strptime(summary["day"], "%Y-%m-%d").strftime("%m月%d日")
    lines = [
        f"{BOT_NAME} · {day}",
        f"收入 {_money(summary['income'])}（其中提成 {_money(summary['commission'])}）",
        f"支出 {_money(summary['expense'])}",
        f"当日结余 {_money(summary['net'])}",
        f"账户余额 {_money(summary['account_balance'])}",
        f"个人净资产 {_money(summary['net_worth'])}",
    ]
    if summary["categories"]:
        lines.append("支出较多的分类：")
        lines.extend(f"· {row['name'] or '未分类'} {_money(row['amount'])}"
                     for row in summary["categories"])
    else:
        lines.append("今天暂无支出记录，继续保持清晰的账目。")
    return "\n".join(lines)


def format_monthly_report(data, *, analysis=False, previous=None):
    summary = data["summary"]
    month = data["month"].replace("-", "年") + "月"
    budget_rows = [row for row in data["budgets"] if row["kind"] == "expense"]
    budget = sum((Decimal(str(row["budget"])) for row in budget_rows), Decimal(0))
    budget_spent = sum((Decimal(str(row["spent"])) for row in budget_rows), Decimal(0))
    expenses = sorted(
        (row for row in budget_rows if Decimal(str(row["spent"])) > 0),
        key=lambda row: Decimal(str(row["spent"])),
        reverse=True,
    )
    lines = [
        f"{BOT_NAME} · {month}账本",
        f"收入 {_money(summary['income'])}（提成 {_money(summary['commission'])}）",
        f"支出 {_money(summary['expense'])}",
        f"收支结余 {_money(summary['net'])}",
        f"账户余额 {_money(summary['account_balance'])}",
        f"待收 {_money(summary['receivable'])} · 待还 {_money(summary['payable'])}",
        f"个人净资产 {_money(summary['net_worth'])}",
        f"支出预算 {_money(budget_spent)} / {_money(budget)}",
    ]
    if expenses:
        lines.append("支出分类排行：")
        lines.extend(f"{index}. {row['name']} {_money(row['spent'])}"
                     for index, row in enumerate(expenses[:5], start=1))
    else:
        lines.append("本月还没有支出分类记录。")
    if analysis:
        if previous:
            previous_income = Decimal(str(previous["summary"]["income"]))
            previous_expense = Decimal(str(previous["summary"]["expense"]))
            income_change = Decimal(str(summary["income"])) - previous_income
            expense_change = Decimal(str(summary["expense"])) - previous_expense
            income_direction = "增加" if income_change > 0 else "减少"
            expense_direction = "增加" if expense_change > 0 else "减少"
            lines.append(
                f"较上月收入{income_direction} {_money(abs(income_change))}，"
                f"支出{expense_direction} {_money(abs(expense_change))}。"
            )
        remaining = budget - budget_spent
        if budget > 0:
            lines.append(
                f"账房点评：预算还剩 {_money(max(remaining, Decimal(0)))}。"
                if remaining >= 0 else f"账房提醒：已超预算 {_money(abs(remaining))}，先看看排行靠前的项目。"
            )
        else:
            lines.append("账房建议：给主要支出分类设个预算，月底复盘会更有依据。")
    return "\n".join(lines)


def _keyboard(rows, *, columns=2):
    markup = types.InlineKeyboardMarkup(row_width=columns)
    markup.add(*rows)
    return markup


def _main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(
            label, web_app=types.WebAppInfo(
                url=f"{FINANCE_MINIAPP_URL}?view={view}"
                + (f"&kind={kind}" if kind else "")
            )
        )
        for label, (view, kind) in MENU_LABELS.items()
    ]
    markup.row(*buttons[:3])
    markup.row(*buttons[3:6])
    markup.row(buttons[6])
    return markup


def _view_keyboard(view, kind=None):
    query = f"?view={view}" + (f"&kind={kind}" if kind else "")
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        "打开财务页面", web_app=types.WebAppInfo(url=f"{FINANCE_MINIAPP_URL}{query}")
    ))
    return markup


def selectable_categories(reference, kind):
    if kind not in {"income", "expense"}:
        return []
    return [
        row for row in reference["categories"]
        if row["kind"] == kind
    ]


def _is_authorized(user, chat, owner_id):
    return bool(user and chat and chat.type == "private"
                and user.id == owner_id and owner_id in admin_ids())


def _safe_reply(app, message, text, **kwargs):
    try:
        app.send_message(message.chat.id, text, **kwargs)
    except Exception:
        logger.exception("Finance bot could not reply to Telegram user %s", message.from_user.id)
        raise


def _ask_amount(app, message, state, *, principal=False):
    state["step"] = "principal" if principal else "amount"
    label = "本金" if principal else "金额"
    _safe_reply(app, message, f"这笔{label}是多少？直接发数字即可，例如 120 或 120.50。")


def _ask_account(app, message, state, reference):
    state["step"] = "account"
    buttons = [
        types.InlineKeyboardButton(
            f"{row['name']} · {_money(row['balance'])}", callback_data=f"acct:{row['id']}"
        ) for row in reference["accounts"]
    ]
    _safe_reply(app, message, "选择这笔记录对应的账户。", reply_markup=_keyboard(buttons))


def _ask_target_account(app, message, state, reference):
    state["step"] = "target_account"
    buttons = [
        types.InlineKeyboardButton(row["name"], callback_data=f"target:{row['id']}")
        for row in reference["accounts"] if row["id"] != state["payload"]["account_id"]
    ]
    _safe_reply(app, message, "选择转入账户。", reply_markup=_keyboard(buttons))


def _show_confirmation(app, message, state, reference):
    payload = state["payload"]
    kind = payload["kind"]
    amount = payload.get("amount")
    if kind in {"debt_repay", "debt_collect"}:
        value_text = f"本金 {_money(payload['principal'])} · 利息 {_money(payload['interest'])}"
    else:
        value_text = _money(amount)
    account_names = {row["id"]: row["name"] for row in reference["accounts"]}
    description = [
        f"核对一下：{KIND_LABELS[kind]}",
        value_text,
    ]
    if payload.get("category_id"):
        category = next((row for row in reference["categories"]
                         if row["id"] == payload["category_id"]), None)
        if category:
            description.append(f"分类：{category['name']}")
    if payload.get("account_id"):
        description.append(f"账户：{account_names.get(payload['account_id'], '账户')}")
    if payload.get("target_account_id"):
        description.append(f"转入：{account_names.get(payload['target_account_id'], '账户')}")
    if payload.get("counterparty"):
        description.append(f"往来对象：{payload['counterparty']}")
    if payload.get("note"):
        description.append(f"备注：{payload['note']}")
    payload["request_id"] = payload.get("request_id") or uuid4().hex
    state["step"] = "confirm"
    buttons = [
        types.InlineKeyboardButton("确认记账", callback_data="save"),
        types.InlineKeyboardButton("取消", callback_data="cancel"),
    ]
    _safe_reply(app, message, "\n".join(description), reply_markup=_keyboard(buttons, columns=2))


def _continue_after_reference(app, message, state, reference):
    kind = state["payload"]["kind"]
    if kind in {"income", "expense"} and "category_id" not in state["payload"]:
        state["step"] = "category"
        categories = selectable_categories(reference, kind)
        buttons = [
            types.InlineKeyboardButton(row["name"], callback_data=f"cat:{row['id']}")
            for row in categories
        ]
        _safe_reply(app, message, "选择和 App 一致的分类，账目会按同一分类统计。", reply_markup=_keyboard(buttons))
        return
    if kind in {"borrow", "lend"} and "counterparty" not in state["payload"]:
        state["step"] = "counterparty"
        _safe_reply(app, message, "这笔借款的往来对象是谁？")
        return
    if "account_id" not in state["payload"]:
        _ask_account(app, message, state, reference)
        return
    if kind == "transfer" and "target_account_id" not in state["payload"]:
        _ask_target_account(app, message, state, reference)
        return
    _show_confirmation(app, message, state, reference)


def create_bot(token, owner_id):
    app = telebot.TeleBot(token)

    def reject_if_unauthorized(message):
        if _is_authorized(message.from_user, message.chat, owner_id):
            return False
        _safe_reply(app, message, "这个账房只对主人开放。")
        return True

    def send_report(message, report_kind):
        if reject_if_unauthorized(message):
            return
        try:
            if report_kind == "today":
                report = finance.daily_summary({"day": _today().isoformat()}, message.from_user.id)
                text = format_daily_report(report)
            else:
                dashboard = _dashboard(message.from_user.id)
                previous = _dashboard(message.from_user.id, _previous_month(dashboard["month"])) \
                    if report_kind == "analysis" else None
                text = format_monthly_report(
                    dashboard, analysis=report_kind == "analysis", previous=previous
                )
            _safe_reply(app, message, text, reply_markup=_main_keyboard())
        except Exception:
            logger.exception("Finance report failed for owner %s", message.from_user.id)
            _safe_reply(app, message, "账本读取失败了，数据没有改动。稍后再试一次；若持续发生请联系维护者。")

    @app.message_handler(commands=["start", "help"])
    def start(message):
        if reject_if_unauthorized(message):
            return
        _safe_reply(
            app, message,
            "我是阿兄的账房，话不多，账要清楚。收入支出、借还款和账户余额都跟现有账本同步。\n"
            "你负责把数字交代清楚，我负责把账算明白。先点「记一笔」或看今日、本月。",
            reply_markup=_main_keyboard(),
        )

    @app.message_handler(commands=["today"])
    def today_command(message):
        send_report(message, "today")

    @app.message_handler(commands=["month"])
    def month_command(message):
        send_report(message, "month")

    @app.message_handler(commands=["analysis"])
    def analysis_command(message):
        send_report(message, "analysis")

    @app.message_handler(commands=["record"])
    def record_command(message):
        start_record(message)

    @app.message_handler(commands=["cancel"])
    def cancel_command(message):
        if reject_if_unauthorized(message):
            return
        sessions.pop(message.from_user.id, None)
        _safe_reply(app, message, "好，这笔没保存。账本没有变化。", reply_markup=_main_keyboard())

    @app.message_handler(func=lambda message: bool(message.text and message.text.strip() in MENU_LABELS))
    def menu_message(message):
        if reject_if_unauthorized(message):
            return
        view, kind = MENU_LABELS[message.text.strip()]
        _safe_reply(
            app, message, "点下方按钮打开对应的个人财务页面。",
            reply_markup=_view_keyboard(view, kind),
        )

    def start_record(message):
        if reject_if_unauthorized(message):
            return
        sessions.pop(message.from_user.id, None)
        try:
            reference = _dashboard(message.from_user.id)
        except Exception:
            logger.exception("Finance record setup failed for owner %s", message.from_user.id)
            _safe_reply(app, message, "账本暂时读取失败，先没开始录入。稍后再试。")
            return
        sessions[message.from_user.id] = {
            "step": "kind",
            "payload": {"happened_on": _today().isoformat()},
            "reference": reference,
        }
        buttons = [
            types.InlineKeyboardButton(label, callback_data=f"kind:{kind}")
            for kind, label in KIND_LABELS.items()
        ]
        _safe_reply(app, message, "这笔是什么？选好类型，我们一步一步记，不会直接写入。", reply_markup=_keyboard(buttons))

    @app.message_handler(content_types=["text"], func=lambda message: bool(
        message.from_user and message.from_user.id in sessions
    ))
    def wizard_text(message):
        if reject_if_unauthorized(message):
            return
        state = sessions.get(message.from_user.id)
        if not state:
            return
        text = (message.text or "").strip()
        try:
            if state["step"] in {"amount", "principal"}:
                amount = finance._money(text, "金额")
                if state["step"] == "principal":
                    state["payload"]["principal"] = str(amount)
                    state["step"] = "interest"
                    _safe_reply(app, message, "利息是多少？没有利息就发 0。")
                    return
                state["payload"]["amount"] = str(amount)
                kind = state["payload"]["kind"]
                if kind in {"borrow", "lend"}:
                    state["step"] = "counterparty"
                    _safe_reply(app, message, "这笔借款的往来对象是谁？")
                    return
                _continue_after_reference(app, message, state, state["reference"])
            elif state["step"] == "interest":
                amount = finance._money(text, "利息", allow_zero=True)
                state["payload"]["interest"] = str(amount)
                _continue_after_reference(app, message, state, state["reference"])
            elif state["step"] == "counterparty":
                state["payload"]["counterparty"] = finance._text(text, "往来对象", 128)
                _continue_after_reference(app, message, state, state["reference"])
            elif state["step"] == "note":
                state["payload"]["note"] = finance._text(text, "备注", 1000, required=False)
                _show_confirmation(app, message, state, state["reference"])
            else:
                _safe_reply(app, message, "点一下上方选项继续，或发送 /cancel 取消。")
        except ValueError as exc:
            _safe_reply(app, message, f"{exc}，请重新输入；也可以发送 /cancel 取消。")
        except Exception:
            logger.exception("Finance record input failed for owner %s", message.from_user.id)
            _safe_reply(app, message, "这一步暂时没处理好，账本没有写入。发送 /cancel 退出后再试。")

    @app.callback_query_handler()
    def callback(call):
        user = call.from_user
        chat = call.message.chat if call.message else None
        if not _is_authorized(user, chat, owner_id):
            app.answer_callback_query(call.id, "无权限")
            return
        state = sessions.get(user.id)
        if call.data == "cancel":
            sessions.pop(user.id, None)
            app.answer_callback_query(call.id, "已取消")
            app.send_message(chat.id, "已取消，这笔没有保存。", reply_markup=_main_keyboard())
            return
        if not state and call.data not in {"today", "month", "analysis"}:
            app.answer_callback_query(call.id, "这一步已过期，请重新开始。")
            return
        try:
            data = call.data
            if data.startswith("kind:"):
                kind = data.split(":", 1)[1]
                if kind not in KIND_LABELS:
                    raise ValueError("记账类型无效")
                reference = state["reference"]
                if kind in {"debt_repay", "debt_collect"}:
                    direction = "payable" if kind == "debt_repay" else "receivable"
                    debts = [row for row in reference["debts"] if row["direction"] == direction]
                    if not debts:
                        app.answer_callback_query(call.id, "没有可选的未结清借款")
                        app.send_message(chat.id, "当前没有对应的未结清借款，请先登记借入或借出。")
                        return
                    state["payload"]["kind"] = kind
                    state["step"] = "debt"
                    buttons = [types.InlineKeyboardButton(
                        f"{row['counterparty']} · 剩余 {_money(row['outstanding'])}",
                        callback_data=f"debt:{row['id']}",
                    ) for row in debts]
                    app.answer_callback_query(call.id)
                    app.send_message(chat.id, "选择对应的借款记录。", reply_markup=_keyboard(buttons))
                    return
                state["payload"]["kind"] = kind
                app.answer_callback_query(call.id)
                _ask_amount(app, call.message, state)
                return
            if data.startswith("debt:"):
                debt_id = data.split(":", 1)[1]
                debt = next((row for row in state["reference"]["debts"] if row["id"] == debt_id), None)
                if not debt:
                    raise ValueError("借款记录已不可用，请刷新后重试")
                state["payload"]["debt_id"] = debt_id
                state["payload"]["counterparty"] = debt["counterparty"]
                app.answer_callback_query(call.id)
                _ask_amount(app, call.message, state, principal=True)
                return
            if data.startswith("cat:"):
                category_id = int(data.split(":", 1)[1])
                category = next((row for row in selectable_categories(
                    state["reference"], state["payload"]["kind"]
                ) if row["id"] == category_id), None)
                if not category:
                    raise ValueError("分类已不可用，请重新选择")
                state["payload"]["category_id"] = category_id
                app.answer_callback_query(call.id)
                _continue_after_reference(app, call.message, state, state["reference"])
                return
            if data.startswith("acct:"):
                account_id = int(data.split(":", 1)[1])
                if not any(row["id"] == account_id for row in state["reference"]["accounts"]):
                    raise ValueError("账户已不可用，请重新选择")
                state["payload"]["account_id"] = account_id
                app.answer_callback_query(call.id)
                _continue_after_reference(app, call.message, state, state["reference"])
                return
            if data.startswith("target:"):
                target_id = int(data.split(":", 1)[1])
                if not any(row["id"] == target_id and row["id"] != state["payload"].get("account_id")
                           for row in state["reference"]["accounts"]):
                    raise ValueError("转入账户无效，请重新选择")
                state["payload"]["target_account_id"] = target_id
                app.answer_callback_query(call.id)
                _show_confirmation(app, call.message, state, state["reference"])
                return
            if data == "save":
                if state["step"] != "confirm":
                    raise ValueError("这笔记录还没有准备好")
                payload = state["payload"]
                result = finance.save_transaction(payload, user.id)
                sessions.pop(user.id, None)
                app.answer_callback_query(call.id, "已保存")
                app.send_message(chat.id, "记好了。账目已同步到现有个人财务账本。", reply_markup=_main_keyboard())
                logger.info("Personal finance entry saved for owner %s as %s",
                            user.id, result.get("transaction_id"))
                return
            if data in {"today", "month", "analysis"}:
                app.answer_callback_query(call.id)
                send_report(call.message, data)
                return
            raise ValueError("操作无效，请重新开始")
        except ValueError as exc:
            app.answer_callback_query(call.id, str(exc)[:180])
        except Exception:
            logger.exception("Finance bot action failed for owner %s", user.id)
            app.answer_callback_query(call.id, "操作暂时失败")
            app.send_message(chat.id, "这次操作没有确认成功。请先查「本月」账本核对，再决定是否重试。")

    return app


def main():
    token = os.getenv("FINANCE_BOT_TOKEN")
    if not token:
        raise RuntimeError("FINANCE_BOT_TOKEN is not configured in bot/.env")
    owner_id_value = os.getenv("FINANCE_BOT_OWNER_ID", "")
    if not owner_id_value.isdigit() or int(owner_id_value) <= 0:
        raise RuntimeError("FINANCE_BOT_OWNER_ID must be your numeric Telegram user ID")
    owner_id = int(owner_id_value)
    if owner_id not in admin_ids():
        raise RuntimeError("FINANCE_BOT_OWNER_ID must also be present in ADMIN_USER_IDS")
    finance.ensure_tables()
    app = create_bot(token, owner_id)
    app.set_my_commands([
        types.BotCommand("today", "查看今日收支"),
        types.BotCommand("month", "查看本月账本"),
        types.BotCommand("analysis", "查看分类与预算分析"),
        types.BotCommand("record", "开始登记一笔账"),
        types.BotCommand("cancel", "取消当前记账"),
        types.BotCommand("help", "查看使用说明"),
    ])
    logger.info("%s is starting with the existing personal finance ledger", BOT_NAME)
    app.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    main()
