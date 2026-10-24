"""DKU: tip om observationer, der mangler dokumentation eller virker forkerte, og sagsbehandling hos DKU.

En sag startes enten af et tip fra en bruger eller af DKU selv (en DKU-ansvarlig åbner sagen direkte på en observation).

Gangen i en sag:
  ny        – en bruger har tippet; kun tipperen og DKU kan se sagen
  behandles – en DKU-ansvarlig har taget sagen (DOFbasen-observationer får status «til behandling»)
  afventer  – DKU har inddraget observatøren, som nu kan se sagen, skrive og rette observationen
  afgjort   – godkendt, rettet, afvist eller henlagt (DOFbasen-observationer får status «behandlet af DKU» ved godkendt/rettet)

Observatøren må aldrig kunne se, om sagen startede med et tip eller hos DKU – og slet ikke hvem der har tippet. Derfor er
beskederne delt i kanaler: dku (intern note), tipper (DKU ↔ tippere; en tipper ser kun egne beskeder og DKU's svar) og
observer (DKU ↔ observatøren). Tippere og observatøren kan aldrig skrive til hinanden, og grunden til tippet vises ikke
for observatøren.
DKU-ansvarlige udpeges pr. lokalafdeling (afdeling 0 = landsdækkende) af brugere med admin-modulet «dku».

Alle tabeller ligger i brugergruppen. Observationens data hentes for sig (DOFbasen i observationsgruppen, feltmeldinger
i brugergruppen) og gemmes som et øjebliksbillede i sagen, så der aldrig joines på tværs af grupperne.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.db import SessionLocal
from portal.models import DkuCase, User
from portal.services import features
from portal.services.auth import SUPERADMIN, can, staff_ids

log = logging.getLogger("portal.dku")

REASONS = {"dokumentation": "Mangler dokumentation", "usandsynlig": "Usandsynlig – mulig fejlobservation",
           "fejl": "Sandsynligvis fejl i indtastningen", "andet": "Andet"}
STATUSES = {"ny": "Nyt tip", "behandles": "Under behandling", "afventer": "Afventer observatøren", "afgjort": "Afgjort"}
OUTCOMES = {"godkendt": "Godkendt – observationen står", "rettet": "Rettet af observatøren", "afvist": "Afvist – fejlobservation",
            "henlagt": "Henlagt – ikke grundlag for at gå videre"}
# Felterne der vises og sammenlignes (nøgle, overskrift)
FIELDS = [("art", "Art"), ("antal", "Antal"), ("dato", "Dato"), ("tid", "Tid"), ("lok", "Lokalitet"), ("kommune", "Kommune"),
          ("koen", "Køn"), ("alder", "Alder"), ("dragt", "Dragt"), ("adf", "Adfærd"), ("position", "Position"),
          ("sikkerhed", "Sikkerhed"), ("grundlag", "Grundlag"), ("noter", "Fuglenoter"), ("turnoter", "Turnoter"),
          ("medier", "Fotos og lyd")]
TIPS_PER_DAY = 10


def _s(v) -> str:
    return "" if v is None else str(v).strip()


async def observation(db: AsyncSession, source: str, ident: int) -> dict | None:
    """Observationens data som flade tekstfelter + metadata (artnr, dato, loknr, afdelinger, obserkode, hemmelig …)."""
    if source == "dofbasen":
        r = (await db.execute(text("""
            SELECT o.obsid, o.dato, o.artnr, s.navn AS art, o.antal, o.koen, o.alderkode, cd.text AS dragt, ca.text AS adf,
                   o.adfkode, o.obstidfra, o.obstidtil, t.turtidfra, t.turtidtil, o.loknr, l.navn AS lok, k.navn AS kommune,
                   o.obs_lat, o.obs_lon, o.fuglnoter, t.turnoter, o.obserkode, o.hemmelig, o.afdelinger, o.kategori,
                   trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')) AS observer
            FROM observations o JOIN species s ON s.artnr = o.artnr
            LEFT JOIN locations l ON l.loknr = o.loknr LEFT JOIN kommuner k ON k.kode = l.kommune_kode
            LEFT JOIN trips t ON t.turid = o.turid LEFT JOIN observers ob ON ob.obserkode = o.obserkode
            LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = o.adfkode
            LEFT JOIN code_texts cd ON cd.kind = 'dragt' AND cd.code = o.dragtkode
            WHERE o.obsid = :id"""), {"id": ident})).mappings().first()
        if r is None:
            return None
        fields = {"art": r["art"], "antal": r["antal"], "dato": r["dato"].isoformat(),
                  "tid": "–".join(x for x in (r["obstidfra"] or r["turtidfra"], r["obstidtil"] or r["turtidtil"]) if x),
                  "lok": r["lok"], "kommune": r["kommune"], "koen": r["koen"] if r["koen"] not in ("-", None) else "",
                  "alder": r["alderkode"], "dragt": r["dragt"], "adf": r["adf"] or r["adfkode"],
                  "position": f"{r['obs_lat']:.5f}, {r['obs_lon']:.5f}" if r["obs_lat"] and r["obs_lon"] else "",
                  "noter": r["fuglnoter"], "turnoter": r["turnoter"], "sikkerhed": "", "grundlag": "", "medier": ""}
        meta = {"source": source, "obsid": r["obsid"], "report_id": None, "artnr": r["artnr"], "dato": r["dato"].isoformat(),
                "loknr": r["loknr"], "afdelinger": list(r["afdelinger"] or []), "obserkode": r["obserkode"],
                "observer": r["observer"] or r["obserkode"], "hemmelig": bool(r["hemmelig"]), "kategori": r["kategori"],
                "url": f"https://dofbasen.dk/popobs.php?obsid={r['obsid']}&summering=tur&obs=obs",
                "thread": f"/#/traad/{r['dato'].isoformat()}/{r['artnr']}/{r['loknr']}" if r["loknr"] else None,
                "owner_user_id": None, "media": []}
    else:
        r = (await db.execute(text("""
            SELECT r.id, r.user_id, r.dato, r.artnr, s.navn AS art, r.antal, r.koen, r.alderkode, r.dragtkode, ca.text AS adf,
                   r.adfkode, r.obstidfra, r.obstidtil, r.loknr, l.navn AS lok, k.navn AS kommune, r.obs_lat, r.obs_lon,
                   r.fuglnoter, r.obserkode, r.hemmelig, r.afdelinger, r.kategori, r.sikkerhed, r.grundlag, r.media, r.trip_id,
                   coalesce(u.navn, u.obserkode) AS observer
            FROM field_reports r JOIN species s ON s.artnr = r.artnr JOIN users u ON u.id = r.user_id
            LEFT JOIN locations l ON l.loknr = r.loknr LEFT JOIN kommuner k ON k.kode = l.kommune_kode
            LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = r.adfkode
            WHERE r.id = :id"""), {"id": ident})).mappings().first()
        if r is None:
            return None
        media = list(r["media"] or [])
        fields = {"art": r["art"], "antal": r["antal"], "dato": r["dato"].isoformat(),
                  "tid": "–".join(x for x in (r["obstidfra"], r["obstidtil"]) if x), "lok": r["lok"], "kommune": r["kommune"],
                  "koen": {"M": "han", "F": "hun"}.get(r["koen"] or "", r["koen"]), "alder": r["alderkode"], "dragt": r["dragtkode"],
                  "adf": r["adf"] or r["adfkode"],
                  "position": f"{r['obs_lat']:.5f}, {r['obs_lon']:.5f}" if r["obs_lat"] and r["obs_lon"] else "",
                  "noter": r["fuglnoter"], "turnoter": "", "sikkerhed": r["sikkerhed"], "grundlag": ", ".join(r["grundlag"] or []),
                  "medier": f"{sum(m.get('kind') == 'image' for m in media)} fotos, {sum(m.get('kind') == 'audio' for m in media)} lyd"
                  if media else ""}
        meta = {"source": source, "obsid": None, "report_id": r["id"], "artnr": r["artnr"], "dato": r["dato"].isoformat(),
                "loknr": r["loknr"], "afdelinger": list(r["afdelinger"] or []), "obserkode": r["obserkode"],
                "observer": r["observer"], "hemmelig": bool(r["hemmelig"]), "kategori": r["kategori"], "url": None,
                "thread": f"/#/traad/{r['dato'].isoformat()}/{r['artnr']}/{r['loknr']}",
                "edit": f"/#/indtast/tur/{r['trip_id']}" if r["trip_id"] else f"/#/indtast/obs/{r['id']}",
                "owner_user_id": r["user_id"], "media": media}
    return {"fields": {k: _s(v) for k, v in fields.items()}, **meta}


def changes(before: dict, after: dict) -> list[dict]:
    """Felter der er ændret mellem to øjebliksbilleder."""
    return [{"field": k, "label": label, "before": before.get(k, ""), "after": after.get(k, "")}
            for k, label in FIELDS if before.get(k, "") != after.get(k, "")]


# ---------------------------------------------------------------- rettigheder

async def member_afdelinger(db: AsyncSession, user_id: int) -> set[int]:
    return set((await db.execute(text("SELECT afdeling_id FROM dku_members WHERE user_id = :u"), {"u": user_id})).scalars())


def is_admin(user: User) -> bool:
    return user.role == SUPERADMIN or can(user, "dku")


async def is_dku_for(db: AsyncSession, user: User, afdelinger: list[int]) -> bool:
    """Er brugeren DKU for en observation fra disse afdelinger (ansvarlig, landsdækkende eller administrator)?"""
    if is_admin(user):
        return True
    afd = await member_afdelinger(db, user.id)
    return 0 in afd or bool(afd & set(afdelinger or []))


def channels(role: str, observer_involved: bool, has_tippers: bool) -> list[str]:
    """Kanaler brugeren må skrive i: DKU vælger, tippere og observatøren har hver kun én."""
    if role == "dku":
        return [*(["observer"] if observer_involved else []), *(["tipper"] if has_tippers else []), "dku"]
    return [role]


def visible_sql(role: str) -> str:
    """Beskeder rollen må se (alias m, parameter :u = brugerens id)."""
    if role == "dku":
        return "TRUE"
    if role == "tipper":
        return "(m.visibility = 'tipper' AND (m.role IN ('dku', 'system') OR m.user_id = :u))"
    return "m.visibility = 'observer'"


async def context(db: AsyncSession, user: User | None) -> dict:
    """Til /api/me og menuen: er modulet synligt, hvilke afdelinger er brugeren DKU for, og antal ulæste sager."""
    if user is None:
        return {"visible": False}
    afd = await member_afdelinger(db, user.id)
    admin = is_admin(user)
    flags = await features.load(db)
    visible = features.visible(flags, "dku", superadmin=user.role == SUPERADMIN, insider=bool(afd) or admin)
    out = {"visible": visible, "member_of": sorted(afd), "admin": admin, "unread": 0, "open": 0}
    if visible:
        out["unread"] = await unread_count(db, user, afd, False)  # administratorer uden DKU-ansvar får ikke badges
        if afd or admin:
            out["open"] = (await db.execute(text(f"""SELECT count(*) FROM dku_cases c WHERE c.status <> 'afgjort'
                AND {_dku_scope_sql(afd, admin)}"""), {"afd": list(afd)})).scalar()
    return out


def _dku_scope_sql(afd: set[int], admin: bool) -> str:
    """WHERE-udtryk for de sager, en DKU-ansvarlig/administrator må se (alias c)."""
    if admin or 0 in afd:
        return "TRUE"
    if not afd:
        return "FALSE"
    return "c.afdelinger && CAST(:afd AS smallint[])"


async def role_in(db: AsyncSession, case: DkuCase, user: User) -> str | None:
    """dku / tipper / observer / None. DKU vinder over de andre roller (en DKU-ansvarlig kan også have tippet)."""
    afd = await member_afdelinger(db, user.id)
    if is_admin(user) or 0 in afd or afd & set(case.afdelinger or []):
        return "dku"
    role = (await db.execute(text("SELECT role FROM dku_participants WHERE case_id = :c AND user_id = :u AND role <> 'dku'"),
                             {"c": case.id, "u": user.id})).scalar()
    if role == "observer" and not case.observer_involved:
        return None
    return role


async def unread_count(db: AsyncSession, user: User, afd: set[int], admin: bool) -> int:
    """Sager med beskeder, brugeren ikke har læst (egne beskeder og interne noter, man ikke må se, tæller ikke)."""
    return (await db.execute(text(f"""
        SELECT count(DISTINCT c.id) FROM dku_cases c
        LEFT JOIN dku_participants p ON p.case_id = c.id AND p.user_id = :u
        JOIN dku_messages m ON m.case_id = c.id
        WHERE m.user_id IS DISTINCT FROM :u AND m.created_at > coalesce(p.last_read_at, 'epoch')
          AND (({_dku_scope_sql(afd, admin)})
               OR (p.role = 'tipper' AND m.visibility = 'tipper' AND m.role IN ('dku', 'system'))
               OR (p.role = 'observer' AND c.observer_involved AND m.visibility = 'observer'))
    """), {"u": user.id, "afd": list(afd)})).scalar() or 0


# ---------------------------------------------------------------- notifikationer

async def recipients_dku(db: AsyncSession, case: DkuCase) -> set[int]:
    rows = (await db.execute(text("""SELECT DISTINCT user_id FROM dku_members
        WHERE afdeling_id = 0 OR afdeling_id = ANY(CAST(:a AS smallint[]))"""), {"a": list(case.afdelinger or [])})).scalars()
    # Ingen DKU-ansvarlige for afdelingen endnu: så går sagen til dem, der administrerer DKU
    return set(rows) or set(await staff_ids(db, "dku"))


async def participants(db: AsyncSession, case_id: int, role: str) -> set[int]:
    return set((await db.execute(text("SELECT user_id FROM dku_participants WHERE case_id = :c AND role = :r"),
                                 {"c": case_id, "r": role})).scalars())


async def notify(case_id: int, *, to: str, title: str, body: str, exclude: int | None = None, key: str = "") -> None:
    """Notifikation (indbakke, live og push) til sagens parter. to: dku / tipper / observer / parter (tipper+observer)."""
    from portal.services.alerts import deliver

    async with SessionLocal() as s:
        try:
            case = await s.get(DkuCase, case_id)
            if case is None:
                return
            users: set[int] = set()
            if to in ("dku", "alle"):
                users |= await recipients_dku(s, case)
            if to in ("tipper", "parter", "alle"):
                users |= await participants(s, case_id, "tipper")
            if to in ("observer", "parter", "alle") and case.observer_involved:
                users |= await participants(s, case_id, "observer")
            users.discard(exclude)
            if not users:
                return
            art = case.snapshot.get("fields", {}).get("art", "")
            msg = {"title": f"DKU: {title} – {art}"[:240], "body": body[:480], "url": f"/#/dku/{case.id}", "tag": f"dku-{case.id}",
                   "kategori": None, "rule_id": None, "push": True}
            stamp = key or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            await deliver(s, None, {(u, f"dku:{case.id}:{stamp}"): msg for u in users})
        except Exception:  # noqa: BLE001 – en fejlet notifikation må ikke vælte handlingen
            log.exception("DKU-notifikation fejlede (sag %s)", case_id)


# ---------------------------------------------------------------- feltmeldinger rettet af observatøren

async def report_changed(report_id: int) -> None:
    """Kaldes efter en feltmelding er gemt: åbne sager om den får en systembesked med ændringerne, og DKU får besked."""
    async with SessionLocal() as s:
        cases = (await s.execute(text("""SELECT id FROM dku_cases WHERE source = 'portal' AND report_id = :r
            AND status <> 'afgjort'"""), {"r": report_id})).scalars().all()
        if not cases:
            return
        now = await observation(s, "portal", report_id)
        if now is None:
            return
        for cid in cases:
            case = await s.get(DkuCase, cid)
            last = (await s.execute(text("""SELECT body FROM dku_messages WHERE case_id = :c AND role = 'system'
                AND body LIKE 'Observationen er rettet%' ORDER BY id DESC LIMIT 1"""), {"c": cid})).scalar()
            base = case.snapshot.get("current") or case.snapshot.get("fields", {})
            diff = changes(base, now["fields"])
            if not diff:
                continue
            lines = "\n".join(f"• {d['label']}: {d['before'] or '–'} → {d['after'] or '–'}" for d in diff)
            body = f"Observationen er rettet af observatøren:\n{lines}"
            if body == last:
                continue
            await s.execute(text("""INSERT INTO dku_messages (case_id, user_id, role, visibility, body)
                VALUES (:c, NULL, 'system', 'observer', :b)"""), {"c": cid, "b": body})
            case.snapshot = {**case.snapshot, "current": now["fields"]}
            case.updated_at = datetime.now(timezone.utc)
            await s.commit()
            await notify(cid, to="dku", title="observationen er rettet", body=lines)
