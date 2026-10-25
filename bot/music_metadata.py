"""Conservative metadata recovery from channel captions and source filenames."""
import re
import unicodedata

UNKNOWN = {"", "<unknown>", "unknown", "未知歌手"}
CAPTION = re.compile(r"今日歌单\s*/\s*[·•]\s*(.+?)\s*☁", re.DOTALL)
NOISE = re.compile(
    r"(?:official\s*(?:music\s*)?(?:video|audio|mv)|m/v|hd\s*高清|"
    r"高清官方|mv官方|动态歌词|動態歌詞|歌词版|歌詞版|歌詞|歌词|"
    r"lyric(?:s|\s*video)?|color\s*coded|vietsub|pinyin)", re.IGNORECASE,
)


def clean_title(value):
    value = re.sub(r"^\s*\d{1,3}[.、]\s*", "", value or "").strip()
    value = re.split(r"[『「]", value, maxsplit=1)[0].strip()
    value = re.sub(r"[\[【(（]([^\]】)）]*)[\]】)）]",
                   lambda m: "" if NOISE.search(m[1]) or m[1].strip().casefold() == "audio" else m[0], value)
    marker = NOISE.search(value)
    if marker:
        value = value[:marker.start()]
    value = re.sub(r"(?:完整放映|MV完整版)\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*\|\s*@\w+.*$", "", value)
    value = value.strip(" \t_-|♪")
    for left, right in [("《", "》"), ("〈", "〉"), ("【", "】"), ("'", "'"), ('"', '"')]:
        if value.startswith(left) and value.endswith(right):
            value = value[len(left):-len(right)].strip()
    return value.strip()


def comparable(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def matches_title(value, anchor):
    value = clean_title(value)
    return comparable(value) == comparable(anchor) or comparable(
        re.sub(r"[（(][^()（）]*(?:版|remix|live|acoustic|slowed|reverb)[^()（）]*[)）]", "", value,
               flags=re.IGNORECASE)
    ) == comparable(anchor)


def clean_artist(value):
    value = (value or "").strip()
    if value.casefold() in UNKNOWN:
        return ""
    value = re.sub(r"^[【\[](?:歌詞|歌词|繁體中字|繁体中字)[】\]]\s*", "", value)
    value = re.split(r"[《【〈\[]", value, maxsplit=1)[0].strip()
    value = re.split(r"\s+_\s+", value, maxsplit=1)[0].strip()
    if NOISE.search(value) or re.search(r"(?:说唱|說唱).*EP\d|iQIYI|抖音", value, re.IGNORECASE):
        return ""
    return value


def candidate_artist(value):
    value = re.sub(r"^(?:【(?:LIVE|纯享|純享)】|纯享[丨：]|純享[丨：])", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\S*官方\s+", "", value)
    value = clean_artist(value.strip(" _-–—"))
    if (not value or len(value) > 60 or re.search(
            r"^[（(]|^(?:ft\.?|feat\.?)\s|原唱|倾情演唱|傾情演唱|@|[【《〈]", value, re.IGNORECASE)):
        return ""
    return re.sub(r"\b([A-Za-z])_([A-Za-z]+)\b", r"\1-\2", value).replace("_", " ")


def identify(title, artist, filename="", caption=""):
    """Require a matching caption/tag title before splitting ambiguous artist/title pairs."""
    title = (title or "").strip()
    artist = clean_artist(artist)
    filename = re.sub(r"\.(?:mp3|m4a|wav|ogg)$", "", filename or "", flags=re.IGNORECASE)
    caption_match = CAPTION.search(caption or "")
    anchor = caption_match[1].strip() if caption_match else ""
    result_title = clean_title(title) or clean_title(filename) or "未命名歌曲"
    for candidate in (title, filename):
        cleaned = clean_title(candidate)
        # Bracketed video titles explicitly separate the performer and song.
        bracket = re.match(r"^(.+?)[【《〈]([^】》〉]+)[】》〉]", candidate)
        if bracket and (not artist or len(artist) == 1) and not NOISE.search(bracket[2]):
            recovered = candidate_artist(bracket[1])
            if len(recovered) > 1:
                artist = recovered
                result_title = clean_title(bracket[2])
                break
        pair = re.split(r"\s+[-–—]\s+", cleaned, maxsplit=1)
        if len(pair) == 2 and anchor:
            if matches_title(pair[0], anchor) and (not artist or len(artist) == 1) and candidate_artist(pair[1]):
                artist, result_title = candidate_artist(pair[1]), clean_title(pair[0])
                break
            if matches_title(pair[1], anchor) and candidate_artist(pair[0]):
                artist, result_title = candidate_artist(pair[0]), clean_title(pair[1])
                break
        # Multi-space separators are common in original music filenames.
        pair = re.split(r"\s{2,}", cleaned, maxsplit=1)
        if len(pair) == 2 and anchor and comparable(pair[0]) == comparable(anchor) and not artist:
            artist, result_title = clean_artist(pair[1]), pair[0]
            break
    # Recover an artist adjacent to an independently known song title in a filename.
    if not artist or len(artist) == 1:
        spaced = filename.replace("_", " ")
        spaced = re.sub(r"\s+(?:\d{2,3}k|\d{3})$", "", spaced, flags=re.IGNORECASE)
        spaced = clean_title(spaced)
        for known_title in (anchor, result_title):
            if not known_title or comparable(known_title) == comparable(spaced):
                continue
            pattern = r"\s*".join(re.escape(char) for char in known_title if not char.isspace())
            match = re.search(pattern, spaced, re.IGNORECASE)
            if not match:
                continue
            before, after = spaced[:match.start()].strip(" -–—"), spaced[match.end():].strip(" -–—")
            adjacent = before if before and not after else after if after and not before else ""
            recovered = candidate_artist(adjacent)
            if recovered and 1 < len(recovered) <= 40 and len(recovered.split()) <= 3:
                artist = recovered
                break
    if anchor:
        # Keep explicit version qualifiers from tags when the caption omits them.
        version = re.search(r"[（(]([^()（）]*(?:版|remix|live|acoustic|slowed|reverb)[^()（）]*)[)）]",
                            result_title, re.IGNORECASE)
        if not version:
            version = re.search(r"[（(]([^()（）]*(?:版|remix|live|acoustic|slowed|reverb)[^()（）]*)[)）]",
                                clean_title(filename.replace("_", " ")), re.IGNORECASE)
        plain_tag = clean_title(title)
        # A clean standalone tag is stronger evidence than a conflicting caption typo.
        if not (plain_tag and comparable(plain_tag) != comparable(anchor)
                and not re.search(r"[\u3400-\u9fff]", plain_tag)
                and not re.search(r"[-_【《〈『「]| {2,}", plain_tag)
                and not re.search(r"[（(]", plain_tag)):
            result_title = anchor
        if version and comparable(version[0]) not in comparable(anchor):
            result_title += version[0]
    return result_title[:120], (artist or "未知歌手")[:120]
