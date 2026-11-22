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

Kterou dlaždici karta dostane, určuje `app_api.py` polem `icon`. Existuje-li ilustrace
`tile_<název>`, vyplní dlaždici a název se vykreslí přes její ztmavený okraj; jinak se kreslí tmavě
modrý přechod se zlatou obrysovou ikonou `ic_tile_<název>`. Zápis `ikona:barva` (např.
`folder:movies`) vezme ikonu jedné položky a odstín druhé – tak vypadají podkategorie.

- Ilustrace mají: `live`, `series`, `movies`, `docs`, `news`, `sport`, `kids`, `fun`, `past`,
  `culture`, `advice`, `nature`, `society`, `spirit`, `archive`.
- Ikony mají: `folder`, `all`, `search`, `sync`, `settings`, `login`, `enrich`, `history`,
  `favorites`, `rating`, `year`, `download`.

Podklady (PNG 800 × 450, SVG) převádí do těchto souborů `tools/import_graphics.py`: ilustrace zmenší do WebP,
z obrysových SVG udělá vektorové obrázky Androidu (`rect` a `circle` převede na cesty).

## Sekce hlavní obrazovky

Postranní nabídka má tři sekce; každá je stránka s vlastními řadami (`home` v `app_api.py` vrací
`sections` → `rows` → karty, v aplikaci `MainFragment` a `SectionFragment`):

- **Přehraj.to** – Rozkoukané, Oblíbené, Objevuj filmy, Objevuj seriály, Stažené na disku a Moje videa
  (to, co je nahrané na účtu).
- **iVysílání** – živé kanály, rozkoukané pořady, Moje pořady a kategorie; nic jiného.
- **Nástroje** – hledání, stahování, synchronizace, metadata, nastavení.

**Objevuj** (`app_discover.py`): Přehraj.to nemá katalog podle roku ani žánru, jen hledání podle názvu
souboru. Seznamy titulů proto dodává TMDB (novinky, populární, nejlépe hodnocené, podle roku, podle
žánru, československé – země původu `CZ|SK|XC`), a teprve u vybraného titulu se na Přehraj.to hledají
jeho verze; u seriálu přes řady a díly. Potřebuje klíč TMDB v Nastavení. Dostupnost se ověřuje až po
otevření titulu, ne u celého seznamu.

**Oblíbené** jsou jen v aplikaci (`favorites.json`): film či seriál z katalogu, nebo titul z Objevuj.
Přidávají se z nabídky karty (podržení OK).

**Stránka titulu** (`TitleActivity`, data z `discover_title`): údaje z TMDB, tlačítka a řady karet.
U filmu první tlačítko přehraje nejlepší verzi podle předvoleb a řady nabízejí ostatní verze; u seriálu
jsou v řadě jeho série a tlačítko Pokračovat vede do naposledy otevřené.

**Předvolby přehrávání** (Nastavení): jazyk (český dabing / originál s titulky / jedno) a kvalita
(nejvyšší / 1080p / 720p / nejmenší soubor). Řídí pořadí verzí (`_version_rank`) i výběr varianty
souboru při přehrání (`_pick_stream`).

**Čištění verzí** (`_find_versions`): hledá se více variantami názvu a dvě stránky výsledků; soubor
výrazně kratší než film (délka z TMDB) jde do řady „Ukázky a neúplné soubory“, kino kopie a verze,
které se nepodařilo přehrát, až na konec, opakovaně nahraný stejný soubor se slučuje. Verzi, kterou si
uživatel pustil sám, si aplikace u titulu pamatuje a nabídne ji příště první.

**Dostupnost** titulů z Objevuj se neověřuje hromadně sama: dlaždice „Ověřit dostupnost“ projde jednu
stránku seznamu (jedno hledání na film) a výsledek se týden pamatuje jako štítek na kartě.

**Hledání**: řada titulů z TMDB (plakáty), naposledy hledané výrazy a hlasové zadání přes systémové
rozpoznávání řeči (kde ho zařízení má).

## Chráněné pořady iVysílání

Zahraniční pořady vydává ČT jen s ochranou DRM. Aplikace nejdřív žádá o běžný stream; když ho ČT
nevydá, požádá o chráněný (`vod(idec, drm=True)`: DASH) a přehraje ho vestavěným Widevine zařízení
(Media3, `DrmConfiguration`). Adresu licence bere výhradně z odpovědi rozhraní (`drm_license`);
když v ní není, přehrávač při chybě ukáže názvy polí odpovědi, aby šlo podporu doladit. Územní
omezení se neobchází a chráněný obsah se neukládá. **Tvar odpovědi u chráněných pořadů nebylo možné
ověřit mimo Česko** – rozhoduje zkouška na zařízení v ČR.

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
