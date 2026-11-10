"""Krydslistemodulet mod en rigtig Postgres: seed, tal = showcasens, listetyper uden kodeændring, manuelle kryds og godkendelse, artslister (CSV) og AviList-diff.
Kræver ARTICLES_TEST_DSN (se test_photo_import.py)."""

import os
from datetime import date

import pytest
import redis.asyncio as aioredis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from portal.services import kl_admin, kl_species, krydslister

DSN = os.environ.get("ARTICLES_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
TODAY = date.today()
Y = TODAY.year - 1


@pytest.fixture
async def s(monkeypatch):
    from portal import cache
    from portal.db import Base
    from portal.dbgroups import USER_TABLES
    from portal.models import EXTRA_DDL, Species, User
    from portal.dbops import ddl_groups
    from portal.services import netfugl

    engine = create_async_engine(DSN)
    wanted = {"species", "locations", "observers", "observations", "code_texts", "species_categories", "category_overrides", "afdelinger", "kommuner"}
    tables = [t for t in Base.metadata.sorted_tables if t.name in USER_TABLES or t.name in wanted]
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
        for ddl in EXTRA_DDL:
            if ("bird_category" in ddl or "kl_taxa" in ddl) and "users" not in ddl_groups(ddl) or "kl_taxa" in ddl:
                await conn.execute(text(ddl))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for mod in (krydslister, netfugl):
        monkeypatch.setattr(mod, "ObsSession", factory)
    kv = aioredis.from_url(os.environ.get("ARTICLES_TEST_REDIS", "redis://127.0.0.1:56379/0"), decode_responses=True)
    await kv.flushdb()
    monkeypatch.setattr(cache, "_client", kv)
    async with factory() as session:
        for i, (kode, navn) in enumerate([("LB", "Leif Bolding"), ("A1", "Anna Andersen"), ("A2", "Bo Bent"), ("MO", "Moderator")], 1):
            session.add(User(id=i, obserkode=kode, navn=navn, role="admin" if kode == "MO" else "user", prefs={}))
        for nr, navn, latin in [(101, "Urfugl", "Lyrurus tetrix"), (102, "Agerhøne", "Perdix perdix"), (103, "Vagtel", "Coturnix coturnix"), (104, "Fasan", "Phasianus colchicus"),
                                (105, "Klyde", "Recurvirostra avosetta")]:
            session.add(Species(artnr=nr, navn=navn, latin=latin, sortering=nr, art_type="art", list_artnr=nr))
        await session.execute(text("INSERT INTO afdelinger (id, navn, slug) VALUES (5, 'Test afd', 'test')"))
        await session.execute(text("INSERT INTO kommuner (kode, navn) VALUES ('0101', 'Københavns')"))
        await session.execute(text("INSERT INTO locations (loknr, navn, kommune_kode) VALUES (1, 'Sted A', '0101'), (2, 'Sted B', NULL)"))
        await session.execute(text("INSERT INTO species_categories (artnr, kategori) VALUES (105, 'D')"))  # Klyde er ikke krydsbar
        rows = [  # obsid, dato, artnr, antal, loknr, obserkode, afdelinger
            (1, date(Y, 5, 1), 101, 2, 1, "LB", [5]), (2, date(Y, 5, 9), 101, 1, 1, "LB", [5]), (3, date(Y, 6, 2), 102, 1, 2, "LB", []), (4, date(Y - 1, 5, 3), 103, 4, 1, "LB", [5]),
            (5, date(Y, 5, 2), 101, 1, 2, "A1", []), (6, date(Y, 7, 7), 104, 3, 1, "A1", [5]), (7, date(Y, 8, 8), 105, 1, 1, "A1", [5]), (8, date(Y, 5, 5), 102, 0, 1, "A1", [5]),
            (9, date(Y, 5, 6), 103, 1, 1, "A2", [5]), (10, date(Y, 5, 7), 101, 1, 1, "A2", [5]),
        ]
        for obsid, d, art, n, lok, kode, afd in rows:
            await session.execute(text("INSERT INTO observations (obsid, dato, artnr, antal, loknr, obserkode, hemmelig, kategori, afdelinger) VALUES (:o, :d, :a, :n, :l, :k, 0, 'alm', :f)"),
                                  {"o": obsid, "d": d, "a": art, "n": n, "l": lok, "k": kode, "f": afd})
        await session.commit()
    async with factory() as session:
        session.info["factory"] = factory
        yield session
    await kv.aclose()
    await engine.dispose()


async def test_seed_giver_ti_listetyper_og_world_bird_list(s):
    assert await krydslister.seed(s) is True and await krydslister.seed(s) is False  # kun første gang
    ds = await krydslister.defs(s)
    assert list(ds) == ["dk", "aar", "md", "afd", "kom", "ring", "aedel", "aedelaar", "vp", "falskvp"]
    assert ds["vp"]["regler"] == {"auto_fra": "dk"} and ds["aar"]["param"] == "year" and ds["dk"]["ref"] == "dk" and ds["falskvp"]["regler"]["godkendelse"] is True
    n_dk = (await s.execute(text("SELECT count(*) FROM kl_species_list_entries e JOIN kl_species_lists l ON l.id = e.list_id WHERE l.key = 'dk'"))).scalar()
    n_vp = (await s.execute(text("SELECT count(*) FROM kl_species_list_entries e JOIN kl_species_lists l ON l.id = e.list_id WHERE l.key = 'vp'"))).scalar()
    assert n_dk > 450 and n_vp > 1000
    ur = (await s.execute(text("SELECT t.artnr, e.kategorier, e.sjaeldenhed FROM kl_taxa t JOIN kl_species_list_entries e ON e.taxon_id = t.id JOIN kl_species_lists l ON l.id = e.list_id "
                               "WHERE l.key = 'dk' AND t.latin = 'Lyrurus tetrix'"))).one()
    assert ur == (101, ["A"], "su")  # Urfugl: SU-art i DK, koblet til DOFbasens art via latinsk navn
    fasan = (await s.execute(text("SELECT e.kategorier FROM kl_taxa t JOIN kl_species_list_entries e ON e.taxon_id = t.id JOIN kl_species_lists l ON l.id = e.list_id AND l.key = 'dk' WHERE t.latin = 'Phasianus colchicus'"))).scalar()
    assert fasan == ["C"]


async def test_tal_er_som_showcasens(s):
    """De ti seedede listetyper giver de samme tal som showcasen for de samme deltagere."""
    from portal.services import netfugl

    await krydslister.seed(s)
    await netfugl.refresh_auto(s)
    await krydslister.refresh_auto(s)
    keys = ["dk", f"aar:{Y}", f"aar:{Y - 1}", "md:5", "md:6", "afd:5", "kom:0101", "vp"]
    for key in keys:
        old, new = await netfugl.load_kryds(s, key), await krydslister.load_kryds(s, key)
        sig = lambda d: sorted((r["kode"], r["key"], r["dato"]) for r in d["rows"])  # noqa: E731
        assert sig(old) == sig(new), key
        assert [(r["kode"], r["antal"], r["placering"]) for r in netfugl.ranking(old)] == [(r["kode"], r["antal"], r["placering"]) for r in krydslister.ranking(new)], key
        assert old["name"] == new["name"], key
    dk = krydslister.ranking(await krydslister.load_kryds(s, "dk"))
    assert [(r["kode"], r["antal"], r["placering"]) for r in dk] == [("LB", 3, 1), ("A1", 2, 2), ("A2", 2, 2)]
    # Klyde (D) og observationen med antal 0 tæller ikke; månedslisten for maj har kun maj-fund
    assert (await krydslister.load_kryds(s, "dk"))["name"] == "Danske Arter" and (await krydslister.load_kryds(s, "md:5"))["name"] == "Danske Månedsarter Maj"
    assert (await krydslister.load_kryds(s, "afd:5"))["name"] == "Lokalarter: Test afd" and (await krydslister.load_kryds(s, "kom:0101"))["name"] == "Lokalarter: Københavns"
    with pytest.raises(krydslister.KlError):
        await krydslister.load_kryds(s, "aar")  # kræver år
    with pytest.raises(krydslister.KlError):
        await krydslister.load_kryds(s, "findes_ikke")


async def test_ny_listetype_i_admin_beregnes_uden_kodeaendring(s):
    await krydslister.seed(s)
    ds = await kl_admin.list_defs(s)
    ref = next(a["id"] for a in ds["artslister"] if a["key"] == "dk")
    d = await kl_admin.save_def(s, {"key": "kunc", "navn": "Kun C-arter", "ref_list_id": ref, "auto": True, "manuel": False, "param": None, "regler": {"kategorier": ["C"]}, "sort": 99})
    # (kategorier i reglerne styrer, hvad der er krydsbart; Fasan har kategori C i species_categories)
    await s.execute(text("INSERT INTO species_categories (artnr, kategori) VALUES (104, 'C')"))
    await s.commit()
    await krydslister.refresh_auto(s)
    data = await krydslister.load_kryds(s, "kunc")
    assert d.key == "kunc" and {(r["kode"], r["navn"]) for r in data["rows"]} == {("A1", "Fasan")}  # kun arter, hvis kategori er i reglerne
    assert (await krydslister.load_kryds(s, "dk"))["rows"]  # de andre lister er uændrede
    with pytest.raises(krydslister.KlError):
        await kl_admin.save_def(s, {"key": "ÆØ", "navn": "x", "ref_list_id": ref})
    with pytest.raises(krydslister.KlError):
        await kl_admin.save_def(s, {"key": "p", "navn": "uden p", "ref_list_id": ref, "param": "year"})
    await kl_admin.delete_def(s, d.id)
    assert "kunc" not in await krydslister.defs(s)
    assert (await s.execute(text("SELECT count(*) FROM kl_auto_lists WHERE liste = 'kunc'"))).scalar() == 0


async def test_manuelle_kryds_godkendelse_og_skjul(s):
    from portal.models import User

    await krydslister.seed(s)
    await krydslister.refresh_auto(s)
    lb, mod = await s.get(User, 1), await s.get(User, 4)
    taxon = lambda latin: s.execute(text("SELECT id FROM kl_taxa WHERE latin = :l"), {"l": latin})  # noqa: E731
    urfugl, masai = (await taxon("Lyrurus tetrix")).scalar(), (await taxon("Struthio camelus")).scalar()
    with pytest.raises(krydslister.KlError) as e:  # SU-art uden dato/lokalitet på DK-listen
        await krydslister.add_kryds(s, lb, liste="ring", taxon_id=urfugl, dato=None, lokalitet="", land="", note="", privileged=False)
    assert e.value.status == 400
    with pytest.raises(krydslister.KlError):  # fremtiden
        await krydslister.add_kryds(s, lb, liste="vp", taxon_id=masai, dato=date(2999, 1, 1), lokalitet="x", land="y", note="", privileged=False)
    k = await krydslister.add_kryds(s, lb, liste="vp", taxon_id=masai, dato=date(Y, 1, 1), lokalitet="Zoo", land="Afrika", note="", privileged=False)
    assert k.status == "ok"
    vp = await krydslister.load_kryds(s, "vp")
    assert {r["navn"] for r in vp["rows"] if r["kode"] == "LB"} >= {"Masaistruds", "Urfugl"}  # manuelt + DK-kryds fra DOFbasen via latinsk navn
    # falsk VP kræver godkendelse og tæller ikke, før den er godkendt
    f = await krydslister.add_kryds(s, lb, liste="falskvp", taxon_id=masai, dato=date(Y, 2, 2), lokalitet="Zoo", land="Afrika", note="", privileged=False)
    assert f.status == "afventer" and (await krydslister.load_kryds(s, "falskvp"))["rows"] == []
    q = await kl_admin.approval_queue(s)
    assert [x["id"] for x in q] == [f.id]
    await kl_admin.decide(s, mod, f.id, True, "")
    assert len((await krydslister.load_kryds(s, "falskvp"))["rows"]) == 1
    assert (await s.execute(text("SELECT subject FROM support_threads WHERE user_id = 1"))).scalar().endswith("er godkendt")
    # skjul et automatisk DK-kryds
    await krydslister.hide_kryds(s, lb, "dk", 102, True)
    assert "Agerhøne" not in {r["navn"] for r in (await krydslister.load_kryds(s, "dk"))["rows"] if r["kode"] == "LB"}
    mine = await krydslister.my_kryds(s, lb, "dk")
    assert [h["artnr"] for h in mine["hidden"]] == [102] and mine["participant"]
    await krydslister.hide_kryds(s, lb, "dk", 102, False)
    assert "Agerhøne" in {r["navn"] for r in (await krydslister.load_kryds(s, "dk"))["rows"] if r["kode"] == "LB"}
    # andres kryds kan kun slettes af en administrator
    with pytest.raises(krydslister.KlError):
        await krydslister.delete_kryds(s, await s.get(User, 2), k.id, False)
    await krydslister.delete_kryds(s, mod, k.id, True)


async def test_doede_fugle_tæller_ikke_på_personlige_lister(s):
    """En død fugl (adfærdskode … DØD) giver ikke kryds på de automatiske lister – den tæller kun på DK-listen."""
    await s.execute(text("INSERT INTO observations (obsid, dato, artnr, antal, loknr, obserkode, hemmelig, kategori, afdelinger, adfkode) "
                         "VALUES (11, :d, 104, 1, 1, 'LB', 0, 'alm', '{5}', 'DØD')"), {"d": date(Y, 6, 1)})
    await s.commit()
    await krydslister.seed(s)
    await krydslister.refresh_auto(s)
    for key in ("dk", f"aar:{Y}", "afd:5", "kom:0101"):
        assert "Fasan" not in {r["navn"] for r in (await krydslister.load_kryds(s, key))["rows"] if r["kode"] == "LB"}, key
    from portal.services.categories import alive_sql
    from portal.services import lists

    where, _ = await lists._where(s, lists.parse_list_spec("dk", Y), "#BB")
    assert alive_sql() in where  # års- og livslister og ranglisterne fra DOFbasen bruger samme betingelse


async def test_aedelarter_pr_aar_og_paa_livslisten(s):
    from portal.models import User

    await krydslister.seed(s)
    lb = await s.get(User, 1)
    # Urfugl er SU, men markeret fra årslisten bygger krydset på observationen, så dato/lokalitet er nok
    await krydslister.set_aedel(s, lb, artnr=101, year=Y, on=True, dato=date(Y, 5, 1), lokalitet="Sted A", obsid=1)
    await krydslister.set_aedel(s, lb, artnr=101, year=Y - 1, on=True, dato=date(Y - 1, 5, 3), obsid=4)
    await krydslister.set_aedel(s, lb, artnr=101, year=Y, on=True, dato=date(Y, 5, 9), obsid=2)  # samme år igen: erstatter årets kryds
    await krydslister.set_aedel(s, lb, artnr=102, year=None, on=True, obsid=3)  # kun livslisten
    names = lambda key: krydslister.load_kryds(s, key)  # noqa: E731
    aar, forrige, liv = await names(f"aedelaar:{Y}"), await names(f"aedelaar:{Y - 1}"), await names("aedel")
    assert [(r["navn"], r["dato"]) for r in aar["rows"]] == [("Urfugl", date(Y, 5, 9).isoformat())]
    assert [r["navn"] for r in forrige["rows"]] == ["Urfugl"]
    assert sorted((r["navn"], r["dato"]) for r in liv["rows"]) == [("Agerhøne", None), ("Urfugl", date(Y - 1, 5, 3).isoformat())]  # første ædelkryds pr. art
    marks = (await krydslister.aedel_marks(s, lb))["marks"]
    assert len(marks) == 3 and {m["obsid"] for m in marks} == {2, 3, 4}
    ov = {r["key"]: r for r in await krydslister.my_overview(s, lb, Y)}
    assert ov["aedel"]["antal"] == 2 and ov[f"aedelaar:{Y}"]["antal"] == 1 and ov[f"aedelaar:{Y}"]["placering"] == 1
    with pytest.raises(krydslister.KlError):  # datoen skal ligge i året
        await krydslister.set_aedel(s, lb, artnr=103, year=Y, on=True, dato=date(Y - 1, 1, 1))
    with pytest.raises(krydslister.KlError):  # arten skal findes på DK-listen
        await krydslister.set_aedel(s, lb, artnr=999999, year=None, on=True)
    # fjern fra årslisten: kun årets kryds; fra livslisten: alle artens ædelkryds
    await krydslister.set_aedel(s, lb, artnr=101, year=Y, on=False)
    assert (await names(f"aedelaar:{Y}"))["rows"] == [] and "Urfugl" in {r["navn"] for r in (await names("aedel"))["rows"]}
    await krydslister.set_aedel(s, lb, artnr=101, year=None, on=False)
    assert [r["navn"] for r in (await names("aedel"))["rows"]] == ["Agerhøne"]


async def test_visninger(s):
    await krydslister.seed(s)
    await krydslister.refresh_auto(s)
    data = await krydslister.load_kryds(s, "dk")
    ds = await krydslister.defs(s)
    ref = await krydslister.reference(s, ds["dk"]["ref_list_id"], True)
    who = krydslister.who_has_what(data, ref)
    assert who["total"] == 3 and next(r for r in who["rows"] if r["navn"] == "Urfugl")["antal"] == 3 and next(r for r in who["rows"] if r["navn"] == "Urfugl")["su"] is True
    assert krydslister.blockers(data)["limit"] == 1
    assert krydslister.latest(data, 2)[0]["x"] >= 1 and len(krydslister.tophits(data, Y)) >= 1


async def test_artsliste_csv_diff(s):
    await krydslister.seed(s)
    lid = (await s.execute(text("SELECT id FROM kl_species_lists WHERE key = 'dk'"))).scalar()
    csv = await kl_admin.export_csv(s, lid)
    assert csv.startswith("﻿latin;dansk;engelsk;kategorier;sjaeldenhed;sort") and "Lyrurus tetrix;Urfugl;Black Grouse;A;su;0" in csv
    edited = csv.replace("Lyrurus tetrix;Urfugl;Black Grouse;A;su;0", "Lyrurus tetrix;Urfugl;Black Grouse;A,C;sjaelden;0").replace("Perdix perdix;Agerhøne;Grey Partridge;A;almindelig", "Perdix perdix;Agerhøne;Grey Partridge;A;ualmindelig")
    diff = await kl_admin.import_diff(s, lid, edited.encode("utf-8"))
    assert {a["latin"] for a in diff["aendrede"]} == {"Lyrurus tetrix", "Perdix perdix"} and diff["anvendt"] is False and diff["nye"] == []
    assert (await s.execute(text("SELECT sjaeldenhed FROM kl_species_list_entries e JOIN kl_taxa t ON t.id = e.taxon_id WHERE t.latin = 'Lyrurus tetrix' AND e.list_id = :l"), {"l": lid})).scalar() == "su"
    diff = await kl_admin.import_diff(s, lid, edited.encode("utf-8"), apply=True)
    assert diff["anvendt"] is True
    assert (await s.execute(text("SELECT kategorier, sjaeldenhed FROM kl_species_list_entries e JOIN kl_taxa t ON t.id = e.taxon_id WHERE t.latin = 'Lyrurus tetrix' AND e.list_id = :l"), {"l": lid})).one() == (["A", "C"], "sjaelden")
    bad = await kl_admin.import_diff(s, lid, "latin;sjaeldenhed\nPerdix perdix;mærkelig\nIkke Kendt;su\n".encode())
    assert bad["fejl"] and bad["ukendte"] == ["Ikke Kendt"]
    with pytest.raises(krydslister.KlError):
        await kl_admin.import_diff(s, lid, b"navn\nx\n")


async def test_admin_retter_arterne_paa_vp_listen(s):
    await krydslister.seed(s)
    vp = (await s.execute(text("SELECT id FROM kl_species_lists WHERE key = 'vp'"))).scalar()
    masai = (await s.execute(text("SELECT id FROM kl_taxa WHERE latin = 'Struthio camelus'"))).scalar()
    # navne, * (SU) og placering
    await kl_admin.save_entry(s, vp, masai, {"kategorier": [], "sjaeldenhed": "su", "dansk": "Struds", "engelsk": "Common Ostrich", "placering": 3})
    rows = (await kl_species.species_list(s, "vp"))["rows"]
    assert rows[2]["id"] == masai and rows[2]["navn"] == "Struds" and rows[2]["su"] and rows[2]["engelsk"] == "Common Ostrich"
    assert [r["sort"] for r in rows[:5]] == [1, 2, 3, 4, 5]  # listen er nummereret fortløbende efter flytningen
    # en art, der ikke findes i World Bird List (fx en ny split), og som får plads nr. 1
    tid = await kl_admin.new_taxon_entry(s, vp, {"latin": "Testus splittus", "dansk": "Splitfugl", "sjaeldenhed": "su", "placering": 1})
    rows = (await kl_species.species_list(s, "vp"))["rows"]
    assert rows[0]["id"] == tid and rows[0]["navn"] == "Splitfugl"
    assert await kl_admin.new_taxon_entry(s, vp, {"latin": "testus  splittus", "engelsk": "Split Bird"}) == tid  # samme latinske navn = samme art
    with pytest.raises(krydslister.KlError):
        await kl_admin.new_taxon_entry(s, vp, {"latin": "x"})
    with pytest.raises(krydslister.KlError):
        await kl_admin.new_taxon_entry(s, vp, {"latin": "Testus nomen"})  # navn mangler
    # CSV-importen retter også navnene
    csv = (await kl_admin.export_csv(s, vp)).replace(";Splitfugl;", ";Nyt navn;")
    diff = await kl_admin.import_diff(s, vp, csv.encode(), apply=True)
    assert [a["latin"] for a in diff["aendrede"]] == ["Testus splittus"]
    assert (await s.execute(text("SELECT dansk FROM kl_taxa WHERE id = :t"), {"t": tid})).scalar() == "Nyt navn"
    # en særskilt artsliste til Falsk VP som kopi af VP-listen
    falsk = await kl_admin.save_species_list(s, {"key": "falskvp", "navn": "Falsk VP-listen", "kopier_fra": vp})
    n = lambda lid: s.execute(text("SELECT count(*) FROM kl_species_list_entries WHERE list_id = :l"), {"l": lid})  # noqa: E731
    assert (await n(falsk.id)).scalar() == (await n(vp)).scalar() > 1000


async def test_kun_godkendte_su_fund_giver_automatiske_kryds(s):
    """Et SU-fund tæller kun automatisk, når SU har godkendt det (DOFbasens kvalitet 2); ellers kan deltageren selv krydse arten af."""
    from portal.models import User

    await s.execute(text("INSERT INTO observations (obsid, dato, artnr, antal, loknr, obserkode, hemmelig, kategori, afdelinger, kvalitet) VALUES "
                         "(21, :d, 104, 1, 1, 'A2', 0, 'SU', '{5}', 1), (22, :d, 102, 1, 1, 'A2', 0, 'SU', '{5}', 2), (23, :d, 105, 1, 1, 'A2', 0, 'SU', '{5}', 2)"),
                    {"d": date(Y, 9, 1)})
    await s.commit()
    await krydslister.seed(s)
    await krydslister.refresh_auto(s)
    a2 = {r["navn"] for r in (await krydslister.load_kryds(s, "dk"))["rows"] if r["kode"] == "A2"}
    assert "Agerhøne" in a2  # godkendt SU-fund
    assert "Fasan" not in a2  # ikke vurderet endnu
    assert "Klyde" not in a2  # godkendt, men kategori D tæller ikke
    fasan = (await s.execute(text("SELECT id FROM kl_taxa WHERE latin = 'Phasianus colchicus'"))).scalar()
    await krydslister.add_kryds(s, await s.get(User, 3), liste="dk", taxon_id=fasan, dato=date(Y, 9, 1), lokalitet="Sted A", land="", note="", privileged=False)
    assert "Fasan" in {r["navn"] for r in (await krydslister.load_kryds(s, "dk"))["rows"] if r["kode"] == "A2"}  # selv skrevet på


def avilist(n=60, rename=False, new=True) -> bytes:
    lines = ["Sequence,Taxon_rank,Scientific_name,English_name_AviList,Order,Family,AviList_ID"]
    lines.append("1,species,Lyrurus tetrix,Black Grouse,GALLIFORMES,Phasianidae,AV1")
    lines.append(f"2,species,{'Perdix hodgsoniae' if rename else 'Perdix perdix'},Grey Partridge,GALLIFORMES,Phasianidae,AV2")
    lines.append("3,genus,Perdix,,GALLIFORMES,Phasianidae,AVG")  # slægter ignoreres
    for i in range(4, n):
        lines.append(f"{i},species,Testus arten{i},Test Bird {i},PASSERIFORMES,Testidae,AVT{i}")
    if new:
        lines.append(f"{n},subspecies,Lyrurus tetrix tetrix,,GALLIFORMES,Phasianidae,AV1S")
    return "\n".join(lines).encode()


async def test_avilist_diff_og_anvend(s):
    from portal.models import User

    await krydslister.seed(s)
    mod = await s.get(User, 4)
    before = (await s.execute(text("SELECT count(*) FROM kl_taxa"))).scalar()
    imp = await kl_admin.avilist_diff(s, mod, avilist(), "avilist.csv", "AviList 2025")
    d = imp.diff
    assert d["antal_fil"] == 58 + 0 or d["antal_fil"] >= 57
    assert d["antal_nye"] >= 55 and d["antal_forsvundet"] > 1000  # showcasens arter findes ikke i den lille testfil
    assert (await s.execute(text("SELECT count(*) FROM kl_taxa"))).scalar() == before  # diffen skriver intet
    out = await kl_admin.avilist_apply(s, imp.id)
    assert out["nye"] >= 55 and out["opdateret"] >= 1
    ur = (await s.execute(text("SELECT orden, familie, avilist_id, artnr, engelsk FROM kl_taxa WHERE latin = 'Lyrurus tetrix'"))).one()
    assert ur == ("Galliformes", "Phasianidae", "AV1", 101, "Black Grouse")
    assert (await s.execute(text("SELECT count(*) FROM kl_taxa WHERE aktiv"))).scalar() == before + out["nye"]  # intet deaktiveret som standard
    assert all(x["latin"].startswith("Testus") or x["latin"] for x in out["uden_artnr"])
    with pytest.raises(krydslister.KlError):
        await kl_admin.avilist_apply(s, imp.id)  # kun én gang
    # omdøbning: samme AviList-id, nyt latinsk navn (split/lump) → artsrækken følger med, og artslisten peger stadig på den
    imp2 = await kl_admin.avilist_diff(s, mod, avilist(rename=True, new=False), "avilist2.csv", "AviList 2026")
    assert imp2.diff["antal_omdoebt"] == 1 and imp2.diff["omdoebt"][0] == {"id": imp2.diff["omdoebt"][0]["id"], "fra": "Perdix perdix", "til": "Perdix hodgsoniae", "engelsk": "Grey Partridge"}
    await kl_admin.avilist_apply(s, imp2.id)
    assert (await s.execute(text("SELECT count(*) FROM kl_species_list_entries e JOIN kl_taxa t ON t.id = e.taxon_id WHERE t.latin = 'Perdix hodgsoniae'"))).scalar() == 2  # DK + VP
    with pytest.raises(krydslister.KlError):
        await kl_admin.avilist_diff(s, mod, b"Scientific_name\nA b\n", "x.csv", "")
    await kl_admin.link_artnr(s, (await s.execute(text("SELECT id FROM kl_taxa WHERE latin = 'Testus arten4'"))).scalar(), 103)
    assert (await s.execute(text("SELECT artnr FROM kl_taxa WHERE latin = 'Testus arten4'"))).scalar() == 103
    with pytest.raises(krydslister.KlError):
        await kl_admin.link_artnr(s, 1, 99999999)
