# -*- coding: utf-8 -*-
"""Průběžný export do Kodi s evidencí vytvořených souborů."""
import json
import os
import sqlite3
import uuid
from collections import namedtuple
from contextlib import closing
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

import xbmcvfs

from exporter import _join, _nfo, _play_url, _safe

ExportResult = namedtuple(
    "ExportResult", "movies shows changed protected failed folder generation scan_pending removed",
    defaults=((),))


def _connect(state_file):
    conn = sqlite3.connect(state_file, timeout=20)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS exports (folder TEXT PRIMARY KEY, names TEXT NOT NULL, "
        "files TEXT NOT NULL, generation INTEGER NOT NULL, scanned INTEGER NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS exported_streams (folder TEXT NOT NULL, relative TEXT NOT NULL, "
                 "vid TEXT NOT NULL, PRIMARY KEY(folder, relative))")
    return conn


def export_folder(folder):
    path = xbmcvfs.translatePath(folder)
    return os.path.realpath(path) if "://" not in path else path.rstrip("/")


def exported_streams(state_file, folder):
    root = export_folder(folder)
    with closing(_connect(state_file)) as state:
        rows = state.execute("SELECT relative, vid FROM exported_streams WHERE folder=?", (root,))
        return {_join(root, *relative.split("/")): vid for relative, vid in rows}


def read_stream_id(path, addon_id):
    try:
        url = urlsplit(_read(path).strip())
        query = parse_qs(url.query)
    except (OSError, RuntimeError, ValueError):
        return None
    if url.scheme == "plugin" and url.netloc == addon_id and query.get("action") == ["play"]:
        return next(iter(query.get("vid", [])), None)
    return None


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
        grouped = lib.videos_grouped()
        for meta in lib.movies():
            videos = grouped.get(meta["key"], [])
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
            for video in grouped.get(meta["key"], []):
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


def _remove_stale(folder, local, files, owned):
    """Smaže soubory, které doplněk dříve vytvořil a jejichž titul už v katalogu není
    (smazané video, přečíslovaný díl, opravený název). Soubor, který mezitím někdo
    upravil, zůstane na místě a doplněk ho přestane považovat za svůj."""
    removed = []
    for relative in [name for name in owned if name not in files]:
        directory, _, filename = relative.rpartition("/")
        directory = _join(folder, *directory.split("/"))
        path = _join(directory, filename)
        try:
            if local and (os.path.islink(path) or os.path.commonpath(
                    (folder, os.path.realpath(directory))) != folder):
                continue
            if xbmcvfs.exists(path):
                if sha256(_read(path).encode("utf-8")).hexdigest() == owned[relative]:
                    if not xbmcvfs.delete(path):
                        continue  # zkusí se znovu při příštím exportu
                    removed.append(path)
            del owned[relative]
            while len(directory) > len(folder):  # prázdné složky titulu a série
                folders, names = xbmcvfs.listdir(directory + "/")
                if folders or names or not xbmcvfs.rmdir(directory + "/"):
                    break
                directory = directory.rpartition("/")[0] if "://" in directory else os.path.dirname(directory)
        except (OSError, RuntimeError, ValueError):
            continue
    return removed


def export(lib, folder, base_url, state_file):
    folder = export_folder(folder)
    local = "://" not in folder
    changed = protected = failed = 0
    with closing(_connect(state_file)) as state, state:
        state.execute("BEGIN IMMEDIATE")
        row = state.execute(
            "SELECT names, files, generation, scanned FROM exports WHERE folder=?", (folder,)).fetchone()
        raw_names, raw_owned, generation, scanned = row or ("{}", "{}", 0, 0)
        names, owned = json.loads(raw_names), json.loads(raw_owned)
        files, movies, shows = _catalogue_files(lib, names, base_url)
        links = {}
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
                if relative.endswith(".strm"):
                    links[relative] = parse_qs(urlsplit(content).query)["vid"][0]
            except (OSError, RuntimeError, ValueError):
                failed += 1
        removed = _remove_stale(folder, local, files, owned)
        changed += len(removed)
        generation += int(changed > 0 or (row is None and bool(owned)))
        state.execute("INSERT OR REPLACE INTO exports VALUES (?, ?, ?, ?, ?)",
                      (folder, json.dumps(names), json.dumps(owned), generation, scanned))
        state.execute("DELETE FROM exported_streams WHERE folder=?", (folder,))
        state.executemany("INSERT INTO exported_streams VALUES (?, ?, ?)",
                          ((folder, relative, vid) for relative, vid in links.items()))
    return ExportResult(movies, shows, changed, protected, failed, folder, generation, generation > scanned,
                        tuple(path for path in removed if path.endswith(".strm")))


def mark_scanned(state_file, result):
    with closing(_connect(state_file)) as state, state:
        state.execute("UPDATE exports SET scanned=MAX(scanned, ?) WHERE folder=?",
                      (result.generation, result.folder))
