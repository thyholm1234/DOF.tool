"""Årslister og scoreboards (Boligbirding/Fugleliga), beregnet direkte i PostgreSQL.

Lister: DK, lokalafdeling, kommune, matrikel (#BB-tag i tur-/fuglenoter), lokalitet og
brugerens egne områder (polygon/radius). År kan være et årstal eller 'all' (livsliste).
Scoreboards caches i Valkey og invalideres når nye observationer for deltagere kommer ind.

Matrikler (som i Boligbirding): matrikel 1 tæller på scoreboards (#BB26 eller #BB26-1), matrikel 2+
er private (#BB26-2 ...). Hver matrikel kan have perioder (adresse, start, evt. slut). Flytter man,
afsluttes den gamle periode og en ny starter; listen tæller kun den aktive periode, så fremgangen
nulstilles ved flytning, mens tidligere perioder kan vælges (eller 'alle perioder' som historik).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from portal.timeutil import dk_today

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, version
from portal.models import Setting
from portal.services.areas import Scope, parse_scope


@dataclass(frozen=True)
class ListSpec:
    scope: Scope
    year: int | None  # None = alle år
    matrikel: int | None = None  # 1, 2, ... (kun matrikel-lister)

    @property
    def key(self) -> str:
        return f"{self.scope.key}:{self.year or 'all'}:{self.matrikel or 0}"


def parse_list_spec(scope: str | None, year: str | int | None, matrikel: int | None = None) -> ListSpec:
    y = None if year in (None, "", "all") else int(year)
    return ListSpec(parse_scope(scope), y, matrikel or None)


async def matrikel_prefix(session: AsyncSession) -> str:
    row = await session.get(Setting, "matrikel_tag")
    value = (row.value if row else None) or "#BB"
    return re.sub(r"[^#A-Za-z0-9]", "", str(value)).upper() or "#BB"


# ---------------------------------------------------------------- matrikelperioder

MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(9999, 12, 31)


@dataclass(frozen=True)
class Period:
    id: int
    navn: str
    start: date
    slut: date | None

    def contains(self, d: date) -> bool:
        return self.start <= d and (self.slut is None or d <= self.slut)

    def overlaps(self, a: date, b: date) -> bool:
        return self.start <= b and (self.slut or MAX_DATE) >= a

    def as_dict(self) -> dict:
        return {"id": self.id, "navn": self.navn, "start": self.start.isoformat(),
                "slut": self.slut.isoformat() if self.slut else None}


def list_range(year: int | None) -> tuple[date, date]:
    today = dk_today()
    if year is None:
        return MIN_DATE, today
    return date(year, 1, 1), min(date(year, 12, 31), today)


def select_active(periods: list[Period], start: date, end: date, ref: date | None = None) -> Period | None:
    """Den periode der gælder for listen [start, end]: den der dækker referencedatoen (i dag, klemt ind i
    intervallet), ellers den senest startede før den, ellers den første i intervallet."""
    if ref is None:
        ref = min(max(dk_today(), start), end)
    overlapping = [p for p in periods if p.overlaps(start, end)]
    if not overlapping:
        return None
    covering = [p for p in overlapping if p.contains(ref)]
    if covering:
        return max(covering, key=lambda p: p.start)
    before = [p for p in overlapping if p.start <= ref]
    if before:
        return max(before, key=lambda p: p.start)
    return min(overlapping, key=lambda p: p.start)


def period_windows(periods: list[Period], year: int | None, choice: str | None = None
                   ) -> tuple[list[tuple[date, date]] | None, dict]:
    """Datovinduer for en matrikelliste. None = ingen begrænsning (matriklen har ingen perioder).
    choice: None (aktiv periode), 'alle' (alle perioder = historik) eller et periode-id."""
    if not periods:
        return None, {"options": [], "selected": None, "active": None}
    start, end = list_range(year)
    options = sorted((p for p in periods if p.overlaps(start, end)), key=lambda p: p.start)
    active = select_active(options, start, end)
    info = {"options": [p.as_dict() for p in options], "active": active.id if active else None}
    if choice == "alle":
        return [(p.start, p.slut or MAX_DATE) for p in periods], {**info, "selected": "alle"}
    chosen = next((p for p in options if str(p.id) == str(choice)), None) if choice else None
    chosen = chosen or active
    if chosen is None:
        return [], {**info, "selected": None}
    return [(chosen.start, chosen.slut or MAX_DATE)], {**info, "selected": chosen.id}


async def load_periods(session: AsyncSession, codes: list[str], nummer: int) -> dict[str, list[Period]]:
    rows = (await session.execute(text("""
        SELECT u.obserkode, p.id, p.navn, p.start, p.slut FROM matrikel_perioder p
        JOIN matrikler m ON m.id = p.matrikel_id JOIN users u ON u.id = m.user_id
        WHERE m.nummer = :n AND u.obserkode = ANY(:codes) ORDER BY p.start
    """), {"n": nummer, "codes": codes})).all()
    out: dict[str, list[Period]] = {}
    for kode, pid, navn, start, slut in rows:
        out.setdefault(kode, []).append(Period(pid, navn, start, slut))
    return out


def _where(spec: ListSpec, prefix: str, windows: list[tuple[date, date]] | None = None) -> tuple[str, dict]:
    pred, params = spec.scope.predicate("o")
    parts = [pred, "s.list_artnr IS NOT NULL", "NOT ls.excluded", "o.antal > 0"]
    if spec.year:
        parts.append("o.dato BETWEEN :y0 AND :y1")
        params.update({"y0": date(spec.year, 1, 1), "y1": date(spec.year, 12, 31)})
    parts.append("o.dato <= current_date")
    if spec.matrikel:
        # #BB26 eller #BB26-1 for matrikel 1, #BB26-2 for matrikel 2 osv. Årstallet skal passe til observationen.
        tag = "(-1)?" if spec.matrikel == 1 else f"-{spec.matrikel}"
        parts.append(
            "upper(coalesce(t.turnoter,'') || ' ' || coalesce(o.fuglnoter,'')) "
            "~ ('(^|[^A-Z0-9])' || :mprefix || to_char(o.dato, 'YY') || :mtag || '([^A-Z0-9-]|$)')"
        )
        params.update({"mprefix": prefix.replace("#", "#"), "mtag": tag})
    if windows is not None:
        if not windows:
            parts.append("FALSE")
        else:
            ors = []
            for i, (ws, we) in enumerate(windows):
                ors.append(f"o.dato BETWEEN :w{i}s AND :w{i}e")
                params.update({f"w{i}s": ws, f"w{i}e": we})
            parts.append("(" + " OR ".join(ors) + ")")
    return " AND ".join(parts), params


async def matrikel_windows(session: AsyncSession, obserkode: str, spec: ListSpec, choice: str | None = None
                           ) -> tuple[list[tuple[date, date]] | None, dict | None]:
    if not spec.matrikel:
        return None, None
    periods = (await load_periods(session, [obserkode], spec.matrikel)).get(obserkode, [])
    return period_windows(periods, spec.year, choice)


async def year_list(session: AsyncSession, obserkode: str, spec: ListSpec,
                    windows: list[tuple[date, date]] | None = None) -> list[dict]:
    prefix = await matrikel_prefix(session)
    where, params = _where(spec, prefix, windows)
    sql = f"""
        SELECT DISTINCT ON (s.list_artnr) s.list_artnr AS artnr, ls.navn AS art, ls.klass, ls.sortering,
               o.dato, o.loknr, l.navn AS lok, o.obsid
        FROM observations o
        JOIN species s ON s.artnr = o.artnr
        JOIN species ls ON ls.artnr = s.list_artnr
        LEFT JOIN trips t ON t.turid = o.turid
        LEFT JOIN locations l ON l.loknr = o.loknr
        WHERE o.obserkode = :kode AND {where}
        ORDER BY s.list_artnr, o.dato, o.obsid
    """
    rows = (await session.execute(text(sql), {"kode": obserkode, **params})).mappings().all()
    out = [{**dict(r), "dato": r["dato"].isoformat()} for r in rows]
    out.sort(key=lambda r: (r["dato"], r["obsid"]))
    return out


async def participants(session: AsyncSession, group_id: int | None = None) -> list[dict]:
    if group_id:
        rows = (await session.execute(text("""
            SELECT m.obserkode, coalesce(u.navn, trim(coalesce(ob.fornavn,'') || ' ' || coalesce(ob.efternavn,'')), m.obserkode) AS navn
            FROM list_group_members m LEFT JOIN users u ON u.obserkode = m.obserkode
            LEFT JOIN observers ob ON ob.obserkode = m.obserkode WHERE m.group_id = :g
        """), {"g": group_id})).all()
    else:
        rows = (await session.execute(text("""
            SELECT obserkode, coalesce(navn, obserkode) FROM users
            WHERE NOT blocked AND coalesce((prefs->>'lists_public')::boolean, true)
        """))).all()
    return [{"obserkode": r[0], "navn": r[1] or r[0]} for r in rows]


async def scoreboard(session: AsyncSession, spec: ListSpec, group_id: int | None = None) -> dict:
    """Rangliste + hver deltagers 'firsts' (til race-graf, matrix og unikke arter)."""
    key = f"sb:{spec.key}:{group_id or 0}:{await version('lists')}"

    async def produce() -> dict:
        people = await participants(session, group_id)
        if spec.scope.kind == "afdeling" and not group_id:
            # Afdelingsboards: kun deltagere der har valgt afdelingen (som i Boligbirding)
            opted = await _opted(session, "afdelinger", spec.scope.ref)
            people = [p for p in people if p["obserkode"] in opted]
        if spec.scope.kind == "kommune" and not group_id:
            opted = await _opted(session, "kommuner", spec.scope.ref)
            people = [p for p in people if p["obserkode"] in opted]
        codes = [p["obserkode"] for p in people]
        if not codes:
            return {"rows": [], "species": {}, "spec": spec.key}
        prefix = await matrikel_prefix(session)
        where, params = _where(spec, prefix)
        window_join = ""
        if spec.matrikel:
            # Hver deltager tæller kun sin aktive matrikelperiode (ingen perioder = alle fund med tagget)
            periods = await load_periods(session, codes, spec.matrikel)
            wk, ws, we = [], [], []
            for c in codes:
                windows, _ = period_windows(periods.get(c, []), spec.year)
                for a, b in (windows if windows is not None else [(MIN_DATE, MAX_DATE)]):
                    wk.append(c); ws.append(a); we.append(b)
            window_join = ("JOIN unnest(CAST(:wk AS text[]), CAST(:ws AS date[]), CAST(:we AS date[])) AS w(kode, s, e) "
                           "ON w.kode = o.obserkode AND o.dato BETWEEN w.s AND w.e")
            params.update(wk=wk, ws=ws, we=we)
        firsts = (await session.execute(text(f"""
            SELECT o.obserkode, s.list_artnr AS artnr, min(o.dato) AS d
            FROM observations o JOIN species s ON s.artnr = o.artnr JOIN species ls ON ls.artnr = s.list_artnr
            LEFT JOIN trips t ON t.turid = o.turid {window_join}
            WHERE o.obserkode = ANY(:codes) AND {where}
            GROUP BY o.obserkode, s.list_artnr
        """), {"codes": codes, **params})).all()
        per: dict[str, list[tuple[int, str]]] = {c: [] for c in codes}
        for kode, artnr, d in firsts:
            per[kode].append((artnr, d.isoformat()))
        species_ids = {a for lst in per.values() for a, _ in lst}
        names = {}
        if species_ids:
            names = {r[0]: r[1] for r in (await session.execute(
                text("SELECT artnr, navn FROM species WHERE artnr = ANY(:a)"), {"a": list(species_ids)})).all()}
        rows = []
        for p in people:
            lst = sorted(per[p["obserkode"]], key=lambda x: x[1])
            last = lst[-1] if lst else None
            rows.append({
                "obserkode": p["obserkode"], "navn": p["navn"], "antal": len(lst),
                "sidste_art": names.get(last[0]) if last else None, "sidste_dato": last[1] if last else None,
                "firsts": lst,
            })
        rows.sort(key=lambda r: (-r["antal"], r["sidste_dato"] or "9999"))
        place = 0
        prev = None
        for i, r in enumerate(rows, start=1):
            if r["antal"] != prev:
                place = i
                prev = r["antal"]
            r["placering"] = place
        return {"rows": rows, "species": {str(k): v for k, v in names.items()}, "spec": spec.key}

    return await cached(key, 6 * 3600, produce)


async def _opted(session: AsyncSession, field: str, ref: str) -> set[str]:
    rows = (await session.execute(text(f"""
        SELECT obserkode FROM users WHERE prefs->'{field}' ? :ref
    """), {"ref": str(ref)})).all()
    return {r[0] for r in rows}


async def available_years(session: AsyncSession, obserkode: str) -> list[int]:
    rows = (await session.execute(text("""
        SELECT DISTINCT extract(year FROM dato)::int FROM observations WHERE obserkode = :k ORDER BY 1 DESC
    """), {"k": obserkode})).all()
    return [r[0] for r in rows]


async def _tag_stats(session: AsyncSession, obserkode: str) -> dict[int, dict]:
    """Matrikelnumre fundet i brugerens turnoter (#BB26 / #BB26-1 = 1, #BB26-3 = 3) med antal ture og datoer."""
    prefix = await matrikel_prefix(session)
    rows = (await session.execute(text("""
        SELECT coalesce(m[1], '1') AS n, count(DISTINCT t.turid), min(t.dato), max(t.dato)
        FROM trips t, regexp_matches(upper(coalesce(t.turnoter, '')), :rx, 'g') AS m
        WHERE t.obserkode = :k AND upper(t.turnoter) LIKE :like GROUP BY 1
    """), {"k": obserkode, "rx": f"{prefix}[0-9]{{2}}(?:-([0-9]+))?(?![A-Z0-9])", "like": f"%{prefix}%"})).all()
    return {int(n): {"n_trips": c, "first": a.isoformat(), "last": b.isoformat()} for n, c, a, b in rows if str(n).isdigit()}


async def matrikel_numbers(session: AsyncSession, obserkode: str) -> list[int]:
    stats = await _tag_stats(session, obserkode)
    defined = (await session.execute(text("""
        SELECT m.nummer FROM matrikler m JOIN users u ON u.id = m.user_id WHERE u.obserkode = :k
    """), {"k": obserkode})).scalars().all()
    return sorted({1, *stats, *defined})


async def matrikel_overview(session: AsyncSession, user_id: int, obserkode: str) -> dict:
    prefix = await matrikel_prefix(session)
    stats = await _tag_stats(session, obserkode)
    defined = {r[0]: {"id": r[1], "navn": r[2]} for r in (await session.execute(text(
        "SELECT nummer, id, navn FROM matrikler WHERE user_id = :u"), {"u": user_id})).all()}
    yy = str(dk_today().year)[2:]
    today = dk_today()
    out = []
    for n in sorted({1, *stats, *defined}):
        plist = (await load_periods(session, [obserkode], n)).get(obserkode, [])
        active = select_active(plist, MIN_DATE, today, ref=today)
        out.append({
            "nummer": n, "navn": defined.get(n, {}).get("navn") or ("Hjemme" if n == 1 else f"Matrikel {n}"),
            "defined": n in defined, "scoreboard": n == 1,
            "tag": f"{prefix}{yy}" if n == 1 else f"{prefix}{yy}-{n}",
            **stats.get(n, {"n_trips": 0, "first": None, "last": None}),
            "perioder": [p.as_dict() for p in plist], "active": active.id if active else None,
        })
    return {"prefix": prefix, "matrikler": out}


# ---------------------------------------------------------------- matrikelbirding (eget modul)

def rerank(rows: list[dict]) -> list[dict]:
    """Ny placering efter filtrering (lige mange arter = samme placering)."""
    out, place, prev = [], 0, None
    for i, r in enumerate(rows, start=1):
        if r["antal"] != prev:
            place, prev = i, r["antal"]
        out.append({**r, "placering": place})
    return out


async def matrikel_board(session: AsyncSession, year: int | None, scope: str = "dk", group_id: int | None = None) -> dict:
    """Boligbirding-scoreboard (matrikel 1). Afdelings- og kommuneboards er deltagerne der har valgt
    området under profil – listen er stadig deres matrikel, uanset hvor den ligger."""
    spec = ListSpec(parse_scope("dk"), year, 1)
    board = await scoreboard(session, spec, group_id)
    kind, _, ref = scope.partition(":")
    rows = board["rows"]
    if kind in ("afd", "kom") and not group_id:
        opted = await _opted(session, "afdelinger" if kind == "afd" else "kommuner", ref)
        rows = [r for r in rows if r["obserkode"] in opted]
    # Kun deltagere med matrikelobservationer i perioden
    return {**board, "rows": rerank([r for r in rows if r["antal"] > 0])}


def _standing(label: str, kind: str, ref: str, rows: list[dict], me: str, my_count: int) -> dict:
    lean = [{k: r[k] for k in ("obserkode", "navn", "antal", "placering", "sidste_art", "sidste_dato")} for r in rows]
    mine = next((r for r in lean if r["obserkode"] == me), None)
    # Er listen ikke offentlig, vises den placering man ville have haft
    place = mine["placering"] if mine else 1 + sum(1 for r in lean if r["antal"] > my_count)
    above = next((r for r in reversed(lean) if r["antal"] > my_count), None)
    below = next((r for r in lean if r["antal"] < my_count), None)
    return {"label": label, "kind": kind, "ref": ref, "n": len(lean) + (0 if mine else 1), "placering": place,
            "public": mine is not None, "leader": lean[0] if lean else None,
            "above": {**above, "gap": above["antal"] - my_count} if above else None,
            "below": {**below, "gap": my_count - below["antal"]} if below else None,
            "top": lean[:5]}


async def matrikel_summary(session: AsyncSession, user_id: int, obserkode: str, prefs: dict, year: int | None) -> dict:
    spec = ListSpec(parse_scope("dk"), year, 1)
    windows, period = await matrikel_windows(session, obserkode, spec)
    items = await year_list(session, obserkode, spec, windows)
    n = len(items)
    dk = await scoreboard(session, spec)
    standings = [_standing("Danmark", "dk", "dk", dk["rows"], obserkode, n)]
    afd = {r[0]: r[1] for r in (await session.execute(text("SELECT id, navn FROM afdelinger"))).all()}
    kom = {r[0]: r[1] for r in (await session.execute(text("SELECT kode, navn FROM kommuner"))).all()}
    for a in prefs.get("afdelinger", []):
        if str(a).isdigit() and int(a) in afd:
            board = await matrikel_board(session, year, f"afd:{a}")
            standings.append(_standing(afd[int(a)], "afd", f"afd:{a}", board["rows"], obserkode, n))
    for k in prefs.get("kommuner", []):
        if k in kom:
            board = await matrikel_board(session, year, f"kom:{k}")
            standings.append(_standing(f"{kom[k]} Kommune", "kom", f"kom:{k}", board["rows"], obserkode, n))
    groups = (await session.execute(text("""
        SELECT g.id, g.name FROM list_groups g JOIN list_group_members m ON m.group_id = g.id
        WHERE m.obserkode = :k ORDER BY g.name
    """), {"k": obserkode})).all()
    for gid, name in groups:
        board = await scoreboard(session, spec, gid)
        standings.append(_standing(name, "group", str(gid), board["rows"], obserkode, n))
    overview = await matrikel_overview(session, user_id, obserkode)
    m1 = next(m for m in overview["matrikler"] if m["nummer"] == 1)
    return {"year": year, "count": n, "items": items, "periode": period, "standings": standings,
            "tag": m1["tag"], "navn": m1["navn"], "n_trips": m1["n_trips"], "first": m1["first"], "last": m1["last"],
            "matrikler": [{k: m[k] for k in ("nummer", "navn", "tag", "n_trips")} for m in overview["matrikler"]]}
