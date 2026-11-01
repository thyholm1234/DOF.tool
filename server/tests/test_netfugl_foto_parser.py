"""Parseren i tools/netfugl_fotos/ på rigtige netfugl.dk-sider (hentet 1. nov. 2026) – ingen netværk."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "netfugl_fotos"
_spec = importlib.util.spec_from_file_location("netfugl_fotos", ROOT / "tools" / "netfugl_fotos" / "netfugl_fotos.py")
nf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nf)


def side(navn):
    return (FIX / navn).read_text(encoding="utf-8")


def test_en_fugl():
    m = nf.parse_foto(side("en_fugl_125244.html"), 125244)
    assert m["schema_version"] == 1 and m["id"] == 125244
    assert m["url"] == "https://netfugl.dk/fotos/125244"
    assert (m["bredde"], m["hoejde"]) == (2400, 1600)
    assert m["titel"] == "Ørkenstenpikker" and m["beskrivelse_html"] is None
    assert (m["dato"], m["uploadet"], m["publiceret"]) == ("2026-10-27", "2026-10-27", "2026-10-28")
    assert m["land"] == {"navn": "Danmark", "netfugl_id": 1}
    assert m["lokalitet"] == "Tisvildekysten" and m["koordinat"] is None
    assert m["fotograf"] == {"navn": "Leif Bolding", "netfugl_bruger_id": 3221}
    assert m["fugle"] == [{
        "art": {"dansk": "Ørkenstenpikker", "latin": "Oenanthe deserti", "engelsk": "Desert Wheatear", "netfugl_id": 25721},
        "adfaerd": "Siddende", "koen": "Han", "alder": "1. kalenderår", "dragt": None, "sjaeldenhed": "SU"}]
    assert m["kamera"]["model"] == "NIKON" and m["kamera"]["iso"] is None
    assert m["rettighed"] == {"id": 2, "tekst": "Personlig aftale med fotografen er påkrævet for brug af foto"}
    assert (m["likes"], m["visninger"]) == (1, 184)
    assert m["emneord"] == [] and m["gruppe"] == [] and m["kommentarer"] == []


def test_flere_fugle_er_en_liste():
    """Siden med flere fugle gentager fuglens blok; syntetisk side bygget af en rigtig (ingen rigtig flerfugle-side fundet)."""
    html = side("en_fugl_125244.html")
    assert len(nf.parse_foto(html, 125244)["fugle"]) == 1
    import copy

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    art = soup.find("i", class_="fa-feather-alt").find_parent("div")
    blok = next(b for b in art.find_all("div", recursive=False) if b.find("a", href=lambda h: h and "birdId" in h))
    blok.insert_after(copy.copy(blok))
    assert len(nf.parse_foto(str(soup), 125244)["fugle"]) == 2


def test_underart():
    m = nf.parse_foto(side("underart_100000.html"), 100000)
    f = m["fugle"][0]
    assert f["art"]["latin"] == "Aegithalos caudatus" and f["art"]["netfugl_id"] == 20431
    assert f["underart"] == {"latin": "caudatus", "netfugl_id": 20432}
    assert f["sjaeldenhed"] == "Almindelig" and f["koen"] is None
    assert m["dato"] == "2015-04-09" and m["visninger"] == 1391 and m["bredde"] is None


def test_koordinat_kamera_beskrivelse():
    m = nf.parse_foto(side("koordinat_og_kamera_124801.html"), 124801)
    assert m["koordinat"] == {"lat": pytest.approx(57.742892275814604), "lon": pytest.approx(10.627212524414062)}
    assert m["kamera"] == {"model": "E-M1MarkII", "linse": None, "blaende": "5.6", "braendvidde": "420",
                           "lukkertid": "1/1600", "iso": "800", "flash": None}
    assert "Ørnevåge fundet af ASF" in m["beskrivelse_html"] and "Beskrivelse:" not in m["beskrivelse_html"]
    assert m["lokalitet"] == "Grenen, Skagen" and m["titel"] == "Ørnevåge Skagen"


def test_emneord():
    assert nf.parse_foto(side("emneord_124822.html"), 124822)["emneord"] == ["Tvivl om køn/alder/dragt"]


def test_kommentarer():
    m = nf.parse_foto(side("kommentarer_124675.html"), 124675)
    k = m["kommentarer"]
    assert [c["id"] for c in k] == [47631, 47632]
    assert k[0]["forfatter"] == "Carsten Murmann" and k[0]["forfatter_id"] == 2204
    assert k[0]["dato"] == "2026-07-02T23:17" and k[0]["tekst_html"].startswith("<p>Stor tak")
    assert k[1]["forfatter_id"] == 1782 and "svar" in k[1]


def test_svar_indlejres_under_forælder():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(
        '<div id="comment-area1">'
        '<div class="comment parrent-comment" id="parent10"><div class="comment-content-author-name"><a href="/bruger/profil/1">A</a></div>'
        '<div class="comment-content-time">2. jul. 2026 kl. 9:05</div><div class="comment-content-text"><p>x</p></div></div>'
        '<div class="comment child-comment"><div class="comment-content-author-name"><a href="/bruger/profil/2">B</a></div>'
        '<div class="comment-content-time">2. jul. 2026 kl. 10:00</div><div class="comment-content-text"><p>y</p></div></div>'
        '</div>', "html.parser")
    k = nf._laes_kommentarer(soup.find(id="comment-area1"))
    assert len(k) == 1 and k[0]["dato"] == "2026-07-02T09:05"
    assert [s["forfatter"] for s in k[0]["svar"]] == ["B"]


@pytest.mark.parametrize("navn,id", [("tom_125246.html", 125246)])
def test_side_uden_foto_er_none(navn, id):
    assert nf.parse_foto(side(navn), id) is None
    assert nf.parse_foto("", 1) is None


def test_parse_dato():
    assert nf.parse_dato("1. nov. 2026 kl. 7:03") == "2026-11-01T07:03"
    assert nf.parse_dato("2026-10-27") == "2026-10-27"
    assert nf.parse_dato("sludder") is None
