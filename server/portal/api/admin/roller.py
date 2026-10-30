"""Admin → Roller (superadmin): roller og deres admin-moduler."""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.admin._deps import (
    superadmin,
)
from portal.config import get_settings
from portal.db import get_session
from portal.models import (
    Role,
    User,
)
from portal.services import (
    activity,
)
from portal.services import auth as auth_service
from portal.services.auth import (
    FIXED_ROLES,
    MODULES,
    ROLE_MODULES,
    SUPERADMIN,
    modules,
)

router = APIRouter(prefix="/api", tags=["admin"])


# ---------------------------------------------------------------- roller (kun superadmin)
# Roller med deres admin-moduler. "user" og "admin" kan ikke slettes (koden bruger dem), og "user" har aldrig moduler.
# Superadmin er ikke en rolle her: den har altid alle moduler og er brugeren fra .env.


class RoleIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    modules: list[str] | None = Field(None, max_length=len(MODULES))


def _role_key(name: str, taken: set[str]) -> str:
    base = "".join(c if c.isalnum() else "_" for c in name.lower().translate(str.maketrans("æøå", "aoa"))).strip("_")[:24] or "rolle"
    key, i = base, 2
    while key in taken or key == SUPERADMIN:
        key, i = f"{base}_{i}", i + 1
    return key


@router.get("/admin/roles")
async def roles(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Rollerne med moduler og antal brugere – og alle med en rolle eller individuelle rettigheder."""
    rows = (await db.execute(select(Role).order_by(Role.sort, Role.name))).scalars().all()
    counts = dict((await db.execute(text("SELECT role, count(*) FROM users GROUP BY role"))).all())
    people = (await db.execute(text("""
        SELECT id, obserkode, navn, role, permissions, blocked, approved, last_seen FROM users
        WHERE role <> 'user' OR permissions IS NOT NULL ORDER BY role = 'superadmin' DESC, role, navn
    """))).mappings().all()
    return {
        "modules": MODULES,
        "roles": [{"key": r.key, "name": r.name, "modules": sorted(ROLE_MODULES.get(r.key, ())), "fixed": r.key in FIXED_ROLES,
                   "locked": r.key == "user", "users": counts.get(r.key, 0)} for r in rows],
        "superadmin": {"obserkode": get_settings().superadmin_obserkode.upper() or None},
        "people": [{**dict(p), "modules": sorted(modules(User(role=p["role"], blocked=False, permissions=p["permissions"])))}
                   for p in people],
    }


@router.post("/admin/roles")
async def create_role(body: RoleIn, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    if not body.name:
        raise HTTPException(400, "Rollen skal have et navn")
    taken = set((await db.execute(select(Role.key))).scalars())
    sort = (await db.execute(text("SELECT coalesce(max(sort), 0) + 10 FROM roles"))).scalar()
    mods = sorted(set(body.modules or []) & set(MODULES))
    role = Role(key=_role_key(body.name, taken), name=body.name.strip(), modules=mods, sort=sort)
    db.add(role)
    await db.commit()
    await auth_service.roles_changed()
    await activity.track(user.id, "roles", None, {"created": role.key})
    return {"key": role.key}


@router.patch("/admin/roles/{key}")
async def update_role(key: str, body: RoleIn, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    role = await db.get(Role, key)
    if role is None:
        raise HTTPException(404)
    if body.name is not None:
        role.name = body.name.strip()
    if body.modules is not None:
        if key == "user":
            raise HTTPException(400, "Rollen Bruger har ingen admin-moduler")
        unknown = set(body.modules) - set(MODULES)
        if unknown:
            raise HTTPException(400, f"Ukendte moduler: {', '.join(sorted(unknown))}")
        role.modules = sorted(set(body.modules))
    await db.commit()
    await auth_service.roles_changed()
    await activity.track(user.id, "roles", None, {"updated": key})
    return {"ok": True}


@router.delete("/admin/roles/{key}")
async def delete_role(key: str, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Brugere med rollen bliver almindelige brugere (individuelle rettigheder bevares)."""
    if key in FIXED_ROLES:
        raise HTTPException(400, "Rollen kan ikke slettes")
    role = await db.get(Role, key)
    if role is None:
        raise HTTPException(404)
    moved = (await db.execute(text("UPDATE users SET role = 'user' WHERE role = :k"), {"k": key})).rowcount
    await db.delete(role)
    await db.commit()
    await auth_service.roles_changed()
    await activity.track(user.id, "roles", None, {"deleted": key, "users": moved})
    return {"ok": True, "users": moved}
