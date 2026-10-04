from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AdminUser,
    BlacklistedUser,
    CommunityNews,
    Observation,
    ObservationComment,
    PageViewEvent,
    PushSubscription,
    UserPreference,
)
from backend.app.db.session import SessionLocal, get_db
from backend.app.services.observation_store import upsert_rows
from backend.app.api.routes.observations import legacy_row_to_ingest
from backend.app.services.dof_enrichment import enrich_observations, load_dof_enrichment

router = APIRouter(tags=["legacy-extended"])
FRONTEND_DIR = Path(__file__).resolve().parents[4] / "frontend"
SERVER_LOG = Path(__file__).resolve().parents[4] / "server.log"


def _user_id(payload: dict[str, Any]) -> str:
    return str(payload.get("user_id") or payload.get("userid") or "").strip()


def _serialize_observation(item: Observation) -> dict[str, Any]:
    return {
        "obsid": item.obsid,
        "species": item.species,
        "location": item.location_name,
        "category": item.category,
        "count": item.count,
        "observed_at": item.observed_at,
        "latitude": item.latitude,
        "longitude": item.longitude,
    }


@router.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@router.post("/api/log-pageview")
async def log_pageview(payload: dict[str, Any], request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    db.add(
        PageViewEvent(
            path=str(payload.get("url") or payload.get("path") or "/")[:240],
            user_id=str(payload.get("user_id") or "") or None,
            referrer=str(payload.get("referrer") or "") or None,
            device_id=str(payload.get("device_id") or request.headers.get("user-agent") or "")[:120] or None,
        )
    )
    await db.commit()
    return {"ok": True}


@router.post("/api/prefs")
async def legacy_preferences(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    user_id = _user_id(payload)
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id kræves")
    pref = await db.get(UserPreference, user_id)
    if pref is None:
        pref = UserPreference(user_id=user_id)
        db.add(pref)
    current = json.loads(pref.afdelinger or "{}") if pref.afdelinger else {}
    updates = payload.get("prefs")
    if isinstance(updates, dict):
        current.update(updates)
        pref.afdelinger = json.dumps(current, ensure_ascii=False)
        await db.commit()
    return current


@router.post("/api/userinfo")
async def legacy_userinfo(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    pref = await db.get(UserPreference, _user_id(payload))
    return {"obserkode": pref.observer_code if pref else "", "navn": pref.display_name if pref else ""}


@router.post("/api/remove-connection")
async def remove_connection(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    pref = await db.get(UserPreference, _user_id(payload))
    if pref:
        pref.observer_code = None
        await db.commit()
    return {"ok": True}


@router.get("/api/nyheder")
async def list_news(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    result = await db.execute(select(CommunityNews).order_by(CommunityNews.published_at.desc()))
    return [
        {
            "id": item.id,
            "titel": item.title,
            "forfatter": item.created_by,
            "oprettet_tidspunkt": item.published_at.date().isoformat() if item.published_at else "",
            "body": item.body_markdown,
        }
        for item in result.scalars().all()
    ]


@router.api_route("/api/admin/nyhed", methods=["GET", "POST", "PUT", "DELETE"])
async def manage_news(
    request: Request,
    data: dict[str, Any] | None = Body(default=None),
    id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Any:
    payload = data or {}
    news_id = id or str(payload.get("id") or "")
    if request.method == "GET":
        item = await db.get(CommunityNews, news_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Nyhed ikke fundet")
        return {"id": item.id, "titel": item.title, "forfatter": item.created_by, "body": item.body_markdown}
    if request.method == "DELETE":
        item = await db.get(CommunityNews, news_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Nyhed ikke fundet")
        await db.delete(item)
        await db.commit()
        return {"ok": True, "id": news_id}
    item = await db.get(CommunityNews, news_id) if news_id else None
    if item is None:
        item = CommunityNews(
            id=str(uuid4()),
            title=str(payload.get("titel") or payload.get("title") or "Nyhed"),
            body_markdown=str(payload.get("body") or ""),
            created_by=_user_id(payload) or "legacy",
        )
        db.add(item)
    else:
        item.title = str(payload.get("titel") or payload.get("title") or item.title)
        item.body_markdown = str(payload.get("body") or item.body_markdown)
    await db.commit()
    return {"ok": True, "id": item.id}


@router.post("/api/users-overview")
async def users_overview(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    result = await db.execute(select(UserPreference).order_by(UserPreference.user_id))
    return [
        {
            "user_id": item.user_id,
            "obserkode": item.observer_code or "",
            "lokalafdelinger": json.loads(item.afdelinger or "{}") if item.afdelinger else {},
            "advanced": bool(item.exclude_species),
        }
        for item in result.scalars().all()
    ]


@router.post("/api/is-app-user-bulk")
async def is_app_user_bulk(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ids = payload.get("user_ids") or payload.get("userids") or []
    result = await db.execute(select(UserPreference.user_id).where(UserPreference.user_id.in_([str(value) for value in ids])))
    active = {row[0] for row in result.all()}
    return {"users": {str(value): str(value) in active for value in ids}}


@router.post("/api/is-admin")
async def is_admin(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    value = await db.scalar(select(AdminUser.user_id).where(AdminUser.user_id == _user_id(payload)))
    return {"is_admin": value is not None}


@router.post("/api/is-superadmin")
async def is_superadmin(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    item = await db.scalar(select(AdminUser).where(AdminUser.user_id == _user_id(payload), AdminUser.role == "superadmin"))
    return {"is_superadmin": item is not None}


@router.post("/api/admin/all-users")
async def all_users(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    result = await db.execute(select(UserPreference).order_by(UserPreference.user_id))
    return [{"user_id": item.user_id, "obserkode": item.observer_code, "navn": item.display_name} for item in result.scalars().all()]


@router.post("/api/admin/delete-user")
async def delete_user(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    user_id = _user_id(payload)
    pref = await db.get(UserPreference, user_id)
    if pref:
        await db.delete(pref)
    await db.execute(delete(AdminUser).where(AdminUser.user_id == user_id))
    await db.commit()
    return {"ok": True}


@router.post("/api/admin/list-admins")
async def list_admins(db: AsyncSession = Depends(get_db)) -> dict[str, list[dict[str, Any]]]:
    result = await db.execute(select(AdminUser).order_by(AdminUser.user_id))
    return {"admins": [{"user_id": item.user_id, "role": item.role} for item in result.scalars().all()]}


@router.post("/api/admin/add-admin")
async def add_admin(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    user_id = _user_id(payload)
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id kræves")
    if await db.get(AdminUser, user_id) is None:
        db.add(AdminUser(user_id=user_id, role=str(payload.get("role") or "admin")))
        await db.commit()
    return {"ok": True}


@router.post("/api/admin/remove-admin")
async def remove_admin(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    item = await db.get(AdminUser, _user_id(payload))
    if item:
        await db.delete(item)
        await db.commit()
    return {"ok": True}


@router.post("/api/admin/superadmin")
async def manage_superadmin(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    action = str(payload.get("action") or "get")
    if action == "get":
        result = await db.execute(select(AdminUser.user_id).where(AdminUser.role == "superadmin").order_by(AdminUser.user_id))
        return {"superadmins": [row[0] for row in result.all()]}
    if action != "toggle":
        raise HTTPException(status_code=400, detail="Ugyldig action")
    user_id = str(payload.get("obserkode") or payload.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="Bruger mangler")
    item = await db.get(AdminUser, user_id)
    if item is not None and item.role == "superadmin":
        item.role = "admin"
    elif item is None:
        db.add(AdminUser(user_id=user_id, role="superadmin"))
    else:
        item.role = "superadmin"
    await db.commit()
    result = await db.execute(select(AdminUser.user_id).where(AdminUser.role == "superadmin").order_by(AdminUser.user_id))
    return {"ok": True, "superadmins": [row[0] for row in result.all()]}


@router.post("/api/admin/blacklist")
async def legacy_blacklist(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> Any:
    target = str(payload.get("obsid") or payload.get("obserkode") or "").strip()
    if not target:
        result = await db.execute(select(BlacklistedUser).order_by(BlacklistedUser.created_at.desc()))
        return [{"obserkode": item.user_id, "reason": item.reason, "created_at": item.created_at} for item in result.scalars().all()]
    item = await db.get(BlacklistedUser, target)
    if item is None:
        db.add(BlacklistedUser(user_id=target, reason=str(payload.get("reason") or "")))
    else:
        item.reason = str(payload.get("reason") or item.reason or "")
    await db.commit()
    return {"ok": True}


@router.post("/api/admin/unblacklist")
async def legacy_unblacklist(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    target = str(payload.get("obsid") or payload.get("obserkode") or "").strip()
    if target:
        await db.execute(delete(BlacklistedUser).where(BlacklistedUser.user_id == target))
        await db.commit()
    return {"ok": True}


@router.post("/api/admin/remove-comment")
async def remove_comment(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    query = select(ObservationComment)
    if payload.get("day"):
        query = query.where(ObservationComment.day == _parse_day(str(payload["day"])))
    if payload.get("thread_id"):
        query = query.where(ObservationComment.thread_id == str(payload["thread_id"]))
    result = await db.execute(query)
    for item in result.scalars().all():
        if payload.get("body") and item.body != payload["body"]:
            continue
        await db.delete(item)
        break
    await db.commit()
    return {"ok": True}


@router.post("/api/admin/comments")
async def admin_comments(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, list[dict[str, Any]]]:
    query = select(ObservationComment).order_by(ObservationComment.created_at.desc()).limit(500)
    if payload.get("day"):
        query = query.where(ObservationComment.day == _parse_day(str(payload["day"])))
    result = await db.execute(query)
    return {"comments": [{"id": item.id, "day": item.day, "thread_id": item.thread_id, "navn": item.display_name, "body": item.body, "created_at": item.created_at} for item in result.scalars().all()]}


@router.post("/api/admin/csv")
async def admin_csv(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    raw_rows = payload.get("rows") or payload.get("data") or []
    rows = [item for item in (legacy_row_to_ingest(row) for row in raw_rows if isinstance(row, dict)) if item]
    if not rows:
        return {"ok": True, "processed": 0}
    return {"ok": True, "processed": await upsert_rows(await enrich_observations(rows), db)}


@router.api_route("/api/admin/fetch-arter-csv", methods=["GET", "POST"])
@router.api_route("/api/admin/fetch-faenologi-csv", methods=["GET", "POST"])
@router.api_route("/api/admin/fetch-all-bemaerk-csv", methods=["GET", "POST"])
async def fetch_legacy_csv() -> dict[str, Any]:
    try:
        enrichment = await load_dof_enrichment()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"Kunne ikke hente DOF-data: {error}")
    return {
        "ok": True,
        "processed": len(enrichment.su) + len(enrichment.sub) + len(enrichment.remarkable),
        "su": len(enrichment.su),
        "sub": len(enrichment.sub),
        "bemaerk": len(enrichment.remarkable),
    }


@router.get("/api/dofbasen")
async def dofbasen_observation(obsid: str = Query(..., min_length=1)) -> dict[str, Any]:
    url = f"https://dofbasen.dk/popobs.php?obsid={obsid}&summering=tur&obs=obs"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "DOF.tool/1.0"})
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"Kunne ikke hente DOFbasen: {error}")
    html = response.content.decode("latin-1", errors="replace")
    text = lambda value: re.sub(r"<[^>]+>", "", value).strip()
    art_match = re.search(r'<font class="(?:subart|defaultart|su)">([^<]+)</font>\s*(?:\(SU\))?\s*\(<i>([^<]+)</i>\)', html, re.I)
    quantity = re.search(r'<td[^>]*valign="top"[^>]*>(\d+)</td>', html, re.I)
    images = re.findall(r'(?:src|data-src)=["\']([^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)', html, re.I)
    sounds = re.findall(r'href=["\']([^"\']*sound_proxy\.php[^"\']*)["\']', html, re.I)
    return {
        "ok": True,
        "obsid": obsid,
        "art": text(art_match.group(1)) if art_match else "",
        "latin": text(art_match.group(2)) if art_match else "",
        "antal": quantity.group(1) if quantity else "",
        "images": images,
        "sound_urls": [f"https://dofbasen.dk{item}" if item.startswith("/") else item for item in sounds],
        "url": url,
    }


@router.post("/api/admin/serverlog")
async def server_log() -> dict[str, str]:
    if not SERVER_LOG.is_file():
        return {"log": ""}
    return {"log": SERVER_LOG.read_text(encoding="utf-8", errors="replace")[-200_000:]}


@router.post("/api/admin/download/{filename}")
async def download_admin_file(filename: str) -> FileResponse:
    candidate = (FRONTEND_DIR / filename).resolve()
    if FRONTEND_DIR not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Fil ikke fundet")
    return FileResponse(candidate)


@router.post("/api/admin/pageview-stats")
@router.post("/api/admin/pageviews-rolling")
@router.post("/api/admin/traffic-graphs")
@router.get("/api/admin/traffic-diffs")
async def pageview_stats(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=365)
    result = await db.execute(
        select(
            func.date(PageViewEvent.created_at).label("day"),
            func.count(PageViewEvent.id).label("views"),
            func.count(func.distinct(PageViewEvent.user_id)).label("users"),
        )
        .where(PageViewEvent.created_at >= datetime.combine(start, datetime.min.time(), timezone.utc))
        .group_by(func.date(PageViewEvent.created_at))
        .order_by(func.date(PageViewEvent.created_at))
    )
    rows = result.all()
    days = [{"date": str(row.day), "total_views": row.views, "unique_users_total": row.users} for row in rows]
    metrics = {
        "total_views": "Sidevisninger",
        "unique_users_total": "Unikke besøgende",
    }
    diffs: dict[str, Any] = {}
    for key, label in metrics.items():
        current = days[-1].get(key, 0) if days else 0
        previous = days[-2].get(key, 0) if len(days) > 1 else 0
        diffs[key] = {
            "label": label,
            "today": current,
            "diff_yesterday": current - previous,
            "pct_yesterday": ((current - previous) / previous * 100) if previous else None,
        }
    total = sum(item["total_views"] for item in days)
    return {"total": total, "days": days, "series": days, "diffs": diffs}


@router.post("/api/admin/archive-pageview-log")
async def archive_pageviews(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    total = await db.scalar(select(func.count(PageViewEvent.id))) or 0
    return {"ok": True, "archived_events": total}


@router.get("/api/payload")
async def payload(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    result = await db.execute(select(Observation).order_by(Observation.observed_at.desc()).limit(500))
    return [_serialize_observation(item) for item in result.scalars().all()]


@router.get("/obs/{day}/threads/{thread_id}")
async def short_thread(day: str, thread_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    observations = await db.execute(select(Observation).where(Observation.observed_on == _parse_day(day)))
    items = [item for item in observations.scalars().all() if item.species.lower().replace(" ", "-") in thread_id]
    return {"thread": {"id": thread_id, "day": day, "observations": [_serialize_observation(item) for item in items]}}


def _parse_day(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise HTTPException(status_code=400, detail="Ugyldig dato")


@router.get("/share/{day}/{thread_id}", response_class=HTMLResponse)
async def share_thread(day: str, thread_id: str) -> HTMLResponse:
    _parse_day(day)
    if not thread_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Ugyldigt thread_id")
    url = f"/notifications.html?day={day}&thread={thread_id}"
    return HTMLResponse(f'<meta property="og:title" content="DOFbasen observation"><meta http-equiv="refresh" content="0;url={url}"><a href="{url}">Åbn observation</a>')


@router.get("/share/obsid/{obsid}/", response_class=HTMLResponse)
async def share_obsid(obsid: str) -> HTMLResponse:
    url = f"/module.html?obsid={obsid}"
    return HTMLResponse(f'<meta property="og:title" content="DOFbasen observation"><meta http-equiv="refresh" content="0;url={url}"><a href="{url}">Åbn observation</a>')


@router.post("/api/debug-push")
async def debug_push(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    user_id = str(payload.get("user_id") or payload.get("userid") or "").strip()
    result = await db.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))
    subscriptions = result.scalars().all()
    if not subscriptions:
        return {"ok": False, "error": "Ingen push-subscription fundet"}
    return {"ok": False, "error": "Push-afsendelse er ikke konfigureret på denne installation", "subscriptions": len(subscriptions)}


@router.websocket("/ws/thread/{day}/{thread_id}")
async def thread_websocket(websocket: WebSocket, day: str, thread_id: str) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "connected", "day": day, "thread_id": thread_id})
    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "get_comments":
                async with SessionLocal() as db:
                    result = await db.execute(
                        select(ObservationComment)
                        .where(ObservationComment.day == _parse_day(day), ObservationComment.thread_id == thread_id)
                        .order_by(ObservationComment.created_at.asc())
                    )
                    comments = result.scalars().all()
                await websocket.send_json({"type": "comments", "comments": [{"id": item.id, "navn": item.display_name or "", "body": item.body, "ts": item.created_at.isoformat(), "thumbs": 0} for item in comments]})
            elif message_type == "new_comment":
                body = str(message.get("body") or "").strip()
                if not body:
                    await websocket.send_json({"type": "error", "message": "Kommentaren er tom"})
                    continue
                async with SessionLocal() as db:
                    db.add(ObservationComment(day=_parse_day(day), thread_id=thread_id, user_id=str(message.get("user_id") or "legacy"), display_name=str(message.get("navn") or ""), body=body))
                    await db.commit()
                await websocket.send_json({"type": "new_comment"})
    except WebSocketDisconnect:
        return
