FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/srv/venv
WORKDIR /srv/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/app ./app
COPY frontend /srv/frontend
# Docker fills a new, empty sitedata volume from the image's /data, so /data/sites exists before Caddy starts:
# Caddy mounts only that subfolder (docker-compose.yml), and Docker refuses to mount a missing one.
RUN useradd --system --uid 10001 --home-dir /srv zerde && mkdir -p /data/sites && chown -R zerde:zerde /data
USER zerde
ENV PATH="/srv/venv/bin:$PATH" DATA_DIR=/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
