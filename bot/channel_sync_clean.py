import re


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
