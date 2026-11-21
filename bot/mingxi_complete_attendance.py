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
    sync_album_from_matched_photos,
    upsert_album_message,
)
from venue_pricing import mingxi_board_price_suffix

logger = logging.getLogger(__name__)

PLATE_VENUE_ID = 2
COMPLETE_MARKER = "【出勤完整版】"
FULL_ROSTER_MARKER = "【人员全部名单】"
ENV_NAV_MARKER = "【环境导航】"
PROMO_MARKER = "【优惠】"
FULL_ROSTER_TRIGGERS = ("全部名单", "全员名单", "人员全部名单")
# 固定入口：出勤/全员只链这里，不跟发整页导航（避免刷屏 + 实体上限）
ENV_NAV_MSG_ID = 10503
ENV_NAV_URL = f"https://t.me/mingxi03/{ENV_NAV_MSG_ID}"
ATTENDANCE_MSG_ID = 10505  # 回退；发出勤后会写入锚点
FULL_ROSTER_MSG_ID = 10506  # 回退；发名单后会写入锚点
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
        or PROMO_MARKER in value
    )


def is_promo_text(text: str) -> bool:
    return PROMO_MARKER in (text or "")


def _public_msg_url(msg_id) -> str:
    return f"https://t.me/mingxi03/{int(msg_id)}"


def _attendance_nav_url() -> str:
    msg_id = (
        get_channel_anchor(MINGXI_CHANNEL_ID, ANCHOR_ATTENDANCE) or ATTENDANCE_MSG_ID
    )
    return _public_msg_url(msg_id)


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
        parts.append(
            f'<a href="{html.escape(_attendance_nav_url(), quote=True)}">'
            f"{html.escape(COMPLETE_MARKER)}</a>"
        )
    if "roster" not in skip:
        parts.append(
            f'<a href="{html.escape(_roster_nav_url(), quote=True)}">'
            f"{html.escape(FULL_ROSTER_MARKER)}</a>"
        )
    if not parts:
        return []
    return ["导航：" + "  |  ".join(parts)]


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


def publish_complete_attendance(
    bot,
    channel_id,
    text,
    *,
    reply_to_message_id=None,
):
    """发布今日出勤完整版：删旧帖、发新帖、记录锚点并置顶（永远挂着最新一条）。"""
    channel_id = int(channel_id)
    old_id = get_channel_anchor(channel_id, ANCHOR_ATTENDANCE)
    kwargs = {
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": footer_keyboard(),
    }
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
        kwargs["allow_sending_without_reply"] = True
    sent = bot.send_message(channel_id, text, **kwargs)
    new_id = getattr(sent, "message_id", None)
    if new_id:
        set_channel_anchor(channel_id, ANCHOR_ATTENDANCE, new_id)
        if old_id and int(old_id) != int(new_id):
            _delete_channel_message(bot, channel_id, old_id)
        _pin_channel_message(bot, channel_id, new_id)
        # 环境导航继续挂着（频道可多置顶时两者并存）
        env_id = get_channel_anchor(channel_id, ANCHOR_ENV_NAV) or ENV_NAV_MSG_ID
        if env_id and int(env_id) != int(new_id):
            _pin_channel_message(bot, channel_id, env_id)
        refresh_cross_nav_messages(bot, channel_id)
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


def _format_board_heading(group_name):
    """牌名 + 后台价目表价格，如 9号牌 · 半价$249 · 会员$499。"""
    name = str(group_name or "名单")
    suffix = mingxi_board_price_suffix(name)
    return f"<b>{html.escape(name)}{html.escape(suffix)}</b>"


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


def refresh_cross_nav_messages(bot, channel_id, venue_name="名玺水汇会所"):
    """出勤/全员/环境互相导航：锚点变化后刷新另外两条文末链接。"""
    channel_id = int(channel_id)
    env_id = get_channel_anchor(channel_id, ANCHOR_ENV_NAV) or ENV_NAV_MSG_ID
    roster_id = get_channel_anchor(channel_id, ANCHOR_ROSTER) or FULL_ROSTER_MSG_ID
    _edit_hub_message(bot, channel_id, env_id, build_env_nav_html(venue_name))
    _edit_hub_message(
        bot,
        channel_id,
        roster_id,
        build_full_roster_html(venue_name, channel_id),
    )


def post_env_nav(bot, channel_id, venue_name="名玺水汇会所", reply_to_message_id=None):
    """发布环境导航独立消息（仅初次/重建用；日常应 edit + pin）。"""
    text = build_env_nav_html(venue_name)
    kwargs = {
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": footer_keyboard(),
    }
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
        kwargs["allow_sending_without_reply"] = True
    try:
        return bot.send_message(channel_id, text, **kwargs)
    except telebot.apihelper.ApiTelegramException:
        logger.exception("名玺环境导航发送失败 chat=%s", channel_id)
        return None


def sync_env_nav_message(bot, channel_id=None, venue_name="名玺水汇会所", msg_id=ENV_NAV_MSG_ID):
    """就地更新固定环境导航帖，并尽量置顶。"""
    channel_id = int(channel_id or MINGXI_CHANNEL_ID)
    text = build_env_nav_html(venue_name)
    try:
        bot.edit_message_text(
            text,
            channel_id,
            int(msg_id),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=footer_keyboard(),
        )
    except telebot.apihelper.ApiTelegramException as exc:
        # 内容未变时 Telegram 会报错，可忽略
        if "message is not modified" not in str(exc).lower():
            logger.exception("名玺环境导航编辑失败 msg=%s", msg_id)
            return False
    set_channel_anchor(channel_id, ANCHOR_ENV_NAV, msg_id)
    _pin_channel_message(bot, channel_id, msg_id)
    return True


def build_complete_attendance_html(
    venue_name,
    attendance,
    channel_id,
    album_urls=None,
    album_msg_ids=None,
):
    """生成完整版 HTML（含标记，避免被再次当初稿解析）。环境导航另条发送。"""
    album_urls = dict(album_urls or {})
    # 兼容旧参数：仅有 msg_id 时回退为频道深链
    for code, msg_id in (album_msg_ids or {}).items():
        album_urls.setdefault(str(code), message_link(channel_id, msg_id))
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

    lines.append("点工号跳转到频道相册对应照片。")
    lines.extend(_hub_footer_lines(exclude=("attendance",)))
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
        lines.extend(_hub_footer_lines(exclude=("roster",)))
        return "\n".join(lines).rstrip() + "\n"

    for group in group_roster_codes(codes):
        lines.append(_format_board_heading(group["group_name"]))
        chips = [
            _format_code(code, album_urls.get(code))
            for code in group["technician_codes"]
        ]
        lines.append("  ".join(chips))
        lines.append("")
    lines.append("本名单为频道相册全员目录。")
    lines.extend(_hub_footer_lines(exclude=("roster",)))
    return "\n".join(lines).rstrip() + "\n"


def build_promo_html(venue_name, body, *, valid_until=None):
    """优惠独立公告（与出勤/全员分开，勿夹在名单里）。"""
    lines = [
        PROMO_MARKER,
        f"{html.escape(venue_name)} · 优惠",
    ]
    if valid_until:
        lines.append(f"有效期至 {html.escape(str(valid_until))}")
    lines.append("")
    lines.append(html.escape((body or "").strip()) or "（内容待补充）")
    lines.append("")
    lines.extend(_hub_footer_lines())
    return "\n".join(lines).rstrip() + "\n"


def post_full_roster(bot, channel_id, venue_config, reply_to_message_id=None):
    """发布名玺人员全部名单（独立于今日出勤）；不跟发环境导航。"""
    if venue_config.get("venue_id") != PLATE_VENUE_ID:
        return None
    venue_name = venue_config.get("venue_name") or "名玺水汇会所"
    text = build_full_roster_html(venue_name, channel_id)
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
        new_id = getattr(sent, "message_id", None)
        if new_id:
            set_channel_anchor(channel_id, ANCHOR_ROSTER, new_id)
            refresh_cross_nav_messages(
                bot, channel_id, venue_name
            )
        logger.info(
            "名玺全部名单已发布 chat=%s msg=%s",
            channel_id,
            new_id,
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
    sync_album_from_matched_photos(message.chat.id)
    codes = list(attendance.get("codes") or [])
    for group in attendance.get("attendance_groups") or []:
        codes.extend(group.get("technician_codes") or [])
    album_urls, _msg_ids = resolve_album_links(
        PLATE_VENUE_ID, codes, channel_id=message.chat.id
    )
    venue_name = venue_config.get("venue_name") or "名玺水汇会所"
    text = build_complete_attendance_html(
        venue_name,
        attendance,
        message.chat.id,
        album_urls=album_urls,
    )
    try:
        sent = publish_complete_attendance(
            bot,
            message.chat.id,
            text,
            reply_to_message_id=message.message_id,
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
    sync_album_from_matched_photos(channel_id)
    album_urls, _msg_ids = resolve_album_links(
        PLATE_VENUE_ID, codes, channel_id=channel_id
    )
    venue_name = venue_config.get("venue_name") or "名玺水汇会所"
    text = build_complete_attendance_html(
        venue_name,
        attendance,
        channel_id,
        album_urls=album_urls,
    )
    return publish_complete_attendance(
        bot,
        channel_id,
        text,
        reply_to_message_id=reply_to_message_id,
    )
