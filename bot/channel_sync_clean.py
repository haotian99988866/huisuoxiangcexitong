import re
import unicodedata


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
MENTION = re.compile(r"(?<![\w@])@[a-z0-9_]{3,32}", re.IGNORECASE)
CONTACT_ENTITIES = {
    "MessageEntityUrl", "MessageEntityTextUrl",
    "MessageEntityMention", "MessageEntityMentionName",
}

ADVERTISEMENT = re.compile(
    r"^[\s*_\#【\[]*(?:付费广告|付費廣告|商业广告|商業廣告|广告推广|廣告推廣|广告|廣告)"
    r"(?=$|[\s*_\#】\]:：])"
)


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
        re.match(r"[^。！？:：]{0,30}联合担保", heading)
        and re.search(r"百家乐|六合彩|电子[|｜/、]棋牌", normalized)
        and re.search(r"诚招代理|打码返佣|起步返水", normalized)
    )


def clean_text(text, entities=()):
    """Remove contact spans using Telegram's UTF-16 offsets before editing text."""
    encoded = (text or "").encode("utf-16-le")
    spans = sorted(
        (entity.offset * 2, (entity.offset + entity.length) * 2)
        for entity in (entities or ())
        if type(entity).__name__ in CONTACT_ENTITIES
    )
    for start, end in reversed(spans):
        encoded = encoded[:start] + encoded[end:]
    text = encoded.decode("utf-16-le")
    text = PROMOTION.sub("", text)
    text = LINK.sub("", text)
    text = MENTION.sub("", text)
    # Removing entities first can leave the promotion's labels behind.
    text = re.sub(r"关注大事件频道\s*[➡️→:\s]*投稿\s*[:：]?", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
