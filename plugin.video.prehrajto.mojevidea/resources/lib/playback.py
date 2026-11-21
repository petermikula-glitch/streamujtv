# -*- coding: utf-8 -*-
"""Přehrávání: výběr verze a streamu, obnova odkazů, pokračování a náhradní verze."""
import json
import time
import urllib.parse
import uuid
from hashlib import sha256
from math import isfinite

import xbmc
import xbmcgui
import xbmcplugin

from kodiutils import log, log_error, notify, setting_bool, setting_int
import ui
from ui import (_, _account_scope, _apply_playback, _json_object, open_library, PLAYBACK_START_TIMEOUT,
                PLAYING_PROPERTY, _resume_groups, _set_resume_point, _transfer_position, url_for,
                _version_group, _version_label, _version_score)

provider = ui.LazyModule("provider")


def _pick_stream(streams, video, choose):
    if not choose:
        return min(streams, key=_version_score)
    labels = []
    for stream in streams:
        original = stream["quality"] == "ORIG"
        details = {
            "lang": stream.get("lang") or video.get("lang"),
            "quality": video.get("quality") if original else stream.get("resolution") or stream["quality"],
            "size": video.get("size") if original else None,
        }
        label = _version_label(details, _(30398) if original else _(30399))
        if stream.get("subtitles"):
            label += " · " + _(30400) % ", ".join(stream["subtitles"])
        labels.append(label)
    idx = ui.select(_(30321), labels)
    return streams[idx] if idx >= 0 else None


def _link_cache_get(vid):
    with open_library() as lib:
        data = _json_object(lib.get_kv("link:" + vid))
    if (isinstance(data.get("url"), str) and isinstance(data.get("ts"), (int, float))
            and data.get("account") == _account_scope()
            and 0 <= time.time() - data["ts"] < provider.LINK_TTL
            and data.get("quality_preference", 0) == setting_int("pref_quality", 0)):
        return data
    return None


def _link_cache_put(vid, final, subtitles, fallback_height):
    cache_id = uuid.uuid4().hex
    payload = {
        "url": final, "subtitles": subtitles, "fallback_height": fallback_height,
        "cache_id": cache_id, "ts": int(time.time()),
        "quality_preference": setting_int("pref_quality", 0), "account": _account_scope(),
    }
    with open_library() as lib:
        lib.set_kv("link:" + vid, json.dumps(payload))
    return cache_id


def _replacement_offer(vid, message, previous_position=None):
    with open_library() as lib:
        source = lib.find_video(vid)
        group = _version_group(source)
        if not group:
            return False
        season = source["season"] if source["kind"] == "episode" else None
        variants = [video for video in lib.videos_for_meta(source["meta_key"], season=season)
                    if _version_group(video) == group and video["id"] != source["id"]]
        resume = lib.resume_points()
        point = resume.get(source["id"]) or previous_position
    if not variants:
        return False
    variants.sort(key=_version_score)
    options = ["%s · %s" % (label, v["raw_title"]) if label else v["raw_title"]
               for v in variants for label in [_version_label(v)]]
    chosen = ui.select("%s · %s" % (_(30519), message), options)
    if chosen < 0:
        return True
    target = variants[chosen]
    args = {"vid": target["id"], "replacement": 1}
    if target["id"] in resume and ui.yesno(_(30521) % _format_position(resume[target["id"]][0]), heading=_(30519)):
        args["continue_play"] = 1
    elif target["id"] not in resume and point:
        source_progress = {"id": source["id"], "position": point[0], "total": point[1]}
        transfer = _transfer_position(target, [source_progress])
        if transfer:
            (position, total), uncertain, source_duration = transfer
            target_duration = target["duration"]
            if uncertain and target_duration:
                prompt = _(30456) % (_format_position(source_duration), _format_position(target_duration),
                                     _format_position(position))
            else:
                prompt = _(30457 if uncertain else 30521) % _format_position(position)
            if ui.yesno(prompt, heading=_(30519)):
                args.update(replacement_source=source["id"], transferred_position=position, transferred_total=total)
    xbmc.executebuiltin("PlayMedia(%s,noresume)" % url_for("play", **args))
    return True


def playback_failed(vid, message, replacement=False, transferred=None):
    if replacement or not _replacement_offer(vid, message, transferred):
        notify(message, time_ms=8000)


def retry_failed_playback(pending):
    """Po chybě Kodi zahodí nefunkční odkaz a znovu spustí pouze pokus z mezipaměti."""
    vid = pending["vid"]
    cache_id = pending.get("cache_id")
    if cache_id is not None:
        with open_library() as lib:
            key = "link:" + vid
            raw = lib.get_kv(key)
            cached = _json_object(raw)
            if cached.get("cache_id", cached.get("ts")) == cache_id:
                lib.delete_kv(key, expected=raw)
    if pending["cached"]:
        notify(_(30401))
        resume = pending["resume"]
        position, total = pending.get("transferred") or (None, None)
        url = url_for("play", vid=vid, retry=1, restart=None if resume else 1,
                      transferred_position=position, transferred_total=total,
                      replacement=1 if pending.get("replacement") else None)
        xbmc.executebuiltin("PlayMedia(%s,%s)" % (url, "resume" if resume else "noresume"))
    else:
        playback_failed(vid, _(30405), pending.get("replacement", False), pending.get("transferred"))


def _resume_request(vid, params):
    """Vrátí (bod přenesený z jiné verze, spustit od uložené pozice, zrušit přehrání)."""
    retry = params.get("retry") == "1"
    continue_play = params.get("continue_play") == "1"
    requested = params.get("resume_requested") == "1" or params.get("transfer") == "1"
    if retry or params.get("replacement_source"):
        try:
            position = float(params["transferred_position"])
            total = float(params["transferred_total"])
            if isfinite(total) and 0 < position < total:
                if retry:
                    return (position, total), True, False
                with open_library() as lib:
                    target = lib.find_video(vid)
                    source = lib.find_video(params["replacement_source"])
                    group = _version_group(target)
                    if group and not target["hidden"] and _version_group(source) == group:
                        return (position, total), True, False
        except (KeyError, ValueError, TypeError):
            pass
    resume = continue_play or requested or params.get("restart") != "1"
    if not requested or retry:
        return None, continue_play or (retry and resume), False
    with open_library() as lib:
        target = lib.find_video(vid)
        canonical = target["id"] if target else vid
        own = lib.resume_points([canonical]).get(canonical)
        if own:
            return None, True, False
        group = _version_group(target)
        candidate = (_transfer_position(target, _resume_groups(lib).get(group, []))
                     if group and not lib.watched([canonical]) else None)
    if not candidate:
        notify(_(30458))
        return None, False, True
    point, uncertain, source_duration = candidate
    if uncertain:
        target_duration = target["duration"]
        position = _format_position(point[0])
        message = (_(30456) % (_format_position(source_duration), _format_position(target_duration), position)
                   if target_duration else _(30457) % position)
        if not ui.yesno(message, heading=_(30455)):
            return None, False, True
    return point, True, False


def _format_position(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return "%d:%02d:%02d" % (hours, minutes, seconds)


def play(params):
    vid = params["vid"]
    xbmcgui.Window(10000).clearProperty(PLAYING_PROPERTY)
    retry = params.get("retry") == "1"
    replacement = params.get("replacement") == "1"
    transferred, force_resume, cancelled = _resume_request(vid, params)
    if cancelled:
        xbmcplugin.setResolvedUrl(ui.HANDLE, False, xbmcgui.ListItem())
        return
    resume = force_resume or params.get("restart") != "1"
    manual = params.get("choose") == "1"
    ask = not retry and (manual or setting_bool("ask_stream"))
    cached = _link_cache_get(vid) if not (ask or retry) else None
    if cached:
        log("play %s: adresa z paměti" % vid)
        cache_id = cached.get("cache_id", cached["ts"])
        _resolved(vid, cached["url"], cached.get("subtitles") or {}, cached.get("fallback_height"),
                  cache_id=cache_id, cached=True, resume=resume, force_resume=force_resume,
                  transferred=transferred, replacement=replacement)
        return
    client = ui.make_client()
    try:
        streams, message = client.get_streams(vid)
        if not streams:
            raise provider.VideoUnavailableError(message or _(30322))
        if message:
            notify(message)
        choose = ask and (manual or len(streams) > 1)
        video = {}
        if choose:
            with open_library() as lib:
                video = lib.find_video(vid) or {}
        stream = _pick_stream(streams, video, choose)
        if stream is None:
            xbmcplugin.setResolvedUrl(ui.HANDLE, False, xbmcgui.ListItem())
            return
        final = client.resolve(stream["url"])
    except provider.ProviderError as exc:
        log_error("play %s: %s" % (vid, exc))
        if isinstance(exc, provider.LoginError):
            message = _(30402)
        elif isinstance(exc, provider.VideoUnavailableError):
            message = _(30403)
        elif isinstance(exc, provider.TemporaryError):
            message = _(30404)
        else:
            message = _(30323) % exc
        xbmcplugin.setResolvedUrl(ui.HANDLE, False, xbmcgui.ListItem())
        if isinstance(exc, provider.LoginError):
            notify(message, time_ms=8000)
        else:
            playback_failed(vid, message, replacement, transferred)
        return
    subtitles = stream.get("subtitles") or {}
    fallback_height = getattr(client, "fallback_height", None)
    cache_id = _link_cache_put(vid, final, subtitles, fallback_height) if not choose else None
    _resolved(vid, final, subtitles, fallback_height, cache_id=cache_id,
              force_resume=force_resume, resume=resume, transferred=transferred, replacement=replacement)


def _resolved(vid, final, subtitles, fallback_height=None, *, cache_id=None, cached=False,
              force_resume=False, resume=True, transferred=None, replacement=False):
    if fallback_height:
        notify(_(30344) % fallback_height)
    li = xbmcgui.ListItem(path=final + "|User-Agent=" + urllib.parse.quote(provider.USER_AGENT))
    if transferred:
        _set_resume_point(li, transferred)
    elif force_resume:
        _apply_playback(li, vid)
    if final.split("?")[0].lower().endswith(".mp4"):
        li.setMimeType("video/mp4")
        li.setContentLookup(False)
    if setting_bool("subtitles") and subtitles:
        li.setSubtitles(list(subtitles.values()))
    pending = {"vid": vid, "cache_id": cache_id, "cached": cached, "resume": resume,
               "transferred": transferred, "replacement": replacement, "path_hash": sha256(final.split("|", 1)[0].encode("utf-8")).hexdigest(),
               "expires": time.monotonic() + PLAYBACK_START_TIMEOUT}
    xbmcgui.Window(10000).setProperty(PLAYING_PROPERTY, json.dumps(pending))
    xbmcplugin.setResolvedUrl(ui.HANDLE, True, li)
