# -*- coding: utf-8 -*-
"""Změří rychlost obrazovek doplňku Přehraj.to nad syntetickým katalogem (bez Kodi a bez sítě).

    python3 tools/bench_prehrajto.py            # 3000 filmů (polovina ve 2 verzích), 150 seriálů × 30 dílů
    python3 tools/bench_prehrajto.py --movies 500 --page-size 0

Používá náhradu Kodi API z tests/fake_kodi, časy jsou proto jen orientační (bez režie Kodi).
"""
import argparse
import os
import random
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--movies", type=int, default=3000)
    parser.add_argument("--shows", type=int, default=150)
    parser.add_argument("--episodes", type=int, default=30)
    parser.add_argument("--page-size", default="250", help="položek na stránku, 0 = vše")
    args = parser.parse_args()

    os.environ["FAKE_KODI_ADDON"] = "plugin.video.prehrajto.mojevidea"
    sys.path.insert(0, os.path.join(ROOT, "tests", "fake_kodi"))
    sys.path.insert(0, os.path.join(ROOT, "plugin.video.prehrajto.mojevidea", "resources", "lib"))
    import xbmcaddon
    import xbmcplugin
    xbmcaddon.PROFILE_DIR[0] = tempfile.mkdtemp(prefix="bench-prehrajto-")
    xbmcaddon.SETTINGS.update({"show_rating": "true", "username": "", "list_page_size": args.page_size})
    started = time.perf_counter()
    import plugin
    import ui
    from nameparser import parse
    print("import plugin: %.0f ms" % ((time.perf_counter() - started) * 1000))

    random.seed(1)
    with ui.open_library() as lib, lib.conn:
        vids = []
        for i in range(args.movies):
            for v in range(1 + (i % 2)):
                raw = "Film %d %d %s 1080p.mkv" % (i, 1950 + i % 70, "CZ" if v else "EN")
                vid = "%d:film-%d:%016x" % (i * 10 + v, i, i * 10 + v)
                lib.upsert_video(vid, raw, "account", parse(raw), duration=6000)
                vids.append(vid)
        for s in range(args.shows):
            for e in range(args.episodes):
                raw = "Serial %d S%02dE%02d CZ.mkv" % (s, 1 + e // 10, 1 + e % 10)
                number = 10 ** 8 + s * 100 + e
                vid = "%d:serial-%d:%016x" % (number, s, number)
                lib.upsert_video(vid, raw, "account", parse(raw), duration=2500)
                vids.append(vid)
        for key in lib.all_meta_keys():
            lib.update_meta(key, {"status": "ok", "source": "tmdb", "title": key, "rating": random.randint(30, 95),
                                  "genres": ["Drama"], "countries": ["Česko"],
                                  "cast": ["Herec %d" % j for j in range(10)], "plot": "x" * 400})
        for vid in random.sample(vids, min(400, len(vids))):
            lib.record_play(vid, position=600, total=6000)
        show_key = next((k for k in lib.all_meta_keys() if k.startswith("show:")), None)
    print("videí: %d" % len(vids))

    screens = [("Hlavní nabídka", plugin.root, {}), ("Moje filmy", plugin.movies, {"order": "title"}),
               ("Filmy – nově přidané", plugin.movies, {"order": "added"}),
               ("Filmy – filtr CZ", plugin.movies, {"language": "CZ", "filtered": "1"}),
               ("Seriály", plugin.shows, {}), ("Rozkoukané", plugin.in_progress, {}),
               ("Nedávno přidané", plugin.recent, {}), ("Všechna videa", plugin.all_videos, {})]
    if show_key:
        screens += [("Seriál", plugin.show, {"key": show_key}),
                    ("Série", plugin.season, {"key": show_key, "season": "1"})]
    for label, screen, params in screens:
        del xbmcplugin.ITEMS[:]
        ui._PLAYBACK.clear()
        started = time.perf_counter()
        screen(params)
        print("%-22s %7.0f ms  položek %5d" % (label, (time.perf_counter() - started) * 1000, len(xbmcplugin.ITEMS)))


if __name__ == "__main__":
    main()
