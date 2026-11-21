# -*- coding: utf-8 -*-
"""Lokální katalog (SQLite) – videa z účtu, metadata z ČSFD/TMDB, kolekce."""
import json
import sqlite3
import time

from nameparser import movie_key, normalize, parse, show_key

SCHEMA_VERSION = 6
BACKUP_VERSION = 1
OPTIONAL_BACKUP_TABLES = ("watchlist", "watched_marks", "progress_hidden", "saved_movie_filters",
                          "episode_additions", "episode_visits")
BACKUP_TABLES = ("videos", "meta", "collections", "collection_items", "history", "search_history",
                 *OPTIONAL_BACKUP_TABLES)

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
CREATE TABLE IF NOT EXISTS history (
    vid TEXT PRIMARY KEY,          -- ID videa
    last_played INTEGER NOT NULL,
    position REAL DEFAULT 0,       -- s, kde se skončilo
    total REAL DEFAULT 0,          -- s, délka videa
    plays INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_history_played ON history(last_played);
CREATE TABLE IF NOT EXISTS watched_marks (
    vid TEXT PRIMARY KEY,
    marked_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS progress_hidden (
    target TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS watchlist (
    kind TEXT NOT NULL CHECK(kind IN ('movie','tv')),
    tmdb_id INTEGER NOT NULL CHECK(tmdb_id > 0),
    title TEXT NOT NULL,
    year INTEGER NOT NULL,
    poster TEXT NOT NULL,
    added INTEGER NOT NULL,
    PRIMARY KEY(kind, tmdb_id)
);
CREATE TABLE IF NOT EXISTS watchlist_availability (
    kind TEXT NOT NULL,
    tmdb_id INTEGER NOT NULL,
    ready INTEGER NOT NULL,
    fresh INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, tmdb_id)
);
CREATE TABLE IF NOT EXISTS saved_movie_filters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    language TEXT NOT NULL,
    quality TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episode_additions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meta_key TEXT NOT NULL,
    season INTEGER NOT NULL,
    episode INTEGER NOT NULL,
    UNIQUE(meta_key, season, episode)
);
CREATE TABLE IF NOT EXISTS episode_visits (
    meta_key TEXT PRIMARY KEY,
    previous INTEGER NOT NULL,
    seen INTEGER NOT NULL
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


# rozpracované = přehráno aspoň minutu a méně než tento podíl délky
IN_PROGRESS_MIN = 60
IN_PROGRESS_MAX = 0.92


class Library(object):
    def __init__(self, path, video_key=None, prefer_ref=None):
        """``video_key(id)`` → klíč, podle kterého se poznají dvě ID téhož videa
        (Přehraj.to: hash); ``prefer_ref(nové, staré)`` → které ID si nechat."""
        self.path = path
        self.video_key = video_key or (lambda vid: vid)
        self.prefer_ref = prefer_ref or (lambda new, old: old)
        self.conn = sqlite3.connect(path, timeout=20)
        self.conn.row_factory = sqlite3.Row
        try:
            if self.conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                self.conn.execute("PRAGMA journal_mode=WAL")
                with self.conn:
                    self.conn.execute("BEGIN IMMEDIATE")
                    if self.conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                        for statement in SCHEMA.split(";"):
                            if statement.strip():
                                self.conn.execute(statement)
                        self._migrate()
                        self.conn.execute("INSERT OR REPLACE INTO settings_kv(k, v) VALUES ('schema', ?)",
                                          (str(SCHEMA_VERSION),))
                        self.conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
        except sqlite3.Error:
            self.conn.close()
            raise

    def _migrate(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(videos)")]
        if "vkey" not in cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN vkey TEXT DEFAULT ''")
        if "size" not in cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN size TEXT DEFAULT ''")
        if "search_name" not in cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN search_name TEXT NOT NULL DEFAULT ''")
            self.conn.executemany("UPDATE videos SET search_name=? WHERE id=?",
                                  ((normalize(title), vid) for vid, title in self.conn.execute(
                                      "SELECT id, raw_title FROM videos").fetchall()))
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_vkey ON videos(vkey)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_season ON videos(meta_key, hidden, season, episode)")
        for (vid,) in self.conn.execute("SELECT id FROM videos WHERE vkey IS NULL OR vkey=''").fetchall():
            self.conn.execute("UPDATE videos SET vkey=? WHERE id=?", (self.video_key(vid), vid))
        # duplicity z dřívějška (stejné video pod dvěma ID) – nech jedno
        dupes = self.conn.execute(
            "SELECT vkey FROM videos WHERE vkey != '' GROUP BY vkey HAVING COUNT(*) > 1").fetchall()
        for (vkey,) in dupes:
            ids = [r[0] for r in self.conn.execute(
                "SELECT id FROM videos WHERE vkey=? ORDER BY added_at", (vkey,)).fetchall()]
            keep = ids[0]
            for other in ids[1:]:
                keep = self.prefer_ref(other, keep)
            for other in ids:
                if other != keep:
                    self._merge_into(other, keep)

    def _merge_into(self, old, new):
        """Smaže řádek ``old`` a jeho vazby převede na ``new``."""
        self.conn.execute("UPDATE OR IGNORE collection_items SET target=? WHERE target=?",
                          ("v:" + new, "v:" + old))
        self.conn.execute("DELETE FROM collection_items WHERE target=?", ("v:" + old,))
        self.conn.execute("UPDATE OR IGNORE history SET vid=? WHERE vid=?", (new, old))
        self.conn.execute("DELETE FROM history WHERE vid=?", (old,))
        self.conn.execute("UPDATE OR IGNORE watched_marks SET vid=? WHERE vid=?", (new, old))
        self.conn.execute("DELETE FROM watched_marks WHERE vid=?", (old,))
        self.conn.execute("DELETE FROM videos WHERE id=?", (old,))

    def _rename_video(self, old, new):
        self.conn.execute("UPDATE videos SET id=? WHERE id=?", (new, old))
        self.conn.execute("UPDATE OR IGNORE collection_items SET target=? WHERE target=?",
                          ("v:" + new, "v:" + old))
        self.conn.execute("UPDATE OR IGNORE history SET vid=? WHERE vid=?", (new, old))
        self.conn.execute("UPDATE OR IGNORE watched_marks SET vid=? WHERE vid=?", (new, old))
        self.conn.execute("DELETE FROM watched_marks WHERE vid=?", (old,))

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def export_backup(self, addon_id):
        """Konzistentní snímek katalogu, bez nastavení a mezipaměti odkazů."""
        with self.conn:
            self.conn.execute("BEGIN")
            tables = {
                table: [dict(row) for row in self.conn.execute("SELECT * FROM " + table)]
                for table in BACKUP_TABLES
            }
        return {"version": BACKUP_VERSION, "addon": addon_id, "tables": tables}

    def restore_backup(self, backup, addon_id):
        if (not isinstance(backup, dict) or backup.get("version") != BACKUP_VERSION
                or backup.get("addon") != addon_id):
            raise ValueError("Unsupported backup format")
        tables = backup.get("tables")
        expected = set(BACKUP_TABLES)
        required = expected - set(OPTIONAL_BACKUP_TABLES)
        if not isinstance(tables, dict) or not required <= set(tables) <= expected:
            raise ValueError("Invalid backup tables")
        tables = {**dict.fromkeys(OPTIONAL_BACKUP_TABLES, []), **tables}
        # Uživatel může zálohu upravit. Názvy sloupců bereme výhradně ze schématu.
        inserts = {}
        for table in BACKUP_TABLES:
            columns = [row[1] for row in self.conn.execute("PRAGMA table_info(%s)" % table)]
            rows = tables[table]
            if table == "videos" and isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
                rows = [dict(row, search_name=normalize(row["raw_title"])) if isinstance(row.get("raw_title"), str)
                        else row for row in rows]
            if not isinstance(rows, list) or any(
                    not isinstance(row, dict) or set(row) != set(columns)
                    or any(value is not None and type(value) not in (str, int, float)
                           for value in row.values()) for row in rows):
                raise ValueError("Invalid backup rows")
            inserts[table] = (columns, rows)
        with self.conn:
            for table in reversed(BACKUP_TABLES):
                self.conn.execute("DELETE FROM " + table)
            for table, (columns, rows) in inserts.items():
                sql = "INSERT INTO %s (%s) VALUES (%s)" % (
                    table, ", ".join(columns), ", ".join("?" for _ in columns))
                self.conn.executemany(sql, ([row[column] for column in columns] for row in rows))
            self.conn.execute("DELETE FROM watchlist_availability")
            self.conn.execute("DELETE FROM settings_kv WHERE k LIKE 'link:%' OR k LIKE 'tmdb:season:%' "
                              "OR k IN ('last_sync', 'account_snapshot', 'sync_retry_after')")

    # --- key/value ---------------------------------------------------------
    def get_kv(self, k, default=None):
        row = self.conn.execute("SELECT v FROM settings_kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else default

    def set_kv(self, k, v):
        self.conn.execute("INSERT OR REPLACE INTO settings_kv(k, v) VALUES (?, ?)", (k, str(v)))
        self.conn.commit()

    def delete_kv(self, k, expected):
        with self.conn:
            self.conn.execute("DELETE FROM settings_kv WHERE k=? AND v=?", (k, expected))

    # --- videa -------------------------------------------------------------
    def get_video(self, vid):
        return _row(self.conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone())

    def find_video(self, vid):
        """Video podle ID, nebo podle klíče (stejné video pod jiným tvarem ID)."""
        v = self.get_video(vid)
        if v is None:
            v = _row(self.conn.execute("SELECT * FROM videos WHERE vkey=? LIMIT 1",
                                       (self.video_key(vid),)).fetchone())
        return v

    def upsert_video(self, vid, raw_title, source, parsed, duration=0, thumb="", description="",
                     now=None, size="", quality="", track_episode=False):
        """Vloží nebo aktualizuje video. Vrací True, pokud je nové."""
        now = now or int(time.time())
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            return self._upsert_video(vid, raw_title, source, parsed, duration, thumb,
                                      description, now, size, quality, track_episode)

    def _upsert_video(self, vid, raw_title, source, parsed, duration, thumb,
                      description, now, size, quality, track_episode):
        existing = self.find_video(vid)
        if existing and existing["id"] != vid:
            # stejné video pod jiným ID (např. ruční odkaz bez čísla videa) – sluč
            keep = self.prefer_ref(vid, existing["id"])
            if keep == vid:
                self._rename_video(existing["id"], vid)
                existing["id"] = vid
            vid = existing["id"]
        if existing:
            updates = {"last_seen": now} if existing["last_seen"] < now else {}
            if size and not existing.get("size"):
                updates["size"] = size
            if quality and not existing["quality"]:
                updates["quality"] = quality
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
                updates.update(self._parsed_fields(raw_title, parsed or parse(raw_title)))
                if not updates["quality"]:
                    updates["quality"] = quality or existing["quality"]
            if updates:
                sets = ", ".join("%s=?" % k for k in updates)
                self.conn.execute("UPDATE videos SET %s WHERE id=?" % sets, list(updates.values()) + [vid])
                if "meta_key" in updates:
                    self._ensure_meta_for_video(vid)
            return False
        fields = self._parsed_fields(raw_title, parsed or parse(raw_title))
        fields.update({"id": vid, "source": source, "added_at": now, "last_seen": now,
                       "duration": duration or 0, "thumb": thumb or "", "description": description or "",
                       "vkey": self.video_key(vid), "size": size or ""})
        if quality and not fields["quality"]:
            fields["quality"] = quality
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        self.conn.execute("INSERT INTO videos (%s) VALUES (%s)" % (cols, marks), list(fields.values()))
        self._ensure_meta_for_video(vid)
        if track_episode and fields["kind"] == "episode" and fields["episode"] > 0:
            self.conn.execute(
                "INSERT OR IGNORE INTO episode_additions(meta_key, season, episode) "
                "SELECT meta_key, season, episode FROM videos AS added WHERE id=? AND NOT EXISTS "
                "(SELECT 1 FROM videos AS v WHERE v.meta_key=added.meta_key AND v.season=added.season "
                "AND v.episode=added.episode AND v.id<>added.id)",
                (vid,))
        return True

    def apply_account_snapshot(self, videos, started, complete, prune):
        """Zapíše již stažený výpis v jedné transakci, včetně případného odebrání."""
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            if started <= int(self.get_kv("account_snapshot", 0)):
                return None
            now = started // 1000000000
            added = sum(self._upsert_video(item["id"], item["title"] or item["id"], "account", None,
                                          item.get("duration", 0), item.get("thumb", ""), "",
                                          now, item.get("size", ""), item.get("quality", ""), True)
                        for item in videos)
            removed = 0
            if complete and prune and videos:
                self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS account_snapshot_keys (vkey TEXT PRIMARY KEY)")
                self.conn.execute("DELETE FROM account_snapshot_keys")
                self.conn.executemany("INSERT OR IGNORE INTO account_snapshot_keys VALUES (?)",
                                      ((self.video_key(item["id"]),) for item in videos))
                missing = "SELECT id FROM videos WHERE source='account' AND last_seen<=? AND NOT EXISTS (" \
                          "SELECT 1 FROM account_snapshot_keys k WHERE k.vkey=videos.vkey)"
                self.conn.execute("DELETE FROM collection_items WHERE target IN (SELECT 'v:' || id FROM (" +
                                  missing + "))", (now,))
                removed = self.conn.execute("DELETE FROM videos WHERE id IN (" + missing + ")", (now,)).rowcount
            self._cleanup_meta()
            self.conn.execute("INSERT OR REPLACE INTO settings_kv VALUES ('account_snapshot', ?)", (str(started),))
            if complete:
                self.conn.execute("INSERT OR REPLACE INTO settings_kv VALUES ('last_sync', ?)",
                                  (str(int(time.time())),))
        return added, removed

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
            "search_name": normalize(raw_title),
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

    def _edit_video(self, vid, kind, title, year, season, episode):
        key = meta_key_for(kind, title, year)
        self.conn.execute(
            "UPDATE videos SET kind=?, title=?, year=?, season=?, episode=?, meta_key=?, locked=1 "
            "WHERE id=?", (kind, title, year or 0, season or 0, episode or 0, key, vid))
        self._ensure_meta_for_video(vid)
        return key

    def edit_video(self, vid, kind, title, year=0, season=0, episode=0):
        """Ruční úprava (uzamkne video proti přepsání synchronizací)."""
        with self.conn:
            key = self._edit_video(vid, kind, title, year, season, episode)
            self._cleanup_meta()
        return key

    def edit_episodes(self, assignments, title, season):
        """Přesune potvrzené soubory najednou a zachová vazby podle ID videa."""
        if not assignments or not title.strip() or season < 0 or any(episode <= 0 for _, episode in assignments):
            raise ValueError("Invalid episode assignment")
        snapshot = ("raw_title", "kind", "meta_key", "season", "episode", "locked")
        old_keys = set()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            for original, episode in assignments:
                current = self.find_video(original["id"])
                if not current or any(current[field] != original[field] for field in snapshot):
                    raise ValueError("Catalogue item changed")
                key = self._edit_video(current["id"], "episode", title, 0, season, episode)
                old_keys.add(current["meta_key"])
            for old in old_keys - {key}:
                if self.conn.execute("SELECT 1 FROM videos WHERE meta_key=? LIMIT 1", (old,)).fetchone():
                    continue
                self.conn.execute(
                    "INSERT OR IGNORE INTO collection_items(collection_id, target, added) "
                    "SELECT collection_id, ?, added FROM collection_items WHERE target=?", (key, old))
                self.conn.execute(
                    "INSERT OR IGNORE INTO progress_hidden(target) SELECT ? FROM progress_hidden WHERE target=?",
                    (key, old))
            self._cleanup_meta()
        return key

    def set_hidden(self, vid, hidden=True):
        self.conn.execute("UPDATE videos SET hidden=? WHERE id=?", (1 if hidden else 0, vid))
        self.conn.commit()

    def delete_video(self, vid):
        self.conn.execute("DELETE FROM videos WHERE id=?", (vid,))
        self.conn.execute("DELETE FROM collection_items WHERE target=?", ("v:" + vid,))
        self.conn.execute("DELETE FROM history WHERE vid=?", (vid,))
        self.conn.execute("DELETE FROM watched_marks WHERE vid=?", (vid,))
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
        with self.conn:
            self._cleanup_meta()

    def _cleanup_meta(self):
        self.conn.execute("DELETE FROM meta WHERE key NOT IN (SELECT DISTINCT meta_key FROM videos)")
        self.conn.execute("DELETE FROM collection_items WHERE target NOT LIKE 'v:%' AND "
                          "target NOT IN (SELECT key FROM meta)")
        self.conn.execute("DELETE FROM watched_marks WHERE vid NOT IN (SELECT id FROM videos)")
        self.conn.execute("DELETE FROM progress_hidden WHERE target NOT IN (SELECT key FROM meta)")
        self.conn.execute("DELETE FROM episode_visits WHERE meta_key NOT IN (SELECT key FROM meta)")
        self.conn.execute(
            "DELETE FROM episode_additions WHERE NOT EXISTS (SELECT 1 FROM videos v "
            "WHERE v.meta_key=episode_additions.meta_key AND v.season=episode_additions.season "
            "AND v.episode=episode_additions.episode)")

    def count_videos(self):
        return self.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]

    def clear(self):
        for t in ("videos", "meta", "collection_items", "collections", "history", "watched_marks",
                  "progress_hidden", "episode_visits", "episode_additions"):
            self.conn.execute("DELETE FROM %s" % t)
        self.conn.commit()

    # --- metadata ----------------------------------------------------------
    def get_meta(self, key):
        return _row(self.conn.execute("SELECT * FROM meta WHERE key=?", (key,)).fetchone())

    def update_meta(self, key, data, lock_videos=False):
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
        with self.conn:
            self.conn.execute("UPDATE meta SET %s WHERE key=?" % sets, list(values.values()) + [key])
            if lock_videos:
                self.conn.execute("UPDATE videos SET locked=1 WHERE meta_key=?", (key,))

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
        q += " ORDER BY season, episode, raw_title COLLATE NOCASE, id"
        return [_row(r) for r in self.conn.execute(q, params).fetchall()]

    def videos_grouped(self, keys=None, season=None):
        query = "SELECT * FROM videos WHERE hidden=0"
        params = []
        if season is not None:
            query += " AND season=?"
            params.append(season)
        order = " ORDER BY meta_key, season, episode, raw_title COLLATE NOCASE, id"
        grouped = {}
        if keys is None:
            batches = [()]
        else:
            keys = list(dict.fromkeys(keys))
            batches = [keys[i:i + 500] for i in range(0, len(keys), 500)]
        for batch in batches:
            where = " AND meta_key IN (%s)" % ",".join("?" for _ in batch) if batch else ""
            for row in self.conn.execute(query + where + order, [*params, *batch]):
                grouped.setdefault(row["meta_key"], []).append(_row(row))
        return grouped

    def movie_filter_versions(self):
        rows = self.conn.execute(
            "SELECT * FROM videos WHERE kind='movie' AND hidden=0 ORDER BY raw_title COLLATE NOCASE, id")
        grouped = {}
        for row in rows:
            video = _row(row)
            grouped.setdefault(video["meta_key"], []).append(video)
        return grouped

    def movie_filters(self):
        return [_row(row) for row in self.conn.execute(
            "SELECT * FROM saved_movie_filters ORDER BY name COLLATE NOCASE, id")]

    def get_movie_filter(self, filter_id):
        return _row(self.conn.execute("SELECT * FROM saved_movie_filters WHERE id=?", (filter_id,)).fetchone())

    def save_movie_filter(self, name, language, quality, status):
        with self.conn:
            inserted = self.conn.execute(
                "INSERT OR IGNORE INTO saved_movie_filters(name, language, quality, status) VALUES (?, ?, ?, ?)",
                (name, language, quality, status))
            return inserted.rowcount > 0

    def delete_movie_filter(self, filter_id):
        with self.conn:
            self.conn.execute("DELETE FROM saved_movie_filters WHERE id=?", (filter_id,))

    _VISIBLE_EPISODES = (
        "EXISTS (SELECT 1 FROM videos v WHERE v.meta_key=a.meta_key AND v.season=a.season "
        "AND v.episode=a.episode AND v.hidden=0)")

    def unread_episode_counts(self):
        return dict(self.conn.execute(
            "SELECT a.meta_key, COUNT(*) FROM episode_additions a LEFT JOIN episode_visits s "
            "ON s.meta_key=a.meta_key WHERE a.id>COALESCE(s.seen,0) AND " + self._VISIBLE_EPISODES +
            " GROUP BY a.meta_key"))

    def visit_episodes(self, key):
        """Vrátí rozsah zobrazené dávky a potvrdí právě tento rozsah."""
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            previous, seen = self.conn.execute(
                "SELECT previous, seen FROM episode_visits WHERE meta_key=?", (key,)).fetchone() or (0, 0)
            newest, count = self.conn.execute(
                "SELECT MAX(a.id), COUNT(*) FROM episode_additions a "
                "WHERE a.meta_key=? AND a.id>? AND " + self._VISIBLE_EPISODES, (key, seen)).fetchone()
            if count:
                self.conn.execute("INSERT OR REPLACE INTO episode_visits VALUES (?, ?, ?)", (key, seen, newest))
                return seen, newest, count, True
            count = self.conn.execute(
                "SELECT COUNT(*) FROM episode_additions a WHERE a.meta_key=? AND a.id>? AND a.id<=? AND " +
                self._VISIBLE_EPISODES, (key, previous, seen)).fetchone()[0]
            return previous, seen, count, False

    def new_episode_positions(self, key, after, through):
        return {(row[0], row[1]) for row in self.conn.execute(
            "SELECT season, episode FROM episode_additions WHERE meta_key=? AND id>? AND id<=?",
            (key, after, through))}

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
        conditions = " AND ".join("instr(search_text, ?) > 0" for _ in words)
        keys = [row[0] for row in self.conn.execute(
            "WITH candidates AS (SELECT m.key, m.search_blob || ' ' || COALESCE(names.label, '') AS search_text "
            "FROM meta m LEFT JOIN (SELECT meta_key, GROUP_CONCAT(search_name, ' ') AS label "
            "FROM videos WHERE hidden=0 GROUP BY meta_key) names ON names.meta_key=m.key) "
            "SELECT key FROM candidates WHERE " + conditions, words)]
        # Starší SQLite v Kodi může mít nízký limit vázaných parametrů.
        if len(keys) > 500:
            matches = set(keys)
            return ([m for m in self.movies() if m["key"] in matches],
                    [m for m in self.shows() if m["key"] in matches])
        return self.movies(keys=keys), self.shows(keys=keys)

    def watchlist(self):
        return [_row(row) for row in self.conn.execute(
            "SELECT * FROM watchlist ORDER BY added DESC, title COLLATE NOCASE")]

    def watchlist_has(self, kind, tmdb_id):
        return self.conn.execute("SELECT 1 FROM watchlist WHERE kind=? AND tmdb_id=?",
                                 (kind, tmdb_id)).fetchone() is not None

    def watchlist_add(self, kind, tmdb_id, title, year, poster):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?, ?, ?, ?)",
                              (kind, tmdb_id, title, year, poster, int(time.time())))

    def watchlist_remove(self, kind, tmdb_id):
        with self.conn:
            self.conn.execute("DELETE FROM watchlist WHERE kind=? AND tmdb_id=?", (kind, tmdb_id))
            self.conn.execute("DELETE FROM watchlist_availability WHERE kind=? AND tmdb_id=?", (kind, tmdb_id))

    def fresh_watchlist(self):
        return {(row[0], row[1]) for row in self.conn.execute(
            "SELECT kind, tmdb_id FROM watchlist_availability WHERE ready=1 AND fresh=1")}

    def acknowledge_watchlist(self, kind, tmdb_id):
        with self.conn:
            self.conn.execute("UPDATE watchlist_availability SET fresh=0 WHERE kind=? AND tmdb_id=?",
                              (kind, tmdb_id))

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

    # --- historie přehrávání -------------------------------------------------
    def record_play(self, vid, position=0.0, total=0.0, started=False, now=None):
        """Zapíše přehrávání. ``started`` = nové spuštění (zvýší počet přehrání)."""
        now = now or int(time.time())
        plays = int(bool(started))
        with self.conn:
            updated = self.conn.execute(
                "UPDATE history SET last_played=?, position=?, total=COALESCE(NULLIF(?, 0), total), "
                "plays=plays+? WHERE vid=?", (now, position, total, plays, vid))
            if not updated.rowcount:
                self.conn.execute("INSERT INTO history(vid, last_played, position, total, plays) VALUES (?, ?, ?, ?, ?)",
                                  (vid, now, position, total, plays))

    def playback_history(self, vid):
        row = self.conn.execute("SELECT * FROM history WHERE vid=?", (vid,)).fetchone()
        mark = self.conn.execute("SELECT marked_at FROM watched_marks WHERE vid=?", (vid,)).fetchone()
        state = _row(row) or {"last_played": 0, "position": 0, "total": 0, "plays": 0}
        state["updated"] = max(state["last_played"], mark[0] if mark else 0)
        state["watched"] = bool(mark or (state["total"] and state["position"] >= state["total"] * IN_PROGRESS_MAX))
        return state

    def import_kodi_history(self, vid, position, total, watched, updated):
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            local = self.playback_history(vid)
            if local["updated"] >= updated:
                return False
            self.conn.execute("INSERT OR REPLACE INTO history VALUES (?, ?, ?, ?, ?)",
                              (vid, updated, position, total, local["plays"]))
            if watched:
                self.conn.execute("INSERT OR REPLACE INTO watched_marks VALUES (?, ?)", (vid, updated))
            else:
                self.conn.execute("DELETE FROM watched_marks WHERE vid=?", (vid,))
        return True

    def resume_points(self, vids=None):
        """{vid: (pozice, délka)} jen pro rozkoukaná videa."""
        q = ("SELECT vid, position, total FROM history WHERE position >= ? AND total > 0 "
             "AND position < total * ? AND vid NOT IN (SELECT vid FROM watched_marks)")
        params = [IN_PROGRESS_MIN, IN_PROGRESS_MAX]
        rows = self.conn.execute(q, params).fetchall()
        out = dict((r[0], (r[1], r[2])) for r in rows)
        if vids is not None:
            vids = set(vids)
            out = dict((k, v) for k, v in out.items() if k in vids)
        return out

    def watched(self, vids=None):
        rows = self.conn.execute("SELECT vid FROM history WHERE total > 0 AND position >= total * ? "
                                 "UNION SELECT vid FROM watched_marks", (IN_PROGRESS_MAX,)).fetchall()
        out = set(r[0] for r in rows)
        return out & set(vids) if vids is not None else out

    def set_watched(self, vids, watched):
        with self.conn:
            if watched:
                now = int(time.time())
                self.conn.executemany(
                    "INSERT OR IGNORE INTO watched_marks(vid, marked_at) SELECT id, ? FROM videos WHERE id=?",
                    ((now, vid) for vid in vids))
            else:
                ids = [(vid,) for vid in vids]
                self.conn.executemany("DELETE FROM watched_marks WHERE vid=?", ids)
                self.conn.executemany(
                    "UPDATE history SET position=0 WHERE vid=? AND total>0 AND position>=total*?",
                    ((vid, IN_PROGRESS_MAX) for (vid,) in ids))

    def progress_history(self):
        """Skutečná historie a ruční označení dílů použitelná pro výběr pokračování."""
        return [_row(row) for row in self.conn.execute(
            "SELECT v.*, COALESCE(h.position, 0) AS position, COALESCE(h.total, 0) AS total, "
            "MAX(COALESCE(h.last_played, 0), COALESCE(w.marked_at, 0)) AS last_played "
            "FROM videos v LEFT JOIN history h ON h.vid=v.id LEFT JOIN watched_marks w ON w.vid=v.id "
            "WHERE v.hidden=0 AND (h.vid IS NOT NULL OR (v.kind='episode' AND w.vid IS NOT NULL)) "
            "ORDER BY last_played DESC, v.season DESC, v.episode DESC, h.rowid DESC")]

    def set_progress_hidden(self, key, hidden):
        with self.conn:
            if hidden:
                self.conn.execute("INSERT OR IGNORE INTO progress_hidden SELECT key FROM meta WHERE key=?", (key,))
            else:
                self.conn.execute("DELETE FROM progress_hidden WHERE target=?", (key,))

    def hidden_progress(self):
        return [_row(row) for row in self.conn.execute(
            "SELECT m.* FROM progress_hidden h JOIN meta m ON m.key=h.target "
            "ORDER BY COALESCE(NULLIF(m.title, ''), m.search_title) COLLATE NOCASE")]

    def history_videos(self, in_progress=False, limit=100):
        q = ("SELECT v.*, h.last_played, h.position, h.total, h.plays FROM history h "
             "JOIN videos v ON v.id = h.vid WHERE v.hidden = 0 ")
        params = []
        if in_progress:
            q += ("AND h.position >= ? AND h.total > 0 AND h.position < h.total * ? "
                  "AND h.vid NOT IN (SELECT vid FROM watched_marks) ")
            params += [IN_PROGRESS_MIN, IN_PROGRESS_MAX]
        q += "ORDER BY h.last_played DESC, h.rowid DESC LIMIT ?"
        params.append(limit)
        return [_row(r) for r in self.conn.execute(q, params).fetchall()]

    def has_play_history(self, vid):
        return self.conn.execute("SELECT 1 FROM history WHERE vid=?", (vid,)).fetchone() is not None

    def remove_history(self, vid=None):
        if vid:
            self.conn.execute("DELETE FROM history WHERE vid=?", (vid,))
        else:
            self.conn.execute("DELETE FROM history")
        self.conn.commit()

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
