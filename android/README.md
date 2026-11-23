# Promítárna – aplikace pro Android TV

Aplikace se dříve jmenovala „Moje videa“. Vnitřní označení (`cz.petermikula.mojevidea`) a název
instalačního souboru (`MojeVidea-<verze>.apk`) zůstávají, aby aktualizace navazovaly.

Samostatná aplikace pro Android TV, která dělá totéž co doplněk pro Kodi: katalog videí
z vašeho účtu Přehraj.to a sekce iVysílání České televize. Kodi k ní není potřeba.

## Jak je postavená

- **Logika je sdílená s doplňkem.** Moduly `prehrajto.py`, `ivysilani.py`, `db.py`, `catalog.py`,
  `nameparser.py`, `tmdb.py`, `csfd.py`, `provider.py` a `search_results.py` se při sestavení
  kopírují z `plugin.video.prehrajto.mojevidea/resources/lib` a v aplikaci běží v Pythonu 3.11
  ([Chaquopy](https://chaquo.com/chaquopy/)). Oprava v doplňku se tak projeví i v aplikaci.
- **`app/src/main/python/app_api.py`** je jediné rozhraní mezi aplikací a logikou. Aplikace volá
  `call(akce, parametry)` a dostane JSON s kartami; každá karta nese cíl (výpis, přehrání, příkaz).
  O navigaci tedy rozhoduje Python a testuje se bez Androidu (`tests/test_app_api.py`).
- **Obrazovky** (Kotlin, Leanback): hlavní obrazovka s řadami karet, mřížka výpisu se stránkováním,
  hledání, nastavení s přihlášením a přehrávač (Media3/ExoPlayer) s ukládáním pozice.

## Sestavení

Potřebujete JDK 17+, Android SDK (platforma 35, build-tools 35.0.0), Python 3.11 a Gradle 8.13+.

```bash
cd android
echo "sdk.dir=/cesta/k/android-sdk" > local.properties
gradle assembleDebug                      # app/build/outputs/apk/debug/app-debug.apk
gradle lintDebug
python3 -m pytest ../tests/test_app_api.py
```

APK obsahuje Python pro 32bitový i 64bitový Android (`armeabi-v7a`, `arm64-v8a`).

### Zkouška v emulátoru

```bash
gradle assembleDebug -PextraAbi=x86                       # emulátor Android TV (x86)
gradle assembleDebug -PextraAbi=x86 -PtestCa=/cesta/ca.crt # navíc důvěra v autoritu zkušební proxy
```

`-PtestCa` je jen pro síť, kde proxy znovu šifruje HTTPS: přidá do ladicího sestavení důvěru v danou
autoritu. Bez tohoto přepínače se do APK nedostane nic. Systémovou proxy (`adb shell settings put
global http_proxy host:port`) aplikace používá celá, včetně části v Pythonu; tomu je ještě potřeba
autoritu připsat do `files/chaquopy/cacert.pem` (`adb shell run-as cz.petermikula.mojevidea …`).
Emulátor mívá jiné měřítko než televize – `adb shell wm density 213` nastaví 960 × 540 dp jako na TV.

### Podpis

Android dovolí nainstalovat novou verzi přes starou jen se stejným podpisem. Klíč aplikace je proto
v repozitáři (`signing/mojevidea.keystore`, heslo `android`, alias `mojevidea`) a každé sestavení ho
použije samo. Je to vědomé rozhodnutí vlastníka u osobní aplikace: klíč je veřejný, aktualizace si ale
aplikace bere jen z vydání tohoto repozitáře. Jiný klíč lze předat při sestavení:

```bash
gradle assembleDebug -PsigningKeystore=/cesta/klic.keystore -PsigningPassword=… -PsigningAlias=…
```

## Instalace na Android TV

1. V Google Play nainstalujte správce souborů (např. X-plore) nebo aplikaci Downloader.
2. V *Nastavení → Aplikace → Zabezpečení a omezení → Neznámé zdroje* mu povolte instalaci.
3. Otevřete soubor APK z USB disku (nebo ho stáhněte Downloaderem) a potvrďte instalaci.

## Vzhled a vlastní grafika

Vzhled je tušová malba na tlumeném rýžovém papíře: světlé pozadí, tmavá tuš, rumělkový akcent
(pečeť, rámeček vybrané karty). Barvy jsou v `res/values/colors.xml` – včetně barev knihovny Leanback,
která jinak počítá s tmavým vzhledem (`lb_…`) –, motivy obrazovek v `res/values/styles.xml`. Karty bez
obrázku (kategorie, nástroje) kreslí `TileDrawable` jako list papíru s motivem. Pozadí je klidné a podle
vybrané karty se nemění (`Backdrop.ENABLED`). Přehrávač zůstává tmavý (`player_…`).

Malby se dodávají jako černá tuš (nejvýš s jednou rumělkovou plochou) na čistě bílém pozadí;
`tools/import_graphics.py` bílou odstraní, takže motiv v aplikaci leží na barvě papíru.

Grafiku lze vyměnit bez zásahu do kódu – stačí soubor se správným názvem:

| Co | Soubor | Rozměr |
| --- | --- | --- |
| Ikona aplikace | `res/mipmap-xhdpi/ic_launcher.png` | 192 × 192 px (podklad 512 × 512) |
| Banner v nabídce Android TV | `res/drawable-xhdpi/banner.png`, `drawable-xxxhdpi` | 320 × 180 a 640 × 360 px |
| Logo na hlavní obrazovce | `res/drawable-nodpi/logo.png` | průhledné, výška 160 px |
| Pozadí obrazovek | `res/drawable-nodpi/app_background_image.webp` | 1920 × 1080 px |
| Malba na dlaždici | `res/drawable-nodpi/tile_<název>.webp` | 480 × 270 px, tuš na průhledném pozadí, bez textu, motiv vpravo |
| Ikona dlaždice | `res/drawable/ic_tile_<název>.xml` | obrysová, výřez 24 × 24 (barví se tuší) |
| Ikona sekce v postranní nabídce | `res/drawable/ic_section_<název>.xml` | obrysová, výřez 24 × 24 (barví se tuší) |
| Ilustrace prázdné obrazovky | `res/drawable-nodpi/empty_<název>.webp` | 480 × 320 px, průhledné pozadí |
| Ikona přehrávače | `res/drawable/ic_player_<název>.xml` | obrysová, bílá, výřez 24 × 24 |
| Avatar profilu | `res/drawable-nodpi/avatar_<název>.webp` | 256 × 256 px, průhledné pozadí |
| Ilustrace průvodce | `res/drawable-nodpi/guide_<název>.webp` | 400 × 400 px, průhledné pozadí |
| Výchozí pozadí úvodního panelu | `res/drawable-nodpi/hero_default.webp` | 1280 × 720 px, motiv vpravo |

Kterou dlaždici karta dostane, určuje `app_api.py` polem `icon`. Existuje-li malba
`tile_<název>`, položí se na papír dlaždice (název je vlevo dole); jinak se vpravo nahoře nakreslí
obrysová ikona `ic_tile_<název>`. Ze zápisu `ikona:barva` (např. `folder:movies`) se bere jen ikona –
barevné odstíny kategorií tento vzhled nemá.

Malby má celá sada: dlaždice kategorií, řad Objevuj a všech 19 filmových žánrů, avatary profilů,
obrázky průvodce a prázdných obrazovek, úvodní panel a kresba papíru na pozadí. Dlaždice bez malby
(nástroje, složky) mají obrysovou ikonu.

Při převodu se motiv dlaždice z podkladu vyřízne a posadí vždy na stejné místo a ve stejné velikosti
(pravá horní část), takže nezáleží na tom, jak velký je na podkladu namalovaný.
Červené plochy maleb se sjednotí na rumělku vzhledu a z obrázku papíru se bere jen kresba vláken –
barvu pozadí určuje vzhled.

Ikonu aplikace, banner a logo skládá `tools/make_brand.py` ze znaku (`znacka/Znak.png`) a názvu:

```bash
python3 android/tools/make_brand.py <podklady>/znacka/Znak.png <Roboto-Light.ttf> "Promítárna"
```

- Ilustrace mají: `live`, `series`, `movies`, `docs`, `news`, `sport`, `kids`, `fun`, `past`,
  `culture`, `advice`, `nature`, `society`, `spirit`, `archive`; dlaždice Objevuj `discover_new`,
  `discover_popular`, `discover_top`, `discover_year`, `discover_genre`, `discover_czechoslovak`
  a žánry `genre_<číslo žánru v TMDB>` (seriálové žánry bez vlastní ilustrace si berou nejbližší
  filmovou, viz `GENRE_ART` v `app_discover.py`).
- Ikony mají: `folder`, `all`, `search`, `sync`, `settings`, `login`, `enrich`, `history`,
  `favorites`, `rating`, `year`, `download`.
- Sekce mají ikony `section_prehrajto`, `section_ivysilani`, `section_tools` (pole `icon` sekce,
  kreslí `SectionHeaderPresenter`).
- Prázdné obrazovky: `nothing` (nic nenalezeno), `favorites` (prázdné Oblíbené; výpis si ji vyžádá
  polem `empty_art`) a `offline` – tu aplikace ukáže, když logika u chyby vrátí `"offline": true`
  (výpadek spojení, ne odpověď serveru).

Podklady (PNG 800 × 450, SVG) převádí do těchto souborů `tools/import_graphics.py`: ilustrace zmenší do WebP,
z obrysových SVG udělá vektorové obrázky Androidu (`rect` a `circle` převede na cesty).

## Sekce hlavní obrazovky

Postranní nabídka má pět sekcí; každá je stránka s vlastními řadami (`home` v `app_api.py` vrací
`sections` → `rows` → karty, v aplikaci `MainFragment` a `SectionFragment`). Po spuštění se otevře
první sekce, ve které něco je (`start`: Oblíbené, jinak Rozkoukané, jinak Objevuj):

- **Oblíbené** – řady Filmy a Seriály; v každé jsou tituly z Přehraj.to i oblíbené pořady z iVysílání
  (ty mají v popisku „iVysílání“).
- **Rozkoukané** – nahoře panel s naposledy rozkoukaným videem, řady Filmy a Seriály (obě služby
  pohromadě, naposledy přehrané první), Další díly a dlaždice Naposledy přehrané. Video se sem zařadí
  po minutě přehrávání (`IN_PROGRESS_MIN` v `db.py`, stejně jako v doplňku pro Kodi); kratší puštění
  je jen v Naposledy přehraných.
- **Objevuj** – velké dlaždice Filmy, Seriály a Hledat a pod nimi řady právě populárních filmů
  a seriálů. Ty mají v datech místo karet `call` a aplikace si je donačte sama až po zobrazení sekce,
  aby hlavní obrazovka nečekala na síť (bez klíče TMDB chybí).
  Dlaždice Filmy a Seriály otevírají vlastní obrazovku (cíl typu `page`, akce `discover_home`,
  `PageActivity`): mřížka velkých dlaždic, tři vedle sebe (řady s příznakem `tiles`, další řada mřížky
  nemá nadpis) – Objevuj filmy (6 dlaždic) a Moje filmy (Stažené filmy, Filmy z mého účtu, Hledat),
  pod ní nově přidané filmy z účtu; u seriálů obdobně.
- **iVysílání** – živé kanály, rozkoukané pořady, Moje pořady a kategorie; nic jiného.
- **Nástroje** – hledání, stahování, synchronizace, metadata, nastavení, předvolby, profily, diagnostika.

Film a seriál z iVysílání se rozlišuje podle druhu pořadu, který si aplikace zapamatuje při jeho
otevření nebo přidání do oblíbených (`ivys_kind:<pořad>`); u neznámého pořadu se v Rozkoukaných
odhadne podle názvů (díl má vedle názvu pořadu i svůj vlastní). Co není film, je v řadě Seriály.

V záhlaví vedle tlačítka hledání je aktivní profil (obrázek a jméno); kliknutím se otevře výběr profilu.

**Objevuj** (`app_discover.py`): Přehraj.to nemá katalog podle roku ani žánru, jen hledání podle názvu
souboru. Seznamy titulů proto dodává TMDB (novinky, populární, nejlépe hodnocené, podle roku, podle
žánru, československé – země původu `CZ|SK|XC`), a teprve u vybraného titulu se na Přehraj.to hledají
jeho verze; u seriálu přes řady a díly. Potřebuje klíč TMDB v Nastavení.

**Jen dostupné tituly** (`_collect`, `_check_available` v `app_discover.py`): výpisy z Objevuj i řady
populárních titulů na hlavní obrazovce ukazují jen to, co je k mání – na Přehraj.to, nebo v iVysílání.
U každého titulu, o kterém to aplikace neví, se jednou hledá na Přehraj.to (u seriálu jeho první díl)
a jednou v iVysílání (shoda názvu, druhu a u filmu roku), čtyři tituly souběžně. Výsledek se pamatuje
(`avail:<druh>:<číslo>`): nalezený titul týden, nenalezený dva dny. Výpis na ověřování nečeká
(`BACKGROUND`, `_verify_later`): tituly, o kterých se zatím nic neví, ukáže hned se štítkem „Ověřuji…“,
ověřuje je vlákno na pozadí (naposledy otevřený výpis má přednost) a odpověď nese `refresh` a `status`
– obrazovka si po pár sekundách řekne znovu, ukazuje „zbývá N“ a nedostupné tituly z ní ubývají na
místě. Protože dostupných titulů bývá na stránce TMDB málo, projde výpis najednou až tři stránky, dokud
jich nemá dvanáct. Když dávka nepřinese nic, aplikace hledá na dalších stránkách sama (nejvýš pětkrát
po sobě). Rozhoduje Přehraj.to: titul, který
se tam hledal a není, se schová, pokud o něm aplikace neví z iVysílání. Titul, u kterého hledání
selhalo (i jen zčásti), ve výpisu zůstane bez štítku a zkusí se příště; služba, která neodpovídá, se
dvě minuty nezkouší. Hledá se i se špatně zadaným heslem (bez přihlášení). Novinky filmů jsou tituly
posledních tří měsíců podle oblíbenosti.
Karty mají štítek „Přehraj.to“ a/nebo „iVysílání“. Ve Filtru jde přepnout Dostupnost na „Všechny
tituly“ – pak se nic neověřuje samo (zůstává dlaždice „Ověřit dostupnost“) a nenalezené mají štítek.
Titul, který je v iVysílání, má na své stránce tlačítko „Přehrát v iVysílání“ (film) nebo „Díly
v iVysílání“ (seriál).

**Oblíbené** jsou jen v aplikaci (`favorites.json`): film či seriál z katalogu, nebo titul z Objevuj.
Mají vlastní sekci v postranní nabídce, filmy a seriály každé svou řadu.
Přidávají se z nabídky karty (podržení OK); pořad z iVysílání kartou „Přidat do oblíbených“ ve výpisu
jeho dílů (ukládá se v tabulce `ivys_favorites` a je vidět i v iVysílání jako Moje pořady).

**Stránka titulu** (`TitleActivity`, data z `discover_title`): údaje z TMDB, tlačítka a řady karet;
pod verzemi řady Celá série, Podobné tituly a Hrají. Hodnocení ČSFD se dotahuje zvlášť (`title_csfd`).
Když hledání na Přehraj.to selže, stránka zůstane použitelná a nabídne Zkusit znovu.
U filmu první tlačítko přehraje nejlepší verzi podle předvoleb a řady nabízejí ostatní verze. Stránka
seriálu je stejná: první tlačítko pustí díl, na kterém se skončilo (rozkoukaný, jinak další po
posledním zhlédnutém, jinak první), a řady jsou díly po řadách (prvních `SEASON_ROWS` řad, další za
kartou řady; u dlouhého seriálu jsou to řady kolem té naposledy sledované). Výběr dílu rovnou hraje
nejlepší verzi (`discover_episode_play`); ruční výběr verze je v nabídce karty dílu. Po návratu
z přehrávače se výběr v řadách přesune na díl, kterým se pokračuje (`focus` v datech stránky). Tlačítko „Jazyk a kvalita“ otevírá předvolby přehrávání.

Tlačítka (`FlowLayout`) se zalamují na další řádek, aby bylo každé vidět celé; popis dostane jen tolik
řádků, kolik nad řadami karet zbývá (`fitPlot`). Nad tlačítky je řádek se souborem, který tlačítko Přehrát
pustí (`detail.file`: název, jak je na Přehraj.to, velikost a délka), a hned za Přehrát tlačítko
„Stáhnout na disk“ s velikostí téhož souboru (`_download_action`; u souboru ve frontě vede na Stahování,
u staženého není a řádek říká „Soubor na disku“).

**Skutečná kvalita** (`_real`, `real_fetch`, `_playing_quality`, `video_size` v `app_api.py`; `_verify_best`
v `app_discover.py`): štítek „HD“ ve výpisu Přehraj.to nese 720p i 1080p, a tak se z něj nepozná, co se
přehraje. Stránka filmu se proto u verze, kterou pustí tlačítko Přehrát, podívá na stránku videa (jeden
dotaz, výsledek se pamatuje) a na tlačítko napíše jazyk a kvalitu, ve které se film opravdu pustí:
„1080p“ = překódovaná verze, „originál 1080p“ / „originál 4K“ = původní soubor (hraje se s účtem). Rozlišení
původního souboru stránka videa neuvádí: bere se nejvyšší překódovaná verze, nebo víc, když to říká název
souboru; nabízí-li stránka jen 720p, napíše se „originál HD“ (původní soubor může být lepší). Po prvním
přehrání to nahradí rozměry obrazu, které nahlásí přehrávač (`onVideoSizeChanged`).
Ostatní verze mají skutečnou kvalitu, jakmile se jednou pustí; do té doby nesou údaj z výpisu. Verze,
o které se jistě ví, že je horší než nejnižší povolená kvalita (bez účtu podle překódovaných verzí, s účtem
až podle změřeného obrazu), vypadne a ověří se další (nejvýš `VERIFY_VERSIONS`).

**Předvolby přehrávání** (dlaždice „Jazyk a kvalita přehrávání“ v Nástrojích, tlačítko na stránce
titulu a Nastavení; cíl typu `choices`, akce `prefs_choices`): jazyk (český dabing / originál s titulky / jedno) a kvalita
(nejvyšší / 1080p / 720p / nejmenší soubor). Řídí pořadí verzí (`_version_rank`) i výběr varianty
souboru při přehrání (`_pick_stream`).

**Čištění verzí** (`_find_versions`): hledá se více variantami názvu a dvě stránky výsledků; soubor
výrazně kratší než film (délka z TMDB) jde do řady „Ukázky a neúplné soubory“, kino kopie a verze,
které se nepodařilo přehrát, až na konec, opakovaně nahraný stejný soubor se slučuje. Verzi, kterou si
uživatel pustil sám, si aplikace u titulu pamatuje a nabídne ji příště první.

**Po dokoukání** (`play_next` v `app_discover.py`, panel v `PlayerActivity`): přehrávač se zeptá, co
dál. U dílu seriálu je to další díl – z vlastního katalogu, jinak vyhledaný na Přehraj.to (u seriálu
z Objevuj podle TMDB, včetně přechodu do další řady) – a po deseti sekundách se spustí sám; odpočet
zastaví přechod na Zavřít. U filmu je to další film stejné série podle TMDB (kolekce, pořadí podle
data uvedení): hraje z katalogu nebo z Přehraj.to, a když tam není, nabídne se stránka titulu. Film se
sám nespouští. Když nic dalšího není nebo to nejde zjistit, přehrávač se zavře jako dřív.
Díl se nikdy nepřeskočí potichu: chybí-li v katalogu bezprostředně následující díl, hledá se nejdřív
na Přehraj.to, a teprve když tam není, nabídne se pozdější díl z katalogu – s poznámkou, který chybí,
a bez samočinného spuštění. Zapamatovaná adresa souboru platí jen pro předvolbu kvality, se kterou
vznikla; po změně předvolby se varianta vybere znovu.
**Nejnižší kvalita** (`min_quality`: bez omezení / HD a vyšší – výchozí / jen Full HD a vyšší / jen 4K;
`_meets_minimum` v `app_api.py`): z Přehraj.to se nabízejí jen verze v aspoň takové kvalitě – na stránce
titulu, v hledání souborů, u dalšího dílu i při stahování řady – a titul, který je jen v nižší kvalitě, se
v Objevuj nepočítá mezi dostupné. Přehraj.to ve výpisu rozlišení neuvádí, jen štítek „HD“, který nese 720p
stejně jako Full HD a 4K. Rozhoduje proto, co je jisté – změřené rozlišení, překódované verze na stránce
videa (viz Skutečná kvalita) a přesný údaj v názvu souboru („1080p“, „4K“) – a teprve potom odhad z velikosti
a délky (HD od 1,5 Mb/s, Full HD od 2 Mb/s; 4K se neodhaduje). Soubor, o kterém se neví nic, se nenabídne.
Vlastní soubory v katalogu a stažené soubory se neskrývají. Mění se v předvolbách „Jazyk a kvalita“ →
Nejnižší kvalita; stránka titulu, který je jen v nižší kvalitě, to sama napíše.
Při předvolbě „nejvyšší dostupná“ má mezi verzemi stejné kvality přednost soubor s vyšším datovým tokem
(větší na minutu) a soubor označený jen „HD“ se od 6 Mb/s řadí mezi Full HD (`_version_rank`).
Změna předvolby jazyka nebo kvality platí hned: obrazovka se načte znovu a verze, které si uživatel
dřív u titulů vybral ručně, přestanou mít přednost (`settings_set` je zapomene).
Řada **Další díly** na hlavní obrazovce: po dokoukaném dílu z katalogu ten následující a seriály
z Objevuj, které se naposledy přehrávaly (vedou na stránku seriálu s tlačítkem Pokračovat). Seriál jde
z řady odebrat („Nenabízet další díly“ v nabídce karty); vrátí se, jakmile se z něj zase něco pustí.
Po třech dílech spuštěných samočinně bez jediného stisku ovladače se přehrávač zeptá „Ještě se
díváte?“ a další díl už sám nepustí.

**Nabídka karty** (podržení OK): u videa z Přehraj.to navíc „Označit jako zhlédnuté / nezhlédnuté“
(`watched_set`) a u rozkoukaného „Odebrat z Rozkoukaných“ (`progress_remove`, i pro iVysílání). Že
nabídka existuje, připomene aplikace při prvních třech spuštěních jednorázovým tipem (`Hints.kt`).

**Úvodní panel a lišta ikon** (`hero` v `app_extras.py`, `HeroPresenter`): sekce s rozkoukaným videem
začíná velkým panelem s tlačítkem Pokračovat; obrázek je z metadat titulu, jinak výchozí ilustrace.
Když se postranní nabídka sbalí, zůstane vlevo úzká lišta ikon sekcí.

**Rozpoznávání názvů a katalog** (sdílené moduly `nameparser.py`, `db.py`, `catalog.py`):
- *Seriály číslované jen číslem* („Ulice 4521“, „Naruto 045“): jeden takový soubor je film („Rocky 2“);
  seriál z nich dělá až skupina aspoň tří souborů se stejným názvem a různým číslem, bez roku v názvu
  a s číslem nebo délkou dílu (`_group_numbered` v `db.py`). K existujícímu seriálu se další přidávají samy.
- *Dva názvy v jednom* („Český / Original (2002)“): katalog vede první, druhý se zkusí při hledání
  plakátu a hodnocení (`alternatives`, `_search_titles`).
- *Slova navíc* („celý film“, „český film“, „Pohádka:“) se z názvu vypouštějí.
- *Dvojdíl* („S01E01-E02“) se eviduje jako oba díly (`episode_end`): po něm následuje až třetí díl.
- Při aktualizaci se katalog přepočítá podle nových pravidel (schéma 10); ručně upravená videa se nemění.

**Oprava přiřazení** (`repair*` v `app_api.py`): v nabídce karty filmu, seriálu nebo dílu je „Opravit
přiřazení“ – výběr správného titulu z TMDB (i pod jiným názvem), změna čísla dílu nebo řady, film místo
dílu a naopak, případně přepis názvu ručně („Pelíšky 1999“, „Ulice 1x4521“). Ruční úprava soubor
zamkne proti přepsání; „Vrátit automatické rozpoznání“ zámek zruší.

**Hledání verzí** (`_find_versions`): když pod prvním názvem není verze podle předvoleb (jazyk, kvalita,
žádná kino kopie), hledá se dál pod dalšími názvy – původní název často najde lepší soubor než český.
Výpis „Filtrovat videa“ z hledání má filtr jazyka a kvality (`search_filters`).
Kvalita se čte z výpisu Přehraj.to i z názvu souboru a rozumí se i slovním označením („4K“, „4K UHD“,
„Full HD“); při volbě „Nejvyšší dostupná“ má 4K přednost před 1080p. Přednost jazyka je ale silnější:
dabovaná verze 1080p se nabídne před 4K v originále.

**Další díl v iVysílání** (`ivys_next`, `_ivys_following` v `app_api.py`): po dokoukání dílu z iVysílání
přehrávač nabídne další díl téhož pořadu – podle pořadí ve výpisu ČT, ať je řazený od nejnovějšího,
nebo od nejstaršího. Sám se nespouští. Výpis pořadu začíná kartou **Pokračovat** (rozkoukaný díl,
jinak první nezhlédnutý po naposledy dokoukaném) a oblíbený pořad má v nabídce karty „Pokračovat
dalším dílem“. U hodně dlouhých pořadů se díl uprostřed výpisu nemusí najít (prochází se nejvýš
`IVYS_NEXT_PAGES` stránek od každého konce) – pak se nenabídne nic.

**Rychlá kontrola účtu** (`quick_sync`): po otevření aplikace (nejvýš jednou za čtvrt hodiny) se na
pozadí načte výpis účtu jen po první stránku bez nového videa, nová videa se přidají a doplní se
k nim metadata. Nic se při tom neodebírá – to dělá jen úplná synchronizace z Nástrojů. Bez přihlášení,
s prázdným katalogem nebo při chybě se nestane nic (chyba je jen v Diagnostice).

**Vzhled titulků** (`subtitle_style` v `app_extras.py`): v nabídce titulků v přehrávači je „Velikost
a pozadí“ – čtyři velikosti a pozadí černé, poloprůhledné, nebo jen obrys. Změna je vidět hned na
titulcích pod oknem a ukládá se k profilu.

**Profily** (`app_extras.py`): každý profil má vlastní Rozkoukané, Oblíbené, Moje pořady z iVysílání
a předvolby přehrávání. Tabulky katalogu se nemění (sdílí je doplněk pro Kodi): osobní stav patří
vždy aktivnímu profilu a při přepnutí se uloží do `profile_<id>.json` a nahradí stavem druhého
profilu. Katalog, stažené soubory a přihlášení jsou společné. Při víc profilech se aplikace po startu
zeptá, kdo se dívá. Osobní jsou i naposledy hledané výrazy. Čí stav v katalogu právě je, si katalog
pamatuje sám (`profile_live`), takže přerušené přepnutí profily nepomíchá.
Přepíná se kliknutím na profil v záhlaví hlavní obrazovky nebo dlaždicí
v Nástrojích; nový profil se po výběru obrázku zeptá na jméno. Jméno se ukládá i potvrzením na
klávesnici televize (cíl typu `input`, `Navigator.askText`).

**Pokračovat na domovské obrazovce televize** (`WatchNext.kt`, akce `watch_next`): rozkoukaná videa
aktivního profilu se zapisují do řady „Pokračovat ve sledování“ Android TV. Odkaz otevírá
`LinkActivity`, která přijme jen přehrání (`play`, `ivys_play`), nic jiného.
Obrázek karty je co nejostřejší (`_art` v `app_extras.py`): obrázek titulu na šířku v šířce 1280 px
z katalogu, u videa puštěného ze stránky titulu z TMDB (titul si aplikace k videu pamatuje, `art:<video>`);
když titul obrázek na šířku nemá, ukáže se jeho plakát na výšku (neořezává se a nezvětšuje). Malý náhled
z Přehraj.to zůstává až jako poslední možnost. Stejný obrázek na šířku má i úvodní panel Rozkoukané.

**Přehrávač**: horní lišta s názvem, časem konce a rychlou volbou zvuku a titulků; volba se pamatuje
pro celý seriál (`track_prefs`, klíč `tracks:<seriál>`) a ukládá se jen to, co uživatel opravdu změnil.
Po chybě uprostřed přehrávání si přehrávač jednou řekne o novou adresu a naváže, kde byl – u souborů
z Přehraj.to i u pořadů a živého vysílání z iVysílání (po dlouhé pauze se živé vysílání vrátí na živo).
U videa rozkoukaného déle než minutu se přehrávač zeptá, jestli navázat, nebo začít znovu (ne
u tlačítka Pokračovat). Ikony ovládání přebíjejí výchozí ikony Media3 (`res/values/player_icons.xml`).

**Diagnostika** (Nástroje, akce `diagnostics`): verze, zařízení, úroveň Widevine, stav přihlášení,
klíče TMDB a iVysílání a posledních pár chyb (`errors.json`; zapisuje je vstupní bod `call` a přehrávač).

**Stažení celé řady**: u seriálu z katalogu dlaždice „Stáhnout N. řadu“ (`download_season`), u řady
z Objevuj „Stáhnout celou řadu“ (`discover_download_season`, jedno hledání na díl). V Nastavení jde
zapnout mazání staženého souboru po dokoukání.

**Hlídání oblíbených** (`favorites_check`): oblíbený film z Objevuj, který na Přehraj.to nemá verzi
v preferovaném jazyce a kvalitě (`meets_preferences`), se znovu vyhledává – po startu aplikace a při
návratech na hlavní obrazovku, nejvýš jednou za 3 hodiny a pár titulů najednou. Když se taková verze
objeví, aplikace ukáže okno s upozorněním a karta dostane štítek „Nově na Přehraj.to“. Na pozadí,
když aplikace neběží, se nehledá.

**Stažené** (`downloads`, volitelně `kind` = `movie` / `episode`): výpis stažených videí s plakáty.
Plakát se bere z metadat katalogu, z titulu, ze kterého se video pouštělo (`choice:`), nebo se
dohledá na TMDB podle názvu a roku; výsledek se u položky zapamatuje (`poster`).

**Stažená verze má přednost**: mezi verzemi titulu je stažený soubor první (štítek „Na disku“)
a tlačítko Přehrát hraje z disku; platí to i tehdy, když hledání na Přehraj.to zrovna nejde.

**Průvodce prvním spuštěním** (`WelcomeActivity`): účet, klíč TMDB a předvolby; nabídne se jen jednou.

**Filtry výpisů**: výpisy z Objevuj i filmy a seriály vlastního katalogu začínají kartou Filtr (cíl
typu `filter`). Aplikace si zavolá `discover_filters` nebo `catalog_filters`, ukáže skupiny (žánr,
období, hodnocení, země, řazení) a po výběru hodnoty načte stejný výpis s parametry, které volba nese.

**Hledání osob**: hledání nabízí i řadu Herci a režiséři (TMDB); karta osoby vede na její filmy
a seriály (`discover_person`) – nejdřív ty, které režírovala. Vlastní katalog se podle herců a režisérů
prohledává také.

**Dostupnost** titulů z Objevuj se neověřuje hromadně sama: dlaždice „Ověřit dostupnost“ projde jednu
stránku seznamu (jedno hledání na film) a výsledek se týden pamatuje jako štítek na kartě.

**Hledání** (`search`): všechny zdroje se ptají najednou. Výsledky jdou v pořadí vlastní katalog,
Filmy a Seriály z TMDB, Herci a režiséři, soubory na Přehraj.to (stejný titul jako jedna karta)
a iVysílání. Titul, který je mezi soubory vrácenými z Přehraj.to, má štítek „Přehraj.to“ a je v řadě
první. Film jde z nabídky karty pustit rovnou („Přehrát hned“). K tomu naposledy hledané výrazy
a hlasové zadání přes systémové rozpoznávání řeči (kde ho zařízení má).

## Chráněné pořady iVysílání

Zahraniční a licencované pořady (i některé české, např. StarDance) vydává ČT jen s ochranou DRM.
Aplikace nejdřív žádá o běžný stream; když ho ČT nevydá, požádá o chráněný (`vod(idec, drm=True)`:
DASH) a přehraje ho vestavěným Widevine zařízení (Media3, `DrmConfiguration`). Adresu licence bere
výhradně z toho, co ČT sama pošle: z odpovědi rozhraní (`drm_license`), nebo z manifestu streamu –
tu si přehrávač najde sám, a když ne, podívá se do manifestu logika (`manifest_protection`,
parametr `inspect`) a přehrání se zkusí ještě jednou. Územní omezení se neobchází a chráněný obsah
se neukládá.

**Stav (zkouška na zařízení v ČR, 0.9.0): chráněné pořady v aplikaci přehrát nejdou.** ČT chráněný
stream vydá, ale adresu licenčního serveru neposílá: odpověď rozhraní má u streamu jen pole
`audioDescriptionType`, `audioDescriptionUrl`, `duration`, `forceSubtitles`, `profileVersion` a `url`
a manifest nese u ochrany jen `cenc(@default_KID)` a `widevine(pssh)`. Zbývala by jen adresa
a přístupový údaj opsané z oficiálního přehrávače, což pravidla projektu vylučují. Při chybě přehrávač ukáže, co
manifest o ochraně říká a která pole odpověď má (jen názvy, žádné hodnoty).

**Předání oficiální aplikaci ČT.** Chráněný pořad, ke kterému ČT neposlala adresu licence, se proto
nepřehrává zde, ale otevře se v oficiální aplikaci iVysílání, je-li v zařízení
(`cz.ceskatelevize.ivysilani.tvapp` pro Android TV, případně `cz.motion.ivysilani`). Logika k odpovědi
`ivys_play` přidá `external` – balíčky aplikace, veřejné adresy dílu a pořadu na webu ČT
(`https://www.ceskatelevize.cz/porady/<pořad>/<díl>/`) a název pořadu. `OfficialApp.kt` zkouší postupně
jen to, k čemu se oficiální aplikace sama hlásí: adresu dílu a pořadu, „přehraj z hledání“ a hledání
podle názvu (`MEDIA_PLAY_FROM_SEARCH`, `ACTION_SEARCH`); když nic z toho neumí, aplikaci aspoň spustí
a napíše, co v ní vyhledat. Bez oficiální aplikace se přehrání zkusí jako dřív a chybová zpráva poradí
ji nainstalovat. Balíčky musí být zapsané i v `AndroidManifest.xml` (`<queries>`).
Co oficiální aplikace v daném zařízení přijímá, ukazuje **Diagnostika** (řádek „Aplikace iVysílání ČT“:
které způsoby předání přijímá a k jakým akcím a adresám se hlásí ve svém manifestu).
Ověřeno jen se zástupnou aplikací v emulátoru: jestli oficiální aplikace odkaz nebo hledání opravdu
dovede až k pořadu, ukáže zkouška na zařízení.

## Stahování na disk

Podržení tlačítka OK (nebo tlačítko Menu) na filmu či dílu z Přehraj.to otevře nabídku se „Stáhnout
na disk“. Stahuje služba v popředí (`DownloadService`), takže pokračuje i po opuštění aplikace, umí
navázat na rozpracovaný soubor a při chybě to zkusí znovu s novou adresou. Evidenci a frontu vede
logika v Pythonu (`downloads.json`; akce `download_*` v `app_api.py`), stažený soubor má při přehrávání
přednost před streamem a hraje i bez internetu. Pořady z iVysílání se nestahují.

Chyba jednoho videa (už není dostupné) označí jen to video. Výpadek připojení, služby nebo přihlášení
se žádného videa netýká: fronta zůstane, jak je, a služba až deset minut čeká na připojení
(`download_next` vrací chybu, ne další položku). Pokus, který kus souboru stáhl, se nepočítá jako
marný. Navazuje se jen na tentýž soubor – jiná celková délka na serveru znamená stahovat znovu.
Stahování, které se zastavilo, naváže samo, jakmile se vrátí připojení nebo disk (dokud aplikace běží;
jinak při jejím příštím spuštění). Evidence má zálohu z poslední změny stavu (`downloads.json.bak`)
a při startu služby se uklidí rozpracované soubory, ke kterým už není záznam (`downloads_cleanup`).

Aplikace zapisuje do vlastní složky na disku (`Android/data/cz.petermikula.mojevidea/files`), k čemuž
nepotřebuje žádné oprávnění; Android tuto složku při odinstalaci aplikace smaže. Úložiště se volí
v Nastavení, bez volby se použije první připojený USB disk. Disk má být ve formátu exFAT – FAT32
neuloží soubor větší než 4 GB (aplikace na to upozorní předem).

**Úložiště a přesun.** Výpis Stahování končí kartami „Kam stahovat“ (výběr disku pro nová stahování,
stejná volba je v Nastavení) a „Přesunout vše na jiný disk“; jednotlivý soubor jde přesunout z nabídky
karty („Přesunout na jiný disk“) a smazat („Smazat stažený soubor“). Aplikace nabídne připojené disky
s volným místem (`planMove` v `Navigator.kt`), přesun zařadí logika (`download_move`, `move_next`,
`move_finish`, `move_fail`) a přenáší ho služba stahování: soubor zkopíruje pod dočasným jménem, ověří
velikost a teprve potom se původní smaže. Během přesunu se hraje z původního místa; nepovedený přesun
nechá soubor, kde byl.

Před stažením celé řady se aplikace zeptá (`confirm` u cíle karty; `space_question` v `app_extras.py`):
ukáže, kolik dílů se stáhne, odhad potřebného místa tam, kde katalog velikosti souborů zná, a volné
místo na disku pro stahování. U řady z Objevuj je velikost známá až po vyhledání dílů – napíše ji
výsledná zpráva.

## Aktualizace

Aplikace se při startu (nejvýš jednou za 6 hodin) zeptá na vydání na GitHubu
(`releases` se značkou `app-v<verze>` a přílohou `.apk`), nabídne novější verzi, stáhne ji a předá
systémovému instalátoru; ruční kontrola je v Nastavení. Poprvé je potřeba aplikaci povolit instalaci
z neznámých zdrojů. Nová verze musí být podepsaná stejným klíčem jako ta nainstalovaná. Stažený soubor
se před instalací porovná s otiskem SHA-256, který GitHub u vydání uvádí. Vydávané sestavení není
laditelné (data aplikace včetně hesla nejdou přečíst přes ADB); pro zkoušky v emulátoru se sestavuje
s `-Pdebuggable`.

Vydání vytváří `.github/workflows/android.yml` při změně na hlavní větvi: spustí testy, sestaví APK,
podepíše ho klíčem ze `signing/` (tajná proměnná `SIGNING_KEYSTORE_BASE64` s úložištěm klíčů v base64
má přednost, je-li nastavená) a založí vydání, pokud pro danou verzi ještě neexistuje.
Nové vydání tedy znamená zvýšit `versionCode` a `versionName` v `app/build.gradle.kts` a přepsat
`RELEASE_NOTES.md` (text se ukáže v nabídce aktualizace).

## Přehrávač a formáty

Přehrávač je Media3/ExoPlayer. Video dekóduje čip zařízení (H.264, HEVC, VP9, AV1 – podle toho, co
zařízení umí), kontejnery MP4, MKV, WebM, TS, AVI a streamy HLS zvládá sám přehrávač. Zvuk, který
zařízení nedekóduje (DTS, TrueHD, někde i AC-3), přehraje softwarově knihovna
[`media3-ffmpeg-decoder`](https://github.com/jellyfin/jellyfin-androidx-media) (GPL-3.0); dekodéry
zařízení mají přednost. Verze knihovny musí odpovídat verzi Media3.

Doplněk je pod GPL-2.0-or-later, sestavená aplikace kvůli této knihovně pod GPL-3.0.

## Co první verze umí a co ne

Umí: přihlášení a synchronizaci s Přehraj.to, katalog filmů a seriálů s plakáty a hodnocením,
Rozkoukané a pokračování od uložené pozice, hledání, iVysílání (živé vysílání s programem, kategorie
a podkategorie, řady a díly, Moje pořady).

Zatím chybí: kolekce, Chci vidět, objevování v TMDB, ruční úpravy katalogu, výběr verze filmu
(vždy se vybere nejlepší: český/slovenský dabing, pak vyšší kvalita) a automatická synchronizace
na pozadí.
