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
