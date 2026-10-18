"""Årslister, scoreboards og listegrupper."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.cache import bump
from portal.db import get_session
from portal.models import Area, ListGroup, ListGroupMember, User
from portal.services import lists
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
    return {"years": await lists.available_years(db, user.obserkode), "matrikler": await lists.matrikel_numbers(db, user.obserkode),
            "scopes": scopes, "matrikel_prefix": await lists.matrikel_prefix(db)}


@router.get("/user/{obserkode}")
async def user_list(obserkode: str, scope: str | None = None, year: str | None = None, matrikel: int | None = None,
                    user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
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
    spec = await _spec(db, scope, year, matrikel, user)
    items = await lists.year_list(db, kode, spec)
    navn = (await db.execute(text("""
        SELECT coalesce(u.navn, trim(coalesce(o.fornavn,'') || ' ' || coalesce(o.efternavn,''))) FROM observers o
        FULL JOIN users u ON u.obserkode = o.obserkode WHERE coalesce(o.obserkode, u.obserkode) = :k
    """), {"k": kode})).scalar()
    return {"obserkode": kode, "navn": navn or kode, "spec": spec.key, "count": len(items), "items": items}


@router.get("/scoreboard")
async def scoreboard(scope: str | None = None, year: str | None = None, matrikel: int | None = None,
                     group: int | None = None, user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    spec = await _spec(db, scope, year, matrikel, user)
    if spec.scope.kind == "area":
        raise HTTPException(400, "Egne områder har ingen fælles scoreboard – brug en gruppe")
    if group:
        g = await db.get(ListGroup, group)
        if g is None:
            raise HTTPException(404)
    return await lists.scoreboard(db, spec, group)


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
