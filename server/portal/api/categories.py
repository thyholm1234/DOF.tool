"""Listekategorier (moderator og op): artens kategori over tid og overstyring af serier (lokalitet × art)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_perm
from portal.cache import bump, kv
from portal.db import get_session
from portal.models import CategoryOverride, ManualFind, Species, SpeciesCategory, User
from portal.services import activity, categories, danish_list, manual_finds
from portal.services.auth import role_at_least

router = APIRouter(prefix="/api/categories", tags=["categories"])
moderator = require_perm("kategorier")
KAT = "^[A-F]$"
PORTAL_ARTNR = 900000  # arter oprettet i portalen uden DOFbasen-artsnummer


@router.get("/species")
async def species(q: str = "", user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    """Søgning i listearter. Uden søgning: arter der ikke er kategori A i dag, eller som har overstyrede serier."""
    rows = (await db.execute(text("""
        SELECT s.artnr, s.navn, s.latin, s.klass,
               bird_category(s.artnr, s.artnr, NULL, current_date) AS kategori,
               EXISTS (SELECT 1 FROM species_categories c WHERE c.artnr = s.artnr AND c.removed_at IS NULL) AS own,
               (SELECT count(*) FROM category_overrides v WHERE v.artnr = s.artnr AND v.removed_at IS NULL) AS n_overrides
        FROM species s
        WHERE s.list_artnr = s.artnr AND (
            CASE WHEN :q = '' THEN bird_category(s.artnr, s.artnr, NULL, current_date) NOT IN ('A')
                   OR s.artnr IN (SELECT artnr FROM category_overrides WHERE removed_at IS NULL)
                 ELSE s.navn ILIKE :like OR s.latin ILIKE :like OR CAST(s.artnr AS text) = :q END)
        ORDER BY s.sortering NULLS LAST, s.artnr LIMIT 300
    """), {"q": q.strip(), "like": f"%{q.strip()}%"})).mappings().all()
    return [dict(r) for r in rows]


def _row(r) -> dict:
    return {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in dict(r).items()}


async def _with_names(db: AsyncSession, rows, *fields: str) -> list[dict]:
    """Erstat bruger-id'er (created_by, removed_by …) med navne. Tabellerne ligger i observationsgruppen, brugerne i brugergruppen."""
    rows = [dict(r) for r in rows]
    ids = {r[f] for r in rows for f in fields if r.get(f) is not None}
    names = dict((await db.execute(text("SELECT id, navn FROM users WHERE id = ANY(:i)"), {"i": list(ids)})).all()) if ids else {}
    for r in rows:
        for f in fields:
            r[f] = names.get(r[f])
    return rows


@router.get("/species/{artnr}")
async def species_detail(artnr: int, year: int | None = None, user: User = Depends(moderator),
                         db: AsyncSession = Depends(get_session)):
    sp = await db.get(Species, artnr)
    if sp is None:
        raise HTTPException(404, "Arten findes ikke")
    if sp.list_artnr and sp.list_artnr != artnr:
        artnr = sp.list_artnr  # underarter vises under listearten
        sp = await db.get(Species, artnr)
    periods = (await db.execute(text("""
        SELECT c.id, c.kategori, c.valid_from, c.note, c.created_at, c.removed_at, c.created_by, c.removed_by
        FROM species_categories c WHERE c.artnr = :a ORDER BY c.valid_from NULLS FIRST, c.id
    """), {"a": artnr})).mappings().all()
    overrides = (await db.execute(text("""
        SELECT v.id, v.loknr, l.navn AS lok, v.date_from, v.date_to, v.kategori, v.note, v.created_at, v.removed_at,
               v.created_by, v.removed_by
        FROM category_overrides v LEFT JOIN locations l ON l.loknr = v.loknr
        WHERE v.artnr = :a ORDER BY v.date_from DESC, v.id DESC
    """), {"a": artnr})).mappings().all()
    years = (await db.execute(text("""
        SELECT DISTINCT extract(year FROM o.dato)::int FROM observations o JOIN species s ON s.artnr = o.artnr
        WHERE s.list_artnr = :a ORDER BY 1 DESC
    """), {"a": artnr})).scalars().all()
    return {"artnr": artnr, "navn": sp.navn, "latin": sp.latin, "klass": sp.klass,
            "current": await categories.species_category(db, artnr), "categories": categories.CATEGORIES,
            "periods": [_row(r) for r in await _with_names(db, periods, "created_by", "removed_by")],
            "overrides": [_row(r) for r in await _with_names(db, overrides, "created_by", "removed_by")], "years": years,
            "year": year, "series": await categories.series(db, artnr, year), "gap_days": categories.SERIES_GAP_DAYS}


def _report(p: dict) -> dict:
    """Rapport til brugerfladen: tal og de første eksempler (hele listen er for lang)."""
    return {"sources": p["sources"], "disagreements": p["disagreements"], "matched": p["matched"], "of": p["of"], "summary": p["summary"],
            "unchanged": p["unchanged"],
            "conflicts": p["conflicts"][:100], "unmatched": [u for u in p["unmatched"] if u["kategori"] in "ABCDF"][:100],
            "n_unmatched": len(p["unmatched"]), "bracket_off_list": p["bracket_off_list"],
            "changes": [{k: c[k] for k in ("navn", "latin", "kategori")} | {"valid_from": c["valid_from"].isoformat() if c["valid_from"] else None}
                        for c in p["changes"][:300]], "n_changes": len(p["changes"])}


@router.get("/danish-list")
async def danish_list_preview(user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    """Hvad en import af Den Danske Liste vil ændre (skriver ikke noget)."""
    return _report(await danish_list.plan(db))


@router.post("/danish-list/refresh")
async def danish_list_refresh(user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    """Hent Netfugls DK-liste (ét kald til netfugl.dk) og vis, hvad en import så vil ændre."""
    if not await kv().set("categories:netfugl_rl", "1", nx=True, ex=300):
        raise HTTPException(429, "Netfugl er hentet for et øjeblik siden – vent et par minutter")
    try:
        await danish_list.refresh_netfugl(db)
    except Exception as exc:  # noqa: BLE001 – netværksfejl og ændret sideformat skal ses af brugeren
        await kv().delete("categories:netfugl_rl")
        raise HTTPException(502, f"Kunne ikke hente Netfugls liste: {exc}") from exc
    return _report(await danish_list.plan(db))


@router.post("/danish-list")
async def danish_list_import(user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    """Læg Den Danske Liste ind som kategorier. Rækker oprettet af moderatorer røres ikke; afløste rækker bevares i historikken."""
    report = await danish_list.apply(db, user.id)
    await activity.track(user.id, "moderation", None, {"category": "danish_list", "written": report["written"]})
    return report


class NewSpeciesIn(BaseModel):
    navn: str = Field(min_length=2, max_length=200)
    latin: str | None = Field(None, max_length=200)
    artnr: int | None = Field(None, ge=1, lt=PORTAL_ARTNR)
    kategori: str = Field(pattern=KAT)
    valid_from: date | None = None
    note: str | None = Field(None, max_length=1000)


@router.post("/species")
async def create_species(body: NewSpeciesIn, user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    """Opret en art med kategori. Med DOFbasens artsnummer kobles den på observationerne, når de hentes (importen
    beholder arten, referencedata retter navnet); uden får den et portalnummer og har ingen observationer."""
    navn = body.navn.strip()
    clash = (await db.execute(text("""
        SELECT artnr, navn FROM species WHERE artnr = CAST(:a AS int) OR lower(trim(both '[]' from navn)) = lower(:n) LIMIT 1
    """), {"a": body.artnr, "n": navn.strip("[]")})).first()
    if clash:
        raise HTTPException(409, f"Arten findes allerede: {clash[1]} (nr. {clash[0]})")
    artnr = body.artnr or max(PORTAL_ARTNR, ((await db.execute(text("SELECT max(artnr) FROM species"))).scalar() or 0) + 1)
    db.add(Species(artnr=artnr, navn=navn, latin=(body.latin or "").strip() or None, klass="Alm", art_type="art",
                   list_artnr=artnr, excluded=False))
    await db.flush()
    db.add(SpeciesCategory(artnr=artnr, kategori=body.kategori, valid_from=body.valid_from, created_by=user.id,
                           note=(body.note or "").strip() or ("Oprettet i portalen" if body.artnr else "Oprettet i portalen uden DOFbasen-artsnummer")))
    await db.commit()
    await bump("reference")
    await _changed(user, {"created_species": artnr, "navn": navn, "kategori": body.kategori})
    return {"artnr": artnr}


class PeriodIn(BaseModel):
    kategori: str = Field(pattern=KAT)
    valid_from: date | None = None
    note: str | None = Field(None, max_length=1000)


async def _changed(user: User, what: dict) -> None:
    await bump("lists")
    await activity.track(user.id, "moderation", None, {"category": what})


@router.post("/species/{artnr}/periods")
async def add_period(artnr: int, body: PeriodIn, user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    if await db.get(Species, artnr) is None:
        raise HTTPException(404, "Arten findes ikke")
    # Samme gyldig-fra-dato erstatter den tidligere række (som bevares i historikken)
    await db.execute(text("""
        UPDATE species_categories SET removed_at = now(), removed_by = :u
        WHERE artnr = :a AND removed_at IS NULL AND valid_from IS NOT DISTINCT FROM CAST(:f AS date)
    """), {"a": artnr, "f": body.valid_from, "u": user.id})
    c = SpeciesCategory(artnr=artnr, kategori=body.kategori, valid_from=body.valid_from,
                        note=(body.note or "").strip() or None, created_by=user.id)
    db.add(c)
    await db.commit()
    await _changed(user, {"artnr": artnr, "kategori": body.kategori, "valid_from": body.valid_from})
    return {"id": c.id}


class OverrideIn(BaseModel):
    artnr: int
    loknr: int
    date_from: date
    date_to: date
    kategori: str = Field(pattern=KAT)
    note: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _order(self):
        if self.date_to < self.date_from:
            raise ValueError("Slutdatoen ligger før startdatoen")
        return self


@router.post("/overrides")
async def add_override(body: OverrideIn, user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    sp = await db.get(Species, body.artnr)
    if sp is None or sp.list_artnr != body.artnr:
        raise HTTPException(400, "Overstyringer sættes på listearten")
    # En ny kategori for præcis samme serie afløser den gamle
    await db.execute(text("""
        UPDATE category_overrides SET removed_at = now(), removed_by = :u
        WHERE artnr = :a AND loknr = :l AND date_from = :f AND date_to = :t AND removed_at IS NULL
    """), {"a": body.artnr, "l": body.loknr, "f": body.date_from, "t": body.date_to, "u": user.id})
    v = CategoryOverride(**body.model_dump(exclude={"note"}), note=(body.note or "").strip() or None, created_by=user.id)
    db.add(v)
    await db.commit()
    await _changed(user, body.model_dump())
    return {"id": v.id}


async def _remove(db: AsyncSession, model, item_id: int, user: User) -> None:
    obj = await db.get(model, item_id)
    if obj is None or obj.removed_at is not None:
        raise HTTPException(404)
    obj.removed_at = datetime.now(timezone.utc)
    obj.removed_by = user.id
    await db.commit()
    await _changed(user, {"removed": model.__tablename__, "id": item_id})


@router.delete("/periods/{item_id}")
async def remove_period(item_id: int, user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    await _remove(db, SpeciesCategory, item_id, user)
    return {"ok": True}


@router.delete("/overrides/{item_id}")
async def remove_override(item_id: int, user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    await _remove(db, CategoryOverride, item_id, user)
    return {"ok": True}


# ---------------------------------------------------------------- manuelle fund (kun administratorer)
# Fund der ikke står i DOFbasen (fx et godkendt fund til havs). Moderatorer kan se dem, administratorer indsætte og slette.


class FindIn(BaseModel):
    artnr: int
    dato: date
    antal: int = Field(1, ge=1, le=1_000_000)
    loknr: int | None = None
    sted: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _place(self):
        if self.loknr is None and not (self.sted or "").strip():
            raise ValueError("Vælg en lokalitet eller skriv hvor fundet blev gjort")
        if self.dato > date.today():
            raise ValueError("Datoen ligger i fremtiden")
        return self


def _admin(user: User) -> None:
    if not role_at_least(user, "admin"):
        raise HTTPException(403, "Kun administratorer kan indsætte og slette fund")


@router.get("/finds")
async def list_finds(user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT f.id, f.dato, f.artnr, s.navn AS art, f.antal, f.loknr, l.navn AS lok, f.sted, f.note, f.created_at,
               f.created_by
        FROM manual_finds f JOIN species s ON s.artnr = f.artnr LEFT JOIN locations l ON l.loknr = f.loknr
        WHERE f.removed_at IS NULL ORDER BY f.dato DESC, f.id DESC
    """))).mappings().all()
    return {"items": [_row(r) for r in await _with_names(db, rows, "created_by")], "can_edit": role_at_least(user, "admin")}


@router.post("/finds")
async def add_find(body: FindIn, user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    _admin(user)
    sp = await db.get(Species, body.artnr)
    if sp is None:
        raise HTTPException(400, "Arten findes ikke")
    if sp.list_artnr != sp.artnr or sp.excluded:  # fundet tæller i Arter pr. år – kun arter fra artslisten
        raise HTTPException(400, f"{sp.navn} er ikke en art på artslisten – vælg arten (fx nominatarten i stedet for underarten)")
    if body.loknr is not None and not (await db.execute(text("SELECT 1 FROM locations WHERE loknr = :l"), {"l": body.loknr})).first():
        raise HTTPException(400, "Lokaliteten findes ikke")
    f = ManualFind(**body.model_dump(exclude={"sted", "note"}), sted=(body.sted or "").strip() or None,
                   note=(body.note or "").strip() or None, created_by=user.id)
    db.add(f)
    await db.flush()
    await manual_finds.mirror(db, f)
    await db.commit()
    await manual_finds.changed(f)
    await activity.track(user.id, "moderation", None, {"category": "manual_find", "id": f.id, "artnr": f.artnr, "dato": f.dato})
    return {"id": f.id}


@router.delete("/finds/{item_id}")
async def remove_find(item_id: int, user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    _admin(user)
    f = await db.get(ManualFind, item_id)
    if f is None or f.removed_at is not None:
        raise HTTPException(404)
    f.removed_at = datetime.now(timezone.utc)
    f.removed_by = user.id
    await manual_finds.mirror(db, f)
    await db.commit()
    await manual_finds.changed(f)
    await activity.track(user.id, "moderation", None, {"category": "manual_find", "removed": item_id})
    return {"ok": True}
