# -*- coding: utf-8 -*-
"""Ruční úpravy katalogu: názvy, metadata, díly, skrytí a smazání videí."""
import xbmc
import xbmcgui

from kodiutils import ADDON, keyboard, log, notify, number, ok, setting_bool, text_viewer
from nameparser import parse
from search_results import normalize
import ui
from ui import (_, add_action, add_video, _display_title, _display_year, end, open_library)

views_tools = ui.LazyModule("views_tools")
_tmdb = ui.LazyModule("tmdb")
catalog = ui.LazyModule("catalog")
csfd = ui.LazyModule("csfd")
provider = ui.LazyModule("provider")


def repair_title(params):
    key = params["key"]
    add_action(_(30373), "tmdb_repair", key=key)
    add_action(_(30300), "meta_assign", key=key)
    with open_library() as lib:
        meta = lib.get_meta(key)
        videos = lib.videos_for_meta(key)
    for video in videos:
        add_video(video, meta)
    end("videos", cache=False)


def tmdb_repair(params):
    key = params["key"]
    with open_library() as lib:
        meta = lib.get_meta(key)
        videos = lib.videos_for_meta(key)
    if not meta or not videos:
        return
    client = ui.make_tmdb()
    if not client.enabled:
        ok(_(30355))
        ADDON.openSettings()
        return
    raw_title = videos[0]["raw_title"]
    query = keyboard(_(30380) % raw_title, meta["search_title"])
    if not query or not query.strip():
        return
    series = meta["kind"] == "show"
    try:
        candidates = client.search(query.strip(), series)
        if not candidates:
            notify(_(30121))
            return
        labels = ["%s (%s)%s" % (item["title"], item["year"] or "?",
                  " · " + item["orig_title"] if item["orig_title"] and item["orig_title"] != item["title"] else "")
                  for item in candidates]
        choice = ui.select(_(30380) % raw_title, labels)
        if choice < 0:
            return
        info = client.details(candidates[choice]["id"], series)
    except _tmdb.TmdbError as e:
        ok(str(e))
        return
    with open_library() as lib:
        catalog.apply_details(lib, key, info, "tmdb", lock_videos=True)
    notify(_(30320))
    xbmc.executebuiltin("Container.Refresh")


def meta_assign(params):
    key = params["key"]
    with open_library() as lib:
        meta = lib.get_meta(key)
    if not meta:
        return
    want_series = meta["kind"] == "show"
    options = [_(30306), _(30307), _(30308), _(30309)]
    idx = ui.select(_(30300), options)
    if idx < 0:
        return
    try:
        if idx == 0:
            query = keyboard(_(30310), meta["search_title"])
            if not query:
                return
            results = csfd.search(query, log=log)
            if not results:
                notify(_(30121))
                return
            labels = ["%s (%s) – %s" % (r["title"], r["year"] or "?", r["type"]) for r in results]
            pick = ui.select(_(30306), labels)
            if pick < 0:
                return
            info = csfd.details(results[pick]["id"], log=log)
            source = "csfd"
        elif idx == 1:
            text = keyboard(_(30307), meta.get("csfd_url") or "")
            fid = csfd.film_id_from_url(text or "")
            if not fid:
                return
            info = csfd.details(fid, log=log)
            source = "csfd"
        elif idx == 2:
            tmdb = ui.make_tmdb()
            if not tmdb.enabled:
                ok(_(30327))
                return
            query = keyboard(_(30310), meta["search_title"])
            if not query:
                return
            results = tmdb.search(query, want_series)
            if not results:
                notify(_(30121))
                return
            labels = ["%s (%s)%s" % (r["title"], r["year"] or "?",
                                     (" – " + r["orig_title"]) if r["orig_title"] != r["title"] else "")
                      for r in results]
            pick = ui.select(_(30308), labels)
            if pick < 0:
                return
            info = tmdb.details(results[pick]["id"], want_series)
            source = "tmdb"
        else:
            value = number(_(30311), meta.get("rating") or "")
            if value is None:
                return
            info = {"rating": max(0, min(100, value)), "votes": 0}
            source = "manual"
    except csfd.CsfdBlocked:
        ok(_(30216))
        return
    except (csfd.CsfdError, _tmdb.TmdbError) as e:
        ok(_(30328) % e)
        return
    with open_library() as lib:
        if source == "manual":
            lib.update_meta(key, {"rating": info["rating"], "rating_source": "manual",
                                  "status": meta["status"] if meta["status"] == "ok" else "manual"})
        else:
            catalog.apply_details(lib, key, info, source)
    notify(_(30320))
    xbmc.executebuiltin("Container.Refresh")


def meta_refresh(params):
    key = params["key"]
    with open_library() as lib:
        lib.reset_meta(key)
    views_tools.run_enrich(keys=[key])
    xbmc.executebuiltin("Container.Refresh")


def batch_episodes(params):
    with open_library() as lib:
        videos = lib.all_videos()
    if not videos:
        notify(_(30121))
        return
    query = keyboard(_(30453))
    if query is None:
        return
    if query:
        words = normalize(query).split()
        matching = []
        for video in videos:
            filename = normalize(video["raw_title"])
            if all(word in filename for word in words):
                matching.append(video)
        videos = matching
    if not videos:
        notify(_(30121))
        return
    selection = xbmcgui.Dialog().multiselect(_(30446), [video["raw_title"] for video in videos])
    if not selection:
        return
    selected = [videos[index] for index in selection]
    default_title = selected[0]["title"] if all(v["title"] == selected[0]["title"] for v in selected) else ""
    title = keyboard(_(30319), default_title)
    if not title:
        return
    seasons = {video["season"] for video in selected if video["kind"] == "episode"}
    default_season = next(iter(seasons)) if len(seasons) == 1 else 1
    season_number = number(_(30317), str(default_season))
    if season_number is None or season_number < 0:
        return
    episodes = [video["episode"] or parse(video["raw_title"])["episode"] for video in selected]
    while True:
        labels = [_(30447) if all(episodes) else _(30448)]
        labels += ["S%02dE%02d · %s" % (season_number, episode, video["raw_title"]) if episode else
                   "? · " + video["raw_title"] for video, episode in zip(selected, episodes)]
        index = ui.select(title, labels)
        if index < 0:
            return
        if index == 0:
            if all(episodes):
                break
            continue
        episode = number(_(30449) % selected[index - 1]["raw_title"], episodes[index - 1])
        if episode and episode > 0:
            episodes[index - 1] = episode
    try:
        with open_library() as lib:
            key = lib.edit_episodes(list(zip(selected, episodes)), title, season_number)
            meta = lib.get_meta(key)
    except ValueError:
        notify(_(30451))
        return
    if meta and meta["status"] == "new" and setting_bool("auto_meta"):
        views_tools.run_enrich(keys=[key], quiet=True)
    notify(_(30452) % len(selected))
    xbmc.executebuiltin("Container.Refresh")


def video_edit(params):
    vid = params["vid"]
    with open_library() as lib:
        v = lib.get_video(vid)
    if not v:
        return
    kind_idx = ui.select(_(30312), [_(30313), _(30314)], preselect=1 if v["kind"] == "episode" else 0)
    if kind_idx < 0:
        return
    title = keyboard(_(30319) if kind_idx == 1 else _(30315), v["title"])
    if not title:
        return
    year = season_ = episode = 0
    if kind_idx == 0:
        year = number(_(30316), v["year"] or "") or 0
        kind = "movie"
    else:
        season_ = number(_(30317), v["season"] or 1) or 1
        episode = number(_(30318), v["episode"] or "") or 0
        kind = "episode"
    with open_library() as lib:
        key = lib.edit_video(vid, kind, title, year, season_, episode)
        meta = lib.get_meta(key)
    if meta and meta["status"] == "new" and setting_bool("auto_meta"):
        views_tools.run_enrich(keys=[key], quiet=True)
    notify(_(30320))
    xbmc.executebuiltin("Container.Refresh")


def video_hide(params):
    with open_library() as lib:
        lib.set_hidden(params["vid"], params.get("hide", "1") == "1")
    xbmc.executebuiltin("Container.Refresh")


def video_delete(params):
    with open_library() as lib:
        v = lib.get_video(params["vid"])
        if v and ui.yesno(_(30326) % v["raw_title"]):
            lib.delete_video(v["id"])
    xbmc.executebuiltin("Container.Refresh")


def video_info(params):
    with open_library() as lib:
        v = lib.get_video(params["vid"])
        meta = lib.get_meta(v["meta_key"]) if v else None
    if not v:
        return
    lines = ["%s: %s" % (_(30330), v["raw_title"]),
             "ID: %s" % v["id"], provider.video_page_url(v["id"]),
             "%s: %s" % (_(30331), v["lang"] or "?"), "%s: %s" % (_(30332), v["quality"] or "?")]
    if meta:
        lines.append("")
        lines.append("%s: %s (%s)" % (_(30315), _display_title(meta), _display_year(meta) or "?"))
        if meta.get("csfd_url"):
            lines.append("ČSFD: %s" % meta["csfd_url"])
        if meta.get("rating"):
            lines.append("%s: %d %%" % (_(30329), meta["rating"]))
    text_viewer(_(30325), "\n".join(lines))
