"""DOF.tool db-admin: register over databasenoderne.

Login med eget kodeord (DBADMIN_PASSWORD), uafhængigt af portalen. Man opretter en node (bruger- eller observationsdatabase, primary eller replika), får et token som bruges som
databasens adgangskode, og et script der opretter noden på serveren, og kan rette IP/port bagefter. API'et og workeren henter topologien her
(/api/topology) og er load balancer for hele DOF-økosystemet. Registret ligger i en lille SQLite i /data, så panelet
ikke afhænger af de databaser det styrer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import time
from contextlib import asynccontextmanager, closing, suppress
from pathlib import Path

import asyncpg
import dockerops
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

SERVICE_TOKEN = os.environ.get("DBADMIN_SERVICE_TOKEN", "")
PASSWORD = os.environ.get("DBADMIN_PASSWORD", "")
COOKIE = "dbadmin_session"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
SESSION_HOURS = 12
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
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, expires REAL NOT NULL);
"""
DEFAULT_SETTINGS = {"read_replicas": {"users": False, "observations": True}, "version": 1}
LEGACY_DSN = os.environ.get("LEGACY_POSTGRES_DSN", "")


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
        # mode: 'legacy' = portalen bruger stadig den samlede database; 'split' = bruger-/observationsnoderne bruges.
        # Et register der allerede har aktive primaries (oprettet før mode fandtes) regnes som split.
        has_nodes = con.execute("SELECT 1 FROM nodes WHERE role = 'primary' AND enabled = 1").fetchone() is not None
        con.execute("INSERT OR IGNORE INTO settings (k, v) VALUES ('mode', ?)", (json.dumps("split" if has_nodes else "legacy"),))
        con.commit()


def setting(con: sqlite3.Connection, k: str):
    row = con.execute("SELECT v FROM settings WHERE k = ?", (k,)).fetchone()
    return json.loads(row["v"]) if row else DEFAULT_SETTINGS.get(k)


def bump(con: sqlite3.Connection) -> None:
    con.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('version', ?)", (json.dumps(setting(con, "version") + 1),))


# ---------------------------------------------------------------- login med eget kodeord (DBADMIN_PASSWORD)

_fails: dict[str, list[float]] = {}


class LoginIn(BaseModel):
    password: str


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    with closing(db()) as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        row = con.execute("SELECT 1 FROM sessions WHERE token = ?", (token,)).fetchone()
        con.commit()
    return row is not None


async def superadmin(request: Request) -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("x-requested-with", "").lower() != "fetch":
        raise HTTPException(403, "Mangler X-Requested-With")
    if not valid_session(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Log ind")


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


@app.post("/api/login")
async def login(body: LoginIn, request: Request, response: Response):
    if request.headers.get("x-requested-with", "").lower() != "fetch":
        raise HTTPException(403, "Mangler X-Requested-With")
    if not PASSWORD:
        raise HTTPException(503, "DBADMIN_PASSWORD er ikke sat på serveren")
    ip = request.client.host if request.client else "?"
    recent = [t for t in _fails.get(ip, []) if t > time.time() - 300]
    if len(recent) >= 5:
        raise HTTPException(429, "For mange forsøg – prøv igen om lidt")
    if not secrets.compare_digest(body.password.encode(), PASSWORD.encode()):
        _fails[ip] = [*recent, time.time()]
        raise HTTPException(401, "Forkert kodeord")
    _fails.pop(ip, None)
    token = secrets.token_urlsafe(32)
    with closing(db()) as con:
        con.execute("INSERT INTO sessions (token, expires) VALUES (?, ?)", (token, time.time() + SESSION_HOURS * 3600))
        con.commit()
    response.set_cookie(COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True, samesite="strict", secure=COOKIE_SECURE)
    return {"ok": True}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with closing(db()) as con:
            con.execute("DELETE FROM sessions WHERE token = ?", (token,))
            con.commit()
    response.delete_cookie(COOKIE)
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
        return {"id": cur.lastrowid, "token": token, "script": setup_script(con, get_node(con, cur.lastrowid), await asyncio.to_thread(panel_network))}


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
        return {"token": row["token"], "script": setup_script(con, row, await asyncio.to_thread(panel_network))}


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
    return {"ok": True, "command": "docker exec -u postgres <container> pg_ctl promote -D /var/lib/postgresql/data"}


@app.post("/api/nodes/{node_id}/rotate", dependencies=[Depends(superadmin)])
async def rotate(node_id: int):
    """Nyt token til en primary (og dens replikaer). Databasens egen adgangskode skal skiftes med kommandoen i svaret,
    og containerne genstartes med det nye token – ellers mister API'et forbindelsen."""
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
        return {"version": setting(con, "version"), "read_replicas": setting(con, "read_replicas"), "mode": setting(con, "mode"),
                "nodes": [{"id": r["id"], "name": r["name"], "group": r["grp"], "role": r["role"], "host": r["host"],
                           "port": r["port"], "dbname": r["dbname"], "user": r["user"], "token": r["token"],
                           "purpose": r["purpose"], "enabled": True, "status": r["status"]} for r in rows]}


# ---------------------------------------------------------------- opsætningsscript

DBNODE_DIR = Path(__file__).resolve().parent.parent / "dbnode"  # Dockerfile og scripts til doftool-dbnode-billedet
IP = re.compile(r"[0-9.]+|.*:.*|localhost")


def panel_network() -> str:
    """Portalens Docker-netværk (det panelet selv kører i); noder på samme vært lægges i det og nås ved containernavn."""
    if dockerops.available():
        with suppress(Exception):
            return dockerops.context(dockerops.client())[1]
    return "doftool_default"


def setup_script(con: sqlite3.Connection, row: sqlite3.Row, network: str) -> str:
    """Bash-script der opretter og starter noden på serveren – indsættes direkte i en terminal. Bygger doftool-dbnode
    (filerne er indlejret), hvis billedet mangler, og lægger containeren i portalens netværk, hvis det findes på serveren."""
    q = shlex.quote
    # Er registrets adresse et navn (ikke IP), bruges det som containernavn, så portalen kan nå noden ved navn
    container = row["host"] if not IP.fullmatch(row["host"]) else f"doftool-db-{row['name']}"
    env = {"POSTGRES_DB": row["dbname"], "POSTGRES_USER": row["user"], "POSTGRES_PASSWORD": row["token"], "DB_ROLE": row["role"],
           "TZ": "Europe/Copenhagen"}
    note = primary = ""
    if row["role"] == "replica":
        p = con.execute("SELECT * FROM nodes WHERE id = ?", (row["primary_id"],)).fetchone()
        env |= {"PRIMARY_HOST": "$PRIMARY_HOST", "PRIMARY_PORT": "$PRIMARY_PORT"}
        primary = (f"PRIMARY_HOST={q(p['host'] if p else 'ADRESSE-PÅ-PRIMARY')}\nPRIMARY_PORT={int(p['port'] if p else 5432)}\n"
                   "# Kører primary som container på samme vært, nås den i netværket på sin interne port\n"
                   'if docker container inspect "$PRIMARY_HOST" >/dev/null 2>&1; then PRIMARY_PORT=5432; fi\n')
        note = (f"# Replika af {p['name'] if p else '?'} ({'aflaster læsninger' if row['purpose'] == 'read' else 'kun backup'}): "
                "tager en basebackup af primary ved første start og streamer derefter.\n")
    files = "".join(f"    cat > \"$dir/{f}\" <<'DOFNODE_FILE'\n{(DBNODE_DIR / f).read_text().rstrip()}\nDOFNODE_FILE\n"
                    for f in ("Dockerfile", "node-entrypoint.sh", "10-replication.sh"))
    envs = " ".join(f"-e {k}=" + (f'"{v}"' if v.startswith("$") else q(v)) for k, v in env.items())
    return f"""# {row['name']} – {'brugerdata' if row['grp'] == 'users' else 'observationer'} ({row['role']})
# Indsæt det hele i en terminal på serveren, hvor noden skal køre (root eller bruger i docker-gruppen). Kan køres igen.
{note}bash <<'DOFNODE'
set -euo pipefail
NAME={q(row['name'])}
CONTAINER={q(container)}
VOLUME={q('doftool_db_' + row['name'])}
IMAGE=doftool-dbnode:latest
NETWORK={q(network)}
PORT={int(row['port'])}
REG_HOST={q(row['host'])}
REG_PORT={int(row['port'])}
{primary}
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "$CONTAINER findes allerede – starter den (data i volumen $VOLUME bevares)"
  docker start "$CONTAINER" >/dev/null
else
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Bygger $IMAGE"
    dir=$(mktemp -d)
{files}    docker build -q -t "$IMAGE" "$dir" >/dev/null
    rm -rf "$dir"
  fi
  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    # Portalen når noden i Docker-netværket; porten på værten er kun til lokal adgang (psql på serveren)
    net=(--network "$NETWORK" -p "127.0.0.1:$PORT:5432")
  else
    echo "Netværket $NETWORK findes ikke her – noden nås på serverens IP, port $PORT."
    echo "Begræns porten i firewallen til portalens server – databasen må ikke være åben mod internettet."
    net=(-p "$PORT:5432")
  fi
  docker run -d --name "$CONTAINER" --restart unless-stopped --label dof.dbnode="$NAME" "${{net[@]}}" \\
    --log-opt max-size=10m --log-opt max-file=5 -v "$VOLUME:/var/lib/postgresql/data" \\
    {envs} \\
    --health-cmd {q(f"pg_isready -h 127.0.0.1 -U {row['user']} -d {row['dbname']}")} --health-interval 5s --health-timeout 5s \\
    --health-retries 20 --health-start-period 1h \\
    "$IMAGE" postgres {' '.join(dockerops.TUNING)} >/dev/null
fi

echo "Venter på at $CONTAINER bliver klar (en replika kopierer først hele primary – det kan tage et stykke tid) ..."
docker logs -f "$CONTAINER" 2>&1 &
logs=$!
stop_logs() {{ {{ kill $logs; wait $logs; }} 2>/dev/null || true; }}
trap stop_logs EXIT
while :; do
  case "$(docker inspect -f '{{{{.State.Status}}}} {{{{.State.Health.Status}}}}' "$CONTAINER")" in
    "running healthy") break ;;
    exited*|dead*|restarting*) sleep 1; echo; echo "FEJL: $CONTAINER stoppede – se 'docker logs $CONTAINER'"; exit 1 ;;
  esac
  sleep 3
done
sleep 1
stop_logs
echo
echo "OK: $CONTAINER kører."
if [ "$(docker inspect -f '{{{{index .NetworkSettings.Networks "'"$NETWORK"'"}}}}' "$CONTAINER")" != "<no value>" ]; then
  [ "$REG_HOST:$REG_PORT" = "$CONTAINER:5432" ] || echo "Ret adressen i panelet til $CONTAINER, port 5432 (samme Docker-netværk som portalen)."
else
  echo "Sæt adressen i panelet til serverens IP og port $PORT (nu: $REG_HOST:$REG_PORT)."
fi
DOFNODE
"""


@app.get("/api/nodes/{node_id}/script", dependencies=[Depends(superadmin)], response_class=PlainTextResponse)
async def node_script(node_id: int):
    network = await asyncio.to_thread(panel_network)
    with closing(db()) as con:
        return setup_script(con, get_node(con, node_id), network)


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


# ---------------------------------------------------------------- opsætning og migrering fra den samlede database

mig: dict = {"running": False, "step": "", "log": [], "error": None, "started": None, "finished": None, "status": None}
mig_task: asyncio.Task | None = None
log = logging.getLogger("dbadmin")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def say(msg: str) -> None:
    mig["step"] = msg
    mig["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
    del mig["log"][:-500]
    log.info("migrering: %s", msg)  # også i 'docker logs <dbadmin>'


async def set_status(st: dict | None) -> None:
    mig["status"] = {**st, "at": time.time()} if st else None


def _asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


ACTIVITY_SQL = """
    SELECT pid, state, wait_event_type, wait_event, round(extract(epoch FROM now() - query_start)) AS seconds,
           left(regexp_replace(query, '\\s+', ' ', 'g'), 160) AS query
    FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND state <> 'idle'
    ORDER BY query_start"""


@app.get("/api/migration/activity", dependencies=[Depends(superadmin)])
async def migration_activity():
    """Hvad laver databaserne lige nu? Aktive forespørgsler, ventetilstande (fx låse) og COPY/indeks-fremdrift."""
    targets = [("samlet (kilde)", _asyncpg_dsn(LEGACY_DSN))] if LEGACY_DSN else []
    with closing(db()) as con:
        for r in con.execute("SELECT * FROM nodes WHERE role = 'primary' AND enabled = 1"):
            targets.append((r["name"], {"host": r["host"], "port": r["port"], "user": r["user"], "password": r["token"],
                                        "database": r["dbname"]}))
    out = []
    for name, target in targets:
        try:
            conn = await (asyncpg.connect(target, timeout=5) if isinstance(target, str) else asyncpg.connect(**target, timeout=5))
        except Exception as exc:  # noqa: BLE001
            out.append({"node": name, "error": str(exc)})
            continue
        try:
            act = [dict(r) for r in await conn.fetch(ACTIVITY_SQL)]
            copy = [dict(r) for r in await conn.fetch(
                "SELECT relid::regclass::text AS tabel, command, type, bytes_processed, tuples_processed FROM pg_stat_progress_copy")]
            idx = [dict(r) for r in await conn.fetch(
                "SELECT relid::regclass::text AS tabel, index_relid::regclass::text AS indeks, phase, blocks_done, blocks_total, "
                "tuples_done, tuples_total FROM pg_stat_progress_create_index")]
            size = await conn.fetchval("SELECT pg_size_pretty(pg_database_size(current_database()))")
            out.append({"node": name, "size": size, "activity": act, "copy": copy, "index": idx})
        except Exception as exc:  # noqa: BLE001
            out.append({"node": name, "error": str(exc)})
        finally:
            await conn.close()
    disk = shutil.disk_usage("/tmp")
    return {"nodes": out, "panel_tmp_free_gb": round(disk.free / 1e9, 1)}


class MigrateIn(BaseModel):
    stop_services: bool = True  # stop api/worker under flytningen, så der ikke skrives i den gamle database
    wipe: bool = False  # erstat eksisterende data i de nye databaser


@app.get("/api/migration", dependencies=[Depends(superadmin)])
async def migration_state():
    with closing(db()) as con:
        mode = setting(con, "mode")
    return {**mig, "mode": mode, "docker": dockerops.available(), "legacy_configured": bool(LEGACY_DSN)}


@app.post("/api/migration", dependencies=[Depends(superadmin)])
async def migration_start(body: MigrateIn):
    with closing(db()) as con:
        if setting(con, "mode") == "split" and not body.wipe:
            raise HTTPException(400, "Opdelingen er allerede slået til")
    if mig["running"]:
        raise HTTPException(409, "En migrering kører allerede")
    if not LEGACY_DSN:
        raise HTTPException(400, "LEGACY_POSTGRES_DSN er ikke sat på panelet")
    mig.update(running=True, step="Starter", log=[], error=None, started=time.time(), finished=None, status=None)
    global mig_task  # asyncio holder kun en svag reference; uden den kan en lang migrering blive samlet op undervejs
    mig_task = asyncio.create_task(run_migration(body))
    return {"ok": True}


@app.post("/api/mode", dependencies=[Depends(superadmin)])
async def set_mode(body: dict):
    """Skift tilbage til den samlede database (data skrevet siden migreringen følger ikke med)."""
    if body.get("mode") not in ("legacy", "split"):
        raise HTTPException(400, "Ugyldig tilstand")
    with closing(db()) as con:
        con.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('mode', ?)", (json.dumps(body["mode"]),))
        bump(con)
        con.commit()
    return {"mode": body["mode"]}


async def wait_ready(row: dict, seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            conn = await asyncpg.connect(host=row["host"], port=row["port"], user=row["user"], password=row["token"],
                                         database=row["dbname"], timeout=3)
            await conn.close()
            return
        except Exception:  # noqa: BLE001
            if time.monotonic() > deadline:
                raise RuntimeError(f"{row['name']} svarer ikke på {row['host']}:{row['port']}") from None
            await asyncio.sleep(2)


async def run_migration(opts: MigrateIn) -> None:
    from portal.dbops import migrate
    from portal.dbrouter import Node, router

    stopped: list = []
    cli = project = network = None
    try:
        use_docker = dockerops.available()
        if use_docker:
            cli = await asyncio.to_thread(dockerops.client)
            project, network = await asyncio.to_thread(dockerops.context, cli)
        if opts.stop_services and use_docker:
            await say("Stopper api og worker")
            for c in await asyncio.to_thread(dockerops.services, cli, project, ("api", "worker")):
                if c.status == "running":
                    await asyncio.to_thread(c.stop, timeout=30)
                    stopped.append(c)
        elif opts.stop_services:
            await say("Docker er ikke tilgængelig i panelet – stop api og worker selv under flytningen")

        for group, label in (("users", "brugerdata"), ("observations", "observationer")):
            with closing(db()) as con:
                node = con.execute("SELECT * FROM nodes WHERE grp = ? AND role = 'primary' AND enabled = 1", (group,)).fetchone()
            if node is not None:
                await say(f"Bruger eksisterende {label}-node {node['name']}")
                continue
            if not use_docker:
                raise RuntimeError(f"Der er ingen primary for {label}. Opret den under 'Tilføj node' (eller giv panelet adgang til Docker).")
            name, token = ("users-1" if group == "users" else "obs-1"), new_token()
            await say(f"Opretter {label}-database {name}")
            host = await asyncio.to_thread(dockerops.create_primary, cli, project, network, name, token, "portal", "portal")
            with closing(db()) as con:
                con.execute("INSERT INTO nodes (name, grp, role, host, port, dbname, user, token, note) "
                            "VALUES (?, ?, 'primary', ?, 5432, 'portal', 'portal', ?, 'oprettet af panelet')", (name, group, host, token))
                bump(con)
                con.commit()
        with closing(db()) as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM nodes WHERE role = 'primary' AND enabled = 1")]
        for r in rows:
            await say(f"Venter på {r['name']} ({r['host']}:{r['port']})")
            await wait_ready(r)
        router.set_nodes([Node(r["id"], r["name"], r["grp"], "primary", r["host"], r["port"], r["dbname"], r["user"], r["token"])
                          for r in rows])
        await migrate(LEGACY_DSN, wipe=opts.wipe, progress=say, status=set_status)
        with closing(db()) as con:
            con.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('mode', ?)", (json.dumps("split"),))
            bump(con)
            con.commit()
        await say("Migrering færdig – opdelingen er slået til")
    except Exception as exc:  # noqa: BLE001
        log.exception("Migrering fejlede")
        mig["error"] = str(exc) or exc.__class__.__name__
        await say("FEJL: " + mig["error"] + " – portalen bruger stadig den samlede database. Start igen for at fortsætte, "
                  "hvor den slap (færdige tabeller og bidder springes over).")
    finally:
        for c in stopped:
            try:
                await asyncio.to_thread(c.start)
            except Exception as exc:  # noqa: BLE001
                await say(f"Kunne ikke starte {c.name}: {exc}")
        if stopped:
            await say("Api og worker startet igen")
        mig.update(running=False, finished=time.time())
        with suppress(Exception):
            await router.stop()


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html", headers={"Cache-Control": "no-cache"})
