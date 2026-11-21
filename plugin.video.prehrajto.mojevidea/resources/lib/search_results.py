# -*- coding: utf-8 -*-
"""Rozpoznání dotazu a slučování výsledků podle ověřitelných shod."""
import re
from hashlib import sha256

from nameparser import normalize, parse

EPISODE_SUFFIX = re.compile(r"(?:[\s._-]+)(?:s(\d{1,2})[ ._-]*e(\d{1,3})|(\d{1,2})x(\d{1,3}))\s*$", re.I)
YEAR_SUFFIX = re.compile(r"[\s._-]+\(?((?:18|19|20)\d{2})\)?\s*$")
EXTRAS = {"trailer", "teaser", "ukazka", "upoutavka"}


def is_preview(filename, titles=()):
    words = set(normalize(filename).split())
    known = set(normalize(" ".join(title for title in titles if title)).split())
    return bool(EXTRAS & (words - known))


def original_title(items, title, year=0, episodic=False):
    normalized = normalize(title)
    matched = {}
    for item in items:
        kind = item.get("media_type")
        if kind not in ("movie", "tv") or (episodic and kind != "tv"):
            continue
        localized = item.get("title") or item.get("name") or ""
        original = item.get("original_title") or item.get("original_name") or ""
        date = item.get("release_date") or item.get("first_air_date") or ""
        if normalize(localized) == normalized and (not year or date[:4] == str(year)):
            matched[kind, item["id"]] = original
    if len(matched) != 1:
        return ""
    original = next(iter(matched.values()))
    return original if normalize(original) != normalized else ""


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


def compare_title(title, names):
    wanted = normalize(title)
    if wanted in names:
        return 0
    if any(name.startswith(wanted + " ") for name in names):
        return 1
    return 2


def aliases(*names):
    return set(filter(None, map(normalize, names)))


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
    return new_card(kind, title, year, position, (original,), tmdb=item)


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
