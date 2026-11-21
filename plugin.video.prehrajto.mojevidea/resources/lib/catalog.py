# -*- coding: utf-8 -*-
"""Logika katalogu nezávislá na Kodi: synchronizace s účtem, import odkazů,
doplnění metadat z ČSFD/TMDB."""
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor

import csfd
import nameparser
import provider
from tmdb import TmdbError


TMDB_WORKERS = 4  # souběžné dotazy na TMDB při doplňování metadat (bez ČSFD)


def _parsed(title):
    return nameparser.parse(title)


def sync_account(client, lib, include_favorites=False, include_playlists=False, remove_missing=True,
                 progress=None, log=None, known=None):
    """Načte videa z účtu do katalogu.

    ``progress(stage, pages, count)`` může vrátit True = zrušit.
    ``known`` = klíče videí, která už katalog má: výpis skončí u první stránky bez nového
    videa (rychlá synchronizace – nic se podle ní neodebírá).
    """
    log = log or (lambda m: None)
    started = time.time_ns()
    stats = {"total": 0, "new": 0, "removed": 0, "favorites": 0, "playlists": 0, "cancelled": False}

    def prog(stage):
        def inner(pages, count):
            if progress and progress(stage, pages, count):
                stats["cancelled"] = True
                return True
            return False
        return inner

    if known:
        videos = client.my_videos(progress=prog("my_videos"), known=known)
    else:
        videos = client.my_videos(progress=prog("my_videos"))
    stats["total"] = len(videos)
    stats["quick"] = bool(known) and bool(getattr(client, "listing_partial", False))
    stats["incomplete"] = not stats["quick"] and not getattr(client, "listing_complete", True)
    complete = not stats["cancelled"] and not stats["incomplete"] and not stats["quick"]
    result = lib.apply_account_snapshot(videos, started, complete, remove_missing,
                                        partial=stats["quick"] and not stats["cancelled"])
    if result is None:
        stats["superseded"] = True
        return stats
    stats["new"], stats["removed"] = result

    if include_favorites and not stats["cancelled"]:
        try:
            favs = client.my_favorites(progress=prog("my_favorites"))
        except provider.ProviderError as e:
            log("oblíbené nelze načíst: %s" % e)
            favs = []
        for v in favs:
            if lib.upsert_video(v["id"], v["title"] or v["id"], "favorites",
                                _parsed(v["title"] or v["id"]), duration=v.get("duration", 0),
                                thumb=v.get("thumb", ""), size=v.get("size", ""),
                            quality=v.get("quality", "")):
                stats["new"] += 1
        stats["favorites"] = len(favs)

    if include_playlists and not stats["cancelled"]:
        try:
            playlists = client.my_playlists()
        except provider.ProviderError as e:
            log("playlisty nelze načíst: %s" % e)
            playlists = []
        for name, url in playlists:
            if stats["cancelled"]:
                break
            try:
                items = client.fetch_listing(url, progress=prog("playlist"))
            except provider.ProviderError as e:
                log("playlist %s nelze načíst: %s" % (name, e))
                continue
            cid = lib.create_collection(name, source="playlist")
            for v in items:
                if lib.upsert_video(v["id"], v["title"] or v["id"], "playlist",
                                    _parsed(v["title"] or v["id"]), duration=v.get("duration", 0),
                                    thumb=v.get("thumb", ""), size=v.get("size", ""),
                            quality=v.get("quality", "")):
                    stats["new"] += 1
                video = lib.get_video(v["id"])
                lib.add_to_collection(cid, video["meta_key"])
            stats["playlists"] += 1

    if include_favorites or include_playlists:
        lib.cleanup_meta()
    return stats


def parse_link_lines(text):
    """Řádky se seznamem odkazů → [(id, název nebo '')].

    Podporované tvary řádku (odkaz pozná ``provider.find_video_id``)::

        <odkaz>
        Matrix (1999) CZ | <odkaz>
        <odkaz> ; Matrix (1999)
    """
    out = []
    seen = set()
    for line in (text or "").splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        vid = provider.find_video_id(line)
        if not vid or vid in seen:
            continue
        seen.add(vid)
        title = line
        for sep in ("|", ";", "\t"):
            if sep in title:
                parts = [p.strip() for p in title.split(sep)]
                parts = [p for p in parts if p and provider.find_video_id(p) != vid]
                title = parts[0] if parts else ""
                break
        else:
            title = "" if provider.find_video_id(title) == vid else title
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
        thumb = provider.thumb_url(vid)
        # stránka videa doplní název, případně úplné ID (Přehraj.to: číslo videa pro API)
        if not title or provider.incomplete_id(vid):
            try:
                info = client.video_info(vid)
                title = title or info.get("title") or vid
                description = info.get("description", "")
                thumb = info.get("thumb") or thumb
                vid = info.get("id") or vid
            except provider.ProviderError as e:
                log("údaje o videu %s nelze zjistit: %s" % (vid, e))
                if not title:
                    stats["failed"] += 1
                    title = vid
        if lib.upsert_video(vid, title, "manual", _parsed(title), thumb=thumb, description=description):
            stats["new"] += 1
        else:
            stats["existing"] += 1
    return stats


def apply_details(lib, key, info, source, lock_videos=False):
    data = dict(info)
    data["status"] = "ok" if source != "manual" else "manual"
    data["source"] = source
    data["rating_source"] = source if info.get("rating") else ""
    if lock_videos:
        lib.update_meta(key, data, lock_videos=True)
    else:
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
        if not csfd_available and tmdb_available:
            # Zbývá jen TMDB (ČSFD je vypnuté nebo blokuje): dotazy nemusí čekat jeden na druhý.
            _enrich_from_tmdb(lib, metas, idx, tmdb, stats, progress, log)
            break
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


def _enrich_from_tmdb(lib, metas, start, tmdb, stats, progress, log):
    """Doplní ``metas[start:]`` z TMDB po dávkách souběžných dotazů; zápis do katalogu
    zůstává v tomto vlákně."""
    def lookup(meta):
        try:
            return tmdb.find(meta["search_title"], meta.get("search_year") or 0, meta["kind"] == "show"), None
        except TmdbError as e:
            return None, e

    total = len(metas)
    with ThreadPoolExecutor(max_workers=TMDB_WORKERS) as pool:
        for first in range(start, total, TMDB_WORKERS * 2):
            batch = metas[first:first + TMDB_WORKERS * 2]
            if progress and progress(first, total, batch[0]["search_title"]):
                stats["cancelled"] = True
                return
            bad_key = False
            for offset, (meta, (info, error)) in enumerate(zip(batch, pool.map(lookup, batch))):
                if progress:
                    progress(first + offset, total, meta["search_title"])
                if info:
                    apply_details(lib, meta["key"], info, "tmdb")
                    stats["ok"] += 1
                    stats["tmdb"] += 1
                elif error:
                    log("TMDB chyba u '%s': %s" % (meta["search_title"], error))
                    stats["last_error"] = str(error)
                    lib.set_meta_status(meta["key"], "error")
                    stats["errors"] += 1
                    bad_key = bad_key or "klíč" in str(error)
                else:
                    lib.set_meta_status(meta["key"], "notfound")
                    stats["notfound"] += 1
            if bad_key:
                # neplatný klíč – zbytek nemá smysl zkoušet
                for rest in metas[first + len(batch):]:
                    lib.set_meta_status(rest["key"], "blocked")
                return


_SECRET_PATTERNS = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), "***JWT***"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "***@***"),
    (re.compile(r"((?:password|pass|passwd|token|access_token|refresh_token|login|email|username)"
                r"[\"']?\s*[=:]\s*[\"']?)[^&\"'\s<>;,]+", re.I), r"\1***"),
    (re.compile(r"(<input\b[^>]*type=[\"']hidden[\"'][^>]*value=[\"'])[^\"']*", re.I), r"\1***"),
    (re.compile(r"((?:_nss|PHPSESSID|CLIPSHARE|session|csrf\w*)[\"']?\s*[=:]\s*[\"']?)[^&\"'\s<>;,]+",
                re.I), r"\1***"),
]


def sanitize(text, secrets=()):
    """Odstraní z HTML přihlašovací údaje, e-maily, tokeny a skrytá pole formulářů
    (diagnostické soubory se posílají dál, nesmí obsahovat nic citlivého)."""
    text = text or ""
    for secret in secrets:
        secret = (secret or "").strip()
        if len(secret) >= 3:
            text = re.sub(re.escape(secret), "***", text, flags=re.I)
            for digest in (hashlib.md5(secret.encode("utf-8")).hexdigest(),):
                text = text.replace(digest, "***")
                text = text.replace(hashlib.md5(digest.encode("ascii")).hexdigest(), "***")
    for rx, repl in _SECRET_PATTERNS:
        text = rx.sub(repl, text)
    return text
