import json
from datetime import date, timedelta

from portal.config import SEED_DIR
from portal.services import dofbasen as svc
from portal.services import features
from portal.timeutil import dk_today


def test_feature_states_and_parents():
    flags = {"dofbasen": "preview", "dofbasen_api": "on", "dofbasen_search": "off"}
    # Forhåndsvisning: kun superadmin og brugere med en DOFbasen-rolle
    assert not features.visible(flags, "dofbasen", superadmin=False, insider=False)
    assert features.visible(flags, "dofbasen", superadmin=True, insider=False)
    assert features.visible(flags, "dofbasen", superadmin=False, insider=True)
    # Underfunktion kræver, at forælderen også er synlig
    assert not features.visible(flags, "dofbasen_api", superadmin=False, insider=False)
    assert features.visible(flags, "dofbasen_api", superadmin=False, insider=True)
    # Slået fra gælder også superadmin
    assert not features.visible(flags, "dofbasen_search", superadmin=True, insider=True)
    flags["dofbasen"] = "off"
    assert not features.visible(flags, "dofbasen_api", superadmin=True, insider=True)


def test_feature_defaults_are_valid():
    for key, (_, _, parent, default) in features.FEATURES.items():
        assert default in features.STATES
        assert parent is None or parent in features.FEATURES, key


def test_ctx_permissions_and_edit_flag():
    ctx = svc.Ctx(user=None, flags={k: "on" for k in features.FEATURES}, perms={"sider"}, roles=["redaktoer"])
    assert ctx.can("sider") and not ctx.can("lister")
    assert ctx.can_edit("sider")
    ctx.flags["dofbasen_edit"] = "off"
    assert ctx.can("sider") and not ctx.can_edit("sider")
    assert ctx.out()["features"]["dofbasen_edit"] is False


def test_period():
    today = dk_today()
    assert svc.period(svc.SearchIn(periode="antaldage", dage=7)) == (today - timedelta(days=6), today)
    assert svc.period(svc.SearchIn(periode="dato", dato=date(2025, 5, 1))) == (date(2025, 5, 1), date(2025, 5, 1))
    assert svc.period(svc.SearchIn(periode="mellemdato", dato_first=date(2025, 6, 1), dato_second=date(2025, 5, 1))) == (date(2025, 5, 1), date(2025, 6, 1))
    assert svc.period(svc.SearchIn(periode="maanedaar", aar=2024, maaned=2)) == (date(2024, 2, 1), date(2024, 2, 29))
    assert svc.period(svc.SearchIn(periode="maanedaar", aar=2024, maaned=12)) == (date(2024, 12, 1), date(2024, 12, 31))
    assert svc.period(svc.SearchIn(periode="maanedaar", aar=2023)) == (date(2023, 1, 1), date(2023, 12, 31))
    assert svc.period(svc.SearchIn(periode="none")) == (None, None)


def test_search_sql_filters():
    p = svc.SearchIn(periode="maanedaar", aar=2025, species="su", omraade="lokal", lokal=578, artdata="art", art=1670,
                     antalregl="stoerre", antal=100, adf=2, udelad_hybrider_ubestemte=True, textsearch="50%_ok", obserdata=" 8220cvh ")
    where, args, notes = svc.search_sql(p, "8220CVH")
    assert "o.hemmelig = 0 OR o.obserkode = :viewer" in where
    assert args["d1"] == date(2025, 1, 1) and args["d2"] == date(2025, 12, 31)
    assert "o.kategori = 'SU'" in where and "l.afdeling_id = :lokal" in where and args["lokal"] == 578
    assert "o.artnr = :art" in where and args["art"] == 1670
    assert "o.antal >= :antal" in where and args["antal"] == 100
    assert "o.adfkode ~" in where
    assert "s.art_type NOT IN ('hybrid', 'ub')" in where
    assert args["txt"] == "%50\\%\\_ok%"  # LIKE-tegn i fritekst escapes
    assert args["obser"] == "8220CVH"
    assert notes == []


def test_search_without_period_falls_back_to_a_week():
    where, args, notes = svc.search_sql(svc.SearchIn(periode="none"), None)
    assert args["d2"] - args["d1"] == timedelta(days=6)
    assert notes
    # Med en art må der søges i alle år
    where, args, notes = svc.search_sql(svc.SearchIn(periode="none", artdata="art", art=720), None)
    assert "d1" not in args and not notes


def test_search_period_is_capped_without_narrowing():
    p = svc.SearchIn(periode="mellemdato", dato_first=date(1990, 1, 1), dato_second=date(2025, 1, 1))
    _, args, notes = svc.search_sql(p, None)
    assert (args["d2"] - args["d1"]).days == svc.MAX_DAYS and notes


def test_tokens_are_hashed():
    token, prefix, digest = svc.new_token()
    assert token.startswith(svc.TOKEN_PREFIX) and token.startswith(prefix) and len(prefix) == 12
    assert digest == svc.hash_token(token) and len(digest) == 64 and token not in digest


def test_csv_line_matches_columns():
    r = {k: None for k in ("dato", "turtidfra", "turtidtil", "loknr", "lokalitet", "art", "latin", "antal", "koen", "adfkode", "adfaerd",
                           "alderkode", "dragtkode", "obserkode", "observator", "obser_by", "medobser", "turnoter", "fuglnoter", "metode",
                           "obstidfra", "obstidtil", "kvalitet", "turid", "obsid", "afdeling", "kommune", "lat", "lon")}
    r["artnr"] = 720
    line = svc.csv_line(r)
    assert len(line) == len(svc.CSV_COLUMNS)
    assert line[5] == "00720"


def test_seed_content_is_complete_and_has_no_contact_details():
    data = json.loads((SEED_DIR / "dofbasen" / "content.json").read_text(encoding="utf-8"))
    for slug in ("start", "rettigheder", "kvalitet", "hjaelp", "kontakt", "om", "opslag", "visningstyper", "visningstyper_soegning"):
        assert data["pages"][slug]["sections"], slug
    for key in ("su", "sub", "foelsomme", "faenologi", "adfaerd", "alder", "dragt", "vindstyrke", "links", "koordinatorer"):
        lst = data["lists"][key]
        assert lst["rows"] and all(len(r) == len(lst["columns"]) for r in lst["rows"]), key
    assert data["news"] and data["focus"] and data["areas"]["stor"] and data["areas"]["iba"]
    text = json.dumps(data, ensure_ascii=False)
    # Koordinatorernes adresser og telefonnumre er ikke kopieret med
    assert "sendmail.php" not in text and "21741770" not in text
