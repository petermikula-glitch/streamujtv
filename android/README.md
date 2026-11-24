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
- **Nástroje** – návod, stahování, nastavení a přihlášení, jazyk a kvalita, profily, záloha a diagnostika.
  Co má cestu jinde nebo běží samo, tu dlaždici nemá: hledání (lupa je nahoře na každé obrazovce),
  synchronizace s účtem a doplnění hodnocení (běží po otevření aplikace; ručně řádek „Synchronizovat
  s účtem“ v Nastavení, který doplní i hodnocení, u kterých se to dřív nepovedlo).

Film a seriál z iVysílání se rozlišuje podle druhu pořadu, který si aplikace zapamatuje při jeho
otevření nebo přidání do oblíbených (`ivys_kind:<pořad>`); u neznámého pořadu se v Rozkoukaných
odhadne podle názvů (díl má vedle názvu pořadu i svůj vlastní). Co není film, je v řadě Seriály.

V záhlaví vedle tlačítka hledání je aktivní profil (obrázek a jméno); kliknutím se otevře výběr profilu.

**Rychlý návrat do hlavní nabídky** (`Home.kt`): podržení tlačítka Zpět na kterékoli obrazovce nad
hlavní (výpis, stránka, titul, hledání, nastavení, diagnostika) zavře všechny obrazovky nad hlavní
(`FLAG_ACTIVITY_CLEAR_TOP`) a otevře postranní nabídku. Přehrávač a upoutávka to nemají (tam Zpět
zastaví přehrávání). Navigator předává obrazovkám hloubku (`depth`) a od třetí obrazovky nad hlavní
se nejvýš třikrát ukáže tip, že to jde.

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
Když hledání na Přehraj.to selže, stránka zůstane použitelná a nabídne Zkusit znovu. Dočasnou chybu
(omezení počtu dotazů, vypršení času) zkusí hledání po chvíli samo ještě jednou (`_search_page`), hláška
říká důvod a technický detail je v Diagnostice. Hledání, na které někdo čeká, má přednost před ověřováním
dostupnosti na pozadí: to po `SEARCH_HOLD` sekund nezačíná další dávku. Když hledání přesto selže, stránka
ho po chvíli zopakuje sama (nejvýš dvakrát). Hledá se jedním klientem společným pro celou aplikaci
(`_search_client`): přihlášení se ověří jednou, ne před každým hledáním, a když ověření neprojde, hledá se
dočasně bez přihlášení.
U filmu první tlačítko přehraje nejlepší verzi podle předvoleb a řady nabízejí ostatní verze. Stránka
seriálu je stejná: první tlačítko pustí díl, na kterém se skončilo (rozkoukaný, jinak další po
posledním zhlédnutém, jinak první), a řady jsou díly po řadách (prvních `SEASON_ROWS` řad, další za
kartou řady; u dlouhého seriálu jsou to řady kolem té naposledy sledované). Výběr dílu rovnou hraje
nejlepší verzi (`discover_episode_play`); ruční výběr verze je v nabídce karty dílu. Po návratu
z přehrávače se výběr v řadách přesune na díl, kterým se pokračuje (`focus` v datech stránky). Položka „Jazyk a kvalita u tohoto filmu / seriálu“ (pod „Další“) nastavuje jazyk a kvalitu jen pro tento titul.

Tlačítka stránky: Přehrát, Stáhnout na disk, (V iVysílání), Upoutávka, Více o filmu, Do/Z oblíbených
a „Další“ (nabídka `menu`: Hledat ručně, Jazyk a kvalita u tohoto filmu / seriálu). Když není co přehrát z Přehraj.to, nabídka
„Další“ se rozbalí do samostatných tlačítek. „Upoutávka“ (cíl typu `open` s klíčem `youtube`) je trailer
z TMDB (`Tmdb.extras`, nejraději česky nebo slovensky). Přehraje ji aplikace sama (`TrailerActivity`: vložený
přehrávač YouTube ve WebView, hlásí se jménem balíčku aplikace) a po skončení nebo tlačítku Zpět se vrátí na
stránku titulu – z aplikace YouTube se člověk sám nevrátil. Když vložený přehrávač nejde použít (chybí
WebView, video vkládání nedovoluje, do 15 s se nerozběhne), otevře se upoutávka jako dřív běžným odkazem
youtube.com v aplikaci YouTube (zkrácený tvar `vnd.youtube:` aplikace YouTube pro televize nesnese).
„Více o filmu“ (cíl typu `info`) ukáže v okně celý
popis, slogan, původní název, režii, obsazení a zemi (`_about`); textem jde listovat šipkami.
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

**Předvolby přehrávání** (dlaždice „Jazyk a kvalita přehrávání“ v Nástrojích – jediné místo, kde se
nastavují pro celou aplikaci; cíl typu `choices`, akce `prefs_choices`): jazyk (český nebo slovenský dabing – oba si jsou
rovny, rozhoduje mezi nimi kvalita / originál s titulky / jedno) a kvalita
(nejvyšší / 1080p / 720p / nejmenší soubor). Řídí pořadí verzí (`_version_rank`) i výběr varianty
souboru při přehrání (`_pick_stream`).

**Jazyk a kvalita jen pro jeden titul** (stránka titulu → Další; akce `title_prefs_choices` a
`title_prefs_set`): tytéž tři volby (jazyk, kvalita, nejnižší kvalita), ale uložené k titulu
(`tprefs:movie:<číslo TMDB>` / `tprefs:tv:<číslo>` – u seriálu pro všechny díly; patří profilu).
Nastavení aplikace se tím nemění; první možnost každé skupiny „Podle nastavení aplikace“ vlastní
volbu ruší. Dřív tlačítko na stránce titulu otevíralo předvolby celé aplikace, takže volba u jednoho
filmu změnila všechno (a změna nejnižší kvality spustila nové ověřování seznamu). Předvolby titulu
(`_title_state`: nastavení aplikace přebité vlastní volbou) se použijí všude, kde se pracuje s jeho
verzemi: stránka titulu, přímé přehrání filmu a dílu, výběr verze dílu, stažení řady, jiná verze po
chybě (`play_other`), další díl a další film série, hlídání oblíbeného filmu – a při výběru varianty
souboru k přehrání a ke stažení (`_stream_link` pozná titul podle `art:<video>`). Vlastní nejnižší
kvalita se nepropisuje do seznamu dostupných titulů: ten se řídí nastavením aplikace, a tak se
u takového titulu ze stránky nezapisuje. Stránka má u tlačítka Další poznámku „vlastní nastavení“.

**Čištění verzí** (`_find_versions`): hledá se více variantami názvu a dvě stránky výsledků; soubor
výrazně kratší než film (délka z TMDB) jde do řady „Ukázky a neúplné soubory“, kino kopie a verze,
které se nepodařilo přehrát, až na konec, opakovaně nahraný stejný soubor se slučuje. Verzi, kterou si
uživatel pustil sám, si aplikace u titulu pamatuje a nabídne ji příště první.

**Výstup zvuku** (`StereoAudio.kt`, tlačítko „Výstup zvuku“ v dialogu Zvuková stopa, nastavení `audio_output`,
možnosti dodává `subtitle_style` → `audio_outputs`): co se s prostorovým zvukem stane, než odejde do televize
a soundbaru.
- **Prostorový – předat soundbaru** (`auto`, výchozí): Dolby a DTS se pošlou zakódované, dekóduje soundbar.
- **Prostorový – dekóduje aplikace** (`pcm`): ven jde vícekanálový zvuk PCM (5.1 zůstává 5.1;
  `AudioRenderersFactory` staví výstup jen pro PCM, nic se nepředává). Když předaný zvuk hučí, vypadává nebo mlčí.
- **Prostorový s tlumenými basy** (`pcm_soft`): totéž, basový kanál (LFE) hraje asi o 8 dB tišeji
  (`StereoAudio.bassTrim`) – pro filmy, které na soundbaru duní.
- **Stereo** (`stereo`): prostorový zvuk se převede na dva kanály (
  `ChannelMixingAudioProcessor`: střed s řečí do obou stran, zadní tišeji, basový kanál se vynechá).

Přepnutí sestaví přehrávač znovu na stejném místě a zachová zvolenou stopu, titulky i to, jestli se hrálo
nebo stálo; volba platí pro celé zařízení. Tlačítko zvuku je proto vidět i u videa s jedinou stopou.

**Posun zvuku** (tlačítko v okně Výstup zvuku, `audio_delay_set`, nastavení `audio_delays`): soundbar zvuk
zpracovává a ten pak zní později, než se hýbou ústa. Posun po krocích 25 ms (−300 až +600 ms) platí hned,
takže se ladí při řeči na obraze, a pamatuje se pro každý výstup zvuku zvlášť (předaný a dekódovaný zvuk
zpožďuje soundbar různě). Přehrávač se řídí časem zvuku; `ShiftedAudioSink` ten čas posune, a obraz
i titulky se tak o zvolenou dobu pozdrží (kladný posun) nebo předběhnou (záporný).

**Záloha a obnova** (dlaždice v Nástrojích, `app_backup.py`, `Backup.kt`): jeden soubor
`promitarna-zaloha-<datum>.zip` s věrnou kopií katalogu (videa, metadata, historie, zhlédnuto, seznam
dostupných titulů) a se soubory nastavení, oblíbených a profilů. Heslo k Přehraj.to v záloze není (soubor
leží na disku, který jde odnést); při obnově zůstane přihlášení, které v zařízení je. Seznam stažených
souborů se nezálohuje. Obnova nahradí katalog celý (přes zálohovací rozhraní SQLite, starší záloha se hned
převede), přepíše soubory a aplikace začne znovu od hlavní obrazovky; záloha z novější verze aplikace se
odmítne. Kam se zálohuje: bez zvláštního povolení jen do složky aplikace na disku – tu ale Android při
odinstalaci smaže. Aby záloha odinstalaci přežila, nabídne okno „Povolit zálohu, která přežije odinstalaci“:
otevře systémové povolení přístupu k souborům (`MANAGE_EXTERNAL_STORAGE`, uděluje ho uživatel sám) a zálohy
pak jdou do složky `Promitarna` v kořeni disku. Aplikace to povolení k ničemu jinému nepoužívá.

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
**Jen to, co je k mání** (`_collect`, `_check_available`, `_verify_loop` v `app_discover.py`): výpisy Objevuj
ukazují jen tituly, o kterých se už ví, že jsou na Přehraj.to nebo v iVysílání. Neověřený titul se neukáže:
ověří se na pozadí, obrazovka se po `VERIFY_REFRESH` sekundách zeptá znovu a výpis se tak plní postupně
(stavový řádek říká, kolik zbývá). Dál než na stránku TMDB, která se ještě ověřuje, se nejde. Co se ověřit
nepodařilo (služba neodpovídá), se neukáže a výpis to napíše. Ve Filtru jde zapnout Všechny tituly.
Ověřování: u filmu dotazy „název rok“, „název“ a totéž pod původním názvem; když se nenajde nic, ještě
slovenský a další české názvy z TMDB (`Tmdb.local_titles`, i na stránce titulu a při přehrání). Seriál se
ověřuje jako celek (`series=True`: vyhoví kterýkoli jeho díl), díl se hledá jako „S01E05“ i „1x05“.
Dotazy na pozadí drží rozestup (`_pace`: u výpisu, na který se někdo dívá, `PACE_FASTEST`; když se seznam
plní sám, `GENTLE_PACE`) a po odmítnutí zpomalí. Když Přehraj.to dotaz odmítne (HTTP 429), ověřování na
pozadí si dá přestávku (`_rate_limited`: `LIMIT_WAIT`, při opakování pokaždé dvojnásobek až po
`LIMIT_WAIT_MAX`) a po tu dobu mu neposílá nic – dotazy zbydou na hledání, na které někdo čeká (stránka
titulu, přehrání); výpis i Diagnostika napíšou, že Přehraj.to dotazy omezuje a kdy se pokračuje. Výsledek se
pamatuje (`avail:<druh>:<číslo>`): nalezený 14 dní, nenalezený podle stáří titulu – letošní a loňský 12 hodin,
starší pěti let 10 dní, jinak 2 dny (`_missing_ttl`).
Co se jednou zjistilo, zůstává: záznam, kterému vypršela platnost, platí dál a jen se na pozadí ověří znovu
(`stale` v `_known`, `_fresh`). Nalezený titul se tedy ukazuje, dokud nové ověření neřekne, že tam není –
a ani pak hned: jedno hledání se může splést, a tak titul vypadne až po `MISSES_TO_DROP` nepovedených
ověřeních za sebou (`miss` v záznamu, další pokus po `MISS_TTL`; dva neúspěchy krátce po sobě se počítají
jako jeden a „nenašlo se“ do `LIMIT_DISTRUST` po odmítnutí dotazů se nepočítá vůbec). Přeověřování výpis neblokuje ani neukazuje
stav; výpis se jen po `RECHECK_REFRESH` sekundách potichu obnoví. Na výpis čekají jen tituly, o kterých se
neví vůbec nic. Ve výpisu jdou napřed tituly v jazyce podle předvolby (`_preferred_first`): dabované, pak
originály s titulky (`s` v souhrnu), pak ostatní; uvnitř skupiny pořadí TMDB.

**Seznam dostupných titulů** (`app_index.py`): aby se neověřovalo až při otevření výpisu, prochází aplikace
seznamy TMDB předem – populární, novinky, nejlépe hodnocené, československé a jednotlivé žánry, filmy
i seriály (`_plan`). Spouští se po otevření aplikace (`quick_sync` → `app_index.start`), ověřuje šetrně
(dotaz nejvýš jednou za `GENTLE_PACE` sekund; po každém odmítnutí se rozestup prodlouží až na
`GENTLE_SLOWEST`, pamatuje se i do příště a jen pozvolna se vrací), nejvýš `DAILY_LIMIT` titulů denně,
a ustoupí, když někdo čeká na hledání, když se
ověřuje otevřený výpis nebo když se přehrává (`busy`; přehrávač odkládá přes `progress_save`). Kde skončil,
si pamatuje (`index:state`), celý plán opakuje nejdřív po `CYCLE_EVERY`. Výsledek jde do stejných záznamů
`avail:…`, takže projitý výpis je hned. Záznam nese i souhrn verzí (`_summary`: jaký dabing je k mání
a nejlepší známé rozlišení), údaje pro kartu a čas `f`, odkdy je titul na Přehraj.to: podle něj je řada
a výpis „Nově na Přehraj.to“ (`discover_fresh`; titul, který tam dřív nebyl a přibyl, nebo čerstvá novinka).
Karty mají štítky CZ/SK (dabing) a 4K, filtr výpisu umí „jen s dabingem“. Stav je v Diagnostice, i s deníkem
seznamu (`index:journal`, `journal_text`): odkdy se vede, změny nastavení, které se ho týkaly, a nečekaný
úbytek záznamů (`watch`) – aby šlo poznat, proč seznam ubyl nebo začal znovu. Řádek „Seznam dostupných titulů“
říká, kolik titulů je prověřeno, kolik z nich je k mání a kolik se nenašlo; „dnes prověřeno“ počítá jen
tituly opravdu ověřené (ne pokusy, které skončily odmítnutím) a o půlnoci začíná od nuly.
Po aktualizaci, která ověřování mění (`GENERATION`, `_renew`), se celý seznam jednou ověří znovu: nalezené
tituly zůstávají vidět a přeověří se, „nenašlo se“ přestává platit a plán se projde od začátku
(`discover.reverify_all`). Plnění seznamu ustupuje výpisu, na který se někdo dívá, i uprostřed dávky
(`_Yield`), a údaj o iVysílání se doplňuje, aniž by posunul stáří údaje o Přehraj.to (`searched=False`).
Přehraj.to nejde projít celé – nemá výpis všeho, jen hledání –, a tak se prochází z druhé strany: vedle
žebříčků a žánrů i rok po roce (`YEARS_BACK` let zpátky, `YEAR_PAGES` stránek nejoblíbenějších titulů roku)
a česká a slovenská tvorba posledních let zvlášť; nejvýš `DAILY_LIMIT` titulů denně. Co se našlo, je vidět
pohromadě ve výpisu „Vše na Přehraj.to“ (`discover_index`, první dlaždice obrazovek Filmy a Seriály): řazeno
podle jazyka (předvolba: dabované, pak s titulky), pak od nejlepší známé kvality a od lépe hodnocených.
Vedle seznamů TMDB se nejvýš jednou za `HARVEST_EVERY` čte i to, co nabízí samo Přehraj.to (`harvest`;
`PrehrajtoClient.offered`): videa z úvodní stránky a z výpisů, na které odkazuje (odkazy se slovy jako
nejnovější, nejsledovanější, top…). Názvy souborů se rozeberou, přiřadí k titulům TMDB jen při jisté shodě
(stejný název a u filmu i rok, `_match`) a tituly se ověří běžnou cestou – dostanou se tak do výpisů i do
řady „Nově na Přehraj.to“. Podobu těch stránek se nepodařilo ověřit předem (web mimo ČR soubory nevrací):
když na nich videa nejsou, nestane se nic a řádek „Nabídka Přehraj.to“ v Diagnostice řekne, co web nabídl.

**Hodnocení a plakáty** se doplňují samy (`_auto_enrich`, volá se z `quick_sync` po otevření aplikace, i bez
přihlášení k Přehraj.to): nové tituly hned, ty, u kterých doplnění selhalo, znovu po šesti hodinách
a nenalezené po týdnu; jedno kolo nejvýš `AUTO_ENRICH_BATCH` titulů. Když se něco doplní, obrazovka se
načte znovu (`refresh`). Ručně a pro všechny (i ty, u kterých se to dřív nepovedlo) to spustí „Synchronizovat s účtem“ v Nastavení.

**Nejnižší kvalita** (`min_quality`: bez omezení / HD a vyšší – výchozí / jen Full HD a vyšší / jen 4K;
`_meets_minimum` v `app_api.py`): z Přehraj.to se nabízejí jen verze v aspoň takové kvalitě – na stránce
titulu, v hledání souborů, u dalšího dílu i při stahování řady – a titul, který je jen v nižší kvalitě, se
v Objevuj nepočítá mezi dostupné. Přehraj.to ve výpisu rozlišení neuvádí, jen štítek „HD“, který nese 720p
stejně jako Full HD a 4K. Rozhoduje proto, co je jisté – změřené rozlišení, překódované verze na stránce
videa (viz Skutečná kvalita) a přesný údaj v názvu souboru („1080p“, „4K“) – a teprve potom odhad z velikosti
a délky (HD od 1,5 Mb/s, Full HD od 2 Mb/s; 4K se neodhaduje). Soubor, o kterém se neví nic, se nenabídne.
Vlastní soubory v katalogu a stažené soubory se neskrývají. Mění se v předvolbách „Jazyk a kvalita“ →
Nejnižší kvalita; stránka titulu, který je jen v nižší kvalitě, to sama napíše. Změna seznam ověřených
titulů nemaže (`minimum_changed`): nalezené tituly zůstávají a přeověří se na pozadí (plán se projde znovu,
`app_index.restart`), při zpřísnění se hned skryjí ty, jejichž nejlepší známá kvalita nestačí.
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
nabídka existuje, připomene aplikace při prvních třech spuštěních jednorázovým tipem (`Hints.kt`). Zvlášť připomene,
že se karta dá odebrat z Rozkoukaných – ve chvíli, kdy uživatel rozkoukané karty prochází.

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
a předvolby jazyka a kvality přehrávání. Tabulky katalogu se nemění (sdílí je doplněk pro Kodi): osobní stav patří
vždy aktivnímu profilu a při přepnutí se uloží do `profile_<id>.json` a nahradí stavem druhého
profilu. Katalog, stažené soubory a přihlášení jsou společné, stejně jako seznam dostupných titulů
a nejnižší kvalita, podle které se sestavuje (`min_quality` není v `app_extras.SETTINGS`). Dřív ji
měl každý profil svou a přepnutí na profil s jinou hodnotou seznam přepočítalo a začalo znovu ověřovat
(Diagnostika pak v každém profilu ukazovala jiná čísla). Hodnota uložená ve starších souborech
`profile_<id>.json` se při přepnutí nepoužije; platí ta, která byla nastavená při aktualizaci. Při víc profilech se aplikace po startu
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

**Přehrávač**: horní lišta s názvem, časem konce a rychlou volbou zvuku a titulků (vestavěná tlačítka
přehrávače pro totéž dole jsou skrytá, aby nebyla dvakrát); volba se pamatuje
pro celý seriál (`track_prefs`, klíč `tracks:<seriál>`) a ukládá se jen to, co uživatel opravdu změnil.
Po chybě uprostřed přehrávání si přehrávač jednou řekne o novou adresu a naváže, kde byl – u souborů
z Přehraj.to i u pořadů a živého vysílání z iVysílání (po dlouhé pauze se živé vysílání vrátí na živo).
U videa rozkoukaného déle než minutu se přehrávač zeptá, jestli navázat, nebo začít znovu (ne
u tlačítka Pokračovat). Ikony ovládání přebíjejí výchozí ikony Media3 (`res/values/player_icons.xml`).

**Návod** (první dlaždice v Nástrojích, `app_guide.py`, akce `guide`): témata o tom, co aplikace umí a jak
se ovládá (ovládání, hledání, Objevuj, stránka titulu, přehrávač, jazyk a kvalita, oblíbené a rozkoukané,
stahování, iVysílání, účet, profily, záloha a potíže). Každé téma je karta, která otevře text v okně (cíl
`info`). Texty používají názvy dlaždic, voleb a položek nabídek tak, jak je aplikace ukazuje; test
`test_guide_describes_the_app_with_the_names_it_really_uses` selže, když se některý přejmenuje a návod ne.

**Diagnostika** (Nástroje, akce `diagnostics`): verze, zařízení, úroveň Widevine, stav přihlášení,
klíče TMDB a iVysílání a posledních pár chyb (`errors.json`; zapisuje je vstupní bod `call` a přehrávač).
Tři tlačítka: znovu zkontrolovat, zkouška Přehraj.to a hlášení.
- **Zkouška Přehraj.to** (`prehrajto_test` v `app_health.py`): aplikace čte web Přehraj.to, a když web změní
  podobu stránek, hledání přestane fungovat bez varování. Zkouška pustí tři běžná hledání a načte jednu
  stránku videa; řekne, jestli výsledky mají to, co aplikace potřebuje (soubory s délkou a velikostí, verze
  k přehrání), a tím odliší chybu aplikace od změny webu. Během přestávky po HTTP 429 se neprovádí.
- **Hlášení** (`diagnostics_report`): celá diagnostika jako text – do souboru `promitarna-hlaseni-<datum>.txt`
  na disku (stejná složka jako zálohy) a zkráceně bez diakritiky do kódu QR (knihovna ZXing, jen kodér),
  který přečte fotoaparát telefonu. Není pak potřeba fotit obrazovku. Kód je rozepsaný e-mail
  (`mailto:?subject=…&body=…` bez adresáta, nejvýš `QR_LIMIT` znaků i s kódováním %XX): fotoaparát
  nabídne Napsat e-mail a text hlášení jde odeslat nebo zkopírovat. Čistý text fotoaparát iPhonu
  jen ukáže a nedá zkopírovat a v řádku „Prehraj.to: …“ najde odkaz na web (verze 0.28.0).

**Ochrana katalogu** (`app_health.py`): po otevření aplikace se nejvýš jednou za `SNAPSHOT_EVERY` uloží kopie
katalogu do `snapshots/` v datech aplikace (zálohovacím rozhraním SQLite, před uložením se zkontroluje,
drží se poslední `SNAPSHOT_KEEP`). Při startu (`configure` → `guard`) se katalog zkontroluje
(`PRAGMA quick_check`); poškozený se odloží stranou (`catalog-poskozeny.db`) a nahradí poslední dobrou
kopií, a když žádná není, začne se s prázdným – jinak by každá obrazovka skončila chybou databáze.
Co se stalo, je vidět v Diagnostice mezi chybami; řádek „Kopie katalogu“ říká, kolik kopií je a z kdy.

**Jiná verze po chybě přehrávání** (`play_other`, `PlayerActivity`): když se verze titulu z Objevuj
nepodaří přehrát ani s novou adresou (a není to výpadek připojení), přehrávač místo chyby pustí další
nejlepší verzi téhož titulu na stejném místě a oznámí to; nejvýš `OTHER_VERSIONS` krát. Nepovedená verze
se poznačí a příště se nenabídne jako první. U vlastních souborů z katalogu a u iVysílání se nic nemění.

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

**Hledání osob** (`find_people`, `people_rows`, `discover_person` v `app_discover.py`): hledání nabízí řadu
Herci a režiséři z TMDB. Zůstanou v ní jen lidé, jejichž jméno hledanému výrazu opravdu odpovídá (každé
hledané slovo je začátkem některého slova jména, bez ohledu na diakritiku) a o kterých TMDB něco ví (fotka
nebo známé tituly); první je přesná shoda jména, pak podle oblíbenosti, a karta říká, čím je osoba známá.
Když výraz přesně odpovídá jménu, jde osoba napřed a pod ní řada jejích nejznámějších titulů. Karta osoby
vede na filmografii: jen uvedené tituly a skutečné role (bez talk show, zpravodajství a reality show, bez
„sám sebe“ a archivních záběrů, bez hostování v jediném dílu), u režiséra režie první, jinak role první,
v každé skupině od nejznámějšího titulu. Ukazuje jen to, co je k mání na Přehraj.to nebo v iVysílání
(ověřuje se postupně jako výpisy Objevuj); první karta přepíná na celou filmografii. Vlastní katalog se podle herců a režisérů
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

Rozhraní GitHubu omezuje počet dotazů z jedné adresy (60 za hodinu, adresu může sdílet víc domácností)
a pak odpovídá „HTTP 403: rate limit exceeded“. Kontrola nové verze proto má zálohu (`_update_from_page`):
stránka `…/releases/latest` téhož repozitáře přesměruje na značku nejnovějšího vydání, adresa souboru APK
se složí z verze a popis změn se vezme z `RELEASE_NOTES.md` u té značky. Otisk souboru tudy zjistit nejde;
pravost aktualizace hlídá Android při instalaci (stejný podpisový klíč).

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

## Odložené návrhy

Domluvené s vlastníkem, zatím neudělané (pořadí není závazné):

- **Méně dotazů na jeden titul při ověřování.** Titul, který na Přehraj.to není, stojí až šest hledání
  (`_count_versions`: tři pod českým a původním názvem, tři pod dalšími názvy z TMDB). Zkrátit na dvě až
  tři podle toho, co ukáže Diagnostika o odmítaných dotazech (HTTP 429). Spolu s tím tři drobnosti
  z kontroly kódu: časovače přehrávače běží i při pauze na pozadí, stránka titulu se krátce načítá ve
  špatném rozložení, a titul, jehož hledání pokaždé skončí chybou, dokáže zadržet plnění seznamu na
  jedné stránce plánu.
- **Zkušební kanál aktualizací.** Každé vydání jde dnes rovnou do televize. Možnost označit vydání jako
  zkušební a instalovat ho jen na vyžádání; vyžaduje úpravu `.github/workflows/` (souhlas vlastníka).
