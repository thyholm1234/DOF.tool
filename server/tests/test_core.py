from datetime import date, datetime

import numpy as np

from portal.dof.classify import Classifier
from portal.domain.adfaerd import first_is_migration as is_migrating
from portal.dof.parse import decode, parse_text
from portal.dof.reference import base_name, is_listable_name, parse_bemaerk, parse_wsdata
from portal.services import phenology
from portal.services.dmi import migration_score
from portal.timeutil import TZ, in_quiet_hours

HEADER = ("Dato;Turtidfra;Turtidtil;Loknr;Loknavn;Artnr;Artnavn;Latin;Sortering;Antal;Koen;Adfkode;Adfbeskrivelse;"
          "Alderkode;Dragtkode;Dragtbeskrivelse;Obserkode;Fornavn;Efternavn;Obser_by;Medobser;Turnoter;Fuglnoter;Metode;"
          "Obstidfra;Obstidtil;Hemmelig;Kvalitet;Turid;Obsid;DOF_afdeling;lok_laengdegrad;lok_breddegrad;obs_laengdegrad;"
          "obs_breddegrad;radius;obser_laengdegrad;obser_breddegrad")


def row(obsid: int, afd: str = "DOF Nordjylland", antal: str = "6", note: str = "") -> str:
    return (f"2025-05-31;04:25;11:02;841020;Grenen, Skagen;00020;Rødstrubet Lom;Gavia stellata;3630;{antal};-;Ø;"
            f"Trækkende mod øst;-;-;Uspecificeret dragt;8200ikl;Inger;Klit;Egå;;\"{note}\";;Uspecificeret/tilfældig;;;0;1;"
            f"5757984;{obsid};{afd};10,635593;57,745089;;;;;")


def test_parse_merges_departments_and_normalizes():
    text = "\n".join([HEADER, row(1), row(1, afd="DOF Vestjylland", antal="8"), row(2, note="#BB25; tur")])
    rows = parse_text(text)
    assert len(rows) == 2
    r1 = next(r for r in rows if r.obsid == 1)
    assert r1.afdelinger == ["DOF Nordjylland", "DOF Vestjylland"]
    assert r1.antal == 8
    assert r1.obserkode == "8200IKL"
    assert r1.artnr == 20
    assert abs(r1.lok_lat - 57.745089) < 1e-6
    assert r1.dato == date(2025, 5, 31)
    assert r1.koen is None  # '-' betyder tom
    r2 = next(r for r in rows if r.obsid == 2)
    assert r2.turnoter == "#BB25; tur"


def test_parse_rejects_html_error_page():
    try:
        parse_text("<html><body>Server busy</body></html>")
    except ValueError:
        return
    raise AssertionError("HTML burde afvises")


def test_decode_latin1_and_smart_quotes():
    assert decode("Bl\xe5vand \x93test\x94".encode("latin-1")) == "Blåvand “test”"
    assert decode("Blåvand".encode("utf-8")) == "Blåvand"


def test_migration_codes():
    assert is_migrating("NØ")
    assert is_migrating("T R")
    assert not is_migrating("R T")
    assert not is_migrating("OF")
    assert not is_migrating(None)


def test_classifier_rules():
    c = Classifier()
    c.klass_by_artnr = {1: "SU", 2: "SUB", 3: "Alm", 4: "Alm"}
    c.windows = {"Pibesvane": [((4, 21), (9, 25))], "Vinterfugl": [((10, 1), (3, 31))]}
    c.thresholds = {586: {"Agerhøne": 10}}
    assert c.kategori(1, "x", date(2025, 1, 1), 1, []) == "SU"
    assert c.kategori(2, "x", date(2025, 1, 1), 1, []) == "SUB"
    assert c.kategori(3, "Pibesvane", date(2025, 6, 1), 1, []) == "bemaerk"
    assert c.kategori(3, "Pibesvane", date(2025, 12, 1), 1, []) == "alm"
    assert c.kategori(3, "Vinterfugl", date(2025, 1, 15), 1, []) == "bemaerk"  # periode over nytår
    assert c.kategori(4, "Agerhøne", date(2025, 3, 1), 12, [586]) == "bemaerk"
    assert c.kategori(4, "Agerhøne", date(2025, 3, 1), 9, [586]) == "alm"
    assert c.kategori(4, "Agerhøne", date(2025, 3, 1), 12, [574]) == "alm"


def test_list_species_names():
    assert base_name("Blisgås, Grønlandsk (ssp. flavirostris)") == "Blisgås"
    assert not is_listable_name("Måge sp.")
    assert not is_listable_name("Gråand x Spidsand (hybrid)")
    assert not is_listable_name("Rødstrubet Lom/Sortstrubet Lom")
    assert is_listable_name("Klippedue (tamdue)")  # DOFbasens eneste Columba livia – kategori C på Den Danske Liste
    assert is_listable_name("Rødstrubet Lom")


def test_reference_parsers():
    html = '<tr><td><span class="x">Agerhøne</span></td><td align="right">1.000</td></tr><tr><td>Aftenfalk</td><td align="right">1</td></tr>'
    assert parse_bemaerk(html) == {"Agerhøne": 1000, "Aftenfalk": 1}
    ws = "<tr><td>01530</td><td x>a</td><td>Pibesvane</td><td>(21/4-25/9)</td></tr>"
    assert parse_wsdata(ws) == [("Pibesvane", "21-04", "25-09")]


def test_quiet_hours():
    q = {"start": "22:00", "end": "07:00"}
    assert in_quiet_hours(q, datetime(2025, 1, 1, 23, 30, tzinfo=TZ))
    assert in_quiet_hours(q, datetime(2025, 1, 1, 6, 59, tzinfo=TZ))
    assert not in_quiet_hours(q, datetime(2025, 1, 1, 12, 0, tzinfo=TZ))
    assert not in_quiet_hours({"start": "08:00", "end": "08:00"}, datetime(2025, 1, 1, 8, 0, tzinfo=TZ))
    assert not in_quiet_hours(None)


def test_quantiles_of_symmetric_peak():
    x = np.zeros(181)
    x[99:102] = 1.0  # top omkring dag 101
    q10, q50, q90 = phenology.quantile_doys(x, 1)
    assert 99 <= q10 <= q50 <= q90 <= 102.5
    assert abs(q50 - 101) < 1


def test_theil_sen_detects_advancing_dates():
    years = np.arange(2010, 2026, dtype=float)
    q50 = 120 - 0.5 * (years - 2010) + np.tile([1, -1], 8)
    t = phenology.theil_sen(years, q50)
    assert t is not None and -6 < t["slope_per_decade"] < -4
    assert t["p"] < 0.01


def test_best_lag_finds_shift():
    days = np.arange(366)
    base = np.exp(-((days - 120) ** 2) / (2 * 12 ** 2))
    later = np.exp(-((days - 128) ** 2) / (2 * 12 ** 2)) * 0.7
    res = phenology.best_lag(later, base, 60, 140)
    assert res is not None and res["lag_days"] == 8


def test_migration_score_direction_logic():
    spot = {"sea": True, "onshore": (250, 360)}
    spring_good = migration_score(4, 5, 180, 0, spot)  # sydlig vind i april = medvind
    spring_bad = migration_score(4, 5, 0, 0, spot)
    assert spring_good["land"] > spring_bad["land"]
    assert migration_score(4, 5, 180, 3, spot)["land"] < spring_good["land"]  # regn trækker ned
    assert migration_score(10, 14, 300, 0, spot)["sea"] > migration_score(10, 14, 100, 0, spot)["sea"]


def test_edges_and_profile():
    days = np.arange(366)
    summer_bird = np.where((days > 110) & (days < 250), 1.0, 0.0)
    assert phenology.profile(summer_bird) == "sommer"
    winter_bird = 1.0 - summer_bird
    assert phenology.profile(winter_bird) == "vinter"
    seg = summer_bird[0:181]
    q10, q50, q90 = phenology.edge_doys(seg, 1, rising=True)
    assert q10 == q50 == q90 == 112  # første dag med forekomst (dag 111 nul-indekseret)
    seg_a = summer_bird[181:366]
    q10, q50, q90 = phenology.edge_doys(seg_a, 182, rising=False)
    assert q90 == 250 and q10 <= q50 <= q90  # sidste dag med forekomst (indeks 249 = dag 250)


def test_popobs_media_links():
    from portal.dof.jobs import parse_popobs

    page = ("<a target=\"blank\"href='/image_proxy.php?mode=o&amp;pic=40440230_20261018071718_523355433.jpg'>"
            "<img src='/image_proxy.php?mode=o&amp;pic=40440230_20261018071718_523355433.jpg&amp;resizeto=small'/></a>"
            "<a href='/image_proxy.php?mode=o&amp;pic=40440230_20261018071730_173677254.jpg'></a>"
            "<a href=\"/sound_proxy.php?file=abc.mp3\">lyd</a>")
    res = parse_popobs(page)
    assert res["images"] == [
        "https://service.dofbasen.dk/media/image/o/40440230_20261018071718_523355433.jpg",
        "https://service.dofbasen.dk/media/image/o/40440230_20261018071730_173677254.jpg",
    ]
    assert res["sounds"] == ["https://dofbasen.dk/sound_proxy.php?file=abc.mp3"]


def test_matrikel_period_selection():
    from portal.services.lists import MAX_DATE, Period, period_windows, select_active

    old = Period(1, "Vestergade 3", date(2020, 1, 1), date(2026, 5, 14))
    new = Period(2, "Skovvej 8", date(2026, 5, 15), None)
    year = (date(2026, 1, 1), date(2026, 12, 31))
    # Efter flytningen er den nye adresse aktiv; før den var den gamle
    assert select_active([old, new], *year, ref=date(2026, 8, 1)) == new
    assert select_active([old, new], *year, ref=date(2026, 3, 1)) == old
    # Et tidligere år viser den periode der gjaldt ved årets udgang
    assert select_active([old, new], date(2024, 1, 1), date(2024, 12, 31)) == old
    # Ingen perioder = ingen begrænsning; 'alle' = historik over alle perioder
    assert period_windows([], 2026)[0] is None
    wins, info = period_windows([old, new], 2026, "alle")
    assert wins == [(old.start, old.slut), (new.start, MAX_DATE)] and info["selected"] == "alle"
    wins, info = period_windows([old, new], 2026, "1")
    assert wins == [(old.start, old.slut)] and info["selected"] == 1
    # Perioder der ikke rører året giver en tom liste (fremgangen er nulstillet)
    assert period_windows([old], 2027)[0] == []
