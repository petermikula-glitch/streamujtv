# -*- coding: utf-8 -*-
"""Pohodlí kolem katalogu: profily, úvodní panel, pokračování na domovské obrazovce televize,
stažení celé řady a zapamatovaná zvuková stopa.

Profily nemění tabulky katalogu (ty sdílí aplikace s doplňkem pro Kodi). Osobní stav – historie
přehrávání, oblíbené, předvolby – patří vždy právě aktivnímu profilu; při přepnutí se uloží do
souboru ``profile_<id>.json`` a nahradí ho stav profilu, na který se přepíná. Katalog, stažené
soubory a přihlášení jsou společné.
"""
import json
import os
import threading

import app_api as api
import app_discover

PROFILES = "profiles.json"
AVATARS = (("clapper", "Klapka"), ("popcorn", "Popcorn"), ("crown", "Koruna"), ("owl", "Sova"), ("cat", "Kočka"),
           ("rocket", "Raketa"))
TABLES = ("history", "watched_marks", "progress_hidden", "ivys_history", "ivys_favorites")
KV_PREFIXES = ("choice:", "last_season:", "recent_series", "tracks:")
# osobní položky nastavení: předvolby, naposledy hledané a čas posledního hlídání oblíbených
SETTINGS = ("prefer_language", "prefer_quality", "search_history", "favorites_checked")
LIVE = "profile_live"            # čí osobní stav je právě v katalogu (zapisuje se spolu s ním)
TRACKS_PREFIX = "tracks:"        # zvuk a titulky naposledy zvolené u seriálu nebo filmu
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Profily
# ---------------------------------------------------------------------------

def _load():
    try:
        with open(api._path(PROFILES), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    known = dict(AVATARS)
    profiles = [{"id": str(item["id"]), "name": str(item.get("name") or "Profil"),
                 "avatar": item.get("avatar") if item.get("avatar") in known else AVATARS[0][0]}
                for item in data.get("profiles") or [] if isinstance(item, dict) and item.get("id")]
    if not profiles:
        profiles = [{"id": "p1", "name": "Hlavní profil", "avatar": AVATARS[0][0]}]
    active = data.get("active")
    if len(profiles) > 1:
        # Přepnutí, které se nedokončilo (pád, vypnutí): rozhoduje, čí stav v katalogu opravdu je.
        # Jinak by se při dalším přepnutí uložil cizí stav do souboru původního profilu.
        with api._library() as lib:
            live = lib.get_kv(LIVE)
        if live in [item["id"] for item in profiles]:
            active = live
    if active not in [item["id"] for item in profiles]:
        active = profiles[0]["id"]
    return {"active": active, "profiles": profiles}


def _save(data):
    temporary = api._path(PROFILES + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(temporary, api._path(PROFILES))


def _file(profile_id):
    return api._path("profile_%s.json" % "".join(char for char in profile_id if char.isalnum()))


def _snapshot(lib):
    """Osobní stav aktivního profilu."""
    tables = {}
    for table in TABLES:
        cursor = lib.conn.execute("SELECT * FROM %s" % table)
        names = [column[0] for column in cursor.description]
        tables[table] = [dict(zip(names, row)) for row in cursor.fetchall()]
    values = {key: value for key, value in lib.conn.execute("SELECT k, v FROM settings_kv").fetchall()
              if key.startswith(KV_PREFIXES)}
    settings = api._settings()
    return {"tables": tables, "kv": values, "favorites": app_discover.load_favorites(),
            "settings": {key: settings[key] for key in SETTINGS if settings.get(key)}}


def _restore(lib, snapshot, owner):
    """Nahradí osobní stav uloženým (prázdný u nového profilu) a ve stejném kroku si do katalogu
    poznamená, čí stav to je."""
    for table in TABLES:
        columns = [row[1] for row in lib.conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
        lib.conn.execute("DELETE FROM %s" % table)
        for row in (snapshot.get("tables") or {}).get(table) or []:
            names = [name for name in columns if name in row]  # sloupce, které tabulka dnes má
            if names:
                lib.conn.execute("INSERT OR REPLACE INTO %s (%s) VALUES (%s)" % (
                    table, ", ".join(names), ", ".join("?" for _ in names)), [row[name] for name in names])
    for key, _value in lib.conn.execute("SELECT k, v FROM settings_kv").fetchall():
        if key.startswith(KV_PREFIXES):
            lib.conn.execute("DELETE FROM settings_kv WHERE k=?", (key,))
    for key, value in (snapshot.get("kv") or {}).items():
        if key.startswith(KV_PREFIXES):
            lib.conn.execute("INSERT OR REPLACE INTO settings_kv (k, v) VALUES (?, ?)", (key, value))
    # Soubory (předvolby, oblíbené) se zapisují před potvrzením změn v katalogu: když zápis selže,
    # katalog zůstane u původního profilu a nic se nepomíchá.
    def change(settings):
        for key in SETTINGS:
            settings.pop(key, None)
        settings.update({key: value for key, value in (snapshot.get("settings") or {}).items() if key in SETTINGS})

    api._change_settings(change)
    favorites = snapshot.get("favorites")
    app_discover._save_favorites(favorites if isinstance(favorites, dict) else {})
    lib.conn.execute("INSERT OR REPLACE INTO settings_kv (k, v) VALUES (?, ?)", (LIVE, owner))
    lib.conn.commit()


def current():
    """Aktivní profil a počet profilů (pro hlavní obrazovku)."""
    data = _load()
    profile = next(item for item in data["profiles"] if item["id"] == data["active"])
    return dict(profile, count=len(data["profiles"]))


def profiles(params):
    data = _load()
    cards = []
    for profile in data["profiles"]:
        active = profile["id"] == data["active"]
        menu = [{"title": "Přejmenovat", "target": api._target("input", "profile_rename", {
                    "id": profile["id"], "value": profile["name"], "hint": "Jméno profilu"})},
                {"title": "Změnit obrázek", "target": api._target("list", "profile_avatars", {"id": profile["id"]},
                                                                 "Obrázek profilu")}]
        if not active:
            menu.append({"title": "Smazat profil", "target": api._target("command", "profile_delete",
                                                                        {"id": profile["id"]})})
        cards.append(api._card(profile["name"], api._target("command", "profile_switch", {"id": profile["id"]}),
                               "Právě se dívá" if active else "Přepnout", "res:avatar_" + profile["avatar"],
                               menu=menu))
    if len(data["profiles"]) < len(AVATARS):
        cards.append(api._card("Přidat profil", api._target("list", "profile_avatars", {"new": 1},
                                                           "Nový profil – vyberte obrázek"),
                               "Vlastní Rozkoukané, Oblíbené a předvolby", icon="login"))
    return {"title": "Kdo se dívá?", "items": cards, "next": None}


def profile_avatars(params):
    """Výběr obrázku: pro nový profil, nebo pro změnu u stávajícího."""
    data = _load()
    used = {item["avatar"] for item in data["profiles"] if item["id"] != params.get("id")}
    cards = []
    for avatar, label in AVATARS:
        if params.get("new"):
            # nový profil: po výběru obrázku se aplikace zeptá na jméno (předvyplní název obrázku)
            target = api._target("input", "profile_add", {"avatar": avatar, "value": _free_name(data, label),
                                                         "hint": "Jméno nového profilu"})
        else:
            target = api._target("command", "profile_avatar", {"id": params.get("id") or "", "avatar": avatar})
        cards.append(api._card(label, target, "Už ho někdo má" if avatar in used else "", "res:avatar_" + avatar))
    return {"items": cards, "next": None}


def _free_name(data, base):
    """Jméno, které ještě žádný profil nemá (Sova, Sova 2, …)."""
    names = {item["name"] for item in data["profiles"]}
    name, number = base, 2
    while name in names:
        name, number = "%s %d" % (base, number), number + 1
    return name


def _name(text):
    return " ".join(str(text or "").split())[:24]


def profile_add(params):
    avatar = params.get("avatar") if params.get("avatar") in dict(AVATARS) else AVATARS[0][0]
    with _LOCK:
        data = _load()
        if len(data["profiles"]) >= len(AVATARS):
            raise api.ApiError("Víc profilů přidat nejde.")
        name = _name(params.get("text")) or _free_name(data, dict(AVATARS)[avatar])
        taken = {item["id"] for item in data["profiles"]}
        profile_id = next("p%d" % index for index in range(1, 100) if "p%d" % index not in taken)
        data["profiles"].append({"id": profile_id, "name": name, "avatar": avatar})
        _save(data)
    return {"message": "", "toast": "Profil „%s“ je připravený." % name, "close": True}


def _find(data, profile_id):
    profile = next((item for item in data["profiles"] if item["id"] == profile_id), None)
    if profile is None:
        raise api.ApiError("Profil už neexistuje.")
    return profile


def profile_rename(params):
    name = _name(params.get("text"))
    if not name:
        raise api.ApiError("Jméno profilu nemůže být prázdné.")
    with _LOCK:
        data = _load()
        profile = _find(data, params.get("id"))
        if profile["name"] == name:
            return {"message": ""}   # beze změny
        profile["name"] = name
        _save(data)
    return {"message": "", "toast": "Profil se teď jmenuje „%s“." % name}


def profile_avatar(params):
    if params.get("avatar") not in dict(AVATARS):
        raise api.ApiError("Neznámý obrázek.")
    with _LOCK:
        data = _load()
        _find(data, params.get("id"))["avatar"] = params["avatar"]
        _save(data)
    return {"message": "", "close": True}


def profile_delete(params):
    with _LOCK:
        data = _load()
        profile = _find(data, params.get("id"))
        if profile["id"] == data["active"]:
            raise api.ApiError("Profil, který se právě dívá, smazat nejde. Nejdřív přepněte na jiný.")
        data["profiles"].remove(profile)
        _save(data)
        try:
            os.remove(_file(profile["id"]))
        except OSError:
            pass
    return {"message": "Profil „%s“ je smazaný." % profile["name"]}


def profile_switch(params):
    """Přepne profil: stav dosavadního se uloží, stav nového načte."""
    def work():
        with _LOCK:
            data = _load()
            target = _find(data, params.get("id"))
            if target["id"] == data["active"]:
                return {"message": "", "close": True}
            try:
                with open(_file(target["id"]), encoding="utf-8") as f:
                    snapshot = json.load(f)
            except (OSError, ValueError):
                snapshot = {}
            with api._library() as lib:
                temporary = _file(data["active"]) + ".tmp"
                with open(temporary, "w", encoding="utf-8") as f:
                    json.dump(_snapshot(lib), f, ensure_ascii=False)
                os.replace(temporary, _file(data["active"]))
                _restore(lib, snapshot if isinstance(snapshot, dict) else {}, target["id"])
            data["active"] = target["id"]
            _save(data)
        return {"message": "", "close": True, "toast": "Dívá se: %s" % target["name"]}

    return api._exclusive(work)


# ---------------------------------------------------------------------------
# Úvodní panel a pokračování na domovské obrazovce televize
# ---------------------------------------------------------------------------

def _remaining(point):
    if not point or point[1] <= 0:
        return ""
    minutes = int(round((point[1] - point[0]) / 60.0))
    if minutes >= 60:
        return "zbývá %d h %02d min" % (minutes // 60, minutes % 60)
    return "zbývá %d min" % max(1, minutes)


def hero(lib, state, section):
    """Velký panel v čele sekce: naposledy rozkoukané video, nebo nic."""
    rows = lib.ivys_progress(1)
    videos = lib.history_videos(in_progress=True, limit=1, include_search=True) if section != "ivysilani" else []
    # Rozkoukané: naposledy přehrané nedokoukané video, ať je z Přehraj.to, nebo z iVysílání
    if rows and (section == "ivysilani" or not videos
                 or (rows[0]["last_played"] or 0) > (videos[0].get("last_played") or 0)):
        row = rows[0]
        point = (row["position"], row["total"])
        return {"title": api._ivys_label(row), "subtitle": " · ".join(part for part in ("iVysílání", _remaining(point)) if part),
                "image": row["image"] or "", "progress": api._percent(point), "label": "Pokračovat",
                "target": api._target("play", "ivys_play", {"idec": row["idec"], "resume": True})}
    if not videos:
        return None
    video = videos[0]
    meta = lib.get_meta(video["meta_key"]) or {}
    point = state["resume"].get(video["id"])
    details = " · ".join(part for part in (video.get("lang"), video.get("quality"), _remaining(point)) if part)
    return {"title": api._video_title(video, meta), "subtitle": details, "image": meta.get("fanart") or "",
            "progress": api._percent(point), "label": "Pokračovat",
            # „Pokračovat“ naváže rovnou, přehrávač se na to už neptá
            "target": api._target("play", "play", {"vid": video["id"], "resume": True})}


def watch_next(params):
    """Rozkoukaná videa pro řadu „Pokračovat ve sledování“ na domovské obrazovce Android TV.

    ``{"items": [{"id", "title", "image", "position", "duration", "episode", "call", "params"}]}``,
    naposledy přehrané první; časy v sekundách.
    """
    items = []
    with api._library() as lib:
        resume = lib.resume_points()
        videos = lib.history_videos(in_progress=True, limit=6, include_search=True)
        metas = lib.get_metas(video["meta_key"] for video in videos)
        for video in videos:
            point = resume.get(video["id"])
            if not point:
                continue
            meta = metas.get(video["meta_key"]) or {}
            items.append({"id": video["id"], "title": api._video_title(video, meta),
                          "image": meta.get("fanart") or meta.get("poster") or video.get("thumb") or "",
                          "position": int(point[0]), "duration": int(point[1]),
                          "episode": video["kind"] == "episode", "played": int(video.get("last_played") or 0),
                          "call": "play", "params": {"vid": video["id"]}})
        for row in lib.ivys_progress(4):
            items.append({"id": api.IVYS + row["idec"], "title": api._ivys_label(row), "image": row["image"] or "",
                          "position": int(row["position"]), "duration": int(row["total"]), "episode": True,
                          "played": int(row["last_played"] or 0),
                          "call": "ivys_play", "params": {"idec": row["idec"]}})
    items.sort(key=lambda item: -item["played"])
    return {"items": items[:8]}


# ---------------------------------------------------------------------------
# Zvuk a titulky zapamatované pro seriál
# ---------------------------------------------------------------------------

def tracks(lib, key):
    """``{"key", "audio", "subs"}`` pro přehrávač; jazyk je kód (cs, en…), titulky i ``off``."""
    try:
        saved = json.loads(lib.get_kv(TRACKS_PREFIX + key) or "{}") if key else {}
    except ValueError:
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    return {"key": key, "audio": str(saved.get("audio") or ""), "subs": str(saved.get("subs") or "")}


def track_prefs(params):
    key = str(params.get("key") or "")
    if not key:
        return {"saved": False}
    with api._library() as lib:
        lib.set_kv(TRACKS_PREFIX + key, json.dumps({"audio": str(params.get("audio") or "")[:12],
                                                    "subs": str(params.get("subs") or "")[:12]}))
    return {"saved": True}


# ---------------------------------------------------------------------------
# Stažení celé řady (seriál z vlastního katalogu)
# ---------------------------------------------------------------------------

def season_counts(videos):
    """{řada: počet dílů} seriálu z katalogu."""
    episodes = {}
    for video in videos:
        if video["episode"]:
            episodes.setdefault(video["season"], set()).add(video["episode"])
    return {season: len(numbers) for season, numbers in episodes.items()}


def download_season(params):
    key, season = params.get("key") or "", int(params.get("season") or 0)
    with api._library() as lib:
        state = api._state(lib)
        best = {}
        for video in lib.videos_for_meta(key):
            if not video["episode"] or video["season"] != season:
                continue
            rank = api._version_rank(video, state["prefs"], state["bad"])
            if video["episode"] not in best or rank < best[video["episode"]][0]:
                best[video["episode"]] = (rank, video["id"])
    if not best:
        raise api.ApiError("V této řadě nejsou žádné díly.")
    added = 0
    for episode in sorted(best):
        vid = best[episode][1]
        entry = state["downloads"].get(vid) or {}
        if entry.get("state") in ("queued", "downloading") or api._saved(state, vid):
            continue
        api.download_add({"vid": vid})
        added += 1
    if not added:
        return {"message": "Všechny díly této řady už jsou stažené nebo ve frontě."}
    return {"message": "Zařazeno ke stažení: %d dílů (%d. řada)." % (added, season), "start": True}


ACTIONS = {
    "profiles": profiles, "profile_avatars": profile_avatars, "profile_add": profile_add,
    "profile_rename": profile_rename, "profile_avatar": profile_avatar, "profile_delete": profile_delete,
    "profile_switch": profile_switch, "watch_next": watch_next, "track_prefs": track_prefs,
    "download_season": download_season,
}
