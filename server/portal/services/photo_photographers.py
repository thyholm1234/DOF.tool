"""Fotografer i fotomodulet og automatisk match på navn (docs/netfugl-afloesning-spec.md §5).

En fotograf kobles kun til en portalbruger eller en DOFbasen-observatør, når navnet er entydigt. Observatørerne ligger i
observationsgruppen og slås op i en separat forespørgsel (aldrig i samme SQL som gal_*)."""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import GalPhotographer, User

_PAREN = re.compile(r"\([^)]*\)")
_PUNCT = re.compile(r"[.,;:'’`´\-–—_/]+")


def norm(navn: str | None) -> str:
    """Casefold, uden parentes-tillæg («(fsh)»), uden tegnsætning/bindestreger, med sammenpressede mellemrum."""
    s = unicodedata.normalize("NFKC", navn or "")
    s = _PAREN.sub(" ", s).casefold()
    s = _PUNCT.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


async def find_or_create(session: AsyncSession, navn: str, netfugl_bruger_id: int | None = None, user_id: int | None = None) -> GalPhotographer:
    """Fotografen for et Netfugl-bruger-id (importerede) eller en portalbruger (uploadede)."""
    q = select(GalPhotographer)
    if netfugl_bruger_id is not None:
        q = q.where(GalPhotographer.netfugl_bruger_id == netfugl_bruger_id)
    elif user_id is not None:
        q = q.where(GalPhotographer.user_id == user_id)
    else:
        q = q.where(GalPhotographer.navn_norm == norm(navn), GalPhotographer.netfugl_bruger_id.is_(None), GalPhotographer.user_id.is_(None))
    p = (await session.execute(q.limit(1))).scalar_one_or_none()
    if p is not None:
        return p
    n = (navn or "Ukendt").strip()[:200]
    p = GalPhotographer(navn=n, navn_norm=norm(n), netfugl_bruger_id=netfugl_bruger_id, user_id=user_id,
                        match="manuel" if user_id is not None and netfugl_bruger_id is None else "ingen")
    if user_id is not None and netfugl_bruger_id is None:
        obs = (await session.execute(select(User.obserkode).where(User.id == user_id))).scalar_one_or_none()
        p.obserkode = obs
    session.add(p)
    await session.flush()
    return p


async def set_user(session: AsyncSession, p: GalPhotographer, user_id: int | None, *, match: str) -> None:
    """Sæt (eller fjern) brugerkoblingen og opdatér fotografens fotos i samme transaktion."""
    p.user_id = user_id
    p.match = match
    if user_id is not None:
        p.obserkode = (await session.execute(select(User.obserkode).where(User.id == user_id))).scalar_one()
    await session.flush()
    await session.execute(text("UPDATE gal_photos SET user_id = :u WHERE photographer_id = :p AND source = 'netfugl'"), {"u": user_id, "p": p.id})


async def match_all(session: AsyncSession) -> dict[str, int]:
    """Match alle fotografer uden bruger og uden manuel kobling. Returnerer {auto, tvetydig, ingen}."""
    users: dict[str, list[tuple[int, str]]] = {}
    for uid, navn, kode in (await session.execute(select(User.id, User.navn, User.obserkode).where(User.navn.isnot(None)))).all():
        users.setdefault(norm(navn), []).append((uid, kode))
    observers: dict[str, list[str]] = {}
    for kode, fn, en in (await session.execute(text("SELECT obserkode, fornavn, efternavn FROM observers"))).all():
        observers.setdefault(norm(f"{fn or ''} {en or ''}"), []).append(kode)
    taken = {u for (u,) in (await session.execute(select(GalPhotographer.user_id).where(GalPhotographer.user_id.isnot(None)))).all()}
    photographers = (await session.execute(select(GalPhotographer).where(GalPhotographer.match != "manuel", GalPhotographer.user_id.is_(None))
                                           .order_by(GalPhotographer.id))).scalars().all()
    by_name: dict[str, int] = {}
    for p in photographers:
        by_name[p.navn_norm] = by_name.get(p.navn_norm, 0) + 1
    out = {"auto": 0, "tvetydig": 0, "ingen": 0}
    for p in photographers:
        cands = users.get(p.navn_norm, [])
        dup = by_name[p.navn_norm] > 1  # to Netfugl-profiler med samme navn → begge tvetydige (spec §5.5)
        if len(cands) == 1 and not dup and cands[0][0] not in taken:
            await set_user(session, p, cands[0][0], match="auto")
            taken.add(cands[0][0])
            out["auto"] += 1
        elif len(cands) > 1 or dup or (len(cands) == 1 and cands[0][0] in taken):
            p.match = "tvetydig"
            out["tvetydig"] += 1
        else:
            obs = observers.get(p.navn_norm, [])
            if len(obs) == 1 and not dup:
                p.obserkode, p.match = obs[0], "auto"
                out["auto"] += 1
            elif len(obs) > 1 or dup:
                p.match = "tvetydig"
                out["tvetydig"] += 1
            else:
                p.match = "ingen"
                out["ingen"] += 1
    await session.flush()
    return out


async def claim_by_obserkode(session: AsyncSession, user: User) -> None:
    """Kaldes ved login: en fotograf, der er matchet på obserkode, kobles til brugeren."""
    p = (await session.execute(select(GalPhotographer).where(GalPhotographer.obserkode == user.obserkode, GalPhotographer.user_id.is_(None),
                                                            GalPhotographer.match == "auto").limit(2))).scalars().all()
    if len(p) == 1 and (await session.execute(select(GalPhotographer.id).where(GalPhotographer.user_id == user.id))).first() is None:
        await set_user(session, p[0], user.id, match="auto")
