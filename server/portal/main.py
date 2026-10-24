from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from portal.api import (admin, alerts, auth, calendar, categories, dofbasen, dofbasen_v1, feed, geo, learn, lists, media, mobgang, netfugl,
                        position, netfugl_v1, privacy, reports, science, share, social, trips)
from portal.api.deps import COOKIE
from portal.bootstrap import init_db
from portal.cache import kv
from portal.config import get_settings
from portal.db import router
from portal.dbgroups import GROUPS
from portal.services import storage
from portal.services.auth import AuthError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
WEB_DIR = Path(os.environ.get("WEB_DIR", Path(__file__).resolve().parents[2] / "web"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await storage.init()
    yield
    await router.stop()


app = FastAPI(title=get_settings().app_name, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


MOB_AUDIO = re.compile(r"^(/v/[^/]+)?/audio/mobgang/")
PUBLIC_API = ("/api/auth/", "/api/health", "/api/me", "/api/settings/public", "/api/ics/", "/api/docs", "/api/openapi.json",
              "/api/dofbasen/v1/", "/api/netfugl/v1/",  # showcase-API'erne har deres egen token-godkendelse
              "/api/location/native")  # appens positionstjeneste sender selv med et positionstoken
TOKEN_APIS = ("/api/dofbasen/v1/", "/api/netfugl/v1/")
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type", "Access-Control-Max-Age": "86400"}


@app.middleware("http")
async def guard(request: Request, call_next):
    # Showcase-API'erne bruges fra andres apps (også i browseren): åben CORS, kun læsning, token i stedet for cookie
    if request.url.path.startswith(TOKEN_APIS):
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=CORS)
        if request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "API'et er kun til læsning"}, status_code=405, headers=CORS)
        response = await call_next(request)
        response.headers.update(CORS)
        response.headers.setdefault("Cache-Control", "no-store")
        return response
    # CSRF-værn: ændrende API-kald skal komme fra vores egen fetch-klient (custom header kræver CORS-preflight)
    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Android-WebView kan sætte sit eget X-Requested-With (pakkenavnet); Sec-Fetch-Site sættes af browseren selv
        xrw = {v.strip() for v in (request.headers.get("x-requested-with") or "").split(",")}
        if "fetch" not in xrw and request.headers.get("sec-fetch-site") != "same-origin":
            return JSONResponse({"detail": "Mangler X-Requested-With"}, status_code=403)
    # Portalen er kun for indloggede brugere: alt under /api kræver en gyldig session, undtagen login,
    # sundhedstjek, /api/me (svarer user=null) og ICS-feeds (personligt token i adressen).
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(PUBLIC_API):
        token = request.cookies.get(COOKIE)
        if not token or not await kv().exists(f"sess:{token}"):
            return JSONResponse({"detail": "Log ind for at bruge portalen"}, status_code=401)
    # Uploads fra feltmeldinger og læring er kun for indloggede; Netfugl-fotos er offentlige (vises også via showcase-API'et)
    private_media = path.startswith("/media/") and not path.startswith("/media/netfugl/")
    if private_media and not await kv().exists(f"sess:{request.cookies.get(COOKIE) or '-'}"):
        return JSONResponse({"detail": "Log ind for at se filen"}, status_code=401)
    mob_audio = bool(MOB_AUDIO.match(path))
    if mob_audio:
        # Mobgang-lyde: kun indloggede med bestået etiktest og uden for ynglesæsonen
        user_id = await kv().get(f"sess:{request.cookies.get(COOKIE) or '-'}")
        if not user_id or not await mobgang.audio_allowed(int(user_id), path):
            return JSONResponse({"detail": "Arten er lukket i ynglesæsonen, eller Mobgang kræver bestået etiktest"}, status_code=403)
    response = await call_next(request)
    if not path.startswith(("/api/", "/media/", "/vendor/", "/icons/", "/v/")):
        # App-filer (html/js/css/sw): browseren skal altid tjekke for ny version (billigt via etag)
        response.headers["Cache-Control"] = "no-cache"
    if mob_audio:
        # Aldrig i delt cache (CDN), og browseren skal spørge igen, så lukningen også gælder cachede filer
        response.headers["Cache-Control"] = "private, no-cache"
    if private_media:
        response.headers["Cache-Control"] = "private, max-age=86400"  # aldrig i en delt cache (CDN)
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), payment=(), usb=(), geolocation=(self)")
    if path.startswith("/media/"):
        # Uploadede filer åbnet direkte må aldrig kunne køre noget
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; img-src 'self'; media-src 'self'; sandbox")
    elif not path.startswith("/api/"):
        response.headers.setdefault("Content-Security-Policy", csp())
    return response


def csp() -> str:
    """Kun egne scripts (plus det inline splash-script i index.html via hash); kort-fliser og DOFbasen-medier via https."""
    index = WEB_DIR / "index.html"
    stamp = index.stat().st_mtime_ns if index.is_file() else 0
    if _csp["at"] != stamp:
        html = index.read_text(encoding="utf-8") if stamp else ""
        hashes = " ".join(f"'sha256-{base64.b64encode(hashlib.sha256(m.encode()).digest()).decode()}'"
                          for m in re.findall(r"<script>(.*?)</script>", html, re.S))
        _csp.update(at=stamp, value="; ".join([
            "default-src 'self'", f"script-src 'self' {hashes}".strip(), "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob: https:", "media-src 'self' blob: https:",
            "connect-src 'self' https://tile.openstreetmap.org", "font-src 'self' data:", "object-src 'none'",
            "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'", "worker-src 'self'", "manifest-src 'self'"]))
    return _csp["value"]


_csp = {"at": None, "value": ""}


@app.exception_handler(AuthError)
async def auth_error(_: Request, exc: AuthError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.get("/api/health")
async def health():
    for group in GROUPS:  # hver gruppes primary skal svare
        async with router.primary(group).connect() as conn:
            await conn.execute(text("SELECT 1"))
    await kv().ping()
    return {"ok": True}


for module in (auth, feed, geo, alerts, lists, science, learn, mobgang, reports, trips, social, calendar, categories, admin, share,
               dofbasen, dofbasen_v1, netfugl, netfugl_v1, position, privacy, media):  # media før /media-mounten
    app.include_router(module.router)

# ---------------------------------------------------------------- versionerede app-filer
# index.html peger på /v/<build>/js/app.js osv. Build-id er en hash af app-filerne, så en ny deploy giver
# nye adresser – CDN'er (fx Cloudflare) og browsere kan cache filerne for evigt uden at levere gammel kode.

_build = {"at": 0.0, "id": ""}


def build_id() -> str:
    now = time.monotonic()
    if not _build["id"] or now - _build["at"] > 5:
        h = hashlib.sha1()
        for f in sorted(p for d in ("js", "css") for p in (WEB_DIR / d).rglob("*") if p.is_file()):
            st = f.stat()
            h.update(f"{f.relative_to(WEB_DIR)}:{st.st_size}:{st.st_mtime_ns}".encode())
        _build.update(at=now, id=h.hexdigest()[:10])
    return _build["id"]


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/index.html", methods=["GET", "HEAD"], include_in_schema=False)
async def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("__BUILD__", build_id())
    return HTMLResponse(html, headers={"Cache-Control": "no-cache", "Content-Security-Policy": csp()})


@app.api_route("/v/{build}/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def versioned(build: str, path: str):
    root = WEB_DIR.resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"})


media = Path(get_settings().media_dir)
media.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media), name="media")
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
