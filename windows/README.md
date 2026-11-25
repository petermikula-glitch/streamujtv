# Promítárna pro Windows (přenosná verze)

Stejná aplikace jako na Android TV, jen v okně na počítači: Oblíbené, Rozkoukané, Objevuj se seznamem
filmů a seriálů na Přehraj.to, iVysílání, hledání, stránky titulů s verzemi, jazyk a kvalita (i jen pro
jeden titul), profily, stahování na disk, záloha a diagnostika.

**Bez instalace:** zip z vydání `win-v<verze>` rozbalit a spustit `Promitarna.exe`. Data (katalog,
nastavení, profily, stažená videa, zálohy) jsou ve složce `data` vedle programu, takže celou složku jde
přenést jinam (i na flashku). Do systému se nic nezapisuje.

## Jak je to udělané

| Část | Soubor | Co dělá |
| --- | --- | --- |
| Logika | `android/app/src/main/python/` a sdílené moduly doplňku pro Kodi | **stejné soubory jako na televizi** (při sestavení se zkopírují do `build/logic`) |
| Server okna | `promitarna_win/server.py` | jen na `127.0.0.1`, s tajným klíčem v adrese; `POST /api/<akce>` = `app_api.call` |
| Okno | `ui/index.html`, `ui/app.js`, `ui/style.css` | vykresluje obrazovky podle dat z logiky – cíle karet (`target.type`) jako `Navigator.kt` |
| Ikony a malby | `build_assets.py` | vektorové ikony Androidu → SVG, malby `drawable-nodpi` beze změny, ikona programu ze znaku loga (do `ui/assets`, negeneruje se do gitu) |
| Přehrávání | `promitarna_win/player.py` | přibalený **mpv**; pozice se ukládá do logiky (`progress_save`), po dokoukání `play_next` |
| Stahování | `promitarna_win/downloads.py` | totéž co `DownloadService` na televizi (navazování `.part`, titulky) |
| Plnění na pozadí | `promitarna_win/background.py` | totéž co `IndexService` na televizi: každých 5 minut a po změně nastavení `index_keepalive`; počítač mezitím neusne (nejvýš 10 h) |
| Spuštění | `promitarna_win/app.py`, `Promitarna.py` | okno přes pywebview (WebView2), jinak Edge v režimu aplikace; po zavření okna skončí celý proces i s mpv a Edge (nic nedrží soubory) |
| Sestavení | `build.py`, workflow `windows.yml` | PyInstaller + mpv → `dist/Promitarna-<verze>-windows.zip` |

Zvuk v mpv podle nastavení z televize (`subtitle_style`): výstup „předat soundbaru“ = `--audio-spdif`
(passthrough Dolby/DTS přes HDMI), „dekóduje aplikace“ = dekóduje mpv, „s tlumenými basy“ = filtr
`lowshelf`, „Stereo“ = `--audio-channels=stereo`; posun zvuku = `--audio-delay`; noční režim =
kompresor `acompressor` (stejné hodnoty jako `NightAudioProcessor`).

Hlavní nabídka je stále nahoře (sekce vedle sebe), obsah má celou šířku okna; dlaždice bez plakátu
(kategorie, nástroje) jsou na šířku přes celé okno. Každé heslo nabídky má rozbalovací podnabídku
(řady dané sekce s jejich položkami): myší najetím, klávesnicí šipkou dolů. Na hlubší obrazovce je
vlevo nahoře tlačítko Zpět.

Ovládání: myš (boční tlačítko Zpět funguje), nebo klávesnice – šipky, Enter, Esc nebo Backspace (zpět), podržený Esc (hlavní nabídka), klávesa Menu,
`m` nebo pravé tlačítko myši (nabídka karty), `/` (hledat). V mpv: mezerník, šipky, `#` zvuková stopa,
`j` titulky, `f` celá obrazovka, `q` konec.

## Co tu není (oproti televizi)

- **Chráněné pořady iVysílání (DRM)** – podle zásad projektu se přehrávají jen vestavěným Widevine
  zařízení s Android TV; na počítači se pouštějí na webu iVysílání.
- Řada „Pokračovat ve sledování“ na domovské obrazovce a automatické aktualizace (nová verze: stáhnout
  nový zip, složku `data` ponechat).
- Výběr disku a přesun stažených souborů: složka pro stahování se zadává v Nastavení.

## Zkoušení a sestavení

```bash
python3 windows/build_assets.py                      # ikony a malby pro okno
PROMITARNA_DATA=/tmp/promitarna python3 windows/Promitarna.py   # spuštění ze zdrojů (i v Linuxu)
python3 -m pytest -q -p no:cacheprovider tests/test_windows.py
python windows/build.py C:\cesta\k\mpv                 # ve Windows: zip do windows/dist
```

Vydání: zvýšit `VERSION` v `promitarna_win/__init__.py`, popsat změny v `RELEASE_NOTES.md`; po sloučení
do `main` workflow sestaví zip a vydá ho jako `win-v<verze>` (vydání se neoznačí jako „nejnovější“ –
podle nejnovějšího vydání hledá aktualizace aplikace pro Android TV).
