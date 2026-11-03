"""Lydquiz under Læring: «Hvilken art synger?» ud fra optagelser fra xeno-canto og Macaulay Library.

* xeno-canto: optagelser findes automatisk pr. art via xeno-canto's API (v3, kræver en gratis nøgle) første gang en art indgår i en test,
  og gemmes i snd_recordings. Nøgle og filtre (kvalitet, område, type, længde) ligger i Admin → Funktioner.
* Macaulay Library har intet åbent API: redaktører lægger optagelser ind med link og art.
* Lyden afspilles i en almindelig lydafspiller via portalen (filen hentes og gemmes lokalt), så intet artsnavn kan ses under afspilning;
  optager, licens og link til originalen vises først efter svaret."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.config import get_settings
from portal.models import SoundRecording
from portal.services import species_quiz
from portal.services.setting_store import get_setting_dict, put_setting

log = logging.getLogger("portal.soundquiz")
KEY = "sound_quiz"
DEFAULTS = {"xc_key": "", "min_quality": "B", "area": "europe", "types": "song", "per_species": 12, "max_length": 60, "extra": ""}
QUALITIES = ("A", "B", "C")
AREAS = ("", "europe", "denmark")
TYPES = ("", "song", "call")
MAX_FETCHES = 25  # nye arter, der højst slås op hos xeno-canto pr. test
MAX_BYTES = 40 * 1024 * 1024
AUDIO_TYPES = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
               "audio/ogg": ".ogg", "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a"}
ML_AUDIO = "https://cdn.download.ams.birds.cornell.edu/api/v1/asset/{}/audio"
ML_REF = re.compile(r"^\s*(?:(?:https?://)?(?:www\.)?macaulaylibrary\.org/asset/|ML\s*)?(\d{4,12})/?\s*$", re.IGNORECASE)


class SoundError(Exception):
    pass


# ---------------------------------------------------------------- indstillinger

async def settings(db: AsyncSession) -> dict:
    return await get_setting_dict(db, KEY, DEFAULTS)


def public_settings(s: dict) -> dict:
    """Indstillingerne til admin-siden – nøglen sendes aldrig tilbage, kun om den er sat og de sidste tegn."""
    key = s.get("xc_key") or ""
    return {**{k: v for k, v in s.items() if k != "xc_key"}, "has_key": bool(key), "key_hint": key[-4:] if key else ""}


async def save_settings(db: AsyncSession, body: dict) -> dict:
    s = await settings(db)
    if body.get("clear_key"):
        s["xc_key"] = ""
    elif (k := (body.get("xc_key") or "").strip()):
        if not re.fullmatch(r"[A-Za-z0-9_\-]{8,80}", k):
            raise SoundError("Nøglen ser forkert ud (kun bogstaver, tal og tegnene _ og -)")
        s["xc_key"] = k
    if body.get("min_quality") in QUALITIES:
        s["min_quality"] = body["min_quality"]
    if body.get("area") in AREAS:
        s["area"] = body["area"]
    if body.get("types") in TYPES:
        s["types"] = body["types"]
    for k, lo, hi in (("per_species", 3, 50), ("max_length", 5, 300)):
        if body.get(k) is not None:
            s[k] = max(lo, min(hi, int(body[k])))
    if body.get("extra") is not None:
        extra = str(body["extra"]).strip()
        if len(extra) > 200:
            raise SoundError("Ekstra søgetekst er for lang")
        s["extra"] = extra
    await put_setting(db, KEY, s)
    return s


def xc_query(latin: str, s: dict) -> str:
    gen, sp = latin.split()[:2]
    parts = [f"gen:{gen}", f"sp:{sp}", {"A": "q:A", "B": 'q:">C"', "C": 'q:">D"'}[s["min_quality"]]]
    if s["area"] == "europe":
        parts.append("area:europe")
    elif s["area"] == "denmark":
        parts.append("cnt:denmark")
    if s["types"]:
        parts.append(f"type:{s['types']}")
    if s["extra"]:
        parts.append(s["extra"])
    return " ".join(parts)


# ---------------------------------------------------------------- xeno-canto

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True, headers={"User-Agent": get_settings().dof_user_agent})


def _secs(v: str | None) -> int | None:
    m = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+)|(\d+):(\d+)", (v or "").strip())
    if not m:
        return None
    parts = [int(x) for x in v.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


async def xc_search(latin: str, s: dict) -> list[dict]:
    """Optagelserne for en art fra xeno-canto's API (filtreret efter indstillingerne). Kaster SoundError ved fejl."""
    if not s["xc_key"]:
        raise SoundError("Der er ikke sat en xeno-canto-nøgle op (Admin → Funktioner → Lydquiz)")
    async with _client() as c:
        try:
            resp = await c.get("https://xeno-canto.org/api/3/recordings", params={"query": xc_query(latin, s), "key": s["xc_key"], "per_page": 100})
        except httpx.HTTPError as exc:
            raise SoundError("xeno-canto svarer ikke lige nu") from exc
    if resp.status_code in (401, 403):
        raise SoundError("xeno-canto afviste nøglen – tjek den under Admin → Funktioner → Lydquiz")
    if resp.status_code == 429:
        raise SoundError("xeno-canto begrænser antallet af kald – prøv igen om lidt")
    try:
        data = resp.json()
    except ValueError as exc:
        raise SoundError(f"xeno-canto svarede {resp.status_code} uden gyldigt svar") from exc
    if resp.status_code != 200 or data.get("error"):
        raise SoundError(f"xeno-canto: {data.get('error', resp.status_code)} {data.get('message', '')}".strip())
    gen, sp = latin.lower().split()[:2]
    out = []
    for r in data.get("recordings", []):
        length = _secs(r.get("length"))
        if str(r.get("gen", "")).lower() != gen or str(r.get("sp", "")).lower() != sp or not r.get("file") or length is None or length > s["max_length"]:
            continue
        lic = r.get("lic") or ""
        out.append({"ext_id": str(r["id"]), "recordist": r.get("rec"), "license_url": ("https:" + lic if lic.startswith("//") else lic) or None,
                    "license": _lic_name(lic), "rtype": r.get("type"), "quality": r.get("q"), "length_s": length, "country": r.get("cnt"),
                    "page_url": f"https://xeno-canto.org/{r['id']}", "file_url": r["file"] if r["file"].startswith("http") else "https:" + r["file"]})
    return out


def _lic_name(url: str) -> str | None:
    m = re.search(r"licenses/([a-z\-]+)/([\d.]+)", url or "")
    return f"CC {m.group(1).upper()} {m.group(2)}" if m else None


async def ensure_species(db: AsyncSession, artnr: int, *, force: bool = False) -> int:
    """Antal optagelser af arten; slår dem op hos xeno-canto første gang (og igen efter 30 dage). Arter uden xeno-canto-træf huskes i 7 dage."""
    have = await _count(db, artnr)
    marker = f"snd:fetched:{artnr}"
    if not force and (have or await kv().exists(marker)):
        return have
    latin = (await db.execute(text("SELECT latin FROM species WHERE artnr = :a AND NOT excluded"), {"a": artnr})).scalar()
    if not latin or len(latin.split()) < 2 or "/" in latin:
        return have
    s = await settings(db)
    found = await xc_search(latin, s)
    random.shuffle(found)
    for r in found[: s["per_species"]]:
        await db.execute(text("""
            INSERT INTO snd_recordings (source, ext_id, artnr, latin, recordist, license, license_url, rtype, quality, length_s, country, page_url, file_url)
            VALUES ('xc', :ext_id, :a, :latin, :recordist, :license, :license_url, :rtype, :quality, :length_s, :country, :page_url, :file_url)
            ON CONFLICT (source, ext_id) DO NOTHING"""), {**r, "a": artnr, "latin": latin})
    await db.commit()
    await kv().set(marker, "1", ex=(30 if found else 7) * 86400)
    return await _count(db, artnr)


async def _count(db: AsyncSession, artnr: int) -> int:
    return (await db.execute(text("SELECT count(*) FROM snd_recordings WHERE artnr = :a AND enabled"), {"a": artnr})).scalar() or 0


async def counts(db: AsyncSession) -> dict[str, int]:
    return {str(a): n for a, n in (await db.execute(text("SELECT artnr, count(*) FROM snd_recordings WHERE enabled GROUP BY artnr"))).all()}


async def clear_cache(db: AsyncSession) -> int:
    """Glem alle automatisk hentede xeno-canto-optagelser (ML-optagelser beholdes), så de hentes igen med de nye filtre."""
    n = (await db.execute(text("DELETE FROM snd_recordings WHERE source = 'xc'"))).rowcount
    await db.commit()
    async for k in kv().scan_iter("snd:fetched:*"):
        await kv().delete(k)
    return n


# ---------------------------------------------------------------- lydfiler (hentes og gemmes lokalt)

def _dir() -> Path:
    d = Path(get_settings().media_dir) / "snd"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def audio_file(source: str, ext_id: str, url: str) -> Path:
    """Lydfilen – hentes første gang og genbruges derefter (lås mod samtidige hentninger)."""
    d, base = _dir(), f"{source}-{ext_id}"
    if hit := next(d.glob(base + ".*"), None):
        if hit.suffix != ".part":
            return hit
    r = kv()
    while not await r.set(f"snd:dl:{base}", "1", nx=True, ex=120):
        await asyncio.sleep(0.5)
        if hit := next((p for p in d.glob(base + ".*") if p.suffix != ".part"), None):
            return hit
    try:
        async with _client() as c, c.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise SoundError(f"Kilden leverede ikke lydfilen ({resp.status_code})")
            ext = AUDIO_TYPES.get(resp.headers.get("content-type", "").split(";")[0].strip().lower())
            if ext is None:
                raise SoundError("Kilden leverede ikke en lydfil")
            tmp, size = d / f"{base}.part", 0
            with tmp.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        f.close()
                        tmp.unlink(missing_ok=True)
                        raise SoundError("Optagelsen er for stor (højst 40 MB)")
                    f.write(chunk)
            target = d / f"{base}{ext}"
            tmp.rename(target)
            return target
    except httpx.HTTPError as exc:
        raise SoundError("Kilden svarer ikke lige nu") from exc
    finally:
        await r.delete(f"snd:dl:{base}")


_prefetch_sem = asyncio.Semaphore(3)
_tasks: set[asyncio.Task] = set()


def prefetch(recs: list[dict]) -> None:
    """Hent lydfilerne i baggrunden, så de er klar, når brugeren når spørgsmålet."""
    async def one(r):
        async with _prefetch_sem:
            try:
                await audio_file(r["source"], r["ext_id"], r["file_url"])
            except Exception as exc:  # noqa: BLE001 – afspilningen prøver igen og viser fejlen
                log.info("forhåndshentning af %s-%s mislykkedes: %s", r["source"], r["ext_id"], exc)

    for r in recs:
        t = asyncio.create_task(one(r))
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)


# ---------------------------------------------------------------- Macaulay Library (redaktører)

def parse_ml_ref(ref: str) -> str:
    m = ML_REF.match(ref or "")
    if not m:
        raise SoundError("Indsæt et Macaulay-link (fx https://macaulaylibrary.org/asset/123456789) eller et ML-nummer")
    return m.group(1)


async def add_ml(db: AsyncSession, ref: str, artnr: int, user_id: int, rtype: str | None, recordist: str | None) -> dict:
    ext = parse_ml_ref(ref)
    sp = (await db.execute(text("SELECT artnr, navn, latin FROM species WHERE artnr = :a AND NOT excluded"), {"a": artnr})).mappings().first()
    if sp is None:
        raise SoundError("Ukendt art")
    if (await db.execute(text("SELECT 1 FROM snd_recordings WHERE source = 'ml' AND ext_id = :e"), {"e": ext})).first():
        raise SoundError(f"ML{ext} er allerede lagt ind")
    url = ML_AUDIO.format(ext)
    await audio_file("ml", ext, url)  # afviser links, hvor lyden ikke kan hentes
    rec = SoundRecording(source="ml", ext_id=ext, artnr=artnr, latin=sp["latin"], recordist=(recordist or "").strip()[:200] or None,
                         license="© Macaulay Library, Cornell Lab of Ornithology", rtype=(rtype or "").strip()[:80] or None,
                         page_url=f"https://macaulaylibrary.org/asset/{ext}", file_url=url, added_by=user_id)
    db.add(rec)
    await db.commit()
    return {"id": rec.id, "ext_id": ext, "artnr": artnr, "navn": sp["navn"], "latin": sp["latin"]}


# ---------------------------------------------------------------- quizzen

def _extra(r) -> dict:
    return {"source": r["source"], "ext_id": r["ext_id"], "recordist": r["recordist"], "license": r["license"], "license_url": r["license_url"],
            "url": r["page_url"], "rtype": r["rtype"], "country": r["country"]}


async def build(db: AsyncSession, artnrs: list[int], method: str, n: int = species_quiz.N_QUESTIONS) -> list[dict]:
    if method not in ("valg", "tekst"):
        raise species_quiz.QuizError("Ukendt svarmetode")
    artnrs = sorted(set(artnrs))
    if len(artnrs) < species_quiz.MIN_SPECIES:
        raise species_quiz.QuizError(f"Vælg mindst {species_quiz.MIN_SPECIES} arter")
    if len(artnrs) > 600:
        raise species_quiz.QuizError("For mange arter")
    rng = random.Random()
    sp = {r["artnr"]: r for r in (await db.execute(text("SELECT artnr, navn, latin, list_artnr FROM species WHERE artnr = ANY(:a) AND NOT excluded"),
                                                   {"a": artnrs})).mappings()}
    order = [a for a in artnrs if a in sp]
    rng.shuffle(order)
    per: dict[int, list] = {}
    fetches, last_error = 0, None
    for a in order:  # arter uden optagelser slås op hos xeno-canto, til der er nok arter
        if len(per) >= n:
            break
        if not await _count(db, a) and not await kv().exists(f"snd:fetched:{a}"):
            if fetches >= MAX_FETCHES:
                continue
            fetches += 1
            try:
                await ensure_species(db, a)
            except SoundError as exc:
                last_error = str(exc)
                if "nøgle" in last_error:
                    break  # alle opslag vil fejle på samme måde
                continue
        rows = (await db.execute(text("SELECT id, source, ext_id, recordist, license, license_url, rtype, country, page_url, file_url "
                                      "FROM snd_recordings WHERE artnr = :a AND enabled ORDER BY random()"), {"a": a})).mappings().all()
        if rows:
            per[a] = list(rows)
    have = list(per)
    if len(have) < species_quiz.MIN_SPECIES:
        raise species_quiz.QuizError(last_error or "Der er ikke optagelser nok af de valgte arter endnu – vælg flere arter.")
    k = -(-n // len(have)) + 1
    picked: list[tuple[int, dict]] = []
    if len(have) >= n:
        picked = [(a, per[a][0]) for a in have[:n]]
    else:
        for rnd in range(k):
            picked += [(a, per[a][rnd]) for a in rng.sample(have, len(have)) if len(per[a]) > rnd]
        picked = picked[:n]
    rng.shuffle(picked)
    names = {a: sp[a]["navn"] for a in have}
    if method == "valg":
        await species_quiz.fill_names(db, rng, have, names, "EXISTS (SELECT 1 FROM snd_recordings r WHERE r.artnr = s.artnr AND r.enabled)")
    out = []
    for a, r in picked:
        data = {"artnr": a, "liste": sp[a]["list_artnr"] or a, "navn": sp[a]["navn"], "latin": sp[a]["latin"], "extra": _extra(r), "rid": r["id"]}
        token = await species_quiz.store_token(data, "quiz:snd:")
        q = {"id": token, "media_url": f"/api/learn/lyd/audio/{token}", "rtype": r["rtype"] and r["rtype"].split(",")[0].strip().lower(), "metode": method}
        if method == "valg":
            q["choices"] = species_quiz.choices(rng, names, a)
        out.append(q)
    prefetch([dict(r) for _, r in picked])
    return out


async def question_file(db: AsyncSession, token: str) -> Path | None:
    raw = await kv().get("quiz:snd:" + token)
    if not raw:
        return None
    r = (await db.execute(text("SELECT source, ext_id, file_url FROM snd_recordings WHERE id = :i"), {"i": json.loads(raw)["rid"]})).mappings().first()
    return await audio_file(r["source"], r["ext_id"], r["file_url"]) if r else None
