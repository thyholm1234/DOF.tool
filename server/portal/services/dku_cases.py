"""DKU-sager: dataadgang for sager, beskeder, deltagere, godkendelser og DKU-ansvarlige (api/dku.py).

Reglerne for hvem der må se og gøre hvad står i services/dku.py; her ligger SQL'en. dofbasen_status skriver i
observationsgruppen og bruger derfor sin egen session.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.db import SessionLocal
from portal.services import dku


async def dofbasen_status(obsid: int, status: str | None, user_id: int, case_id: int) -> None:
    """DKU-status på en DOFbasen-observation (DOFbasen-modulets blå pil). Egen session: tabellen ligger i observationsgruppen.
    status None fjerner kun «til behandling» – en status sat af DKU eller SU i forvejen røres ikke."""
    async with SessionLocal() as s:
        if status is None:
            await s.execute(text("DELETE FROM dofb_obs_status WHERE obsid = :o AND status = 'behandling'"), {"o": obsid})
        elif status == "behandling":
            await s.execute(text("""INSERT INTO dofb_obs_status (obsid, status, note, set_by) VALUES (:o, 'behandling', :n, :u)
                ON CONFLICT (obsid) DO NOTHING"""), {"o": obsid, "n": f"DKU-sag #{case_id}", "u": user_id})
        else:
            await s.execute(text("""INSERT INTO dofb_obs_status (obsid, status, note, set_by) VALUES (:o, :s, :n, :u)
                ON CONFLICT (obsid) DO UPDATE SET status = EXCLUDED.status, note = EXCLUDED.note, set_by = EXCLUDED.set_by,
                set_at = now() WHERE dofb_obs_status.status IN ('behandling', 'dku')"""),
                {"o": obsid, "s": status, "n": f"DKU-sag #{case_id}", "u": user_id})
        await s.commit()


# ---------------------------------------------------------------- opslag

async def afdeling_names(session: AsyncSession) -> dict[int, str]:
    return dict((await session.execute(text("SELECT id, navn FROM afdelinger"))).all())


async def afdeling_name(session: AsyncSession, afdeling_id: int) -> str | None:
    return (await session.execute(text("SELECT navn FROM afdelinger WHERE id = :a"), {"a": afdeling_id})).scalar()


async def location_afdeling(session: AsyncSession, loknr: int) -> int | None:
    return (await session.execute(text("SELECT afdeling_id FROM locations WHERE loknr = :l"), {"l": loknr})).scalar()


async def observer_user_id(session: AsyncSession, obserkode: str) -> int | None:
    """Portalbrugeren med obserkoden (ikke blokeret), hvis observatøren bruger DOF.tool."""
    return (await session.execute(text("SELECT id FROM users WHERE obserkode = :k AND NOT blocked"), {"k": obserkode})).scalar()


async def user_names(session: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict((await session.execute(text("SELECT id, coalesce(navn, obserkode) FROM users WHERE id = ANY(:i)"),
                                       {"i": list(ids)})).all())


# ---------------------------------------------------------------- sager

async def open_case_id(session: AsyncSession, source: str, ident: int) -> int | None:
    """Åben (ikke afgjort) sag om observationen (source dofbasen: obsid, portal: report_id)."""
    col = "obsid" if source == "dofbasen" else "report_id"
    return (await session.execute(text(f"""SELECT id FROM dku_cases WHERE source = :s AND {col} = :i AND status <> 'afgjort'
        ORDER BY id LIMIT 1"""), {"s": source, "i": ident})).scalar()


async def add_tipper(session: AsyncSession, case_id: int, user_id: int) -> None:
    await session.execute(text("""INSERT INTO dku_participants (case_id, user_id, role, last_read_at) VALUES (:c, :u, 'tipper', now())
        ON CONFLICT (case_id, user_id) DO NOTHING"""), {"c": case_id, "u": user_id})


async def case_list(session: AsyncSession, user_id: int, view: str, state: str, member_of: set[int], admin: bool) -> list[dict]:
    """Sagslisten: DKU ser sagerne i sine afdelinger (admin alle), observatøren sine egne, når observatøren er inddraget.
    Felterne afhænger af rollen – observatøren ser hverken grunden eller hvordan sagen startede."""
    if view == "dku":
        where, role = dku._dku_scope_sql(member_of, admin), "dku"
    else:
        where, role = ("c.observer_involved AND EXISTS (SELECT 1 FROM dku_participants x WHERE x.case_id = c.id AND x.user_id = :u "
                       "AND x.role = 'observer')"), "observer"
    st = {"open": "c.status <> 'afgjort'", "closed": "c.status = 'afgjort'", "all": "TRUE"}[state]
    rows = (await session.execute(text(f"""
        SELECT c.id, c.source, c.origin, c.status, c.outcome, c.reason, c.dato, c.observer_involved, c.updated_at,
               c.snapshot->'fields' AS f, c.snapshot->>'observer' AS observer, c.afdelinger, a.navn AS assignee,
               (SELECT count(*) FROM dku_participants x WHERE x.case_id = c.id AND x.role = 'tipper') AS n_tippers,
               EXISTS (SELECT 1 FROM dku_messages m LEFT JOIN dku_participants p ON p.case_id = c.id AND p.user_id = :u
                       WHERE m.case_id = c.id AND m.user_id IS DISTINCT FROM :u AND {dku.visible_sql(role)}
                         AND m.created_at > coalesce(p.last_read_at, 'epoch')) AS unread
        FROM dku_cases c LEFT JOIN users a ON a.id = c.assignee_id
        WHERE {where} AND {st} ORDER BY c.updated_at DESC LIMIT 300"""), {"u": user_id, "afd": list(member_of)})).mappings().all()
    names = await afdeling_names(session)
    out = []
    for r in rows:
        item = {"id": r["id"], "source": r["source"], "status": r["status"], "status_label": dku.STATUSES[r["status"]],
                "outcome": r["outcome"], "outcome_label": dku.OUTCOMES.get(r["outcome"] or ""),
                "art": r["f"].get("art"), "antal": r["f"].get("antal"), "lok": r["f"].get("lok"), "dato": r["dato"].isoformat(),
                "unread": r["unread"], "updated_at": r["updated_at"].isoformat()}
        if role != "observer":
            item |= {"reason": dku.REASONS[r["reason"]], "observer": r["observer"]}
        if role == "dku":
            item |= {"origin": r["origin"], "afdelinger": [names.get(a, str(a)) for a in r["afdelinger"] or []],
                     "assignee": r["assignee"], "n_tippers": r["n_tippers"], "observer_involved": r["observer_involved"]}
        out.append(item)
    return out


async def messages(session: AsyncSession, case_id: int, role: str, user_id: int) -> list[dict]:
    """Sagens beskeder, som rollen må se dem."""
    rows = (await session.execute(text(f"""SELECT m.id, m.user_id, m.role, m.visibility, m.body, m.created_at FROM dku_messages m
        WHERE m.case_id = :c AND {dku.visible_sql(role)} ORDER BY m.created_at, m.id"""), {"c": case_id, "u": user_id})).mappings().all()
    return [dict(m) for m in rows]


async def tipper_names(session: AsyncSession, case_id: int) -> list[str]:
    return [n for _, n in (await session.execute(text("""SELECT u.id, coalesce(u.navn, u.obserkode) FROM dku_participants p
        JOIN users u ON u.id = p.user_id WHERE p.case_id = :c AND p.role = 'tipper'"""), {"c": case_id})).all()]


async def mark_read(session: AsyncSession, case_id: int, user_id: int, role: str) -> None:
    """Brugeren har set sagen nu (bliver deltager med rollen, hvis brugeren ikke var det)."""
    await session.execute(text("""INSERT INTO dku_participants (case_id, user_id, role, last_read_at) VALUES (:c, :u, :r, now())
        ON CONFLICT (case_id, user_id) DO UPDATE SET last_read_at = now()"""), {"c": case_id, "u": user_id, "r": role})


async def touch_read(session: AsyncSession, case_id: int, user_id: int) -> None:
    await session.execute(text("UPDATE dku_participants SET last_read_at = now() WHERE case_id = :c AND user_id = :u"),
                          {"c": case_id, "u": user_id})


# ---------------------------------------------------------------- godkendte kandidater

async def approve(session: AsyncSession, obsid: int, note: str, afdelinger: list[int], user_id: int) -> int:
    """Markér observationen som set igennem (1 hvis ny godkendelse, 0 hvis den var godkendt i forvejen)."""
    res = await session.execute(text("""INSERT INTO dku_reviews (obsid, decision, note, afdelinger, reviewed_by)
        VALUES (:o, 'godkendt', :n, CAST(:a AS smallint[]), :u) ON CONFLICT (obsid) DO NOTHING"""),
                                {"o": obsid, "n": note, "a": afdelinger, "u": user_id})
    return res.rowcount


async def unapprove(session: AsyncSession, obsid: int) -> None:
    await session.execute(text("DELETE FROM dku_reviews WHERE obsid = :o"), {"o": obsid})


# ---------------------------------------------------------------- DKU-ansvarlige pr. lokalafdeling

async def members_overview(session: AsyncSession) -> list[dict]:
    """Alle afdelinger (plus 'landsdækkende' = 0) med deres DKU-ansvarlige."""
    afd = [{"id": 0, "navn": "Landsdækkende (alle afdelinger)"}] + [
        {"id": i, "navn": n} for i, n in (await session.execute(text("SELECT id, navn FROM afdelinger ORDER BY navn"))).all()]
    rows = (await session.execute(text("""SELECT m.afdeling_id, u.id, coalesce(u.navn, u.obserkode) AS navn, u.obserkode
        FROM dku_members m JOIN users u ON u.id = m.user_id ORDER BY navn"""))).mappings().all()
    by: dict[int, list] = {}
    for r in rows:
        by.setdefault(r["afdeling_id"], []).append({"user_id": r["id"], "navn": r["navn"], "obserkode": r["obserkode"]})
    return [{**a, "members": by.get(a["id"], [])} for a in afd]


async def afdeling_exists(session: AsyncSession, afdeling_id: int) -> bool:
    return bool((await session.execute(text("SELECT 1 FROM afdelinger WHERE id = :a"), {"a": afdeling_id})).scalar())


async def approved_user_id(session: AsyncSession, obserkode: str) -> int | None:
    return (await session.execute(text("SELECT id FROM users WHERE obserkode = :k AND approved AND NOT blocked"),
                                  {"k": obserkode.strip().upper()})).scalar()


async def add_member(session: AsyncSession, afdeling_id: int, user_id: int, added_by: int) -> None:
    await session.execute(text("""INSERT INTO dku_members (afdeling_id, user_id, added_by) VALUES (:a, :u, :by)
        ON CONFLICT DO NOTHING"""), {"a": afdeling_id, "u": user_id, "by": added_by})


async def remove_member(session: AsyncSession, afdeling_id: int, user_id: int) -> None:
    await session.execute(text("DELETE FROM dku_members WHERE afdeling_id = :a AND user_id = :u"), {"a": afdeling_id, "u": user_id})
