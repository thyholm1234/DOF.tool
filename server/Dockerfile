FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Copenhagen
WORKDIR /app/server

COPY server/pyproject.toml ./
RUN mkdir portal && touch portal/__init__.py && pip install . && pip uninstall -y dof-portal

COPY server/ ./
RUN pip install --no-deps -e .
COPY web/ /app/web/

ENV WEB_DIR=/app/web
EXPOSE 8000
CMD ["uvicorn", "portal.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
