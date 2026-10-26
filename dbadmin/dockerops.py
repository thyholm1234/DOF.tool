"""Docker-styring til panelet: opret database-containere, find eksisterende og læg dem i panelets netværk.

Kræver /var/run/docker.sock i panel-containeren. Uden den må noderne oprettes og tilsluttes manuelt."""

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
                       log_config={"Type": "json-file", "Config": {"max-size": "10m", "max-file": "5"}},
                       environment={"POSTGRES_DB": dbname, "POSTGRES_USER": user, "POSTGRES_PASSWORD": token, "DB_ROLE": "primary",
                                    "TZ": "Europe/Copenhagen"},
                       volumes={f"{project}_db_{name}": {"bind": "/var/lib/postgresql/data", "mode": "rw"}})
    return cname


def dbnode_containers(cli) -> list:
    """Alle database-containere, der er oprettet af panelet eller af scriptet fra 'Tilføj node' (label dof.dbnode)."""
    return cli.containers.list(all=True, filters={"label": "dof.dbnode"})


def connect(cli, container, network: str) -> bool:
    """Læg containeren i panelets netværk, hvis den ikke allerede er der. Returnerer True, hvis den blev tilsluttet."""
    container.reload()
    if network in container.attrs["NetworkSettings"]["Networks"]:
        return False
    cli.networks.get(network).connect(container)
    return True


def env_of(container) -> dict[str, str]:
    return dict(e.split("=", 1) for e in container.attrs["Config"].get("Env") or [] if "=" in e)
