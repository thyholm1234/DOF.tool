from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from portal.api import admin, alerts, auth, feed, geo, learn, lists, reports, science
from portal.bootstrap import init_db
from portal.cache import kv
from portal.config import get_settings
from portal.db import engine
from portal.services.auth import AuthError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
WEB_DIR = Path(os.environ.get("WEB_DIR", Path(__file__).resolve().parents[2] / "web"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield
    await engine.dispose()


app = FastAPI(title=get_settings().app_name, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def guard(request: Request, call_next):
    # CSRF-værn: ændrende API-kald skal komme fra vores egen fetch-klient (custom header kræver CORS-preflight)
    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get("x-requested-with") != "fetch":
            return JSONResponse({"detail": "Mangler X-Requested-With"}, status_code=403)
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", "sw.js", ".webmanifest")):
        response.headers["Cache-Control"] = "no-cache"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.exception_handler(AuthError)
async def auth_error(_: Request, exc: AuthError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.get("/api/health")
async def health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    await kv().ping()
    return {"ok": True}


for module in (auth, feed, geo, alerts, lists, science, learn, reports, admin):
    app.include_router(module.router)

media = Path(get_settings().media_dir)
media.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media), name="media")
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
