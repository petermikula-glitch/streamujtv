import pytest

import nameparser

CASES = [
    ("Pernikovy.tata.S02E05.CZ.dabing.1080p.mkv", "episode", "Pernikovy tata", 0, 2, 5),
    ("Matrix (1999) CZ tit", "movie", "Matrix", 1999, 0, 0),
    ("big bang  s06E18", "episode", "big bang", 0, 6, 18),
    ("South Park 1x01", "episode", "South Park", 0, 1, 1),
    ("Pelíšky.1999.1080p.BluRay.x264.CZ", "movie", "Pelíšky", 1999, 0, 0),
    ("1917.2019.CZ.720p", "movie", "1917", 2019, 0, 0),
    ("Blade Runner 2049 (2017)", "movie", "Blade Runner 2049", 2017, 0, 0),
    ("2001 Vesmírná odysea (1968)", "movie", "2001 Vesmírná odysea", 1968, 0, 0),
    ("Dune.Part.Two.2024.2160p.DDP5.1.H.265-GRP.mkv", "movie", "Dune Part Two", 2024, 0, 0),
    ("Ordinace v růžové zahradě 2 - 1. série 3. díl", "episode", "Ordinace v růžové zahradě 2", 0, 1, 3),
    ("Vyprávěj - díl 12", "episode", "Vyprávěj", 0, 1, 12),
    ("Chernobyl.E03.720p", "episode", "Chernobyl", 0, 1, 3),
    ("Hra o trůny S01 E02 cz tit", "episode", "Hra o trůny", 0, 1, 2),
    ("Sparta", "movie", "Sparta", 0, 0, 0),
]


@pytest.mark.parametrize("raw,kind,title,year,season,episode", CASES)
def test_parse(raw, kind, title, year, season, episode):
    p = nameparser.parse(raw)
    assert (p["kind"], p["title"], p["year"], p["season"], p["episode"]) == (kind, title, year, season, episode)


def test_lang_quality():
    p = nameparser.parse("Avatar 2009 CZ dabing 1080p")
    assert p["lang"] == "CZ dabing"
    assert p["quality"] == "1080p"
    assert nameparser.parse("Avatar 2009 cz tit")["lang"] == "CZ titulky"


def test_normalize():
    assert nameparser.normalize("Pelíšky!") == "pelisky"
    assert nameparser.show_key("Hra o Trůny") == nameparser.show_key("hra o truny")
