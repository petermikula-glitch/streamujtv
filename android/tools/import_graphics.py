#!/usr/bin/env python3
"""Převede podklady grafiky do zdrojů aplikace.

    python3 android/tools/import_graphics.py <složka s podklady> [android/app/src/main/res]

Složka s podklady: ``dlazdice/``, ``objevuj/`` a ``zanry/`` (PNG na šířku, bez textu), ``ikony/*.svg``
(obrysové, výřez 24 × 24, skupina s ``fill="none"`` a bílou linkou), ``profily/``, ``pruvodce/``,
``prazdne-stavy/``, ``uvod/`` a ``znacka/`` (Ikona-aplikace.png, Banner-Android-TV.png, Logo.png,
Pozadi.png nebo Papir.png). Chybějící soubory se přeskočí. Potřebuje knihovnu Pillow.

Malby jsou tuší na čistě bílém pozadí (vzhled „tuš na papíře“): bílá se při převodu odstraní, takže
motiv v aplikaci leží na barvě papíru. Obrázek, který už průhledné pozadí má, se nemění.
"""
import os
import sys
import xml.etree.ElementTree as ET

from PIL import Image

TILES = {"Archiv": "archive", "Dokumenty": "docs", "Filmy": "movies", "Historie": "past", "Kultura": "culture",
         "Priroda": "nature", "Pro-deti": "kids", "Rady-a-recepty": "advice", "Serialy": "series",
         "Spiritualita": "spirit", "Spolecnost": "society", "Sport": "sport", "Zabava": "fun",
         "Zive-vysilani": "live", "Zpravodajstvi": "news"}
# Dlaždice řad „Objevuj“ a žánrů; žánry nesou číslo žánru v TMDB (seriálové žánry bez vlastní
# ilustrace přiřazuje app_discover.GENRE_ART k nejbližší filmové).
DISCOVER = {"Novinky": "discover_new", "Popularni": "discover_popular", "Nejlepe-hodnocene": "discover_top",
            "Podle-roku": "discover_year", "Podle-zanru": "discover_genre", "Ceskoslovenske": "discover_czechoslovak"}
GENRES = {"Akcni": 28, "Dobrodruzny": 12, "Animovany": 16, "Komedie": 35, "Krimi": 80, "Dokumentarni": 99,
          "Drama": 18, "Rodinny": 10751, "Fantasy": 14, "Historicky": 36, "Horor": 27, "Hudebni": 10402,
          "Mysteriozni": 9648, "Romanticky": 10749, "Sci-fi": 878, "Televizni-film": 10770, "Thriller": 53,
          "Valecny": 10752, "Western": 37}
EMPTY = {"Bez-pripojeni": "empty_offline", "Nic-nenalezeno": "empty_nothing", "Prazdne-oblibene": "empty_favorites"}
SECTIONS = {"Prehraj-to": "ic_section_prehrajto", "iVysilani": "ic_section_ivysilani", "Nastroje": "ic_section_tools"}
ICONS = {"Doplnit-plakaty": "enrich", "Hledat": "search", "Moje-porady": "favorites", "Naposledy-prehrane": "history",
         "Nastaveni": "settings", "Podle-hodnoceni": "rating", "Podle-roku": "year", "Prihlaseni": "login",
         "Slozka": "folder", "Synchronizovat": "sync", "Vsechny-porady": "all", "Stahovani": "download"}
PLAYER = {"Prehrat": "play", "Pauza": "pause", "Zpet-10": "rewind", "Vpred-10": "forward", "Predchozi": "previous",
          "Dalsi-dil": "next", "Titulky": "subtitles", "Zvuk": "audio", "Kvalita": "quality",
          "Od-zacatku": "restart", "Zavrit": "close"}
PROFILES = {"Profil-klapka": "clapper", "Profil-popcorn": "popcorn", "Profil-koruna": "crown", "Profil-sova": "owl",
            "Profil-kocka": "cat", "Profil-raketa": "rocket"}
GUIDE = {"Ucet": "account", "Katalog": "catalog", "Predvolby": "prefs"}
SVG = "{http://www.w3.org/2000/svg}"


SURFACE = (0xE3, 0xDB, 0xC8)   # barva listu papíru, na kterém leží dlaždice a úvodní panel
WHITE = 248                    # od této světlosti se bod bere jako čistě bílý podklad (generované obrázky mívají 253–254)


def ink(image):
    """Tuš na bílém pozadí → tuš na průhledném: čím je bod tmavší, tím je krytější, a barva (černá,
    rumělka) se zachová. Bílá zmizí úplně; šedé lavírování zůstane jako poloprůhledná čerň."""
    if image.mode == "RGBA" and image.getextrema()[3][0] < 250:
        return image   # průhledné pozadí už má
    # „skoro bílá“ podkladu by jinak zůstala jako sotva viditelný závoj přes celou dlaždici
    plain = image.convert("RGB").point([min(255, value * 255 // WHITE) for value in range(256)] * 3)
    source = plain.tobytes()
    points = bytearray(len(source) // 3 * 4)
    for index in range(0, len(source), 3):
        red, green, blue = source[index], source[index + 1], source[index + 2]
        white = min(red, green, blue)        # kolik je v bodě bílého podkladu
        cover = 255 - white
        if cover:                            # barva bez podkladu: (c - bílá) / krytí
            target = index // 3 * 4
            points[target] = (red - white) * 255 // cover
            points[target + 1] = (green - white) * 255 // cover
            points[target + 2] = (blue - white) * 255 // cover
            points[target + 3] = cover
    return Image.frombytes("RGBA", plain.size, bytes(points))


def wide(image, size=(480, 270)):
    """Výřez na poměr stran cíle (ze středu) a zmenšení."""
    width, height = image.size
    target = size[0] / float(size[1])
    if width / float(height) > target:
        crop = int(height * target)
        image = image.crop(((width - crop) // 2, 0, (width - crop) // 2 + crop, height))
    else:
        crop = int(width / target)
        image = image.crop((0, (height - crop) // 2, width, (height - crop) // 2 + crop))
    return image.resize(size, Image.LANCZOS)


def motif(image, size=(480, 270), box=(0.50, 0.08, 0.95, 0.72)):
    """Motiv dlaždice se z podkladu vyřízne a posadí do pravé horní části (``box`` v podílech dlaždice):
    všechny motivy jsou pak stejně velké a pod nimi i vlevo zůstává místo na název, ať je na podkladu
    namalovaný jakkoli velký. Obrázek s průhledným pozadím se jen ořízne a zmenší."""
    if image.mode == "RGBA" and image.getextrema()[3][0] < 250:
        return wide(image, size)
    plain = image.convert("RGB")
    bounds = plain.convert("L").point([255 if value < WHITE - 3 else 0 for value in range(256)]).getbbox()
    if not bounds:
        return wide(image, size)
    margin = max(plain.size) // 60     # ať se neusekne slabé lavírování na okraji motivu
    cut = plain.crop((max(0, bounds[0] - margin), max(0, bounds[1] - margin),
                      min(plain.width, bounds[2] + margin), min(plain.height, bounds[3] + margin)))
    room = ((box[2] - box[0]) * size[0], (box[3] - box[1]) * size[1])
    scale = min(room[0] / cut.width, room[1] / cut.height)
    cut = cut.resize((max(1, int(cut.width * scale)), max(1, int(cut.height * scale))), Image.LANCZOS)
    sheet = Image.new("RGB", size, (255, 255, 255))
    sheet.paste(cut, (int(box[2] * size[0]) - cut.width, int(box[1] * size[1] + (room[1] - cut.height) / 2)))
    return sheet


def number(value):
    return ("%.3f" % float(value)).rstrip("0").rstrip(".")


def rect_path(element):
    x, y, w, h = (float(element.get(key, 0)) for key in ("x", "y", "width", "height"))
    r = float(element.get("rx", 0))
    if r <= 0:
        return "M%s %sh%sv%sh%sZ" % (number(x), number(y), number(w), number(h), number(-w))
    n = number
    return ("M%s %sh%sa%s %s 0 0 1 %s %sv%sa%s %s 0 0 1 %s %sh%sa%s %s 0 0 1 %s %sv%sa%s %s 0 0 1 %s %sZ" % (
        n(x + r), n(y), n(w - 2 * r), n(r), n(r), n(r), n(r), n(h - 2 * r), n(r), n(r), n(-r), n(r),
        n(-(w - 2 * r)), n(r), n(r), n(-r), n(-r), n(-(h - 2 * r)), n(r), n(r), n(r), n(-r)))


def circle_path(element):
    cx, cy, r = (float(element.get(key)) for key in ("cx", "cy", "r"))
    return "M%s %sa%s %s 0 1 0 %s 0a%s %s 0 1 0 %s 0Z" % (
        number(cx - r), number(cy), number(r), number(r), number(2 * r), number(r), number(r), number(-2 * r))


def vector(paths, width):
    body = "".join('''    <path
        android:pathData="%s"
        android:strokeColor="#FFFFFFFF"
        android:strokeLineCap="round"
        android:strokeLineJoin="round"
        android:strokeWidth="%s" />
''' % (data, width) for data in paths)
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
            '    android:width="24dp"\n    android:height="24dp"\n'
            '    android:viewportWidth="24"\n    android:viewportHeight="24">\n' + body + '</vector>\n')


def icon(source, target):
    root = ET.parse(source).getroot()
    group = root.find(SVG + "g")
    if group is None:  # vlastnosti tahu přímo na kořeni, tvary hned pod ním
        group = root
    if root.get("viewBox") != "0 0 24 24" or group.get("fill") != "none":
        raise SystemExit("%s: čekám obrysovou ikonu ve výřezu 24 × 24" % source)
    paths = []
    for element in group:
        kind = element.tag.replace(SVG, "")
        if kind == "path":
            paths.append(element.get("d"))
        elif kind == "rect":
            paths.append(rect_path(element))
        elif kind == "circle":
            paths.append(circle_path(element))
        else:
            raise SystemExit("%s: neznámý prvek <%s>" % (source, kind))
    with open(target, "w", encoding="utf-8") as output:
        output.write(vector(paths, group.get("stroke-width") or "1.7"))


def main(source, res):
    done = []
    for folder in ("drawable", "drawable-nodpi", "drawable-xhdpi", "drawable-xxxhdpi", "mipmap-xhdpi"):
        os.makedirs(os.path.join(res, folder), exist_ok=True)
    for name, key in TILES.items():
        path = os.path.join(source, "dlazdice", name + ".png")
        if os.path.exists(path):
            image = ink(motif(Image.open(path)))
            image.save(os.path.join(res, "drawable-nodpi", "tile_%s.webp" % key), "WEBP", lossless=True, method=6)
            done.append("tile_" + key)
    for folder, names in (("objevuj", DISCOVER), ("zanry", {name: "genre_%d" % number for name, number in GENRES.items()})):
        for name, key in names.items():
            path = os.path.join(source, folder, name + ".png")
            if os.path.exists(path):
                image = ink(motif(Image.open(path)))
                image.save(os.path.join(res, "drawable-nodpi", "tile_%s.webp" % key), "WEBP", lossless=True, method=6)
                done.append("tile_" + key)
    for name, key in EMPTY.items():
        path = os.path.join(source, "prazdne-stavy", name + ".png")
        if os.path.exists(path):
            source_image = Image.open(path)
            square = source_image.width == source_image.height
            image = ink(source_image.resize((360, 360) if square else (480, 320), Image.LANCZOS))
            image.save(os.path.join(res, "drawable-nodpi", key + ".webp"), "WEBP", lossless=True, method=6)
            done.append(key)
    for name, key in ICONS.items():
        path = os.path.join(source, "ikony", name + ".svg")
        if os.path.exists(path):
            icon(path, os.path.join(res, "drawable", "ic_tile_%s.xml" % key))
            done.append("ic_tile_" + key)
    for name, key in SECTIONS.items():
        path = os.path.join(source, "ikony", name + ".svg")
        if os.path.exists(path):
            icon(path, os.path.join(res, "drawable", key + ".xml"))
            done.append(key)
    for name, key in PLAYER.items():
        path = os.path.join(source, "prehravac", name + ".svg")
        if os.path.exists(path):
            icon(path, os.path.join(res, "drawable", "ic_player_%s.xml" % key))
            done.append("ic_player_" + key)
    for folder, names, prefix, size in (("profily", PROFILES, "avatar_", 256), ("pruvodce", GUIDE, "guide_", 400)):
        for name, key in names.items():
            path = os.path.join(source, folder, name + ".png")
            if os.path.exists(path):
                image = ink(Image.open(path).resize((size, size), Image.LANCZOS))
                image.save(os.path.join(res, "drawable-nodpi", prefix + key + ".webp"), "WEBP", lossless=True, method=6)
                done.append(prefix + key)
    hero = os.path.join(source, "uvod", "Uvodni-panel.png")
    if os.path.exists(hero):
        # úvodní panel je neprůhledný: malba se položí na barvu listu papíru
        painted = ink(wide(Image.open(hero), (1280, 720)))
        sheet = Image.new("RGB", painted.size, SURFACE)
        sheet.paste(painted, (0, 0), painted)
        sheet.save(os.path.join(res, "drawable-nodpi", "hero_default.webp"), "WEBP", quality=90, method=6)
        done.append("hero_default")
    brand = os.path.join(source, "znacka")
    paper = next((os.path.join(brand, name) for name in ("Papir.png", "Pozadi.png")
                  if os.path.exists(os.path.join(brand, name))), None)
    if paper:
        wide(Image.open(paper).convert("RGB"), (1920, 1080)).save(
            os.path.join(res, "drawable-nodpi", "app_background_image.webp"), "WEBP", quality=92, method=6)
        done.append("pozadí")
    if os.path.exists(os.path.join(brand, "Banner-Android-TV.png")):
        banner = Image.open(os.path.join(brand, "Banner-Android-TV.png")).convert("RGB")
        banner.resize((320, 180), Image.LANCZOS).save(os.path.join(res, "drawable-xhdpi", "banner.png"), optimize=True)
        banner.resize((640, 360), Image.LANCZOS).save(os.path.join(res, "drawable-xxxhdpi", "banner.png"), optimize=True)
        done.append("banner")
    if os.path.exists(os.path.join(brand, "Ikona-aplikace.png")):
        Image.open(os.path.join(brand, "Ikona-aplikace.png")).convert("RGB").resize((192, 192), Image.LANCZOS).save(
            os.path.join(res, "mipmap-xhdpi", "ic_launcher.png"), optimize=True)
        done.append("ikona")
    if os.path.exists(os.path.join(brand, "Logo.png")):
        Image.open(os.path.join(brand, "Logo.png")).save(os.path.join(res, "drawable-nodpi", "logo.png"), optimize=True)
        done.append("logo")
    print("Převedeno: %s" % ", ".join(done))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", "app", "src", "main", "res"))
