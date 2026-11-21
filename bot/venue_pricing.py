"""Price data shared with the existing venue boards in the Mini App."""
import json
from decimal import Decimal
from pathlib import Path
import re

with (Path(__file__).resolve().parent.parent / "shared" / "venue-prices.json").open(encoding="utf-8") as source:
    PRICES = json.load(source)

DIHAO_CATEGORIES = {"0": "0牌", "1": "1牌", "2": "2牌", "3": "3牌", "6": "6牌艺人",
                    "7": "7牌公主", "8": "8牌佳丽", "9": "9牌贵妃"}


def dihao_category_for_photo(code, text):
    if code == "韩国08":
        return "韩国牌"
    labels = {"艺人": "6牌艺人", "公主": "7牌公主", "佳丽": "8牌佳丽", "贵妃": "9牌贵妃"}
    categories = set()
    for line in (text or "").splitlines():
        match = re.fullmatch(r"\s*(艺人|公主|佳丽|贵妃)\s*[:：;；]\s*\d{2,4}\s*", line)
        if match:
            categories.add(labels[match.group(1)])
    if len(categories) == 1:
        return next(iter(categories))
    if categories or not code:
        return None
    return DIHAO_CATEGORIES.get(str(code)[0])


# 出勤/名单分组名 → 价目表 board 名
_MINGXI_BOARD_ALIASES = {
    "日韩专场": "日韩牌",
    "日韩牌": "日韩牌",
    "俄罗斯": "7号牌",  # 7026 等归 7 牌计价
}


def resolve_mingxi_board_name(group_name):
    """出勤分组名对齐 shared/venue-prices.json 的 board。"""
    name = str(group_name or "").strip()
    if not name:
        return None
    if name in _MINGXI_BOARD_ALIASES:
        return _MINGXI_BOARD_ALIASES[name]
    for board in PRICES.get("mingxiBoardPrices") or []:
        if board.get("board") == name:
            return name
    return None


def mingxi_board_price_suffix(group_name):
    """牌名后的价签，如「 · 半价$249 · 会员$499」；无价则空串。"""
    board_name = resolve_mingxi_board_name(group_name)
    if not board_name:
        return ""
    board = next(
        (
            item
            for item in PRICES.get("mingxiBoardPrices") or []
            if item.get("board") == board_name
        ),
        None,
    )
    if not board:
        return ""
    parts = []
    if board.get("half") is not None:
        parts.append(f"半价${board['half']}")
    if board.get("member") is not None:
        parts.append(f"会员${board['member']}")
    if not parts:
        return ""
    return " · " + " · ".join(parts)


def mingxi_price_options(tags):
    return [
        {"board": board["board"], "minutes": minutes, "price_usd": str(board[field]),
         "label": label}
        for board in PRICES["mingxiBoardPrices"] if "board:" + board["board"] in tags
        for minutes, field, label in ((45, "half", "半价"), (90, "member", "会员"))
        if board.get(field) is not None
    ]


def staff_price(venue_id, code, tags, services):
    candidates = []
    if venue_id == 1:
        for service in services:
            if "service:" + service["category"] in tags and service["price"] is not None:
                candidates.append((Decimal(str(service["price"])), service["name"]))
    elif venue_id == 2:
        for board in PRICES["mingxiBoardPrices"]:
            if "board:" + board["board"] in tags and board.get("half") is not None:
                candidates.append((Decimal(str(board["half"])), f"{board['board']} · 45分钟"))
    else:
        key = {4: "kaidiTechnicianCategories", 5: "atlanticTechnicianCategories",
               6: "dihaoTechnicianCategories"}[venue_id]
        category = None
        if venue_id == 4:
            known = [tag[9:] for tag in tags if tag.startswith("category:")]
            category = known[0] if len(known) == 1 else (
                "车模" if code in {"6666", "7777"}
                else code[0] + "牌" if code.isdigit() and len(code) <= 3 else None
            )
        elif venue_id == 5:
            known = [tag[9:] for tag in tags if tag.startswith("category:")]
            category = known[0] if len(known) == 1 else None
        elif venue_id == 6:
            known = [tag[9:] for tag in tags if tag.startswith("category:")]
            category = known[0] if len(known) == 1 else dihao_category_for_photo(code, "")
        for item in PRICES[key]:
            if item["name"] == category and item.get("price") is not None:
                candidates.append((Decimal(str(item["price"])), f"{category} · 90分钟"))
    if not candidates:
        return None, "价格待确认"
    return min(candidates, key=lambda value: value[0])
