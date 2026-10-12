# zerde/deploy

Drag-and-drop static site hosting for zerde.ai: allowlisted people sign in (Google or an email code) and publish a folder, `.zip` or `.html` file to `https://<name>.deploy.zerde.ai`. UI in English, Russian and Kazakh.

- `frontend/` — templates, CSS, JS, translations
- `backend/` — FastAPI app and tests
- `deploys/` — Docker, Caddy, scripts and [server setup](deploys/server-setup.md)

## Develop

```bash
cd backend
uv sync
ADMIN_EMAILS=you@example.com uv run uvicorn app.main:create_app --factory --reload --port 8000
```

Open http://localhost:8000 — sign-in codes are printed in the terminal until SMTP is configured. Deployed sites open at `http://<name>.localhost:8000`.

## Test

```bash
cd backend && uv run pytest -q
```

## Deploy

See [deploys/server-setup.md](deploys/server-setup.md), then `DEPLOY_HOST=ubuntu@212.116.232.74 ./deploys/deploy.sh`.
