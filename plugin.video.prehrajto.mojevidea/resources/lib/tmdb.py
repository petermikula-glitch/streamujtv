# -*- coding: utf-8 -*-
"""Záložní zdroj metadat: The Movie Database (TMDB), v češtině.

Použije se, jen když je v nastavení vyplněný vlastní (zdarma dostupný) API
klíč z https://www.themoviedb.org/settings/api – v3 klíč i v4 "Read Access Token".
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from nameparser import normalize

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
TIMEOUT = 20


class TmdbError(Exception):
    pass


class Tmdb(object):
    def __init__(self, key, language="cs-CZ", log=None):
        self.key = (key or "").strip()
        self.language = language
        self.log = log or (lambda m: None)

    @property
    def enabled(self):
        return bool(self.key)

    def _get(self, path, *, timeout=TIMEOUT, **params):
        params["language"] = self.language
        headers = {"Accept": "application/json", "User-Agent": "Kodi addon"}
        if len(self.key) > 40:  # v4 token
            headers["Authorization"] = "Bearer " + self.key
        else:
            params["api_key"] = self.key
        url = API + path + "?" + urllib.parse.urlencode(params)
        self.log("tmdb: GET %s" % path)
        try:
            resp = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)
            try:
                return json.loads(resp.read().decode("utf-8"))
            finally:
                resp.close()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise TmdbError("TMDB: neplatný API klíč")
            raise TmdbError("TMDB: HTTP %s" % e.code)
        except Exception as e:  # noqa: BLE001
            raise TmdbError("TMDB je nedostupné: %s" % e)

    def genres(self, kind):
        return self._get("/genre/%s/list" % kind).get("genres") or []

    def browse(self, kind, category, page=1, genre="", year=""):
        params = {"page": page}
        if category == "discover":
            params.update(sort_by="popularity.desc", include_adult="false")
            if genre:
                params["with_genres"] = genre
            if year:
                params["primary_release_year" if kind == "movie" else "first_air_date_year"] = year
            data = self._get("/discover/" + kind, **params)
        else:
            data = self._get("/%s/%s" % (kind, category), **params)
        return data.get("results") or [], min(data.get("total_pages") or 0, 500)

    def search_titles(self, query, page=1):
        data = self._get("/search/multi", query=query, include_adult="false", page=page)
        return ([item for item in data.get("results") or [] if item.get("media_type") in ("movie", "tv")],
                min(data.get("total_pages") or 0, 500))

    def search(self, query, want_series=False):
        """Seznam kandidátů pro ruční výběr: [{id, title, year, orig_title}]."""
        data = self._get("/search/" + ("tv" if want_series else "movie"), query=query,
                         include_adult="false")
        out = []
        for r in (data.get("results") or [])[:20]:
            date = r.get("release_date") or r.get("first_air_date") or ""
            out.append({"id": r["id"], "title": r.get("title") or r.get("name") or "",
                        "orig_title": r.get("original_title") or r.get("original_name") or "",
                        "year": int(date[:4]) if date[:4].isdigit() else 0})
        return out

    def find(self, title, year=0, want_series=False):
        kind = "tv" if want_series else "movie"
        params = {"query": title, "include_adult": "false"}
        if year:
            params["first_air_date_year" if want_series else "year"] = year
        data = self._get("/search/" + kind, **params)
        results = data.get("results") or []
        if not results and year:
            params.pop("first_air_date_year", None)
            params.pop("year", None)
            results = (self._get("/search/" + kind, **params).get("results") or [])
        if not results:
            return None
        target = normalize(title)

        def score(r):
            names = [r.get("title") or r.get("name") or "", r.get("original_title") or r.get("original_name") or ""]
            s = 0
            if any(normalize(n) == target for n in names):
                s += 50
            date = r.get("release_date") or r.get("first_air_date") or ""
            if year and date[:4].isdigit():
                d = abs(int(date[:4]) - int(year))
                s += 30 if d == 0 else (15 if d == 1 else -20)
            s += min(10, (r.get("vote_count") or 0) // 100)
            return s
        best = max(results[:10], key=score)
        return self.details(best["id"], want_series)

    def season_episodes(self, tmdb_id, season):
        data = self._get("/tv/%d/season/%d" % (tmdb_id, season), timeout=4)
        episodes = {}
        for episode in data.get("episodes") or []:
            number = episode.get("episode_number")
            if not isinstance(number, int) or number <= 0:
                continue
            still = episode.get("still_path") or ""
            runtime = episode.get("runtime")
            episodes[str(number)] = {
                "title": episode.get("name") or "",
                "plot": episode.get("overview") or "",
                "duration": runtime * 60 if isinstance(runtime, int) and runtime > 0 else 0,
                "still": IMG + "w500" + still if isinstance(still, str) and still.startswith("/") else "",
            }
        return episodes

    def details(self, tmdb_id, want_series=False):
        kind = "tv" if want_series else "movie"
        d = self._get("/%s/%s" % (kind, tmdb_id), append_to_response="credits")
        date = d.get("release_date") or d.get("first_air_date") or ""
        credits = d.get("credits") or {}
        directors = [c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"]
        if want_series and not directors:
            directors = [c["name"] for c in d.get("created_by", []) if c.get("name")]
        runtime = d.get("runtime") or 0
        if not runtime and d.get("episode_run_time"):
            runtime = d["episode_run_time"][0]
        return {
            "tmdb_id": d.get("id", tmdb_id),
            "title": d.get("title") or d.get("name") or "",
            "orig_title": d.get("original_title") or d.get("original_name") or "",
            "year": int(date[:4]) if date[:4].isdigit() else 0,
            "rating": int(round((d.get("vote_average") or 0) * 10)),
            "votes": d.get("vote_count") or 0,
            "genres": [g["name"] for g in d.get("genres", []) if g.get("name")],
            "countries": [c.get("name") or c.get("iso_3166_1") for c in d.get("production_countries", [])],
            "directors": directors,
            "cast": [c["name"] for c in credits.get("cast", [])[:15] if c.get("name")],
            "plot": d.get("overview") or "",
            "poster": IMG + "w500" + d["poster_path"] if d.get("poster_path") else "",
            "fanart": IMG + "w1280" + d["backdrop_path"] if d.get("backdrop_path") else "",
            "runtime": runtime or 0,
        }
