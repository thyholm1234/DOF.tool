FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Copenhagen
# pg_dump til backup – samme hovedversion som databaserne (17), fra PostgreSQL' eget apt-arkiv
# apt-get upgrade: sikkerhedsrettelser fra Debian, før basisimaget selv er genbygget (fx zlib CVE-2026-85091)
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends postgresql-common ca-certificates \
    && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
    && apt-get install -y --no-install-recommends postgresql-client-17 poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/server

COPY server/pyproject.toml server/constraints.txt ./
# Afhængighederne installeres med de låste versioner (constraints.txt), så samme commit giver samme image
RUN mkdir portal && touch portal/__init__.py && pip install -c constraints.txt . && pip uninstall -y dof-portal

COPY server/ ./
RUN pip install --no-deps -e .
COPY web/ /app/web/

ENV WEB_DIR=/app/web
# X-Forwarded-For/-Proto bruges kun fra proxyen (10.0.1.1); ellers kan klienten selv skrive sin IP
ENV FORWARDED_ALLOW_IPS=10.0.1.1
# api og worker kører ikke som root: et hul i appen giver så kun adgang som «app» (uid 1000) – ikke til at ændre
# kode/pakker i imaget. Skrivbare mapper (/data/*) ejes af app via data-perms-init i docker-compose.yml.
RUN useradd --uid 1000 --user-group --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/media /data/weather /data/models /data/archive-upload /data/article-imports /data/photo-imports \
    && chown -R app:app /data
USER app
EXPOSE 8000
CMD ["uvicorn", "portal.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
