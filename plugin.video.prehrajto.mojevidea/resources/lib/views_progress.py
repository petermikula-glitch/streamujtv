# -*- coding: utf-8 -*-
"""Rozkoukané, naposledy přehrané, zhlédnuté a správa položek."""
import xbmc
import xbmcgui
import xbmcplugin

from kodiutils import notify
import ui
from ui import (_, add_action, add_dir, add_video, _display_title, end, _episode_metadata_key, open_library,
                _PLAYBACK, PLAYING_PROPERTY, url_for, _version_score)

views_catalog = ui.LazyModule("views_catalog")
views_edit = ui.LazyModule("views_edit")
views_kodi = ui.LazyModule("views_kodi")


def _episode_position(video):
    season, episode = video["season"], video["episode"]
    return (season, episode) if episode > 0 else None


def _series_progress(lib, history, resume, watched, videos=None):
    latest = history[0]
    if videos is None:
        videos = lib.videos_for_meta(latest["meta_key"])
    completed = {_episode_position(video) for video in videos if video["id"] in watched}
    completed.discard(None)
    position = _episode_position(latest)
    if latest["id"] in resume and position not in completed:
        return latest
    if position is None:
        return next((video for video in history if video["id"] in resume
                     and _episode_position(video) not in completed), None)

    following = _next_episode(videos, position, completed, resume)
    if following:
        partial = next((video for video in history if video["id"] == following["id"] and video["id"] in resume),
                       None)
        return partial or dict(following, next_episode=True, last_played=latest["last_played"])
    return None


def _next_episode(videos, position, completed, resume):
    following = []
    for video in videos:
        candidate = _episode_position(video)
        if candidate is None or candidate <= position or candidate in completed:
            continue
        if following and candidate != _episode_position(following[0]):
            break  # videos_for_meta() řadí podle série a dílu
        following.append(video)
    return min(following, key=lambda video: (video["id"] not in resume, _version_score(video)), default=None)


def offer_next_episode(vid, player):
    with open_library() as lib:
        current = lib.find_video(vid)
        if not current or current["kind"] != "episode" or not _episode_position(current):
            return
        videos = lib.videos_for_meta(current["meta_key"])
        watched = lib.watched()
        resume = lib.resume_points()
        completed = {_episode_position(video) for video in videos if video["id"] in watched}
        following = _next_episode(videos, _episode_position(current), completed, resume)
        meta = lib.get_meta(current["meta_key"]) or {}
    if following:
        episode = "%s %dx%02d" % (_display_title(meta) or following["title"], *_episode_position(following))
        _offer_playback(player, _(30418), episode, following["id"], following["id"] in resume)


def _offer_playback(player, heading, title, vid, resume):
    window = xbmcgui.Window(10000)
    if player.isPlaying() or window.getProperty(PLAYING_PROPERTY):
        return
    if xbmcgui.Dialog().yesno(heading, title, yeslabel=_(30419), autoclose=15000):
        if not player.isPlaying() and not window.getProperty(PLAYING_PROPERTY):
            mode = "resume" if resume else "noresume"
            url = url_for("play", vid=vid, continue_play=1 if resume else None)
            xbmc.executebuiltin("PlayMedia(%s,%s)" % (url, mode))


def in_progress(params):
    views_kodi._import_kodi_history()
    with open_library() as lib:
        resume = lib.resume_points()
        watched = lib.watched()
        entries = []
        series = {}
        movies = set()
        hidden = {meta["key"] for meta in lib.hidden_progress()}
        for video in lib.progress_history():
            if video["meta_key"] in hidden:
                continue
            vid = video["id"]
            if video["kind"] == "episode" and video["meta_key"]:
                if vid in resume or vid in watched:
                    series.setdefault(video["meta_key"], []).append(video)
            elif vid in resume or vid in watched:
                # rozhoduje naposledy přehraná verze: film dokoukaný v jiné verzi už rozkoukaný není
                key = video["meta_key"] or vid
                if key not in movies:
                    movies.add(key)
                    if vid in resume:
                        entries.append(video)
        episodes = lib.videos_grouped(series) if series else {}  # jeden dotaz místo dotazu na každý seriál
        for key, history in series.items():
            video = _series_progress(lib, history, resume, watched, episodes.get(key, []))
            if video:
                entries.append(video)
        entries.sort(key=lambda video: video["last_played"], reverse=True)
        entries = entries[:100]
        found = lib.get_metas(v["meta_key"] for v in entries)
        metas = {v["meta_key"]: found.get(v["meta_key"]) or {} for v in entries}

    keys = (_episode_metadata_key(metas[v["meta_key"]], v["season"]) for v in entries
            if v["kind"] == "episode" and v["episode"])
    metadata = views_catalog._cached_episode_metadata(keys, max_fetches=2)
    for video in entries:
        meta = metas[video["meta_key"]]
        title = _display_title(meta) or video["title"] or video["raw_title"]
        is_episode = video["kind"] == "episode"
        episode_data = {}
        if is_episode:
            season_key = _episode_metadata_key(meta, video["season"])
            episode_data = metadata.get(season_key, {}).get(str(video["episode"]), {})
            if video["episode"]:
                title = "%s · %s" % (title, views_catalog._episode_label(video, episode_data))
            else:
                title = video["raw_title"]
        if video.get("next_episode"):
            label = _(30365) % title
            progress = ""
        else:
            position, total = video["position"], video["total"]
            progress = _(30366) % (int(100 * position / total), max(1, int((total - position + 59) // 60)))
            label = "%s · %s" % (title, progress)
        add_video(video, meta, label=label, mediatype="episode" if is_episode else "movie",
                  progress=progress, progress_key=video["meta_key"], episode_data=episode_data)
    if hidden:
        add_dir(_(30414), "progress_hidden", "DefaultVideoDeleted.png")
    elif not entries:
        notify(_(30367))
    end("videos", cache=False)


def progress_hidden(params):
    with open_library() as lib:
        items = lib.hidden_progress()
    for item in items:
        add_action(_(30415) % _display_title(item), "progress_visibility", key=item["key"], hide=0)
    end(cache=False)


def progress_visibility(params):
    with open_library() as lib:
        lib.set_progress_hidden(params["key"], params.get("hide") == "1")
    xbmc.executebuiltin("Container.Refresh")


def _item_videos(lib, params):
    season = int(params["season"]) if "season" in params else None
    episode = int(params["episode"]) if "episode" in params else None
    videos = lib.videos_for_meta(params["key"], season=season)
    return [video for video in videos if episode is None or video["episode"] == episode]


def mark_watched(params):
    watched = params.get("watched") == "1"
    if "season" in params and "episode" not in params and watched and not ui.yesno(_(30416)):
        return
    with open_library() as lib:
        if "vid" in params:
            video = lib.find_video(params["vid"])
            if not video:
                return
            videos = [video]
            if video["kind"] == "movie":
                videos += lib.videos_for_meta(video["meta_key"])
            elif video["episode"]:
                videos += _item_videos(lib, {"key": video["meta_key"], "season": video["season"],
                                             "episode": video["episode"]})
        else:
            videos = _item_videos(lib, params)
        lib.set_watched(dict.fromkeys(video["id"] for video in videos), watched)
    _PLAYBACK.clear()
    if videos:
        views_kodi.publish_kodi_history({v["id"] for v in videos}, mark=watched)
    xbmc.executebuiltin("Container.Refresh")


def manage_item(params):
    with open_library() as lib:
        if "vid" in params:
            video = lib.find_video(params["vid"])
            videos = [video] if video else []
            key = video["meta_key"] if video else ""
            has_history = bool(video and lib.has_play_history(video["id"]))
        else:
            videos = _item_videos(lib, params)
            key = params["key"]
            has_history = False
        meta = lib.get_meta(key) if key else None
    options = []
    if meta:
        options.extend([(30373, views_edit.tmdb_repair), (30300, views_edit.meta_assign), (30301, views_edit.meta_refresh)])
    metadata_options = len(options)
    if videos:
        options.extend([(30325, views_edit.video_info), (30302, views_edit.video_edit),
                        (30304 if all(video["hidden"] for video in videos) else 30303, views_edit.video_hide),
                        (30305, views_edit.video_delete)])
        if has_history:
            options.append((30138, history_remove))
    if not options:
        return
    selection = ui.select(_(30409), [_(label) for label, _handler in options])
    if selection < 0:
        return
    handler = options[selection][1]
    if selection < metadata_options:
        handler({"key": key})
        return
    index = 0
    if len(videos) > 1:
        index = ui.select(_(30409), [video["raw_title"] for video in videos])
    if index >= 0:
        video = videos[index]
        handler({"vid": video["id"], "hide": str(int(not video["hidden"]))})


def played(params):
    views_catalog._video_list(lambda lib: lib.history_videos(), [xbmcplugin.SORT_METHOD_UNSORTED])


def history_remove(params):
    with open_library() as lib:
        lib.remove_history(params.get("vid"))
    xbmc.executebuiltin("Container.Refresh")


def history_clear(params):
    if ui.yesno(_(30238)):
        with open_library() as lib:
            lib.remove_history()
