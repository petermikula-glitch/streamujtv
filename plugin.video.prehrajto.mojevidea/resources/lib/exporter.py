# -*- coding: utf-8 -*-
"""Export katalogu do knihovny Kodi (.strm + .nfo).

Filmy:   <složka>/Filmy/<Název (rok)>/<Název (rok)>.strm
Seriály: <složka>/Serialy/<Název>/Season NN/<Název> SxxEyy.strm
Ke každému titulu se zapíše .nfo s údaji z katalogu (název, rok, děj,
hodnocení ČSFD), takže je Kodi nemusí stahovat ze scraperu.
"""
import re
import urllib.parse
from xml.sax.saxutils import escape

import xbmcvfs


def _safe(name):
    name = re.sub(r'[\\/:*?"<>|]+', " ", name or "").strip(" .")
    return re.sub(r"\s+", " ", name)[:120] or "Bez nazvu"


def _join(*parts):
    sep = "\\" if "\\" in parts[0] and "/" not in parts[0] else "/"
    out = parts[0].rstrip("/\\")
    for p in parts[1:]:
        out += sep + p
    return out


def _write(path, content):
    f = xbmcvfs.File(path, "w")
    try:
        f.write(content)
    finally:
        f.close()


def _mkdirs(path):
    if not xbmcvfs.exists(path + "/"):
        xbmcvfs.mkdirs(path)


def _play_url(base_url, vid):
    return base_url + "?" + urllib.parse.urlencode({"action": "play", "vid": vid})


def _nfo(root, meta, extra=""):
    def tag(name, value):
        return "  <%s>%s</%s>\n" % (name, escape(str(value)), name) if value else ""
    title = meta.get("title") or meta.get("search_title")
    year = meta.get("year") or meta.get("search_year")
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<%s>\n' % root
    xml += tag("title", title) + tag("originaltitle", meta.get("orig_title")) + tag("year", year)
    xml += tag("plot", meta.get("plot"))
    if meta.get("rating"):
        src = meta.get("rating_source") or "csfd"
        xml += ('  <ratings>\n    <rating name="%s" max="10" default="true">\n'
                '      <value>%.1f</value>\n      <votes>%d</votes>\n    </rating>\n  </ratings>\n'
                % (escape(src), meta["rating"] / 10.0, meta.get("votes") or 0))
    for g in meta.get("genres") or []:
        xml += tag("genre", g)
    for c in meta.get("countries") or []:
        xml += tag("country", c)
    for d in meta.get("directors") or []:
        xml += tag("director", d)
    for a in meta.get("cast") or []:
        xml += "  <actor>\n  " + tag("name", a) + "  </actor>\n"
    if meta.get("poster"):
        xml += '  <thumb aspect="poster">%s</thumb>\n' % escape(meta["poster"])
    if meta.get("csfd_id"):
        xml += '  <uniqueid type="csfd">%s</uniqueid>\n' % meta["csfd_id"]
    if meta.get("tmdb_id"):
        xml += '  <uniqueid type="tmdb">%s</uniqueid>\n' % meta["tmdb_id"]
    xml += extra
    xml += "</%s>\n" % root
    return xml


def export(lib, folder, base_url):
    movies_dir = _join(folder, "Filmy")
    shows_dir = _join(folder, "Serialy")
    n_movies = n_shows = 0
    for meta in lib.movies():
        title = meta.get("title") or meta.get("search_title")
        year = meta.get("year") or meta.get("search_year")
        name = _safe("%s (%s)" % (title, year) if year else title)
        d = _join(movies_dir, name)
        _mkdirs(d)
        videos = lib.videos_for_meta(meta["key"])
        if not videos:
            continue
        _write(_join(d, name + ".strm"), _play_url(base_url, videos[0]["id"]))
        _write(_join(d, name + ".nfo"), _nfo("movie", meta))
        n_movies += 1
    for meta in lib.shows():
        title = meta.get("title") or meta.get("search_title")
        name = _safe(title)
        d = _join(shows_dir, name)
        _mkdirs(d)
        _write(_join(d, "tvshow.nfo"), _nfo("tvshow", meta))
        done = set()
        for v in lib.videos_for_meta(meta["key"]):
            if not v["episode"]:
                continue
            code = "S%02dE%02d" % (v["season"], v["episode"])
            if code in done:
                continue  # víc verzí stejného dílu – exportuje se první
            done.add(code)
            sd = _join(d, "Season %02d" % v["season"])
            _mkdirs(sd)
            _write(_join(sd, "%s %s.strm" % (name, code)), _play_url(base_url, v["id"]))
        n_shows += 1
    return n_movies, n_shows
