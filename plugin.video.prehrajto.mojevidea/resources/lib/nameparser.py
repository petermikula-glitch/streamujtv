# -*- coding: utf-8 -*-
"""Rozbor názvů nahraných videí.

Z názvu typu ``Pernikovy.tata.S02E05.CZ.dabing.1080p.mkv`` nebo
``Matrix (1999) CZ tit`` vytáhne název, rok, sérii/díl, jazyk a kvalitu,
aby šlo videa roztřídit na filmy a seriály a dohledat je na ČSFD.
"""
import re
import time
import unicodedata

EXTENSIONS = r"(?:mkv|mp4|avi|m4v|mov|wmv|mpg|mpeg|ts|flv|webm)"

# značky, za kterými už název nepokračuje
_TAG_WORDS = [
    r"2160p", r"1080p", r"1080i", r"720p", r"576p", r"480p", r"4k", r"uhd", r"fhd", r"hd", r"sd",
    r"hdr10?", r"dv", r"bluray", r"blu-ray", r"bdrip", r"brrip", r"bdremux", r"remux", r"webrip",
    r"web-?dl", r"web", r"hdtv", r"tvrip", r"dvdrip", r"dvd", r"hdrip", r"camrip", r"cam", r"ts",
    r"kinorip", r"x264", r"x265", r"h\.?264", r"h\.?265", r"hevc", r"avc", r"xvid", r"divx",
    r"aac", r"ac3", r"eac3", r"dts", r"dts-hd", r"truehd", r"atmos", r"(?:dd\+?|ddp)?[257][01]ch",
    r"h26[45]",
    r"cz", r"sk", r"en", r"eng", r"cze", r"svk", r"czech", r"slovak", r"english",
    r"dab(?:ing)?", r"dabbing", r"cz-?dab(?:ing)?", r"czdab", r"sk-?dab(?:ing)?", r"tit(?:ulky)?",
    r"cz-?tit(?:ulky)?", r"cztit", r"sk-?tit(?:ulky)?", r"sktit", r"subs?", r"multi", r"dual",
    r"proper", r"repack", r"extended", r"unrated", r"directors\.?cut", r"remastered", r"limited",
    r"internal", r"complete",
]
_TAG_WORD_RE = re.compile(r"(?:%s)\Z" % "|".join(_TAG_WORDS), re.I)
# Značky, které jsou zároveň běžná slova názvů (Johnny English, Charlotte's Web, La vie en rose,
# A Complete Unknown). Název ukončí jen tehdy, když za nimi až do konce následují samé značky.
_AMBIGUOUS_TAGS = frozenset((
    "english", "czech", "slovak", "en", "web", "cam", "complete", "limited", "internal", "extended",
    "proper", "dual", "multi", "sub", "subs",
))

# hranice značek dílů: "(S01E02)", "S01E02:", "- S01E02,"
_B = r"(?:^|[\s(\-])"
_E = r"(?=[\s,:;)\-]|$)"

_EP_PATTERNS = [
    # S01E02, S1E2, S01 E02, S01E02E03, S01E02-E03
    re.compile(_B + r"s(\d{1,2})\s?e(\d{1,4})(?:\s?-?\s?e\d{1,4})?" + _E, re.I),
    # 1x02
    re.compile(_B + r"(\d{1,2})x(\d{1,4})" + _E, re.I),
    # 1. série 2. díl / 1 serie 2 dil / série 1 díl 2
    re.compile(r"(?:^|\s)(\d{1,2})\s?\.?\s?s[eé]rie\s+(\d{1,4})\s?\.?\s?d[ií]l", re.I),
    re.compile(r"(?:^|\s)s[eé]rie\s?(\d{1,2})\s+d[ií]l\s?(\d{1,4})", re.I),
    re.compile(r"(?:^|\s)(\d{1,2})\s?\.?\s?sezona\s+(\d{1,4})\s?\.?\s?(?:d[ií]l|epizoda)", re.I),
    re.compile(r"(?:^|\s)season\s?(\d{1,2})\s+episode\s?(\d{1,4})", re.I),
]
# jen díl bez série → 1. série (denní seriály mají i čtyřmístná čísla dílů)
_EP_ONLY_PATTERNS = [
    re.compile(_B + r"e[pP]?(\d{1,4})" + _E, re.I),
    re.compile(r"(?:^|\s)(\d{1,4})\s?\.?\s?d[ií]l" + _E, re.I),
    re.compile(r"(?:^|\s)d[ií]l\s?(\d{1,4})" + _E, re.I),
    re.compile(r"(?:^|\s)epizoda\s?(\d{1,4})" + _E, re.I),
    re.compile(r"(?:^|\s)episode\s?(\d{1,4})" + _E, re.I),
]
# celá série bez dílu ("S02", "2. série")
_SEASON_ONLY = re.compile(r"(?:^|\s)(?:s(\d{1,2})|(\d{1,2})\s?\.?\s?s[eé]rie)(?=\s|$)", re.I)

_YEAR_RE = re.compile(r"(?:^|[\s(\[])((?:19[2-9]|20[0-4])\d)(?=[\s)\]]|$)")


def _cut_at_tags(title, anchored):
    """Zkrátí název u první technické nebo jazykové značky.

    ``anchored`` = název už končí u roku nebo čísla dílu; text před nimi je téměř vždy
    čistý název, takže dvojznačná slova (english, web…) ho neukončují."""
    words = title.split(" ")
    mixed_case = any(c.islower() for c in title)
    for index in range(1, len(words)):
        word = words[index].lower()
        if not _TAG_WORD_RE.match(word):
            continue
        # "Film EN", "Film WEB" – značka psaná verzálkami v jinak běžně psaném názvu je značka
        shouting = mixed_case and words[index].isupper()
        if word in _AMBIGUOUS_TAGS and not shouting:
            rest = [w.lower() for w in words[index + 1:] if w]
            if anchored or not rest or not all(_TAG_WORD_RE.match(w) for w in rest):
                continue
        return " ".join(words[:index])
    return title


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFD", text or "")
                   if unicodedata.category(c) != "Mn")


def normalize(text):
    """Klíč pro porovnávání názvů: malá písmena, bez diakritiky a interpunkce."""
    text = strip_accents(text or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tidy(text):
    text = re.sub(r"\s+", " ", text or "").strip(" -_.,:;|/[]()")
    return text.strip()


def _detect_lang(text):
    t = " " + text.lower() + " "
    dub = None
    subs = None
    if re.search(r"\s(?:cz|cze|czech)?\s?-?\s?dab(?:ing)?\s|\sczdab\s|\scz-dab", t):
        dub = "CZ"
    if re.search(r"\ssk\s?-?\s?dab(?:ing)?\s|\sskdab\s", t):
        dub = "SK"
    if re.search(r"\s(?:cz\s?-?\s?tit(?:ulky)?|cztit|cz\s?subs?)\s", t):
        subs = "CZ"
    elif re.search(r"\s(?:sk\s?-?\s?tit(?:ulky)?|sktit|sk\s?subs?)\s", t):
        subs = "SK"
    elif re.search(r"\stit(?:ulky)?\s", t):
        subs = "CZ"
    if dub is None and subs is None:
        if re.search(r"\s(?:cz|cze|czech)\s", t):
            dub = "CZ"
        elif re.search(r"\s(?:sk|svk|slovak)\s", t):
            dub = "SK"
        elif re.search(r"\s(?:en|eng|english)\s", t):
            dub = "EN"
    parts = []
    if dub:
        parts.append(dub + " dabing" if dub in ("CZ", "SK") else dub)
    if subs:
        parts.append(subs + " titulky")
    return ", ".join(parts)


def _detect_quality(text):
    t = text.lower()
    for pattern, label in ((r"2160p|\b4k\b|\buhd\b", "2160p"), (r"1080[pi]|\bfhd\b", "1080p"),
                           (r"720p|\bhd\b", "720p"), (r"576p|480p|\bsd\b|dvdrip", "SD")):
        if re.search(pattern, t):
            return label
    return ""


def parse(raw_title):
    """Vrací dict: kind ('movie'|'episode'), title, year, season, episode, lang, quality."""
    raw = raw_title or ""
    text = re.sub(r"\.%s$" % EXTENSIONS, "", raw.strip(), flags=re.I)
    no_spaces = " " not in text
    # tečky uvnitř technických značek (H.264, 5.1) se nesmí rozpadnout na čísla
    text = re.sub(r"(?i)\bh\.(26[45])\b", r"h\1", text)
    text = re.sub(r"(?i)\b(dd\+?|ddp)?([257])\.([01])\b", r"\1\2\3ch", text)
    # oddělovače → mezery
    text = re.sub(r"[_.]+", " ", text)
    # pomlčky místo mezer (Ta-nejlepsi-videa) – jen když v názvu nejsou mezery
    if no_spaces and "-" in text:
        text = re.sub(r"-+", " ", text)
    text = re.sub(r"\s-+\s|-{2,}", " ", text)
    text = re.sub(r"[\[\]{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    result = {"kind": "movie", "title": "", "year": 0, "season": 0, "episode": 0,
              "lang": _detect_lang(text), "quality": _detect_quality(text)}

    cut = len(text)
    marker = (0, 0)  # část textu s číslem dílu – čísla v ní nejsou rok
    for pat in _EP_PATTERNS:
        m = pat.search(text)
        if m:
            result["kind"] = "episode"
            result["season"] = int(m.group(1))
            result["episode"] = int(m.group(2))
            cut = min(cut, m.start())
            marker = m.span()
            break
    if result["kind"] == "movie":
        for pat in _EP_ONLY_PATTERNS:
            m = pat.search(text)
            if m and m.start() > 0:
                result["kind"] = "episode"
                result["season"] = 1
                result["episode"] = int(m.group(1))
                cut = min(cut, m.start())
                marker = m.span()
                break
    if result["kind"] == "movie":
        m = _SEASON_ONLY.search(text)
        if m and m.start() > 0:
            result["kind"] = "episode"
            result["season"] = int(m.group(1) or m.group(2))
            cut = min(cut, m.start())

    years = list(_YEAR_RE.finditer(text))
    latest = time.localtime().tm_year + 1  # "Blade Runner 2049" není film z roku 2049
    # rok, který není na začátku názvu (např. "2001 Vesmírná odysea" ponechá v názvu)
    for ym in reversed(years):
        start = ym.start(1)
        if start == 0 and len(text) > 4:
            continue
        if int(ym.group(1)) > latest or marker[0] <= start < marker[1]:
            continue
        result["year"] = int(ym.group(1))
        if result["kind"] == "movie" or start < cut:
            cut = min(cut, start)
        break

    title = _cut_at_tags(text[:cut], anchored=cut < len(text))
    title = re.sub(r"\(\s*\)", "", title)
    title = _tidy(title)
    if not title:
        title = _tidy(text) or raw.strip()
    result["title"] = title
    return result


def title_key(title):
    """Klíč názvu. Názvy bez latinky a číslic (azbuka…) by po normalizaci splynuly
    do jednoho prázdného klíče, proto se u nich použije název sám."""
    return normalize(title) or re.sub(r"\s+", " ", (title or "").casefold()).strip()


def show_key(title):
    return title_key(title)


def movie_key(title, year):
    return "%s|%s" % (title_key(title), year or "")
