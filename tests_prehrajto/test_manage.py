"""Kolekce, ruční úpravy, nástroje a řazení katalogu."""
import pytest

import nameparser
import prehrajto
import provider

from helpers import Fake, REF


def _add(plugin, vid, raw, **extra):
    with plugin.open_library() as lib:
        lib.upsert_video(vid, raw, "account", nameparser.parse(raw), **extra)


@pytest.fixture
def dialogs(plugin):
    import xbmc
    import xbmcgui
    for answers in (xbmc.KEYBOARD_INPUT, xbmc.BUILTINS, xbmcgui.SELECT_ANSWERS, xbmcgui.YESNO_ANSWERS,
                    xbmcgui.NUMERIC_ANSWERS, xbmcgui.BROWSE_ANSWERS):
        del answers[:]
    return xbmc, xbmcgui


def test_collections_are_created_filled_renamed_and_deleted(plugin, dialogs):
    import xbmcplugin
    xbmc, xbmcgui = dialogs
    other = "2:serial:%016x" % 2
    _add(plugin, REF, "Pelisky.1999.CZ")
    _add(plugin, other, "Archa.S01E01.CZ")

    xbmcgui.SELECT_ANSWERS.append(0)              # „nová kolekce"
    xbmc.KEYBOARD_INPUT.append("Chci vidět")
    plugin.collection_add({"target": "movie:pelisky|1999"})
    xbmcgui.SELECT_ANSWERS.append(1)              # existující kolekce
    plugin.collection_add({"target": "show:archa"})
    xbmcgui.SELECT_ANSWERS.append(1)
    plugin.collection_add({"target": "v:" + other})
    xbmcgui.SELECT_ANSWERS.append(-1)             # zrušený výběr nic nepřidá
    plugin.collection_add({"target": "movie:neexistuje|"})
    assert "Chci vidět" in xbmcgui.NOTIFICATIONS[-1]

    plugin.collections({})
    listed = [(item.label, url) for url, item, _folder in xbmcplugin.ITEMS if "action=collection&" in url
              or url.endswith("action=collection")]
    assert [label for label, _url in listed] == ["Chci vidět (3)"]
    with plugin.open_library() as lib:
        cid = lib.collections()[0]["id"]

    del xbmcplugin.ITEMS[:]
    plugin.collection({"cid": str(cid)})
    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert plugin.url_for("play", vid=REF) in urls and plugin.url_for("show", key="show:archa") in urls
    assert plugin.url_for("play", vid=other) in urls
    movie = next(item for url, item, _folder in xbmcplugin.ITEMS if url == plugin.url_for("play", vid=REF))
    assert any("collection_remove" in command for _label, command in movie.context)

    xbmc.KEYBOARD_INPUT.append("Na víkend")
    plugin.collection_rename({"cid": str(cid)})
    plugin.collection_remove({"cid": str(cid), "target": "show:archa"})
    with plugin.open_library() as lib:
        assert [(c["name"], c["item_count"]) for c in lib.collections()] == [("Na víkend", 2)]
    xbmcgui.YESNO_ANSWERS.append(False)
    plugin.collection_delete({"cid": str(cid)})
    with plugin.open_library() as lib:
        assert len(lib.collections()) == 1
    xbmcgui.YESNO_ANSWERS.append(True)
    plugin.collection_delete({"cid": str(cid)})
    with plugin.open_library() as lib:
        assert lib.collections() == []
    xbmc.KEYBOARD_INPUT.append(None)              # zrušená klávesnice
    assert plugin.collection_new({}) is None
    xbmc.KEYBOARD_INPUT.append("Pro děti")
    assert plugin.collection_new({}) and "Container.Refresh" in xbmc.BUILTINS


def test_manual_video_edit_hide_info_and_delete(plugin, dialogs):
    import xbmcplugin
    xbmc, xbmcgui = dialogs
    _add(plugin, REF, "Divny nazev souboru 7")

    xbmcgui.SELECT_ANSWERS.append(1)              # díl seriálu
    xbmc.KEYBOARD_INPUT.append("Archa")
    xbmcgui.NUMERIC_ANSWERS.extend(["2", "5"])
    plugin.video_edit({"vid": REF})
    with plugin.open_library() as lib:
        video = lib.get_video(REF)
        assert (video["kind"], video["meta_key"], video["season"], video["episode"], video["locked"]) == (
            "episode", "show:archa", 2, 5, 1)
        # zamčené video synchronizace ani nové rozpoznání nepřepíše
        lib.upsert_video(REF, "Uplne jiny nazev 2001", "account", nameparser.parse("Uplne jiny nazev 2001"))
        assert lib._reparse_unlocked() == 0 and lib.get_video(REF)["meta_key"] == "show:archa"

    xbmcgui.SELECT_ANSWERS.append(0)              # zpět na film
    xbmc.KEYBOARD_INPUT.append("Archa")
    xbmcgui.NUMERIC_ANSWERS.append("2012")
    plugin.video_edit({"vid": REF})
    with plugin.open_library() as lib:
        assert lib.get_video(REF)["meta_key"] == "movie:archa|2012" and lib.get_meta("show:archa") is None
    xbmcgui.SELECT_ANSWERS.append(-1)             # zrušeno → beze změny
    plugin.video_edit({"vid": REF})

    plugin.video_info({"vid": REF})
    text = xbmcgui.OK_DIALOGS[-1]
    assert "Divny nazev souboru 7" in text and prehrajto.video_page_url(REF) in text and "Archa (2012)" in text

    plugin.video_hide({"vid": REF})
    plugin.movies({"order": "title"})
    assert not xbmcplugin.ITEMS
    plugin.hidden({})
    assert [url for url, _item, _folder in xbmcplugin.ITEMS] == [plugin.url_for("play", vid=REF)]
    plugin.video_hide({"vid": REF, "hide": "0"})

    xbmcgui.YESNO_ANSWERS.append(False)
    plugin.video_delete({"vid": REF})
    with plugin.open_library() as lib:
        assert lib.count_videos() == 1
    xbmcgui.YESNO_ANSWERS.append(True)
    plugin.video_delete({"vid": REF})
    with plugin.open_library() as lib:
        assert lib.count_videos() == 0 and lib.all_meta_keys() == []


def test_links_are_imported_from_keyboard_and_file(plugin, dialogs, monkeypatch, tmp_path):
    from conftest import fixture
    xbmc, xbmcgui = dialogs
    page = fixture("video_synthetic.html")
    client = Fake({"/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60": (page, None, "", 200)})
    monkeypatch.setattr(plugin.ui, "make_client", lambda: client)

    xbmc.KEYBOARD_INPUT.append("tohle není odkaz")
    plugin.add_link({})
    assert xbmcgui.OK_DIALOGS and not xbmcgui.NOTIFICATIONS
    xbmc.KEYBOARD_INPUT.append("https://prehraj.to/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60")
    plugin.add_link({})
    with plugin.open_library() as lib:
        videos = lib.all_videos()
        assert len(videos) == 1 and videos[0]["source"] == "manual" and videos[0]["meta_key"].startswith("movie:")

    listing = tmp_path / "odkazy.txt"
    listing.write_text("# komentář\n"
                       "Samotáři (2000) CZ | https://prehraj.to/samotari-2000/00000000000000aa\n"
                       "https://prehraj.to/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60\n"
                       "https://prehraj.to/samotari-2000/00000000000000aa ; duplicitní řádek\n", encoding="utf-8")
    xbmcgui.BROWSE_ANSWERS.append(str(listing))
    plugin.import_file({})
    with plugin.open_library() as lib:
        assert sorted(v["meta_key"] for v in lib.all_videos())[-1] == "movie:samotari|2000"
        assert lib.count_videos() == 2
    xbmcgui.BROWSE_ANSWERS.append("")             # zrušený výběr souboru
    plugin.import_file({})


def test_tools_menu_statistics_account_status_and_cleanup(plugin, dialogs, monkeypatch):
    import xbmcaddon
    import xbmcplugin
    xbmc, xbmcgui = dialogs
    _add(plugin, REF, "Pelisky.1999.CZ")
    with plugin.open_library() as lib:
        lib.cache_put("tmdb:/genre/movie/list?language=cs-CZ", {"genres": []}, 3600)
        lib.cache_put("search:online:abc", {"query": "x"}, 600)
        lib.set_kv("link:" + REF, "{}")
        lib.set_kv("tmdb:season:1:1", "{}")
        lib.set_kv("search:availability:guest", "{}")
        lib.record_play(REF, 600, 6000)

    plugin.tools({})
    actions = [url.rsplit("action=", 1)[1] for url, _item, _folder in xbmcplugin.ITEMS]
    assert {"sync", "enrich", "cache_clear", "backup_catalog", "stats", "settings"} <= set(actions)
    assert all(action in plugin.ROUTES for action in actions)

    plugin.stats({})
    assert "1" in xbmcgui.OK_DIALOGS[-1]

    plugin.cache_clear({})
    with plugin.open_library() as lib:
        assert lib.conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
        assert lib.get_kv("link:" + REF) is None and lib.get_kv("tmdb:season:1:1") is None
        assert lib.get_kv("search:availability:guest") is None
        assert lib.count_videos() == 1 and lib.resume_points() == {REF: (600.0, 6000.0)}  # katalog i historie zůstaly

    for days, expected in ((30, "30"), (0, None)):
        client = Fake({})
        monkeypatch.setattr(client, "check_user", lambda days=days: (
            (provider.CHECK_PREMIUM, days) if days else (provider.CHECK_NO_PREMIUM, None)))
        monkeypatch.setattr(plugin.ui, "make_client", lambda client=client: client)
        plugin.account_status({})
        value = xbmcaddon.SETTINGS["premium_until"]
        assert (expected in value) if expected else value == "-"
    xbmcaddon.SETTINGS["password"] = ""
    shown = len(xbmcgui.OK_DIALOGS)
    plugin.account_status({})                     # bez přihlašovacích údajů jen upozornění
    assert len(xbmcgui.OK_DIALOGS) + len(xbmcgui.NOTIFICATIONS) > shown

    xbmcgui.YESNO_ANSWERS.append(False)
    plugin.clear_library({})
    xbmcgui.YESNO_ANSWERS.append(True)
    plugin.history_clear({})
    with plugin.open_library() as lib:
        assert lib.count_videos() == 1 and lib.resume_points() == {}
    xbmcgui.YESNO_ANSWERS.append(True)
    plugin.clear_library({})
    with plugin.open_library() as lib:
        assert lib.count_videos() == 0


def test_whole_catalogue_order_can_be_changed_on_paged_listings(plugin, dialogs):
    import xbmcaddon
    import xbmcplugin
    xbmc, xbmcgui = dialogs
    for i in range(5):
        _add(plugin, "%d:film-%d:%016x" % (i + 1, i, i + 1), "Film %02d %d CZ" % (i, 2004 - i))
    with plugin.open_library() as lib:
        for i, key in enumerate(sorted(lib.all_meta_keys())):
            lib.update_meta(key, {"status": "ok", "title": "Film %02d" % i, "year": 2004 - i, "rating": 50 + i * 10})
    xbmcaddon.SETTINGS["list_page_size"] = "2"

    plugin.movies({"order": "title"})
    url, item, folder = xbmcplugin.ITEMS[0]
    assert "action=list_order" in url and "target=movies" in url and not folder
    assert item.props["SpecialSort"] == "top" and "podle názvu" in item.label
    del xbmcplugin.ITEMS[:]
    plugin.movies({"order": "title", "page": "2"})
    assert not any("action=list_order" in url for url, _item, _folder in xbmcplugin.ITEMS)

    xbmcgui.SELECT_ANSWERS.append(2)              # podle hodnocení
    plugin.list_order({"target": "movies", "order": "title", "genre": "Drama"})
    assert xbmc.BUILTINS == ["Container.Update(%s,replace)" % plugin.url_for("movies", order="rating", genre="Drama")]
    del xbmc.BUILTINS[:]
    xbmcgui.SELECT_ANSWERS.append(0)              # stejné řazení → nic
    plugin.list_order({"target": "movies", "order": "title"})
    plugin.list_order({"target": "tools", "order": "title"})  # cizí cíl se ignoruje
    assert not xbmc.BUILTINS

    del xbmcplugin.ITEMS[:]
    plugin.movies({"order": "rating"})
    labels = [item.label for url, item, _folder in xbmcplugin.ITEMS if "action=play" in url]
    assert labels == ["Film 04 (2000)", "Film 03 (2001)"]
    del xbmcplugin.ITEMS[:]
    plugin.movies({"order": "year"})
    assert [item.label for url, item, _f in xbmcplugin.ITEMS if "action=play" in url][0] == "Film 00 (2004)"

    xbmcaddon.SETTINGS["list_page_size"] = "0"    # bez stránkování stačí řazení Kodi
    del xbmcplugin.ITEMS[:]
    plugin.movies({"order": "title"})
    assert not any("action=list_order" in url for url, _item, _folder in xbmcplugin.ITEMS)


def test_tmdb_find_rejects_a_clear_mismatch(monkeypatch):
    import tmdb
    client = tmdb.Tmdb("k" * 32)
    results = {"results": [{"id": 7, "title": "Úplně jiný film", "original_title": "Something Else",
                            "release_date": "2015-05-01", "vote_count": 5000}]}
    monkeypatch.setattr(client, "_get", lambda path, **params: results)
    monkeypatch.setattr(client, "details", lambda tmdb_id, want_series=False: {"tmdb_id": tmdb_id})
    assert client.find("Pelíšky", 1999) is None                    # nesedí název ani rok
    assert client.find("Uplne jiny film", 1999) == {"tmdb_id": 7}  # název sedí, rok je v názvu souboru špatně
    assert client.find("Jiný film", 2016) == {"tmdb_id": 7}        # rok o jedna vedle (premiéra v jiné zemi)
    assert client.find("Jiný film") == {"tmdb_id": 7}              # bez roku se chování nemění
