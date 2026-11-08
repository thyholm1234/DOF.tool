"""SU-modulet: årsrapporter, åbent arkiv over godkendte SU-beskrivelser og sager med dokumentation og beskeder
(se services/su.py)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.cache import kv
from portal.db import get_session
from portal.models import SuCase, SuFile, SuMemberRead, SuMessage, SuObsApproval, SuReport, User
from portal.services import storage
from portal.services import su as svc
from portal.services import dataset

router = APIRouter(prefix="/api/su", tags=["su"])


async def ctx(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> dict:
    c = await svc.context(db, user)
    if not c["visible"]:
        raise HTTPException(404, "SU-modulet er ikke slået til")
    return c


def need_su(c: dict) -> None:
    if not c["member"]:
        raise HTTPException(403, "Kun for SU")


async def _case(db: AsyncSession, case_id: int, user: User) -> tuple[SuCase, str]:
    case = await db.get(SuCase, case_id)
    role = svc.role_in(case, user) if case else None
    if case is None or role is None:
        raise HTTPException(404, "Sagen findes ikke")
    return case, role


def _touch(case: SuCase) -> None:
    case.updated_at = datetime.now(timezone.utc)


async def _mark_read(db: AsyncSession, case: SuCase, user: User, role: str) -> None:
    now = datetime.now(timezone.utc)
    if case.user_id == user.id:  # også når indsenderen selv er SU-medlem (rollen er så «su»)
        case.read_by_owner_at = now
    if role == "su":
        await db.execute(insert(SuMemberRead).values(case_id=case.id, user_id=user.id, read_at=now)
                         .on_conflict_do_update(index_elements=["case_id", "user_id"], set_={"read_at": now}))


@router.get("/status")
async def status(c: dict = Depends(ctx)):
    return {**c, "regions": svc.REGIONS, "lande": svc.LANDE, "kinds": svc.KINDS, "statuses": svc.STATUSES, "outcomes": svc.OUTCOMES,
            "max_mb": svc.MAX_FILE // (1024 * 1024),
            "ext": {k: sorted(e[1:] for e in v) for k, v in svc.EXT.items()}}


# ---------------------------------------------------------------- årsrapporter

def _report(r: SuReport) -> dict:
    return {"id": r.id, "label": r.label, "title": r.title, "name": r.name, "size": r.size, "published": r.published,
            "url": f"/api/su/reports/{r.id}/file", "created_at": r.created_at.isoformat() if r.created_at else None}


@router.get("/reports")
async def reports(c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    q = select(SuReport).order_by(SuReport.sort_year.desc(), SuReport.label.desc())
    if not c["member"]:
        q = q.where(SuReport.published)
    return [_report(r) for r in (await db.execute(q)).scalars()]


LABEL = re.compile(r"^(\d{4})(?:-(\d{4}))?$")


def _label(label: str) -> int:
    m = LABEL.match(label.strip())
    if not m or not 1900 <= int(m[1]) <= 2100 or (m[2] and int(m[2]) < int(m[1])):
        raise HTTPException(400, "Året skrives som «2024» eller «1995-1996»")
    return int(m[2] or m[1])


@router.post("/reports")
async def upload_report(file: UploadFile = File(...), label: str = Form(..., max_length=20), title: str = Form("", max_length=200),
                        c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    need_su(c)
    sort_year = _label(label)
    data = await file.read(svc.MAX_REPORT + 1)
    if len(data) > svc.MAX_REPORT:
        raise HTTPException(400, f"Filen er for stor (højst {svc.MAX_REPORT // (1024 * 1024)} MB)")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Årsrapporten skal være en PDF")
    key = f"rapporter/{svc.secrets.token_hex(16)}.pdf"
    await storage.put(key, data, "application/pdf", svc.PREFIX)
    r = SuReport(label=label.strip(), sort_year=sort_year, title=title.strip(), key=key, name=(file.filename or f"SU-rapport {label}.pdf")[:200],
                 size=len(data), uploaded_by=user.id)
    db.add(r)
    await db.commit()
    return _report(r)


class ReportPatch(BaseModel):
    label: str | None = Field(None, max_length=20)
    title: str | None = Field(None, max_length=200)
    published: bool | None = None


@router.patch("/reports/{rid}")
async def patch_report(rid: int, body: ReportPatch, c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    need_su(c)
    r = await db.get(SuReport, rid)
    if r is None:
        raise HTTPException(404, "Rapporten findes ikke")
    if body.label is not None:
        r.sort_year, r.label = _label(body.label), body.label.strip()
    if body.title is not None:
        r.title = body.title.strip()
    if body.published is not None and body.published != r.published:
        r.published = body.published
        r.published_at = datetime.now(timezone.utc) if body.published else None
    await db.commit()
    return _report(r)


@router.delete("/reports/{rid}")
async def delete_report(rid: int, c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    need_su(c)
    r = await db.get(SuReport, rid)
    if r is None:
        raise HTTPException(404, "Rapporten findes ikke")
    await storage.delete(r.key, svc.PREFIX)
    await db.delete(r)
    await db.commit()
    return {"ok": True}


@router.api_route("/reports/{rid}/file", methods=["GET", "HEAD"], include_in_schema=False)
async def report_file(rid: int, request: Request, c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    r = await db.get(SuReport, rid)
    if r is None or not (r.published or c["member"]):
        raise HTTPException(404, "Rapporten findes ikke")
    return await _serve(request, r.key, r.name)


async def _serve(request: Request, key: str, name: str) -> Response:
    RANGE = storage.RANGE

    start = end = None
    m = RANGE.match(request.headers.get("range", "").strip())
    if m and (m[1] or m[2]):
        size = await storage.stat(key, svc.PREFIX)
        if size is None:
            raise HTTPException(404)
        start, end = (int(m[1]), min(int(m[2]), size - 1) if m[2] else size - 1) if m[1] else (max(0, size - int(m[2])), size - 1)
        if start >= size or end < start:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    obj = await storage.get(key, start, end, svc.PREFIX)
    if obj is None:
        raise HTTPException(404, "Filen findes ikke")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff", **svc.file_headers(name, key)}
    status = 200
    if start is not None:
        status = 206
        headers["Content-Range"] = f"bytes {start}-{start + len(obj.data) - 1}/{obj.size}"
    return Response(b"" if request.method == "HEAD" else obj.data, status_code=status, media_type=obj.content_type, headers=headers)


# ---------------------------------------------------------------- sager: liste og arkiv

def _summary(c: SuCase, *, private: bool) -> dict:
    out = {"id": c.id, "sagsnr": c.sagsnr, "art": c.art, "artnr": c.artnr, "antal": c.antal, "lokalitet": c.lokalitet, "region": c.region,
           "land": c.land, "dato_fra": c.dato_fra.isoformat() if c.dato_fra else None, "dato_til": c.dato_til.isoformat() if c.dato_til else None,
           "observatorer": c.observatorer, "status": c.status, "status_label": svc.STATUSES[c.status], "outcome": c.outcome,
           "outcome_label": svc.OUTCOMES.get(c.outcome or ""), "closed_at": c.closed_at.isoformat() if c.closed_at else None}
    if private:
        out |= {"submitted_at": c.submitted_at.isoformat() if c.submitted_at else None, "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                "uploader": c.up_navn}
    return out


@router.get("/archive")
async def archive(q: str = Query("", max_length=100), aar: int | None = Query(None, ge=1900, le=2100), region: str = Query("", max_length=8),
                  c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    """Det åbne arkiv: alle godkendte SU-beskrivelser, nyeste observation først."""
    where, args = ["status = 'afgjort'", "outcome = 'godkendt'"], {}
    if q.strip():
        where.append("(art ILIKE :q OR lokalitet ILIKE :q OR observatorer ILIKE :q OR sagsnr ILIKE :q)")
        args["q"] = f"%{q.strip()}%"
    if aar:
        where.append("extract(year FROM dato_fra) = :aar")
        args["aar"] = aar
    if region:
        where.append("region = :region")
        args["region"] = region
    ids = (await db.execute(text(f"SELECT id FROM su_cases WHERE {' AND '.join(where)} ORDER BY dato_fra DESC NULLS LAST, id DESC LIMIT 1000"), args)).scalars().all()
    rows = (await db.execute(select(SuCase).where(SuCase.id.in_(ids)))).scalars().all() if ids else []
    order = {i: n for n, i in enumerate(ids)}
    years = (await db.execute(text("""SELECT DISTINCT extract(year FROM dato_fra)::int FROM su_cases
        WHERE status = 'afgjort' AND outcome = 'godkendt' AND dato_fra IS NOT NULL ORDER BY 1 DESC"""))).scalars().all()
    return {"rows": [_summary(r, private=False) for r in sorted(rows, key=lambda r: order[r.id])], "years": years}


@router.get("/cases")
async def cases(view: str = Query("mine", pattern="^(mine|su)$"), state: str = Query("open", pattern="^(open|closed|all)$"),
                c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Mine sager (også kladder) eller SU's sagsliste (alle indsendte; ulæste markeret)."""
    q = select(SuCase)
    if view == "su":
        need_su(c)
        q = q.where(SuCase.status != "kladde")
    else:
        q = q.where(SuCase.user_id == user.id)
    if state == "open":
        q = q.where(SuCase.status != "afgjort")
    elif state == "closed":
        q = q.where(SuCase.status == "afgjort")
    rows = (await db.execute(q.order_by(SuCase.updated_at.desc()).limit(500))).scalars().all()
    reads = {}
    if view == "su" and rows:
        reads = dict((await db.execute(select(SuMemberRead.case_id, SuMemberRead.read_at)
                                        .where(SuMemberRead.user_id == user.id, SuMemberRead.case_id.in_([r.id for r in rows])))).all())
    out = []
    for r in rows:
        d = _summary(r, private=True)
        d["unread"] = (r.status in svc.OPEN and (r.id not in reads or r.updated_at > reads[r.id])) if view == "su" else False
        out.append(d)
    return out


# ---------------------------------------------------------------- én sag

class CaseIn(BaseModel):
    up_navn: str = Field(min_length=1, max_length=120)
    up_adresse: str = Field("", max_length=200)
    up_postnr: str = Field("", max_length=10)
    up_by: str = Field("", max_length=80)
    up_email: str = Field(min_length=3, max_length=200)
    up_telefon: str = Field("", max_length=30)
    artnr: int | None = None
    art: str = Field("", max_length=120)  # sættes fra artslisten ud fra artnr
    dato_fra: date
    dato_til: date | None = None
    lokalitet: str = Field("", max_length=200)  # i Danmark: navnet sættes fra DOFbasens lokalitet (loknr)
    loknr: int | None = None
    region: str = Field(min_length=1, max_length=8)
    land: str = Field("Danmark", max_length=40)
    antal: int = Field(ge=1, le=100000)
    dragt: str = Field("", max_length=200)
    adfaerd: str = Field("", max_length=200)
    observatorer: str = Field("", max_length=500)
    bemaerkninger: str = Field("", max_length=500)
    beskrivelse: str = Field("", max_length=50000)
    obsid: int | None = None

    @field_validator("up_email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v.strip()):
            raise ValueError("Ugyldig e-mail")
        return v.strip()

    def check(self) -> None:
        if self.region not in svc.REGIONS:
            raise HTTPException(400, "Ukendt region")
        if self.land not in svc.LANDE:
            raise HTTPException(400, "Ukendt land")
        if self.dato_til and self.dato_til < self.dato_fra:
            raise HTTPException(400, "Slutdatoen ligger før startdatoen")
        if self.dato_fra > date.today():
            raise HTTPException(400, "Observationsdatoen ligger i fremtiden")


@router.get("/species")
async def su_species(c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    """SU-arterne (artslistens klasse SU) med danske navne – kun dem kan vælges i SU-formularen."""
    rows = (await db.execute(text("""SELECT artnr, navn FROM species WHERE klass = 'SU' AND NOT coalesce(excluded, false)
        ORDER BY navn"""))).mappings().all()
    return [dict(r) for r in rows]


async def _place(db: AsyncSession, body: "CaseIn") -> None:
    """I Danmark vælges lokaliteten blandt DOFbasens lokaliteter (loknr, navnet sættes derfra); på Færøerne og i Grønland
    skrives den som fri tekst."""
    if body.land == "Danmark":
        navn = (await db.execute(text("SELECT navn FROM locations WHERE loknr = :l"), {"l": body.loknr})).scalar() if body.loknr else None
        if not navn:
            raise HTTPException(400, "Vælg lokaliteten fra DOFbasens lokaliteter")
        body.lokalitet = navn
    else:
        body.loknr = None
        if not body.lokalitet.strip():
            raise HTTPException(400, "Skriv lokaliteten")


async def _su_species(db: AsyncSession, body: "CaseIn") -> None:
    """Arten skal være en SU-art; navnet sættes fra artslisten (dansk)."""
    navn = (await db.execute(text("SELECT navn FROM species WHERE artnr = :a AND klass = 'SU' AND NOT coalesce(excluded, false)"),
                             {"a": body.artnr})).scalar() if body.artnr else None
    if not navn:
        raise HTTPException(400, "Vælg en SU-art fra listen")
    body.art = navn


@router.post("/cases")
async def create_case(body: CaseIn, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Trin 1–2: uploader og beskrivelsesinfo gemmes som kladde, som dokumentationen derefter lægges på."""
    body.check()
    await _su_species(db, body)
    await _place(db, body)
    if (await db.execute(text("SELECT count(*) FROM su_cases WHERE user_id = :u AND status = 'kladde'"), {"u": user.id})).scalar() >= 20:
        raise HTTPException(429, "Du har allerede mange kladder – indsend eller slet nogle af dem")
    case = SuCase(user_id=user.id, status="kladde", **{**body.model_dump(), "dato_til": body.dato_til or body.dato_fra})
    db.add(case)
    await db.commit()
    return {"id": case.id}


@router.put("/cases/{case_id}")
async def update_case(case_id: int, body: CaseIn, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    case, role = await _case(db, case_id, user)
    if role != "owner" or case.status != "kladde":
        raise HTTPException(400, "Beskrivelsen kan ikke ændres efter indsendelse – skriv til SU i sagen")
    body.check()
    await _su_species(db, body)
    await _place(db, body)
    for k, v in {**body.model_dump(), "dato_til": body.dato_til or body.dato_fra}.items():
        setattr(case, k, v)
    _touch(case)
    await db.commit()
    return {"ok": True}


@router.delete("/cases/{case_id}")
async def delete_case(case_id: int, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Afbryd: en kladde slettes med sine filer."""
    case, role = await _case(db, case_id, user)
    if role != "owner" or case.status != "kladde":
        raise HTTPException(400, "Kun kladder kan slettes")
    for f in (await db.execute(select(SuFile).where(SuFile.case_id == case.id))).scalars():
        await storage.delete(f.key, svc.PREFIX)
    await db.delete(case)
    await db.commit()
    return {"ok": True}


def _file(f: SuFile, names: dict[int, str]) -> dict:
    return {"id": f.id, "kind": f.kind, "kind_label": svc.KINDS[f.kind], "name": f.name, "size": f.size, "info": f.info,
            "content_type": f.content_type, "url": f"/api/su/files/{f.id}", "by": names.get(f.uploaded_by),
            "created_at": f.created_at.isoformat() if f.created_at else None}


async def _names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {r.id: r.navn or r.obserkode for r in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()}


@router.get("/cases/{case_id}")
async def get_case(case_id: int, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    case, role = await _case(db, case_id, user)
    files = (await db.execute(select(SuFile).where(SuFile.case_id == case.id).order_by(SuFile.id))).scalars().all()
    msgs = []
    if role in ("su", "owner"):
        q = select(SuMessage).where(SuMessage.case_id == case.id).order_by(SuMessage.id)
        if role == "owner":
            q = q.where(~SuMessage.internal)
        msgs = (await db.execute(q)).scalars().all()
    names = await _names(db, {m.user_id for m in msgs if m.user_id} | {f.uploaded_by for f in files if f.uploaded_by} | ({case.assignee_id} if case.assignee_id else set()))
    out = {
        **_summary(case, private=role != "public"), "role": role,
        "dragt": case.dragt, "adfaerd": case.adfaerd, "bemaerkninger": case.bemaerkninger, "beskrivelse": case.beskrivelse,
        "region_label": svc.REGIONS.get(case.region, case.region), "loknr": case.loknr, "obsid": case.obsid,
        "outcome_note": case.outcome_note if case.status == "afgjort" else None,
        "files": [_file(f, names) for f in files],
        "can": {"edit": role == "owner" and case.status == "kladde",
                "files": (role == "owner" and case.status in ("kladde", "afventer", "behandles", "indsendt")) or role == "su",
                "write": role == "su" or (role == "owner" and case.status in svc.OPEN), "manage": role == "su"},
    }
    if role in ("su", "owner"):
        out |= {"uploader_info": {k: getattr(case, k) for k in ("up_navn", "up_adresse", "up_postnr", "up_by", "up_email", "up_telefon")},
                "messages": [{"id": m.id, "role": m.role, "internal": m.internal, "mine": m.user_id == user.id, "body": m.body,
                              "author": "SU" if (m.role == "su" and role == "owner") else "DOF.tool" if m.role == "system" else names.get(m.user_id, "Slettet bruger"),
                              "created_at": m.created_at.isoformat()} for m in msgs]}
    if role == "su":
        out["assignee"] = names.get(case.assignee_id)
    await _mark_read(db, case, user, role)
    await db.commit()
    return out


@router.post("/cases/{case_id}/files")
async def upload_file(case_id: int, tasks: BackgroundTasks, file: UploadFile = File(...), kind: str = Form(...), info: str = Form("", max_length=2000),
                      c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    case, role = await _case(db, case_id, user)
    if not (role == "su" or (role == "owner" and case.status != "afgjort")):
        raise HTTPException(403, "Du kan ikke tilføje filer til sagen")
    try:
        ext = svc.file_kind_ok(kind, file.filename or "")
    except svc.SuError as exc:
        raise HTTPException(400, str(exc)) from exc
    if (await db.execute(text("SELECT count(*) FROM su_files WHERE case_id = :c"), {"c": case.id})).scalar() >= 60:
        raise HTTPException(400, "Sagen har allerede mange filer")
    data = await file.read(svc.MAX_FILE + 1)
    if len(data) > svc.MAX_FILE:
        raise HTTPException(400, f"Filen er for stor (højst {svc.MAX_FILE // (1024 * 1024)} MB)")
    if not data:
        raise HTTPException(400, "Filen er tom")
    key, ctype = await svc.put_file(case.id, data, ext)
    f = SuFile(case_id=case.id, kind=kind, name=(file.filename or "fil")[:200], key=key, content_type=ctype, size=len(data),
               info=info.strip(), uploaded_by=user.id)
    db.add(f)
    if case.status != "kladde":
        db.add(SuMessage(case_id=case.id, user_id=None, role="system", internal=False,
                         body=f"{user.navn or user.obserkode} har tilføjet dokumentation: {f.name}"))
        _touch(case)
    await db.commit()
    if case.status != "kladde" and role == "owner":
        tasks.add_task(svc.notify, case.id, to="su", title="ny dokumentation", body=f.name)
    return _file(f, await _names(db, {user.id}))


@router.delete("/cases/{case_id}/files/{fid}")
async def delete_file(case_id: int, fid: int, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    case, role = await _case(db, case_id, user)
    f = await db.get(SuFile, fid)
    if f is None or f.case_id != case.id:
        raise HTTPException(404, "Filen findes ikke")
    if not (role == "su" or (role == "owner" and case.status == "kladde")):
        raise HTTPException(403, "Filer kan kun slettes, mens beskrivelsen er en kladde")
    await storage.delete(f.key, svc.PREFIX)
    await db.delete(f)
    await db.commit()
    return {"ok": True}


@router.api_route("/files/{fid}", methods=["GET", "HEAD"], include_in_schema=False)
async def get_file(fid: int, request: Request, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    f = await db.get(SuFile, fid)
    case = await db.get(SuCase, f.case_id) if f else None
    if case is None or svc.role_in(case, user) is None:
        raise HTTPException(404, "Filen findes ikke")
    return await _serve(request, f.key, f.name)


@router.post("/cases/{case_id}/submit")
async def submit(case_id: int, tasks: BackgroundTasks, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Trin 4: indsenderen bekræfter – sagen får et sagsnummer og går til SU. Derefter kan beskrivelsen ikke ændres."""
    case, role = await _case(db, case_id, user)
    if role != "owner" or case.status != "kladde":
        raise HTTPException(400, "Beskrivelsen er allerede indsendt")
    has_files = (await db.execute(text("SELECT count(*) FROM su_files WHERE case_id = :c"), {"c": case.id})).scalar()
    if not case.beskrivelse.strip() and not has_files:
        raise HTTPException(400, "Skriv en beskrivelse eller upload den som dokument, før du indsender")
    now = datetime.now(timezone.utc)
    case.status, case.submitted_at, case.read_by_owner_at = "indsendt", now, now
    for _ in range(5):
        case.sagsnr = await svc.next_sagsnr(db)
        try:
            async with db.begin_nested():
                await db.flush()
            break
        except IntegrityError:
            continue
    db.add(SuMessage(case_id=case.id, user_id=None, role="system", internal=False,
                     body=f"SU-beskrivelsen er indsendt som sag {case.sagsnr}. SU kigger på den og skriver her, hvis der er spørgsmål."))
    _touch(case)
    await db.commit()
    tasks.add_task(svc.notify, case.id, to="su", title=f"ny SU-beskrivelse ({case.sagsnr})",
                   body=f"{case.antal or ''} {case.art}, {case.lokalitet} {case.dato_fra.isoformat() if case.dato_fra else ''}".strip())
    return {"ok": True, "sagsnr": case.sagsnr}


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    internal: bool = False


@router.post("/cases/{case_id}/messages")
async def post_message(case_id: int, body: MessageIn, tasks: BackgroundTasks, c: dict = Depends(ctx), user: User = Depends(require_user),
                       db: AsyncSession = Depends(get_session)):
    """SU ↔ indsender. SU's besked til indsenderen sætter sagen til «afventer»; indsenderens svar sætter den tilbage til «behandles».
    SU's interne noter ses aldrig af indsenderen."""
    case, role = await _case(db, case_id, user)
    if role == "public" or (role == "owner" and case.status not in svc.OPEN):
        raise HTTPException(400, "Du kan ikke skrive i sagen")
    if body.internal and role != "su":
        raise HTTPException(403, "Kun SU kan skrive interne noter")
    if not await kv().set(f"su:msg:{user.id}", "1", nx=True, ex=3):
        raise HTTPException(429, "Vent et øjeblik før næste besked")
    db.add(SuMessage(case_id=case.id, user_id=user.id, role="su" if role == "su" else "uploader", internal=body.internal, body=body.body.strip()))
    if role == "su" and not body.internal and case.status in svc.OPEN:
        case.status = "afventer"
        case.assignee_id = case.assignee_id or user.id
    elif role == "owner" and case.status == "afventer":
        case.status = "behandles"
    _touch(case)
    await _mark_read(db, case, user, role)
    await db.commit()
    preview = body.body.strip()[:200]
    if role == "owner":
        tasks.add_task(svc.notify, case.id, to="su", title="svar fra indsenderen", body=preview)
    elif body.internal:
        tasks.add_task(svc.notify, case.id, to="su", title="intern note", body=preview, exclude=user.id)
    else:
        tasks.add_task(svc.notify, case.id, to="owner", title="ny besked fra SU", body=preview)
    return {"ok": True, "status": case.status}


class ActionIn(BaseModel):
    action: str = Field(pattern="^(take|close|reopen)$")
    outcome: str | None = Field(None, pattern="^(godkendt|ikke_godkendt|henlagt)$")
    note: str = Field("", max_length=8000)


@router.post("/cases/{case_id}/actions")
async def action(case_id: int, body: ActionIn, tasks: BackgroundTasks, c: dict = Depends(ctx), user: User = Depends(require_user),
                 db: AsyncSession = Depends(get_session)):
    """SU's handlinger: tag sagen, afgør den (godkendt → åbent arkiv) eller genåbn den."""
    case, role = await _case(db, case_id, user)
    if role != "su":
        raise HTTPException(403, "Kun SU kan behandle sagen")
    who, note, now = user.navn or user.obserkode, body.note.strip(), datetime.now(timezone.utc)
    if body.action == "take":
        if case.status == "afgjort":
            raise HTTPException(400, "Sagen er afgjort")
        case.assignee_id = user.id
        if case.status == "indsendt":
            case.status = "behandles"
        db.add(SuMessage(case_id=case.id, role="system", internal=True, body=f"{who} har taget sagen."))
    elif body.action == "close":
        if not body.outcome:
            raise HTTPException(400, "Vælg en afgørelse")
        if case.status == "afgjort":
            raise HTTPException(400, "Sagen er allerede afgjort")
        case.status, case.outcome, case.outcome_note, case.closed_at = "afgjort", body.outcome, note or None, now
        case.assignee_id = case.assignee_id or user.id
        db.add(SuMessage(case_id=case.id, role="system", internal=False, body=f"SU har afgjort sagen: {svc.OUTCOMES[body.outcome]}."
                         + (" Den ligger nu i SU's åbne arkiv." if body.outcome == "godkendt" else "")))
    else:
        if case.status != "afgjort":
            raise HTTPException(400, "Sagen er ikke afgjort")
        case.status, case.outcome, case.outcome_note, case.closed_at = "behandles", None, None, None
        db.add(SuMessage(case_id=case.id, role="system", internal=False, body=f"SU har genåbnet sagen." + (f"\n{note}" if note else "")))
    _touch(case)
    await _mark_read(db, case, user, role)
    await db.commit()
    if body.action == "close":
        tasks.add_task(svc.notify, case.id, to="owner", title="sagen er afgjort", body=svc.OUTCOMES[body.outcome] + (f": {note[:180]}" if note else ""))
    elif body.action == "reopen":
        tasks.add_task(svc.notify, case.id, to="owner", title="sagen er genåbnet", body=note[:200] or "SU har genåbnet sagen.")
    return {"ok": True, "status": case.status}



# ---------------------------------------------------------------- godkendelse af SU-arter i DOFbasen

@router.get("/obs")
async def su_observations(aar: int | None = Query(None, ge=1990, le=2100), state: str = Query("afventer", pattern="^(afventer|godkendt|ikke_godkendt|alle)$"),
                          c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Fund af SU-arter i DOFbasen i et år – samlet pr. art × lokalitet × dag (som trådene), så 75 indtastninger af den samme
    fugl er ét fund. Kun for SU. Afventende har intet mærke; godkendte får det gyldne SU-mærke på alle fundets observationer.
    Observationerne og afgørelserne hentes hver for sig (hver sin databasegruppe)."""
    need_su(c)
    year = aar or date.today().year
    rows = [dict(r) for r in (await dataset.execute(db, """
        SELECT o.dato, o.artnr, s.navn AS art, o.loknr, l.navn AS lokalitet, count(*) AS n_obs, max(o.antal) AS max_antal,
               count(DISTINCT o.obserkode) AS n_observers, min(o.obsid) AS obsid,
               left(string_agg(DISTINCT trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')), ', '), 200) AS observatorer,
               left(string_agg(DISTINCT nullif(o.fuglnoter, ''), ' · '), 300) AS fuglnoter
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        LEFT JOIN observers ob ON ob.obserkode = o.obserkode
        WHERE o.kategori = 'SU' AND o.hemmelig = 0 AND o.loknr IS NOT NULL AND o.dato BETWEEN make_date(:y, 1, 1) AND make_date(:y, 12, 31)
        GROUP BY o.dato, o.artnr, s.navn, s.sortering, o.loknr, l.navn
        ORDER BY o.dato DESC, s.sortering, l.navn LIMIT 3000""", {"y": year})).mappings()]
    dec = {}
    if rows:
        dec = {(a.dato, a.artnr, a.loknr): a for a in (await db.execute(select(SuObsApproval).where(
            SuObsApproval.dato.between(date(year, 1, 1), date(year, 12, 31))))).scalars()}
    names = await _names(db, {a.decided_by for a in dec.values() if a.decided_by})
    reports = await _reports_for(db, rows, user)
    out, counts = [], {"afventer": 0, "godkendt": 0, "ikke_godkendt": 0}
    for r in rows:
        r["reports"] = reports(r)
        a = dec.get((r["dato"], r["artnr"], r["loknr"]))
        r["key"] = f"{r['dato'].isoformat()}/{r['artnr']}/{r['loknr']}"
        r["dato"] = r["dato"].isoformat()
        r["decision"] = a.decision if a else "afventer"
        r["note"], r["decided_by"] = (a.note, names.get(a.decided_by)) if a else ("", None)
        r["decided_at"] = a.decided_at.isoformat() if a and a.decided_at else None
        counts[r["decision"]] += 1
        if state in ("alle", r["decision"]):
            out.append(r)
    return {"aar": year, "rows": out, "counts": counts}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9æøå]+", " ", (s or "").lower()).strip()


async def _reports_for(db: AsyncSession, rows: list[dict], user: User):
    """Indsendte SU-beskrivelser (ikke kladder), der kan høre til et fund: samme art og en datoperiode, der rammer fundets dag
    (± 1 dag). Formularens lokalitet er fri tekst, så «samme lokalitet» afgøres ud fra navnet (det ene indeholder det andet)
    – andre beskrivelser af arten på datoen vises som mulige. Returnerer en funktion: række → liste af beskrivelser."""
    artnrs = sorted({r["artnr"] for r in rows})
    if not artnrs:
        return lambda r: []
    cases = (await db.execute(select(SuCase).where(SuCase.status != "kladde", SuCase.artnr.in_(artnrs)))).scalars().all()
    if not cases:
        return lambda r: []
    reads = dict((await db.execute(select(SuMemberRead.case_id, SuMemberRead.read_at)
                                    .where(SuMemberRead.user_id == user.id, SuMemberRead.case_id.in_([c.id for c in cases])))).all())
    by_art: dict[int, list[SuCase]] = {}
    for c in cases:
        by_art.setdefault(c.artnr, []).append(c)
    one = timedelta(days=1)

    def match(r: dict) -> list[dict]:
        d, lok = r["dato"], _norm(r.get("lokalitet") or "")
        out = []
        for c in by_art.get(r["artnr"], []):
            if not c.dato_fra or not (c.dato_fra - one <= d <= (c.dato_til or c.dato_fra) + one):
                continue
            name = _norm(c.lokalitet)
            same = c.loknr == r["loknr"] or (bool(name) and bool(lok) and (name in lok or lok in name))
            out.append({"id": c.id, "sagsnr": c.sagsnr, "status": c.status, "status_label": svc.STATUSES[c.status], "outcome": c.outcome,
                        "outcome_label": svc.OUTCOMES.get(c.outcome or ""), "lokalitet": c.lokalitet, "same_place": same,
                        "uploader": c.up_navn, "unread": c.status in svc.OPEN and (c.id not in reads or c.updated_at > reads[c.id])})
        return sorted(out, key=lambda x: not x["same_place"])
    return match


KEY = re.compile(r"^(\d{4}-\d{2}-\d{2})/(\d+)/(\d+)$")


class DecideIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=500)  # «dato/artnr/loknr» – samme nøgle som trådene
    decision: str = Field(pattern="^(godkendt|ikke_godkendt|)$")  # tom = fortryd (tilbage til afventer)
    note: str = Field("", max_length=2000)


@router.post("/obs/decide")
async def decide(body: DecideIn, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Godkend (eller afvis/fortryd) fund. Kun fund, der rummer en ikke-hemmelig observation af en SU-art, kan afgøres."""
    need_su(c)
    keys = []
    for k in dict.fromkeys(body.keys):
        m = KEY.match(k)
        if not m:
            raise HTTPException(400, f"Ugyldig nøgle: {k}")
        keys.append((date.fromisoformat(m[1]), int(m[2]), int(m[3])))
    now, done = datetime.now(timezone.utc), 0
    if not body.decision:
        for d, a, l in keys:
            done += (await db.execute(SuObsApproval.__table__.delete().where(SuObsApproval.dato == d, SuObsApproval.artnr == a,
                                                                             SuObsApproval.loknr == l))).rowcount or 0
        await db.commit()
        return {"ok": True, "n": done}
    valid = set((await dataset.execute(db, """SELECT DISTINCT o.dato, o.artnr, o.loknr FROM observations o
        JOIN unnest(CAST(:d AS date[]), CAST(:a AS int[]), CAST(:l AS int[])) AS k(dato, artnr, loknr)
          ON k.dato = o.dato AND k.artnr = o.artnr AND k.loknr = o.loknr
        WHERE o.kategori = 'SU' AND o.hemmelig = 0""", {"d": [k[0] for k in keys], "a": [k[1] for k in keys], "l": [k[2] for k in keys]})).all())
    for d, a, l in keys:
        if (d, a, l) not in valid:
            continue
        await db.execute(insert(SuObsApproval).values(dato=d, artnr=a, loknr=l, decision=body.decision, note=body.note.strip(), decided_by=user.id, decided_at=now)
                         .on_conflict_do_update(index_elements=["dato", "artnr", "loknr"],
                                                set_={"decision": body.decision, "note": body.note.strip(), "decided_by": user.id, "decided_at": now}))
        done += 1
    await db.commit()
    return {"ok": True, "n": done}
