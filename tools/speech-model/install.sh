#!/usr/bin/env bash
# Lægger en færdigbygget talemodel (speech-model.tar fra pack.sh) i serverens volumen «models», så /models/ leverer den og
# appen kan hente den. Kør på serveren, hvor docker compose-projektet ligger:
#
#   tools/speech-model/install.sh /sti/til/speech-model.tar
#
# Intet byggeri og ingen Hugging Face-adgang på serveren. Kan køres igen (en ny model erstatter den gamle).
set -euo pipefail
TAR=${1:?angiv speech-model.tar}
cd "$(dirname "$0")/../.."
PROJECT=$(docker compose config --format json | python3 -c "import json,sys; print(json.load(sys.stdin)['name'])")
VOLUME="${PROJECT}_models"
( cd "$(dirname "$TAR")" && sha256sum -c "$(basename "$TAR").sha256" )
docker volume create "$VOLUME" >/dev/null
docker run --rm -v "$VOLUME:/models" -v "$(cd "$(dirname "$TAR")" && pwd)/$(basename "$TAR"):/in/model.tar:ro" alpine:3.21.3 sh -c '
  set -e
  tar -xf /in/model.tar -C /models
  # manifestet er pakket som speech.json.new og flyttes til sidst: først nu tilbydes den nye model
  mv /models/speech.json.new /models/speech.json
  for f in /models/roest-v3-int8-*.onnx; do
    [ "$f" = "/models/$(sed -n "s/.*\"url\": \"\/models\/\(.*\)\",/\1/p" /models/speech.json)" ] || rm -f "$f"
  done
  ls -la /models'
echo "Færdig: talemodellen ligger i $VOLUME. Åbn en tur i appen for at hente den."
