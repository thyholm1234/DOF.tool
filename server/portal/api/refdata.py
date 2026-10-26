"""Moderation → Arter: arterne selv, bemærk-tærskler pr. lokalafdeling og fænologivinduer.

DOFbasens referencedata hentes jævnligt og overskriver tabellerne; ændringer herfra gemmes derfor også i
reference_edits/species_edits og lægges oven på igen efter hver hentning (dof/reference.py). Alt skrives i observationsgruppen, hvor
referencedata hører hjemme, og kopieres derefter til brugergruppen. Ændringerne gælder nye og ændrede observationer –
eksisterende observationer beholder deres kategori, til de hentes igen.

SU- og SUB-arter er altid bemærkelsesværdige (klassifikationen har forrang), så de vises ikke og kan ikke tilføjes her."""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_perm, require_user
from portal.cache import bump
from portal.db import ObsSession
from portal.dbops import sync_reference
from portal.dof.reference import SPECIES_FIELDS, put_threshold, put_windows
from portal.models import ReferenceEdit, Species, SpeciesEdit, User
from portal.services.auth import can

router = APIRouter(prefix="/api/admin", tags=["admin"])
perm_arter = require_perm("arter")
DDMM = re.compile(r"^(\d{2})-(\d{2})$")


def _valid_ddmm(v: str) -> str:
    m = DDMM.match(v or "")
    try:
        if not m:
            raise ValueError
        date(2024, int(m[2]), int(m[1]))  # skudår, så 29-02 er gyldig
    except ValueError:
        raise ValueError(f"Ugyldig dato {v!r} – brug DD-MM, fx 15-04") from None
    return v


class ThresholdIn(BaseModel):
    artnavn: str = Field(min_length=1, max_length=200)
    antal: int = Field(ge=1, le=1_000_000)


class WindowsIn(BaseModel):
    artnavn: str = Field(min_length=1, max_length=200)
    windows: list[tuple[str, str]] = Field(min_length=1, max_length=4)

    @field_validator("windows")
    @classmethod
    def dates(cls, v: list[tuple[str, str]]) -> list[tuple[str, str]]:
        return [(_valid_ddmm(a), _valid_ddmm(b)) for a, b in v]


NOT_SU = "coalesce(s.klass, 'Alm') NOT IN ('SU', 'SUB')"  # s = species


async def _species_exists(s: AsyncSession, navn: str) -> None:
    klass = (await s.execute(text("SELECT coalesce(klass, 'Alm') FROM species WHERE navn = :n LIMIT 1"), {"n": navn})).scalar()
    if klass is None:
        raise HTTPException(400, f"Arten {navn} findes ikke")
    if klass in ("SU", "SUB"):
        raise HTTPException(400, f"{navn} er en {klass}-art og er altid bemærkelsesværdig")


async def _edit(s: AsyncSession, kind: str, afdeling_id: int, artnavn: str) -> ReferenceEdit:
    """Ændringsposten for (kind, afdeling, art); en ny husker DOFbasens nuværende værdi til fortryd."""
    e = (await s.execute(select(ReferenceEdit).where(ReferenceEdit.kind == kind, ReferenceEdit.afdeling_id == afdeling_id,
                                                     ReferenceEdit.artnavn == artnavn))).scalar()
    if e is None:
        e = ReferenceEdit(kind=kind, afdeling_id=afdeling_id, artnavn=artnavn)
        if kind == "bemaerk":
            e.dof_antal = (await s.execute(text("SELECT antal FROM bemaerk_thresholds WHERE afdeling_id = :a AND artnavn = :n"),
                                           {"a": afdeling_id, "n": artnavn})).scalar()
        else:
            e.dof_windows = [[a, b] for a, b in (await s.execute(text(
                "SELECT fra, til FROM phenology_windows WHERE artnavn = :n ORDER BY id"), {"n": artnavn})).all()] or None
        s.add(e)
    return e


async def _done(s: AsyncSession, table: str) -> None:
    await s.commit()
    await sync_reference((table,))
    await bump("reference")  # klassifikationen læser tabellerne igen


async def _afdeling(s: AsyncSession, afdeling_id: int) -> str:
    navn = (await s.execute(text("SELECT navn FROM afdelinger WHERE id = :a"), {"a": afdeling_id})).scalar()
    if navn is None:
        raise HTTPException(404, "Ukendt lokalafdeling")
    return navn


# ---------------------------------------------------------------- bemærk-tærskler


@router.get("/bemaerk")
async def thresholds(afdeling: int | None = None, user: User = Depends(perm_arter)):
    async with ObsSession() as s:
        afdelinger = [dict(r) for r in (await s.execute(text("SELECT id, navn FROM afdelinger ORDER BY navn"))).mappings()]
        if afdeling is None:
            return {"afdelinger": afdelinger, "rows": [], "removed": []}
        await _afdeling(s, afdeling)
        rows = (await s.execute(text(f"""
            SELECT b.artnavn, b.antal, s.artnr, e.id AS edit_id, e.dof_antal, s.sortering
            FROM bemaerk_thresholds b LEFT JOIN species s ON s.navn = b.artnavn
            LEFT JOIN reference_edits e ON e.kind = 'bemaerk' AND e.afdeling_id = b.afdeling_id AND e.artnavn = b.artnavn
            WHERE b.afdeling_id = :a AND {NOT_SU} ORDER BY s.sortering NULLS LAST, b.artnavn"""), {"a": afdeling})).mappings().all()
        removed = (await s.execute(text(f"""
            SELECT e.id AS edit_id, e.artnavn, e.dof_antal FROM reference_edits e LEFT JOIN species s ON s.navn = e.artnavn
            WHERE e.kind = 'bemaerk' AND e.afdeling_id = :a AND e.removed AND {NOT_SU} ORDER BY e.artnavn"""),
            {"a": afdeling})).mappings().all()
    return {"afdelinger": afdelinger,
            "rows": [{k: r[k] for k in ("artnavn", "antal", "artnr", "edit_id", "dof_antal")} for r in rows],
            "removed": [dict(r) for r in removed]}


@router.put("/bemaerk/{afdeling_id}")
async def set_threshold(afdeling_id: int, body: ThresholdIn, user: User = Depends(perm_arter)):
    """Tilføj eller ret artens tærskel i lokalafdelingen."""
    async with ObsSession() as s:
        await _afdeling(s, afdeling_id)
        await _species_exists(s, body.artnavn)
        e = await _edit(s, "bemaerk", afdeling_id, body.artnavn)
        e.antal, e.removed, e.user_id = body.antal, False, user.id
        await put_threshold(s, afdeling_id, body.artnavn, body.antal)
        await _done(s, "bemaerk_thresholds")
    return {"ok": True}


@router.delete("/bemaerk/{afdeling_id}")
async def remove_threshold(afdeling_id: int, artnavn: str = Query(min_length=1), user: User = Depends(perm_arter)):
    """Arten har ingen tærskel i lokalafdelingen (heller ikke efter næste hentning fra DOFbasen)."""
    async with ObsSession() as s:
        await _afdeling(s, afdeling_id)
        e = await _edit(s, "bemaerk", afdeling_id, artnavn)
        e.antal, e.removed, e.user_id = None, True, user.id
        await put_threshold(s, afdeling_id, artnavn, None)
        await _done(s, "bemaerk_thresholds")
    return {"ok": True}


# ---------------------------------------------------------------- fænologi


@router.get("/phenology")
async def phenology(user: User = Depends(perm_arter)):
    async with ObsSession() as s:
        rows = (await s.execute(text(f"""
            SELECT w.artnavn, s.artnr, s.sortering, json_agg(json_build_array(w.fra, w.til) ORDER BY w.id) AS windows,
                   e.id AS edit_id, e.dof_windows
            FROM phenology_windows w LEFT JOIN species s ON s.navn = w.artnavn
            LEFT JOIN reference_edits e ON e.kind = 'pheno' AND e.afdeling_id = 0 AND e.artnavn = w.artnavn
            WHERE {NOT_SU}
            GROUP BY w.artnavn, s.artnr, s.sortering, e.id, e.dof_windows
            ORDER BY s.sortering NULLS LAST, w.artnavn"""))).mappings().all()
        removed = (await s.execute(text(f"""
            SELECT e.id AS edit_id, e.artnavn, e.dof_windows FROM reference_edits e LEFT JOIN species s ON s.navn = e.artnavn
            WHERE e.kind = 'pheno' AND e.removed AND {NOT_SU} ORDER BY e.artnavn"""))).mappings().all()
    return {"rows": [{k: r[k] for k in ("artnavn", "artnr", "windows", "edit_id", "dof_windows")} for r in rows],
            "removed": [dict(r) for r in removed]}


@router.put("/phenology")
async def set_windows(body: WindowsIn, user: User = Depends(perm_arter)):
    """Tilføj arten til fænologien eller ret dens perioder (erstatter artens perioder)."""
    async with ObsSession() as s:
        await _species_exists(s, body.artnavn)
        e = await _edit(s, "pheno", 0, body.artnavn)
        e.windows, e.removed, e.user_id = [list(w) for w in body.windows], False, user.id
        await put_windows(s, body.artnavn, body.windows)
        await _done(s, "phenology_windows")
    return {"ok": True}


@router.delete("/phenology")
async def remove_windows(artnavn: str = Query(min_length=1), user: User = Depends(perm_arter)):
    """Fjern arten fra fænologien (også efter næste hentning fra DOFbasen)."""
    async with ObsSession() as s:
        e = await _edit(s, "pheno", 0, artnavn)
        e.windows, e.removed, e.user_id = None, True, user.id
        await put_windows(s, artnavn, [])
        await _done(s, "phenology_windows")
    return {"ok": True}


# ---------------------------------------------------------------- fortryd


@router.delete("/reference-edits/{edit_id}")
async def revert(edit_id: int, user: User = Depends(perm_arter)):
    """Fortryd en ændring: DOFbasens værdi sættes ind igen (eller arten forsvinder, hvis DOFbasen ikke har den)."""
    async with ObsSession() as s:
        e = await s.get(ReferenceEdit, edit_id)
        if e is None:
            raise HTTPException(404, "Ændringen findes ikke")
        if e.kind == "bemaerk":
            await put_threshold(s, e.afdeling_id, e.artnavn, e.dof_antal)
        else:
            await put_windows(s, e.artnavn, e.dof_windows or [])
        await s.delete(e)
        await _done(s, "bemaerk_thresholds" if e.kind == "bemaerk" else "phenology_windows")
    return {"ok": True}


# ---------------------------------------------------------------- arterne selv

TYPES = ("art", "underart", "hybrid", "ub", "andre")


async def perm_species_read(user: User = Depends(require_user)) -> User:
    """Artslisten læses også af dem med listekategorierne (fx manuelle fund vælger art herfra)."""
    if not (can(user, "arter") or can(user, "kategorier")):
        raise HTTPException(403, "Kræver adgang til Arter eller Kategorier")
    return user


@router.get("/species")
async def species_list(q: str = "", user: User = Depends(perm_species_read)):
    """Hovedlisten: alle arter (taxa) fra observationsgruppen i systematisk rækkefølge, med de felter portalen har rettet."""
    like = f"%{q.strip()}%"
    async with ObsSession() as s:
        rows = (await s.execute(text("""
            SELECT s.artnr, s.navn, s.latin, s.sortering, s.klass, s.art_type, s.list_artnr, s.excluded, ls.navn AS list_navn,
                   coalesce(e.fields, '{}') AS edited, e.dof
            FROM species s LEFT JOIN species ls ON ls.artnr = s.list_artnr LEFT JOIN species_edits e ON e.artnr = s.artnr
            WHERE :q = '' OR s.navn ILIKE :like OR s.latin ILIKE :like OR CAST(s.artnr AS text) = :q
            ORDER BY s.sortering NULLS LAST, s.artnr
        """), {"q": q.strip(), "like": like})).mappings().all()
    return [dict(r) for r in rows]


class SpeciesIn(BaseModel):
    """Felter der skal ændres (kun dem, der sendes med). Samme værdi som DOFbasens = ændringen fjernes."""
    navn: str | None = Field(None, min_length=1, max_length=200)
    latin: str | None = Field(None, max_length=200)
    sortering: int | None = Field(None, ge=0)
    klass: str | None = Field(None, pattern="^(Alm|SUB|SU)$")
    art_type: str | None = Field(None, pattern="^(art|underart|hybrid|ub|andre)$")
    list_artnr: int | None = None
    excluded: bool | None = None


async def _species_done(s: AsyncSession) -> None:
    await s.commit()
    await sync_reference(("species",))
    for v in ("reference", "lists"):  # klassifikationen og årslisterne læser arterne igen
        await bump(v)


@router.patch("/species/{artnr}")
async def update_species(artnr: int, body: SpeciesIn, user: User = Depends(perm_arter)):
    """Ret en art. Ændringen gemmes i species_edits og lægges oven på DOFbasens artsdata hver gang de hentes."""
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if "navn" in changes and not (changes["navn"] or "").strip():
        raise HTTPException(400, "Arten skal have et navn")
    for k in ("navn", "latin"):
        if k in changes and changes[k] is not None:
            changes[k] = changes[k].strip() or None
    for k in ("klass", "art_type", "excluded"):
        if k in changes and changes[k] is None:
            raise HTTPException(400, f"{k} kan ikke være tom")
    async with ObsSession() as s:
        sp = await s.get(Species, artnr)
        if sp is None:
            raise HTTPException(404, "Arten findes ikke")
        if changes.get("list_artnr") not in (None, artnr) and await s.get(Species, changes["list_artnr"]) is None:
            raise HTTPException(400, "«Tæller som» skal være en art, der findes")
        e = await s.get(SpeciesEdit, artnr)
        if e is None:
            e = SpeciesEdit(artnr=artnr, fields=[], dof={f: getattr(sp, f) for f in SPECIES_FIELDS})
            s.add(e)
        fields = set(e.fields or [])
        for k, v in changes.items():
            setattr(sp, k, v)
            if v == e.dof.get(k):
                fields.discard(k)  # tilbage til DOFbasens værdi
            else:
                fields.add(k)
                setattr(e, k, v)
        if fields:
            e.fields, e.user_id = sorted(fields), user.id
            e.updated_at = func.now()
        else:
            await s.delete(e)
        await _species_done(s)
    return {"ok": True, "edited": sorted(fields)}


@router.delete("/species/{artnr}/edits")
async def revert_species(artnr: int, user: User = Depends(perm_arter)):
    """Fortryd portalens ændringer af en art: DOFbasens værdier fra før første ændring sættes tilbage."""
    async with ObsSession() as s:
        sp, e = await s.get(Species, artnr), await s.get(SpeciesEdit, artnr)
        if sp is None or e is None:
            raise HTTPException(404, "Arten er ikke rettet i portalen")
        for f in e.fields or []:
            setattr(sp, f, e.dof.get(f))
        await s.delete(e)
        await _species_done(s)
    return {"ok": True}
