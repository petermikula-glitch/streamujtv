# Promítárna

Filmy a seriály z Přehraj.to, objevování podle TMDB a pořady iVysílání České televize – ve dvou
verzích nad stejnou logikou:

| Verze | Složka | Popis |
| --- | --- | --- |
| Aplikace pro Android TV | `android/` | Kotlin (Leanback, Media3) + Python přes Chaquopy; [android/README.md](android/README.md) |
| Přenosná verze pro Windows | `windows/` | okno nad stejnou logikou, přehrávač mpv; [windows/README.md](windows/README.md) |

Dřívější doplněk pro Kodi se už nevyvíjí a z repozitáře byl odstraněn (i s Kodi repozitářem
a stránkou na GitHub Pages). Najdete ho v historii gitu.

## Stažení

Obě verze se vydávají automaticky jako vydání na GitHubu: aplikace jako `app-v<verze>`
(soubor `MojeVidea-<verze>.apk`), Windows jako `win-v<verze>` (zip). Aplikace na televizi si
nové vydání najde sama a nabídne ho k instalaci.

## Jak je to postavené

- `shared/` – sdílená logika: klient Přehraj.to (`prehrajto.py`, `provider.py`), iVysílání
  (`ivysilani.py`), katalog v SQLite (`db.py`, `catalog.py`), rozbor názvů souborů (`nameparser.py`),
  hledání a seskupení výsledků (`search_results.py`), TMDB a ČSFD (`tmdb.py`, `csfd.py`).
  Při sestavení se kopíruje do aplikace pro Android i do Windows verze.
- `android/app/src/main/python/app_api.py` – rozhraní mezi obrazovkami a logikou: obrazovky
  (Kotlin na televizi, HTML ve Windows) jen vykreslují karty, které vrátí `call(akce, parametry)`.
- `tests/` – testy sdílené logiky, rozhraní aplikace i Windows verze (pytest, bez Androidu a bez Windows).

## Jak aplikace pracuje s Přehraj.to

- Hledá a přehrává to, co Přehraj.to samo nabízí; přihlašuje se jen vaším účtem. S **Premium**
  se přehrává originální soubor, jinak nejlepší překódovaná verze. Titulky ze stránky videa se
  načtou samy.
- Adresa souboru se pamatuje 6 hodin (podepsaný odkaz platí zhruba den), takže pokračování ve
  sledování nechodí znovu na web.
- Když Přehraj.to odpoví „příliš mnoho dotazů“ (HTTP 429), aplikace chvíli počká a zkusí to znovu;
  ověřování dostupnosti na pozadí si dá delší přestávku.
- Přihlášení se ukládá – každé nové přihlášení totiž na Přehraj.to zakládá další záznam ve
  „Správě přihlášených zařízení“.

## Hodnocení z ČSFD

ČSFD nemá veřejné API a jeho web je chráněný proti botům. **Aplikace tuto ochranu záměrně
neobchází**: když ČSFD místo stránky vrátí výzvu, přestane ho v daném běhu zatěžovat a použije TMDB
(klíč zdarma na <https://www.themoviedb.org/settings/api>). Když ČSFD dotazy pustí, čtou se
strukturovaná data stránky (JSON-LD).

## Vývoj

```bash
python3 -m pytest -q -p no:cacheprovider tests      # testy (bez Androidu a bez Windows)
cd android && gradle assembleDebug lintDebug          # sestavení aplikace (postup v android/README.md)
```

Pravidla spolupráce, kontroly a vydávání jsou v [AGENTS.md](AGENTS.md), šetření minut GitHub
Actions v [CLAUDE.md](CLAUDE.md).

## Licence

GPL-2.0, viz [LICENSE.txt](LICENSE.txt).
