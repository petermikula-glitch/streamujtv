# -*- coding: utf-8 -*-
"""Router doplňku: podle akce načte jen modul s danou obrazovkou.

Obrazovky jsou v modulech ``views_*.py`` a ``playback.py``, společné prvky v ``ui.py``.
Jména z těchto modulů jsou dostupná i jako ``plugin.<jméno>`` (služba na pozadí, testy).
"""
import importlib
import sys
import urllib.parse

import ui
from kodiutils import log, log_error

MODULES = ("ui", "views_catalog", "views_progress", "views_discover", "views_search", "views_collections",
           "views_tools", "views_kodi", "views_edit", "playback")

# akce -> "modul.funkce"
ROUTES = {
    # views_catalog
    "root": "views_catalog.root",
    "movies_menu": "views_catalog.movies_menu",
    "movies": "views_catalog.movies",
    "movie_genres": "views_catalog.movie_genres",
    "movie_decades": "views_catalog.movie_decades",
    "movie_countries": "views_catalog.movie_countries",
    "movie_ratings": "views_catalog.movie_ratings",
    "versions": "views_catalog.versions",
    "shows_menu": "views_catalog.shows_menu",
    "shows": "views_catalog.shows",
    "show": "views_catalog.show",
    "season": "views_catalog.season",
    "recent": "views_catalog.recent",
    "all_videos": "views_catalog.all_videos",
    "unmatched": "views_catalog.unmatched",
    "hidden": "views_catalog.hidden",
    "news_menu": "views_catalog.news_menu",
    "my_videos_menu": "views_catalog.my_videos_menu",
    "season_status": "views_catalog.season_status",
    "movie_filter_create": "views_catalog.movie_filter_create",
    "movie_filter_save": "views_catalog.movie_filter_save",
    "movie_filter_delete": "views_catalog.movie_filter_delete",
    "episode_detail": "views_catalog.episode_detail",
    "new_episodes": "views_catalog.new_episodes",
    # views_progress
    "in_progress": "views_progress.in_progress",
    "played": "views_progress.played",
    "history_remove": "views_progress.history_remove",
    "history_clear": "views_progress.history_clear",
    "manage_item": "views_progress.manage_item",
    "mark_watched": "views_progress.mark_watched",
    "progress_hidden": "views_progress.progress_hidden",
    "progress_visibility": "views_progress.progress_visibility",
    # views_discover
    "tmdb_catalog": "views_discover.tmdb_catalog",
    "tmdb_categories": "views_discover.tmdb_categories",
    "tmdb_filters": "views_discover.tmdb_filters",
    "tmdb_titles": "views_discover.tmdb_titles",
    "tmdb_title": "views_discover.tmdb_title",
    "tmdb_facets": "views_discover.tmdb_facets",
    "tmdb_refine": "views_discover.tmdb_refine",
    "movie_parts": "views_discover.movie_parts",
    "tmdb_search": "views_discover.tmdb_search",
    "tmdb_movie_versions": "views_discover.tmdb_movie_versions",
    "watchlist": "views_discover.watchlist",
    "watchlist_change": "views_discover.watchlist_change",
    "watchlist_acknowledge": "views_discover.watchlist_acknowledge",
    # views_search
    "search_menu": "views_search.search_menu",
    "search": "views_search.search",
    "search_delete": "views_search.search_delete",
    "search_title": "views_search.search_title",
    "search_people": "views_search.search_people",
    "person_titles": "views_search.person_titles",
    "provider_search": "views_search.provider_search",
    "provider_save": "views_search.provider_save",
    "online_filters": "views_search.online_filters",
    # views_collections
    "collections": "views_collections.collections",
    "collection": "views_collections.collection",
    "collection_new": "views_collections.collection_new",
    "collection_rename": "views_collections.collection_rename",
    "collection_delete": "views_collections.collection_delete",
    "collection_add": "views_collections.collection_add",
    "collection_remove": "views_collections.collection_remove",
    # views_tools
    "backup_catalog": "views_tools.backup_catalog",
    "restore_catalog": "views_tools.restore_catalog",
    "tools": "views_tools.tools",
    "sync": "views_tools.sync",
    "enrich": "views_tools.enrich",
    "refresh_all": "views_tools.refresh_all",
    "add_link": "views_tools.add_link",
    "import_file": "views_tools.import_file",
    "account_status": "views_tools.account_status",
    "stats": "views_tools.stats",
    "clear_library": "views_tools.clear_library",
    "settings": "views_tools.open_settings",
    "diagnostics": "views_tools.diagnostics",
    # views_kodi
    "export_library": "views_kodi.export_library",
    "kodi_setup": "views_kodi.kodi_setup",
    "kodi_choose_folder": "views_kodi.kodi_choose_folder",
    "kodi_instructions": "views_kodi.kodi_instructions",
    "kodi_sources": "views_kodi.kodi_sources",
    "kodi_scan": "views_kodi.kodi_scan",
    "kodi_status": "views_kodi.kodi_status",
    "kodi_import": "views_kodi.kodi_import",
    "kodi_enable_history": "views_kodi.kodi_enable_history",
    # views_edit
    "repair_title": "views_edit.repair_title",
    "tmdb_repair": "views_edit.tmdb_repair",
    "meta_assign": "views_edit.meta_assign",
    "meta_refresh": "views_edit.meta_refresh",
    "video_edit": "views_edit.video_edit",
    "video_hide": "views_edit.video_hide",
    "video_delete": "views_edit.video_delete",
    "video_info": "views_edit.video_info",
    "batch_episodes": "views_edit.batch_episodes",
    # playback
    "play": "playback.play",
}


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        if name in vars(module):
            return getattr(module, name)
    raise AttributeError("module 'plugin' has no attribute %r" % name)


def run(argv=None):
    argv = argv or sys.argv
    ui.BASE_URL = argv[0]
    ui._PLAYBACK.clear()
    del ui._PENDING_ITEMS[:]
    ui.HANDLE = int(argv[1]) if len(argv) > 1 and argv[1].lstrip("-").isdigit() else -1
    query = argv[2][1:] if len(argv) > 2 and argv[2].startswith("?") else (argv[2] if len(argv) > 2 else "")
    params = dict(urllib.parse.parse_qsl(query))
    action = params.pop("action", "root")
    if action == "play" and params.get("retry") != "1" and len(argv) > 3:
        if argv[3] == "resume:false":
            params["restart"] = "1"
        elif argv[3] == "resume:true" and params.get("offer_transfer") == "1":
            params["resume_requested"] = "1"
    log("route %s %s" % (action, params))
    target = ROUTES.get(action)
    if target is None:
        log_error("neznámá akce %s" % action)
        return
    module_name, function = target.rsplit(".", 1)
    getattr(importlib.import_module(module_name), function)(params)
