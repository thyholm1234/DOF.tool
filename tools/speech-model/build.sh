#!/usr/bin/env bash
# Bygger talemodellen til appens tale-indtastning og lægger den, hvor serveren leverer den (/models/):
#
#   tools/speech-model/build.sh
#
# CoRal-projektets roest-v3-wav2vec2-315m hentes fra Hugging Face, eksporteres til ONNX og kvantiseres til int8
# (export.py). Resultatet lægges i Docker-volumen <projekt>_models med et manifest (speech.json: version, url, størrelse,
# SHA-256), som appen læser, før den henter modellen til telefonen. Kører én gang (ca. 5 min, 1,3 GB hentes); igen kun
# når modellen skal opdateres. Telefonerne tilbydes den nye version, næste gang en tur åbnes.
#
# Afprøvning på syntetisk dansk tale (Piper):  tools/speech-model/build.sh --evaluate
set -euo pipefail
cd "$(dirname "$0")"
PROJECT=$(docker compose -f ../../docker-compose.yml config --format json | python3 -c "import json,sys; print(json.load(sys.stdin)['name'])")
VOLUME="${PROJECT}_models"
WORK=$(mktemp -d -p "${TMPDIR:-/var/tmp}")  # ca. 3 GB undervejs
trap 'docker run --rm -v "$WORK:/work" alpine:3.21.3 rm -rf /work/out /work/hf >/dev/null 2>&1; rm -rf "$WORK"' EXIT

docker build -q -t doftool-speech-model . >/dev/null
docker volume create "$VOLUME" >/dev/null
docker run --rm -v "$WORK:/work" -e HF_HOME=/work/hf doftool-speech-model python /opt/export.py

if [ "${1:-}" = "--evaluate" ]; then
  mkdir -p "$WORK/voice"
  for f in da_DK-talesyntese-medium.onnx da_DK-talesyntese-medium.onnx.json; do
    curl -sSL -o "$WORK/voice/$f" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/da/da_DK/talesyntese/medium/$f"
  done
  docker run --rm -v "$WORK:/work" doftool-speech-model python /opt/evaluate.py out/roest-v3-fp32.onnx out/roest-v3-int8.onnx
fi

docker run --rm -v "$WORK:/work" -v "$VOLUME:/models" doftool-speech-model python /opt/publish.py /work/out /models
