# -*- coding: utf-8 -*-
"""Rozpoznání dotazu a slučování výsledků podle ověřitelných shod."""
import re
from difflib import SequenceMatcher
from hashlib import sha256

from nameparser import normalize, parse

EPISODE_SUFFIX = re.compile(r"(?:[\s._-]+)(?:s(\d{1,2})[ ._-]*e(\d{1,3})|(\d{1,2})x(\d{1,3}))\s*$", re.I)
YEAR_SUFFIX = re.compile(r"[\s._-]+\(?((?:18|19|20)\d{2})\)?\s*$")
EXTRAS = {"trailer", "teaser", "ukazka", "upoutavka"}
DECADE_SUFFIX = re.compile(r"(?:^| )(?:z )?((?:18|19|20)?[0-9]0) (?:let|leta|letech)$")
GENRES = {
    "28": (30545, {"akcni"}), "12": (30546, {"dobrodruzny", "dobrodruzne"}),
    "16": (30547, {"animovany", "animovane", "animaky"}),
    "35": (30548, {"komedie", "komedii", "komedialni"}),
    "80": (30549, {"krimi", "kriminalni"}), "99": (30550, {"dokument", "dokumenty", "dokumentarni"}),
    "18": (30551, {"drama", "dramata", "dramaticke"}), "27": (30552, {"horor", "horory", "hororove"}),
    "10749": (30553, {"romanticke", "romanticky", "romantika"}),
    "878": (30554, {"sci fi", "vedeckofantasticke"}), "53": (30555, {"thriller", "thrillery"}),
    "10751": (30556, {"rodinne"}), "10752": (30557, {"valecne", "valecny"}),
    "14": (30558, {"fantasy"}), "36": (30559, {"historicke", "historicky"}),
    "9648": (30560, {"mysteriozni"}), "10402": (30561, {"hudebni", "muzikaly", "muzikal"}),
    "37": (30562, {"western", "westerny"}), "10770": (30563, {"televizni"}),
}


def is_preview(filename, titles=()):
    words = set(normalize(filename).split())
    known = set(normalize(" ".join(title for title in titles if title)).split())
    return bool(EXTRAS & (words - known))


def matching_title(items, title, year=0, episodic=False):
    normalized = normalize(title)
    matched = {}
    for item in items:
        kind = item.get("media_type")
        if kind not in ("movie", "tv") or (episodic and kind != "tv"):
            continue
        localized = item.get("title") or item.get("name") or ""
        original = item.get("original_title") or item.get("original_name") or ""
        date = item.get("release_date") or item.get("first_air_date") or ""
        if item.get("id") and normalized in aliases(localized, original) and (not year or date[:4] == str(year)):
            matched[kind, item["id"]] = item
    return next(iter(matched.values())) if len(matched) == 1 else None


def split_query(query):
    title = query.strip()
    episode = EPISODE_SUFFIX.search(title)
    position = None
    if episode:
        season, number = episode.groups()[:2] if episode.group(1) else episode.groups()[2:]
        if int(number) > 0:
            position = int(season), int(number)
            title = title[:episode.start()]
    found_year = YEAR_SUFFIX.search(title)
    year = int(found_year.group(1)) if found_year else 0
    if found_year:
        title = title[:found_year.start()]
    return title.strip() or query, year, position


def format_query(title, year=0, position=None):
    if year:
        title += " %d" % year
    if position:
        title += " S%02dE%02d" % position
    return title


def period(value):
    if re.fullmatch(r"(?:18|19|20)\d{2}", value):
        return int(value), int(value)
    if re.fullmatch(r"(?:18|19|20)\d0s", value):
        start = int(value[:-1])
        return start, start + 9
    return None, None


def discover_query(query, countries):
    """Uzná dotaz pouze tehdy, když lze celý text převést na alespoň dva filtry."""
    name = normalize(query)
    match = DECADE_SUFFIX.search(name)
    year = ""
    if match:
        value = int(match[1])
        start = value + (1900 if value >= 20 else 2000) if len(match[1]) == 2 else value
        year = "%ds" % start
        name = name[:match.start()]
    else:
        match = re.search(r"(?:^| )(?:z roku |z )?((?:18|19|20)\d{2})$", name)
        if match:
            year = match[1]
            name = name[:match.start()]
    name = re.sub(r"\b(?:filmy|film|filmu)\b", " ", name)
    name = " ".join(name.split())
    origin = ""
    for alias in sorted(countries, key=lambda value: (-len(value), value)):
        remaining, count = re.subn(r"\b" + re.escape(alias) + r"\b", "", name, count=1)
        if count:
            origin = countries[alias]
            name = " ".join(remaining.split())
            break
    genre = next((code for code, (_label, names) in GENRES.items() if name in names), "")
    return ({"origin": origin, "genre": genre, "year": year}
            if (not name or genre) and sum(bool(value) for value in (origin, genre, year)) >= 2 else {})


def spelling_suggestions(title, names, limit=2):
    target = normalize(title)
    if len(target) < 4:
        return []
    matches = {}
    for name in names:
        candidate = normalize(name)
        if candidate == target or abs(len(candidate) - len(target)) > 3 or candidate in matches:
            continue
        score = SequenceMatcher(None, target, candidate).ratio()
        if score >= .79:
            matches[candidate] = (score, name)
    return [name for _score, name in sorted(matches.values(), key=lambda item: (-item[0], item[1]))[:limit]]


def compare_title(title, names):
    wanted = normalize(title)
    if wanted in names:
        return 0
    if any(name.startswith(wanted + " ") for name in names):
        return 1
    return 2


def aliases(*names):
    return set(filter(None, map(normalize, names)))


def filename_names(names):
    single = aliases(*names)
    return single | {first + " " + second for first in single for second in single if first != second}


def filename_matches(parsed, names, year=0, position=None, kind=None, *, require_year=False):
    if normalize(parsed["title"]) not in names or (kind and parsed["kind"] != kind):
        return False
    if year and (parsed["year"] != year and (parsed["year"] or require_year)):
        return False
    return not position or (parsed["season"], parsed["episode"]) == position


def new_card(kind, title, year=0, position=None, names=(), local=None, videos=(), tmdb=None):
    return dict(kind=kind, title=title, year=year, position=position, names=aliases(title, *names),
                local=local, videos=list(videos), tmdb=tmdb, provider=[])


def remote_card(item, kind, position):
    title = item.get("title") or item.get("name")
    if not title or not item.get("id"):
        return None
    original = item.get("original_title") or item.get("original_name")
    date = item.get("release_date") or item.get("first_air_date") or ""
    year = int(date[:4]) if date[:4].isdigit() else 0
    card = new_card(kind, title, year, position, (original, *item.get("_search_aliases", ())), tmdb=item)
    card["names"] = filename_names(card["names"])
    return card


def _same_title(card, candidate):
    if card["kind"] != candidate["kind"] or card["position"] != candidate["position"]:
        return False
    if card["year"] and candidate["year"] and card["year"] != candidate["year"]:
        return False
    # U filmů bez roku nestačí ani zcela stejný název.
    if card["kind"] == "movie" and not (card["year"] and candidate["year"]):
        return False
    return not card["names"].isdisjoint(candidate["names"])


def _tmdb_match(cards, candidate, tmdb_cards):
    tmdb_id = candidate["tmdb"]["id"]
    of_kind = [card for card in cards if card["kind"] == candidate["kind"]]
    exact = [card for card in of_kind if (card["local"] or {}).get("tmdb_id") == tmdb_id
             or (card["tmdb"] or {}).get("id") == tmdb_id]
    if exact:
        return exact[0] if len(exact) == 1 else None
    matches = [card for card in of_kind if not card["tmdb"] and not (card["local"] or {}).get("tmdb_id")
               and card["year"] and candidate["year"] and _same_title(card, candidate)]
    if len(matches) != 1:
        return None
    local = matches[0]
    possible_ids = {entry["tmdb"]["id"] for entry in tmdb_cards if _same_title(local, entry)}
    return local if possible_ids == {tmdb_id} else None


def combine(cards, tmdb_items, provider_items, position, video_key):
    cards = list(cards)
    tmdb_cards = []
    for item in tmdb_items:
        kind = item.get("media_type")
        if kind not in ("movie", "tv") or (position and kind == "movie"):
            continue
        candidate = remote_card(item, kind, position)
        if candidate:
            tmdb_cards.append(candidate)
    for candidate in tmdb_cards:
        match = _tmdb_match(cards, candidate, tmdb_cards)
        if match:
            match["tmdb"] = candidate["tmdb"]
            match["names"].update(candidate["names"])
        else:
            cards.append(candidate)

    files = {video_key(video["id"]): card for card in cards for video in card["videos"]}
    for video in provider_items:
        identity = video_key(video["id"])
        if identity in files:
            card = files[identity]
            if not any(video_key(item["id"]) == identity for item in card["provider"]):
                card["provider"].append(video)
            continue
        parsed = parse(video["title"])
        kind = "tv" if parsed["kind"] == "episode" else "movie"
        ep = (parsed["season"], parsed["episode"]) if parsed["episode"] else None
        if position and (kind != "tv" or ep != position):
            continue
        title = parsed["title"] or video["title"]
        candidate = new_card(kind, title, parsed["year"], position)
        is_extra = is_preview(video["title"], (title,))
        matches = [card for card in cards if _same_title(card, candidate)] if not is_extra else []
        if len(matches) == 1:
            card = matches[0]
        else:
            card = candidate
            if is_extra:
                card["title"] = video["title"]
            cards.append(card)
        files[identity] = card
        card["provider"].append(video)
    return cards


def card_id(card):
    if card["local"]:
        key = "local:" + card["local"]["key"]
    elif card["tmdb"]:
        key = "%s:%d" % (card["kind"], card["tmdb"]["id"])
    else:
        key = "provider:" + card["provider"][0]["id"]
    return sha256(key.encode("utf-8")).hexdigest()[:20]
