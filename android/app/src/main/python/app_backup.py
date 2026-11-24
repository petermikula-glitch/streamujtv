# -*- coding: utf-8 -*-
"""Záloha dat aplikace do jednoho souboru na disku a obnova z něj.

V záloze je celý katalog (videa, metadata, historie přehrávání, zhlédnuto, seznam dostupných titulů)
a soubory s nastavením, oblíbenými a profily. Heslo k Přehraj.to v ní není: soubor leží na disku,
který si může kdokoli odnést. Po obnově na stejném zařízení zůstane přihlášení, jaké bylo; po
přeinstalaci se heslo zadá znovu v Nastavení. Seznam stažených souborů se nezálohuje – soubory samy
v záloze nejsou a po přeinstalaci na disku nezůstanou.

Kam se zálohuje, vybírá aplikace (zná připojené disky a ví, jestli smí zapisovat mimo svou složku);
tady je, co do zálohy patří a jak se obnovuje.
"""

import json
import os
import re
import sqlite3
import time
import zipfile

import db

import app_api as api

PREFIX = "promitarna-zaloha-"
MANIFEST = "backup.json"
CATALOG = "catalog.db"
# soubory s daty, které do zálohy patří (nastavení, oblíbené, profily a jejich uložený stav)
FILES = re.compile(r"^(settings|favorites|profiles|profile_[A-Za-z0-9]+)\.json$")
SECRET = ("password",)               # co se ze zálohy nastavení vynechává
LIMIT = 512 << 20                    # větší katalog v záloze není: taková záloha se neobnoví


def _when(stamp):
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(stamp))


def backup_export(params):
    """Zapíše zálohu do složky ``dir``. Vrací ``{"message", "path"}``."""
    target = str(params.get("dir") or "")
    if not target:
        raise api.ApiError("Není vybraný disk pro zálohu.")
    try:
        os.makedirs(target, exist_ok=True)
    except OSError:
        raise api.ApiError("Na vybraný disk se nedá zapisovat.")
    path = os.path.join(target, PREFIX + time.strftime("%Y%m%d-%H%M%S") + ".zip")
    copy = api._path("backup-export.tmp")
    try:
        # věrná kopie katalogu pořízená databází samotnou: platí i tehdy, když se do něj zrovna zapisuje
        with api._library() as lib:
            videos = lib.count_videos(visible_only=False)
            destination = sqlite3.connect(copy)
            try:
                lib.conn.backup(destination)
            finally:
                destination.close()
        manifest = {"app": "promitarna", "created": int(time.time()), "schema": db.SCHEMA_VERSION,
                    "version": str(params.get("version") or ""), "videos": videos}
        with zipfile.ZipFile(path + ".tmp", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest))
            archive.write(copy, CATALOG)
            for name in sorted(os.listdir(api._path(""))):
                if not FILES.match(name):
                    continue
                try:
                    with open(api._path(name), encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                if name == "settings.json" and isinstance(data, dict):
                    data = {key: value for key, value in data.items() if key not in SECRET}
                archive.writestr(name, json.dumps(data, ensure_ascii=False))
        os.replace(path + ".tmp", path)
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        _remove(path + ".tmp")
        api._remember_error("záloha", error)
        raise api.ApiError("Zálohu se nepodařilo zapsat – na disku možná není místo nebo se odpojil.")
    finally:
        _remove(copy)
    return {"message": "Záloha je uložená: %s (%s).\n\nHeslo k Přehraj.to v ní není." % (
        os.path.basename(path), _size(os.path.getsize(path))), "path": path}


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _size(count):
    return "%.1f MB" % (count / float(1 << 20)) if count >= 1 << 20 else "%d kB" % max(1, count >> 10)


def _manifest(path):
    """Údaje o záloze, nebo ``None``, když soubor zálohou této aplikace není."""
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
            archive.getinfo(CATALOG)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    return manifest if isinstance(manifest, dict) and manifest.get("app") == "promitarna" else None


def backup_list(params):
    """Zálohy nalezené ve složkách ``dirs`` (``[{"dir", "label"}]``), nejnovější první:
    ``{"backups": [{"path", "title"}]}``."""
    found = []
    for place in params.get("dirs") or []:
        folder = str((place or {}).get("dir") or "")
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not (name.startswith(PREFIX) and name.endswith(".zip")):
                continue
            path = os.path.join(folder, name)
            manifest = _manifest(path)
            if manifest is None:
                continue
            created = int(manifest.get("created") or 0)
            version = " · verze %s" % manifest["version"] if manifest.get("version") else ""
            found.append((created, {"path": path, "title": "%s%s · %d videí · %s" % (
                _when(created), version, int(manifest.get("videos") or 0), (place or {}).get("label") or "disk")}))
    found.sort(key=lambda item: -item[0])
    return {"backups": [entry for _created, entry in found]}


def backup_import(params):
    """Obnoví data ze zálohy ``path``: katalog nahradí celý, soubory s nastavením, oblíbenými a profily
    přepíše. Přihlášení k Přehraj.to, které v zařízení je, zůstává. Vrací ``{"message", "restart"}``."""
    path = str(params.get("path") or "")
    manifest = _manifest(path)
    if manifest is None:
        raise api.ApiError("Soubor není záloha této aplikace, nebo je poškozený.")
    if int(manifest.get("schema") or 0) > db.SCHEMA_VERSION:
        raise api.ApiError("Záloha je z novější verze aplikace. Nejdřív aplikaci aktualizujte.")
    copy = api._path("backup-import.tmp")

    def work():
        import app_index
        app_index._CRAWL["stop"] = True          # plnění seznamu ať do obnovovaného katalogu nezapisuje
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.getinfo(CATALOG).file_size > LIMIT:
                    raise api.ApiError("Záloha je nečekaně velká a neobnoví se.")
                with archive.open(CATALOG) as source, open(copy, "wb") as target:
                    while True:
                        block = source.read(1 << 20)
                        if not block:
                            break
                        target.write(block)
                files = {}
                for name in archive.namelist():
                    if FILES.match(name):          # jen známé soubory, žádné cesty
                        data = json.loads(archive.read(name).decode("utf-8"))
                        if isinstance(data, dict):
                            files[name] = data
            source = sqlite3.connect(copy)
            try:
                if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise api.ApiError("Katalog v záloze je poškozený; nic se nezměnilo.")
                # obnova databází samotnou: ostatní vlákna aplikace uvidí nový obsah bez výměny souboru
                with api._library() as lib:
                    source.backup(lib.conn)
            finally:
                source.close()
            current = api._settings()
            for name, data in files.items():
                if name == "settings.json":
                    if current.get("password"):    # přihlášení v zařízení zůstává
                        data = dict(data, username=current.get("username", ""), password=current["password"])
                    api._save_settings(data)
                    continue
                temporary = api._path(name + ".tmp")
                with open(temporary, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(temporary, api._path(name))
            with api._library():                   # záloha ze starší verze: katalog se převede hned
                pass
        except (OSError, ValueError, KeyError, sqlite3.Error, zipfile.BadZipFile) as error:
            api._remember_error("obnova zálohy", error)
            raise api.ApiError("Zálohu se nepodařilo obnovit: soubor nejde přečíst.")
        finally:
            _remove(copy)
        api._STATE["quick_sync"] = 0
        return {"message": "Data ze zálohy z %s jsou obnovená. Aplikace se teď načte znovu." % _when(
            int(manifest.get("created") or 0)), "restart": True}

    return api._exclusive(work)


ACTIONS = {"backup_export": backup_export, "backup_list": backup_list, "backup_import": backup_import}
