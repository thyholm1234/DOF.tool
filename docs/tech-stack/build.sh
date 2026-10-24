#!/bin/sh
# Byg docs/DOF.tool-tech-stack.pdf: diagram og tabel fra gen.py, layout med Graphviz, PDF med WeasyPrint (i Docker).
set -eu
cd "$(dirname "$0")"
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
docker build -q -t doftool-docs . >/dev/null
docker run --rm -v "$PWD/..:/docs" -w /docs/tech-stack doftool-docs sh -c "
  python gen.py && dot -Tsvg flow.dot -o _flow.svg && python render.py $COMMIT ../DOF.tool-tech-stack.pdf
  rm -f _flow.svg _preview.png; chown $(id -u):$(id -g) flow.dot edges.html ../DOF.tool-tech-stack.pdf"
