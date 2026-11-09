"""Nye arter og underarter fra DOFbasen – og ændret SU/SUB-status: portalen tilpasser sig selv med det samme.

Når en observation har et artsnummer, portalen ikke kender, opretter indlæsningen (dof/ingest.py) straks arten med
DOFbasens navn og latinske navn og en foreløbig type ud fra navnet (art, underart, hybrid, ubestemt). Derefter:

1. discovered(): referenceversionen tælles op (så klassifikatoren kender arten, og den ikke «opdages» igen), og ét
   job «new_species» lægges i gatewayens opslagsbane. Det udføres med det samme – uden at vente på udtrækkenes interval
   – og henter artslisten (den/de lister, arten hører til) og SU/SUB-listerne fra DOFbasen.
2. reclassify(): kategorien (SU/SUB/bemærk/alm) regnes igen for artens observationer – alle, uden tidsgrænse. Dem fra
   i dag, der bliver bemærkelsesværdige, sendes gennem alarmerne som nye observationer. En ny art for Danmark giver
   dermed en SU-alarm sekunder efter, at den første gang dukker op i live-hentningen.

Samme genberegning sker, hver gang artslisten eller SU/SUB-listerne hentes ellers (natkørslen): for alle arter, hvis
klassifikation har ændret sig (klass_snapshot/changed_since) – fx en art, der er kommet på SU-listen.
"""

from __future__ import annotations

import logging

from sqlalchemy import select, text

from portal.cache import bump, publish
from portal.db import ObsSession, SessionLocal
from portal.dof import gateway
from portal.dof.classify import NOTABLE, get_classifier
from portal.models import Species
from portal.timeutil import dk_today

log = logging.getLogger("portal.newspecies")

# DOFbasens artdata-lister (artdata.php?list=…) pr. foreløbig type
LIST_FOR_TYPE = {"art": "art", "underart": "art", "hybrid": "hybrid", "ub": "ub", "andre": "andre"}


async def discovered(artnrs: set[int]) -> str | None:
    """Nye arter: bestil artsliste og SU/SUB-status i opslagsbanen (udføres straks). Returnerer job-id'et."""
    if not artnrs:
        return None
    await bump("reference")
    async with ObsSession() as s:
        rows = (await s.execute(select(Species.artnr, Species.navn, Species.art_type).where(Species.artnr.in_(artnrs)))).all()
    lists = sorted({LIST_FOR_TYPE.get(t or "art", "art") for _, _, t in rows} or {"art"})
    nrs = sorted(artnrs)
    job = await gateway.enqueue("new_species", {"artnrs": nrs, "lists": lists}, priority=gateway.INTERACTIVE,
                                dedupe="new_species:" + ",".join(map(str, nrs)))
    log.info("Nye arter fra DOFbasen: %s – artsliste (%s) og SU/SUB-status hentes nu",
             ", ".join(f"{navn} ({nr})" for nr, navn, _ in rows), ", ".join(lists))
    return job


async def klass_snapshot() -> dict[int, str]:
    """Alle arters klassifikation lige nu (før en hentning af artsliste eller SU/SUB-status)."""
    async with ObsSession() as s:
        return dict((await s.execute(select(Species.artnr, Species.klass))).all())


async def changed_since(before: dict[int, str]) -> set[int]:
    after = await klass_snapshot()
    return {a for a, k in after.items() if before.get(a) != k}


async def reclassify(artnrs: set[int] | list[int]) -> dict:
    """Regn kategorien igen for arternes observationer (alle, uden tidsgrænse). Observationer fra i dag, der bliver
    bemærkelsesværdige, behandles af alarmerne som nye."""
    artnrs = sorted(set(artnrs))
    if not artnrs:
        return {"species": 0, "changed": 0, "upgraded": 0}
    today = dk_today()
    async with ObsSession() as s:
        clf = await get_classifier(s)
        rows = (await s.execute(text("""
            SELECT o.obsid, o.artnr, sp.navn, o.dato, o.antal, o.afdelinger, o.kategori
            FROM observations o JOIN species sp ON sp.artnr = o.artnr WHERE o.artnr = ANY(:a)"""), {"a": artnrs})).all()
        updates, upgraded = [], []
        for obsid, artnr, navn, dato, antal, afd, old in rows:
            new = clf.kategori(artnr, navn, dato, antal or 0, list(afd or []))
            if new == old:
                continue
            updates.append({"o": obsid, "k": new})
            if new in NOTABLE and old not in NOTABLE and dato == today:
                upgraded.append(obsid)
        if updates:
            await s.execute(text("UPDATE observations SET kategori = :k WHERE obsid = :o"), updates)
            await s.commit()
    if upgraded:
        try:
            from portal.services import alerts

            async with SessionLocal() as s:
                await alerts.process(s, upgraded, [])
        except Exception:  # noqa: BLE001 – alarmerne må ikke vælte hentningen af referencedata
            log.exception("Alarmer efter ny klassifikation fejlede")
    if updates:
        for v in ("feed", "lists", "regions", "locyear"):
            await bump(v)
        await publish("feed", {"type": "feed", "changed": len(updates)})
        log.info("Ny klassifikation for %s art(er): kategorien er rettet for %s observationer (%s bemærkelsesværdige i dag)",
                 len(artnrs), len(updates), len(upgraded))
    return {"species": len(artnrs), "changed": len(updates), "upgraded": len(upgraded)}
