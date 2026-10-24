FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Copenhagen
WORKDIR /app
RUN pip install "fastapi>=0.115" "uvicorn[standard]>=0.32" "httpx>=0.28" "asyncpg>=0.30" "redis>=5.2"
COPY dbadmin/ ./
VOLUME /data
EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100", "--proxy-headers", "--forwarded-allow-ips=*"]
