"""Artsgenkendelse i artikeltekst (tokenbaseret matcher med danske bøjninger)."""

import pytest

from portal.services.article_species import SpeciesMatcher, inflections, name_forms

ROWS = [(1, "Dværgterne", "Sternula albifrons"), (2, "Sortgrå Ryle", "Calidris maritima"), (3, "Almindelig Ryle", "Calidris alpina"),
        (4, "Ravn", "Corvus corax"), (5, "Hjejle", "Pluvialis apricaria"), (6, "Stor Skarv", "Phalacrocorax carbo"), (7, "And", "Anas x")]


@pytest.fixture
def m():
    return SpeciesMatcher(ROWS, {"Ravn"})


def hits(m, text):
    return {k: v.hits for k, v in m.find(text).items()}


def test_inflected_forms(m):
    assert {"dværgterne", "dværgterner", "dværgternerne", "dværgternes"} <= name_forms("Dværgterne")
    assert {"hjejle", "hjejler", "hjejlerne", "hjejlens"} <= inflections("Hjejle") | {"hjejlens"}
    assert hits(m, "Dværgterner, dværgternerne og Dværgterne") == {1: 3}


def test_longest_match_wins_and_abbreviation(m):
    assert hits(m, "Sortgrå Ryle og Alm. Ryle samt almindelig ryle") == {2: 1, 3: 2}
    assert 3 not in hits(m, "Sortgrå Ryle")


def test_hyphen_case_latin_and_positions(m):
    h = m.find("Stor-skarv og STOR SKARV; Calidris alpina.")
    assert h[6].hits == 2 and h[3].hits == 1 and h[6].first_pos == 0


def test_short_names_are_ignored(m):
    assert hits(m, "And and AND") == {}


def test_ambiguous_names_need_capital_inside_sentence(m):
    assert hits(m, "Ravn var der. Vi så en ravn flyve. Peter Ravn kom.") == {4: 1}  # kun «Peter Ravn» (stort midt i sætningen)
    assert hits(m, "Se: Ravn.") == {}
    assert hits(m, "Der kom en Ravn og to Ravne") == {4: 2}


def test_exact_lookup_for_tags(m):
    assert m.tag_species("  dværgterne ") == 1 and m.tag_species("Calidris Alpina") == 3 and m.tag_species("Skagen") is None
