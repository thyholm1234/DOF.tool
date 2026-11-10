"""Krydslister → Admin: listetyper, artslister (med CSV-eksport/-import med diff), World Bird List (AviList-import med diff) og godkendelse af kryds."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump, worker_command
from portal.models import KlImport, KlKryds, KlListDef, KlSpeciesList, KlSpeciesListEntry, KlTaxon, User
from portal.services import krydslister, moderation
from portal.services.krydslister import KlError, SJAELDENHED, VERSION
from portal.timeutil import TZ

PARAMS = (None, "year", "month", "afdeling", "kommune")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


def _def_out(d: KlListDef) -> dict:
    return {"id": d.id, "key": d.key, "navn": d.navn, "ref_list_id": d.ref_list_id, "auto": d.auto, "manuel": d.manuel, "param": d.param, "regler": d.regler or {},
            "sort": d.sort, "aktiv": d.aktiv, "beskrivelse_md": d.beskrivelse_md}


async def list_defs(session: AsyncSession) -> dict:
    rows = (await session.execute(select(KlListDef).order_by(KlListDef.sort, KlListDef.id))).scalars().all()
    lists = [dict(r) for r in (await session.execute(text("SELECT id, key, navn FROM kl_species_lists ORDER BY sort, id"))).mappings()]
    return {"defs": [_def_out(d) for d in rows], "artslister": lists}


async def save_def(session: AsyncSession, data: dict, did: int | None = None) -> KlListDef:
    """Opret/ret en listetype. En ændring sætter en genberegning af de automatiske lister i gang (worker: kryds_recompute)."""
    key = str(data.get("key") or "").strip().lower()
    if not KEY_RE.match(key):
        raise KlError(400, "Nøglen skal være små bogstaver/tal/understreg og starte med et bogstav (højst 24 tegn)")
    if data.get("param") not in PARAMS:
        raise KlError(400, "Ukendt parameter")
    navn = str(data.get("navn") or "").strip()
    if not navn:
        raise KlError(400, "Navn mangler")
    if data.get("param") and "{p}" not in navn:
        raise KlError(400, "Navnet skal indeholde {p}, når listen har en parameter")
    if not data.get("ref_list_id") or (await session.get(KlSpeciesList, data["ref_list_id"])) is None:
        raise KlError(400, "Vælg en referenceliste")
    regler = data.get("regler") or {}
    cats = regler.get("kategorier")
    if cats is not None and (not isinstance(cats, list) or any(c not in list("ABCDEF") for c in cats)):
        raise KlError(400, "Kategorier skal være bogstaver A–F")
    d = await session.get(KlListDef, did) if did else None
    if d is None:
        if (await session.execute(select(KlListDef.id).where(KlListDef.key == key))).first():
            raise KlError(400, f"Nøglen {key} findes allerede")
        d = KlListDef(key=key)
        session.add(d)
    elif d.key != key:
        raise KlError(400, "Nøglen kan ikke ændres")
    d.navn, d.ref_list_id, d.auto, d.manuel, d.param = navn[:160], data["ref_list_id"], bool(data.get("auto")), bool(data.get("manuel")), data.get("param")
    d.regler, d.sort, d.aktiv, d.beskrivelse_md = regler, int(data.get("sort") or 0), bool(data.get("aktiv", True)), data.get("beskrivelse_md") or None
    await session.commit()
    await bump(VERSION)
    if d.auto:
        await worker_command("kryds_recompute")
    return d


async def delete_def(session: AsyncSession, did: int) -> None:
    d = await session.get(KlListDef, did)
    if d is None:
        raise KlError(404, "Listetypen findes ikke")
    await session.execute(text("DELETE FROM kl_auto_lists WHERE liste = :k OR liste LIKE :p"), {"k": d.key, "p": d.key + ":%"})
    await session.delete(d)
    await session.commit()
    await bump(VERSION)


# ---------------------------------------------------------------- artslister

async def save_species_list(session: AsyncSession, data: dict, lid: int | None = None) -> KlSpeciesList:
    key = str(data.get("key") or "").strip().lower()
    if not KEY_RE.match(key):
        raise KlError(400, "Ugyldig nøgle")
    if not str(data.get("navn") or "").strip():
        raise KlError(400, "Navn mangler")
    l = await session.get(KlSpeciesList, lid) if lid else None
    if l is None:
        if (await session.execute(select(KlSpeciesList.id).where(KlSpeciesList.key == key))).first():
            raise KlError(400, f"Nøglen {key} findes allerede")
        l = KlSpeciesList(key=key)
        session.add(l)
    l.navn, l.beskrivelse, l.sort = data["navn"].strip()[:120], data.get("beskrivelse") or None, int(data.get("sort") or 0)
    if not lid and data.get("kopier_fra"):  # ny liste som kopi af en anden (fx en særskilt artsliste til Falsk VP)
        await session.flush()
        await session.execute(text("""INSERT INTO kl_species_list_entries (list_id, taxon_id, kategorier, sjaeldenhed, sort)
            SELECT :ny, taxon_id, kategorier, sjaeldenhed, sort FROM kl_species_list_entries WHERE list_id = :fra"""), {"ny": l.id, "fra": int(data["kopier_fra"])})
    await session.commit()
    await bump(VERSION)
    return l


def _entry_fields(data: dict) -> tuple[list[str], str]:
    cats = [c for c in (data.get("kategorier") or []) if c in list("ABCDEF")]
    sj = data.get("sjaeldenhed") or "almindelig"
    if sj not in SJAELDENHED:
        raise KlError(400, "Ukendt sjældenhed")
    return sorted(set(cats)), sj


async def save_entry(session: AsyncSession, list_id: int, taxon_id: int, data: dict) -> None:
    if await session.get(KlSpeciesList, list_id) is None or await session.get(KlTaxon, taxon_id) is None:
        raise KlError(404, "Listen eller arten findes ikke")
    cats, sj = _entry_fields(data)
    e = await session.get(KlSpeciesListEntry, (list_id, taxon_id))
    if e is None:
        mx = (await session.execute(text("SELECT coalesce(max(sort), 0) FROM kl_species_list_entries WHERE list_id = :l"), {"l": list_id})).scalar() or 0
        e = KlSpeciesListEntry(list_id=list_id, taxon_id=taxon_id, sort=int(data.get("sort") if data.get("sort") is not None else mx + 1))
        session.add(e)
    e.kategorier, e.sjaeldenhed = cats, sj
    if data.get("sort") is not None:
        e.sort = int(data["sort"])
    _names(await session.get(KlTaxon, taxon_id), data)
    await session.commit()
    if data.get("placering"):
        await move_entry(session, list_id, taxon_id, int(data["placering"]))
    await bump(VERSION)


def _names(t: KlTaxon, data: dict) -> bool:
    """Ret artens danske/engelske navn (gælder arten overalt, fx på både DK- og VP-listen). Returnerer True, hvis noget blev ændret."""
    changed = False
    for field in ("dansk", "engelsk"):
        if field in data and data[field] is not None:
            v = str(data[field]).strip()[:200] or None
            if v != getattr(t, field):
                setattr(t, field, v)
                changed = True
    return changed


async def move_entry(session: AsyncSession, list_id: int, taxon_id: int, position: int) -> None:
    """Flyt arten til plads nr. position (1 = først) og nummerér listen fortløbende."""
    ids = [r[0] for r in (await session.execute(text("SELECT taxon_id FROM kl_species_list_entries WHERE list_id = :l ORDER BY sort, taxon_id"), {"l": list_id})).all()]
    if taxon_id not in ids:
        raise KlError(404, "Arten er ikke på listen")
    ids.remove(taxon_id)
    ids.insert(max(0, min(len(ids), position - 1)), taxon_id)
    await session.execute(text("UPDATE kl_species_list_entries SET sort = :s WHERE list_id = :l AND taxon_id = :t"),
                          [{"s": i, "l": list_id, "t": t} for i, t in enumerate(ids, start=1)])
    await session.commit()
    await bump(VERSION)


async def new_taxon_entry(session: AsyncSession, list_id: int, data: dict) -> int:
    """Tilføj en art, der ikke findes i World Bird List (fx en ny split på VP-listen): opretter arten og sætter den på listen. Findes det latinske navn
    allerede, bruges den eksisterende art. Returnerer artens id."""
    if await session.get(KlSpeciesList, list_id) is None:
        raise KlError(404, "Listen findes ikke")
    latin = re.sub(r"\s+", " ", str(data.get("latin") or "")).strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z .()×-]{2,199}", latin):
        raise KlError(400, "Skriv et gyldigt latinsk navn")
    tid = (await session.execute(text("SELECT id FROM kl_taxa WHERE lower(latin) = lower(:la) ORDER BY aktiv DESC, id LIMIT 1"), {"la": latin})).scalar()
    t = await session.get(KlTaxon, tid) if tid else None
    if t is None:
        mx = (await session.execute(text("SELECT coalesce(max(sort), 0) FROM kl_taxa"))).scalar() or 0
        t = KlTaxon(latin=latin, rang="art", version="manuel", sort=mx + 1, aktiv=True)
        sp = (await session.execute(text("SELECT artnr FROM species WHERE lower(latin) = lower(:la) ORDER BY art_type = 'art' DESC LIMIT 1"), {"la": latin})).scalar()
        t.artnr = sp
        session.add(t)
        await session.flush()
    elif not t.aktiv:
        t.aktiv = True
    _names(t, data)
    if not (t.dansk or t.engelsk):
        raise KlError(400, "Skriv et dansk eller engelsk navn")
    await session.commit()
    await save_entry(session, list_id, t.id, {"kategorier": data.get("kategorier") or [], "sjaeldenhed": data.get("sjaeldenhed") or "almindelig",
                                             "placering": data.get("placering")})
    return t.id


async def remove_entry(session: AsyncSession, list_id: int, taxon_id: int) -> None:
    await session.execute(text("DELETE FROM kl_species_list_entries WHERE list_id = :l AND taxon_id = :t"), {"l": list_id, "t": taxon_id})
    await session.commit()
    await bump(VERSION)


CSV_COLS = ["latin", "dansk", "engelsk", "kategorier", "sjaeldenhed", "sort"]


async def export_csv(session: AsyncSession, list_id: int) -> str:
    rows = (await session.execute(text("""
        SELECT t.latin, t.dansk, t.engelsk, e.kategorier, e.sjaeldenhed, e.sort FROM kl_species_list_entries e JOIN kl_taxa t ON t.id = e.taxon_id WHERE e.list_id = :l ORDER BY e.sort, t.id"""),
                                  {"l": list_id})).all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(CSV_COLS)
    for r in rows:
        w.writerow([r[0], r[1] or "", r[2] or "", ",".join(r[3] or []), r[4], r[5]])
    return "﻿" + buf.getvalue()


def _read_csv(raw: bytes) -> list[dict]:
    txt = raw.decode("utf-8-sig", "replace")
    first = txt.splitlines()[0] if txt.strip() else ""
    delim = max(";\t,", key=first.count)
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(txt), delimiter=delim)]


async def import_diff(session: AsyncSession, list_id: int, raw: bytes, apply: bool = False) -> dict:
    """Sammenlign en CSV (eksport → ret → import) med artslisten. apply=True skriver ændringerne (nye/ændrede; slettes aldrig automatisk)."""
    rows = _read_csv(raw)
    if not rows or "latin" not in rows[0]:
        raise KlError(400, "CSV-filen skal have en kolonne «latin» (brug eksporten som skabelon)")
    cur = {r["latin"].lower(): r for r in (await session.execute(text("""
        SELECT t.id, t.latin, t.dansk, t.engelsk, e.kategorier, e.sjaeldenhed, e.sort FROM kl_species_list_entries e JOIN kl_taxa t ON t.id = e.taxon_id WHERE e.list_id = :l"""), {"l": list_id})).mappings()}
    taxa = {r["latin"].lower(): r["id"] for r in (await session.execute(text("SELECT id, latin FROM kl_taxa WHERE aktiv"))).mappings()}
    diff = {"nye": [], "aendrede": [], "ukendte": [], "uaendret": 0, "fejl": []}
    seen = set()
    for i, r in enumerate(rows, start=2):
        key = r["latin"].lower()
        if not key:
            continue
        seen.add(key)
        try:
            cats, sj = _entry_fields({"kategorier": [c for c in re.split(r"[,; ]+", r.get("kategorier", "").upper()) if c], "sjaeldenhed": r.get("sjaeldenhed") or "almindelig"})
        except KlError as exc:
            diff["fejl"].append(f"Række {i}: {exc.message}")
            continue
        sort = int(r["sort"]) if r.get("sort", "").lstrip("-").isdigit() else None
        if key not in taxa:
            diff["ukendte"].append(r["latin"])
        elif key not in cur:
            diff["nye"].append({"latin": r["latin"], "kategorier": cats, "sjaeldenhed": sj})
            if apply:
                await save_entry(session, list_id, taxa[key], {"kategorier": cats, "sjaeldenhed": sj, "sort": sort})
        else:
            c = cur[key]
            # Navnekolonnerne (dansk, engelsk) retter artens navn, når de er udfyldt; tomme felter rører ikke navnet
            names = {f: r[f] for f in ("dansk", "engelsk") if r.get(f) and r[f] != (c[f] or "")}
            if sorted(c["kategorier"] or []) != cats or c["sjaeldenhed"] != sj or (sort is not None and sort != c["sort"]) or names:
                diff["aendrede"].append({"latin": r["latin"], "foer": {"kategorier": c["kategorier"], "sjaeldenhed": c["sjaeldenhed"], "sort": c["sort"], **{f: c[f] for f in names}},
                                         "efter": {"kategorier": cats, "sjaeldenhed": sj, "sort": sort, **names}})
                if apply:
                    await save_entry(session, list_id, c["id"], {"kategorier": cats, "sjaeldenhed": sj, "sort": sort, **names})
            else:
                diff["uaendret"] += 1
    diff["mangler_i_filen"] = [cur[k]["latin"] for k in cur if k not in seen]
    diff["anvendt"] = apply
    return diff


# ---------------------------------------------------------------- World Bird List (AviList)

ALIASES = {
    "latin": ("scientific name", "scientific_name", "scientificname", "latin", "taxon", "latin name"),
    "engelsk": ("english name", "english_name", "english_name_avilist", "english", "common name", "english name (avilist)"),
    "rang": ("taxon rank", "taxon_rank", "rank", "category"),
    "orden": ("order", "orden"),
    "familie": ("family", "familie", "family (latin)"),
    "avilist_id": ("avilist id", "avilist_id", "avilistid", "avibase id", "id"),
    "dansk": ("danish name", "dansk", "dansk navn"),
    "sort": ("sequence", "sort", "seq", "avilist sequence"),
}


def _col(row: dict, field: str) -> str:
    for a in ALIASES[field]:
        if a in row and row[a]:
            return row[a]
    return ""


def parse_avilist(raw: bytes) -> list[dict]:
    out = []
    for i, r in enumerate(_read_csv(raw)):
        latin = _col(r, "latin")
        if not latin or len(latin.split()) < 2:
            continue  # slægter, familier og lignende
        rang = _col(r, "rang").lower()
        if rang and not any(w in rang for w in ("species", "art", "subspecies", "underart", "ssp")):
            continue
        sub = "sub" in rang or "under" in rang or len(latin.split()) >= 3
        out.append({"latin": latin, "engelsk": _col(r, "engelsk") or None, "rang": "underart" if sub else "art", "orden": _col(r, "orden").title() or None,
                    "familie": _col(r, "familie") or None, "avilist_id": _col(r, "avilist_id") or None, "dansk": _col(r, "dansk") or None,
                    "sort": int(_col(r, "sort")) if _col(r, "sort").isdigit() else i})
    return out


async def avilist_diff(session: AsyncSession, user: User, raw: bytes, filename: str, version: str) -> KlImport:
    """Læs AviList-CSV'en og vis, hvad der er nyt, omdøbt (splits/lumps: samme avilist_id, nyt latinsk navn), ændret og forsvundet – før noget skrives."""
    rows = parse_avilist(raw)
    if len(rows) < 50:
        raise KlError(400, "Filen ligner ikke AviList (færre end 50 arter fundet). Kolonner: Scientific name, English name, Order, Family …")
    cur = [dict(r) for r in (await session.execute(text("SELECT id, latin, engelsk, orden, familie, avilist_id, artnr, aktiv, rang FROM kl_taxa"))).mappings()]
    by_id = {c["avilist_id"]: c for c in cur if c["avilist_id"]}
    by_latin = {c["latin"].lower(): c for c in cur}
    new, renamed, changed, matched = [], [], [], set()
    for r in rows:
        c = by_id.get(r["avilist_id"]) if r["avilist_id"] else None
        c = c or by_latin.get(r["latin"].lower())
        if c is None:
            new.append({"latin": r["latin"], "engelsk": r["engelsk"], "familie": r["familie"]})
            continue
        matched.add(c["id"])
        if c["latin"].lower() != r["latin"].lower():
            renamed.append({"id": c["id"], "fra": c["latin"], "til": r["latin"], "engelsk": r["engelsk"]})
        elif (c["engelsk"] or None) != r["engelsk"] or (c["familie"] or None) != r["familie"] or (c["orden"] or None) != r["orden"]:
            changed.append({"id": c["id"], "latin": c["latin"], "foer": {"engelsk": c["engelsk"], "familie": c["familie"]}, "efter": {"engelsk": r["engelsk"], "familie": r["familie"]}})
    gone = [{"id": c["id"], "latin": c["latin"]} for c in cur if c["id"] not in matched and c["aktiv"]]
    diff = {"antal_fil": len(rows), "nye": new[:2000], "antal_nye": len(new), "omdoebt": renamed[:2000], "antal_omdoebt": len(renamed), "aendret": changed[:2000],
            "antal_aendret": len(changed), "forsvundet": gone[:2000], "antal_forsvundet": len(gone)}
    imp = KlImport(filename=filename[:300], version=version[:40] or datetime.now(TZ).strftime("AviList %Y"), rows=rows, diff=diff, created_by=user.id)
    session.add(imp)
    await session.commit()
    return imp


async def avilist_apply(session: AsyncSession, imp_id: int, *, deactivate_gone: bool = False) -> dict:
    """Anvend en AviList-diff i én transaktion. artnr kobles via latinsk navn; arter der ikke kunne kobles, returneres til manuel kobling."""
    imp = await session.get(KlImport, imp_id)
    if imp is None or imp.status != "diff":
        raise KlError(404, "Importen findes ikke, eller den er allerede anvendt")
    latin_art = {r[0].lower(): r[1] for r in (await session.execute(text(
        "SELECT latin, artnr FROM species WHERE latin IS NOT NULL AND NOT excluded AND art_type IN ('art', 'underart') ORDER BY art_type = 'art' DESC, artnr DESC"))).all() if r[0]}
    cur = {c.id: c for c in (await session.execute(select(KlTaxon))).scalars()}
    by_id = {c.avilist_id: c for c in cur.values() if c.avilist_id}
    by_latin = {c.latin.lower(): c for c in cur.values()}
    seen, n_new, n_ren, n_chg = set(), 0, 0, 0
    for r in imp.rows:
        c = (by_id.get(r["avilist_id"]) if r["avilist_id"] else None) or by_latin.get(r["latin"].lower())
        if c is None:
            c = KlTaxon(latin=r["latin"], version=imp.version)
            session.add(c)
            n_new += 1
        elif c.latin.lower() != r["latin"].lower():
            n_ren += 1
        else:
            n_chg += 1
        c.latin, c.engelsk, c.rang, c.orden, c.familie, c.avilist_id, c.sort, c.aktiv = r["latin"], r["engelsk"] or c.engelsk, r["rang"], r["orden"], r["familie"], r["avilist_id"] or c.avilist_id, r["sort"], True
        c.dansk = c.dansk or r.get("dansk")
        c.artnr = c.artnr or latin_art.get(r["latin"].lower())
        seen.add(c.id)
    await session.flush()
    gone = 0
    if deactivate_gone:
        for c in cur.values():
            if c.id not in seen and c.aktiv:
                c.aktiv = False
                gone += 1
    imp.status, imp.applied_at = "anvendt", datetime.now(TZ)
    await session.commit()
    await bump(VERSION)
    return {"nye": n_new, "omdoebt": n_ren, "opdateret": n_chg, "deaktiveret": gone, "uden_artnr": await taxa_without_artnr(session)}


async def taxa_without_artnr(session: AsyncSession) -> list[dict]:
    return [dict(r) for r in (await session.execute(text("SELECT id, latin, dansk, engelsk FROM kl_taxa WHERE aktiv AND artnr IS NULL AND rang = 'art' ORDER BY sort LIMIT 500"))).mappings()]


async def link_artnr(session: AsyncSession, taxon_id: int, artnr: int | None) -> None:
    t = await session.get(KlTaxon, taxon_id)
    if t is None:
        raise KlError(404, "Arten findes ikke")
    if artnr is not None and not (await session.execute(text("SELECT 1 FROM species WHERE artnr = :a"), {"a": artnr})).first():
        raise KlError(400, "Ukendt artnr i DOFbasen")
    t.artnr = artnr
    await session.commit()
    await bump(VERSION)


async def edit_taxon(session: AsyncSession, taxon_id: int, data: dict) -> None:
    t = await session.get(KlTaxon, taxon_id)
    if t is None:
        raise KlError(404, "Arten findes ikke")
    if "dansk" in data:
        t.dansk = (data["dansk"] or "").strip()[:200] or None
    if "artnr" in data:
        await link_artnr(session, taxon_id, data["artnr"])
        return
    await session.commit()
    await bump(VERSION)


async def link_netfugl_ids(session: AsyncSession) -> int:
    """Netfugls artsnummer (fra fotoarkivet: gal_birds.netfugl_art_id + latinsk navn) → kl_taxa.netfugl_id, og taxon_id på fotos (worker: photos_link_taxa)."""
    n = (await session.execute(text("""
        UPDATE kl_taxa t SET netfugl_id = s.nid FROM (SELECT DISTINCT ON (lower(b.latin)) lower(b.latin) AS lat, b.netfugl_art_id AS nid FROM gal_birds b
            WHERE b.netfugl_art_id IS NOT NULL AND b.latin IS NOT NULL ORDER BY lower(b.latin), b.netfugl_art_id) s
        WHERE lower(t.latin) = s.lat AND t.netfugl_id IS NULL AND NOT EXISTS (SELECT 1 FROM kl_taxa x WHERE x.netfugl_id = s.nid)"""))).rowcount
    await session.execute(text("""
        UPDATE gal_birds b SET taxon_id = t.id FROM kl_taxa t WHERE b.taxon_id IS NULL AND (lower(b.latin) = lower(t.latin) OR b.netfugl_art_id = t.netfugl_id)
          AND t.aktiv"""))
    await session.commit()
    return n


# ---------------------------------------------------------------- godkendelse af kryds

async def approval_queue(session: AsyncSession, status: str = "afventer") -> list[dict]:
    rows = (await session.execute(text("""
        SELECT k.id, k.obserkode, k.liste, k.navn, k.latin, k.dato, k.lokalitet, k.land, k.note, k.status, k.created_at, u.id AS user_id, u.navn AS bruger
        FROM kl_kryds k LEFT JOIN users u ON u.obserkode = k.obserkode WHERE k.status = :s AND NOT k.hidden ORDER BY k.created_at DESC LIMIT 200"""), {"s": status})).mappings().all()
    return [{**dict(r), "dato": r["dato"].isoformat() if r["dato"] else None, "created_at": r["created_at"].isoformat()} for r in rows]


async def decide(session: AsyncSession, mod: User, kid: int, approve: bool, reason: str = "") -> None:
    k = await session.get(KlKryds, kid)
    if k is None or k.status != "afventer":
        raise KlError(404, "Krydset venter ikke på godkendelse")
    k.status, k.behandlet_af = ("ok" if approve else "afvist"), mod.id
    await session.commit()
    await bump(VERSION)
    owner = (await session.execute(select(User).where(User.obserkode == k.obserkode))).scalar_one_or_none()
    if owner is not None and owner.id != mod.id:
        await moderation.send_message(session, target_id=owner.id, mod_id=mod.id, subject=f"Dit kryds på {k.liste}: {k.navn} er {'godkendt' if approve else 'afvist'}",
                                      body=(reason.strip() or ("Krydset er godkendt og tæller nu på listen." if approve else "Krydset blev ikke godkendt.")), link="#/lister/mine")
