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
    # běžná slova názvů, která jsou zároveň značkami
    ("Johnny English (2003) CZ dabing", "movie", "Johnny English", 2003, 0, 0),
    ("Johnny English Reborn CZ", "movie", "Johnny English Reborn", 0, 0, 0),
    ("The English Patient (1996)", "movie", "The English Patient", 1996, 0, 0),
    ("Charlotte's Web (2006) CZ", "movie", "Charlotte's Web", 2006, 0, 0),
    ("La vie en rose (2007)", "movie", "La vie en rose", 2007, 0, 0),
    ("A Complete Unknown 2024", "movie", "A Complete Unknown", 2024, 0, 0),
    ("Film WEB 1080p CZ", "movie", "Film", 0, 0, 0),
    ("Movie Extended 1080p", "movie", "Movie", 0, 0, 0),
    ("Avatar.CZ.dabing.2009.1080p", "movie", "Avatar", 2009, 0, 0),
    ("Avatar EN", "movie", "Avatar", 0, 0, 0),
    ("Film WEB (2010)", "movie", "Film", 2010, 0, 0),
    ("JOHNNY ENGLISH 2003", "movie", "JOHNNY ENGLISH", 2003, 0, 0),
    ("Johnny English", "movie", "Johnny English", 0, 0, 0),
    # denní seriály a čísla dílů, která vypadají jako rok
    ("Ulice 4012. díl", "episode", "Ulice", 0, 1, 4012),
    ("Ulice 1998. díl CZ", "episode", "Ulice", 0, 1, 1998),
    ("Ordinace v ruzove zahrade 2 - 1105. dil", "episode", "Ordinace v ruzove zahrade 2", 0, 1, 1105),
    ("Blade Runner 2049 1080p CZ", "movie", "Blade Runner 2049", 0, 0, 0),
    # interpunkce kolem označení dílu
    ("Show S01E02: Pilot", "episode", "Show", 0, 1, 2),
    ("Show (S01E02) CZ", "episode", "Show", 0, 1, 2),
    ("Show - S01E02, CZ", "episode", "Show", 0, 1, 2),
    ("Show S01E02-E03 CZ", "episode", "Show", 0, 1, 2),
    ("Wall-E 2008", "movie", "Wall-E", 2008, 0, 0),
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


def test_keys_keep_titles_without_latin_letters_apart():
    assert nameparser.movie_key("Брат", 2001) != nameparser.movie_key("Унесённые призраками", 2001)
    assert nameparser.show_key("Брат") != nameparser.show_key("Бригада")
    assert nameparser.movie_key("Pelíšky", 1999) == "pelisky|1999"
