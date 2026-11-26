# -*- coding: utf-8 -*-
"""Seznam dostupných titulů: aplikace si ho na pozadí plní sama.

Výpisy v Objevuj ukazují jen tituly, o kterých se ví, že jsou na Přehraj.to nebo v iVysílání
(``app_discover``). Kdyby se ověřovalo až při otevření výpisu, čekalo by se u každého. Tento modul
proto seznamy TMDB – populární, novinky, nejlépe hodnocené, československé a jednotlivé žánry, filmy
i seriály – prochází předem, šetrným tempem, a co zjistí, ukládá do stejných záznamů (``avail:…``).
Výpis, který už je projitý, se pak ukáže hned; a protože se prochází znovu každý den, přibudou do něj
samy i tituly, které se na Přehraj.to objevily nově (řada „Nově na Přehraj.to“).

Prochází se jen, dokud aplikace běží, nikdy během přehrávání ani když na výsledek hledání někdo čeká.

Vedle seznamů TMDB se čte i to, co nabízí samo Přehraj.to (``harvest``): videa z jeho úvodní stránky
a z výpisů, na které odkazuje. Tak se do seznamu dostanou i tituly, které v žebříčcích TMDB nejsou.
"""

import json
import threading
import time

import nameparser
import provider
import search_results

import app_api as api
import app_discover as discover
from tmdb import TmdbError

HARVEST_KEY = "index:harvest"    # co naposledy nabídlo samo Přehraj.to (zpráva pro Diagnostiku)
HARVEST_EVERY = 6 * 3600         # nabídka Přehraj.to se čte nejvýš jednou za tuto dobu
HARVEST_TITLES = 40              # tolik titulů z ní se nejvýš zkusí přiřadit a ověřit
STATE_KEY = "index:state"        # kde se v plánu skončilo a kolik se dnes ověřilo
GENERATION = 2                   # zvýšení = po aktualizaci se celý seznam jednou ověří znovu (``_renew``)
JOURNAL_KEY = "index:journal"    # odkdy se seznam vede, kolik měl naposledy záznamů a co ho změnilo
JOURNAL_KEEP = 5                 # tolik posledních událostí se pamatuje
JOURNAL_DROP = 20                # o tolik záznamů (a zároveň o třetinu) musí seznam ubýt, aby to stálo za zápis
CYCLE_EVERY = 20 * 3600          # celý plán se prochází znovu nejdřív po této době
DAILY_LIMIT = 3000               # tolik titulů nejvýš se za den ověří na pozadí
CHUNK = 2                        # tolik titulů najednou…
GAP = 2.0                        # …a pak tolik sekund pauza; dotazy samy drží rozestup ``discover.GENTLE_PACE``
START_DELAY = 20                 # s po otevření aplikace, než se začne procházet
FAILURES_MAX = 3                 # po tolika nepovedených dávkách za sebou se procházení odloží
STUCK_MAX = 3                    # titul, jehož ověření tolikrát selže (služba přitom odpovídá), se v tomto průchodu přeskočí
STUCK_OK_WITHIN = 1800           # „služba odpovídá“ = jiný titul se podařilo ověřit nejvýš před tolika sekundami
FRESH_DAYS = 30                  # jak dlouho je titul „nově na Přehraj.to“
FRESH_LIMIT = 60                 # tolik titulů nejvýš ve výpisu novinek
# (parametry výpisu, kolik stránek) – nejdřív to, co je vidět na hlavní obrazovce a v novinkách
LISTS = (({"category": "popular"}, 2), ({"category": "recent"}, 2), ({"category": "popular"}, 15),
         ({"category": "recent"}, 6), ({"origin": discover.CZECHOSLOVAK}, 10), ({"category": "top_rated"}, 10))
GENRE_PAGES = 3
# Přehraj.to nejde projít celé (nemá výpis všeho, jen hledání), a tak se prochází z druhé strany:
# po letech. Tolik let zpátky a tolik stránek nejoblíbenějších titulů každého roku se ověřuje.
YEARS_BACK = {"movie": 40, "tv": 25}
YEAR_PAGES = {"movie": 3, "tv": 2}
CZECHOSLOVAK_YEARS = 8           # česká a slovenská tvorba posledních let zvlášť (v žebříčcích zapadne)
INDEX_PAGE = 60                  # tolik titulů na jedno načtení výpisu „Vše na Přehraj.to“

_CRAWL = {"thread": None, "stop": False}
_LOCK = threading.Lock()


def _plan():
    """Co všechno se prochází: ``[(druh, parametry výpisu, stránka)]`` v pořadí důležitosti."""
    steps = []
    for params, pages in LISTS:
        for kind in ("movie", "tv"):
            for page in range(1, pages + 1):
                step = (kind, dict(params, kind=kind), page)
                if step not in steps:
                    steps.append(step)
    year = time.localtime().tm_year
    for kind in ("movie", "tv"):
        for back in range(CZECHOSLOVAK_YEARS):
            steps.append((kind, {"kind": kind, "origin": discover.CZECHOSLOVAK, "year": str(year - back)}, 1))
    for kind in ("movie", "tv"):
        try:
            genres = [genre["id"] for genre in discover._tmdb().genres(kind)
                      if isinstance(genre, dict) and genre.get("id")]
        except (TmdbError, api.ApiError):
            genres = []
        for genre in genres:
            steps += [(kind, {"kind": kind, "genre": str(genre)}, page) for page in range(1, GENRE_PAGES + 1)]
    # rok po roce od nejnovějších: nejdřív první stránky všech let, pak další
    for page in range(1, max(YEAR_PAGES.values()) + 1):
        for back in range(max(YEARS_BACK.values())):
            for kind in ("movie", "tv"):
                if page <= YEAR_PAGES[kind] and back < YEARS_BACK[kind]:
                    steps.append((kind, {"kind": kind, "year": str(year - back)}, page))
    return steps


def _state():
    with api._library() as lib:
        try:
            state = json.loads(lib.get_kv(STATE_KEY) or "{}")
        except ValueError:
            state = {}
    return state if isinstance(state, dict) else {}


def _save(state):
    with api._library() as lib:
        lib.set_kv(STATE_KEY, json.dumps(state))


def _today():
    return time.strftime("%Y-%m-%d")


def _wanted(known, kind, item):
    """Titul, o kterém seznam ještě neví, jehož údaj zestárl, nebo o kterém neví z iVysílání."""
    return not discover._fresh(known, "%s:%s" % (kind, item["id"]))


def busy():
    """Má teď přednost něco jiného? Hledání, na které někdo čeká, přehrávání, nebo ověřování výpisu,
    na který se člověk dívá."""
    return discover._VERIFY.get("hold", 0) > time.time() or bool(discover._VERIFY["queue"]) \
        or bool(discover._VERIFY["active"])


def step(limit=None):
    """Projde další stránku plánu: ověří tituly, o kterých seznam neví. Vrací ``{"done": plán je celý
    projitý, "checked": kolik titulů se ověřovalo, "failed": ověřit se nepodařilo}``.

    ``limit`` omezí počet ověřovaných titulů (zbytek stránky se dodělá příště)."""
    state, today = _state(), _today()
    if state.get("day") != today:
        state.update(day=today, count=0)
    plan = _plan()
    position = int(state.get("step") or 0)
    if position >= len(plan):
        if time.time() - float(state.get("finished") or 0) < CYCLE_EVERY:
            return {"done": True, "checked": 0, "failed": False}
        position = 0                       # nový průchod: co je ověřené a platí, se přeskočí
        state["started"] = int(time.time())
        state["stuck"] = {}
    kind, params, page = plan[position]
    try:
        items, _pages = discover._list_items(dict(params, page=page))
    except (TmdbError, api.ApiError):
        return {"done": False, "checked": 0, "failed": True}
    items = [item for item in items if isinstance(item, dict) and item.get("id")]
    with api._library() as lib:
        shared = api._state(lib)
        known = discover._known(lib, kind, [item["id"] for item in items])
    # Titul, jehož ověření pokaždé selže, ačkoli Přehraj.to jinak odpovídá (podivný název, chyba na
    # jeho stránce…), by jinak držel celé procházení na této stránce plánu: po STUCK_MAX pokusech se
    # v tomto průchodu přeskočí a zkusí se zase v příštím.
    stuck = dict(state.get("stuck") or {})
    todo = [item for item in items if _wanted(known, kind, item)
            and int(stuck.get("%s:%s" % (kind, item["id"])) or 0) < STUCK_MAX]
    # tituly, které už selhaly, až nakonec: ostatní na stránce se mezitím ověří
    todo.sort(key=lambda item: int(stuck.get("%s:%s" % (kind, item["id"])) or 0))
    room = max(0, DAILY_LIMIT - int(state.get("count") or 0))
    batch = todo[:min(room, limit) if limit is not None else room]
    checked, failed = 0, False
    for start in range(0, len(batch), CHUNK):
        if _CRAWL["stop"] or (start and busy()):
            batch = batch[:start]
            break
        chunk = batch[start:start + CHUNK]
        fresh = discover._check_available(kind, chunk, shared, gentle=True)
        # počítá se jen to, co se opravdu ověřilo – ne pokusy, které skončily odmítnutím nebo výpadkem
        done = [item for item in chunk if discover._fresh(fresh, "%s:%s" % (kind, item["id"]))]
        checked += len(done)
        if len(done) < len(chunk) and busy() and not discover.search_paused():
            batch = batch[:start]          # ustoupilo se výpisu nebo hledání: stránka se dodělá potom
            break
        missing = ["%s:%s" % (kind, item["id"]) for item in chunk if "%s:%s" % (kind, item["id"]) not in fresh]
        if done:
            state["ok_at"] = int(time.time())
        if missing and not discover.limited() and time.time() - float(state.get("ok_at") or 0) < STUCK_OK_WITHIN:
            # Selhal titul, ne služba: Přehraj.to neomezuje dotazy a jiné tituly se před chvílí ověřit
            # podařilo. Počítá se, kolikrát už (po chybě si služba dá krátkou přestávku a zkusí se znovu).
            for key in missing:
                stuck[key] = int(stuck.get(key) or 0) + 1
        if discover.search_paused() or missing:
            failed = True                  # služba neodpovídá nebo omezuje dotazy: nemá cenu pokračovat
            break
        if start + CHUNK < len(batch):
            time.sleep(GAP)
    state["count"] = int(state.get("count") or 0) + checked
    if not failed and len(batch) == len(todo):
        position += 1                      # stránka je hotová
        if position >= len(plan):
            state["finished"] = int(time.time())
            stuck = {}                     # v příštím průchodu dostanou šanci znovu
    # Stav se mezitím mohl změnit (změna nastavení začíná průchod znovu): zapsat jen to, co se tu změnilo.
    latest = _state()
    if latest.get("restart") != state.get("restart"):
        position = int(latest.get("step") or 0)
    else:
        latest.update({name: state[name] for name in ("finished", "started") if name in state})
    latest.update(day=state["day"], count=state["count"], step=position, stuck=stuck, ok_at=state.get("ok_at") or 0,
                  pace=round(discover.gentle_pace(recover=bool(checked) and not failed), 2))
    state = latest
    _save(state)
    return {"done": position >= len(plan) or int(state["count"]) >= DAILY_LIMIT,
            "checked": checked, "failed": failed}


def _match(tmdb, group):
    """Titul TMDB, který odpovídá názvu souboru – jen při jisté shodě: stejný název (český nebo
    původní) a u filmu i rok (soubor ho musí uvádět, nejvýš o rok jinak). Jinak ``None``: raději
    titul vynechat než přiřadit cizí."""
    series = group["kind"] == "tv"
    if not series and not group["year"]:
        return None
    target = search_results.normalize(group["title"])
    for item in tmdb.candidates(group["title"], group["year"], want_series=series):
        names = {search_results.normalize(item.get(name) or "")
                 for name in ("title", "name", "original_title", "original_name")}
        date = item.get("release_date") or item.get("first_air_date") or ""
        year = int(date[:4]) if date[:4].isdigit() else 0
        if target in names and (series or abs(year - group["year"]) <= 1):
            return item
    return None


def harvest(force=False):
    """Zařadí do seznamu to, co nabízí samo Přehraj.to (úvodní stránka a výpisy, na které odkazuje).

    Názvy souborů se rozeberou (``nameparser``), přiřadí k titulům TMDB (``_match``) a tituly se
    ověří běžnou cestou – tím se dostanou do výpisů a do řady „Nově na Přehraj.to“. Podobu stránek
    neznáme předem: když na nich videa nejsou nebo je nejde přiřadit, nestane se nic a zpráva
    v Diagnostice řekne, co web nabídl. Vrací zprávu, nebo ``None``, když ještě není čas."""
    with api._library() as lib:
        try:
            last = json.loads(lib.get_kv(HARVEST_KEY) or "{}")
        except ValueError:
            last = {}
        shared = api._state(lib)
    now = time.time()
    if not force and now - float((last or {}).get("ts") or 0) < HARVEST_EVERY:
        return None
    report = {"ts": int(now), "files": 0, "titles": 0, "matched": 0, "pages": []}
    try:
        videos, pages = provider.offered(discover._search_client())
        report["pages"] = [[name, count] for name, _url, count in pages]
        report["files"] = len(videos)
        groups = {}
        for video in videos:
            raw = video.get("title") or ""
            parsed = nameparser.parse(raw)
            if not parsed["title"] or api.CAM_RE.search(raw) or search_results.is_preview(raw, (parsed["title"],)):
                continue
            kind = "tv" if parsed["kind"] == "episode" else "movie"
            key = (kind, search_results.normalize(parsed["title"]), parsed["year"] if kind == "movie" else 0)
            groups.setdefault(key, {"kind": kind, "title": parsed["title"], "year": key[2]})
        report["titles"] = len(groups)
        tmdb = discover._tmdb()
        matched = {"movie": [], "tv": []}
        for group in list(groups.values())[:HARVEST_TITLES]:
            item = _match(tmdb, group)
            if item and item["id"] not in [known["id"] for known in matched[group["kind"]]]:
                matched[group["kind"]].append(dict(item, _fresh=True))
        report["matched"] = len(matched["movie"]) + len(matched["tv"])
        for kind, items in matched.items():
            with api._library() as lib:
                known = discover._known(lib, kind, [item["id"] for item in items])
            todo = [item for item in items if _wanted(known, kind, item)]
            for start in range(0, len(todo), CHUNK):
                if _CRAWL["stop"] or busy() or discover.search_paused():
                    break                  # přednost má, co dělá člověk; zbytek se zkusí příště
                discover._check_available(kind, todo[start:start + CHUNK], shared, gentle=True)
                if start + CHUNK < len(todo):
                    time.sleep(GAP)
    except (provider.ProviderError, TmdbError, api.ApiError) as error:
        report["error"] = str(error)[:200]
    with api._library() as lib:
        lib.set_kv(HARVEST_KEY, json.dumps(report))
    return report


def harvest_text():
    """Co naposledy nabídlo samo Přehraj.to – řádek do Diagnostiky."""
    with api._library() as lib:
        try:
            report = json.loads(lib.get_kv(HARVEST_KEY) or "{}")
        except ValueError:
            report = {}
    if not report:
        return "zatím se nečetla"
    when = time.strftime("%d.%m. %H:%M", time.localtime(report.get("ts") or 0))
    if report.get("error"):
        return "%s – nepodařilo se: %s" % (when, report["error"])
    pages = ", ".join("%s (%s)" % (name, count if count >= 0 else "chyba") for name, count in report.get("pages") or [])
    return "%s – souborů %d, titulů %d, přiřazeno %d; stránky: %s" % (
        when, report.get("files") or 0, report.get("titles") or 0, report.get("matched") or 0, pages or "žádné")


def _loop():
    failures = 0
    try:
        time.sleep(START_DELAY)            # start aplikace má přednost: nejdřív ať se načte, co je na obrazovce
        try:
            if not busy() and not discover.search_paused():
                harvest()                  # nejdřív to, co nabízí samo Přehraj.to
        except Exception as error:         # neznámá podoba stránek nesmí zastavit zbytek
            api._remember_error("nabídka Přehraj.to", error)
        while not _CRAWL["stop"]:
            if busy() or discover.search_paused():
                # Přednost má, co dělá člověk; a když Přehraj.to dotazy odmítá, čeká se, až přestávka
                # skončí (při opakování je pokaždé delší) – na pozadí se mu mezitím neposílá nic.
                time.sleep(2)
                continue
            try:
                result = step()
            except Exception as error:     # vlákno na pozadí nesmí shodit aplikaci
                api._remember_error("seznam dostupných titulů", error)
                return
            if result["done"]:
                return
            if result["failed"] and discover.limited():
                continue                   # omezení dotazů není výpadek: po přestávce se pokračuje pomaleji
            failures = failures + 1 if result["failed"] else 0
            if failures >= FAILURES_MAX:
                return                     # služba neodpovídá: zkusí se to při příštím otevření aplikace
            time.sleep(GAP if not result["failed"] else discover.CHECK_PAUSE / 4.0)
    finally:
        with _LOCK:
            _CRAWL["thread"] = None


def restart():
    """Začne plán procházet znovu od začátku (po změně nastavení, která mění, co je „k mání“).
    Co je ověřené a platí, se přeskočí; ověří se jen tituly, kterých se změna týká."""
    state = _state()
    state.update(step=0, restart=int(state.get("restart") or 0) + 1)
    state.pop("finished", None)
    _save(state)
    return start()


def _renew():
    """Jednou po aktualizaci, která mění ověřování (``GENERATION``): celý seznam se ověří znovu.
    Nalezené tituly zůstávají vidět a přeověří se, „nenašlo se“ přestává platit a plán se projde od
    začátku. Nová instalace nemá co obnovovat, jen si zapamatuje, že je aktuální."""
    state = _state()
    if int(state.get("gen") or 0) >= GENERATION:
        return False
    renewed = bool(_entries())
    if renewed:
        discover.reverify_all()
        note("po aktualizaci se celý seznam ověřuje znovu (nalezené tituly zůstávají vidět)", len(_entries()))
    state = _state()
    state.update(gen=GENERATION, step=0, restart=int(state.get("restart") or 0) + 1)
    state.pop("finished", None)
    _save(state)
    return renewed


def _settle_waiting():
    """Jednou (verze 0.29.1): nalezené tituly, které čekaly na nové ověření jen proto, že se změnila
    nejnižší kvalita (dřív i přepnutím profilu), a nové nejnižší kvalitě vyhovují, se znovu ověřovat
    nebudou – byly to stovky dotazů navíc. Ověří se zase až po obvyklé době."""
    state = _state()
    if state.get("settled"):
        return 0
    need = api._need_lines(api._prefs())
    now, changed = int(time.time()), [0]

    def change(entry):
        if entry.get("n") and not entry.get("ts") and not entry.get("x") and int(entry.get("q") or 0) >= need:
            entry["ts"] = now
            changed[0] += 1
            return True
        return False

    discover._rewrite_records(change)
    if changed[0]:
        note("%d nalezených titulů už nečeká na nové ověření (vyhovují nastavení)" % changed[0], len(_entries()))
    state = _state()
    state["settled"] = 1
    _save(state)
    return changed[0]


def keepalive(params):
    """Pro službu, která drží aplikaci naživu, když běží jen na pozadí (spořič obrazovky, noc):
    znovu spustí procházení, pokud se zastavilo (výpadek spojení), a řekne, jestli ještě běží."""
    start()
    return {"active": _CRAWL["thread"] is not None}


def start():
    """Spustí procházení na pozadí, pokud neběží a je co procházet. Volá se po otevření aplikace."""
    if not api._tmdb().enabled or not discover.BACKGROUND:
        return False
    try:
        _renew()
        _settle_waiting()
    except Exception as error:             # obnova seznamu nesmí zastavit otevření aplikace
        api._remember_error("obnova seznamu", error)
    state = _state()
    try:
        watch(len(_entries()))             # po každém otevření aplikace: neubylo ze seznamu?
    except Exception as error:             # deník je jen pro Diagnostiku, nesmí nic zastavit
        api._remember_error("deník seznamu", error)
    if state.get("pace"):
        discover.gentle_pace(max(float(state["pace"]), discover.gentle_pace()))   # tempo naučené minule
    if state.get("day") == _today() and int(state.get("count") or 0) >= DAILY_LIMIT:
        return False
    with _LOCK:
        if _CRAWL["thread"] is not None:
            return False
        _CRAWL["stop"] = False
        thread = threading.Thread(target=_loop, name="seznam-dostupnych")
        thread.daemon = True
        _CRAWL["thread"] = thread
        thread.start()
    return True


def _entries():
    """Všechny záznamy seznamu: ``[(druh, číslo TMDB, záznam)]``."""
    with api._library() as lib:
        rows = lib.conn.execute("SELECT k, v FROM settings_kv WHERE k LIKE ?",
                                (discover.AVAILABLE_PREFIX + "%",)).fetchall()
    found = []
    for key, value in rows:
        parts = key.split(":")
        try:
            entry = json.loads(value or "null")
        except ValueError:
            continue
        if len(parts) == 3 and parts[2].isdigit() and isinstance(entry, dict):
            found.append((parts[1], int(parts[2]), entry))
    return found


def stats():
    """Stav seznamu pro Diagnostiku."""
    entries = _entries()
    state = _state()
    plan_done = int(state.get("step") or 0)
    current = [entry for _kind, _id, entry in entries if entry.get("n") or entry.get("iv")
               or entry.get("r") == discover.AVAILABLE_RULES]
    return {"known": len(current),
            "available": len([1 for entry in current if entry.get("n") or entry.get("iv")]),
            "waiting": len([1 for entry in current if (entry.get("n") or entry.get("iv")) and not entry.get("ts")]),
            "today": int(state.get("count") or 0) if state.get("day") == _today() else 0,
            "finished": int(state.get("finished") or 0), "step": plan_done,
            "running": _CRAWL["thread"] is not None}


def _journal(lib):
    try:
        journal = json.loads(lib.get_kv(JOURNAL_KEY) or "{}")
    except ValueError:
        journal = {}
    return journal if isinstance(journal, dict) else {}


def note(text, count=None):
    """Zapíše do deníku seznamu událost (``text``) a kolik má seznam právě záznamů. Podle deníku jde
    v Diagnostice poznat, proč seznam ubyl nebo začal znovu: změna nastavení, nebo ztráta dat aplikace
    (pak je pryč i deník a seznam je „veden od“ nového dne)."""
    with api._library() as lib:
        journal = _journal(lib)
        journal.setdefault("since", int(time.time()))
        if count is not None:
            journal["count"] = int(count)
        if text:
            events = [[int(time.time()), str(text)[:160]]] + list(journal.get("events") or [])
            journal["events"] = events[:JOURNAL_KEEP]
        lib.set_kv(JOURNAL_KEY, json.dumps(journal))
    return journal


def watch(count):
    """Hlídá velikost seznamu: když záznamů výrazně ubude, aniž to způsobila změna nastavení, zapíše
    to do deníku (nemělo by se to stát – a když ano, je vidět kdy a o kolik). Vrací deník."""
    with api._library() as lib:
        journal = _journal(lib)
    before = int(journal.get("count") or 0)
    if "since" not in journal or count != before:
        dropped = before - count >= JOURNAL_DROP and count * 3 < before * 2
        journal = note("záznamů ubylo z %d na %d" % (before, count) if dropped else "", count)
    return journal


def journal_text():
    """Řádek do Diagnostiky: odkdy se seznam vede a co se s ním naposledy stalo."""
    journal = watch(len(_entries()))
    text = "veden od %s" % time.strftime("%d.%m. %H:%M", time.localtime(journal.get("since") or 0))
    events = ["%s %s" % (time.strftime("%d.%m. %H:%M", time.localtime(when)), what)
              for when, what in journal.get("events") or []]
    return text + ("; " + "; ".join(events) if events else "; beze změn")


def summary_text():
    info = stats()
    when = time.strftime("%d.%m. %H:%M", time.localtime(info["finished"])) if info["finished"] else "ještě ne"
    try:
        total = len(_plan())
    except Exception:   # bez TMDB se plán nesestaví; stav jde ukázat i tak
        total = 0
    wait = discover.limited()
    limit = "; Přehraj.to teď omezuje počet dotazů – pokračuje se asi za %s" % discover._duration_text(wait + 59) \
        if wait else ""
    waiting = ", z toho %d čeká na nové ověření" % info["waiting"] if info["waiting"] else ""
    return ("prověřeno %d titulů: k mání %d%s, nenalezeno %d; dnes prověřeno %d; výpisů projito %d z %d; "
            "celý průchod dokončen: %s%s%s") % (
        info["known"], info["available"], waiting, info["known"] - info["available"], info["today"],
        min(info["step"], total), total, when, " (právě běží)" if info["running"] else "", limit)


def _fresh(kind=None):
    """Tituly, které jsou na Přehraj.to nově (viz ``f`` v záznamu), od nejnovějšího."""
    since = time.time() - FRESH_DAYS * 86400
    found = [(entry["f"], item_kind, tmdb_id, entry) for item_kind, tmdb_id, entry in _entries()
             if entry.get("n") and entry.get("f", 0) >= since and entry.get("t") and (not kind or item_kind == kind)]
    found.sort(key=lambda row: (-row[0], row[2]))
    return found[:FRESH_LIMIT]


FRESH_SORTER = (("added", "Podle data přidání"), ("rating", "Podle hodnocení"), ("new", "Podle roku (nejnovější)"))
INDEX_SORTER = (("best", "Doporučené (jazyk a kvalita)"), ("rating", "Podle hodnocení"),
                ("new", "Podle roku (nejnovější)"))


def _sorted_rows(rows, sort):
    """Řádky ``(…, druh, číslo, záznam)`` podle hodnocení (procenta z karty) nebo roku uvedení;
    jiné řazení nechá pořadí, jak je. Řazení je stabilní: při shodě zůstává původní pořadí."""
    if sort == "rating":
        return sorted(rows, key=lambda row: int(row[-1].get("v") or 0), reverse=True)
    if sort == "new":
        return sorted(rows, key=lambda row: row[-1].get("rd") or "%04d" % int(row[-1].get("y") or 0), reverse=True)
    return rows


def _sort_card(call, params, sorter, current):
    """Dlaždice Seřadit na začátku výpisu (stejná jako v Objevuj): jak je právě seřazeno."""
    base = {key: value for key, value in params.items() if key != "offset"}
    return api._card("Seřadit", {"type": "filter", "call": call, "params": base},
                     dict(sorter).get(current, ""), icon="settings:search")


def fresh_sorts(params):
    return discover.sort_choices(params, FRESH_SORTER, FRESH_SORTER[0][0])


def index_sorts(params):
    return discover.sort_choices(params, INDEX_SORTER, INDEX_SORTER[0][0])


def _fresh_cards(kind=None, limit=None, sort=""):
    with api._library() as lib:
        state = api._state(lib)
    rows = _sorted_rows(_fresh(kind), sort)[:limit]
    known = {"%s:%s" % (item_kind, tmdb_id): {"n": entry.get("n") or 0, "iv": entry.get("iv") or None,
                                               "d": entry.get("d") or "", "q": int(entry.get("q") or 0),
                                               "s": int(entry.get("s") or 0)}
             for _when, item_kind, tmdb_id, entry in rows}
    discover._mark_known(state, known)
    cards = []
    for _when, item_kind, tmdb_id, entry in rows:
        poster = entry.get("p") or ""
        cards.append(discover._title_card(
            item_kind, tmdb_id, entry["t"], int(entry.get("y") or 0),
            discover.IMG + "w342" + poster if poster.startswith("/") else "", entry.get("o") or "", state,
            rating=int(entry.get("v") or 0)))
    return cards


def discover_fresh(params):
    """Výpis „Nově na Přehraj.to“: tituly, které se tam objevily v posledních týdnech."""
    kind = params.get("kind") if params.get("kind") in ("movie", "tv") else None
    sort = params.get("sort") if params.get("sort") in dict(FRESH_SORTER) else FRESH_SORTER[0][0]
    cards = _fresh_cards(kind, sort=sort)
    count = len(cards)
    if cards:
        cards.insert(0, _sort_card("fresh_sorts", params, FRESH_SORTER, sort))
    return {"items": cards, "next": None, "count": discover.count_text(count),
            "empty": "Zatím tu nic není. Seznam se plní sám na pozadí – nové tituly se objeví, "
                     "jakmile je aplikace na Přehraj.to najde."}


def discover_fresh_row(params):
    """Řada „Nově na Přehraj.to“ na hlavní obrazovce; prázdná se neukazuje (``hide``)."""
    cards = _fresh_cards(limit=api.HOME_ROW)
    if not cards:
        return {"items": [], "hide": True}
    cards.append(api._card("Zobrazit vše", api._target("list", "discover_fresh", {}, "Nově na Přehraj.to"),
                           "Filmy a seriály, které přibyly", icon="all"))
    return {"items": cards}


def _tier(entry, prefer):
    """Pořadí podle jazyka: při předvolbě dabingu nejdřív dabované (a co je v iVysílání), pak
    originály s titulky, pak ostatní; při předvolbě titulků naopak."""
    dubbed, subtitled = bool(entry.get("d") or entry.get("iv")), bool(entry.get("s"))
    first, second = (subtitled, dubbed) if prefer == "subs" else (dubbed, subtitled)
    return 0 if first else 1 if second else 2


def _catalogued(kind=None):
    """Všechny tituly, o kterých se ví, že jsou k mání: ``[(druh, číslo, záznam)]``."""
    return [(item_kind, tmdb_id, entry) for item_kind, tmdb_id, entry in _entries()
            if (entry.get("n") or entry.get("iv")) and entry.get("t") and (not kind or item_kind == kind)]


def discover_index(params):
    """Výpis „Vše na Přehraj.to“: co aplikace na Přehraj.to (a v iVysílání) dosud našla.

    Řazeno tak, jak si to uživatel přeje vidět: nejdřív v jazyce podle předvolby (dabované, pak
    originály s titulky), v každé skupině od nejlepší známé kvality a od lépe hodnocených."""
    kind = params.get("kind") if params.get("kind") in ("movie", "tv") else None
    offset = api._offset(params)
    with api._library() as lib:
        state = api._state(lib)
    prefer = (state.get("prefs") or {}).get("language")
    sort = params.get("sort") if params.get("sort") in dict(INDEX_SORTER) else INDEX_SORTER[0][0]
    rows = _catalogued(kind)
    rows.sort(key=lambda row: (_tier(row[2], prefer), -int(row[2].get("q") or 0), -int(row[2].get("v") or 0),
                               row[2].get("t") or "", row[1]))
    rows = _sorted_rows(rows, sort)
    window = rows[offset:offset + INDEX_PAGE]
    discover._mark_known(state, {
        "%s:%s" % (item_kind, tmdb_id): {"n": entry.get("n") or 0, "iv": entry.get("iv") or None,
                                         "d": entry.get("d") or "", "q": int(entry.get("q") or 0),
                                         "s": int(entry.get("s") or 0)}
        for item_kind, tmdb_id, entry in window})
    cards = []
    for item_kind, tmdb_id, entry in window:
        poster = entry.get("p") or ""
        cards.append(discover._title_card(
            item_kind, tmdb_id, entry["t"], int(entry.get("y") or 0),
            discover.IMG + "w342" + poster if poster.startswith("/") else "", entry.get("o") or "", state,
            rating=int(entry.get("v") or 0)))
    following = {"call": "discover_index", "params": dict(params, offset=offset + INDEX_PAGE)} \
        if offset + INDEX_PAGE < len(rows) else None
    if offset == 0 and cards:
        cards.insert(0, _sort_card("index_sorts", params, INDEX_SORTER, sort))
    return {"items": cards, "next": following, "count": discover.count_text(len(rows)),
            "empty": "Zatím tu nic není. Seznam se plní sám na pozadí, když je aplikace otevřená – "
                     "první tituly se objeví během pár minut."}


def everything_tile():
    """Dlaždice „Všechny filmy a seriály“ v sekci Objevuj: celý seznam dostupných titulů dohromady."""
    count = len(_catalogued())
    return api._card("Všechny filmy a seriály", api._target("list", "discover_index", {},
                                                            "Filmy a seriály na Přehraj.to"),
                     "K mání %d · seřadit podle hodnocení nebo roku" % count if count
                     else "Seznam se plní sám na pozadí", icon="all")


def catalogue_tile(kind):
    """Dlaždice „Vše na Přehraj.to“ pro obrazovku Filmy / Seriály: kolik titulů už seznam zná."""
    count = len(_catalogued(kind))
    name = "Seriály" if kind == "tv" else "Filmy"
    return api._card("Vše na Přehraj.to", api._target("list", "discover_index", {"kind": kind},
                                                      "%s na Přehraj.to" % name),
                     "K mání %d – seznam se plní sám" % count if count else "Seznam se plní sám na pozadí",
                     icon="all:" + ("series" if kind == "tv" else "movies"))


ACTIONS = {"discover_fresh": discover_fresh, "discover_fresh_row": discover_fresh_row,
           "fresh_sorts": fresh_sorts, "index_sorts": index_sorts,
           "discover_index": discover_index, "index_keepalive": keepalive}
