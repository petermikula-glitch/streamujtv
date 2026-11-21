# Streamuj.tv – Moje videa (doplněk pro Kodi)

Doplněk pro Kodi 19+ (Matrix, Nexus, Omega, Piers), který z vašeho **premium účtu streamuj.tv**
načte nahraná videa a udělá z nich přehledný katalog filmů a seriálů s hodnocením a popisy
z **ČSFD** (záložně z TMDB).

## Co umí

- **Synchronizace s účtem** – načte všechna vaše videa ze stránky „Moje videa“ (volitelně i
  oblíbená videa a playlisty, které se uloží jako kolekce). Na pozadí se umí synchronizovat
  automaticky a videa smazaná z účtu z katalogu odebere.
- **Automatické třídění** – z názvů souborů (`Pelisky.1999.1080p.CZ.mkv`,
  `Hra o trůny S01E02 cz tit`, `Vyprávěj - díl 12` …) pozná název, rok, sérii a díl, jazyk
  (dabing/titulky) a kvalitu. Víc verzí téhož filmu (CZ / EN, 720p / 1080p) se sloučí
  pod jeden titul, díly seriálů pod seriál a série.
- **Hodnocení, popisy a plakáty** z ČSFD (hodnocení v %, žánry, země, režie, herci, děj).
  Když ČSFD není dostupné, použije se TMDB (vlastní zdarma API klíč) – viz níže.
- **Procházení** – filmy podle žánru, desetiletí, země a hodnocení, nejlépe hodnocené,
  nově přidané; seriály → série → díly. Řazení podle názvu, roku, hodnocení a data přidání.
- **Vyhledávání** v celém katalogu podle názvu (i původního), herce, režiséra, žánru nebo
  názvu souboru – bez diakritiky, s historií hledání.
- **Katalogizace** – vlastní kolekce (např. „Chci vidět“, „Pro děti“), ruční oprava
  názvu/roku/zařazení (film ↔ díl seriálu), ruční přiřazení ČSFD/TMDB nebo hodnocení,
  skrývání videí, seznam nerozpoznaných titulů.
- **Přehrávání** v kvalitě vašeho premium účtu, s volbou preferovaného jazyka a kvality
  a s titulky, pokud je video má.
- **Export do knihovny Kodi** (`.strm` + `.nfo` s hodnocením), takže se filmy a seriály
  objeví i v hlavní knihovně Kodi.
- Ruční **import odkazů** (jeden odkaz, nebo textový soubor se seznamem odkazů).

## Instalace

1. Stáhněte ZIP (nebo ho sestavte příkazem `python3 tools/build_zip.py`, vznikne v `dist/`).
2. V Kodi: *Nastavení → Systém → Doplňky → povolit Neznámé zdroje*.
3. *Doplňky → Instalovat ze ZIP souboru* → vyberte `plugin.video.streamujtv-1.0.0.zip`.
4. Otevřete nastavení doplňku a vyplňte **uživatelské jméno a heslo** k streamuj.tv.
5. Spusťte *Nástroje → Synchronizovat s účtem streamuj.tv*.

V *Nástroje → Stav účtu* ověříte přihlášení a platnost premia.

## Hodnocení z ČSFD – důležité upozornění

ČSFD nemá veřejné API a jeho web je chráněný anti-bot ochranou (Anubis, výpočetní výzva
v prohlížeči). **Doplněk tuto ochranu záměrně neobchází.** Pokud ČSFD místo stránky vrátí
výzvu proti botům, doplněk:

- přestane ČSFD v daném běhu zatěžovat dalšími dotazy,
- použije **TMDB**, pokud je v nastavení vyplněný API klíč (zdarma po registraci na
  <https://www.themoviedb.org/settings/api>; funguje v3 klíč i v4 token), – TMDB dává české
  názvy, popisy, plakáty a hodnocení,
- případně si hodnocení zadáte ručně (kontextové menu titulu → *Přiřadit ČSFD / TMDB*).

Pokud ČSFD automatické dotazy pustí, čtou se z něj strukturovaná data (JSON-LD) a v záloze HTML.

## Jak to funguje uvnitř

| Část | Soubor | Poznámka |
|---|---|---|
| API přehrávače streamuj.tv | `resources/lib/streamuj.py` | `json_api_player.php?action=check-user` / `get-video-links`, mezilehlý odkaz → finální mp4 (ověřeno proti živému webu) |
| Výpis vlastních videí | `resources/lib/streamuj.py` | přihlášení formulářem `/login`, čtení `/my_videos` včetně stránkování |
| Rozbor názvů | `resources/lib/nameparser.py` | film / seriál, rok, SxxEyy, jazyk, kvalita |
| Katalog | `resources/lib/db.py` | SQLite v profilu doplňku |
| ČSFD / TMDB | `resources/lib/csfd.py`, `tmdb.py` | |
| Obrazovky Kodi | `resources/lib/plugin.py` | |
| Automatická synchronizace | `service.py` | |

Heslo se do logu Kodi nikdy nezapisuje (ani jeho hash).

## Vývoj

```bash
python3 -m pytest tests          # testy (s náhradou Kodi API)
STREAMUJ_LIVE=1 python3 -m pytest tests -k live   # živý test API streamuj.tv
python3 tools/strings.py         # po úpravě textů vygeneruje strings.po (cs/en)
python3 tools/build_zip.py       # instalační ZIP do dist/
```

Licence: GPL-2.0-or-later.
