from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from math import atan2, cos, radians, sin, sqrt

import httpx

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.observations import _thread_id
from backend.app.db.models import (
    Observation,
    ObservationComment,
    ObservationThreadSubscription,
    ObservationSubscription,
    PushSubscription,
    SpeciesFilter,
    UserPreference,
)
from backend.app.db.session import get_db
from backend.app.services.dof_auth import authenticate_dof_user, fetch_dof_observer_name

router = APIRouter(tags=["legacy-compatibility"])


def _distance_km(latitude: float, longitude: float, target_latitude: float, target_longitude: float) -> float:
    radius = 6371.0
    d_lat = radians(target_latitude - latitude)
    d_lon = radians(target_longitude - longitude)
    value = sin(d_lat / 2) ** 2 + cos(radians(latitude)) * cos(radians(target_latitude)) * sin(d_lon / 2) ** 2
    return radius * 2 * atan2(sqrt(value), sqrt(1 - value))


@router.post("/api/validate-login")
async def legacy_validate_login(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or "").strip()
    username = str(payload.get("obserkode") or "").strip()
    password = str(payload.get("adgangskode") or "")
    if not user_id or not username or not password:
        raise HTTPException(status_code=400, detail="user_id, obserkode og adgangskode kræves")
    result = await authenticate_dof_user(username, password)
    pref = await db.get(UserPreference, user_id)
    if pref is None:
        pref = UserPreference(user_id=user_id)
        db.add(pref)
    pref.observer_code = username.upper()
    pref.display_name = str(result.user.get("name") or result.user.get("display_name") or "").strip()
    if not pref.display_name:
        pref.display_name = await fetch_dof_observer_name(username)
    if not pref.display_name:
        pref.display_name = username.upper()
    await db.commit()
    return {"ok": True, "token": result.access_token, "navn": pref.display_name}


def _parse_day(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="Ugyldig dato.")


@router.get("/api/threads/{day}")
async def legacy_threads(day: str, db: AsyncSession = Depends(get_db)) -> list[dict]:
    target = _parse_day(day)
    result = await db.execute(select(Observation).where(Observation.observed_on == target).order_by(Observation.observed_at.desc()))
    grouped: dict[str, list[Observation]] = {}
    for item in result.scalars().all():
        grouped.setdefault(_thread_id(item.species, item.location_id, item.location_name), []).append(item)
    rows = []
    for thread_id, items in grouped.items():
        latest = max(items, key=lambda item: item.observed_at)
        comments = await db.scalar(select(func.count(ObservationComment.id)).where(ObservationComment.day == target, ObservationComment.thread_id == thread_id))
        rows.append({"thread_id": thread_id, "day": target.isoformat(), "art": latest.species, "species": latest.species, "location": latest.location_name, "last_kategori": latest.category, "category": latest.category, "antal_observationer": len(items), "antal_individer": sum(item.count or 0 for item in items), "latest_observed_at": latest.observed_at, "comment_count": comments or 0})
    return rows


@router.get("/api/thread/{day}/{thread_id}")
async def legacy_thread(day: str, thread_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    target = _parse_day(day)
    result = await db.execute(select(Observation).where(Observation.observed_on == target).order_by(Observation.observed_at.asc()))
    items = [item for item in result.scalars().all() if _thread_id(item.species, item.location_id, item.location_name) == thread_id]
    if not items:
        raise HTTPException(status_code=404, detail="Tråd ikke fundet.")
    comments_result = await db.execute(select(ObservationComment).where(ObservationComment.day == target, ObservationComment.thread_id == thread_id).order_by(ObservationComment.created_at.asc()))
    return {"thread_id": thread_id, "day": target.isoformat(), "observations": [{"obsid": item.obsid, "species": item.species, "location": item.location_name, "category": item.category, "count": item.count, "observed_at": item.observed_at} for item in items], "comments": [{"id": item.id, "user_id": item.user_id, "navn": item.display_name, "body": item.body, "created_at": item.created_at} for item in comments_result.scalars().all()]}


@router.post("/api/thread/{day}/{thread_id}/subscribe")
async def subscribe_thread(day: str, thread_id: str, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    target = _parse_day(day)
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id mangler.")
    existing = await db.scalar(select(ObservationThreadSubscription).where(ObservationThreadSubscription.day == target, ObservationThreadSubscription.thread_id == thread_id, ObservationThreadSubscription.user_id == user_id))
    if existing is None:
        db.add(ObservationThreadSubscription(day=target, thread_id=thread_id, user_id=user_id))
        await db.commit()
    return {"status": "ok", "subscribed": True}


@router.post("/api/thread/{day}/{thread_id}/unsubscribe")
async def unsubscribe_thread(day: str, thread_id: str, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    target = _parse_day(day)
    await db.execute(delete(ObservationThreadSubscription).where(ObservationThreadSubscription.day == target, ObservationThreadSubscription.thread_id == thread_id, ObservationThreadSubscription.user_id == str(payload.get("user_id") or "")))
    await db.commit()
    return {"status": "ok", "subscribed": False}


@router.post("/api/thread/{day}/{thread_id}/subscription")
async def thread_subscription(day: str, thread_id: str, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    target = _parse_day(day)
    existing = await db.scalar(select(ObservationThreadSubscription.id).where(ObservationThreadSubscription.day == target, ObservationThreadSubscription.thread_id == thread_id, ObservationThreadSubscription.user_id == str(payload.get("user_id") or "")))
    return {"subscribed": existing is not None}


@router.post("/api/prefs/quiet-hours")
async def legacy_quiet_hours(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or "").strip()
    start = str(payload.get("start") or "").strip()
    end = str(payload.get("end") or "").strip()
    pref = await db.get(UserPreference, user_id)
    if pref is None:
        pref = UserPreference(user_id=user_id)
        db.add(pref)
    pref.quiet_hours = payload.get("quiet_hours") or payload.get("value") or (f"{start}-{end}" if start and end else None)
    await db.commit()
    return {"status": "ok", "quiet_hours": pref.quiet_hours}


@router.post("/api/prefs/user/species")
async def species_filters(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or "").strip()
    filters = payload.get("filters")
    if isinstance(filters, dict):
        await db.execute(delete(SpeciesFilter).where(SpeciesFilter.user_id == user_id))
        for species in filters.get("exclude", []):
            db.add(SpeciesFilter(user_id=user_id, species=str(species).strip().lower(), excluded=True))
        for species, minimum_count in (filters.get("counts") or {}).items():
            try:
                count = int(minimum_count)
            except (TypeError, ValueError):
                continue
            if count >= 1:
                db.add(SpeciesFilter(user_id=user_id, species=str(species).strip().lower(), minimum_count=count))
        await db.commit()
    result = await db.execute(select(SpeciesFilter).where(SpeciesFilter.user_id == user_id))
    items = result.scalars().all()
    return {"include": [item.species for item in items if not item.excluded], "exclude": [item.species for item in items if item.excluded], "counts": {item.species: item.minimum_count for item in items if item.minimum_count is not None}}


@router.post("/api/copy-species-filter-to-obserkode-users")
async def copy_species_filters(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or "").strip()
    source = await db.execute(select(SpeciesFilter).where(SpeciesFilter.user_id == user_id))
    items = source.scalars().all()
    pref = await db.get(UserPreference, user_id)
    updated_users = [user_id] if pref and pref.observer_code else []
    return {"ok": True, "updated_users": updated_users, "copied_filters": len(items)}


@router.get("/api/latest")
async def legacy_latest(db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(select(Observation).order_by(Observation.observed_at.desc()).limit(100))
    return [{"obsid": item.obsid, "species": item.species, "location": item.location_name, "category": item.category, "count": item.count, "observed_at": item.observed_at} for item in result.scalars().all()]


@router.get("/api/obs/full")
async def legacy_observation_media(obsid: str) -> dict:
    url = f"https://dofbasen.dk/popobs.php?obsid={obsid}&summering=tur&obs=obs"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"Kunne ikke hente observationen: {error}")
    html = response.text
    images = re.findall(r"(?:src|data-src)=['\"]([^'\"]+\.(?:jpg|jpeg|png|webp)[^'\"]*)", html, re.IGNORECASE)
    sounds = re.findall(r"href=['\"]([^'\"]*sound_proxy\.php[^'\"]*)['\"]", html, re.IGNORECASE)
    return {"obsid": obsid, "status": "", "images": images, "sound_urls": sounds}


@router.get("/api/lok_koordinater")
async def legacy_location_coordinates(loknr: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(f"https://dofbasen.dk/poplok.php?loknr={loknr}")
            response.raise_for_status()
    except httpx.HTTPError as error:
        return {"ok": False, "error": str(error)}
    longitude = re.search(r'<span id="lok_center_lon">([0-9.\-]+)</span>', response.text)
    latitude = re.search(r'<span id="lok_center_lat">([0-9.\-]+)</span>', response.text)
    if not longitude or not latitude:
        return {"ok": False, "error": "Koordinater ikke fundet"}
    return {"ok": True, "laengde": float(longitude.group(1)), "bredde": float(latitude.group(1))}


@router.post("/api/obsid/{obsid}/subscribe")
async def observation_subscription(obsid: str, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id mangler.")
    existing = await db.scalar(select(ObservationSubscription).where(ObservationSubscription.user_id == user_id, ObservationSubscription.obsid == obsid))
    requested = payload.get("subscribe")
    if requested is None:
        return {"subscribed": existing is None or existing.subscribed}
    if existing is None:
        db.add(ObservationSubscription(user_id=user_id, obsid=obsid, subscribed=bool(requested)))
    else:
        existing.subscribed = bool(requested)
    await db.commit()
    return {"ok": True, "subscribed": bool(requested)}


@router.post("/api/subscribe")
async def legacy_subscribe(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or payload.get("userid") or "").strip()
    device_id = str(payload.get("device_id") or payload.get("deviceid") or "").strip()
    subscription = payload.get("subscription") or {}
    endpoint = str(subscription.get("endpoint") or device_id).strip()
    keys = subscription.get("keys") or {}
    if not user_id or not device_id or not endpoint:
        raise HTTPException(status_code=400, detail="user_id, device_id og subscription kræves")
    existing = await db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if existing is None:
        db.add(PushSubscription(user_id=user_id, endpoint=endpoint, p256dh=str(keys.get("p256dh") or ""), auth=str(keys.get("auth") or "")))
    else:
        existing.user_id = user_id
        existing.p256dh = str(keys.get("p256dh") or "")
        existing.auth = str(keys.get("auth") or "")
    await db.commit()
    return {"ok": True}


@router.post("/api/unsubscribe")
async def legacy_unsubscribe(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or "").strip()
    device_id = str(payload.get("device_id") or "").strip()
    result = await db.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))
    for subscription in result.scalars().all():
        if subscription.endpoint == device_id or not device_id:
            await db.delete(subscription)
    await db.commit()
    return {"ok": True}


@router.post("/api/is-subscribed")
async def legacy_is_subscribed(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or "").strip()
    count = await db.scalar(select(func.count(PushSubscription.id)).where(PushSubscription.user_id == user_id))
    return {"subscribed": bool(count)}


@router.post("/api/prefs")
async def legacy_preferences(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or "").strip()
    pref = await db.get(UserPreference, user_id)
    if pref is None:
        pref = UserPreference(user_id=user_id)
        db.add(pref)
    new_prefs = payload.get("prefs")
    if isinstance(new_prefs, dict):
        pref.afdelinger = json.dumps(new_prefs, ensure_ascii=False)
        await db.commit()
    return json.loads(pref.afdelinger or "{}") if pref.afdelinger else {}


@router.post("/api/userinfo")
async def legacy_userinfo(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or "").strip()
    pref = await db.get(UserPreference, user_id)
    return {"obserkode": pref.observer_code if pref else "", "navn": pref.display_name if pref else ""}


@router.post("/api/remove-connection")
async def legacy_remove_connection(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    user_id = str(payload.get("user_id") or "").strip()
    pref = await db.get(UserPreference, user_id)
    if pref:
        pref.observer_code = None
        pref.display_name = None
        await db.commit()
    return {"ok": True}


@router.post("/api/nearby-observations")
async def nearby_observations(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        latitude = float(payload["lat"])
        longitude = float(payload.get("lng", payload.get("lon")))
        radius_km = min(float(payload.get("radius_km", 5)), 100)
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="lat, lng og radius_km skal være gyldige tal.")
    result = await db.execute(
        select(Observation).where(
            Observation.latitude.is_not(None), Observation.longitude.is_not(None)
        ).order_by(Observation.observed_at.desc()).limit(5000)
    )
    observations = []
    for item in result.scalars().all():
        distance = _distance_km(latitude, longitude, item.latitude, item.longitude)
        if distance <= radius_km:
            observations.append({"obsid": item.obsid, "species": item.species, "location": item.location_name, "category": item.category, "count": item.count, "observed_at": item.observed_at, "distance_km": round(distance, 2)})
    return {"observations": observations}