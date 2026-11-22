"""名玺会所：出勤初稿入库 → 公开发完整版并删初稿；相册变更防抖刷新全员/本班枢纽。"""
from __future__ import annotations

import html
import logging
import threading
from types import SimpleNamespace

import telebot

from db import get_conn
from footer import footer_keyboard
from mingxi_channel_album import (
    MINGXI_CHANNEL_ID,
    PLATE_VENUE_ID,
    list_album_entries,
    lookup_album_msg_ids,
    sync_album_from_matched_photos,
    upsert_album_message,
)
from venue_pricing import mingxi_board_price_suffix

logger = logging.getLogger(__name__)

_hub_timers = {}
_hub_timers_lock = threading.Lock()
HUB_QUIET_SECONDS = 75

COMPLETE_MARKER = "【出勤完整版】"
FULL_ROSTER_MARKER = "【人员全部名单】"
ENV_NAV_MARKER = "【环境导航】"
FULL_ROSTER_TRIGGERS = ("全部名单", "全员名单", "人员全部名单")
# 固定入口回退：环境/全员帖相对稳定；出勤无固定回退（必须有锚点才互链）
ENV_NAV_MSG_ID = 10503
FULL_ROSTER_MSG_ID = 10506
ANCHOR_ATTENDANCE = "attendance_complete"
ANCHOR_ENV_NAV = "env_nav"
ANCHOR_ROSTER = "full_roster"
# 环境导航：来自 @mingxi03 #1622 起现帖扫描（优先 3189+ 新版实景）
ENV_NAV_LINKS = (
    ("价目表", "https://t.me/mingxi03/10501"),
    ("大堂吧", "https://t.me/mingxi03/3189"),
    ("女浴", "https://t.me/mingxi03/3190"),
    ("男浴", "https://t.me/mingxi03/3191"),
    ("台球厅", "https://t.me/mingxi03/3193"),
    ("选秀厅", "https://t.me/mingxi03/3194"),
    ("奶茶店", "https://t.me/mingxi03/3195"),
    ("雪茄吧", "https://t.me/mingxi03/3197"),
    ("美容区", "https://t.me/mingxi03/3198"),
    ("理发区", "https://t.me/mingxi03/3199"),
    ("足浴休息区", "https://t.me/mingxi03/3201"),
    ("自助餐厅", "https://t.me/mingxi03/3202"),
    ("棋牌麻将房", "https://t.me/mingxi03/3203"),
    ("按摩包厢", "https://t.me/mingxi03/3204"),
    ("中医理疗", "https://t.me/mingxi03/3205"),
    ("KTV·玺御厅403", "https://t.me/mingxi03/3207"),
    ("KTV·玺汇厅401", "https://t.me/mingxi03/3208"),
    ("KTV·玺盈厅405", "https://t.me/mingxi03/3209"),
    ("KTV·玺聚厅402", "https://t.me/mingxi03/3210"),
    ("KTV·玺悦厅503", "https://t.me/mingxi03/3212"),
    ("KTV·玺宸厅501", "https://t.me/mingxi03/3213"),
    ("KTV·玺运厅502", "https://t.me/mingxi03/3214"),
    ("KTV·玺顺厅505", "https://t.me/mingxi03/3215"),
    ("教师情景房", "https://t.me/mingxi03/3217"),
    ("护士情景房", "https://t.me/mingxi03/3218"),
    ("空姐情景房", "https://t.me/mingxi03/3219"),
    ("学生情景房", "https://t.me/mingxi03/3220"),
    ("商务房", "https://t.me/mingxi03/3222"),
)

_JP_KR_CODES = frozenset({"01", "02", "03", "05", "06", "07", "08", "77"})
_MINGXI_PLATE_CODES = frozenset({"3333", "5555", "7777", "8888", "9999"})
_GROUP_ORDER = (
    "名玺牌",
    "日韩专场",
    "9号牌",
    "8号牌",
    "7号牌",
    "6号牌",
    "5号牌",
    "其他",
)


def is_complete_attendance_text(text: str) -> bool:
    """机器人托管公告（勿再当初稿出勤解析）。"""
    value = text or ""
    return (
        COMPLETE_MARKER in value
        or FULL_ROSTER_MARKER in value
        or ENV_NAV_MARKER in value
        or "【优惠】" in value  # 历史优惠帖，勿当初稿
    )


def _public_msg_url(msg_id) -> str:
    return f"https://t.me/mingxi03/{int(msg_id)}"


def _attendance_nav_url():
    msg_id = get_channel_anchor(MINGXI_CHANNEL_ID, ANCHOR_ATTENDANCE)
    return _public_msg_url(msg_id) if msg_id else None


def _roster_nav_url() -> str:
    msg_id = get_channel_anchor(MINGXI_CHANNEL_ID, ANCHOR_ROSTER) or FULL_ROSTER_MSG_ID
    return _public_msg_url(msg_id)


def _env_nav_url() -> str:
    msg_id = get_channel_anchor(MINGXI_CHANNEL_ID, ANCHOR_ENV_NAV) or ENV_NAV_MSG_ID
    return _public_msg_url(msg_id)


def _hub_footer_lines(*, exclude=()):
    """三条互链：出勤 / 全员 / 环境；exclude 里的不显示（避免链自己）。"""
    skip = {str(item) for item in exclude}
    parts = []
    if "env" not in skip:
        parts.append(
            f'<a href="{html.escape(_env_nav_url(), quote=True)}">'
            f"{html.escape(ENV_NAV_MARKER)}</a>"
        )
    if "attendance" not in skip:
        att_url = _attendance_nav_url()
        if att_url:
            parts.append(
                f'<a href="{html.escape(att_url, quote=True)}">'
                f"{html.escape(COMPLETE_MARKER)}</a>"
            )
    if "roster" not in skip:
        parts.append(
            f'<a href="{html.escape(_roster_nav_url(), quote=True)}">'
            f"{html.escape(FULL_ROSTER_MARKER)}</a>"
        )
    if not parts:
        return []
    return ["导航  " + "  ·  ".join(parts)]


def ensure_anchor_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mingxi_channel_anchors (
                    channel_id VARCHAR(32) NOT NULL,
                    kind VARCHAR(32) NOT NULL,
                    msg_id BIGINT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (channel_id, kind)
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def get_channel_anchor(channel_id, kind):
    ensure_anchor_tables()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT msg_id FROM mingxi_channel_anchors
                WHERE channel_id = %s AND kind = %s
                """,
                (str(channel_id), kind),
            )
            row = cur.fetchone()
            return int(row["msg_id"]) if row else None
    finally:
        conn.close()


def set_channel_anchor(channel_id, kind, msg_id):
    ensure_anchor_tables()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mingxi_channel_anchors (channel_id, kind, msg_id)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE msg_id = VALUES(msg_id)
                """,
                (str(channel_id), kind, int(msg_id)),
            )
        conn.commit()
    finally:
        conn.close()


def _delete_channel_message(bot, channel_id, msg_id):
    if not msg_id:
        return
    try:
        bot.delete_message(channel_id, int(msg_id))
        logger.info("已删除旧频道帖 chat=%s msg=%s", channel_id, msg_id)
    except telebot.apihelper.ApiTelegramException:
        logger.warning("删除旧频道帖失败 chat=%s msg=%s", channel_id, msg_id)


def _pin_channel_message(bot, channel_id, msg_id):
    if not msg_id:
        return
    try:
        bot.pin_chat_message(channel_id, int(msg_id), disable_notification=True)
    except telebot.apihelper.ApiTelegramException:
        logger.warning("置顶失败 chat=%s msg=%s", channel_id, msg_id)


def _unpin_channel_message(bot, channel_id, msg_id):
    if not msg_id:
        return
    try:
        bot.unpin_chat_message(channel_id, message_id=int(msg_id))
    except telebot.apihelper.ApiTelegramException:
        logger.warning("取消置顶失败 chat=%s msg=%s", channel_id, msg_id)


def publish_complete_attendance(
    bot,
    channel_id,
    text,
    *,
    reply_markup=None,
    reply_to_message_id=None,
):
    """发布今日出勤完整版：发新删旧挂在频道底部，不置顶（环境导航仍可置顶）。"""
    channel_id = int(channel_id)
    old_id = get_channel_anchor(channel_id, ANCHOR_ATTENDANCE)
    kwargs = {
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": reply_markup or footer_keyboard(),
        "disable_notification": True,
    }
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
        kwargs["allow_sending_without_reply"] = True
    sent = bot.send_message(channel_id, text, **kwargs)
    new_id = getattr(sent, "message_id", None)
    if new_id:
        set_channel_anchor(channel_id, ANCHOR_ATTENDANCE, new_id)
        if old_id and int(old_id) != int(new_id):
            _unpin_channel_message(bot, channel_id, old_id)
            _delete_channel_message(bot, channel_id, old_id)
        # 环境导航继续置顶；出勤完整版只挂底不置顶
        env_id = get_channel_anchor(channel_id, ANCHOR_ENV_NAV) or ENV_NAV_MSG_ID
        if env_id and int(env_id) != int(new_id):
            _pin_channel_message(bot, channel_id, env_id)
        refresh_cross_nav_messages(bot, channel_id, skip=("attendance",))
    return sent


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
    # 7026 等俄罗斯技师按 7 号牌归类计价
    if code == "7026" or (code.isdigit() and len(code) >= 3 and code[0] == "7"):
        return "7号牌"
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


def remember_album_photo(code, msg_id, channel_id=MINGXI_CHANNEL_ID):
    """照片入库成功后刷新频道相册索引。"""
    return upsert_album_message(code, msg_id, channel_id=channel_id)


def _format_code(code, href):
    label = html.escape(str(code))
    if not href:
        return label
    safe = html.escape(str(href), quote=True)
    return f'<a href="{safe}">{label}</a>'


def _format_board_heading(group_name):
    """牌名加粗 + 紧凑价签，如 9号牌  半$249  会$499（避免长标题折行）。"""
    name = str(group_name or "名单")
    suffix = mingxi_board_price_suffix(name)
    compact = (
        suffix.replace(" · 半价$", "  半$")
        .replace(" · 会员$", "  会$")
        .replace("半价$", "半$")
        .replace("会员$", "会$")
    )
    return f"<b>{html.escape(name)}</b>{html.escape(compact)}"


def _join_codes(chips):
    """工号连成一行，由客户端按气泡宽度自然折行，铺满横向空间。"""
    chips = [chip for chip in chips if chip]
    return "  ".join(chips) if chips else "—"


def build_env_nav_html(venue_name="名玺水汇会所", env_nav_links=None):
    """独立一条环境导航（避免与全员工号链挤爆 Telegram 100 实体上限）。"""
    links = ENV_NAV_LINKS if env_nav_links is None else env_nav_links
    parts = [
        _format_code(title, url) for title, url in links if title and url
    ]
    lines = [
        ENV_NAV_MARKER,
        f"{html.escape(venue_name)} · 环境导航",
        "点名称查看实景 / 价目。",
        "",
    ]
    if not parts:
        lines.append("暂无环境导航链接。")
        lines.append("")
        lines.extend(_hub_footer_lines(exclude=("env",)))
        return "\n".join(lines).rstrip() + "\n"
    # 每行若干个，便于阅读
    row = []
    for i, part in enumerate(parts, 1):
        row.append(part)
        if i % 4 == 0:
            lines.append("  |  ".join(row))
            row = []
    if row:
        lines.append("  |  ".join(row))
    lines.append("")
    lines.extend(_hub_footer_lines(exclude=("env",)))
    return "\n".join(lines).rstrip() + "\n"


def _edit_hub_message(bot, channel_id, msg_id, text):
    if not msg_id or not text:
        return
    try:
        bot.edit_message_text(
            text,
            int(channel_id),
            int(msg_id),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=footer_keyboard(),
        )
    except telebot.apihelper.ApiTelegramException as exc:
        if "message is not modified" not in str(exc).lower():
            logger.warning(
                "刷新导航帖失败 chat=%s msg=%s: %s", channel_id, msg_id, exc
            )


def refresh_cross_nav_messages(
    bot, channel_id, venue_name="名玺水汇会所", *, skip=()
):
    """出勤/全员/环境互相导航：锚点变化后刷新其余枢纽文末链接。"""
    channel_id = int(channel_id)
    skip = {str(item) for item in skip}
    if "env" not in skip:
        env_id = get_channel_anchor(channel_id, ANCHOR_ENV_NAV) or ENV_NAV_MSG_ID
        _edit_hub_message(bot, channel_id, env_id, build_env_nav_html(venue_name))
    if "roster" not in skip:
        roster_id = get_channel_anchor(channel_id, ANCHOR_ROSTER) or FULL_ROSTER_MSG_ID
        _edit_hub_message(
            bot,
            channel_id,
            roster_id,
            build_full_roster_html(venue_name, channel_id),
        )
    if "attendance" not in skip:
        att_id = get_channel_anchor(channel_id, ANCHOR_ATTENDANCE)
        if att_id:
            attendance = load_latest_mingxi_attendance()
            if attendance:
                codes = list(attendance.get("codes") or [])
                for group in attendance.get("attendance_groups") or []:
                    codes.extend(group.get("technician_codes") or [])
                album_urls, _ = resolve_album_links(
                    PLATE_VENUE_ID, codes, channel_id=channel_id
                )
                text, _markup = build_complete_attendance_content(
                    venue_name, attendance, channel_id, album_urls=album_urls
                )
                _edit_hub_message(bot, channel_id, att_id, text)


def _roster_groups(attendance):
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
    return groups


def build_complete_attendance_html(
    venue_name,
    attendance,
    channel_id,
    album_urls=None,
):
    """完整版正文：紧凑牌价 + 工号自然折行铺满气泡宽度。"""
    album_urls = dict(album_urls or {})
    duty_date = attendance.get("duty_date")
    duty_shift = attendance.get("duty_shift") or "出勤"
    if duty_shift == "早班":
        duty_shift = "白班"
    date_text = str(duty_date) if duty_date else "日期待确认"
    lines = [
        COMPLETE_MARKER,
        (
            f"<b>{html.escape(venue_name)}</b>"
            f" · {html.escape(str(duty_shift))}"
            f" · {html.escape(date_text)}"
        ),
        "",
    ]

    groups = _roster_groups(attendance)
    missing = []
    for group in groups:
        name = group.get("group_name") or "名单"
        lines.append(_format_board_heading(name))
        chips = []
        for code in group.get("technician_codes") or []:
            code = str(code).strip()
            if not code:
                continue
            href = album_urls.get(code)
            if not href:
                missing.append(code)
            chips.append(_format_code(code, href))
        lines.append(_join_codes(chips))

    service_groups = [
        group
        for group in (attendance.get("attendance_groups") or [])
        if group.get("group_type") == "service" and group.get("technician_codes")
    ]
    if service_groups:
        lines.append("")
        lines.append("<b>本班项目</b>")
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
            lines.append(f"<b>{html.escape(str(name))}</b>  {_join_codes(chips)}")

    seen = set()
    missing_unique = []
    for code in missing:
        if code not in seen:
            seen.add(code)
            missing_unique.append(code)
    if missing_unique:
        lines.append("")
        lines.append(
            "<b>暂无相册</b>  "
            + _join_codes([html.escape(code) for code in missing_unique])
        )

    lines.append("")
    lines.append("点蓝字看相册")
    lines.extend(_hub_footer_lines(exclude=("attendance",)))
    return "\n".join(lines).rstrip() + "\n"


def build_complete_attendance_content(
    venue_name,
    attendance,
    channel_id,
    album_urls=None,
):
    """完整版正文 + 入口底栏（工号在正文蓝字，不用按钮）。"""
    text = build_complete_attendance_html(
        venue_name,
        attendance,
        channel_id,
        album_urls=album_urls,
    )
    return text, footer_keyboard()


def build_full_roster_html(venue_name, channel_id, entries=None):
    """全部人员名单（与今日出勤分开）；仅含频道相册索引中的工号。"""
    entries = list(entries if entries is not None else list_album_entries(channel_id))
    codes = [code for code, _msg_id in entries]
    album_urls = {
        code: message_link(channel_id, msg_id) for code, msg_id in entries
    }
    lines = [
        FULL_ROSTER_MARKER,
        (
            f"<b>{html.escape(venue_name)}</b>"
            f" · 人员全部名单 · 共 {len(codes)} 人"
        ),
        "",
    ]
    if not codes:
        lines.append("暂无已索引的频道相册人员。")
        lines.append("")
        lines.extend(_hub_footer_lines(exclude=("roster",)))
        return "\n".join(lines).rstrip() + "\n"

    for group in group_roster_codes(codes):
        lines.append(_format_board_heading(group["group_name"]))
        chips = [
            _format_code(code, album_urls.get(code))
            for code in group["technician_codes"]
        ]
        lines.append(_join_codes(chips))
    lines.append("")
    lines.append("点蓝字看相册 · 与今日出勤分开")
    lines.extend(_hub_footer_lines(exclude=("roster",)))
    return "\n".join(lines).rstrip() + "\n"


def publish_full_roster(bot, channel_id, text):
    """发布人员全部名单：优先编辑现帖；失败则发新删旧，只留最新一条。"""
    channel_id = int(channel_id)
    old_id = get_channel_anchor(channel_id, ANCHOR_ROSTER) or FULL_ROSTER_MSG_ID
    if old_id:
        try:
            bot.edit_message_text(
                text,
                channel_id,
                int(old_id),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=footer_keyboard(),
            )
            set_channel_anchor(channel_id, ANCHOR_ROSTER, old_id)
            refresh_cross_nav_messages(bot, channel_id, skip=("roster",))
            return SimpleNamespace(message_id=int(old_id), edited=True)
        except telebot.apihelper.ApiTelegramException as exc:
            if "message is not modified" in str(exc).lower():
                set_channel_anchor(channel_id, ANCHOR_ROSTER, old_id)
                return SimpleNamespace(message_id=int(old_id), edited=True)
            logger.info("名玺全部名单无法编辑，改为发新删旧: %s", exc)
    sent = bot.send_message(
        channel_id,
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=footer_keyboard(),
    )
    new_id = getattr(sent, "message_id", None)
    if new_id:
        set_channel_anchor(channel_id, ANCHOR_ROSTER, new_id)
        if old_id and int(old_id) != int(new_id):
            _delete_channel_message(bot, channel_id, old_id)
        refresh_cross_nav_messages(bot, channel_id, skip=("roster",))
    return sent


def post_full_roster(bot, channel_id, venue_config, reply_to_message_id=None):
    """发布名玺人员全部名单（独立于今日出勤）；不跟发环境导航。"""
    if venue_config.get("venue_id") != PLATE_VENUE_ID:
        return None
    venue_name = venue_config.get("venue_name") or "名玺水汇会所"
    sync_album_from_matched_photos(channel_id)
    text = build_full_roster_html(venue_name, channel_id)
    try:
        sent = publish_full_roster(bot, channel_id, text)
        new_id = getattr(sent, "message_id", None)
        logger.info(
            "名玺全部名单已发布 chat=%s msg=%s",
            channel_id,
            new_id,
        )
        if reply_to_message_id:
            _delete_channel_message(bot, channel_id, reply_to_message_id)
        return sent
    except telebot.apihelper.ApiTelegramException:
        logger.exception("名玺全部名单发送失败 chat=%s", channel_id)
        return None


def load_latest_mingxi_attendance():
    """读取名玺库中最新一条出勤（含分组）。"""
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
    return {
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


def refresh_complete_attendance_links(bot, channel_id, venue_name="名玺水汇会所"):
    """相册变更后：发新删旧刷新出勤完整版并挂回频道底部。"""
    attendance = load_latest_mingxi_attendance()
    if not attendance:
        return None
    channel_id = int(channel_id)
    sync_album_from_matched_photos(channel_id)
    codes = list(attendance.get("codes") or [])
    for group in attendance.get("attendance_groups") or []:
        codes.extend(group.get("technician_codes") or [])
    album_urls, _msg_ids = resolve_album_links(
        PLATE_VENUE_ID, codes, channel_id=channel_id
    )
    text, markup = build_complete_attendance_content(
        venue_name, attendance, channel_id, album_urls=album_urls
    )
    return publish_complete_attendance(
        bot, channel_id, text, reply_markup=markup
    )


def schedule_hub_refresh_after_album(
    bot, channel_id, code=None, venue_name="名玺水汇会所", delay=HUB_QUIET_SECONDS
):
    """相册索引更新后防抖刷新：全员名单必刷；工号若在本班则刷新出勤链接。"""
    channel_id = str(channel_id)
    code = str(code or "").strip()
    with _hub_timers_lock:
        state = _hub_timers.pop(channel_id, None)
        codes = set((state or {}).get("codes") or ())
        if state and state.get("timer"):
            state["timer"].cancel()
        if code:
            codes.add(code)
        timer = threading.Timer(
            delay,
            _fire_hub_refresh,
            args=(bot, channel_id, venue_name),
        )
        timer.daemon = True
        _hub_timers[channel_id] = {"timer": timer, "codes": codes}
        timer.start()


def _fire_hub_refresh(bot, channel_id, venue_name):
    with _hub_timers_lock:
        state = _hub_timers.pop(str(channel_id), None)
        codes = set((state or {}).get("codes") or ())
    venue_config = {"venue_id": PLATE_VENUE_ID, "venue_name": venue_name}
    try:
        post_full_roster(bot, channel_id, venue_config)
    except Exception:
        logger.exception("名玺防抖刷新全部名单失败 chat=%s", channel_id)
    attendance = load_latest_mingxi_attendance()
    duty_codes = {
        str(code).strip()
        for code in (attendance or {}).get("codes") or []
        if str(code).strip()
    }
    for group in (attendance or {}).get("attendance_groups") or []:
        for code in group.get("technician_codes") or []:
            if str(code).strip():
                duty_codes.add(str(code).strip())
    if not duty_codes:
        return
    if codes and not (codes & duty_codes):
        return
    try:
        refresh_complete_attendance_links(bot, channel_id, venue_name)
    except Exception:
        logger.exception("名玺防抖刷新出勤完整版失败 chat=%s", channel_id)


def post_complete_attendance_reply(bot, message, venue_config, attendance):
    """初稿入库后：公开发完整版挂底；成功则删除频道初稿。"""
    if venue_config.get("venue_id") != PLATE_VENUE_ID:
        return None
    sync_album_from_matched_photos(message.chat.id)
    codes = list(attendance.get("codes") or [])
    for group in attendance.get("attendance_groups") or []:
        codes.extend(group.get("technician_codes") or [])
    album_urls, _msg_ids = resolve_album_links(
        PLATE_VENUE_ID, codes, channel_id=message.chat.id
    )
    venue_name = venue_config.get("venue_name") or "名玺水汇会所"
    text, markup = build_complete_attendance_content(
        venue_name,
        attendance,
        message.chat.id,
        album_urls=album_urls,
    )
    draft_id = getattr(message, "message_id", None)
    try:
        sent = publish_complete_attendance(
            bot, message.chat.id, text, reply_markup=markup
        )
        complete_id = getattr(sent, "message_id", None)
        logger.info(
            "名玺出勤完整版已发布 draft=%s complete=%s links=%s missing=%s",
            draft_id,
            complete_id,
            len(album_urls),
            len(set(str(c).strip() for c in codes if str(c).strip())) - len(album_urls),
        )
        if complete_id and draft_id:
            _delete_channel_message(bot, message.chat.id, draft_id)
        return sent
    except telebot.apihelper.ApiTelegramException:
        logger.exception(
            "名玺出勤完整版发送失败 chat=%s draft=%s",
            message.chat.id,
            draft_id,
        )
        return None
