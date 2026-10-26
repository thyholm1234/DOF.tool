"""Byggeagent til talemodellen: venter på kommandoer i Valkey (listen speech:cmd) og bygger modellen, så Admin → Drift kan
starte og følge byggeriet uden at workeren skal have PyTorch eller adgang til Docker.

Kommando: {"cmd": "build", "force": false}. Uden force springes byggeriet over, hvis /models allerede har en model bygget af
samme revision af CoRal-modellen (export.py). Status skrives i samme format som workerens opgaver (worker:jobs / worker:job_log),
så den vises i Drift sammen med de øvrige opgaver, og en pulsslag-nøgle (speech:agent) viser, at agenten kører."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import redis

MODELS = os.environ.get("MODELS_DIR", "/data/models")
WORK = os.environ.get("WORK_DIR", "/work")
JOB, LABEL = "speech_model", "Talemodel (CoRal) til tale-indtastning"
r = redis.from_url(os.environ.get("REDIS_DSN", "redis://valkey:6379/0"), decode_responses=True)
now = lambda: datetime.now(ZoneInfo("Europe/Copenhagen")).isoformat()
REV = re.search(r'"ROEST_REVISION", "([0-9a-f]{40})"', open("/opt/export.py").read()).group(1)


def put(**job):
    r.hset("worker:jobs", JOB, json.dumps({"name": JOB, "label": LABEL, **job}, ensure_ascii=False))


def current() -> dict:
    raw = r.hget("worker:jobs", JOB)
    return json.loads(raw) if raw else {}


def manifest() -> dict | None:
    try:
        return json.load(open(f"{MODELS}/speech.json"))
    except (OSError, ValueError):
        return None


def finish(status: str, t0: float, started: str, error: str | None = None, step: str = "") -> None:
    end = {"status": status, "started_at": started, "finished_at": now(), "duration": round(time.time() - t0, 1), "error": error}
    put(**end, step=step, last=end)
    r.lpush("worker:job_log", json.dumps({"name": JOB, "label": LABEL, **end}, ensure_ascii=False))
    r.ltrim("worker:job_log", 0, 49)


NEED_MB = int(os.environ.get("NEED_MB", "6500"))  # målt toppen under eksporten: 5,7 GB (ONNX-sporing af 315M parametre i fp32) plus lidt luft


def available_mb() -> int:
    """Hukommelse byggeriet kan få: det mindste af containerens grænse og værtens ledige hukommelse (inkl. swap)."""
    meminfo = {k: int(v.split()[0]) for k, v in (l.split(":", 1) for l in open("/proc/meminfo"))}
    avail = (meminfo["MemAvailable"] + meminfo.get("SwapFree", 0)) // 1024
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            lim = open(path).read().strip()
            if lim.isdigit() and int(lim) < 1 << 50:
                avail = min(avail, int(lim) // (1024 * 1024))
        except OSError:
            continue
    return avail


def build(force: bool) -> None:
    t0, started = time.time(), now()
    put(status="running", started_at=started, step="Starter", last=current().get("last"))
    m = manifest()
    if not force and m and m.get("revision") == REV and os.path.exists(f"{MODELS}/{m['url'].rsplit('/', 1)[-1]}"):
        finish("done", t0, started, step=f"Uændret ({m['version']})")
        return
    free = available_mb()
    if free < NEED_MB:
        finish("error", t0, started, f"For lidt hukommelse: byggeriet kræver ca. {NEED_MB / 1000:.1f} GB fri hukommelse, men der er {free / 1000:.1f} GB "
               "(inkl. swap). Luk andet, tilføj swap eller hæv mem_limit for speech-model i docker-compose.yml.")
        return
    out = f"{WORK}/out"
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    env = {**os.environ, "OUT_DIR": out, "HF_HOME": f"{WORK}/hf", "PYTHONWARNINGS": "ignore"}
    try:
        p = subprocess.Popen([sys.executable, "-u", "/opt/export.py"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        tail = []
        for line in p.stdout:
            line = line.strip()
            if line.startswith("step: "):
                put(status="running", started_at=started, step=line[6:], last=current().get("last"))
            elif line and "Warning" not in line and not line.startswith(("if ", "warnings.warn", "hukommelse efter")):
                tail = (tail + [line])[-6:]
        code = p.wait()
        if code in (-9, 137):  # dræbt af kernen: ude af hukommelse (ingen traceback, kun advarsler før)
            raise RuntimeError("Eksporten blev stoppet, fordi hukommelsen slap op (kræver ca. 6 GB). "
                               "Tilføj swap eller hæv mem_limit for speech-model i docker-compose.yml.")
        if code != 0:
            raise RuntimeError(f"Eksporten fejlede (kode {code}): " + " | ".join(tail)[-400:])
        put(status="running", started_at=started, step="Lægger modellen på serveren", last=current().get("last"))
        q = subprocess.run([sys.executable, "/opt/publish.py", out, MODELS], capture_output=True, text=True)
        if q.returncode:
            raise RuntimeError("Udgivelsen fejlede: " + q.stderr[-400:])
        finish("done", t0, started, step=f"Bygget ({manifest()['version']})")
    except Exception as exc:  # noqa: BLE001 – agenten skal blive ved med at lytte
        finish("error", t0, started, str(exc)[:500])
    finally:
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(f"{WORK}/hf", ignore_errors=True)


def main() -> None:
    print("byggeagent klar", flush=True)
    for stale in (current(),):
        if stale.get("status") in ("running", "queued"):  # afbrudt af en genstart
            put(**{**stale, "status": "interrupted", "finished_at": now()})
    while True:
        r.set("speech:agent", now(), ex=90)
        try:
            item = r.blpop("speech:cmd", timeout=30)
        except redis.RedisError:
            time.sleep(5)
            continue
        if not item:
            continue
        try:
            cmd = json.loads(item[1])
        except ValueError:
            continue
        if cmd.get("cmd") == "build":
            build(bool(cmd.get("force")))


if __name__ == "__main__":
    main()
