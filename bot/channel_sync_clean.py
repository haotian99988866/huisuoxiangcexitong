import re
import unicodedata
from urllib.parse import urlsplit


PROMOTION = re.compile(
    r"关注大事件频道\s*[➡️→:\s]*@svip\s*投稿\s*[:：]?\s*@tx188",
    re.IGNORECASE,
)
LINK = re.compile(
    r"(?:https?://|tg://|www\.|(?:t|telegram)\.me/)[^\s<>]+"
    r"|(?<![\w@])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:[a-z]{2,63})(?::\d+)?(?:/[^\s<>]*)?",
    re.IGNORECASE,
)
TRAILING_LINK_PUNCTUATION = ".,!?;:，。；！？）)]】"
MAP_HOSTS = {
    "maps.app.goo.gl", "goo.gl", "maps.google.com", "google.com", "www.google.com",
    "maps.apple.com", "openstreetmap.org", "www.openstreetmap.org",
    "waze.com", "www.waze.com", "ul.waze.com", "here.com", "wego.here.com",
}
MENTION = re.compile(r"(?<![\w@])@[a-z0-9_]{3,32}", re.IGNORECASE)
CONTACT_ENTITIES = {
    "MessageEntityUrl", "MessageEntityTextUrl",
    "MessageEntityMention", "MessageEntityMentionName",
}

ADVERTISEMENT = re.compile(
    r"^[\s*_\#【\[]*(?:付费广告|付費廣告|商业广告|商業廣告|广告推广|廣告推廣|广告|廣告)"
    r"(?=$|[\s*_\#】\]:：])"
)


def is_map_url(value):
    """Allow only known map providers so unrelated links remain filtered."""
    candidate = (value or "").rstrip(TRAILING_LINK_PUNCTUATION)
    if "\\" in candidate or any(char.isspace() or ord(char) < 32 for char in candidate):
        return False
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        return False
    if hostname not in MAP_HOSTS:
        return False
    if hostname == "goo.gl" and not parsed.path.startswith("/maps"):
        return False
    if hostname == "google.com" or hostname.endswith(".google.com"):
        return hostname.startswith("maps.") or parsed.path.startswith("/maps")
    return True


def extract_map_urls(text):
    """Return unique, recognized map links from plain text."""
    urls = []
    for match in LINK.finditer(text or ""):
        value = match.group(0).rstrip(TRAILING_LINK_PUNCTUATION)
        if is_map_url(value) and value not in urls:
            urls.append(value if "://" in value else f"https://{value}")
    return urls


def is_advertisement(text):
    """Only treat an explicit leading advertisement label as an opt-out."""
    return bool(ADVERTISEMENT.match((text or "").lstrip()))


def is_news_advertisement(text):
    """Recognize promotional templates, not isolated words in news reports."""
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Cf")
    if is_advertisement(normalized):
        return True
    lines = [line.strip() for line in normalized.splitlines()
             if re.search(r"[a-zA-Z0-9\u3400-\u9fff]", line)]
    if not lines:
        return False
    heading = re.sub(r"^[^\w\u3400-\u9fff]+|^_+", "", lines[0])
    if re.search(r"警方|警惕|警告|提醒|查处|查獲|查获|破获|曝光|被捕|诈骗|詐騙|通报|通報|报道|報道", heading):
        return False
    if re.search(r"广告位(?:招商|招租)", heading):
        return True
    if (re.match(r"购买所有广告获得优惠", heading)
            and re.search(r"绑定优惠券", normalized)
            and re.search(r"官方机器人", normalized)
            and re.search(r"广告同步展示", normalized)):
        return True
    if (re.match(r"(?:支持\s*\d+\s*倍验资|注册就送|新人首存)", heading)
            and re.search(r"注册就送|首存.{0,20}赠送", normalized)
            and re.search(r"六合彩|百家乐|娱乐城", normalized)
            and re.search(r"返水|充值.{0,12}送|存款.{0,12}赠送", normalized)
            and re.search(r"注册网址|官方客服", normalized)):
        return True
    if (re.match(r"NO\s*钱包", heading, re.IGNORECASE)
            and re.search(r"雄厚实力护航", normalized)
            and re.search(r"官网权威验证", normalized)
            and re.search(r"免费能量赞助商", normalized)):
        return True
    if (re.match(r"频道互推(?:\s|#|$)", heading)
            and re.search(r"相似推荐|获得曝光|邀请奖励", normalized)):
        return True
    if (re.match(r"(?:在\s*Telegram\s*里找内容[,，]\s*先打开\s*SOSO\s*搜搜|"
                 r"Telegram\s*必备的搜索引擎[,，]\s*极搜)", heading, re.IGNORECASE)
            and re.search(r"频道|群组", normalized)
            and re.search(r"专属邀请链接|精准找到", normalized)):
        return True
    if (re.match(r"云购(?:入口|网址)\s*[:：]", heading)
            and "一元云购" in normalized
            and re.search(r"积分抽奖|兑现成\s*USDT", normalized, re.IGNORECASE)
            and re.search(r"官方客服|官方频道", normalized)):
        return True
    return bool(
        re.search(r"联合担保", normalized)
        and re.search(r"百家乐|六合彩|电子[|｜/、]棋牌", normalized)
        and re.search(r"诚招代理|打码返佣|起步返水", normalized)
    )


def clean_text(text, entities=()):
    """Remove contact spans using Telegram's UTF-16 offsets before editing text."""
    encoded = (text or "").encode("utf-16-le")
    spans = []
    for entity in entities or ():
        entity_type = type(entity).__name__
        if entity_type not in CONTACT_ENTITIES:
            continue
        start, end = entity.offset * 2, (entity.offset + entity.length) * 2
        display_text = encoded[start:end].decode("utf-16-le")
        target_url = getattr(entity, "url", None)
        if entity_type in {"MessageEntityUrl", "MessageEntityTextUrl"} and is_map_url(target_url or display_text):
            replacement = display_text
            if target_url and target_url not in display_text:
                replacement = f"{display_text} ({target_url})"
            spans.append((start, end, replacement.encode("utf-16-le")))
        else:
            spans.append((start, end, b""))
    for start, end, replacement in sorted(spans, reverse=True):
        encoded = encoded[:start] + replacement + encoded[end:]
    text = encoded.decode("utf-16-le")
    text = PROMOTION.sub("", text)
    text = LINK.sub(lambda match: match.group(0) if is_map_url(match.group(0)) else "", text)
    text = MENTION.sub("", text)
    # Removing entities first can leave the promotion's labels behind.
    text = re.sub(r"关注大事件频道\s*[➡️→:\s]*投稿\s*[:：]?", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clean_news_text(text, entities=()):
    text = clean_text(text, entities)
    while True:
        previous = text
        text = re.sub(
            r"(?:^|\n)[^\w\n]*出海导航[^\w\n]*投稿招商[^\w]*"
            r"#神盾联盟广告赞助商[^\w]*$",
            "", text,
        ).strip()
        text = re.sub(
            r"(?:^|\n)[^\w\n]*订阅东南亚大事件频道[^\w\n]*(?:\n[^\w\n]*)+投稿澄清爆料\s*[:：]?\s*$",
            "", text,
        ).strip()
        inline_footer = re.search(
            r"订阅东南亚大事件频道[^\w\n]*(?:\n[^\w\n]*)*投稿澄清爆料\s*[:：]?\s*$",
            text,
        )
        if inline_footer:
            text = re.sub(r"[\s👉➡️⬇️↓]+$", "", text[:inline_footer.start()]).strip()
        if text == previous:
            return text
