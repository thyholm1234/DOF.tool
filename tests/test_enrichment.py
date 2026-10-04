from backend.app.services.dof_enrichment import DofEnrichment, parse_names, parse_remarkable
from backend.app.services.dof_auth import extract_dof_observer_name


def test_dof_classification_prefers_su_and_sub_before_remarkable():
    enrichment = DofEnrichment(
        su={"sjælden art"},
        sub={"sub art"},
        remarkable={"almindelig art": 4},
    )

    assert enrichment.classify("Sjælden art", "alm") == ("SU", None)
    assert enrichment.classify("Sub art", "alm") == ("SUB", None)
    assert enrichment.classify("Almindelig art", "alm") == ("BEMÆRK", 4)
    assert enrichment.classify("Anden art", "faenologi") == ("FÆNOLOGI", None)


def test_parsers_normalize_dofbasen_html():
    html = "<tr><td>1</td><td></td><td> Hedelærke&nbsp; </td></tr>"
    assert "hedelærke" in parse_names(html)

    remarkable = "<tr><td>Hedelærke</td><td align='right'>1.200</td></tr>"
    assert parse_remarkable(remarkable) == {"hedelærke": 1200}


def test_dof_observer_name_parser_matches_legacy_markup():
    page = 'Navn</acronym>:</td><td valign="top">Måge &amp; Søn</td>'
    assert extract_dof_observer_name(page) == "Måge & Søn"