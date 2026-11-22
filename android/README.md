# Moje videa – aplikace pro Android TV

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

Barvy jsou v `res/values/colors.xml` (tmavě modré pozadí, jantarový akcent), motivy obrazovek
v `res/values/styles.xml`. Karty bez obrázku (kategorie, nástroje) kreslí `TileDrawable`, pozadí
obrazovky se mění podle vybrané karty (`Backdrop`).

Grafiku lze vyměnit bez zásahu do kódu – stačí soubor se správným názvem:

| Co | Soubor | Rozměr |
| --- | --- | --- |
| Ikona aplikace | `res/mipmap-xhdpi/ic_launcher.png` | 192 × 192 px (podklad 512 × 512) |
| Banner v nabídce Android TV | `res/drawable-xhdpi/banner.png`, `drawable-xxxhdpi` | 320 × 180 a 640 × 360 px |
| Logo na hlavní obrazovce | `res/drawable-nodpi/logo.png` | průhledné, výška 160 px |
| Pozadí obrazovek | `res/drawable-nodpi/app_background_image.webp` | 1920 × 1080 px |
| Ilustrace dlaždice | `res/drawable-nodpi/tile_<název>.webp` | 480 × 270 px, bez textu, motiv vpravo |
| Ikona dlaždice | `res/drawable/ic_tile_<název>.xml` | obrysová, bílá, výřez 24 × 24 |
| Ikona sekce v postranní nabídce | `res/drawable/ic_section_<název>.xml` | obrysová, bílá, výřez 24 × 24 |
| Ilustrace prázdné obrazovky | `res/drawable-nodpi/empty_<název>.webp` | 480 × 320 px, průhledné pozadí |
| Ikona přehrávače | `res/drawable/ic_player_<název>.xml` | obrysová, bílá, výřez 24 × 24 |
| Avatar profilu | `res/drawable-nodpi/avatar_<název>.webp` | 256 × 256 px, průhledné pozadí |
| Ilustrace průvodce | `res/drawable-nodpi/guide_<název>.webp` | 400 × 400 px, průhledné pozadí |
| Výchozí pozadí úvodního panelu | `res/drawable-nodpi/hero_default.webp` | 1280 × 720 px, motiv vpravo |

Kterou dlaždici karta dostane, určuje `app_api.py` polem `icon`. Existuje-li ilustrace
`tile_<název>`, vyplní dlaždici a název se vykreslí přes její ztmavený okraj; jinak se kreslí tmavě
modrý přechod se zlatou obrysovou ikonou `ic_tile_<název>`. Zápis `ikona:barva` (např.
`folder:movies`) vezme ikonu jedné položky a odstín druhé – tak vypadají podkategorie.

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

Postranní nabídka má tři sekce; každá je stránka s vlastními řadami (`home` v `app_api.py` vrací
`sections` → `rows` → karty, v aplikaci `MainFragment` a `SectionFragment`):

- **Přehraj.to** – Rozkoukané, Další díly, Oblíbené, Objevuj filmy, Objevuj seriály, Stažené na disku
  a Moje videa (to, co je nahrané na účtu).
- **iVysílání** – živé kanály, rozkoukané pořady, Moje pořady a kategorie; nic jiného.
- **Nástroje** – hledání, stahování, synchronizace, metadata, nastavení.

**Objevuj** (`app_discover.py`): Přehraj.to nemá katalog podle roku ani žánru, jen hledání podle názvu
souboru. Seznamy titulů proto dodává TMDB (novinky, populární, nejlépe hodnocené, podle roku, podle
žánru, československé – země původu `CZ|SK|XC`), a teprve u vybraného titulu se na Přehraj.to hledají
jeho verze; u seriálu přes řady a díly. Potřebuje klíč TMDB v Nastavení. Dostupnost se ověřuje až po
otevření titulu, ne u celého seznamu.

**Oblíbené** jsou jen v aplikaci (`favorites.json`): film či seriál z katalogu, nebo titul z Objevuj.
Přidávají se z nabídky karty (podržení OK).

**Stránka titulu** (`TitleActivity`, data z `discover_title`): údaje z TMDB, tlačítka a řady karet;
pod verzemi řady Celá série, Podobné tituly a Hrají. Hodnocení ČSFD se dotahuje zvlášť (`title_csfd`).
Když hledání na Přehraj.to selže, stránka zůstane použitelná a nabídne Zkusit znovu.
U filmu první tlačítko přehraje nejlepší verzi podle předvoleb a řady nabízejí ostatní verze; u seriálu
jsou v řadě jeho série a tlačítko Pokračovat vede do naposledy otevřené.

**Předvolby přehrávání** (Nastavení): jazyk (český dabing / originál s titulky / jedno) a kvalita
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
Řada **Další díly** na hlavní obrazovce: po dokoukaném dílu z katalogu ten následující a seriály
z Objevuj, které se naposledy přehrávaly (vedou na stránku seriálu s tlačítkem Pokračovat).

**Úvodní panel a lišta ikon** (`hero` v `app_extras.py`, `HeroPresenter`): sekce s rozkoukaným videem
začíná velkým panelem s tlačítkem Pokračovat; obrázek je z metadat titulu, jinak výchozí ilustrace.
Když se postranní nabídka sbalí, zůstane vlevo úzká lišta ikon sekcí.

**Profily** (`app_extras.py`): každý profil má vlastní Rozkoukané, Oblíbené, Moje pořady z iVysílání
a předvolby přehrávání. Tabulky katalogu se nemění (sdílí je doplněk pro Kodi): osobní stav patří
vždy aktivnímu profilu a při přepnutí se uloží do `profile_<id>.json` a nahradí stavem druhého
profilu. Katalog, stažené soubory a přihlášení jsou společné. Při víc profilech se aplikace po startu
zeptá, kdo se dívá.

**Pokračovat na domovské obrazovce televize** (`WatchNext.kt`, akce `watch_next`): rozkoukaná videa
aktivního profilu se zapisují do řady „Pokračovat ve sledování“ Android TV. Odkaz otevírá
`LinkActivity`, která přijme jen přehrání (`play`, `ivys_play`), nic jiného.

**Přehrávač**: horní lišta s názvem, časem konce a rychlou volbou zvuku a titulků; volba se pamatuje
pro celý seriál (`track_prefs`, klíč `tracks:<seriál>`) a ukládá se jen to, co uživatel opravdu změnil.
U videa rozkoukaného déle než minutu se přehrávač zeptá, jestli navázat, nebo začít znovu (ne
u tlačítka Pokračovat). Ikony ovládání přebíjejí výchozí ikony Media3 (`res/values/player_icons.xml`).

**Diagnostika** (Nástroje, akce `diagnostics`): verze, zařízení, úroveň Widevine, stav přihlášení,
klíče TMDB a iVysílání a posledních pár chyb (`errors.json`; zapisuje je vstupní bod `call` a přehrávač).

**Stažení celé řady**: u seriálu z katalogu dlaždice „Stáhnout N. řadu“ (`download_season`), u řady
z Objevuj „Stáhnout celou řadu“ (`discover_download_season`, jedno hledání na díl). V Nastavení jde
zapnout mazání staženého souboru po dokoukání.

**Hlídání oblíbených** (`favorites_check`): film z Objevuj, který na Přehraj.to nebyl, se po startu
aplikace občas znovu vyhledá (nejvýš jednou za 12 hodin, pár titulů); když se objeví, karta dostane
štítek „Nově na Přehraj.to“.

**Průvodce prvním spuštěním** (`WelcomeActivity`): účet, klíč TMDB a předvolby; nabídne se jen jednou.

**Filtry výpisů**: výpisy z Objevuj i filmy a seriály vlastního katalogu začínají kartou Filtr (cíl
typu `filter`). Aplikace si zavolá `discover_filters` nebo `catalog_filters`, ukáže skupiny (žánr,
období, hodnocení, země, řazení) a po výběru hodnoty načte stejný výpis s parametry, které volba nese.

**Hledání osob**: hledání nabízí i řadu Herci a režiséři (TMDB); karta osoby vede na její filmy
a seriály (`discover_person`) – nejdřív ty, které režírovala. Vlastní katalog se podle herců a režisérů
prohledává také.

**Dostupnost** titulů z Objevuj se neověřuje hromadně sama: dlaždice „Ověřit dostupnost“ projde jednu
stránku seznamu (jedno hledání na film) a výsledek se týden pamatuje jako štítek na kartě.

**Hledání**: řada titulů z TMDB (plakáty), naposledy hledané výrazy a hlasové zadání přes systémové
rozpoznávání řeči (kde ho zařízení má).

## Chráněné pořady iVysílání

Zahraniční a licencované pořady (i některé české, např. StarDance) vydává ČT jen s ochranou DRM.
Aplikace nejdřív žádá o běžný stream; když ho ČT nevydá, požádá o chráněný (`vod(idec, drm=True)`:
DASH) a přehraje ho vestavěným Widevine zařízení (Media3, `DrmConfiguration`). Adresu licence bere
výhradně z toho, co ČT sama pošle: z odpovědi rozhraní (`drm_license`), nebo z manifestu streamu –
tu si přehrávač najde sám, a když ne, podívá se do manifestu logika (`manifest_protection`,
parametr `inspect`) a přehrání se zkusí ještě jednou. Územní omezení se neobchází a chráněný obsah
se neukládá.

**Stav (zkouška na zařízení v ČR, 0.7.0):** ČT chráněný stream vydá, ale adresu licence v odpovědi
rozhraní ani v manifestu přehrávač nenašel („No license URL“). Pokud ji nenajde ani hledání
v manifestu, zbývala by jen adresa a přístupový údaj opsané z oficiálního přehrávače, což pravidla
projektu vylučují – chráněné pořady pak v aplikaci přehrát nejdou. Při chybě přehrávač ukáže, co
manifest o ochraně říká a která pole odpověď má (jen názvy, žádné hodnoty).

## Stahování na disk

Podržení tlačítka OK (nebo tlačítko Menu) na filmu či dílu z Přehraj.to otevře nabídku se „Stáhnout
na disk“. Stahuje služba v popředí (`DownloadService`), takže pokračuje i po opuštění aplikace, umí
navázat na rozpracovaný soubor a při chybě to zkusí znovu s novou adresou. Evidenci a frontu vede
logika v Pythonu (`downloads.json`; akce `download_*` v `app_api.py`), stažený soubor má při přehrávání
přednost před streamem a hraje i bez internetu. Pořady z iVysílání se nestahují.

Aplikace zapisuje do vlastní složky na disku (`Android/data/cz.petermikula.mojevidea/files`), k čemuž
nepotřebuje žádné oprávnění; Android tuto složku při odinstalaci aplikace smaže. Úložiště se volí
v Nastavení, bez volby se použije první připojený USB disk. Disk má být ve formátu exFAT – FAT32
neuloží soubor větší než 4 GB (aplikace na to upozorní předem).

## Aktualizace

Aplikace se při startu (nejvýš jednou za 6 hodin) zeptá na vydání na GitHubu
(`releases` se značkou `app-v<verze>` a přílohou `.apk`), nabídne novější verzi, stáhne ji a předá
systémovému instalátoru; ruční kontrola je v Nastavení. Poprvé je potřeba aplikaci povolit instalaci
z neznámých zdrojů. Nová verze musí být podepsaná stejným klíčem jako ta nainstalovaná.

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
