# -*- coding: utf-8 -*-
"""Logika katalogu nezávislá na Kodi: synchronizace s účtem, import odkazů,
doplnění metadat z ČSFD/TMDB."""
import time

import csfd
import nameparser
import streamuj
from tmdb import TmdbError


def _parsed(title):
    return nameparser.parse(title)


def sync_account(client, lib, include_favorites=False, include_playlists=False, remove_missing=True,
                 progress=None, log=None):
    """Načte videa z účtu streamuj.tv do katalogu.

    ``progress(stage, pages, count)`` může vrátit True = zrušit.
    """
    log = log or (lambda m: None)
    started = int(time.time())
    stats = {"total": 0, "new": 0, "removed": 0, "favorites": 0, "playlists": 0, "cancelled": False}

    def prog(stage):
        def inner(pages, count):
            if progress and progress(stage, pages, count):
                stats["cancelled"] = True
                return True
            return False
        return inner

    videos = client.my_videos(progress=prog("my_videos"))
    for v in videos:
        if lib.upsert_video(v["id"], v["title"] or v["id"], "account", _parsed(v["title"] or v["id"]),
                            duration=v.get("duration", 0), thumb=v.get("thumb", "")):
            stats["new"] += 1
    stats["total"] = len(videos)

    if include_favorites and not stats["cancelled"]:
        try:
            favs = client.my_favorites(progress=prog("my_favorites"))
        except streamuj.StreamujError as e:
            log("oblíbené nelze načíst: %s" % e)
            favs = []
        for v in favs:
            if lib.upsert_video(v["id"], v["title"] or v["id"], "favorites",
                                _parsed(v["title"] or v["id"]), duration=v.get("duration", 0),
                                thumb=v.get("thumb", "")):
                stats["new"] += 1
        stats["favorites"] = len(favs)

    if include_playlists and not stats["cancelled"]:
        try:
            playlists = client.my_playlists()
        except streamuj.StreamujError as e:
            log("playlisty nelze načíst: %s" % e)
            playlists = []
        for name, url in playlists:
            if stats["cancelled"]:
                break
            try:
                items = client.fetch_listing(url, progress=prog("playlist"))
            except streamuj.StreamujError as e:
                log("playlist %s nelze načíst: %s" % (name, e))
                continue
            cid = lib.create_collection(name, source="playlist")
            for v in items:
                if lib.upsert_video(v["id"], v["title"] or v["id"], "playlist",
                                    _parsed(v["title"] or v["id"]), duration=v.get("duration", 0),
                                    thumb=v.get("thumb", "")):
                    stats["new"] += 1
                video = lib.get_video(v["id"])
                lib.add_to_collection(cid, video["meta_key"])
            stats["playlists"] += 1

    # mazat jen po úplném a neprázdném načtení – chyba stránky nesmí vymazat katalog
    if remove_missing and not stats["cancelled"] and videos:
        stats["removed"] = lib.remove_missing("account", started)
    lib.cleanup_meta()
    lib.set_kv("last_sync", int(time.time()))
    return stats


def parse_link_lines(text):
    """Řádky se seznamem odkazů → [(id, název nebo '')].

    Podporované tvary řádku::

        https://www.streamuj.tv/video/0645c382f89879db00bc
        0645c382f89879db00bc
        Matrix (1999) CZ | https://www.streamuj.tv/video/...
        https://www.streamuj.tv/video/... ; Matrix (1999)
    """
    out = []
    seen = set()
    for line in (text or "").splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        vid = streamuj.extract_video_id(line)
        if not vid:
            for token in line.replace("|", " ").replace(";", " ").replace("\t", " ").split():
                if streamuj.BARE_ID_RE.match(token):
                    vid = token.lower()
                    break
        if not vid or vid in seen:
            continue
        seen.add(vid)
        title = line
        for sep in ("|", ";", "\t"):
            if sep in title:
                parts = [p.strip() for p in title.split(sep)]
                parts = [p for p in parts if p and streamuj.extract_video_id(p) != vid]
                title = parts[0] if parts else ""
                break
        else:
            title = "" if streamuj.extract_video_id(title) == vid else title
        out.append((vid, title.strip()))
    return out


def import_links(client, lib, links, progress=None, log=None):
    """Přidá ručně zadané odkazy. Chybějící název se dočte ze stránky videa."""
    log = log or (lambda m: None)
    stats = {"new": 0, "existing": 0, "failed": 0, "cancelled": False}
    for idx, (vid, title) in enumerate(links):
        if progress and progress(idx, len(links), title or vid):
            stats["cancelled"] = True
            break
        description = ""
        thumb = streamuj.thumb_url(vid)
        if not title:
            try:
                info = client.video_info(vid)
                title = info.get("title") or vid
                description = info.get("description", "")
            except streamuj.StreamujError as e:
                log("název videa %s nelze zjistit: %s" % (vid, e))
                stats["failed"] += 1
                title = vid
        if lib.upsert_video(vid, title, "manual", _parsed(title), thumb=thumb, description=description):
            stats["new"] += 1
        else:
            stats["existing"] += 1
    return stats


def apply_details(lib, key, info, source):
    data = dict(info)
    data["status"] = "ok" if source != "manual" else "manual"
    data["source"] = source
    data["rating_source"] = source if info.get("rating") else ""
    lib.update_meta(key, data)


def enrich(lib, metas, use_csfd=True, tmdb=None, progress=None, log=None):
    """Doplní metadata k zadaným titulům. Vrací statistiku.

    ``progress(index, total, title)`` může vrátit True = zrušit.
    """
    log = log or (lambda m: None)
    stats = {"ok": 0, "csfd": 0, "tmdb": 0, "notfound": 0, "errors": 0, "csfd_blocked": False,
             "cancelled": False, "last_error": ""}
    csfd_available = use_csfd
    tmdb_available = bool(tmdb and tmdb.enabled)
    if not csfd_available and not tmdb_available:
        stats["last_error"] = "Není zapnutý žádný zdroj metadat (ČSFD ani TMDB)"
        return stats
    for idx, meta in enumerate(metas):
        if progress and progress(idx, len(metas), meta["search_title"]):
            stats["cancelled"] = True
            break
        want_series = meta["kind"] == "show"
        title, year = meta["search_title"], meta.get("search_year") or 0
        info = None
        source = ""
        failed = False
        if csfd_available:
            try:
                info = csfd.find(title, year, want_series, log=log)
                source = "csfd" if info else ""
            except csfd.CsfdBlocked as e:
                log("ČSFD blokuje automatické dotazy: %s" % e)
                stats["csfd_blocked"] = True
                stats["last_error"] = str(e)
                csfd_available = False
            except csfd.CsfdError as e:
                log("ČSFD chyba u '%s': %s" % (title, e))
                stats["last_error"] = str(e)
                failed = True
        if info is None and tmdb_available:
            try:
                info = tmdb.find(title, year, want_series)
                source = "tmdb" if info else ""
                failed = False
            except TmdbError as e:
                log("TMDB chyba u '%s': %s" % (title, e))
                stats["last_error"] = str(e)
                failed = True
                if "klíč" in str(e):
                    tmdb_available = False
        if info:
            apply_details(lib, meta["key"], info, source)
            stats["ok"] += 1
            stats[source] += 1
        elif failed:
            lib.set_meta_status(meta["key"], "error")
            stats["errors"] += 1
        elif not csfd_available and not tmdb_available and stats["csfd_blocked"]:
            lib.set_meta_status(meta["key"], "blocked")
            stats["errors"] += 1
        else:
            lib.set_meta_status(meta["key"], "notfound")
            stats["notfound"] += 1
        if not csfd_available and not tmdb_available:
            # zbytek nemá smysl zkoušet – označ jako zablokované a skonči
            for rest in metas[idx + 1:]:
                lib.set_meta_status(rest["key"], "blocked")
            break
    return stats
