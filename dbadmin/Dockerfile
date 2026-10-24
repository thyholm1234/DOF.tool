FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Copenhagen
# Portalens pakke er med, så panelet selv kan oprette skemaer og flytte data (migrering)
WORKDIR /app/server
COPY server/pyproject.toml ./
RUN mkdir portal && touch portal/__init__.py && pip install . "docker>=7" && pip uninstall -y dof-portal
COPY server/ ./
RUN pip install --no-deps -e .
WORKDIR /app/dbadmin
COPY dbadmin/ ./
# Indlejres i opsætningsscriptet til nye noder
COPY dbnode/ /app/dbnode/
VOLUME /data
EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100", "--proxy-headers", "--forwarded-allow-ips=*"]
