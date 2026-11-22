# Přehraj.to – Moje videa (doplněk pro Kodi)

Doplněk **Přehraj.to – Moje videa** (`plugin.video.prehrajto.mojevidea`) pro Kodi 19+ (Matrix,
Nexus, Omega, Piers). Instaluje se z Kodi repozitáře, který je přímo v tomto GitHub repozitáři
(složka `repo/`) – viz [Instalace](#instalace).

Po přihlášení načte **videa nahraná na váš účet** a udělá z nich přehledný katalog filmů
a seriálů s hodnocením a popisy z **ČSFD** (záložně z TMDB). Podrobnosti k práci s webem
Přehraj.to jsou v sekci [Jak doplněk pracuje s Přehraj.to](#jak-doplněk-pracuje-s-přehrajto).

## Co umí

- **Synchronizace s účtem** – načte všechna vaše videa ze stránky „Nahraná videa“. Na pozadí
  se umí synchronizovat automaticky a videa smazaná z účtu z katalogu odebere.
- **Automatické třídění** – z názvů souborů (`Pelisky.1999.1080p.CZ.mkv`,
  `Hra o trůny S01E02 cz tit`, `Vyprávěj - díl 12`, `Ulice 4012. díl`, `Johnny English (2003) CZ` …)
  pozná název, rok, sérii a díl, jazyk (dabing/titulky) a kvalitu. Víc verzí téhož filmu (CZ / EN, 720p / 1080p) se sloučí
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
- **Přehrávání** originálního souboru (s Premium) nebo nejlepší překódované verze, s volbou
  preferovaného jazyka a kvality a s titulky, pokud je video má.
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

V doplňku Přehraj.to otevřete **Hledat** a zadejte dotaz. Výsledky z vlastního katalogu,
Přehraj.to a dostupných metadat TMDB se zobrazí společně. Pokud má titul více ověřených shod,
otevře se seznam verzí s jazykem, kvalitou a velikostí, pokud jsou tyto údaje k dispozici.
Automatické online hledání můžete vypnout v **Nastavení → Hledat**. Online video lze přes
kontextovou nabídku přidat do vlastního katalogu.
S nastaveným klíčem TMDB můžete procházet filmografii herce. V přehledu podle herce nebo země
doplněk ověřuje první čtyři dosud nezkontrolované tituly na dané stránce. **Jen nalezené na Přehraj.to**
zobrazí potvrzené shody a **Ověřit další tituly na této stránce** načte další dávku.

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
délky a náhledy epizod. Popis dílu je v kontextové nabídce **Detail epizody**.
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
`Pelíšky 1999` a `Hra o trůny 2x03`. Filmy se správným rokem řadí před shody bez uvedeného roku.
U seriálových dotazů s číslem dílu zůstává série i díl součástí hledání při zkoušení dalších názvů.
**Detail výsledku a verze** v kontextové nabídce otevře přehrávání a dostupné varianty.

V **Nástroje → Propojit s knihovnou Kodi** lze vybrat složku, exportovat videa,
přejít k přidání zdrojů a zkontrolovat, kolik exportovaných videí Kodi načetlo.
Po zapnutí **Sdílet historii sledování s knihovnou Kodi** doplněk propojí
pozice a zhlédnutí u rozpoznaných exportů. Záznamy z Kodi se načtou při otevření
Rozkoukaných nebo ručně v průvodci.

Společná položka **Československé filmy** je v **Moje filmy** i v **Objevovat**.
Ve vlastním katalogu zahrnuje českou, slovenskou i historickou československou produkci
podle metadat ČSFD nebo TMDB. V nabídce **Hledat** lze zadat například `Francie` nebo
`československé komedie z 80. let`. V **Objevovat → Podle země** využijete katalog TMDB.
Volba **Upřesnit výběr** v jeho výsledcích otevře dialog pro kombinaci země, žánru a roku či desetiletí.
**Novinky** nabízejí nově přidaná videa z účtu a filmy vydané v posledních 90 dnech
podle TMDB. Velikost výpisu lze upravit v **Nastavení → Hledat**.
Velké výpisy vlastního katalogu (**Moje filmy**, **Seriály**, **Všechna videa**) se zobrazují
po stránkách s položkou **Další stránka** na konci seznamu. Počet položek na stránku (100, 250, 500
nebo vše najednou) nastavíte v **Nastavení → Hledat → Položek katalogu na stránku**.
Doplněk si pamatuje posledních 20 online hledání (10 minut) a odpovědi TMDB (žánry a země týden,
seznamy 6 hodin, detaily den), takže návrat zpět a opakované otevření nečekají na síť.
Doplnění a obnova metadat čtou z TMDB vždy aktuální data.
Mezipaměť lze kdykoli smazat v **Nástroje → Vymazat mezipaměť** (katalog ani historie se tím nemění).
Řazení v Kodi přeskládá jen zobrazenou stránku; pořadí celého výpisu (podle názvu, data přidání,
hodnocení nebo roku) změníte položkou **Řazení celého katalogu** nahoře na první stránce.

V **Nastavení → Synchronizace** lze k automatické synchronizaci zapnout **Rychlou automatickou
synchronizaci**: skončí u první stránky nahraných videí, na které není nic nového. Úplná
synchronizace, která jediná odebírá smazaná videa, proběhne dál nejméně jednou denně a při každém
ručním spuštění. Rychlá předpokládá, že Přehraj.to řadí nahraná videa od nejnovějších; pokud by se
vám nová videa objevovala až po úplné synchronizaci, volbu vypněte.
Když ČSFD není dostupné nebo je vypnuté, doplňují se metadata z TMDB čtyřmi dotazy souběžně.
Výsledek TMDB, u kterého nesedí název a rok se liší o víc než rok, doplněk nepřijme a titul zůstane
mezi nerozpoznanými.

Export do knihovny Kodi po sobě uklízí: soubory titulů, které z katalogu zmizely (smazané video,
přečíslovaný díl, opravený název), smaže a odebere i odpovídající položky z knihovny Kodi. Soubory,
které jste v cílové složce ručně upravili, nechává být.
Výchozí výpis spojuje dvě stránky služby, pokud jsou dostupné.

**Další části** v detailu filmu vycházejí z filmových kolekcí TMDB. Pokud je
navazující film dostupný ve vlastním katalogu, doplněk jej může nabídnout po
dokoukání. Zapíná se to v **Nastavení → Přehrávání**.

Ve výsledcích Přehraj.to lze otevřít **Filtrovat výsledky Přehraj.to**
a zkombinovat český nebo slovenský dabing, kvalitu od 1080p a skrytí upoutávek.
Pokud doplněk nenajde odpovídající video, zkusí dostupné původní, české nebo slovenské názvy
z TMDB. Při prázdném výsledku nabídne podle situace opravu názvu, uvolnění filtrů nebo nový
online pokus.
**Chci vidět** označí filmy a seriály, které se objevily po synchronizaci, jako
**Nově dostupné**. V **Dalších částech** se zobrazuje stav každého filmu a
zkratka na první nezhlédnutý díl. Při chybě přehrávání doplněk nabídne jiné
verze stejného filmu nebo epizody ve vlastním katalogu.

## Instalace

Kodi neumí instalovat přímo z gitu, proto je tu **Kodi repozitář**. Nainstaluje se jednou
a pak se z něj instaluje doplněk – včetně automatických aktualizací.

1. V Kodi: *Nastavení → Systém → Doplňky → povolit Neznámé zdroje*.
2. Přidejte zdroj: *Nastavení → Správce souborů → Přidat zdroj → <Žádný>* a zadejte adresu
   **`https://petermikula-glitch.github.io/streamujtv/`**, pojmenujte ho třeba `petermikula`.
   (Adresa funguje, až je v GitHubu zapnuté GitHub Pages: *Settings → Pages → Deploy from a branch →
   `main` / `/docs`*.)
3. *Doplňky → Instalovat ze ZIP souboru → petermikula* → `repository.petermikula-1.0.1.zip`.

   Bez GitHub Pages: stáhněte
   [repository.petermikula-1.0.1.zip](https://raw.githubusercontent.com/petermikula-glitch/streamujtv/main/repo/zips/repository.petermikula/repository.petermikula-1.0.1.zip)
   jinde (počítač, telefon), nahrajte na USB disk / do sdílené složky a v kroku 3 vyberte ten disk.
4. *Doplňky → Instalovat z repozitáře → Repozitář petermikula → Doplňky pro video* →
   **Přehraj.to – Moje videa** → *Instalovat*.
5. Otevřete nastavení doplňku a vyplňte **přihlašovací údaje** (e-mail a heslo k Přehraj.to).
6. Spusťte *Nástroje → Synchronizovat s účtem*.

Doplněk jde nainstalovat i samostatně ze ZIPu v `repo/zips/plugin.video.prehrajto.mojevidea/`,
pak se ale neaktualizuje sám.

Adresa repozitáře obsahuje z historických důvodů název `streamujtv` (jméno tohoto GitHub
repozitáře). Doplněk pro streamuj.tv, který tu dříve byl, už součástí repozitáře není.

V *Nástroje → Stav účtu* ověříte přihlášení a platnost premia.

## Jak doplněk pracuje s Přehraj.to

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
| Klient Přehraj.to | `resources/lib/prehrajto.py` | přihlášení, výpis nahraných videí, hledání, odkazy na soubory |
| Rozbor názvů | `resources/lib/nameparser.py` | film / seriál, rok, SxxEyy, jazyk, kvalita |
| Katalog | `resources/lib/db.py`, `catalog.py` | SQLite v profilu doplňku, synchronizace, doplnění metadat |
| ČSFD / TMDB | `resources/lib/csfd.py`, `tmdb.py` | |
| Export do knihovny Kodi | `resources/lib/library_export.py`, `kodi_library.py` | |
| Obrazovky Kodi | `resources/lib/plugin.py`, `ui.py`, `views_*.py`, `playback.py` | |
| Sledování přehrávání a automatická synchronizace | `service.py` | |

`resources/lib/plugin.py` je jen router: podle akce načte modul s danou
obrazovkou (`views_catalog`, `views_progress`, `views_discover`, `views_search`, `views_collections`,
`views_tools`, `views_kodi`, `views_edit`, `playback`), společné prvky jsou v `ui.py`. Síťové knihovny
se načítají až při prvním použití, takže obrazovky vlastního katalogu startují rychleji.
Klient Přehraj.to (`prehrajto.py`) udržuje spojení se serverem otevřené mezi požadavky.

Heslo se do logu Kodi nikdy nezapisuje (ani jeho hash).

## Vývoj

Překlady se upravují přímo v souborech `resources/language/*/strings.po`.

```bash
python3 -m pytest tests                  # všechny testy (náhrada Kodi API je v tests/fake_kodi)
PREHRAJTO_LIVE=1 python3 -m pytest tests -k live  # živý test přihlášení Přehraj.to
python3 tools/bench_prehrajto.py         # rychlost obrazovek nad syntetickým katalogem
# nová verze doplňku: zvyšte version v addon.xml, spusťte build_zip.py a pushněte do main
python3 tools/build_zip.py               # sestaví Kodi repozitář do repo/ (ZIPy + addons.xml)
```

Licence: GPL-2.0-or-later.
