"""Årslister, scoreboards og listegrupper."""

from __future__ import annotations

import secrets
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.cache import bump
from portal.db import get_session
from portal.models import Area, ListGroup, ListGroupMember, Matrikel, MatrikelPeriode, User
from portal.services import lists, regions
from portal.services.areas import scope_for_area
from portal.services.auth import normalize

router = APIRouter(prefix="/api/lists", tags=["lists"])


async def _spec(db: AsyncSession, scope: str | None, year: str | None, matrikel: int | None, user: User | None):
    try:
        spec = lists.parse_list_spec(scope, year, matrikel)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if spec.scope.kind == "area":
        area = await db.get(Area, int(spec.scope.ref))
        if area is None or user is None or area.user_id != user.id:
            raise HTTPException(404, "Område findes ikke")
    return spec


@router.get("/meta")
async def meta(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    areas = (await db.execute(select(Area).where(Area.user_id == user.id, Area.use_for_lists))).scalars().all()
    prefs = user.prefs or {}
    afd = {r[0]: r[1] for r in (await db.execute(text("SELECT id, navn FROM afdelinger"))).all()}
    kom = {r[0]: r[1] for r in (await db.execute(text("SELECT kode, navn FROM kommuner"))).all()}
    scopes = [{"scope": "dk", "label": "Danmark"}]
    scopes += [{"scope": f"afd:{a}", "label": afd.get(int(a), a)} for a in prefs.get("afdelinger", []) if str(a).isdigit()]
    scopes += [{"scope": f"kom:{k}", "label": kom.get(k, k)} for k in prefs.get("kommuner", [])]
    scopes += [{"scope": scope_for_area(a).key, "label": a.name, "own": True} for a in areas]
    overview = await lists.matrikel_overview(db, user.id, user.obserkode)
    return {"years": await lists.available_years(db, user.obserkode),
            "matrikler": [{"nummer": m["nummer"], "navn": m["navn"], "tag": m["tag"], "scoreboard": m["scoreboard"]}
                          for m in overview["matrikler"]],
            "scopes": scopes, "matrikel_prefix": await lists.matrikel_prefix(db)}


@router.get("/user/{obserkode}")
async def user_list(obserkode: str, scope: str | None = None, year: str | None = None, matrikel: int | None = None,
                    user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session),
                    periode: str | None = None):
    kode = normalize(obserkode)
    own = user is not None and user.obserkode == kode
    if not own:
        target = (await db.execute(select(User).where(User.obserkode == kode))).scalar_one_or_none()
        in_group = user is not None and (await db.execute(text("""
            SELECT 1 FROM list_group_members a JOIN list_group_members b ON a.group_id = b.group_id
            WHERE a.obserkode = :me AND b.obserkode = :them LIMIT 1
        """), {"me": user.obserkode, "them": kode})).scalar()
        public = target is not None and (target.prefs or {}).get("lists_public", True)
        if not public and not in_group:
            raise HTTPException(403, "Listen er ikke offentlig")
        if scope and scope.startswith("area:"):
            raise HTTPException(403, "Egne områder er private")
        if matrikel and matrikel > 1:
            raise HTTPException(403, "Matrikel 2 og op er private")
        periode = None  # andre ser altid den aktive periode
    spec = await _spec(db, scope, year, matrikel, user)
    windows, period_info = await lists.matrikel_windows(db, kode, spec, periode)
    items = await lists.year_list(db, kode, spec, windows)
    navn = (await db.execute(text("""
        SELECT coalesce(u.navn, trim(coalesce(o.fornavn,'') || ' ' || coalesce(o.efternavn,''))) FROM observers o
        FULL JOIN users u ON u.obserkode = o.obserkode WHERE coalesce(o.obserkode, u.obserkode) = :k
    """), {"k": kode})).scalar()
    return {"obserkode": kode, "navn": navn or kode, "spec": spec.key, "count": len(items), "items": items,
            "periode": period_info}


@router.get("/scoreboard")
async def scoreboard(scope: str | None = None, year: str | None = None, matrikel: int | None = None,
                     group: int | None = None, user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    spec = await _spec(db, scope, year, matrikel, user)
    if spec.scope.kind == "area":
        raise HTTPException(400, "Egne områder har ingen fælles scoreboard – brug en gruppe")
    if spec.matrikel and spec.matrikel > 1:
        raise HTTPException(400, "Kun matrikel 1 har scoreboard – matrikel 2 og op er private")
    if group:
        g = await db.get(ListGroup, group)
        if g is None:
            raise HTTPException(404)
    return await lists.scoreboard(db, spec, group)


# ---------------------------------------------------------------- matrikelbirding


def _year(year: str | None) -> int | None:
    try:
        return None if year in (None, "", "all") else int(year)
    except ValueError as exc:
        raise HTTPException(400, "Ugyldigt år") from exc


@router.get("/matrikel/summary")
async def matrikel_summary(year: str | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    return await lists.matrikel_summary(db, user.id, user.obserkode, user.prefs or {}, _year(year))


@router.get("/matrikel/scoreboard")
async def matrikel_scoreboard(scope: str = "dk", year: str | None = None, group: int | None = None,
                              db: AsyncSession = Depends(get_session)):
    if scope != "dk" and not scope.startswith(("afd:", "kom:")):
        raise HTTPException(400, "Ukendt område")
    if group and await db.get(ListGroup, group) is None:
        raise HTTPException(404)
    return await lists.matrikel_board(db, _year(year), scope, group)


# ---------------------------------------------------------------- arter pr. år for regioner


@router.get("/regions")
async def region_overview(level: str = "afd", db: AsyncSession = Depends(get_session)):
    try:
        return await regions.overview(db, level)
    except ValueError as exc:
        raise HTTPException(400, "Ukendt niveau") from exc


@router.get("/regions/{region}")
async def region_detail(region: str, year: int | None = None, db: AsyncSession = Depends(get_session)):
    if region != "dk" and not region.startswith(("afd:", "kom:")):
        raise HTTPException(400, "Ukendt region")
    return await regions.detail(db, region, year)


# ---------------------------------------------------------------- grupper


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.get("/groups")
async def my_groups(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT g.id, g.name, g.owner_id, g.invite_code, (SELECT count(*) FROM list_group_members m2 WHERE m2.group_id = g.id) AS n
        FROM list_groups g WHERE g.owner_id = :u OR EXISTS (
            SELECT 1 FROM list_group_members m WHERE m.group_id = g.id AND m.obserkode = :k) ORDER BY g.name
    """), {"u": user.id, "k": user.obserkode})).mappings().all()
    return [{**dict(r), "owner": r["owner_id"] == user.id,
             "invite_code": r["invite_code"] if r["owner_id"] == user.id else None} for r in rows]


@router.post("/groups")
async def create_group(body: GroupIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    g = ListGroup(name=body.name, owner_id=user.id, invite_code=secrets.token_urlsafe(6)[:8].upper())
    db.add(g)
    await db.flush()
    db.add(ListGroupMember(group_id=g.id, obserkode=user.obserkode))
    await db.commit()
    await bump("lists")
    return {"id": g.id, "name": g.name, "invite_code": g.invite_code}


@router.post("/groups/join")
async def join_group(body: dict, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    code = str(body.get("code", "")).strip().upper()
    g = (await db.execute(select(ListGroup).where(ListGroup.invite_code == code))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, "Ukendt invitationskode")
    if not await db.get(ListGroupMember, (g.id, user.obserkode)):
        db.add(ListGroupMember(group_id=g.id, obserkode=user.obserkode))
        await db.commit()
        await bump("lists")
    return {"id": g.id, "name": g.name}


@router.get("/groups/{group_id}")
async def group_detail(group_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    g = await db.get(ListGroup, group_id)
    if g is None:
        raise HTTPException(404)
    members = await lists.participants(db, group_id)
    if g.owner_id != user.id and user.obserkode not in {m["obserkode"] for m in members}:
        raise HTTPException(403)
    return {"id": g.id, "name": g.name, "owner": g.owner_id == user.id,
            "invite_code": g.invite_code if g.owner_id == user.id else None, "members": members}


@router.post("/groups/{group_id}/members")
async def add_member(group_id: int, body: dict, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    g = await db.get(ListGroup, group_id)
    if g is None or g.owner_id != user.id:
        raise HTTPException(403, "Kun gruppens ejer kan tilføje medlemmer")
    kode = normalize(str(body.get("obserkode", "")))
    if not await db.get(ListGroupMember, (group_id, kode)):
        db.add(ListGroupMember(group_id=group_id, obserkode=kode))
        await db.commit()
        await bump("lists")
    return {"ok": True}


@router.delete("/groups/{group_id}/members/{obserkode}")
async def remove_member(group_id: int, obserkode: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    g = await db.get(ListGroup, group_id)
    if g is None or (g.owner_id != user.id and obserkode.upper() != user.obserkode):
        raise HTTPException(403)
    await db.execute(delete(ListGroupMember).where(ListGroupMember.group_id == group_id,
                                                   ListGroupMember.obserkode == obserkode.upper()))
    await db.commit()
    await bump("lists")
    return {"ok": True}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    g = await db.get(ListGroup, group_id)
    if g is None or (g.owner_id != user.id and user.role not in ("admin", "superadmin")):
        raise HTTPException(403)
    await db.delete(g)
    await db.commit()
    await bump("lists")
    return {"ok": True}


@router.get("/compare")
async def compare(a: str, b: str, scope: str | None = None, year: str | None = None,
                  user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Arter den ene har og den anden mangler (begge skal have offentlige lister eller være dig)."""
    left = await user_list(a, scope, year, None, user, db)
    right = await user_list(b, scope, year, None, user, db)
    la = {i["artnr"]: i for i in left["items"]}
    rb = {i["artnr"]: i for i in right["items"]}
    return {"a": {"obserkode": left["obserkode"], "navn": left["navn"], "count": left["count"]},
            "b": {"obserkode": right["obserkode"], "navn": right["navn"], "count": right["count"]},
            "only_a": [la[k] for k in la.keys() - rb.keys()], "only_b": [rb[k] for k in rb.keys() - la.keys()],
            "both": len(la.keys() & rb.keys())}


# ---------------------------------------------------------------- matrikler og perioder


class MatrikelIn(BaseModel):
    navn: str = Field(min_length=1, max_length=120)


class PeriodeIn(BaseModel):
    navn: str = Field(min_length=1, max_length=160)
    start: date
    slut: date | None = None


class PeriodePatch(BaseModel):
    navn: str | None = Field(None, min_length=1, max_length=160)
    start: date | None = None
    slut: date | None = None
    aaben: bool = False  # True = fjern slutdato


class FlytIn(BaseModel):
    dato: date
    ny_navn: str = Field(min_length=1, max_length=160)
    gammel_navn: str | None = Field(None, max_length=160)


async def _matrikel(db: AsyncSession, user: User, nummer: int, create: bool = False) -> Matrikel | None:
    if not 1 <= nummer <= 20:
        raise HTTPException(400, "Matrikelnummer skal være 1–20")
    m = (await db.execute(select(Matrikel).where(Matrikel.user_id == user.id, Matrikel.nummer == nummer))).scalar_one_or_none()
    if m is None and create:
        m = Matrikel(user_id=user.id, nummer=nummer, navn="Hjemme" if nummer == 1 else f"Matrikel {nummer}")
        db.add(m)
        await db.flush()
    return m


async def _check_period(db: AsyncSession, m: Matrikel, start: date, slut: date | None, exclude: int | None = None) -> None:
    if slut and slut < start:
        raise HTTPException(400, "Slutdatoen ligger før startdatoen")
    others = (await db.execute(select(MatrikelPeriode).where(MatrikelPeriode.matrikel_id == m.id))).scalars().all()
    for p in others:
        if p.id == exclude:
            continue
        if p.start <= (slut or date.max) and (p.slut or date.max) >= start:
            raise HTTPException(400, f"Perioden overlapper '{p.navn}' ({p.start:%d/%m/%Y}–{p.slut:%d/%m/%Y})" if p.slut
                                else f"Perioden overlapper '{p.navn}' (fra {p.start:%d/%m/%Y})")


async def _own_period(db: AsyncSession, user: User, periode_id: int) -> tuple[MatrikelPeriode, Matrikel]:
    p = await db.get(MatrikelPeriode, periode_id)
    m = await db.get(Matrikel, p.matrikel_id) if p else None
    if p is None or m is None or m.user_id != user.id:
        raise HTTPException(404, "Perioden findes ikke")
    return p, m


@router.get("/matrikler")
async def matrikler(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    return await lists.matrikel_overview(db, user.id, user.obserkode)


@router.put("/matrikler/{nummer}")
async def save_matrikel(nummer: int, body: MatrikelIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    m = await _matrikel(db, user, nummer, create=True)
    m.navn = body.navn.strip()
    await db.commit()
    return await lists.matrikel_overview(db, user.id, user.obserkode)


@router.delete("/matrikler/{nummer}")
async def delete_matrikel(nummer: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Sletter navn og perioder. Tags i DOFbasen-noterne er urørte, så listen findes stadig (uden perioder)."""
    m = await _matrikel(db, user, nummer)
    if m:
        await db.delete(m)
        await db.commit()
        await bump("lists")
    return await lists.matrikel_overview(db, user.id, user.obserkode)


@router.post("/matrikler/{nummer}/perioder")
async def add_period(nummer: int, body: PeriodeIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    m = await _matrikel(db, user, nummer, create=True)
    await _check_period(db, m, body.start, body.slut)
    db.add(MatrikelPeriode(matrikel_id=m.id, navn=body.navn.strip(), start=body.start, slut=body.slut))
    await db.commit()
    await bump("lists")
    return await lists.matrikel_overview(db, user.id, user.obserkode)


@router.patch("/perioder/{periode_id}")
async def patch_period(periode_id: int, body: PeriodePatch, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    p, m = await _own_period(db, user, periode_id)
    start = body.start or p.start
    slut = None if body.aaben else (body.slut if body.slut is not None else p.slut)
    await _check_period(db, m, start, slut, exclude=p.id)
    p.start, p.slut = start, slut
    if body.navn:
        p.navn = body.navn.strip()
    await db.commit()
    await bump("lists")
    return await lists.matrikel_overview(db, user.id, user.obserkode)


@router.delete("/perioder/{periode_id}")
async def delete_period(periode_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    p, _ = await _own_period(db, user, periode_id)
    await db.delete(p)
    await db.commit()
    await bump("lists")
    return await lists.matrikel_overview(db, user.id, user.obserkode)


@router.post("/matrikler/{nummer}/flyt")
async def move(nummer: int, body: FlytIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Flytning: den periode der gælder dagen før flyttedatoen afsluttes dér, og en ny periode starter.
    Har matriklen ingen perioder, oprettes den gamle adresse fra første fund med tagget."""
    m = await _matrikel(db, user, nummer, create=True)
    periods = (await db.execute(select(MatrikelPeriode).where(MatrikelPeriode.matrikel_id == m.id)
                                .order_by(MatrikelPeriode.start))).scalars().all()
    if any(p.start >= body.dato for p in periods):
        raise HTTPException(400, "Der findes allerede en periode, der starter på eller efter flyttedatoen – ret den i stedet")
    split = body.dato - timedelta(days=1)
    if not periods:
        stats = (await lists._tag_stats(db, user.obserkode)).get(nummer)
        first = date.fromisoformat(stats["first"]) if stats else date(body.dato.year, 1, 1)
        first = min(first, split)
        db.add(MatrikelPeriode(matrikel_id=m.id, navn=(body.gammel_navn or "Tidligere adresse").strip(), start=first, slut=split))
    else:
        current = next((p for p in reversed(periods) if p.start <= split and (p.slut is None or p.slut >= split)), None)
        if current is not None:
            current.slut = split
            if body.gammel_navn:
                current.navn = body.gammel_navn.strip()
    db.add(MatrikelPeriode(matrikel_id=m.id, navn=body.ny_navn.strip(), start=body.dato, slut=None))
    await db.commit()
    await bump("lists")
    return await lists.matrikel_overview(db, user.id, user.obserkode)
