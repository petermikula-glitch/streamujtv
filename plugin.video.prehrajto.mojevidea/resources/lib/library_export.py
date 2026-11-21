# -*- coding: utf-8 -*-
"""Průběžný export do Kodi s evidencí vytvořených souborů."""
import json
import os
import sqlite3
import uuid
from collections import namedtuple
from contextlib import closing
from hashlib import sha256
from xml.sax.saxutils import escape

import xbmcvfs

from exporter import _join, _nfo, _play_url, _safe

ExportResult = namedtuple(
    "ExportResult", "movies shows changed protected failed folder generation scan_pending")


def _connect(state_file):
    conn = sqlite3.connect(state_file, timeout=20)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS exports (folder TEXT PRIMARY KEY, names TEXT NOT NULL, "
        "files TEXT NOT NULL, generation INTEGER NOT NULL, scanned INTEGER NOT NULL)")
    return conn


def _directory_name(meta, names, used):
    key = meta["key"]
    name = names.get(key)
    if name:
        return name
    title = meta.get("title") or meta.get("search_title")
    year = meta.get("year") or meta.get("search_year")
    is_movie = meta["kind"] == "movie"
    base = _safe("%s (%s)" % (title, year) if is_movie and year else title)
    name = base
    counter = 0
    while (is_movie, name.casefold()) in used:
        token = sha256(("%s:%d" % (key, counter)).encode("utf-8")).hexdigest()[:12]
        suffix = " [%s]" % token
        name = _safe(base[:120 - len(suffix)] + suffix)
        counter += 1
    names[key] = name
    used.add((is_movie, name.casefold()))
    return name


def _catalogue_files(lib, names, base_url):
    files = {}
    used = {(key.startswith("movie:"), name.casefold()) for key, name in names.items()}
    movies = shows = 0
    with lib.conn:
        lib.conn.execute("BEGIN")
        for meta in lib.movies():
            videos = lib.videos_for_meta(meta["key"])
            if not videos:
                continue
            name = _directory_name(meta, names, used)
            path = "Filmy/%s/%s" % (name, name)
            files[path + ".strm"] = _play_url(base_url, videos[0]["id"])
            files[path + ".nfo"] = _nfo("movie", meta)
            movies += 1
        for meta in lib.shows():
            name = _directory_name(meta, names, used)
            path = "Serialy/" + name
            files[path + "/tvshow.nfo"] = _nfo("tvshow", meta)
            for video in lib.videos_for_meta(meta["key"]):
                season, episode = video["season"], video["episode"]
                if not episode:
                    continue
                code = "S%02dE%02d" % (season, episode)
                stem = "%s/Season %02d/%s %s" % (path, season, name, code)
                if stem + ".strm" in files:
                    continue
                files[stem + ".strm"] = _play_url(base_url, video["id"])
                title = meta.get("title") or meta.get("search_title") or name
                extra = ("  <showtitle>%s</showtitle>\n  <season>%d</season>\n  <episode>%d</episode>\n"
                         % (escape(title), season, episode))
                if video.get("duration"):
                    extra += "  <runtime>%d</runtime>\n" % max(1, round(video["duration"] / 60))
                files[stem + ".nfo"] = _nfo("episodedetails", {"title": "%s %s" % (title, code)}, extra)
            shows += 1
    return files, movies, shows


def _read(path):
    file = xbmcvfs.File(path)
    try:
        return file.read()
    finally:
        file.close()


def _replace_file(directory, path, content, existed):
    if not xbmcvfs.exists(directory + "/"):
        if not xbmcvfs.mkdirs(directory) and not xbmcvfs.exists(directory + "/"):
            raise OSError("Cannot create the export folder")
    temporary = path + "." + uuid.uuid4().hex + ".tmp"
    remote = "://" in path
    try:
        file = xbmcvfs.File(temporary, "w")
        try:
            if not file.write(content):
                raise OSError("Cannot write the export file")
        finally:
            file.close()
        if not remote:
            os.replace(temporary, path)
        elif not xbmcvfs.rename(temporary, path):
            # Některé síťové VFS nepovolují přejmenování přes existující soubor.
            backup = temporary + ".bak"
            if not existed or not xbmcvfs.rename(path, backup):
                raise OSError("Cannot replace the export file")
            if not xbmcvfs.rename(temporary, path):
                xbmcvfs.rename(backup, path)
                raise OSError("Cannot replace the export file")
            xbmcvfs.delete(backup)
    finally:
        if xbmcvfs.exists(temporary):
            xbmcvfs.delete(temporary) if remote else os.unlink(temporary)


def export(lib, folder, base_url, state_file):
    folder = xbmcvfs.translatePath(folder)
    local = "://" not in folder
    folder = os.path.realpath(folder) if local else folder.rstrip("/")
    changed = protected = failed = 0
    with closing(_connect(state_file)) as state, state:
        state.execute("BEGIN IMMEDIATE")
        row = state.execute(
            "SELECT names, files, generation, scanned FROM exports WHERE folder=?", (folder,)).fetchone()
        raw_names, raw_owned, generation, scanned = row or ("{}", "{}", 0, 0)
        names, owned = json.loads(raw_names), json.loads(raw_owned)
        files, movies, shows = _catalogue_files(lib, names, base_url)
        for relative, content in files.items():
            directory, _, filename = relative.rpartition("/")
            directory = _join(folder, *directory.split("/"))
            path = _join(directory, filename)
            try:
                if local and (os.path.islink(path) or os.path.commonpath(
                        (folder, os.path.realpath(directory))) != folder):
                    protected += 1
                    continue
                exists = xbmcvfs.exists(path)
                current = _read(path) if exists else None
                if current != content:
                    if exists and owned.get(relative) != sha256(current.encode("utf-8")).hexdigest():
                        protected += 1
                        continue
                    _replace_file(directory, path, content, exists)
                    changed += 1
                owned[relative] = sha256(content.encode("utf-8")).hexdigest()
            except (OSError, RuntimeError, ValueError):
                failed += 1
        generation += int(changed > 0 or (row is None and bool(owned)))
        state.execute("INSERT OR REPLACE INTO exports VALUES (?, ?, ?, ?, ?)",
                      (folder, json.dumps(names), json.dumps(owned), generation, scanned))
    return ExportResult(movies, shows, changed, protected, failed, folder, generation, generation > scanned)


def mark_scanned(state_file, result):
    with closing(_connect(state_file)) as state, state:
        state.execute("UPDATE exports SET scanned=MAX(scanned, ?) WHERE folder=?",
                      (result.generation, result.folder))
