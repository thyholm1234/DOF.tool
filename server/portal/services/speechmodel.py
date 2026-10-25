"""Talemodellen til appens tale-indtastning (CoRal, ONNX int8): status og kommandoer til byggeagenten.

Agenten (tools/speech-model/agent.py, compose-servicen speech-model) bygger modellen på kommando og lægger den i
models_dir sammen med manifestet speech.json, som serveren leverer under /models/ og appen læser. Workeren starter
byggeriet efter planen (services/schedule.py), og Admin → Drift kan starte det og vise status."""

from __future__ import annotations

import json
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


async def status() -> dict:
    m = manifest()
    beat = await kv().get(HEARTBEAT)
    return {"manifest": m, "agent": beat, "agent_alive": bool(beat), "pending": await kv().llen(COMMANDS)}
