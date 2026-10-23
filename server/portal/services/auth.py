"""Fælles login med DOFbasen-obserkode.

Hvert login verificeres hos DOFbasen (gatewayens opslagsbane – uden minimumsinterval). Portalen gemmer intet om
adgangskoden, heller ikke en hash; i gateway-køen ligger den højst 3 minutter. Højst LOGIN_PER_MIN forsøg i minuttet
pr. obserkode og pr. IP.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.config import get_settings
from portal.dof import gateway
from portal.models import Role, Setting, User

LOGIN_PER_MIN = 5
OBSERKODE_RE = re.compile(r"^[A-Z0-9]{2,16}$")
# Admin-moduler. Rollen giver et standardsæt; en bruger kan i stedet få en individuel liste (users.permissions).
MODULES = {
    "drift": "Drift: gateway, kø, arkiv, jobs og indstillinger",
    "data": "Data: hentning fra DOFbasen",
    "vejr": "Vejrarkiv",
    "aktivitet": "Aktivitet",
    "support": "Support: alle samtaler",
    "moderation": "Moderation: kommentarer, udelukkelser og beskeder",
    "ture": "Portalens ture",
    "kategorier": "Listekategorier",
    "brugere": "Brugere og rettigheder",
    "arter": "Arter",
    "indhold": "Nyheder",
    "laering": "Læring: kurser og spørgsmål",
    "mobgang_presets": "Mobgang: officielle presets",
    "mobgang_season": "Mobgang: lukning i ynglesæsonen",
    "dofbasen": "DOFbasen: alle rettigheder i DOFbasen-modulet (sider, lister, nyheder, roller, API)",
}
_MOD = {"moderation", "ture", "kategorier"}

# Roller ligger i tabellen roles og redigeres af superadmin. Startsættet lægges ind første gang. "user" (ingen
# admin-adgang) og "admin" (koden giver administratorer særlige rettigheder) kan ikke slettes; "user" har aldrig moduler.
# Superadmin er ikke en rolle i tabellen: den har altid alle moduler og er brugeren fra .env.
SUPERADMIN = "superadmin"
DEFAULT_ROLES = [("user", "Bruger", set()), ("moderator", "Moderator", _MOD),
                 ("editor", "Redaktør", _MOD | {"laering", "mobgang_presets"}), ("admin", "Administrator", set(MODULES))]
FIXED_ROLES = {"user", "admin"}
# Rollernes moduler og navne i denne proces – genindlæses, når en anden proces har ændret dem (se ensure_roles)
ROLE_MODULES: dict[str, set[str]] = {k: set(m) for k, _, m in DEFAULT_ROLES}
ROLE_NAMES: dict[str, str] = {k: n for k, n, _ in DEFAULT_ROLES}
ROLES_VERSION = "roles:version"
_roles_loaded: list = [None]


async def seed_roles(session: AsyncSession) -> None:
    if (await session.execute(select(Role.key).limit(1))).first() is None:
        for i, (key, name, mods) in enumerate(DEFAULT_ROLES):
            session.add(Role(key=key, name=name, modules=sorted(mods), sort=i * 10))
        await session.commit()


async def ensure_roles(session: AsyncSession, force: bool = False) -> None:
    """Hent rollerne fra databasen, hvis de er ændret siden sidst (ét Valkey-opslag pr. kald)."""
    ver = await kv().get(ROLES_VERSION) or "0"
    if not force and _roles_loaded[0] == ver:
        return
    rows = (await session.execute(select(Role).order_by(Role.sort, Role.name))).scalars().all()
    if rows:
        ROLE_MODULES.clear()
        ROLE_NAMES.clear()
        for r in rows:
            ROLE_MODULES[r.key] = set() if r.key == "user" else set(r.modules or []) & set(MODULES)
            ROLE_NAMES[r.key] = r.name
    _roles_loaded[0] = ver


async def roles_changed() -> None:
    await kv().incr(ROLES_VERSION)


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def normalize(kode: str) -> str:
    kode = (kode or "").strip().upper()
    if not OBSERKODE_RE.fullmatch(kode):
        raise AuthError("Ugyldig obserkode", 400)
    return kode


async def ensure_superadmin(session: AsyncSession) -> None:
    """Superadmin er obserkoden fra .env. Den logger ind via DOFbasen som alle andre."""
    s = get_settings()
    if not s.superadmin_obserkode:
        return
    kode = normalize(s.superadmin_obserkode)
    user = (await session.execute(select(User).where(User.obserkode == kode))).scalar_one_or_none()
    if user is None:
        user = User(obserkode=kode, navn=kode, role="superadmin", prefs={})
        session.add(user)
    user.role = SUPERADMIN
    user.approved = True
    # Der er kun én superadmin – den fra .env. Andre med rollen (fx en tidligere SUPERADMIN_OBSERKODE) bliver administratorer.
    await session.execute(update(User).where(User.role == SUPERADMIN, User.id != user.id).values(role="admin"))
    await session.commit()


async def _rate_limit(kode: str, ip: str) -> None:
    """Hvert forsøg tæller (det giver et kald til DOFbasen): højst LOGIN_PER_MIN i minuttet pr. obserkode og pr. IP."""
    r = kv()
    for key in (f"login:try:{kode}", f"login:try:ip:{ip}"):
        n = await r.incr(key)
        if n == 1:
            await r.expire(key, 60)
        if n > LOGIN_PER_MIN:
            raise AuthError("For mange forsøg. Prøv igen om et minut.", 429)


PENDING_MSG = ("Din konto er oprettet, men afventer godkendelse af en administrator. "
               "Prøv igen senere – der er ikke noget at gøre fra din side.")


async def approval_required(session: AsyncSession) -> bool:
    """Skal nye brugere godkendes, før de kan bruge portalen? Standard: ja (kan slås fra under Admin → Brugere)."""
    row = await session.get(Setting, "require_approval")
    return True if row is None else bool(row.value)


async def staff_ids(session: AsyncSession, module: str) -> list[int]:
    """Brugere med adgang til et admin-modul (rolle eller individuel rettighed)."""
    await ensure_roles(session)
    rows = (await session.execute(select(User).where(~User.blocked, or_(User.role != "user", User.permissions.isnot(None))))).scalars().all()
    return [u.id for u in rows if module in modules(u)]


async def login(session: AsyncSession, obserkode: str, password: str, ip: str) -> tuple[User, bool]:
    """Returnerer (bruger, ny_bruger). Er godkendelse slået til, oprettes en ny bruger som afventende, når koden er
    gyldig hos DOFbasen – men får ingen session, før en administrator har godkendt kontoen."""
    kode = normalize(obserkode)
    if not password:
        raise AuthError("Adgangskode mangler", 400)
    await _rate_limit(kode, ip)
    user = (await session.execute(select(User).where(User.obserkode == kode))).scalar_one_or_none()
    if user and user.blocked:
        raise AuthError("Brugeren er spærret", 403)

    # Verificér hos DOFbasen (opslagsbanen: med det samme, uden at vente på minimumsintervallet)
    job_id = await gateway.enqueue("login", {"obserkode": kode}, priority=gateway.INTERACTIVE,
                                   secret={"password": password})
    result = await gateway.wait_result(job_id, timeout=60)
    if result is None:
        await gateway.cancel(job_id)
        raise AuthError("DOFbasen svarede ikke i tide. Prøv igen om lidt.", 503)
    if not result.get("ok"):
        raise AuthError("DOFbasen kunne ikke nås. Prøv igen om lidt.", 503)
    if not (result.get("result") or {}).get("valid"):
        raise AuthError("Forkert obserkode eller adgangskode (tjekket hos DOFbasen)")

    new = user is None
    navn = None
    if new or user.navn in (None, "", user.obserkode):
        navn = (await session.execute(text(
            "SELECT trim(coalesce(fornavn,'') || ' ' || coalesce(efternavn,'')) FROM observers WHERE obserkode = :k"),
            {"k": kode})).scalar()
    if new:
        user = User(obserkode=kode, navn=navn or kode, role="user", approved=not await approval_required(session),
                    prefs={"lists_public": True, "push_enabled": True, "afdelinger": [], "kommuner": []})
        session.add(user)
    elif navn:
        user.navn = navn
    user.dof_verified_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user)
    if not user.approved:
        if new:
            await _notify_pending(session, user)
        raise AuthError(PENDING_MSG, 403)
    return user, new


async def _notify_pending(session: AsyncSession, user: User) -> None:
    """Giv dem, der styrer brugere, besked om en ny bruger der venter på godkendelse."""
    from portal.api.social import _notify  # her for at undgå cirkulær import

    ids = await staff_ids(session, "brugere")
    if ids:
        await _notify(ids, "Ny bruger afventer godkendelse", f"{user.navn or user.obserkode} ({user.obserkode}) har logget ind med en gyldig DOFbasen-kode.",
                      "/#/admin?tab=brugere", f"pending:{user.id}")


async def create_session(user: User) -> str:
    token = secrets.token_urlsafe(32)
    await kv().set(f"sess:{token}", user.id, ex=get_settings().session_days * 86400)
    return token


async def user_from_token(session: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    user_id = await kv().get(f"sess:{token}")
    if not user_id:
        return None
    user = await session.get(User, int(user_id))
    if user is None or user.blocked or not user.approved:
        return None
    await ensure_roles(session)
    return user


async def destroy_session(token: str | None) -> None:
    if token:
        await kv().delete(f"sess:{token}")


def role_at_least(user: User | None, role: str) -> bool:
    """Har brugeren rollen (superadmin har alle)?"""
    return bool(user) and user.role in (role, SUPERADMIN)


def modules(user: User | None) -> set[str]:
    """Brugerens admin-moduler: den individuelle liste, ellers rollens standard. Superadmin har altid alle."""
    if user is None or user.blocked:
        return set()
    if user.role == SUPERADMIN:
        return set(MODULES)
    if user.permissions is not None:
        return set(user.permissions) & set(MODULES)
    return set(ROLE_MODULES.get(user.role, ()))


def can(user: User | None, module: str) -> bool:
    return module in modules(user)
