# Moje videa – doplňky pro Kodi (streamuj.tv a Přehraj.to)

Dva doplňky pro Kodi 19+ (Matrix, Nexus, Omega, Piers) se stejným katalogem a obrazovkami:

| Doplněk | Složka |
|---|---|
| **Streamuj.tv – Moje videa** | `plugin.video.streamujtv` |
| **Přehraj.to – Moje videa** | `plugin.video.prehrajto.mojevidea` |

Instalují se z Kodi repozitáře, který je přímo v tomto GitHub repozitáři (složka `repo/`) –
viz [Instalace](#instalace).

Oba po přihlášení načtou **videa nahraná na váš účet** a udělají z nich přehledný katalog filmů
a seriálů s hodnocením a popisy z **ČSFD** (záložně z TMDB). Vše níže platí pro oba doplňky,
rozdíly jsou v sekci [Přehraj.to](#přehrajto--rozdíly).

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
- Ruční **import odkazů** (jeden odkaz, nebo textový soubor se seznamem odkazů). Stejné video
  přidané odkazem i synchronizací se v katalogu sloučí (kolekce i historie zůstanou).
- **Rozkoukané a Naposledy přehrané** – služba na pozadí si pamatuje, kde jste ve videu
  skončili; v seznamech (i ve filmech a dílech) Kodi nabídne pokračování, dokoukaná videa
  jsou označená jako zhlédnutá.
- **Diagnostika** (*Nástroje → Uložit diagnostiku*) – uloží stránky vašeho účtu do vybrané
  složky, aby šel parser doladit podle skutečného webu. Jméno, e-mail, heslo, tokeny a skrytá
  pole formulářů se z nich před uložením odstraní.

### Vyhledávání na Přehraj.to a záloha

V doplňku Přehraj.to otevřete **Hledat** a zadejte dotaz. Pod výsledky z vlastního katalogu
vyberte **Zahrnout online výsledky**. Pod společným přehledem jsou odkazy na úplné
výsledky jednotlivých služeb.
Video můžete přehrát rovnou nebo ho přes kontextovou nabídku přidat do vlastního katalogu.

V **Nástroje → Zálohovat katalog** uložíte katalog, kolekce, ruční úpravy a historii
do souboru JSON. **Obnovit katalog** po potvrzení nahradí aktuální data zálohou
ze stejného doplňku. Záloha neobsahuje přihlášení, cookies ani mezipaměť přehrávacích odkazů.

V doplňku Přehraj.to nabízí kontextová nabídka v **Rozkoukaných** volbu
**Skrýt z Rozkoukaných**. Skryté položky lze v této sekci znovu zobrazit.
Kontextová nabídka série umožňuje označit všechny její dostupné díly za zhlédnuté.
V nastavení **Přehrávání** lze zapnout **Nabídnout další díl po skončení přehrávání**.

V **Moje filmy** lze podle jazyka, kvality a zhlédnutí sestavit filtr a uložit ho
pod vlastním názvem. **Objevovat → Chci vidět → Připravené k přehrání** ukáže
tituly, pro které už doplněk našel video ve vlastním katalogu.
V **Moje seriály** se po synchronizaci objeví počet nových dílů. Při otevření
seriálu se počet vynuluje a poslední dávka zůstane dostupná v seznamu dílů.
Pokud má seriál přiřazené TMDB ID a je nastavený API klíč, doplněk načte názvy,
délky a náhledy epizod. Popis díje je v kontextové nabídce **Detail epizody**.
**Nástroje → Hromadně přiřadit díly** umožní zkontrolovat a opravit čísla vybraných
dílů před jejich společným přiřazením k seriálu a sérii.

Při výběru jiné verze stejného filmu či dílu nabídne Kodi pokračování od
uložené pozice. Stejnou volbu najdete v kontextové nabídce **Pokračovat z jiné
verze**. Rozdílnou nebo neznámou délku doplněk před pokračováním ověří dotazem.

V **Nastavení → Knihovna Kodi** vyberte cílovou složku a zapněte
**Exportovat do knihovny Kodi po synchronizaci**. Volba **Spustit aktualizaci
knihovny Kodi po automatickém exportu** načte nové soubory do nastavených
zdrojů Kodi. Přidejte podsložky `Filmy` a `Serialy` ve správci zdrojů
Kodi jako obsah typu Filmy a Seriály. Export zachovává ručně upravené soubory
v cílové složce.

Hledání přijímá také dotazy s rokem nebo číslem dílu, například
`Pelíšky 1999` a `Hra o trůny 2x03`. Volba **Zahrnout online výsledky** přidá
první stránku z Přehraj.to a případně TMDB a sloučí jednoznačné shody.
**Detail výsledku a verze** v kontextové nabídce otevře přehrávání a dostupné varianty.

V **Nástroje → Propojit s knihovnou Kodi** lze vybrat složku, exportovat videa,
přejít k přidání zdrojů a zkontrolovat, kolik exportovaných videí Kodi načetlo.
Po zapnutí **Sdílet historii sledování s knihovnou Kodi** doplněk propojí
pozice a zhlédnutí u rozpoznaných exportů. Záznamy z Kodi se načtou při otevření
Rozkoukaných nebo ručně v průvodci.

## Instalace

Kodi neumí instalovat přímo z gitu, proto je tu **Kodi repozitář**. Nainstaluje se jednou
a pak se z něj instalují oba doplňky – včetně automatických aktualizací.

1. V Kodi: *Nastavení → Systém → Doplňky → povolit Neznámé zdroje*.
2. Přidejte zdroj: *Nastavení → Správce souborů → Přidat zdroj → <Žádný>* a zadejte adresu
   **`https://petermikula-glitch.github.io/streamujtv/`**, pojmenujte ho třeba `petermikula`.
   (Adresa funguje, až je v GitHubu zapnuté GitHub Pages: *Settings → Pages → Deploy from a branch →
   `main` / `/docs`*.)
3. *Doplňky → Instalovat ze ZIP souboru → petermikula* → `repository.petermikula-1.0.0.zip`.

   Bez GitHub Pages: stáhněte
   [repository.petermikula-1.0.0.zip](https://raw.githubusercontent.com/petermikula-glitch/streamujtv/main/repo/zips/repository.petermikula/repository.petermikula-1.0.0.zip)
   jinde (počítač, telefon), nahrajte na USB disk / do sdílené složky a v kroku 3 vyberte ten disk.
4. *Doplňky → Instalovat z repozitáře → Repozitář petermikula → Doplňky pro video* →
   **Streamuj.tv – Moje videa** a/nebo **Přehraj.to – Moje videa** → *Instalovat*.
5. Otevřete nastavení doplňku a vyplňte **přihlašovací údaje** (streamuj.tv: jméno a heslo,
   Přehraj.to: e-mail a heslo).
6. Spusťte *Nástroje → Synchronizovat s účtem*.

Doplňky jdou nainstalovat i samostatně ze ZIPu v `repo/zips/<doplněk>/`, pak se ale
neaktualizují samy.

V *Nástroje → Stav účtu* ověříte přihlášení a platnost premia.

## Přehraj.to – rozdíly

- Synchronizace čte stránku **Nahraná videa** (`/profil/nahrana-videa`) vašeho účtu.
  Další videa můžete přidat odkazem (`https://prehraj.to/<název>/<kód>`) přes *Nástroje → Přidat
  odkaz* nebo importem textového souboru s odkazy.
- Přehrávání: s **Premium** se přehrává **originální soubor** (JSON API Přehraj.to, záložně
  stažení přes web), bez premia nebo při chybě nejlepší **překódovaná verze** (až 1080p).
  Titulky ze stránky videa se načtou automaticky.
- U verzí filmu se zobrazuje **kvalita a velikost** souboru z výpisu nahraných videí.
- Adresa souboru se **pamatuje 6 hodin** (podepsaný odkaz platí zhruba den), takže opakované
  spuštění nebo pokračování ve sledování nechodí znovu na web.
- Když Přehraj.to odpoví „příliš mnoho dotazů“ (HTTP 429), doplněk chvíli počká a zkusí to znovu.
- U ručně vloženého odkazu se ze stránky videa dočte číslo videa (potřebuje ho rychlejší
  stažení originálu přes API).
- Přihlášení se ukládá (cookies v profilu doplňku) – každé nové přihlášení totiž na Přehraj.to
  zakládá další záznam ve „Správě přihlášených zařízení“.
- Oblíbená videa a playlisty Přehraj.to nemá, volby v nastavení proto chybí.
- Ověřeno naostro: přihlašovací formulář a odmítnutí špatného hesla. Stránku nahraných videí
  a stránku videa s premium jsem bez vašeho účtu neviděl – parser je napsaný podle známého
  markupu webu a je tolerantní (bere všechny odkazy na videa na stránce).

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

Sdílené soubory uvedené v `tools/sync_shared.py` se kopírují z `plugin.video.streamujtv`.
Každý doplněk má vlastní router, službu přehrávání, katalog, databázi a klienty
pro svou službu a TMDB. Překlady Přehraj.to se upravují v jeho souborech `strings.po`.

```bash
python3 -m pytest tests                  # testy streamuj.tv + sdíleného kódu (náhrada Kodi API)
python3 -m pytest tests_prehrajto        # testy Přehraj.to (spouštět zvlášť)
STREAMUJ_LIVE=1 python3 -m pytest tests -k live            # živý test API streamuj.tv
PREHRAJTO_LIVE=1 python3 -m pytest tests_prehrajto -k live  # živý test přihlášení Přehraj.to
python3 tools/sync_shared.py             # po úpravě sdíleného kódu
# nová verze doplňku: zvyšte version v addon.xml, spusťte build_zip.py a pushněte do main
python3 tools/strings.py                 # vygeneruje strings.po (cs/en) pro Streamuj.tv
python3 tools/build_zip.py               # sestaví Kodi repozitář do repo/ (ZIPy + addons.xml)
```

Licence: GPL-2.0-or-later.
