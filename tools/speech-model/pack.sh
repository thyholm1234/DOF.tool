#!/usr/bin/env bash
# Pakker den byggede talemodel (fra build.sh eller Admin → Drift) til én fil, der kan kopieres til en anden server:
#
#   tools/speech-model/pack.sh [ud-mappe]        → <ud-mappe>/speech-model.tar + .sha256   (standard: app/dist)
#
# På serveren:  tools/speech-model/install.sh speech-model.tar
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$(mkdir -p "${1:-app/dist}" && cd "${1:-app/dist}" && pwd)
PROJECT=$(docker compose config --format json | python3 -c "import json,sys; print(json.load(sys.stdin)['name'])")
docker run --rm -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -v "${PROJECT}_models:/models:ro" -v "$OUT:/out" alpine:3.21.3 sh -c '
  set -e
  [ -f /models/speech.json ] || { echo "Ingen talemodel bygget endnu (kør build.sh)" >&2; exit 1; }
  mkdir /out/.stage
  cp /models/roest-v3* /out/.stage/
  cp /models/speech.json /out/.stage/speech.json.new
  tar -cf /out/speech-model.tar -C /out/.stage .
  rm -rf /out/.stage; chown "${HOST_UID:-0}:${HOST_GID:-0}" /out/speech-model.tar'
( cd "$OUT" && sha256sum speech-model.tar > speech-model.tar.sha256 )
ls -la "$OUT"/speech-model.tar*
