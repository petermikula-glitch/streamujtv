# Pravidla projektu

Tento soubor je společné zadání pro všechny, kdo na projektu pracují – lidi i AI asistenty
(ChatGPT/Codex, Claude). Platí pro celý repozitář. Vlastník projektu má vždy poslední slovo.

## Co v repozitáři je

| Složka | Obsah |
| --- | --- |
| `plugin.video.prehrajto.mojevidea/` | doplněk pro Kodi 19+ (Python); popis v `README.md` |
| `android/` | aplikace pro Android TV (Kotlin + Python přes Chaquopy); popis v `android/README.md` |
| `tests/` | testy doplňku i logiky aplikace (pytest, bez Kodi a bez Androidu) |
| `tools/build_zip.py` | sestavení Kodi repozitáře |
| `repository.petermikula/`, `repo/`, `docs/` | Kodi repozitář a GitHub Pages – **generované soubory** |
| `.github/workflows/` | testy a automatické vydávání aplikace |

## Spolupráce více autorů

Cíl: nikdo nepřepíše práci někoho jiného.

1. **Pravda je ve větvi `main`.** Před každým úkolem `git fetch origin` a nová větev z `origin/main`.
   Nevycházejte ze starší kopie ani z kódu vloženého do chatu.
2. **Každý pracuje ve vlastní větvi** (`claude/…`, `codex/…`). Do `main` se změny dostávají až hotové:
   pull requestem, nebo sloučením, ke kterému dal vlastník výslovný souhlas. Do `main` se nikdy
   nepoužívá `--force` a historie se nepřepisuje.
3. **Jeden úkol, jeden autor.** Než začnete, podívejte se na otevřené pull requesty a větve; pokud
   na téže věci někdo pracuje, nepokračujte a řekněte to vlastníkovi.
4. **Měňte jen to, co úkol potřebuje.** Žádné hromadné přeformátování, přejmenovávání ani „úklid“
   souborů mimo zadání – právě to způsobuje konflikty.
5. **Před sloučením** zařaďte do své větve aktuální `main` (`git merge origin/main`). Konflikt řešte
   spojením obou změn, ne výběrem své verze; když si nejste jistí, zeptejte se vlastníka.
6. **Do popisu změny napište**, co jste udělali, co jste ověřili a co ověřené není.

## Co ověřit před odevzdáním

```bash
python3 -m pytest -q -p no:cacheprovider tests      # musí projít celé
python3 tools/build_zip.py --check                   # Kodi repozitář odpovídá zdrojům
```

Při změně v `android/` navíc sestavení (`cd android && gradle assembleDebug lintDebug`, postup
v `android/README.md`). Stejné kontroly běží na GitHubu u každého pull requestu.
Testy se neobcházejí: nepřeskakují se, nemažou a neupravují jen proto, aby prošly.

## Sdílená logika

Moduly `catalog.py`, `csfd.py`, `db.py`, `ivysilani.py`, `nameparser.py`, `prehrajto.py`,
`provider.py`, `search_results.py` a `tmdb.py` v `plugin.video.prehrajto.mojevidea/resources/lib/`
používá doplněk **i** aplikace (kopírují se do ní při sestavení). Proto:

- nesmějí záviset na Kodi (žádný `import xbmc…`) ani na Androidu,
- musejí běžet v Pythonu 3.7 (Kodi) i 3.11 (aplikace) – žádná novější syntaxe,
- změna tabulek v `db.py` znamená zvýšit `SCHEMA_VERSION` a doplnit převod starých dat,
- změna chování se projeví na obou místech – spusťte všechny testy, ne jen „ty své“.

Aplikace: o obsahu obrazovek a navigaci rozhoduje `android/app/src/main/python/app_api.py`
(karty a jejich cíle), Kotlin je jen vykresluje. Novou funkci tedy začněte tam a pokryjte ji
v `tests/test_app_api.py`.

## Vydávání

**Verzi zvedá jen ten, kdo právě vydává, a jen na pokyn vlastníka.**

- **Aplikace:** `versionCode` a `versionName` v `android/app/build.gradle.kts` a text
  v `android/RELEASE_NOTES.md`. Jakmile se nová verze dostane do `main`, GitHub ji sám sestaví,
  vydá (`app-v<verze>`) a aplikace ji nabídne vlastníkovi k instalaci. Rozpracované změny proto
  verzi nemění.
- **Doplněk pro Kodi:** `version` a `<news>` v `addon.xml`, popis v `README.md`, pak
  `python3 tools/build_zip.py`. Skript přebalí i nezměněné balíčky – ty vraťte
  (`git checkout -- repo/zips/repository.petermikula docs/repository.petermikula-*.zip`)
  a ověřte `python3 tools/build_zip.py --check`. Soubory v `repo/` a `docs/` ručně neupravujte.

## Bez souhlasu vlastníka neměnit

- `android/signing/` a nastavení podpisu – jiným klíčem by nešla nainstalovat aktualizace,
- `.github/workflows/` – automatické vydávání,
- `applicationId` aplikace a `id` doplňků,
- adresy, odkud se berou aktualizace (GitHub vydání, Kodi repozitář).

Do repozitáře nepatří přihlašovací údaje, tokeny ani soukromá data vlastníka (ani v testech).

## Zásady vůči cizím službám

- **Přehraj.to:** jen vlastní účet uživatele a to, co mu web sám nabízí.
- **iVysílání ČT:** poctivá hlavička `User-Agent`, žádné předstírání původu požadavku
  (`Origin`/`Referer`), žádné obcházení omezení (ani územního) a žádné stahování pořadů.
- **Chráněné pořady (DRM):** doplněk pro Kodi je nepřehrává. Aplikace pro Android TV je smí přehrát
  jen vestavěným Widevine zařízení a jen s adresou licence, kterou rozhraní ČT samo vrátí
  (rozhodnutí vlastníka). Do kódu nepatří tokeny ani adresy opsané z oficiálního přehrávače,
  ukládání chráněného obsahu ani jakékoli odstraňování ochrany.

## Zvyklosti

- Komentáře v kódu a texty pro uživatele česky; zprávy commitů anglicky, věcně.
- Chyby pro uživatele srozumitelně česky, technický detail do logu.
- Co nejde ověřit (skutečné zařízení, skutečný účet), napište otevřeně – nepředstírejte, že to funguje.
