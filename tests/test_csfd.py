import pytest

import csfd
from conftest import fixture


def test_parse_search():
    res = csfd.parse_search(fixture("csfd_search_synthetic.html"))
    assert [(r["id"], r["year"]) for r in res] == [(9499, 1999), (9497, 2003), (1234567, 2021)]
    assert res[2]["type"] == "seriál"
    assert res[0]["type"] == "film"


def test_best_match():
    res = csfd.parse_search(fixture("csfd_search_synthetic.html"))
    assert csfd.best_match(res, "Matrix", 1999)["id"] == 9499
    assert csfd.best_match(res, "Matrix", 0, want_series=True)["id"] == 1234567
    assert csfd.best_match(res, "Úplně jiný film", 1950) is None


def test_parse_film():
    info = csfd.parse_film(fixture("csfd_film_synthetic.html"), "https://www.csfd.cz/film/9499-matrix/prehled/")
    assert info["csfd_id"] == 9499
    assert info["title"] == "Matrix"
    assert info["orig_title"] == "The Matrix"
    assert info["rating"] == 90
    assert info["votes"] == 123456
    assert info["genres"] == ["Sci-Fi", "Akční"]
    assert info["countries"] == ["USA", "Austrálie"]
    assert info["year"] == 1999 and info["runtime"] == 136
    assert info["directors"][0] == "Lilly Wachowski"
    assert "Keanu Reeves" in info["cast"]
    assert info["plot"].startswith("Thomas A. Anderson") and "distributora" not in info["plot"]
    assert info["poster"].startswith("https://image.pmgstatic.com/cache/resized/w420/")


def test_challenge_detection():
    page = "<html><title>Making sure you&#39;re not a bot!</title><script id=\"anubis_challenge\">"
    assert csfd.is_challenge(page)
    assert not csfd.is_challenge(fixture("csfd_film_synthetic.html"))


def test_film_id_from_url():
    assert csfd.film_id_from_url("https://www.csfd.cz/film/9499-matrix/prehled/") == 9499
    assert csfd.film_id_from_url("9499") == 9499
    assert csfd.film_id_from_url("nesmysl") == 0
