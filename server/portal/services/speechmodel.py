"""Talemodellen til appens tale-indtastning (CoRal, ONNX int8): status og kommandoer til byggeagenten.

Agenten (tools/speech-model/agent.py, compose-servicen speech-model) bygger modellen på kommando og lægger den i
models_dir sammen med manifestet speech.json, som serveren leverer under /models/ og appen læser. Workeren starter
byggeriet efter planen (services/schedule.py), og Admin → Drift kan starte det og vise status."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
from pathlib import Path

from portal import jobstatus
from portal.cache import kv
from portal.config import get_settings

COMMANDS = "speech:cmd"
HEARTBEAT = "speech:agent"
JOB = "speech_model"


def manifest() -> dict | None:
    try:
        return json.loads((Path(get_settings().models_dir) / "speech.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def request(force: bool = False) -> None:
    """Bed agenten om at bygge modellen (uden force springes det over, hvis den allerede er bygget af samme revision)."""
    await kv().rpush(COMMANDS, json.dumps({"cmd": "build", "force": force}))
    await jobstatus.queued(JOB)


MODEL_RE = re.compile(r"^roest-v3-int8-[0-9a-f]{12}\.onnx$")
MEMBERS = ("roest-v3-vocab.json", "speech.json.new")
MAX_BYTES = 1_000_000_000
MAX_UPLOAD = 600_000_000  # selve arkivet (modellen er ca. 355 MB)


def install(tar_path: Path) -> dict:
    """Læg en færdigbygget model (speech-model.tar fra tools/speech-model/pack.sh) i models_dir og skift manifestet til sidst.

    Arkivet pakkes ud fil for fil, kun de forventede navne og kun almindelige filer, og modellens SHA-256 skal passe til
    manifestet, før noget tages i brug. Rejser ValueError med en forklaring, hvis arkivet ikke er en talemodel."""
    root = Path(get_settings().models_dir)
    root.mkdir(parents=True, exist_ok=True)
    stage = root / ".upload"
    for old in stage.glob("*") if stage.is_dir() else []:
        old.unlink()
    stage.mkdir(exist_ok=True)
    try:
        try:
            tf = tarfile.open(tar_path)
        except tarfile.TarError as exc:
            raise ValueError("Filen er ikke et tar-arkiv (brug speech-model.tar fra pack.sh)") from exc
        with tf:
            names = []
            for m in tf:
                base = os.path.basename(m.name)
                if base in ("", ".") and m.isdir():
                    continue
                if not m.isfile() or m.name.lstrip("./") != base or not (MODEL_RE.match(base) or base in MEMBERS) or m.size > MAX_BYTES:
                    raise ValueError(f"Uventet indhold i arkivet: {m.name}")
                src = tf.extractfile(m)
                with (stage / base).open("wb") as out:
                    while chunk := src.read(1 << 20):
                        out.write(chunk)
                names.append(base)
        model = [n for n in names if MODEL_RE.match(n)]
        if len(model) != 1 or not {"speech.json.new", "roest-v3-vocab.json"} <= set(names):
            raise ValueError("Arkivet skal indeholde modellen, roest-v3-vocab.json og speech.json.new")
        try:
            m = json.loads((stage / "speech.json.new").read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError("Manifestet i arkivet kan ikke læses") from exc
        if m.get("url") != f"/models/{model[0]}" or m.get("size") != (stage / model[0]).stat().st_size:
            raise ValueError("Manifestet passer ikke til modelfilen")
        h = hashlib.sha256()
        with (stage / model[0]).open("rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        if h.hexdigest() != m.get("sha256"):
            raise ValueError("Kontrolsummen passer ikke – filen er beskadiget eller ikke færdigsendt")
        os.replace(stage / model[0], root / model[0])
        os.replace(stage / "roest-v3-vocab.json", root / "roest-v3-vocab.json")
        os.replace(stage / "speech.json.new", root / "speech.json")  # manifestet sidst: først nu tilbydes den nye model
        for old in root.glob("roest-v3-int8-*.onnx"):
            if old.name != model[0]:
                old.unlink()
        return m
    finally:
        for left in stage.glob("*"):
            left.unlink(missing_ok=True)
        stage.rmdir()


async def status() -> dict:
    m = manifest()
    beat = await kv().get(HEARTBEAT)
    return {"manifest": m, "agent": beat, "agent_alive": bool(beat), "pending": await kv().llen(COMMANDS)}
