# -*- coding: utf-8 -*-
"""Vlastní kolekce."""
import xbmc

from kodiutils import keyboard, notify
import ui
from ui import (_, add_action, add_dir, add_movie, add_show, add_video, end, MOVIE_SORTS, _movie_versions,
                open_library, url_for)


def collections(params):
    add_action("[B]%s[/B]" % _(30123), "collection_new", "DefaultAddSource.png")
    with open_library() as lib:
        cols = lib.collections()
    for c in cols:
        ctx = [(_(30125), "RunPlugin(%s)" % url_for("collection_rename", cid=c["id"])),
               (_(30126), "RunPlugin(%s)" % url_for("collection_delete", cid=c["id"]))]
        add_dir("%s (%d)" % (c["name"], c["item_count"]), "collection", "DefaultSets.png", context=ctx,
                cid=c["id"])
    end(cache=False)


def collection(params):
    cid = int(params["cid"])
    with open_library() as lib:
        targets = lib.collection_targets(cid)
        keys = [t for t in targets if not t.startswith("v:")]
        movies_ = dict((m["key"], m) for m in lib.movies(keys=keys))
        shows_ = dict((m["key"], m) for m in lib.shows(keys=keys))
        videos = [lib.get_video(t[2:]) for t in targets if t.startswith("v:")]
        counts = lib.unread_episode_counts() if shows_ else {}
        versions = _movie_versions(lib, movies_.values())
        for t in keys:
            if t in movies_:
                add_movie(movies_[t], lib, collection_id=cid, versions=versions)
            elif t in shows_:
                add_show(shows_[t], collection_id=cid, new_count=counts.get(t, 0))
    for v in videos:
        if v:
            add_video(v)
    end("movies", MOVIE_SORTS, cache=False)


def collection_new(params):
    name = keyboard(_(30124))
    if not name:
        return None
    with open_library() as lib:
        cid = lib.create_collection(name)
    xbmc.executebuiltin("Container.Refresh")
    return cid


def collection_rename(params):
    cid = int(params["cid"])
    with open_library() as lib:
        current = next((c["name"] for c in lib.collections() if c["id"] == cid), "")
        name = keyboard(_(30125), current)
        if name:
            lib.rename_collection(cid, name)
    xbmc.executebuiltin("Container.Refresh")


def collection_delete(params):
    cid = int(params["cid"])
    with open_library() as lib:
        current = next((c["name"] for c in lib.collections() if c["id"] == cid), "")
        if ui.yesno(_(30127) % current):
            lib.delete_collection(cid)
    xbmc.executebuiltin("Container.Refresh")


def collection_add(params):
    target = params["target"]
    with open_library() as lib:
        cols = lib.collections()
        names = ["[B]%s[/B]" % _(30123)] + [c["name"] for c in cols]
        idx = ui.select(_(30128), names)
        if idx < 0:
            return
        if idx == 0:
            name = keyboard(_(30124))
            if not name:
                return
            cid = lib.create_collection(name)
        else:
            cid = cols[idx - 1]["id"]
            name = cols[idx - 1]["name"]
        lib.add_to_collection(cid, target)
    notify(_(30130) % name)


def collection_remove(params):
    with open_library() as lib:
        lib.remove_from_collection(int(params["cid"]), params["target"])
    xbmc.executebuiltin("Container.Refresh")
