FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Copenhagen
# Sikkerhedsrettelser fra Debian, før basisimaget selv er genbygget
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
# Portalens pakke er med, så panelet selv kan oprette skemaer og flytte data (migrering)
WORKDIR /app/server
COPY server/pyproject.toml server/constraints.txt ./
RUN mkdir portal && touch portal/__init__.py && pip install -c constraints.txt . "docker>=7" && pip uninstall -y dof-portal
COPY server/ ./
RUN pip install --no-deps -e .
WORKDIR /app/dbadmin
COPY dbadmin/ ./
# Indlejres i opsætningsscriptet til nye noder
COPY dbnode/ /app/dbnode/
VOLUME /data
# X-Forwarded-For/-Proto bruges kun fra proxyen (10.0.1.1); ellers kan klienten selv skrive sin IP
ENV FORWARDED_ALLOW_IPS=10.0.1.1
EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100", "--proxy-headers"]
