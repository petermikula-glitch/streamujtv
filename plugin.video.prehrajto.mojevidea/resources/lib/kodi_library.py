# -*- coding: utf-8 -*-
"""Propojení katalogu s položkami exportovanými do knihovny Kodi."""
import json
import os
import time

import xbmc
import xbmcvfs

import library_export

PROPERTIES = ["file", "playcount", "resume", "lastplayed"]
MEDIA = (("movie", "Movie", "movies"), ("episode", "Episode", "episodes"))


class KodiLibraryError(Exception):
    pass


def rpc(method, **params):
    try:
        response = json.loads(xbmc.executeJSONRPC(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})))
    except (RuntimeError, ValueError, TypeError) as error:
        raise KodiLibraryError(method) from error
    if not isinstance(response, dict) or "error" in response or "result" not in response:
        raise KodiLibraryError(method)
    return response["result"]


def _path_key(path):
    path = xbmcvfs.translatePath(path).rstrip("/\\")
    return path.replace("\\", "/") if "://" in path else os.path.normcase(os.path.realpath(path))


def source_status(folder):
    sources = {_path_key(entry["file"]) for entry in rpc("Files.GetSources", media="video").get("sources", [])}
    root = library_export.export_folder(folder)
    return tuple(_path_key(library_export._join(root, name)) in sources for name in ("Filmy", "Serialy"))


def _link_map(folder, state_file, vids=None):
    return {_path_key(path): (path, vid) for path, vid in library_export.exported_streams(state_file, folder).items()
            if vids is None or vid in vids}


def _verified_item(item, links, addon_id):
    matched = links.get(_path_key(item["file"])) if item.get("file") else None
    if not matched:
        return None
    expected_path, expected_vid = matched
    return expected_vid if library_export.read_stream_id(expected_path, addon_id) == expected_vid else None


def indexed_links(folder, state_file, addon_id, vids=None, with_history=False):
    links = _link_map(folder, state_file, vids)
    if not links:
        return
    for kind, method, key in MEDIA:
        offset = 0
        while True:
            result = rpc("VideoLibrary.Get" + method + "s", properties=PROPERTIES,
                         limits={"start": offset, "end": offset + 250},
                         sort={"method": "file", "order": "ascending"})
            items = result.get(key, [])
            for item in items:
                if with_history and not (item.get("lastplayed") or item.get("playcount")
                                         or (item.get("resume") or {}).get("position")):
                    continue
                vid = _verified_item(item, links, addon_id)
                if vid:
                    yield kind, method, item, vid
            limits = result.get("limits") or {}
            end = limits.get("end", offset + len(items))
            if not items or end <= offset or end >= limits.get("total", end):
                break
            offset = end


def _native_history(item):
    resume = item.get("resume") or {}
    total = float(resume.get("total") or 0)
    position = max(0, min(float(resume.get("position") or 0), total))
    watched = bool(item.get("playcount"))
    try:
        updated = int(time.mktime(time.strptime(item.get("lastplayed") or "", "%Y-%m-%d %H:%M:%S")))
    except (OverflowError, ValueError):
        updated = 0
    return position, total, watched, updated


def import_history(lib, folder, state_file, addon_id):
    changed = 0
    for _kind, _method, item, vid in indexed_links(folder, state_file, addon_id, with_history=True):
        video = lib.find_video(vid)
        if not video:
            continue
        position, total, watched, updated = _native_history(item)
        local = lib.playback_history(video["id"])
        if not updated and not local["updated"] and (watched or position):
            updated = 1  # starší Kodi položka bez data, převzít jen do prázdné historie
        if updated <= local["updated"] or not (watched or position or local["updated"]):
            continue
        if any(lib.playback_history(alt)["updated"] >= updated for alt in versions(lib, video)):
            continue
        changed += lib.import_kodi_history(video["id"], position, total, watched, updated)
    return changed


def versions(lib, video):
    if not video.get("meta_key"):
        return {video["id"]}
    if video["kind"] == "movie":
        return {v["id"] for v in lib.videos_for_meta(video["meta_key"])}
    if video["episode"]:
        return {v["id"] for v in lib.videos_for_meta(video["meta_key"], season=video["season"])
                if v["episode"] == video["episode"]}
    return {video["id"]}


def accept_kodi_mark(lib, folder, state_file, addon_id, kind, item_id, watched):
    method = {"movie": "Movie", "episode": "Episode"}.get(kind)
    if not method:
        return
    item = rpc("VideoLibrary.Get" + method + "Details", **{
        kind + "id": item_id, "properties": PROPERTIES,
    }).get(kind + "details", {})
    if bool(item.get("playcount")) != watched:
        return
    vid = _verified_item(item, _link_map(folder, state_file), addon_id)
    video = lib.find_video(vid) if vid else None
    if not video:
        return
    group = versions(lib, video)
    if bool(lib.watched(group)) != watched:
        lib.set_watched(group, watched)


def publish_history(lib, folder, state_file, addon_id, vids, mark=None):
    sources = {}
    for vid in vids:
        video = lib.find_video(vid)
        if not video:
            continue
        canonical = video["id"]
        local = lib.playback_history(canonical)
        for alternate in versions(lib, video) | {vid, canonical}:
            if alternate not in sources or local["updated"] > sources[alternate][0]["updated"]:
                sources[alternate] = local, canonical
    for kind, method, item, exported in indexed_links(folder, state_file, addon_id, sources):
        local, canonical = sources[exported]
        kodi_position, kodi_total, kodi_watched, kodi_updated = _native_history(item)
        if mark is None and kodi_updated > local["updated"]:
            continue
        watched = (local["watched"] or kodi_watched) if mark is None else mark
        position = 0 if watched else local["position"]
        total = local["total"] or kodi_total
        if canonical != exported and position:
            target = lib.find_video(exported)
            target_duration = (target or {}).get("duration") or kodi_total
            if not target_duration or not total or abs(target_duration - total) > min(90, max(20, total * .03)):
                continue  # odlišný střih vyžaduje potvrzení při přehrání v doplňku
            total = target_duration
        changes = {kind + "id": item[kind + "id"]}
        if watched != kodi_watched:
            changes["playcount"] = int(watched)
        if abs(kodi_position - position) >= 2:
            changes["resume"] = {"position": position, "total": total}
        if len(changes) == 1:
            continue
        last_played = local["updated"] if mark is True else local["last_played"]
        if last_played:
            changes["lastplayed"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_played))
        rpc("VideoLibrary.Set" + method + "Details", **changes)
