"""Databasegrupper og routing: brugerdata og observationer ligger i hver sin gruppe af Postgres-noder.

Noderne styres fra db-admin-containeren (Admin → databaser): hver node har en gruppe (users/observations), en rolle
(primary/replica), adresse og token. API'et (og workeren) henter topologien derfra og er dermed load balanceren for
hele DOF-økosystemet:

* skrivninger går altid til gruppens primary
* læsninger kan fordeles på replikaer med formålet 'read' (slås til pr. gruppe i db-admin; standard: kun observationer);
  replikaer med formålet 'backup' er kun kopier og bruges aldrig til læsning
* en session der har skrevet i en gruppe læser resten af transaktionen fra primary (ingen forældet læsning)
* en sætning må kun røre én gruppe; referencedata (arter, lokaliteter …) findes i begge og holdes ens af sync_reference()

Uden DBADMIN_URL bruges POSTGRES_DSN som én enkelt node for begge grupper (som før).
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.sql import ClauseElement
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.selectable import Select
from sqlalchemy.sql.util import find_tables

from portal.config import get_settings
from portal.dbgroups import GROUPS, OBSERVATIONS, USERS, group_of_sql, group_of_table

log = logging.getLogger("portal.dbrouter")

REFRESH_SECONDS = 15
DOWN_SECONDS = 30  # en node der gav forbindelsesfejl springes over så længe


@dataclass(frozen=True)
class Node:
    id: int
    name: str
    group: str
    role: str  # primary / replica
    host: str
    port: int
    dbname: str
    user: str
    token: str
    healthy: bool = True
    purpose: str = "read"  # replika: read (aflaster læsninger) eller backup (aldrig brugt til læsning)

    @property
    def dsn(self) -> str:
        return f"postgresql+asyncpg://{quote(self.user)}:{quote(self.token, safe='')}@{self.host}:{self.port}/{self.dbname}"

    @property
    def key(self) -> str:
        return hashlib.sha1(self.dsn.encode()).hexdigest()[:12]


class Router:
    def __init__(self) -> None:
        self.nodes: list[Node] = []
        self.read_replicas: dict[str, bool] = {USERS: False, OBSERVATIONS: True}
        self.version = 0
        self.engines: dict[str, AsyncEngine] = {}
        self._down: dict[str, float] = {}
        self._rr = itertools.count()
        self._task: asyncio.Task | None = None
        self._started = False
        self.legacy = True

    # ------------------------------------------------------------ opsætning

    async def start(self) -> None:
        if self._started:
            return
        s = get_settings()
        if not s.dbadmin_url:
            self._apply_legacy(s.postgres_dsn)
        else:
            for attempt in range(1, 31):
                try:
                    await self.refresh()
                    break
                except Exception as exc:  # noqa: BLE001 – db-admin kan starte lidt efter os
                    log.warning("Topologi fra db-admin ikke klar (%s) – forsøg %s af 30", exc, attempt)
                    await asyncio.sleep(2)
            else:
                raise RuntimeError("Kunne ikke hente databasetopologien fra " + s.dbadmin_url)
            self._task = asyncio.create_task(self._refresh_loop())
        self._started = True

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        for e in {id(e): e for e in self.engines.values()}.values():
            await e.dispose()
        self.engines.clear()
        self._started = False

    def _apply_legacy(self, dsn: str) -> None:
        from sqlalchemy.engine import make_url
        u = make_url(dsn)
        self.legacy = True
        self.nodes = [Node(0, "postgres", g, "primary", u.host or "localhost", u.port or 5432, u.database or "portal",
                           u.username or "portal", u.password or "") for g in GROUPS]
        self.version = 0

    async def refresh(self) -> None:
        s = get_settings()
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(s.dbadmin_url.rstrip("/") + "/api/topology",
                                 headers={"Authorization": f"Bearer {s.dbadmin_service_token}"})
            r.raise_for_status()
            data = r.json()
        if data.get("mode") == "legacy":  # db-admin: opdelingen er ikke slået til endnu (eller rullet tilbage)
            self._apply_legacy(s.postgres_dsn)
            self._drop_unused(old=self.engines)
            return
        nodes = [Node(n["id"], n["name"], n["group"], n["role"], n["host"], int(n["port"]), n["dbname"], n["user"], n["token"],
                      n.get("status") != "down", n.get("purpose", "read")) for n in data["nodes"] if n.get("enabled", True)]
        for g in GROUPS:
            if not any(n.group == g and n.role == "primary" for n in nodes):
                raise RuntimeError(f"Ingen primary i gruppen '{g}' – opret en under Admin → databaser")
        self.legacy = False
        self.read_replicas = {g: bool(data.get("read_replicas", {}).get(g, g == OBSERVATIONS)) for g in GROUPS}
        if nodes != self.nodes:
            log.info("Databasetopologi opdateret: %s", ", ".join(f"{n.group}/{n.role}:{n.name}@{n.host}:{n.port}" for n in nodes))
        self.nodes, self.version = nodes, data.get("version", 0)
        self._drop_unused(old=self.engines)

    def _drop_unused(self, old: dict[str, AsyncEngine]) -> None:
        keep = {n.key for n in self.nodes}
        for k in [k for k in old if k not in keep]:
            asyncio.create_task(old.pop(k).dispose())

    def set_nodes(self, nodes: list[Node], read_replicas: dict[str, bool] | None = None) -> None:
        """Brug en given nodeliste uden at hente topologien (db-admin bruger det under migrering)."""
        self.legacy, self._started = False, True
        self.nodes = nodes
        if read_replicas:
            self.read_replicas = read_replicas
        self._drop_unused(old=self.engines)

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(REFRESH_SECONDS)
            try:
                await self.refresh()
            except Exception as exc:  # noqa: BLE001 – behold den kendte topologi
                log.warning("Kunne ikke opdatere topologien: %s", exc)

    # ------------------------------------------------------------ valg af node

    def engine_for(self, node: Node) -> AsyncEngine:
        eng = self.engines.get(node.key)
        if eng is None:
            eng = create_async_engine(node.dsn, pool_size=10, max_overflow=20, pool_pre_ping=True)
            event.listen(eng.sync_engine, "handle_error", lambda ctx, n=node: self._failed(n, ctx))
            event.listen(eng.sync_engine, "do_connect", lambda dialect, rec, cargs, cparams, n=node: self._connect(n, dialect, cargs, cparams))
            self.engines[node.key] = eng
        return eng

    def _connect(self, node: Node, dialect, cargs, cparams):
        """Ny forbindelse til noden; mislykkes det, springes noden over et stykke tid (asyncpg-fejl som DNS/refused når
        ikke altid frem til handle_error)."""
        try:
            return dialect.connect(*cargs, **cparams)
        except Exception:
            self._down[node.key] = time.monotonic() + DOWN_SECONDS
            log.warning("Kan ikke forbinde til node %s (%s:%s) – springes over i %s s", node.name, node.host, node.port, DOWN_SECONDS)
            raise

    def _failed(self, node: Node, ctx) -> None:
        if ctx.is_disconnect or isinstance(ctx.original_exception, (OSError, ConnectionError)):
            self._down[node.key] = time.monotonic() + DOWN_SECONDS
            log.warning("Node %s (%s:%s) svarer ikke – springes over i %s s", node.name, node.host, node.port, DOWN_SECONDS)

    def _up(self, node: Node) -> bool:
        return node.healthy and self._down.get(node.key, 0) < time.monotonic()

    def primary_node(self, group: str) -> Node:
        return next(n for n in self.nodes if n.group == group and n.role == "primary")

    def primary(self, group: str) -> AsyncEngine:
        return self.engine_for(self.primary_node(group))

    def reader(self, group: str) -> AsyncEngine:
        if self.read_replicas.get(group):
            ups = [n for n in self.nodes if n.group == group and n.role == "replica" and n.purpose == "read" and self._up(n)]
            if ups:
                return self.engine_for(ups[next(self._rr) % len(ups)])
        return self.primary(group)

    def primaries(self) -> dict[str, AsyncEngine]:
        return {g: self.primary(g) for g in GROUPS}

    def separate(self) -> bool:
        """Er brugere og observationer på hver sin database?"""
        return self.primary(USERS) is not self.primary(OBSERVATIONS) and \
            self.primary_node(USERS).key != self.primary_node(OBSERVATIONS).key


router = Router()

# ---------------------------------------------------------------- session

_WRITE = re.compile(r"\b(insert\s+into|update\s+[\w\"]|delete\s+from|truncate|lock\s+table|for\s+update|for\s+share|copy|vacuum|"
                    r"analyze|refresh\s+materialized|create|alter|drop)\b|nextval|setval|advisory", re.I)
_READ = re.compile(r"\s*\(?\s*(select|with|show|explain|values)\b", re.I)


def is_read_sql(sql: str) -> bool:
    return bool(_READ.match(sql)) and not _WRITE.search(sql)


class RoutingSession(Session):
    """Sender hver sætning til den databasegruppe, tabellerne hører til (se dbgroups). `info['home']` afgør hvor sætninger
    uden gruppetabeller (kun referencedata) og rå forbindelser går hen; standard er brugergruppen."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._written: set[str] = set()

    def _home(self) -> str:
        return self.info.get("home", USERS)

    def _classify(self, mapper, clause) -> tuple[str, bool]:
        """(gruppe, er_skrivning)"""
        # ORM-sætninger (update(Model), delete(Model)) kommer med både mapper og clause – sætningstypen afgør, ikke mapperen
        if isinstance(clause, (Insert, Update, Delete)):
            return group_of_table(clause.table.name) or self._home(), True
        if mapper is not None:
            locks = isinstance(clause, Select) and clause._for_update_arg is not None
            return group_of_table(mapper.local_table.name) or self._home(), self._flushing or locks
        if clause is None:
            return self._home(), True
        if isinstance(clause, Select):
            groups = {g for t in find_tables(clause, check_columns=False, include_joins=False, include_aliases=False)
                      if (g := group_of_table(t.name))}
            if len(groups) > 1:
                raise ValueError("Forespørgslen blander bruger- og observationstabeller")
            return next(iter(groups), self._home()), clause._for_update_arg is not None
        sql = str(clause)
        return group_of_sql(sql) or self._home(), not is_read_sql(sql)

    def get_bind(self, mapper=None, clause: ClauseElement | None = None, **kw):
        group, write = self._classify(mapper, clause)
        if write:
            self._written.add(group)
        if write or group in self._written or self.info.get("primary_only"):
            return router.primary(group).sync_engine
        return router.reader(group).sync_engine

    def _forget(self) -> None:
        self._written.clear()

    def commit(self):
        try:
            return super().commit()
        finally:
            self._forget()

    def rollback(self):
        try:
            return super().rollback()
        finally:
            self._forget()

    def close(self):
        try:
            return super().close()
        finally:
            self._forget()


def _factory(**info) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(class_=AsyncSession, sync_session_class=RoutingSession, expire_on_commit=False, info=info)


SessionLocal = _factory(home=USERS)
# Til kode der skriver observationer/referencedata (indlæsning fra DOFbasen): læser også kun fra primary (ingen replika-forsinkelse)
ObsSession = _factory(home=OBSERVATIONS, primary_only=True)
