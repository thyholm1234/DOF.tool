"""Docker-styring til panelet: opret database-containere og stop/start portalens egne api/worker under en migrering.

Kræver /var/run/docker.sock i panel-containeren. Uden den må noderne oprettes og migreringen køres manuelt."""

from __future__ import annotations

import os
import socket

SOCKET = "/var/run/docker.sock"
IMAGE = os.environ.get("DBNODE_IMAGE", "doftool-dbnode:latest")
TUNING = ["-c", "shared_buffers=1GB", "-c", "work_mem=64MB", "-c", "maintenance_work_mem=512MB", "-c", "effective_cache_size=4GB",
          "-c", "max_wal_size=8GB", "-c", "random_page_cost=1.1", "-c", "wal_level=replica", "-c", "max_wal_senders=10",
          "-c", "hot_standby=on", "-c", "wal_keep_size=2GB"]


def available() -> bool:
    return os.path.exists(SOCKET)


def client():
    import docker
    return docker.from_env()


def context(cli) -> tuple[str, str]:
    """(compose-projektets navn, netværk) for panelets egen container – nye noder lægges i samme netværk."""
    me = cli.containers.get(socket.gethostname())
    project = me.labels.get("com.docker.compose.project") or "doftool"
    networks = list(me.attrs["NetworkSettings"]["Networks"])
    return project, networks[0]


def services(cli, project: str, names: tuple[str, ...]):
    out = []
    for n in names:
        out += cli.containers.list(all=True, filters={"label": [f"com.docker.compose.project={project}",
                                                                f"com.docker.compose.service={n}"]})
    return out


def create_primary(cli, project: str, network: str, name: str, token: str, dbname: str, user: str) -> str:
    """Opret og start en primary-container; returnerer værtsnavnet (containernavnet) i compose-netværket."""
    cname = f"{project}-db-{name}"
    try:
        cli.images.get(IMAGE)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Billedet {IMAGE} findes ikke – kør 'docker compose build dbnode-image' (eller 'up -d --build')") from exc
    cli.containers.run(IMAGE, ["postgres", *TUNING], name=cname, detach=True, network=network,
                       restart_policy={"Name": "unless-stopped"}, labels={"dof.dbnode": name},
                       environment={"POSTGRES_DB": dbname, "POSTGRES_USER": user, "POSTGRES_PASSWORD": token, "DB_ROLE": "primary",
                                    "TZ": "Europe/Copenhagen"},
                       volumes={f"{project}_db_{name}": {"bind": "/var/lib/postgresql/data", "mode": "rw"}})
    return cname
