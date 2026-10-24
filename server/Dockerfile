FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Copenhagen
WORKDIR /app/server

COPY server/pyproject.toml ./
RUN mkdir portal && touch portal/__init__.py && pip install . && pip uninstall -y dof-portal

COPY server/ ./
RUN pip install --no-deps -e .
COPY web/ /app/web/

ENV WEB_DIR=/app/web
# X-Forwarded-For/-Proto bruges kun fra disse adresser (proxyen); ellers kan klienten selv skrive sin IP
ENV FORWARDED_ALLOW_IPS=127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
EXPOSE 8000
CMD ["uvicorn", "portal.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
