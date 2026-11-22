#!/usr/bin/env python3
"""Převede podklady grafiky do zdrojů aplikace.

    python3 android/tools/import_graphics.py <složka s podklady> [android/app/src/main/res]

Složka s podklady: ``dlazdice/``, ``objevuj/`` a ``zanry/`` (PNG 800 × 450, bez textu), ``ikony/*.svg``
(obrysové, výřez 24 × 24, skupina s ``fill="none"`` a bílou linkou), ``prazdne-stavy/`` (PNG s průhledným
pozadím) a ``znacka/`` (Ikona-aplikace.png, Banner-Android-TV.png, Logo.png, Pozadi.png). Chybějící
soubory se přeskočí. Potřebuje knihovnu Pillow.
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
SVG = "{http://www.w3.org/2000/svg}"


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
    if root.get("viewBox") != "0 0 24 24" or group is None or group.get("fill") != "none":
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
            image = Image.open(path).convert("RGB").resize((480, 270), Image.LANCZOS)
            image.save(os.path.join(res, "drawable-nodpi", "tile_%s.webp" % key), "WEBP", quality=90, method=6)
            done.append("tile_" + key)
    for folder, names in (("objevuj", DISCOVER), ("zanry", {name: "genre_%d" % number for name, number in GENRES.items()})):
        for name, key in names.items():
            path = os.path.join(source, folder, name + ".png")
            if os.path.exists(path):
                image = Image.open(path).convert("RGB").resize((480, 270), Image.LANCZOS)
                image.save(os.path.join(res, "drawable-nodpi", "tile_%s.webp" % key), "WEBP", quality=90, method=6)
                done.append("tile_" + key)
    for name, key in EMPTY.items():
        path = os.path.join(source, "prazdne-stavy", name + ".png")
        if os.path.exists(path):
            image = Image.open(path).convert("RGBA").resize((480, 320), Image.LANCZOS)
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
    brand = os.path.join(source, "znacka")
    if os.path.exists(os.path.join(brand, "Pozadi.png")):
        Image.open(os.path.join(brand, "Pozadi.png")).convert("RGB").save(
            os.path.join(res, "drawable-nodpi", "app_background_image.webp"), "WEBP", lossless=True, method=6)
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
