#!/usr/bin/env bash
# Ship the repository to the server and (re)start the stack. Needs SSH key access.
#   DEPLOY_HOST=ubuntu@212.116.232.74 ./deploys/deploy.sh
# Exits non-zero unless the app answers /healthz and Caddy serves it on the public address.
set -euo pipefail
HOST="${DEPLOY_HOST:?Set DEPLOY_HOST, e.g. ubuntu@212.116.232.74}"
DIR="${DEPLOY_DIR:-/opt/zerde-deploy}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

rsync -az --delete \
  --exclude .git --exclude .idea --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  --exclude /backend/data --exclude /deploys/.env --exclude /main.py \
  "$ROOT/" "$HOST:$DIR/"

ssh "$HOST" "bash -s -- $(printf '%q' "$DIR")" <<'REMOTE'
# The braces make bash read the whole script before running it. Otherwise `docker compose exec`
# would read the rest of the script from stdin and bash would stop there with status 0.
{
  set -euo pipefail
  cd "$1/deploys"
  test -f .env || { echo "Create $1/deploys/.env from .env.example first" >&2; exit 1; }
  docker compose up -d --build --remove-orphans
  docker compose ps

  retry() {  # retry <tries> <delay-seconds> <command...>
    local tries=$1 delay=$2
    shift 2
    for _ in $(seq "$tries"); do
      "$@" && return 0
      sleep "$delay"
    done
    return 1
  }

  echo "Smoke check: the app answers /healthz"
  healthz='import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2)'
  if ! retry 30 2 docker compose exec -T app python -c "$healthz" >/dev/null 2>&1; then
    echo "FAIL: the app did not answer /healthz within 60 s (is SECRET_KEY set in .env?). Last app log lines:" >&2
    docker compose logs --tail 30 app >&2
    exit 1
  fi

  # Caddy reads its configuration once, at start, through single-file bind mounts. rsync replaces a changed
  # file with a new one while the running container keeps the old one, and `up` does not recreate Caddy for
  # a changed file, so a new Caddyfile, sites.caddy or 404 page would never take effect. Compare what the
  # container sees with the files on disk; on any difference validate the new configuration in a throwaway
  # container, then recreate Caddy (certificates live in the caddy_data volume, open connections drop once).
  caddy_stale() {
    local pair
    for pair in Caddyfile:/etc/caddy/Caddyfile sites.caddy:/etc/caddy/sites.caddy \
      ../frontend/static/site-404.html:/srv/errors/site-404.html; do
      docker compose exec -T caddy cat "${pair#*:}" </dev/null 2>/dev/null | cmp -s - "${pair%%:*}" || return 0
    done
    return 1
  }
  if caddy_stale; then
    echo "Caddy configuration changed: validating it, then recreating the caddy container"
    if ! docker compose run --rm --no-deps -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null; then
      echo "FAIL: the new Caddy configuration is invalid; the running Caddy keeps the old one" >&2
      exit 1
    fi
    docker compose up -d --no-deps --force-recreate caddy
  fi

  url="$(docker compose exec -T app python -c 'from app.config import load_settings; print(load_settings().app_origin)')/healthz"
  echo "Smoke check: $url answers through Caddy"
  if ! retry 20 3 curl -fs -o /dev/null --max-time 10 "$url"; then
    echo "FAIL: $url does not answer (DNS, certificate or Caddy):" >&2
    curl -fsS -o /dev/null --max-time 10 "$url" || true
    echo "Last caddy log lines:" >&2
    docker compose logs --tail 30 caddy >&2
    exit 1
  fi
  echo "OK: the app is healthy and $url answers"
}
REMOTE
