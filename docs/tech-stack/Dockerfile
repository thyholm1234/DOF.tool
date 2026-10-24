# Byggeværktøj til dokumentationen: Graphviz (diagram) + WeasyPrint (HTML → PDF). Bruges af build.sh.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends graphviz fonts-dejavu-core libpango-1.0-0 libpangoft2-1.0-0 \
    libharfbuzz-subset0 && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir weasyprint==63.1 pypdf==5.1.0
WORKDIR /work
