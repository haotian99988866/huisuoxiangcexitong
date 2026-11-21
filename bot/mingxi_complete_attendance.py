"""名玺会所：出勤初稿 → 带相册链接的完整版回复。"""
from __future__ import annotations

import html
import logging
from html.parser import HTMLParser

import telebot

from db import get_conn
from footer import footer_keyboard
from mingxi_channel_album import (
    MINGXI_CHANNEL_ID,
    list_album_entries,
    lookup_album_msg_ids,
    upsert_album_message,
)

logger = logging.getLogger(__name__)

PLATE_VENUE_ID = 2
COMPLETE_MARKER = "【出勤完整版】"
FULL_ROSTER_MARKER = "【人员全部名单】"
FULL_ROSTER_TRIGGERS = ("全部名单", "全员名单", "人员全部名单")
# 店介绍链接后续配置；本期可空
INTRO_LINKS = ()  # (("环境介绍", msg_id), ...)

_JP_KR_CODES = frozenset({"01", "02", "03", "05", "06", "07", "08"})
_MINGXI_PLATE_CODES = frozenset({"77", "3333", "5555", "7777", "8888", "9999"})
_GROUP_ORDER = (
    "名玺牌",
    "日韩专场",
    "9号牌",
    "8号牌",
    "7号牌",
    "6号牌",
    "5号牌",
    "俄罗斯",
    "其他",
)


def is_complete_attendance_text(text: str) -> bool:
    return COMPLETE_MARKER in (text or "") or FULL_ROSTER_MARKER in (text or "")


def is_full_roster_request(text: str) -> bool:
    """频道短指令：发布与今日出勤分开的全部人员名单。"""
    value = "".join((text or "").split())
    if not value or is_complete_attendance_text(value):
        return False
    for prefix in ("请发", "发一下", "发", "请"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value in FULL_ROSTER_TRIGGERS


def classify_roster_group(code: str) -> str:
    code = str(code or "").strip()
    if code in _MINGXI_PLATE_CODES:
        return "名玺牌"
    if code in _JP_KR_CODES or (code.isdigit() and len(code) <= 2):
        return "日韩专场"
    if code == "7026":
        return "俄罗斯"
    if code.isdigit() and len(code) >= 3:
        return f"{code[0]}号牌"
    return "其他"


def group_roster_codes(codes):
    buckets = {name: [] for name in _GROUP_ORDER}
    for code in codes:
        code = str(code).strip()
        if not code:
            continue
        name = classify_roster_group(code)
        buckets.setdefault(name, [])
        if code not in buckets[name]:
            buckets[name].append(code)
    groups = []
    for name in _GROUP_ORDER:
        if buckets.get(name):
            groups.append({"group_name": name, "technician_codes": buckets[name]})
    for name, items in buckets.items():
        if name not in _GROUP_ORDER and items:
            groups.append({"group_name": name, "technician_codes": items})
    return groups


def channel_numeric_id(channel_id) -> str:
    value = str(channel_id)
    if value.startswith("-100"):
        return value[4:]
    return value.lstrip("-")


def message_link(channel_id, msg_id) -> str:
    """频道内单张照片（?single 避免媒体组乱跳）。t.me 域名，不弹外链确认。"""
    return (
        f"https://t.me/c/{channel_numeric_id(channel_id)}/{int(msg_id)}?single"
    )


def resolve_album_links(venue_id, codes, channel_id=None):
    """工号 → 频道相册消息链接（只链索引里仍有效的现帖）。"""
    if venue_id != PLATE_VENUE_ID:
        return {}, {}
    wanted = [str(code).strip() for code in codes if str(code).strip()]
    if not wanted:
        return {}, {}
    channel_id = str(channel_id or MINGXI_CHANNEL_ID)
    msg_ids = lookup_album_msg_ids(wanted, channel_id=channel_id)
    urls = {
        code: message_link(channel_id, msg_id)
        for code, msg_id in msg_ids.items()
    }
    return urls, msg_ids


def resolve_album_msg_ids(venue_id, codes):
    """兼容旧调用：返回工号 → 频道 msg_id。"""
    _, msg_ids = resolve_album_links(venue_id, codes)
    return msg_ids


def remember_album_photo(code, msg_id, channel_id=MINGXI_CHANNEL_ID):
    """照片入库成功后刷新频道相册索引。"""
    return upsert_album_message(code, msg_id, channel_id=channel_id)


def _format_code(code, href):
    label = html.escape(str(code))
    if not href:
        return label
    safe = html.escape(str(href), quote=True)
    return f'<a href="{safe}">{label}</a>'


def build_complete_attendance_html(
    venue_name,
    attendance,
    channel_id,
    album_urls=None,
    album_msg_ids=None,
    intro_links=None,
):
    """生成完整版 HTML（含标记，避免被再次当初稿解析）。"""
    album_urls = dict(album_urls or {})
    # 兼容旧参数：仅有 msg_id 时回退为频道深链
    for code, msg_id in (album_msg_ids or {}).items():
        album_urls.setdefault(str(code), message_link(channel_id, msg_id))
    intro_links = intro_links if intro_links is not None else INTRO_LINKS
    duty_date = attendance.get("duty_date")
    duty_shift = attendance.get("duty_shift") or "出勤"
    date_text = str(duty_date) if duty_date else "日期待确认"
    lines = [
        COMPLETE_MARKER,
        f"{html.escape(venue_name)} · {html.escape(str(duty_shift))}",
        f"日期 {html.escape(date_text)}",
        "",
    ]

    groups = [
        group
        for group in (attendance.get("attendance_groups") or [])
        if group.get("group_type") == "roster" and group.get("technician_codes")
    ]
    if not groups and attendance.get("codes"):
        groups = [
            {
                "group_type": "roster",
                "group_name": "出勤名单",
                "technician_codes": list(attendance["codes"]),
            }
        ]

    missing = []
    for group in groups:
        name = group.get("group_name") or "名单"
        lines.append(f"<b>{html.escape(str(name))}</b>")
        chips = []
        for code in group.get("technician_codes") or []:
            code = str(code).strip()
            if not code:
                continue
            href = album_urls.get(code)
            if not href:
                missing.append(code)
            chips.append(_format_code(code, href))
        lines.append("  ".join(chips) if chips else "（无）")
        lines.append("")

    service_groups = [
        group
        for group in (attendance.get("attendance_groups") or [])
        if group.get("group_type") == "service" and group.get("technician_codes")
    ]
    if service_groups:
        lines.append("<b>项目</b>")
        for group in service_groups:
            name = group.get("group_name") or "项目"
            chips = [
                _format_code(
                    str(code).strip(),
                    album_urls.get(str(code).strip()),
                )
                for code in group["technician_codes"]
                if str(code).strip()
            ]
            lines.append(f"{html.escape(str(name))}：{'  '.join(chips)}")
        lines.append("")

    # 去重保持顺序
    seen = set()
    missing_unique = []
    for code in missing:
        if code not in seen:
            seen.add(code)
            missing_unique.append(code)
    if missing_unique:
        lines.append(
            "暂无相册：" + " ".join(html.escape(code) for code in missing_unique)
        )
        lines.append("")

    if intro_links:
        parts = []
        for title, msg_id in intro_links:
            href = html.escape(message_link(channel_id, msg_id), quote=True)
            parts.append(f'<a href="{href}">{html.escape(str(title))}</a>')
        lines.append("—— 了解本店 ——")
        lines.append("  |  ".join(parts))
        lines.append("")

    lines.append("点工号跳转到频道相册对应照片。")
    return "\n".join(lines).rstrip() + "\n"


def build_full_roster_html(venue_name, channel_id, entries=None):
    """全部人员名单（与今日出勤分开）；仅含频道相册索引中的工号。"""
    entries = list(entries if entries is not None else list_album_entries(channel_id))
    codes = [code for code, _msg_id in entries]
    album_urls = {
        code: message_link(channel_id, msg_id) for code, msg_id in entries
    }
    lines = [
        FULL_ROSTER_MARKER,
        f"{html.escape(venue_name)} · 人员全部名单",
        f"共 {len(codes)} 人（与今日出勤分开，点工号看频道相册）",
        "",
    ]
    if not codes:
        lines.append("暂无已索引的频道相册人员。")
        return "\n".join(lines).rstrip() + "\n"

    for group in group_roster_codes(codes):
        lines.append(f"<b>{html.escape(group['group_name'])}</b>")
        chips = [
            _format_code(code, album_urls.get(code))
            for code in group["technician_codes"]
        ]
        lines.append("  ".join(chips))
        lines.append("")
    lines.append("今日谁在请看【出勤完整版】；本名单为频道相册全员目录。")
    return "\n".join(lines).rstrip() + "\n"


def post_full_roster(bot, channel_id, venue_config, reply_to_message_id=None):
    """发布名玺人员全部名单（独立于今日出勤）。"""
    if venue_config.get("venue_id") != PLATE_VENUE_ID:
        return None
    text = build_full_roster_html(
        venue_config.get("venue_name") or "名玺水汇会所",
        channel_id,
    )
    kwargs = {
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": footer_keyboard(),
    }
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
        kwargs["allow_sending_without_reply"] = True
    try:
        sent = bot.send_message(channel_id, text, **kwargs)
        logger.info(
            "名玺全部名单已发布 chat=%s msg=%s",
            channel_id,
            getattr(sent, "message_id", None),
        )
        return sent
    except telebot.apihelper.ApiTelegramException:
        logger.exception("名玺全部名单发送失败 chat=%s", channel_id)
        return None


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def html_to_plain(value: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(value)
    return "".join(parser.parts)


def post_complete_attendance_reply(bot, message, venue_config, attendance):
    """回复初稿：完整版。仅名玺会所。"""
    if venue_config.get("venue_id") != PLATE_VENUE_ID:
        return None
    codes = list(attendance.get("codes") or [])
    for group in attendance.get("attendance_groups") or []:
        codes.extend(group.get("technician_codes") or [])
    album_urls, _msg_ids = resolve_album_links(
        PLATE_VENUE_ID, codes, channel_id=message.chat.id
    )
    text = build_complete_attendance_html(
        venue_config.get("venue_name") or "名玺水汇会所",
        attendance,
        message.chat.id,
        album_urls=album_urls,
    )
    try:
        sent = bot.send_message(
            message.chat.id,
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_to_message_id=message.message_id,
            allow_sending_without_reply=True,
            reply_markup=footer_keyboard(),
        )
        logger.info(
            "名玺出勤完整版已回复 draft=%s complete=%s links=%s missing=%s",
            message.message_id,
            getattr(sent, "message_id", None),
            len(album_urls),
            len(set(str(c).strip() for c in codes if str(c).strip())) - len(album_urls),
        )
        return sent
    except telebot.apihelper.ApiTelegramException:
        logger.exception(
            "名玺出勤完整版发送失败 chat=%s draft=%s",
            message.chat.id,
            message.message_id,
        )
        return None


def post_complete_for_latest_attendance(bot, channel_id, venue_config, reply_to_message_id=None):
    """用库中最新出勤补发完整版（初稿已发、功能上线后补一次）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT duty_date, duty_shift, technician_codes
                FROM attendance_daily
                WHERE venue_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (PLATE_VENUE_ID,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                """
                SELECT group_type, group_name, technician_codes
                FROM attendance_groups
                WHERE venue_id = %s AND duty_date = %s
                ORDER BY sort_order, id
                """,
                (PLATE_VENUE_ID, row["duty_date"]),
            )
            groups = []
            for group in cur.fetchall():
                groups.append(
                    {
                        "group_type": group["group_type"],
                        "group_name": group["group_name"],
                        "technician_codes": [
                            code.strip()
                            for code in (group["technician_codes"] or "").split(",")
                            if code.strip()
                        ],
                    }
                )
    finally:
        conn.close()

    codes = [c.strip() for c in (row["technician_codes"] or "").split(",") if c.strip()]
    attendance = {
        "duty_date": row["duty_date"],
        "duty_shift": row["duty_shift"],
        "codes": codes,
        "attendance_groups": groups
        or [
            {
                "group_type": "roster",
                "group_name": "出勤名单",
                "technician_codes": codes,
            }
        ],
    }
    album_urls, _msg_ids = resolve_album_links(
        PLATE_VENUE_ID, codes, channel_id=channel_id
    )
    text = build_complete_attendance_html(
        venue_config.get("venue_name") or "名玺水汇会所",
        attendance,
        channel_id,
        album_urls=album_urls,
    )
    kwargs = {
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": footer_keyboard(),
    }
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
        kwargs["allow_sending_without_reply"] = True
    return bot.send_message(channel_id, text, **kwargs)
