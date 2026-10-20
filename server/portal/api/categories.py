"""Listekategorier (moderator og op): artens kategori over tid og overstyring af serier (lokalitet × art)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_role
from portal.cache import bump
from portal.db import get_session
from portal.models import CategoryOverride, Species, SpeciesCategory, User
from portal.services import activity, categories

router = APIRouter(prefix="/api/categories", tags=["categories"])
moderator = require_role("moderator")
KAT = "^[A-E]$"


@router.get("/species")
async def species(q: str = "", user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    """Søgning i listearter. Uden søgning: arter der ikke er kategori A i dag, eller som har overstyrede serier."""
    rows = (await db.execute(text(f"""
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
        SELECT c.id, c.kategori, c.valid_from, c.note, c.created_at, c.removed_at, cu.navn AS created_by, ru.navn AS removed_by
        FROM species_categories c LEFT JOIN users cu ON cu.id = c.created_by LEFT JOIN users ru ON ru.id = c.removed_by
        WHERE c.artnr = :a ORDER BY c.valid_from NULLS FIRST, c.id
    """), {"a": artnr})).mappings().all()
    overrides = (await db.execute(text("""
        SELECT v.id, v.loknr, l.navn AS lok, v.date_from, v.date_to, v.kategori, v.note, v.created_at, v.removed_at,
               cu.navn AS created_by, ru.navn AS removed_by
        FROM category_overrides v LEFT JOIN locations l ON l.loknr = v.loknr
        LEFT JOIN users cu ON cu.id = v.created_by LEFT JOIN users ru ON ru.id = v.removed_by
        WHERE v.artnr = :a ORDER BY v.date_from DESC, v.id DESC
    """), {"a": artnr})).mappings().all()
    years = (await db.execute(text("""
        SELECT DISTINCT extract(year FROM o.dato)::int FROM observations o JOIN species s ON s.artnr = o.artnr
        WHERE s.list_artnr = :a ORDER BY 1 DESC
    """), {"a": artnr})).scalars().all()
    return {"artnr": artnr, "navn": sp.navn, "latin": sp.latin, "klass": sp.klass,
            "current": await categories.species_category(db, artnr), "categories": categories.CATEGORIES,
            "periods": [_row(r) for r in periods], "overrides": [_row(r) for r in overrides], "years": years,
            "year": year, "series": await categories.series(db, artnr, year), "gap_days": categories.SERIES_GAP_DAYS}


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
