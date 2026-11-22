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

Android dovolí nainstalovat novou verzi přes starou jen se stejným podpisem. Vlastní klíč se
předává při sestavení a do repozitáře nepatří:

```bash
gradle assembleDebug -PsigningKeystore=/cesta/klic.keystore -PsigningPassword=… -PsigningAlias=…
```

## Instalace na Android TV

1. V Google Play nainstalujte správce souborů (např. X-plore) nebo aplikaci Downloader.
2. V *Nastavení → Aplikace → Zabezpečení a omezení → Neznámé zdroje* mu povolte instalaci.
3. Otevřete soubor APK z USB disku (nebo ho stáhněte Downloaderem) a potvrďte instalaci.

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
(vždy se vybere nejlepší: český/slovenský dabing, pak vyšší kvalita), automatická synchronizace
na pozadí a upozornění na novou verzi aplikace.
