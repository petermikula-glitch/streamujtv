# -*- coding: utf-8 -*-
"""Záložní zdroj metadat: The Movie Database (TMDB), v češtině.

Použije se, jen když je v nastavení vyplněný vlastní (zdarma dostupný) API
klíč z https://www.themoviedb.org/settings/api – v3 klíč i v4 "Read Access Token".
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from nameparser import normalize
from search_results import period

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
TIMEOUT = 20
HOUR = 3600
# Jak dlouho platí uložená odpověď (podle začátku cesty); ostatní dotazy den.
CACHE_TTL = (("/genre/", 7 * 24 * HOUR), ("/configuration/", 7 * 24 * HOUR), ("/search/", HOUR),
             ("/discover/", 6 * HOUR), ("/movie/popular", 6 * HOUR), ("/movie/top_rated", 6 * HOUR),
             ("/movie/now_playing", 6 * HOUR), ("/movie/upcoming", 6 * HOUR), ("/tv/popular", 6 * HOUR),
             ("/tv/top_rated", 6 * HOUR), ("/tv/on_the_air", 6 * HOUR), ("/tv/airing_today", 6 * HOUR),
             ("/trending/", 6 * HOUR))
DEFAULT_TTL = 24 * HOUR


def _expected_shape(path, data):
    """Odpovědi TMDB jsou objekty; jen číselníky pod /configuration/ jsou seznamy."""
    if path.startswith("/configuration/"):
        return isinstance(data, (list, dict))
    return isinstance(data, dict)


def cache_ttl(path):
    for prefix, ttl in CACHE_TTL:
        if path.startswith(prefix):
            return ttl
    return DEFAULT_TTL


class TmdbError(Exception):
    pass


class Tmdb(object):
    def __init__(self, key, language="cs-CZ", log=None, timeout=TIMEOUT, cache=None):
        """``cache`` – objekt s ``get(klíč)`` a ``put(klíč, data, ttl)``; chyby se neukládají."""
        self.key = (key or "").strip()
        self.language = language
        self.log = log or (lambda m: None)
        self.timeout = timeout
        self.cache = cache

    @property
    def enabled(self):
        return bool(self.key)

    def _get(self, path, *, timeout=None, **params):
        params["language"] = self.language
        cache_key = "tmdb:" + path + "?" + urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items()))
        if self.cache is not None:
            try:
                cached = self.cache.get(cache_key)
            except Exception:  # noqa: BLE001 – mezipaměť je jen zrychlení
                cached = None
            if _expected_shape(path, cached):
                return cached
        data = self._fetch(path, timeout, params)
        if not _expected_shape(path, data):
            # např. filtrující proxy vrátí 200 s jiným obsahem – neukládat a nehlásit jako prázdný výsledek
            raise TmdbError("TMDB vrátilo neočekávanou odpověď")
        if self.cache is not None:
            try:
                self.cache.put(cache_key, data, cache_ttl(path))
            except Exception:  # noqa: BLE001
                pass
        return data

    def _fetch(self, path, timeout, params):
        params = dict(params)
        headers = {"Accept": "application/json", "User-Agent": "Kodi addon"}
        if len(self.key) > 40:  # v4 token
            headers["Authorization"] = "Bearer " + self.key
        else:
            params["api_key"] = self.key
        url = API + path + "?" + urllib.parse.urlencode(params)
        self.log("tmdb: GET %s" % path)
        try:
            resp = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout or self.timeout)
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

    def countries(self):
        return self._get("/configuration/countries")

    def browse(self, kind, category, page=1, genre="", year="", origin=""):
        params = {"page": page}
        if category in ("discover", "recent"):
            params.update(sort_by="popularity.desc", include_adult="false")
            if origin and kind == "movie":
                params["with_origin_country"] = origin
            if category == "recent":
                today = date.today()
                params.update({"sort_by": "primary_release_date.desc",
                               "primary_release_date.gte": (today - timedelta(days=90)).isoformat(),
                               "primary_release_date.lte": today.isoformat()})
            if genre:
                params["with_genres"] = genre
            start, end = period(str(year))
            if start == end and start:
                params["primary_release_year" if kind == "movie" else "first_air_date_year"] = str(start)
            elif start:
                field = "primary_release_date" if kind == "movie" else "first_air_date"
                low, high = "%d-01-01" % start, "%d-12-31" % end
                params[field + ".gte"] = max(low, params.get(field + ".gte", low))
                params[field + ".lte"] = min(high, params.get(field + ".lte", high))
                if params[field + ".gte"] > params[field + ".lte"]:
                    return [], 0
            data = self._get("/discover/" + kind, **params)
        else:
            data = self._get("/%s/%s" % (kind, category), **params)
        return data.get("results") or [], min(data.get("total_pages") or 0, 500)

    def search_titles(self, query, page=1):
        data = self._get("/search/multi", query=query, include_adult="false", page=page)
        return ([item for item in data.get("results") or [] if item.get("media_type") in ("movie", "tv")],
                min(data.get("total_pages") or 0, 500))

    def alternative_titles(self, item):
        kind = item["media_type"]
        data = self._get("/%s/%d/alternative_titles" % (kind, item["id"]))
        alternatives = data.get("titles" if kind == "movie" else "results") or []
        return list(dict.fromkeys(entry["title"].strip() for entry in alternatives
                                  if entry.get("iso_3166_1") in ("CZ", "SK") and entry.get("title", "").strip()))

    def search_people(self, query, page=1):
        data = self._get("/search/person", query=query, page=page, include_adult="false")
        people = [item for item in data.get("results") or [] if item.get("id") and item.get("name")]
        return people, min(data.get("total_pages") or 0, 500)

    def acting_credits(self, person_id):
        data = self._get("/person/%d/combined_credits" % person_id)
        entries = {}
        for item in data.get("cast") or []:
            key = item.get("media_type"), item.get("id")
            if key[0] in ("movie", "tv") and key[1] and not item.get("adult"):
                entries.setdefault(key, item)
        return sorted(entries.values(), key=lambda item: item.get("release_date") or item.get("first_air_date") or "",
                      reverse=True)

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

    def collection_movies(self, collection_id):
        parts = self._get("/collection/%d" % collection_id).get("parts") or []
        return sorted((part for part in parts if part.get("id") and part.get("title")),
                      key=lambda part: (part.get("release_date") or "9999-99-99", part["id"]))

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
            "collection_id": (d.get("belongs_to_collection") or {}).get("id") or 0,
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
