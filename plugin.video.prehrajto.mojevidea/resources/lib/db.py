# -*- coding: utf-8 -*-
"""Lokální katalog (SQLite) – videa z účtu, metadata z ČSFD/TMDB, kolekce."""
import json
import sqlite3
import time

from nameparser import movie_key, normalize, show_key

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,           -- ID videa u poskytovatele
    raw_title TEXT NOT NULL,       -- název tak, jak je u poskytovatele
    source TEXT NOT NULL,          -- account | favorites | playlist | manual
    added_at INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    duration INTEGER DEFAULT 0,
    thumb TEXT DEFAULT '',
    description TEXT DEFAULT '',
    kind TEXT NOT NULL,            -- movie | episode
    title TEXT NOT NULL,
    year INTEGER DEFAULT 0,
    season INTEGER DEFAULT 0,
    episode INTEGER DEFAULT 0,
    lang TEXT DEFAULT '',
    quality TEXT DEFAULT '',
    meta_key TEXT NOT NULL,        -- odkaz do tabulky meta
    locked INTEGER DEFAULT 0,      -- 1 = uživatel ručně upravil název/typ, sync nepřepisuje
    hidden INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_videos_meta ON videos(meta_key);
CREATE INDEX IF NOT EXISTS idx_videos_kind ON videos(kind);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,          -- movie:<název>|<rok> nebo show:<název>
    kind TEXT NOT NULL,            -- movie | show
    search_title TEXT NOT NULL,
    search_year INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',     -- new | ok | notfound | blocked | error | manual
    source TEXT DEFAULT '',        -- csfd | tmdb | manual
    csfd_id INTEGER DEFAULT 0,
    csfd_url TEXT DEFAULT '',
    tmdb_id INTEGER DEFAULT 0,
    title TEXT DEFAULT '',
    orig_title TEXT DEFAULT '',
    year INTEGER DEFAULT 0,
    rating INTEGER DEFAULT 0,      -- 0–100 %
    rating_source TEXT DEFAULT '', -- csfd | tmdb | manual
    votes INTEGER DEFAULT 0,
    genres TEXT DEFAULT '[]',      -- JSON seznam
    countries TEXT DEFAULT '[]',
    directors TEXT DEFAULT '[]',
    cast TEXT DEFAULT '[]',
    plot TEXT DEFAULT '',
    poster TEXT DEFAULT '',
    fanart TEXT DEFAULT '',
    runtime INTEGER DEFAULT 0,     -- minuty
    fetched_at INTEGER DEFAULT 0,
    search_blob TEXT DEFAULT ''    -- normalizovaný text pro vyhledávání
);
CREATE INDEX IF NOT EXISTS idx_meta_status ON meta(status);

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created INTEGER NOT NULL,
    source TEXT DEFAULT 'user'     -- user | playlist
);
CREATE TABLE IF NOT EXISTS collection_items (
    collection_id INTEGER NOT NULL,
    target TEXT NOT NULL,          -- meta key (film/seriál) nebo v:<id videa>
    added INTEGER NOT NULL,
    PRIMARY KEY (collection_id, target)
);
CREATE TABLE IF NOT EXISTS search_history (
    query TEXT PRIMARY KEY,
    ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings_kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""

JSON_FIELDS = ("genres", "countries", "directors", "cast")
META_FIELDS = ("status", "source", "csfd_id", "csfd_url", "tmdb_id", "title", "orig_title", "year",
               "rating", "rating_source", "votes", "genres", "countries", "directors", "cast",
               "plot", "poster", "fanart", "runtime")


def meta_key_for(kind, title, year):
    if kind == "episode":
        return "show:" + show_key(title)
    return "movie:" + movie_key(title, year)


def _row(row):
    if row is None:
        return None
    d = dict(row)
    for f in JSON_FIELDS:
        if f in d:
            try:
                d[f] = json.loads(d[f] or "[]")
            except ValueError:
                d[f] = []
    return d


class Library(object):
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path, timeout=20)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO settings_kv(k, v) VALUES ('schema', ?)",
                          (str(SCHEMA_VERSION),))
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- key/value ---------------------------------------------------------
    def get_kv(self, k, default=None):
        row = self.conn.execute("SELECT v FROM settings_kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else default

    def set_kv(self, k, v):
        self.conn.execute("INSERT OR REPLACE INTO settings_kv(k, v) VALUES (?, ?)", (k, str(v)))
        self.conn.commit()

    # --- videa -------------------------------------------------------------
    def get_video(self, vid):
        return _row(self.conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone())

    def upsert_video(self, vid, raw_title, source, parsed, duration=0, thumb="", description="",
                     now=None):
        """Vloží nebo aktualizuje video. Vrací True, pokud je nové."""
        now = now or int(time.time())
        existing = self.get_video(vid)
        if existing:
            updates = {"last_seen": now}
            if duration and not existing["duration"]:
                updates["duration"] = duration
            if thumb and not existing["thumb"]:
                updates["thumb"] = thumb
            if description and not existing["description"]:
                updates["description"] = description
            # přednost má zdroj "account" před ostatními
            if source == "account" and existing["source"] != "account":
                updates["source"] = source
            if not existing["locked"] and raw_title and raw_title != existing["raw_title"]:
                updates.update(self._parsed_fields(raw_title, parsed))
            sets = ", ".join("%s=?" % k for k in updates)
            self.conn.execute("UPDATE videos SET %s WHERE id=?" % sets, list(updates.values()) + [vid])
            self._ensure_meta_for_video(vid)
            self.conn.commit()
            return False
        fields = self._parsed_fields(raw_title, parsed)
        fields.update({"id": vid, "source": source, "added_at": now, "last_seen": now,
                       "duration": duration or 0, "thumb": thumb or "", "description": description or ""})
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        self.conn.execute("INSERT INTO videos (%s) VALUES (%s)" % (cols, marks), list(fields.values()))
        self._ensure_meta_for_video(vid)
        self.conn.commit()
        return True

    def _parsed_fields(self, raw_title, parsed):
        key = meta_key_for(parsed["kind"], parsed["title"], parsed.get("year"))
        if parsed["kind"] == "movie" and not parsed.get("year"):
            # film bez roku v názvu patří k jedinému stejnojmennému filmu s rokem
            prefix = "movie:" + normalize(parsed["title"]) + "|"
            rows = self.conn.execute("SELECT key FROM meta WHERE kind='movie' AND key LIKE ? "
                                     "AND key != ?", (prefix + "%", key)).fetchall()
            if len(rows) == 1:
                key = rows[0][0]
        return {
            "raw_title": raw_title,
            "kind": parsed["kind"],
            "title": parsed["title"],
            "year": parsed.get("year") or 0,
            "season": parsed.get("season") or 0,
            "episode": parsed.get("episode") or 0,
            "lang": parsed.get("lang") or "",
            "quality": parsed.get("quality") or "",
            "meta_key": key,
        }

    def _ensure_meta_for_video(self, vid):
        v = self.get_video(vid)
        kind = "show" if v["kind"] == "episode" else "movie"
        self.conn.execute(
            "INSERT OR IGNORE INTO meta (key, kind, search_title, search_year, title, year, search_blob) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (v["meta_key"], kind, v["title"], v["year"] if kind == "movie" else 0, v["title"],
             v["year"] if kind == "movie" else 0, normalize(v["title"])))

    def edit_video(self, vid, kind, title, year=0, season=0, episode=0):
        """Ruční úprava (uzamkne video proti přepsání synchronizací)."""
        key = meta_key_for(kind, title, year)
        self.conn.execute(
            "UPDATE videos SET kind=?, title=?, year=?, season=?, episode=?, meta_key=?, locked=1 "
            "WHERE id=?", (kind, title, year or 0, season or 0, episode or 0, key, vid))
        self._ensure_meta_for_video(vid)
        self.conn.commit()
        self.cleanup_meta()
        return key

    def set_hidden(self, vid, hidden=True):
        self.conn.execute("UPDATE videos SET hidden=? WHERE id=?", (1 if hidden else 0, vid))
        self.conn.commit()

    def delete_video(self, vid):
        self.conn.execute("DELETE FROM videos WHERE id=?", (vid,))
        self.conn.execute("DELETE FROM collection_items WHERE target=?", ("v:" + vid,))
        self.conn.commit()
        self.cleanup_meta()

    def remove_missing(self, source, seen_before):
        """Smaže videa daného zdroje, která při poslední synchronizaci nebyla na účtu."""
        rows = self.conn.execute("SELECT id FROM videos WHERE source=? AND last_seen<?",
                                 (source, seen_before)).fetchall()
        for r in rows:
            self.conn.execute("DELETE FROM videos WHERE id=?", (r[0],))
            self.conn.execute("DELETE FROM collection_items WHERE target=?", ("v:" + r[0],))
        self.conn.commit()
        self.cleanup_meta()
        return len(rows)

    def cleanup_meta(self):
        self.conn.execute("DELETE FROM meta WHERE key NOT IN (SELECT DISTINCT meta_key FROM videos)")
        self.conn.execute("DELETE FROM collection_items WHERE target NOT LIKE 'v:%' AND "
                          "target NOT IN (SELECT key FROM meta)")
        self.conn.commit()

    def count_videos(self):
        return self.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]

    def clear(self):
        for t in ("videos", "meta", "collection_items", "collections"):
            self.conn.execute("DELETE FROM %s" % t)
        self.conn.commit()

    # --- metadata ----------------------------------------------------------
    def get_meta(self, key):
        return _row(self.conn.execute("SELECT * FROM meta WHERE key=?", (key,)).fetchone())

    def update_meta(self, key, data):
        data = dict(data)
        values = {}
        for f in META_FIELDS:
            if f in data and data[f] is not None:
                v = data[f]
                if f in JSON_FIELDS:
                    v = json.dumps(list(v), ensure_ascii=False)
                values[f] = v
        values["fetched_at"] = int(time.time())
        current = self.get_meta(key) or {}
        merged = dict(current)
        merged.update(data)
        blob_parts = [merged.get("search_title", ""), merged.get("title", ""), merged.get("orig_title", "")]
        for f in ("directors", "cast", "genres", "countries"):
            blob_parts.extend(merged.get(f) or [])
        values["search_blob"] = normalize(" ".join(str(p) for p in blob_parts if p))
        sets = ", ".join("%s=?" % k for k in values)
        self.conn.execute("UPDATE meta SET %s WHERE key=?" % sets, list(values.values()) + [key])
        self.conn.commit()

    def set_meta_status(self, key, status):
        self.conn.execute("UPDATE meta SET status=?, fetched_at=? WHERE key=?",
                          (status, int(time.time()), key))
        self.conn.commit()

    def reset_meta(self, key):
        self.conn.execute(
            "UPDATE meta SET status='new', source='', csfd_id=0, csfd_url='', tmdb_id=0, rating=0, "
            "rating_source='', votes=0, genres='[]', countries='[]', directors='[]', cast='[]', "
            "plot='', poster='', fanart='', runtime=0, orig_title='', title=search_title, "
            "year=search_year WHERE key=?", (key,))
        self.conn.commit()

    def meta_to_fetch(self, statuses=("new",), retry_after=0):
        q = "SELECT * FROM meta WHERE status IN (%s)" % ",".join("?" for _ in statuses)
        params = list(statuses)
        if retry_after:
            q += " AND fetched_at < ?"
            params.append(int(time.time()) - retry_after)
        q += " ORDER BY key"
        return [_row(r) for r in self.conn.execute(q, params).fetchall()]

    def all_meta_keys(self):
        return [r[0] for r in self.conn.execute("SELECT key FROM meta ORDER BY key").fetchall()]

    # --- výpisy ------------------------------------------------------------
    _MOVIE_SELECT = (
        "SELECT m.*, COUNT(v.id) AS video_count, MAX(v.added_at) AS added_at, "
        "MIN(v.id) AS first_video, GROUP_CONCAT(DISTINCT v.lang) AS langs, "
        "GROUP_CONCAT(DISTINCT v.quality) AS qualities, MAX(v.duration) AS duration, "
        "MIN(v.thumb) AS thumb "
        "FROM meta m JOIN videos v ON v.meta_key = m.key AND v.hidden = 0 ")

    def movies(self, genre=None, year_from=None, year_to=None, min_rating=None, country=None,
               order="title", limit=None, keys=None):
        where = ["m.kind = 'movie'"]
        params = []
        if genre:
            where.append("m.genres LIKE ?")
            params.append('%%"%s"%%' % genre)
        if country:
            where.append("m.countries LIKE ?")
            params.append('%%"%s"%%' % country)
        if year_from:
            where.append("COALESCE(NULLIF(m.year,0), m.search_year) >= ?")
            params.append(year_from)
        if year_to:
            where.append("COALESCE(NULLIF(m.year,0), m.search_year) <= ?")
            params.append(year_to)
        if min_rating:
            where.append("m.rating >= ?")
            params.append(min_rating)
        if keys is not None:
            if not keys:
                return []
            where.append("m.key IN (%s)" % ",".join("?" for _ in keys))
            params.extend(keys)
        q = self._MOVIE_SELECT + "WHERE " + " AND ".join(where) + " GROUP BY m.key "
        q += self._order(order)
        if limit:
            q += " LIMIT %d" % int(limit)
        return [_row(r) for r in self.conn.execute(q, params).fetchall()]

    def shows(self, genre=None, min_rating=None, order="title", limit=None, keys=None):
        where = ["m.kind = 'show'"]
        params = []
        if genre:
            where.append("m.genres LIKE ?")
            params.append('%%"%s"%%' % genre)
        if min_rating:
            where.append("m.rating >= ?")
            params.append(min_rating)
        if keys is not None:
            if not keys:
                return []
            where.append("m.key IN (%s)" % ",".join("?" for _ in keys))
            params.extend(keys)
        q = (self._MOVIE_SELECT.replace("MIN(v.id) AS first_video",
                                        "COUNT(DISTINCT v.season) AS season_count, MIN(v.id) AS first_video")
             + "WHERE " + " AND ".join(where) + " GROUP BY m.key ")
        q += self._order(order)
        if limit:
            q += " LIMIT %d" % int(limit)
        return [_row(r) for r in self.conn.execute(q, params).fetchall()]

    @staticmethod
    def _order(order):
        return {
            "title": "ORDER BY COALESCE(NULLIF(m.title,''), m.search_title) COLLATE NOCASE",
            "rating": "ORDER BY m.rating DESC, m.votes DESC",
            "year": "ORDER BY COALESCE(NULLIF(m.year,0), m.search_year) DESC",
            "added": "ORDER BY MAX(v.added_at) DESC",
        }.get(order, "ORDER BY m.title COLLATE NOCASE")

    def videos_for_meta(self, key, season=None):
        q = "SELECT * FROM videos WHERE meta_key=? AND hidden=0"
        params = [key]
        if season is not None:
            q += " AND season=?"
            params.append(season)
        q += " ORDER BY season, episode, raw_title COLLATE NOCASE"
        return [_row(r) for r in self.conn.execute(q, params).fetchall()]

    def seasons(self, key):
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT season FROM videos WHERE meta_key=? AND hidden=0 ORDER BY season",
            (key,)).fetchall()]

    def recent_videos(self, limit=50):
        return [_row(r) for r in self.conn.execute(
            "SELECT * FROM videos WHERE hidden=0 ORDER BY added_at DESC, raw_title LIMIT ?",
            (limit,)).fetchall()]

    def all_videos(self):
        return [_row(r) for r in self.conn.execute(
            "SELECT * FROM videos WHERE hidden=0 ORDER BY raw_title COLLATE NOCASE").fetchall()]

    def hidden_videos(self):
        return [_row(r) for r in self.conn.execute(
            "SELECT * FROM videos WHERE hidden=1 ORDER BY raw_title COLLATE NOCASE").fetchall()]

    def unmatched(self):
        """Tituly, ke kterým se nepodařilo najít metadata."""
        return [_row(r) for r in self.conn.execute(
            "SELECT m.*, COUNT(v.id) AS video_count, MIN(v.id) AS first_video, MIN(v.thumb) AS thumb "
            "FROM meta m JOIN videos v ON v.meta_key=m.key AND v.hidden=0 "
            "WHERE m.status IN ('new', 'notfound', 'error', 'blocked') GROUP BY m.key "
            "ORDER BY m.search_title COLLATE NOCASE").fetchall()]

    def genres(self, kind="movie"):
        counts = {}
        for r in self.conn.execute(
                "SELECT m.genres FROM meta m WHERE m.kind=? AND EXISTS "
                "(SELECT 1 FROM videos v WHERE v.meta_key=m.key AND v.hidden=0)", (kind,)):
            for g in json.loads(r[0] or "[]"):
                counts[g] = counts.get(g, 0) + 1
        return sorted(counts.items(), key=lambda kv: kv[0].lower())

    def countries(self, kind="movie"):
        counts = {}
        for r in self.conn.execute(
                "SELECT m.countries FROM meta m WHERE m.kind=? AND EXISTS "
                "(SELECT 1 FROM videos v WHERE v.meta_key=m.key AND v.hidden=0)", (kind,)):
            for g in json.loads(r[0] or "[]"):
                counts[g] = counts.get(g, 0) + 1
        return sorted(counts.items(), key=lambda kv: kv[0].lower())

    def decades(self, kind="movie"):
        rows = self.conn.execute(
            "SELECT (COALESCE(NULLIF(m.year,0), m.search_year)/10)*10 AS d, COUNT(*) FROM meta m "
            "WHERE m.kind=? AND COALESCE(NULLIF(m.year,0), m.search_year) > 0 AND EXISTS "
            "(SELECT 1 FROM videos v WHERE v.meta_key=m.key AND v.hidden=0) GROUP BY d ORDER BY d DESC",
            (kind,)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def search(self, query):
        """Hledá v názvech (i původních), režisérech, hercích, žánrech a názvech souborů."""
        words = normalize(query).split()
        if not words:
            return [], []
        where = []
        params = []
        for w in words:
            where.append("(m.search_blob LIKE ? OR EXISTS (SELECT 1 FROM videos v2 WHERE "
                         "v2.meta_key=m.key AND v2.raw_title LIKE ?))")
            params.extend(["%" + w + "%", "%" + w + "%"])
        keys = [r[0] for r in self.conn.execute(
            "SELECT m.key FROM meta m WHERE " + " AND ".join(where), params).fetchall()]
        # raw_title LIKE nezná diakritiku – doplň shody přes normalizované názvy souborů
        for r in self.conn.execute("SELECT DISTINCT meta_key, raw_title FROM videos"):
            if r[0] not in keys and all(w in normalize(r[1]) for w in words):
                keys.append(r[0])
        return self.movies(keys=keys), self.shows(keys=keys)

    # --- kolekce ------------------------------------------------------------
    def collections(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT c.*, COUNT(ci.target) AS item_count FROM collections c "
            "LEFT JOIN collection_items ci ON ci.collection_id=c.id GROUP BY c.id "
            "ORDER BY c.name COLLATE NOCASE").fetchall()]

    def create_collection(self, name, source="user"):
        name = (name or "").strip()
        if not name:
            raise ValueError("prázdný název kolekce")
        row = self.conn.execute("SELECT id FROM collections WHERE name=?", (name,)).fetchone()
        if row:
            return row[0]
        cur = self.conn.execute("INSERT INTO collections(name, created, source) VALUES (?, ?, ?)",
                                (name, int(time.time()), source))
        self.conn.commit()
        return cur.lastrowid

    def rename_collection(self, cid, name):
        self.conn.execute("UPDATE collections SET name=? WHERE id=?", (name.strip(), cid))
        self.conn.commit()

    def delete_collection(self, cid):
        self.conn.execute("DELETE FROM collection_items WHERE collection_id=?", (cid,))
        self.conn.execute("DELETE FROM collections WHERE id=?", (cid,))
        self.conn.commit()

    def add_to_collection(self, cid, target):
        self.conn.execute("INSERT OR IGNORE INTO collection_items(collection_id, target, added) "
                          "VALUES (?, ?, ?)", (cid, target, int(time.time())))
        self.conn.commit()

    def remove_from_collection(self, cid, target):
        self.conn.execute("DELETE FROM collection_items WHERE collection_id=? AND target=?",
                          (cid, target))
        self.conn.commit()

    def collection_targets(self, cid):
        return [r[0] for r in self.conn.execute(
            "SELECT target FROM collection_items WHERE collection_id=? ORDER BY added", (cid,)).fetchall()]

    def collections_containing(self, target):
        return [r[0] for r in self.conn.execute(
            "SELECT collection_id FROM collection_items WHERE target=?", (target,)).fetchall()]

    # --- historie hledání ----------------------------------------------------
    def add_search(self, query, keep=20):
        self.conn.execute("INSERT OR REPLACE INTO search_history(query, ts) VALUES (?, ?)",
                          (query, int(time.time())))
        self.conn.execute("DELETE FROM search_history WHERE query NOT IN "
                          "(SELECT query FROM search_history ORDER BY ts DESC LIMIT ?)", (keep,))
        self.conn.commit()

    def search_history(self):
        return [r[0] for r in self.conn.execute(
            "SELECT query FROM search_history ORDER BY ts DESC").fetchall()]

    def delete_search(self, query):
        self.conn.execute("DELETE FROM search_history WHERE query=?", (query,))
        self.conn.commit()

    def stats(self):
        c = self.conn
        return {
            "videos": c.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
            "movies": c.execute("SELECT COUNT(*) FROM meta WHERE kind='movie'").fetchone()[0],
            "shows": c.execute("SELECT COUNT(*) FROM meta WHERE kind='show'").fetchone()[0],
            "with_rating": c.execute("SELECT COUNT(*) FROM meta WHERE rating>0").fetchone()[0],
            "unmatched": c.execute("SELECT COUNT(*) FROM meta WHERE status IN "
                                   "('new','notfound','error','blocked')").fetchone()[0],
        }
