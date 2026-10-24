"""DOF.tool db-admin: register over databasenoderne (kun for superadmin).

Superadmin opretter en node (bruger- eller observationsdatabase, primary eller replika), får et token som bruges som
databasens adgangskode i dens docker compose, og kan rette IP/port bagefter. API'et og workeren henter topologien her
(/api/topology) og er load balancer for hele DOF-økosystemet. Registret ligger i en lille SQLite i /data, så panelet
ikke afhænger af de databaser det styrer.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, closing
from pathlib import Path

import asyncpg
import httpx
import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

PORTAL_URL = os.environ.get("PORTAL_URL", "http://api:8000").rstrip("/")
SERVICE_TOKEN = os.environ.get("DBADMIN_SERVICE_TOKEN", "")
REDIS_DSN = os.environ.get("REDIS_DSN", "redis://valkey:6379/0")
COOKIE = os.environ.get("SESSION_COOKIE", "dt_session")
DB_PATH = Path(os.environ.get("DBADMIN_DATA", "/data")) / "registry.db"
CHECK_SECONDS = 10
GROUPS = ("users", "observations")
ROLES = ("primary", "replica")
PURPOSES = ("read", "backup")  # replika: aflaster læsninger, eller kun backup (bruges aldrig til læsning)
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,38}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    grp TEXT NOT NULL,
    role TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    dbname TEXT NOT NULL DEFAULT 'portal',
    user TEXT NOT NULL DEFAULT 'portal',
    token TEXT NOT NULL,
    primary_id INTEGER,
    purpose TEXT NOT NULL DEFAULT 'read',
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unknown',
    detail TEXT NOT NULL DEFAULT '',
    lag_seconds REAL,
    checked_at REAL,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS superadmins (user_id INTEGER PRIMARY KEY, seen_at REAL NOT NULL);
"""
DEFAULT_SETTINGS = {"read_replicas": {"users": False, "observations": True}, "version": 1}


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as con:
        con.executescript(SCHEMA)
        if "purpose" not in {r["name"] for r in con.execute("PRAGMA table_info(nodes)")}:  # register fra før purpose fandtes
            con.execute("ALTER TABLE nodes ADD COLUMN purpose TEXT NOT NULL DEFAULT 'read'")
        for k, v in DEFAULT_SETTINGS.items():
            con.execute("INSERT OR IGNORE INTO settings (k, v) VALUES (?, ?)", (k, json.dumps(v)))
        con.commit()


def setting(con: sqlite3.Connection, k: str):
    row = con.execute("SELECT v FROM settings WHERE k = ?", (k,)).fetchone()
    return json.loads(row["v"]) if row else DEFAULT_SETTINGS[k]


def bump(con: sqlite3.Connection) -> None:
    con.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('version', ?)", (json.dumps(setting(con, "version") + 1),))


# ---------------------------------------------------------------- login: portalens session, kun superadmin

_verdicts: dict[str, tuple[float, bool]] = {}
_redis: aioredis.Redis | None = None


def kv() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_DSN, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
    return _redis


async def is_superadmin(token: str | None) -> bool:
    """Spørg portalen (/api/me). Er portalen nede (fx fordi en database er det), godtages en session for en bruger,
    der tidligere er set som superadmin – ellers kunne panelet ikke bruges til at rette adressen på den database."""
    if not token:
        return False
    hit = _verdicts.get(token)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    verdict: bool | None = None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(PORTAL_URL + "/api/me", cookies={COOKIE: token})
        if r.status_code == 200:
            user = r.json().get("user")
            verdict = bool(user and user.get("role") == "superadmin")
            if verdict:
                with closing(db()) as con:
                    con.execute("INSERT OR REPLACE INTO superadmins (user_id, seen_at) VALUES (?, ?)", (user["id"], time.time()))
                    con.commit()
    except httpx.HTTPError:
        verdict = None
    if verdict is None:  # portalen svarer ikke
        try:
            uid = await kv().get(f"sess:{token}")
        except Exception:  # noqa: BLE001
            uid = None
        with closing(db()) as con:
            verdict = bool(uid and con.execute("SELECT 1 FROM superadmins WHERE user_id = ? AND seen_at > ?",
                                               (int(uid), time.time() - 30 * 86400)).fetchone())
        _verdicts[token] = (time.monotonic() + 5, verdict)
        return verdict
    _verdicts[token] = (time.monotonic() + 30, verdict)
    return verdict


async def superadmin(request: Request) -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("x-requested-with", "").lower() != "fetch":
        raise HTTPException(403, "Mangler X-Requested-With")
    if not await is_superadmin(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Kun superadmin – log ind i portalen først")


async def service(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    if not SERVICE_TOKEN or not secrets.compare_digest(auth, f"Bearer {SERVICE_TOKEN}"):
        raise HTTPException(401, "Ugyldigt servicetoken")


# ---------------------------------------------------------------- noder

class NodeIn(BaseModel):
    name: str
    group: str
    role: str = "primary"
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(5432, ge=1, le=65535)
    dbname: str = "portal"
    user: str = "portal"
    primary_id: int | None = None  # kræves for en replika
    purpose: str = "read"  # replika: read = tager læsetrafik, backup = kun kopi
    note: str = ""


class NodePatch(BaseModel):
    name: str | None = None
    host: str | None = Field(None, min_length=1, max_length=253)
    port: int | None = Field(None, ge=1, le=65535)
    enabled: bool | None = None
    note: str | None = None
    purpose: str | None = None


class SettingsIn(BaseModel):
    read_replicas: dict[str, bool]


def new_token() -> str:
    return secrets.token_urlsafe(32)


def public(row: sqlite3.Row) -> dict:
    d = dict(row)
    d.pop("token")
    d["group"] = d.pop("grp")
    d["enabled"] = bool(d["enabled"])
    d["lag_seconds"] = None if d["lag_seconds"] is None else round(d["lag_seconds"], 1)
    return d


def get_node(con: sqlite3.Connection, node_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Ukendt node")
    return row


def check_host(host: str) -> str:
    host = host.strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", host):
        raise HTTPException(400, "Ugyldig adresse (IP eller værtsnavn)")
    return host


@asynccontextmanager
async def lifespan(_: FastAPI):
    init()
    task = asyncio.create_task(checker())
    yield
    task.cancel()


app = FastAPI(title="DOF.tool db-admin", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/me", dependencies=[Depends(superadmin)])
async def me():
    return {"ok": True}


@app.get("/api/nodes", dependencies=[Depends(superadmin)])
async def list_nodes():
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM nodes ORDER BY grp, CASE role WHEN 'primary' THEN 0 ELSE 1 END, name").fetchall()
        return {"nodes": [public(r) for r in rows], "read_replicas": setting(con, "read_replicas"), "version": setting(con, "version")}


@app.post("/api/nodes", dependencies=[Depends(superadmin)])
async def create_node(body: NodeIn):
    if not NAME.match(body.name):
        raise HTTPException(400, "Navn: små bogstaver, tal, - og _ (maks. 39 tegn)")
    if body.group not in GROUPS or body.role not in ROLES or body.purpose not in PURPOSES:
        raise HTTPException(400, "Ugyldig gruppe eller rolle")
    host = check_host(body.host)
    with closing(db()) as con:
        token, primary_id = new_token(), None
        if body.role == "replica":
            primary = con.execute("SELECT * FROM nodes WHERE id = ? AND role = 'primary' AND grp = ?",
                                  (body.primary_id, body.group)).fetchone()
            if primary is None:
                raise HTTPException(400, "En replika skal knyttes til en primary i samme gruppe")
            token, primary_id = primary["token"], primary["id"]  # replikaen er en kopi og har primariens adgangskode
        elif con.execute("SELECT 1 FROM nodes WHERE grp = ? AND role = 'primary' AND enabled = 1", (body.group,)).fetchone():
            raise HTTPException(400, f"Gruppen '{body.group}' har allerede en aktiv primary – tilføj en replika, eller deaktivér den først")
        try:
            cur = con.execute("INSERT INTO nodes (name, grp, role, host, port, dbname, user, token, primary_id, note, purpose) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (body.name, body.group, body.role, host, body.port, body.dbname, body.user, token, primary_id, body.note,
                               body.purpose if body.role == "replica" else "read"))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Navnet findes allerede") from None
        bump(con)
        con.commit()
        return {"id": cur.lastrowid, "token": token, "compose": compose(con, get_node(con, cur.lastrowid))}


@app.patch("/api/nodes/{node_id}", dependencies=[Depends(superadmin)])
async def patch_node(node_id: int, body: NodePatch):
    with closing(db()) as con:
        row = get_node(con, node_id)
        sets, args = [], []
        if body.name is not None:
            if not NAME.match(body.name):
                raise HTTPException(400, "Ugyldigt navn")
            sets.append("name = ?"); args.append(body.name)
        if body.host is not None:
            sets.append("host = ?"); args.append(check_host(body.host))
        if body.port is not None:
            sets.append("port = ?"); args.append(body.port)
        if body.note is not None:
            sets.append("note = ?"); args.append(body.note)
        if body.purpose is not None:
            if body.purpose not in PURPOSES or row["role"] != "replica":
                raise HTTPException(400, "Formål kan kun sættes på en replika (read/backup)")
            sets.append("purpose = ?"); args.append(body.purpose)
        if body.enabled is not None:
            if not body.enabled and row["role"] == "primary" and row["enabled"]:
                other = con.execute("SELECT 1 FROM nodes WHERE grp = ? AND role = 'primary' AND enabled = 1 AND id <> ?",
                                    (row["grp"], node_id)).fetchone()
                if other is None:
                    raise HTTPException(400, "Gruppen skal have en aktiv primary – gør en replika til primary først")
            sets.append("enabled = ?"); args.append(int(body.enabled))
        if sets:
            sets.append("status = 'unknown'")
            try:
                con.execute(f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?", (*args, node_id))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "Navnet findes allerede") from None
            bump(con)
            con.commit()
        return public(get_node(con, node_id))


@app.delete("/api/nodes/{node_id}", dependencies=[Depends(superadmin)])
async def delete_node(node_id: int):
    with closing(db()) as con:
        row = get_node(con, node_id)
        if row["role"] == "primary" and con.execute("SELECT 1 FROM nodes WHERE primary_id = ?", (node_id,)).fetchone():
            raise HTTPException(400, "Slet eller flyt først primariens replikaer")
        con.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        bump(con)
        con.commit()
    return {"ok": True}


@app.get("/api/nodes/{node_id}/token", dependencies=[Depends(superadmin)])
async def show_token(node_id: int):
    with closing(db()) as con:
        row = get_node(con, node_id)
        return {"token": row["token"], "compose": compose(con, row)}


@app.post("/api/nodes/{node_id}/promote", dependencies=[Depends(superadmin)])
async def promote(node_id: int):
    """Gør en replika til gruppens primary (efter at databasen selv er promoveret, se kommandoen i svaret)."""
    with closing(db()) as con:
        row = get_node(con, node_id)
        if row["role"] != "replica":
            raise HTTPException(400, "Noden er allerede primary")
        old = con.execute("SELECT * FROM nodes WHERE id = ?", (row["primary_id"],)).fetchone()
        con.execute("UPDATE nodes SET role = 'primary', primary_id = NULL, status = 'unknown' WHERE id = ?", (node_id,))
        if old is not None:
            con.execute("UPDATE nodes SET enabled = 0, status = 'unknown' WHERE id = ?", (old["id"],))
        con.execute("UPDATE nodes SET primary_id = ? WHERE primary_id = ? AND id <> ?", (node_id, row["primary_id"], node_id))
        bump(con)
        con.commit()
    return {"ok": True, "command": f"docker exec -u postgres <container> pg_ctl promote -D /var/lib/postgresql/data"}


@app.post("/api/nodes/{node_id}/rotate", dependencies=[Depends(superadmin)])
async def rotate(node_id: int):
    """Nyt token til en primary (og dens replikaer). Databasens egen adgangskode skal skiftes med kommandoen i svaret,
    og compose-filerne opdateres – ellers mister API'et forbindelsen."""
    with closing(db()) as con:
        row = get_node(con, node_id)
        if row["role"] != "primary":
            raise HTTPException(400, "Roter tokenet på primary")
        token = new_token()
        con.execute("UPDATE nodes SET token = ? WHERE id = ? OR primary_id = ?", (token, node_id, node_id))
        bump(con)
        con.commit()
        return {"token": token, "command": f"ALTER USER {row['user']} PASSWORD '{token}';"}


@app.put("/api/settings", dependencies=[Depends(superadmin)])
async def put_settings(body: SettingsIn):
    with closing(db()) as con:
        rr = {g: bool(body.read_replicas.get(g, False)) for g in GROUPS}
        con.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('read_replicas', ?)", (json.dumps(rr),))
        bump(con)
        con.commit()
    return {"read_replicas": rr}


@app.post("/api/check", dependencies=[Depends(superadmin)])
async def check_now():
    await check_all()
    return await list_nodes()


@app.get("/api/topology", dependencies=[Depends(service)])
async def topology():
    """Til API og worker: aktive noder med token, i den rækkefølge de skal bruges."""
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM nodes WHERE enabled = 1 ORDER BY id").fetchall()
        return {"version": setting(con, "version"), "read_replicas": setting(con, "read_replicas"),
                "nodes": [{"id": r["id"], "name": r["name"], "group": r["grp"], "role": r["role"], "host": r["host"],
                           "port": r["port"], "dbname": r["dbname"], "user": r["user"], "token": r["token"],
                           "purpose": r["purpose"], "enabled": True, "status": r["status"]} for r in rows]}


# ---------------------------------------------------------------- compose

TUNING = "-c shared_buffers=1GB -c work_mem=64MB -c maintenance_work_mem=512MB -c effective_cache_size=4GB -c max_wal_size=4GB -c random_page_cost=1.1"


def compose(con: sqlite3.Connection, row: sqlite3.Row) -> str:
    """docker-compose.yml til serveren hvor noden skal køre (mappen dbnode/ fra projektet kopieres dertil)."""
    env = [f"      POSTGRES_DB: {row['dbname']}", f"      POSTGRES_USER: {row['user']}", f"      POSTGRES_PASSWORD: {row['token']}",
           f"      DB_ROLE: {row['role']}"]
    note = ""
    if row["role"] == "replica":
        p = con.execute("SELECT * FROM nodes WHERE id = ?", (row["primary_id"],)).fetchone()
        env += [f"      PRIMARY_HOST: {p['host'] if p else 'ADRESSE-PÅ-PRIMARY'}", f"      PRIMARY_PORT: \"{p['port'] if p else 5432}\""]
        note = f"# Replika af {p['name'] if p else '?'} ({'aflaster læsninger' if row['purpose'] == 'read' else 'kun backup'}): tager en basebackup af primary ved første start og streamer derefter.\n"
    return f"""# {row['name']} – {'brugerdata' if row['grp'] == 'users' else 'observationer'} ({row['role']})
# Kopiér mappen dbnode/ fra projektet til serveren ved siden af denne fil.
{note}name: dof-db-{row['name']}
services:
  postgres:
    build: ./dbnode
    restart: unless-stopped
    environment:
{chr(10).join(env)}
      TZ: Europe/Copenhagen
    command: >
      postgres {TUNING}
               -c wal_level=replica -c max_wal_senders=10 -c hot_standby=on -c wal_keep_size=2GB
    ports:
      - "{row['port']}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U {row['user']} -d {row['dbname']}"]
      interval: 5s
      timeout: 5s
      retries: 20
volumes:
  pgdata:
"""


@app.get("/api/nodes/{node_id}/compose", dependencies=[Depends(superadmin)], response_class=PlainTextResponse)
async def node_compose(node_id: int):
    with closing(db()) as con:
        return compose(con, get_node(con, node_id))


# ---------------------------------------------------------------- sundhedstjek

async def probe(row: sqlite3.Row) -> tuple[str, str, float | None]:
    try:
        conn = await asyncpg.connect(host=row["host"], port=row["port"], user=row["user"], password=row["token"],
                                     database=row["dbname"], timeout=4)
    except Exception as exc:  # noqa: BLE001
        return "down", str(exc)[:200] or exc.__class__.__name__, None
    try:
        recovery = await conn.fetchval("SELECT pg_is_in_recovery()")
        lag = await conn.fetchval("SELECT extract(epoch FROM now() - pg_last_xact_replay_timestamp())") if recovery else None
        if row["role"] == "primary" and recovery:
            return "wrong", "Registreret som primary, men databasen kører som replika", lag
        if row["role"] == "replica" and not recovery:
            return "wrong", "Registreret som replika, men databasen er primary", None
        return "ok", "", float(lag) if lag is not None else None
    except Exception as exc:  # noqa: BLE001
        return "down", str(exc)[:200], None
    finally:
        await conn.close()


async def check_all() -> None:
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM nodes WHERE enabled = 1").fetchall()
    results = await asyncio.gather(*(probe(r) for r in rows))
    with closing(db()) as con:
        changed = False
        for r, (status, detail, lag) in zip(rows, results):
            cur = con.execute("SELECT status FROM nodes WHERE id = ?", (r["id"],)).fetchone()
            if cur is None:
                continue
            changed |= cur["status"] != status
            con.execute("UPDATE nodes SET status = ?, detail = ?, lag_seconds = ?, checked_at = ? WHERE id = ?",
                        (status, detail, lag, time.time(), r["id"]))
        if changed:
            bump(con)  # API'et henter topologien igen, så en node der er gået ned springes over hurtigt
        con.commit()


async def checker() -> None:
    while True:
        try:
            await check_all()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(CHECK_SECONDS)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html", headers={"Cache-Control": "no-cache"})
