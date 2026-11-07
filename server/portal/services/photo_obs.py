"""Fotos ↔ DOFbasen-observationer (docs/netfugl-afloesning-spec.md §8).

Databasegrupperne må ikke blandes i én SQL: observationer slås op i observationsgruppen, og fotoer i brugergruppen – to forespørgsler,
ingen join mellem ``gal_*`` og ``observations``. ``obsid`` gemmes som tal uden fremmednøgle."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from portal.services import dataset

OBS = """
    SELECT o.obsid, o.dato, o.antal, o.artnr, o.loknr, l.navn AS lokalitet, l.lat, l.lon, o.koen, o.adfkode, o.alderkode, o.dragtkode,
           ca.text AS adf_tekst, cd.text AS dragt_tekst, s.navn AS art
    FROM observations o LEFT JOIN locations l ON l.loknr = o.loknr LEFT JOIN species s ON s.artnr = o.artnr
         LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = o.adfkode LEFT JOIN code_texts cd ON cd.kind = 'dragt' AND cd.code = o.dragtkode"""


async def mine(session: AsyncSession, obserkode: str, artnr: int, dato: date) -> list[dict]:
    """Brugerens egne observationer af arten ±1 dag."""
    rows = (await dataset.execute(session, OBS + " WHERE o.obserkode = :k AND o.artnr = :a AND o.dato BETWEEN :f AND :t ORDER BY o.dato, o.obsid",
                                  {"k": obserkode, "a": artnr, "f": dato - timedelta(days=1), "t": dato + timedelta(days=1)})).mappings().all()
    return [_out(r) for r in rows]


def _out(r) -> dict:
    return {"obsid": r["obsid"], "dato": r["dato"].isoformat(), "antal": r["antal"], "artnr": r["artnr"], "art": r["art"], "loknr": r["loknr"], "lokalitet": r["lokalitet"],
            "lat": r["lat"], "lon": r["lon"], "koen": {"M": "han", "F": "hun"}.get(r["koen"] or ""), "adfaerd": r["adf_tekst"], "dragt": r["dragt_tekst"],
            "alderkode": r["alderkode"]}


async def get_own(session: AsyncSession, obserkode: str, obsid: int) -> dict | None:
    """Observationen, hvis den tilhører obserkoden (ellers None)."""
    r = (await dataset.execute(session, OBS + " WHERE o.obsid = :o AND o.obserkode = :k", {"o": obsid, "k": obserkode}, obserkode=obserkode)).mappings().first()
    return _out(r) if r else None


async def to_codes(session: AsyncSession, obs: dict) -> dict:
    """Oversæt observationens køn/adfærd/dragt/alder til fotomodulets koder (via gal_codes.dofbasen_code, ellers ens tekst/kode)."""
    codes = (await session.execute(text("SELECT kind, code, text, dofbasen_code FROM gal_codes"))).mappings().all()

    def find(kind: str, text_: str | None, raw: str | None = None) -> str | None:
        for c in codes:
            if c["kind"] != kind:
                continue
            if raw and (c["dofbasen_code"] or "").casefold() == raw.casefold():
                return c["code"]
        for c in codes:
            if c["kind"] == kind and ((text_ and c["text"].casefold() == text_.casefold()) or (raw and c["code"] == raw.casefold())):
                return c["code"]
        return None
    return {"koen": obs.get("koen"), "adfaerd": find("adfaerd", obs.get("adfaerd")), "dragt": find("dragt", obs.get("dragt")),
            "alder": find("alder", None, obs.get("alderkode"))}


async def for_observations(session: AsyncSession, obsids: list[int], limit: int = 200) -> dict[int, list[dict]]:
    """Publicerede fotos pr. observation (til visning på observationen i DOFbasen-modulet)."""
    ids = list(dict.fromkeys(obsids))[:limit]
    if not ids:
        return {}
    rows = (await session.execute(text("""
        SELECT p.obsid, p.id, m.thumb_key, m.storage_key FROM gal_photos p LEFT JOIN content_media m ON m.id = p.media_id
        WHERE p.obsid = ANY(:ids) AND p.status = 'publiceret' ORDER BY p.id"""), {"ids": ids})).mappings().all()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["obsid"], []).append({"id": r["id"], "thumb": "/media/art/" + (r["thumb_key"] or r["storage_key"])})
    return out
