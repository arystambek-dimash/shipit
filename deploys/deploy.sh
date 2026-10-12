#!/usr/bin/env bash
# Ship the repository to the server and (re)start the stack. Needs SSH key access.
#   DEPLOY_HOST=ubuntu@212.116.232.74 ./deploys/deploy.sh
set -euo pipefail
HOST="${DEPLOY_HOST:?Set DEPLOY_HOST, e.g. ubuntu@212.116.232.74}"
DIR="${DEPLOY_DIR:-/opt/zerde-deploy}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

rsync -az --delete \
  --exclude .git --exclude .idea --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  --exclude /backend/data --exclude /deploys/.env --exclude /main.py \
  "$ROOT/" "$HOST:$DIR/"

ssh "$HOST" "cd '$DIR/deploys' \
  && { test -f .env || { echo 'Create $DIR/deploys/.env from .env.example first'; exit 1; }; } \
  && docker compose up -d --build --remove-orphans \
  && docker compose ps"
