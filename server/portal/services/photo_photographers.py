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
    while p is not None and p.merged_into is not None:  # flettet: følg kæden til den, der lever videre
        p = await session.get(GalPhotographer, p.merged_into)
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
    photographers = (await session.execute(select(GalPhotographer).where(GalPhotographer.match != "manuel", GalPhotographer.user_id.is_(None), GalPhotographer.merged_into.is_(None))
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


# ---------------------------------------------------------------- admin → Fotografer

async def listing(session: AsyncSession, *, match: str = "", q: str = "", limit: int = 50, offset: int = 0) -> dict:
    where, params = ["p.merged_into IS NULL"], {"lim": limit, "off": offset}
    if match:
        where.append("p.match = :m")
        params["m"] = match
    if q.strip():
        where.append("(p.navn ILIKE :q OR p.obserkode ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    w = " AND ".join(where)
    total = (await session.execute(text(f"SELECT count(*) FROM gal_photographers p WHERE {w}"), params)).scalar() or 0
    rows = (await session.execute(text(f"""
        SELECT p.id, p.navn, p.netfugl_bruger_id, p.user_id, p.obserkode, p.match, u.navn AS bruger, (SELECT count(*) FROM gal_photos x WHERE x.photographer_id = p.id) AS fotos
        FROM gal_photographers p LEFT JOIN users u ON u.id = p.user_id WHERE {w} ORDER BY p.navn_norm, p.id LIMIT :lim OFFSET :off"""), params)).mappings().all()
    counts = {r[0]: r[1] for r in (await session.execute(text("SELECT match, count(*) FROM gal_photographers WHERE merged_into IS NULL GROUP BY 1"))).all()}
    return {"total": total, "rows": [dict(r) for r in rows], "antal": counts}


async def link(session: AsyncSession, pid: int, *, user_id: int | None = None, obserkode: str | None = None, unlink: bool = False) -> None:
    """Koble fotografen til en bruger og/eller obserkode, eller fjern koblingen. Manuelle koblinger røres aldrig af automatikken."""
    p = await session.get(GalPhotographer, pid)
    if p is None or p.merged_into is not None:
        raise LookupError("Fotografen findes ikke")
    if unlink:
        old = p.user_id
        p.user_id, p.obserkode, p.match = None, None, "manuel"
        await session.flush()
        if old is not None:
            await session.execute(text("UPDATE gal_photos SET user_id = NULL WHERE photographer_id = :p AND source = 'netfugl'"), {"p": pid})
    elif user_id is not None:
        other = (await session.execute(select(GalPhotographer).where(GalPhotographer.user_id == user_id, GalPhotographer.id != pid))).scalar_one_or_none()
        if other is not None:
            raise ValueError(f"Brugeren er allerede koblet til fotografen «{other.navn}» – flet dem i stedet")
        if await session.get(User, user_id) is None:
            raise LookupError("Brugeren findes ikke")
        await set_user(session, p, user_id, match="manuel")
    elif obserkode:
        p.obserkode, p.match = obserkode.strip().upper()[:16], "manuel"
    else:
        raise ValueError("Angiv bruger, obserkode eller fjern koblingen")
    await session.commit()


async def merge(session: AsyncSession, from_id: int, into_id: int) -> None:
    """Flet to fotografer: fotos og grupper flyttes til into; from bevares som tom række, der peger på into (så importen finder den igen)."""
    if from_id == into_id:
        raise ValueError("Vælg to forskellige fotografer")
    a, b = await session.get(GalPhotographer, from_id), await session.get(GalPhotographer, into_id)
    if a is None or b is None or a.merged_into is not None or b.merged_into is not None:
        raise LookupError("Fotografen findes ikke")
    if a.user_id is not None and b.user_id is not None:
        raise ValueError("Begge fotografer er koblet til en bruger – fjern den ene kobling først")
    if a.user_id is not None:  # brugerkoblingen følger med til den, der lever videre
        uid, kode = a.user_id, a.obserkode
        a.user_id = None
        await session.flush()
        b.user_id, b.obserkode, b.match = uid, kode or b.obserkode, "manuel"
    b.standard_rettighed = b.standard_rettighed if b.user_id else a.standard_rettighed
    b.dof_samtykke = b.dof_samtykke or a.dof_samtykke
    await session.execute(text("UPDATE gal_photos SET photographer_id = :b, user_id = coalesce(user_id, :u) WHERE photographer_id = :a"), {"a": from_id, "b": into_id, "u": b.user_id})
    await session.execute(text("UPDATE gal_groups SET photographer_id = :b WHERE photographer_id = :a"), {"a": from_id, "b": into_id})
    a.merged_into, a.match = into_id, "manuel"
    await session.commit()
