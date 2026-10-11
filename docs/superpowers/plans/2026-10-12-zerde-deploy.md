# Zerde Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A light, self-hosted "Netlify Drop" at `https://deploy.zerde.ai`: allowlisted people sign in (Google or email code) and drag-and-drop static sites that go live at `https://<name>.deploy.zerde.ai`, UI in English/Russian/Kazakh.

**Architecture:** One FastAPI service (server-rendered Jinja2 pages, ~6 KB vanilla JS, SQLite) handles the dashboard, auth and uploads. Uploaded sites are written as immutable deploy trees with an atomically swapped `current` symlink; in production Caddy serves them straight from disk with on-demand TLS, so visitor traffic never touches Python. Development serves sites from the app itself at `http://<name>.localhost:8000`.

**Tech Stack:** Python ≥3.11 (Docker: 3.12), FastAPI, uvicorn, Jinja2, python-multipart, PyJWT[crypto], SQLite (WAL), pytest + httpx, uv; Caddy 2; Docker Compose; IBM Plex Sans (self-hosted WOFF2).

**Spec:** `docs/superpowers/specs/2026-10-12-zerde-deploy-design.md`

## Global Constraints

- Repository layout: `frontend/` (templates, static, i18n), `backend/` (FastAPI app + tests), `deploys/` (Docker, Caddy, scripts, server docs). Runtime data lives in `DATA_DIR` (default `backend/data/` in dev, `/data` in Docker) and is never committed.
- Backend dependencies are exactly: `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`, `pyjwt[crypto]`. Dev: `pytest`, `httpx`. Do not add others.
- No frontend framework, no build step, no CDN at runtime except the Google Identity Services script on `/login` when `GOOGLE_CLIENT_ID` is set.
- Weight budget (gzip): `app.css` ≤ 12 KB, `app.js` ≤ 8 KB, dashboard HTML ≤ 15 KB, all `.woff2` files together ≤ 100 KB.
- Languages `en`, `ru`, `kk` (Kazakh Cyrillic). Every user-visible string comes from `frontend/i18n/<lang>.json`; all three files have identical keys and identical `{placeholder}` sets.
- Visual rules: no gradients, glassmorphism, glow, emoji icons, marketing hero, card grids, hype copy. 1 px borders, one accent colour, inline SVG icons only.
- Sessions: cookie `__Host-zd_session` (HTTPS) / `zd_session` (HTTP dev), HttpOnly, SameSite=Lax, 30-day fixed expiry, only SHA-256 of the token stored.
- Every POST/PATCH/DELETE must carry `Origin` (or `Referer`) equal to the app origin, else 403.
- Upload limits: 50 MB per upload, 2000 files, 25 MB per file, zip uncompressed ≤ 200 MB, zip-bomb ratio guard 100× (entries > 1 MB).
- Site names: `^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$`, no `--`, not reserved. Keep the newest 10 deploys per site.
- Email codes: 6 digits, 10-minute TTL, 5 attempts, 1 send/60 s and 5/hour per email, 20/hour per IP; stored as HMAC-SHA256.
- Commit after every task with a conventional message ending in `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- All commands run from the repository root `/Users/dimash/PycharmProjects/biolab` unless a `cd` is shown. Tests: `cd backend && uv run pytest -q`.

## File Map

```
backend/pyproject.toml, backend/uv.lock
backend/app/__init__.py
backend/app/config.py          Settings + load_settings()
backend/app/db.py              connect(), init_db(), transaction(), now()
backend/app/users.py           get_user(), upsert_user()
backend/app/i18n.py            LANGS, t(), negotiate(), format_size(), format_ago()
backend/app/access.py          normalize_email/pattern, is_admin, can_deploy, allowlist CRUD
backend/app/signing.py         sign()/unsign() for short-lived cookies
backend/app/auth/__init__.py
backend/app/auth/sessions.py   create/lookup/delete sessions, cookie helpers, origin_allowed()
backend/app/auth/mailer.py     Message, ConsoleMailer, SmtpMailer, make_mailer()
backend/app/auth/email_code.py issue_code(), verify_code(), seconds_until_resend(), RateLimited
backend/app/auth/google.py     GoogleVerifier, GoogleIdentity, GoogleAuthError
backend/app/deploys/__init__.py
backend/app/deploys/names.py   is_valid_name(), normalize_name(), generate_name(), RESERVED
backend/app/deploys/ingest.py  Limits, UploadError, StagedTree, clean_path(), stage_files(), stage_zip(), precompress()
backend/app/deploys/store.py   SiteStore (atomic publish/activate/prune/rename/delete/lock)
backend/app/deploys/service.py publish(), restore(), rename(), delete(), list_sites(), list_deploys(), SiteError
backend/app/deps.py            get_conn, current_user, require_user/deployer/admin, NotSignedIn/Forbidden/NotFound
backend/app/web.py             render(), redirect(), set_flash(), safe_next(), wants_json()
backend/app/devsites.py        dev-only site file serving
backend/app/main.py            create_app() factory, middleware, error handlers
backend/app/routes/__init__.py
backend/app/routes/auth.py     /login, /login/code, /auth/*
backend/app/routes/pages.py    /
backend/app/routes/sites.py    /deploy, /sites/{name}/*
backend/app/routes/admin.py    /admin/*
backend/app/routes/internal.py /internal/tls-ask
backend/tests/...              one test module per unit + route modules
frontend/i18n/en.json ru.json kk.json
frontend/templates/base.html error.html login.html code.html noaccess.html dashboard.html site.html admin.html _drop.html _icons.html
frontend/static/css/app.css fonts.css   frontend/static/js/app.js
frontend/static/fonts/*.woff2 LICENSE.txt   frontend/static/favicon.svg site-404.html
frontend/tools/fetch_fonts.py
deploys/Dockerfile docker-compose.yml docker-compose.local.yml Caddyfile Caddyfile.local sites.caddy
deploys/.env.example deploy.sh backup.sh smoke_local.sh server-setup.md
```

---

### Task 1: Backend scaffold, settings, health check

**Files:**
- Create: `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/config.py`, `backend/app/main.py`
- Create: `backend/tests/__init__.py`, `backend/tests/conftest.py`, `backend/tests/test_config.py`, `backend/tests/test_health.py`
- Create: `backend/uv.lock` (generated)

**Interfaces:**
- Produces: `app.config.Settings` (frozen dataclass; fields `app_domain, sites_domain, https, secret_key, google_client_id, admin_emails: frozenset[str], smtp_host, smtp_port, smtp_user, smtp_password, smtp_from, smtp_starttls, data_dir: Path, serve_sites_in_app, max_upload_mb, max_files, max_file_mb`; properties `scheme, app_origin, db_path, sites_dir, tmp_dir, session_cookie`; method `site_url(name) -> str`), `load_settings(env: Mapping[str,str] | None = None) -> Settings`, `read_dotenv(path) -> dict`, constants `ROOT_DIR, FRONTEND_DIR, DEV_SECRET, MB`.
- Produces: `app.main.create_app(settings: Settings | None = None, *, mailer=None, google=None) -> FastAPI` (stores args on `app.state`).

- [ ] **Step 1: Create the project file**

`backend/pyproject.toml`:
```toml
[project]
name = "zerde-deploy"
version = "0.1.0"
description = "Drag-and-drop static site hosting for zerde.ai"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "jinja2>=3.1",
  "python-multipart>=0.0.9",
  "pyjwt[crypto]>=2.9",
]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]

[tool.uv]
package = false

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

Create empty files `backend/app/__init__.py` and `backend/tests/__init__.py`.

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_config.py`:
```python
import pytest

from app.config import load_settings, read_dotenv


def test_defaults_are_local_development():
    s = load_settings({})
    assert s.app_origin == "http://localhost:8000"
    assert s.session_cookie == "zd_session"
    assert s.serve_sites_in_app is True
    assert s.site_url("demo") == "http://demo.localhost:8000/"
    assert s.db_path.name == "app.db"


def test_production_values():
    s = load_settings(
        {
            "APP_DOMAIN": "deploy.zerde.ai",
            "SITES_DOMAIN": "deploy.zerde.ai",
            "HTTPS": "true",
            "SECRET_KEY": "x" * 40,
            "ADMIN_EMAILS": " A@Zerde.ai, b@x.com ,",
            "GOOGLE_CLIENT_ID": " abc.apps.googleusercontent.com ",
        }
    )
    assert s.app_origin == "https://deploy.zerde.ai"
    assert s.session_cookie == "__Host-zd_session"
    assert s.serve_sites_in_app is False
    assert s.admin_emails == frozenset({"a@zerde.ai", "b@x.com"})
    assert s.google_client_id == "abc.apps.googleusercontent.com"
    assert s.site_url("demo") == "https://demo.deploy.zerde.ai/"


def test_https_requires_a_real_secret():
    with pytest.raises(ValueError):
        load_settings({"HTTPS": "true"})


def test_read_dotenv(tmp_path):
    path = tmp_path / ".env"
    path.write_text('# comment\nAPP_DOMAIN="x.test"\nEMPTY=\n\nNOEQUALS\n', encoding="utf-8")
    assert read_dotenv(path) == {"APP_DOMAIN": "x.test", "EMPTY": ""}
    assert read_dotenv(tmp_path / "missing") == {}
```

`backend/tests/conftest.py` (grows in Task 10):
```python
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

APP = "http://localhost:8000"


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=APP) as c:
        c.headers["origin"] = APP
        yield c
```

`backend/tests/test_health.py`:
```python
def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"
```

- [ ] **Step 3: Install and run the tests to verify they fail**

Run: `cd backend && uv sync && uv run pytest -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 4: Implement settings and the app factory**

`backend/app/config.py`:
```python
"""Settings from environment variables, with an optional .env file for development."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"
DEV_SECRET = "dev-insecure-secret-change-me"
MB = 1024 * 1024


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    app_domain: str = "localhost:8000"
    sites_domain: str = "localhost:8000"
    https: bool = False
    secret_key: str = DEV_SECRET
    google_client_id: str = ""
    admin_emails: frozenset[str] = frozenset()
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    data_dir: Path = Path("data")
    serve_sites_in_app: bool = True
    max_upload_mb: int = 50
    max_files: int = 2000
    max_file_mb: int = 25

    @property
    def scheme(self) -> str:
        return "https" if self.https else "http"

    @property
    def app_origin(self) -> str:
        return f"{self.scheme}://{self.app_domain}"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def sites_dir(self) -> Path:
        return self.data_dir / "sites"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def session_cookie(self) -> str:
        return "__Host-zd_session" if self.https else "zd_session"

    def site_url(self, name: str) -> str:
        return f"{self.scheme}://{name}.{self.sites_domain}/"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        env = {**read_dotenv(Path(os.environ.get("ZD_ENV_FILE", ".env"))), **os.environ}
    get = env.get
    https = _bool(get("HTTPS"), False)
    app_domain = get("APP_DOMAIN") or "localhost:8000"
    settings = Settings(
        app_domain=app_domain,
        sites_domain=get("SITES_DOMAIN") or app_domain,
        https=https,
        secret_key=get("SECRET_KEY") or DEV_SECRET,
        google_client_id=(get("GOOGLE_CLIENT_ID") or "").strip(),
        admin_emails=frozenset(
            e.strip().lower() for e in (get("ADMIN_EMAILS") or "").split(",") if e.strip()
        ),
        smtp_host=(get("SMTP_HOST") or "").strip(),
        smtp_port=int(get("SMTP_PORT") or 587),
        smtp_user=get("SMTP_USER") or "",
        smtp_password=get("SMTP_PASSWORD") or "",
        smtp_from=get("SMTP_FROM") or get("SMTP_USER") or "",
        smtp_starttls=_bool(get("SMTP_STARTTLS"), True),
        data_dir=Path(get("DATA_DIR") or "data"),
        serve_sites_in_app=_bool(get("SERVE_SITES_IN_APP"), not https),
        max_upload_mb=int(get("MAX_UPLOAD_MB") or 50),
        max_files=int(get("MAX_FILES") or 2000),
        max_file_mb=int(get("MAX_FILE_MB") or 25),
    )
    if settings.https and settings.secret_key == DEV_SECRET:
        raise ValueError("SECRET_KEY must be set when HTTPS=true")
    return settings
```

`backend/app/main.py` (replaced by the full version in Task 10):
```python
"""Application factory. Run with: uvicorn app.main:create_app --factory"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from .config import Settings, load_settings


def create_app(settings: Settings | None = None, *, mailer=None, google=None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.mailer = mailer
    app.state.google = google

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    return app
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest -q`
Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app backend/tests
git commit -m "feat(backend): scaffold FastAPI app with settings and health check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Database and users

**Files:**
- Create: `backend/app/db.py`, `backend/app/users.py`
- Test: `backend/tests/test_db_users.py`

**Interfaces:**
- Consumes: nothing beyond stdlib.
- Produces: `db.now() -> int`, `db.connect(path: Path) -> sqlite3.Connection` (autocommit, `Row` factory, WAL, foreign keys), `db.init_db(conn)`, `db.transaction(conn)` context manager (`BEGIN IMMEDIATE` … `COMMIT`/`ROLLBACK`; never nest). Modules must call `db.now()` through the module (tests monkeypatch `app.db.now`).
- Produces: `users.get_user(conn, user_id) -> Row | None`, `users.upsert_user(conn, email, *, name="", avatar_url="", google_sub=None, now=None) -> Row`.
- Tables: `users(id, email UNIQUE, name, avatar_url, google_sub UNIQUE NULL, created_at, last_login_at)`, `allowlist(id, pattern UNIQUE, added_by, created_at)`, `sessions(token_hash PK, user_id, created_at, expires_at)`, `login_codes(id, email, code_hash, ip, created_at, expires_at, attempts, consumed_at)`, `sites(id, name UNIQUE, owner_id, current_deploy_id, created_at, updated_at)`, `deploys(id, site_id, created_by, file_count, total_bytes, created_at)`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_db_users.py`:
```python
import sqlite3

import pytest

from app import db, users


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "x" / "app.db")
    db.init_db(c)
    yield c
    c.close()


def test_connect_uses_wal_and_foreign_keys(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_init_db_is_idempotent(conn):
    db.init_db(conn)
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "allowlist", "sessions", "login_codes", "sites", "deploys"} <= tables


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            conn.execute("INSERT INTO allowlist (pattern, added_by, created_at) VALUES ('a@b.co', 'x', 1)")
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM allowlist").fetchone()[0] == 0


def test_upsert_creates_then_updates(conn):
    user = users.upsert_user(conn, "  Ann@Example.COM ", now=100)
    assert user["email"] == "ann@example.com"
    assert user["created_at"] == 100 and user["last_login_at"] == 100
    again = users.upsert_user(conn, "ann@example.com", name="Ann", now=200)
    assert again["id"] == user["id"]
    assert again["name"] == "Ann" and again["last_login_at"] == 200 and again["created_at"] == 100


def test_upsert_links_google_account_to_email_user(conn):
    user = users.upsert_user(conn, "ann@example.com", now=100)
    linked = users.upsert_user(conn, "ann@example.com", google_sub="g-1", now=150)
    assert linked["id"] == user["id"] and linked["google_sub"] == "g-1"
    # Same Google account, email changed at Google: still the same user.
    same = users.upsert_user(conn, "ann.new@example.com", google_sub="g-1", now=160)
    assert same["id"] == user["id"]


def test_emails_are_unique(conn):
    users.upsert_user(conn, "a@b.co", now=1)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO users (email, created_at, last_login_at) VALUES ('a@b.co', 1, 1)")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_db_users.py -q`
Expected: FAIL — `ImportError: cannot import name 'db'`.

- [ ] **Step 3: Implement**

`backend/app/db.py`:
```python
"""SQLite access: one short-lived connection per request, WAL mode, autocommit by default."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  avatar_url TEXT NOT NULL DEFAULT '',
  google_sub TEXT UNIQUE,
  created_at INTEGER NOT NULL,
  last_login_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS allowlist (
  id INTEGER PRIMARY KEY,
  pattern TEXT NOT NULL UNIQUE,
  added_by TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_codes (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  ip TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  consumed_at INTEGER
);
CREATE INDEX IF NOT EXISTS login_codes_email ON login_codes (email, created_at);
CREATE INDEX IF NOT EXISTS login_codes_ip ON login_codes (ip, created_at);
CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  current_deploy_id INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sites_owner ON sites (owner_id);
CREATE TABLE IF NOT EXISTS deploys (
  id INTEGER PRIMARY KEY,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  created_by INTEGER NOT NULL REFERENCES users(id),
  file_count INTEGER NOT NULL,
  total_bytes INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS deploys_site ON deploys (site_id, id);
"""


def now() -> int:
    return int(time.time())


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
```

`backend/app/users.py`:
```python
"""User records. A user is identified by email; a Google account links via google_sub."""
from __future__ import annotations

import sqlite3

from . import db


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def upsert_user(
    conn: sqlite3.Connection,
    email: str,
    *,
    name: str = "",
    avatar_url: str = "",
    google_sub: str | None = None,
    now: int | None = None,
) -> sqlite3.Row:
    ts = db.now() if now is None else now
    email = email.strip().lower()
    with db.transaction(conn):
        row = None
        if google_sub:
            row = conn.execute("SELECT id FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        if row is None:
            row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            user_id = conn.execute(
                "INSERT INTO users (email, name, avatar_url, google_sub, created_at, last_login_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (email, name, avatar_url, google_sub, ts, ts),
            ).lastrowid
        else:
            user_id = row["id"]
            conn.execute(
                "UPDATE users SET"
                " name = CASE WHEN ? != '' THEN ? ELSE name END,"
                " avatar_url = CASE WHEN ? != '' THEN ? ELSE avatar_url END,"
                " google_sub = COALESCE(google_sub, ?),"
                " last_login_at = ?"
                " WHERE id = ?",
                (name, name, avatar_url, avatar_url, google_sub, ts, user_id),
            )
    user = get_user(conn, user_id)
    assert user is not None
    return user
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass (11).

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/app/users.py backend/tests/test_db_users.py
git commit -m "feat(backend): SQLite schema, transactions and user records

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Translations (EN / RU / KK)

**Files:**
- Create: `frontend/i18n/en.json`, `frontend/i18n/ru.json`, `frontend/i18n/kk.json`
- Create: `backend/app/i18n.py`
- Test: `backend/tests/test_i18n.py`

**Interfaces:**
- Produces: `i18n.LANGS = ("en", "ru", "kk")`, `DEFAULT_LANG = "en"`, `LANG_COOKIE = "zd_lang"`, `LANG_LABELS = {"en": "Eng", "ru": "Рус", "kk": "Қаз"}`, `catalog(lang) -> dict[str, str]`, `t(lang, key, **params) -> str` (falls back to English, then the key), `negotiate(query, cookie, accept_language) -> str`, `format_size(lang, n_bytes) -> str`, `format_ago(lang, seconds) -> str`.
- Every later template/route uses only keys defined here.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_i18n.py`:
```python
import re

import pytest

from app import i18n

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_all_languages_have_the_same_keys():
    keys = {lang: set(i18n.catalog(lang)) for lang in i18n.LANGS}
    assert keys["ru"] == keys["en"]
    assert keys["kk"] == keys["en"]


@pytest.mark.parametrize("lang", ["en", "ru", "kk"])
def test_no_empty_strings_and_same_placeholders(lang):
    for key, text in i18n.catalog(lang).items():
        assert text.strip(), f"{lang}:{key} is empty"
        assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(i18n.catalog("en")[key])), key


def test_kazakh_uses_kazakh_letters():
    text = " ".join(i18n.catalog("kk").values())
    for letter in "әғқңөұүі":
        assert letter in text


def test_t_formats_and_falls_back():
    assert i18n.t("en", "time.min", n=5) == "5 min ago"
    assert i18n.t("ru", "time.min", n=5) == "5 мин назад"
    assert i18n.t("kk", "no.such.key") == "no.such.key"


@pytest.mark.parametrize(
    "query,cookie,header,expected",
    [
        ("ru", "kk", "en", "ru"),
        (None, "kk", "ru-RU,ru;q=0.9", "kk"),
        (None, None, "ru-RU,ru;q=0.9,en;q=0.8", "ru"),
        (None, None, "kk-KZ", "kk"),
        (None, None, "kz", "kk"),
        ("de", "xx", "fr-FR", "en"),
        (None, None, None, "en"),
    ],
)
def test_negotiate(query, cookie, header, expected):
    assert i18n.negotiate(query, cookie, header) == expected


def test_format_size():
    assert i18n.format_size("en", 512) == "512 B"
    assert i18n.format_size("en", 1536) == "1.5 KB"
    assert i18n.format_size("ru", 1536) == "1,5 КБ"
    assert i18n.format_size("kk", 25 * 1024 * 1024) == "25 МБ"


def test_format_ago():
    assert i18n.format_ago("en", 10) == "just now"
    assert i18n.format_ago("ru", 125) == "2 мин назад"
    assert i18n.format_ago("kk", 7200) == "2 сағ бұрын"
    assert i18n.format_ago("en", 3 * 86400) == "3 d ago"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_i18n.py -q`
Expected: FAIL — `ImportError: cannot import name 'i18n'`.

- [ ] **Step 3: Write the catalogs**

`frontend/i18n/en.json`:
```json
{
  "nav.skip": "Skip to content",
  "nav.main": "Main",
  "nav.sites": "Sites",
  "nav.admin": "Access",
  "nav.account": "Account",
  "nav.sign_out": "Sign out",
  "lang.label": "Language",
  "login.title": "Sign in",
  "login.lead": "Publish static sites to {domain}.",
  "login.or": "or",
  "login.email_label": "Email",
  "login.email_hint": "We'll send a 6-digit code to this address.",
  "login.email_submit": "Send code",
  "code.title": "Check your email",
  "code.lead": "We sent a 6-digit code to {email}. It works for 10 minutes.",
  "code.label": "Code",
  "code.submit": "Sign in",
  "code.resend": "Send a new code",
  "code.resend_in": "New code in {s} s",
  "code.change_email": "Use another email",
  "code.sent": "We sent a new code.",
  "email.subject": "{code} is your zerde/deploy code",
  "email.body": "Your sign-in code for {domain}:\n\n{code}\n\nIt works for 10 minutes. If you didn't ask for it, you can ignore this email.",
  "noaccess.title": "No access yet",
  "noaccess.body": "You're signed in as {email}, but this account can't publish sites yet. Ask an administrator to add you.",
  "dash.title": "Sites",
  "dash.yours": "Your sites",
  "drop.title": "Drop a folder, .zip or .html file",
  "drop.redeploy_title": "Drop a new version",
  "drop.hint": "Up to {mb} MB and {files} files. index.html must be at the top level.",
  "drop.choose_files": "Choose files",
  "drop.choose_folder": "Choose folder",
  "drop.submit": "Publish",
  "sites.empty": "No sites yet. Drop your first project above.",
  "sites.copy": "Copy link",
  "sites.settings": "Settings",
  "site.back": "All sites",
  "site.history": "Versions",
  "site.current": "Live",
  "site.restore": "Restore",
  "site.files": "Files: {n}",
  "site.rename_title": "Address",
  "site.rename_hint": "Lowercase Latin letters, digits and hyphens, 3–40 characters. The old address stops working.",
  "site.rename_submit": "Save",
  "site.delete_title": "Delete site",
  "site.delete_hint": "The site and all its versions will be removed. Type {name} to confirm.",
  "site.delete_label": "Site name",
  "site.delete_submit": "Delete",
  "admin.title": "Access",
  "admin.lead": "People on this list can publish sites. Add an email, or a whole domain like @zerde.ai.",
  "admin.pattern_label": "Email or @domain",
  "admin.add": "Add",
  "admin.remove": "Remove",
  "admin.empty": "Nobody besides administrators yet.",
  "admin.admins": "Administrators",
  "admin.admins_hint": "Set in the server configuration (ADMIN_EMAILS).",
  "admin.all_sites": "All sites",
  "admin.owner": "Owner",
  "flash.deployed": "Published.",
  "flash.restored": "Version restored.",
  "flash.renamed": "Address changed.",
  "flash.deleted": "Site deleted.",
  "flash.added": "Added.",
  "flash.removed": "Removed.",
  "error.email_invalid": "Enter a valid email address.",
  "error.code_invalid": "This code is wrong or has expired.",
  "error.rate_limited": "Too many attempts. Try again in {s} s.",
  "error.google_failed": "Couldn't sign in with Google. Try again or use email.",
  "error.signed_out": "Your session has ended. Sign in again.",
  "error.not_found": "Page not found",
  "error.forbidden": "You don't have access to this page",
  "error.generic": "Something went wrong. Try again.",
  "error.back_home": "Back to sites",
  "error.name_invalid": "Use 3–40 lowercase Latin letters, digits or hyphens.",
  "error.name_taken": "This address is taken.",
  "error.confirm_mismatch": "The name doesn't match.",
  "error.pattern_invalid": "Enter an email or a domain that starts with @.",
  "error.pattern_exists": "Already on the list.",
  "upload.error.empty": "No files were received.",
  "upload.error.too_big": "The upload is larger than {mb} MB.",
  "upload.error.file_too_big": "{path} is larger than {mb} MB.",
  "upload.error.too_many_files": "Too many files (the limit is {n}).",
  "upload.error.no_index": "There is no index.html at the top level.",
  "upload.error.unsafe_path": "This file name isn't allowed: {path}",
  "upload.error.duplicate_path": "Two files have the same path: {path}",
  "upload.error.bad_zip": "The .zip file can't be read.",
  "js.copied": "Copied",
  "js.reading": "Reading files…",
  "js.uploading": "Uploading {done} of {total}",
  "js.processing": "Publishing…",
  "js.failed": "Upload failed. Check your connection and try again.",
  "js.cancel": "Cancel",
  "unit.b": "B",
  "unit.kb": "KB",
  "unit.mb": "MB",
  "unit.gb": "GB",
  "time.now": "just now",
  "time.min": "{n} min ago",
  "time.hour": "{n} h ago",
  "time.day": "{n} d ago"
}
```

`frontend/i18n/ru.json`:
```json
{
  "nav.skip": "Перейти к содержимому",
  "nav.main": "Основное меню",
  "nav.sites": "Сайты",
  "nav.admin": "Доступ",
  "nav.account": "Аккаунт",
  "nav.sign_out": "Выйти",
  "lang.label": "Язык",
  "login.title": "Вход",
  "login.lead": "Публикация статических сайтов на {domain}.",
  "login.or": "или",
  "login.email_label": "Эл. почта",
  "login.email_hint": "Мы отправим на этот адрес 6-значный код.",
  "login.email_submit": "Получить код",
  "code.title": "Проверьте почту",
  "code.lead": "Мы отправили 6-значный код на {email}. Он действует 10 минут.",
  "code.label": "Код",
  "code.submit": "Войти",
  "code.resend": "Отправить новый код",
  "code.resend_in": "Новый код через {s} с",
  "code.change_email": "Другой адрес",
  "code.sent": "Мы отправили новый код.",
  "email.subject": "{code} — ваш код для zerde/deploy",
  "email.body": "Ваш код для входа на {domain}:\n\n{code}\n\nКод действует 10 минут. Если вы его не запрашивали, просто проигнорируйте это письмо.",
  "noaccess.title": "Доступа пока нет",
  "noaccess.body": "Вы вошли как {email}, но этот аккаунт пока не может публиковать сайты. Попросите администратора добавить вас.",
  "dash.title": "Сайты",
  "dash.yours": "Ваши сайты",
  "drop.title": "Перетащите папку, .zip или .html",
  "drop.redeploy_title": "Перетащите новую версию",
  "drop.hint": "До {mb} МБ и {files} файлов. index.html должен лежать в корне.",
  "drop.choose_files": "Выбрать файлы",
  "drop.choose_folder": "Выбрать папку",
  "drop.submit": "Опубликовать",
  "sites.empty": "Сайтов пока нет. Перетащите первый проект в поле выше.",
  "sites.copy": "Копировать ссылку",
  "sites.settings": "Настройки",
  "site.back": "Все сайты",
  "site.history": "Версии",
  "site.current": "Сейчас",
  "site.restore": "Вернуть",
  "site.files": "Файлов: {n}",
  "site.rename_title": "Адрес",
  "site.rename_hint": "Строчные латинские буквы, цифры и дефис, от 3 до 40 символов. Старый адрес перестанет работать.",
  "site.rename_submit": "Сохранить",
  "site.delete_title": "Удалить сайт",
  "site.delete_hint": "Сайт и все его версии будут удалены. Для подтверждения введите {name}.",
  "site.delete_label": "Имя сайта",
  "site.delete_submit": "Удалить",
  "admin.title": "Доступ",
  "admin.lead": "Люди из этого списка могут публиковать сайты. Добавьте адрес почты или весь домен, например @zerde.ai.",
  "admin.pattern_label": "Почта или @домен",
  "admin.add": "Добавить",
  "admin.remove": "Убрать",
  "admin.empty": "Пока никого, кроме администраторов.",
  "admin.admins": "Администраторы",
  "admin.admins_hint": "Задаются в настройках сервера (ADMIN_EMAILS).",
  "admin.all_sites": "Все сайты",
  "admin.owner": "Владелец",
  "flash.deployed": "Опубликовано.",
  "flash.restored": "Версия восстановлена.",
  "flash.renamed": "Адрес изменён.",
  "flash.deleted": "Сайт удалён.",
  "flash.added": "Добавлено.",
  "flash.removed": "Удалено.",
  "error.email_invalid": "Введите корректный адрес почты.",
  "error.code_invalid": "Код неверный или устарел.",
  "error.rate_limited": "Слишком много попыток. Попробуйте через {s} с.",
  "error.google_failed": "Не получилось войти через Google. Попробуйте ещё раз или войдите по почте.",
  "error.signed_out": "Сеанс завершён. Войдите снова.",
  "error.not_found": "Страница не найдена",
  "error.forbidden": "У вас нет доступа к этой странице",
  "error.generic": "Что-то пошло не так. Попробуйте ещё раз.",
  "error.back_home": "К списку сайтов",
  "error.name_invalid": "Используйте от 3 до 40 строчных латинских букв, цифр или дефисов.",
  "error.name_taken": "Этот адрес уже занят.",
  "error.confirm_mismatch": "Имя не совпадает.",
  "error.pattern_invalid": "Введите адрес почты или домен, начинающийся с @.",
  "error.pattern_exists": "Уже есть в списке.",
  "upload.error.empty": "Файлы не получены.",
  "upload.error.too_big": "Загрузка больше {mb} МБ.",
  "upload.error.file_too_big": "Файл {path} больше {mb} МБ.",
  "upload.error.too_many_files": "Слишком много файлов (максимум {n}).",
  "upload.error.no_index": "В корне нет index.html.",
  "upload.error.unsafe_path": "Недопустимое имя файла: {path}",
  "upload.error.duplicate_path": "Два файла с одинаковым путём: {path}",
  "upload.error.bad_zip": "Не удаётся прочитать .zip-файл.",
  "js.copied": "Скопировано",
  "js.reading": "Читаем файлы…",
  "js.uploading": "Загружено {done} из {total}",
  "js.processing": "Публикуем…",
  "js.failed": "Не удалось загрузить. Проверьте соединение и попробуйте снова.",
  "js.cancel": "Отмена",
  "unit.b": "Б",
  "unit.kb": "КБ",
  "unit.mb": "МБ",
  "unit.gb": "ГБ",
  "time.now": "только что",
  "time.min": "{n} мин назад",
  "time.hour": "{n} ч назад",
  "time.day": "{n} дн. назад"
}
```

`frontend/i18n/kk.json`:
```json
{
  "nav.skip": "Мазмұнға өту",
  "nav.main": "Негізгі мәзір",
  "nav.sites": "Сайттар",
  "nav.admin": "Рұқсат",
  "nav.account": "Аккаунт",
  "nav.sign_out": "Шығу",
  "lang.label": "Тіл",
  "login.title": "Кіру",
  "login.lead": "Статикалық сайттарды {domain} доменінде жариялау.",
  "login.or": "немесе",
  "login.email_label": "Эл. пошта",
  "login.email_hint": "Осы мекенжайға 6 таңбалы код жібереміз.",
  "login.email_submit": "Код алу",
  "code.title": "Поштаңызды тексеріңіз",
  "code.lead": "{email} мекенжайына 6 таңбалы код жібердік. Ол 10 минут жарамды.",
  "code.label": "Код",
  "code.submit": "Кіру",
  "code.resend": "Жаңа код жіберу",
  "code.resend_in": "Жаңа код {s} с кейін",
  "code.change_email": "Басқа мекенжай",
  "code.sent": "Жаңа код жібердік.",
  "email.subject": "{code} — zerde/deploy кіру коды",
  "email.body": "{domain} сайтына кіру кодыңыз:\n\n{code}\n\nКод 10 минут жарамды. Егер оны сұрамаған болсаңыз, бұл хатты елемеңіз.",
  "noaccess.title": "Әзірге рұқсат жоқ",
  "noaccess.body": "Сіз {email} ретінде кірдіңіз, бірақ бұл аккаунт әзірге сайт жариялай алмайды. Әкімшіден сізді қосуын сұраңыз.",
  "dash.title": "Сайттар",
  "dash.yours": "Сіздің сайттарыңыз",
  "drop.title": "Буманы, .zip немесе .html файлын осында сүйреңіз",
  "drop.redeploy_title": "Жаңа нұсқаны осында сүйреңіз",
  "drop.hint": "{mb} МБ және {files} файлға дейін. index.html түбірде болуы керек.",
  "drop.choose_files": "Файлдарды таңдау",
  "drop.choose_folder": "Буманы таңдау",
  "drop.submit": "Жариялау",
  "sites.empty": "Әзірге сайт жоқ. Алғашқы жобаңызды жоғарыдағы аймаққа сүйреңіз.",
  "sites.copy": "Сілтемені көшіру",
  "sites.settings": "Баптаулар",
  "site.back": "Барлық сайттар",
  "site.history": "Нұсқалар",
  "site.current": "Қазір",
  "site.restore": "Қайтару",
  "site.files": "Файл саны: {n}",
  "site.rename_title": "Мекенжай",
  "site.rename_hint": "Кіші латын әріптері, сандар және дефис, 3–40 таңба. Ескі мекенжай жұмысын тоқтатады.",
  "site.rename_submit": "Сақтау",
  "site.delete_title": "Сайтты жою",
  "site.delete_hint": "Сайт пен оның барлық нұсқалары жойылады. Растау үшін {name} деп жазыңыз.",
  "site.delete_label": "Сайт атауы",
  "site.delete_submit": "Жою",
  "admin.title": "Рұқсат",
  "admin.lead": "Бұл тізімдегі адамдар сайт жариялай алады. Пошта мекенжайын немесе @zerde.ai сияқты толық доменді қосыңыз.",
  "admin.pattern_label": "Пошта немесе @домен",
  "admin.add": "Қосу",
  "admin.remove": "Алып тастау",
  "admin.empty": "Әкімшілерден басқа әзірге ешкім жоқ.",
  "admin.admins": "Әкімшілер",
  "admin.admins_hint": "Сервер баптауларында беріледі (ADMIN_EMAILS).",
  "admin.all_sites": "Барлық сайттар",
  "admin.owner": "Иесі",
  "flash.deployed": "Жарияланды.",
  "flash.restored": "Нұсқа қайтарылды.",
  "flash.renamed": "Мекенжай өзгертілді.",
  "flash.deleted": "Сайт жойылды.",
  "flash.added": "Қосылды.",
  "flash.removed": "Алып тасталды.",
  "error.email_invalid": "Дұрыс пошта мекенжайын енгізіңіз.",
  "error.code_invalid": "Код қате немесе мерзімі өткен.",
  "error.rate_limited": "Әрекет тым көп. {s} с кейін қайталап көріңіз.",
  "error.google_failed": "Google арқылы кіру мүмкін болмады. Қайталап көріңіз немесе пошта арқылы кіріңіз.",
  "error.signed_out": "Сеанс аяқталды. Қайта кіріңіз.",
  "error.not_found": "Бет табылмады",
  "error.forbidden": "Бұл бетке рұқсатыңыз жоқ",
  "error.generic": "Бірдеңе дұрыс болмады. Қайталап көріңіз.",
  "error.back_home": "Сайттар тізіміне",
  "error.name_invalid": "3–40 кіші латын әрпін, санды немесе дефисті қолданыңыз.",
  "error.name_taken": "Бұл мекенжай бос емес.",
  "error.confirm_mismatch": "Атауы сәйкес келмейді.",
  "error.pattern_invalid": "Пошта мекенжайын немесе @ белгісінен басталатын доменді енгізіңіз.",
  "error.pattern_exists": "Тізімде бар.",
  "upload.error.empty": "Файлдар келмеді.",
  "upload.error.too_big": "Жүктеме көлемі {mb} МБ-тан асады.",
  "upload.error.file_too_big": "{path} файлы {mb} МБ-тан үлкен.",
  "upload.error.too_many_files": "Файлдар тым көп (шегі — {n}).",
  "upload.error.no_index": "Түбірде index.html жоқ.",
  "upload.error.unsafe_path": "Файл атауына рұқсат жоқ: {path}",
  "upload.error.duplicate_path": "Екі файлдың жолы бірдей: {path}",
  "upload.error.bad_zip": ".zip файлын оқу мүмкін емес.",
  "js.copied": "Көшірілді",
  "js.reading": "Файлдар оқылуда…",
  "js.uploading": "{total} ішінен {done} жүктелді",
  "js.processing": "Жариялануда…",
  "js.failed": "Жүктеу сәтсіз аяқталды. Байланысты тексеріп, қайталап көріңіз.",
  "js.cancel": "Болдырмау",
  "unit.b": "Б",
  "unit.kb": "КБ",
  "unit.mb": "МБ",
  "unit.gb": "ГБ",
  "time.now": "жаңа ғана",
  "time.min": "{n} мин бұрын",
  "time.hour": "{n} сағ бұрын",
  "time.day": "{n} күн бұрын"
}
```

- [ ] **Step 4: Implement `i18n.py`**

`backend/app/i18n.py`:
```python
"""Translations from frontend/i18n/<lang>.json, language negotiation and small formatters."""
from __future__ import annotations

import json
from functools import lru_cache

from .config import FRONTEND_DIR

LANGS = ("en", "ru", "kk")
DEFAULT_LANG = "en"
LANG_COOKIE = "zd_lang"
LANG_LABELS = {"en": "Eng", "ru": "Рус", "kk": "Қаз"}
_ALIASES = {"kz": "kk"}


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    path = FRONTEND_DIR / "i18n" / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def t(lang: str, key: str, **params: object) -> str:
    text = catalog(lang).get(key) or catalog(DEFAULT_LANG).get(key) or key
    return text.format(**params) if params else text


def negotiate(query: str | None, cookie: str | None, accept_language: str | None) -> str:
    for candidate in (query, cookie):
        if candidate in LANGS:
            return candidate
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        code = _ALIASES.get(code, code)
        if code in LANGS:
            return code
    return DEFAULT_LANG


def format_size(lang: str, n_bytes: int) -> str:
    units = [t(lang, "unit.b"), t(lang, "unit.kb"), t(lang, "unit.mb"), t(lang, "unit.gb")]
    value, index = float(n_bytes), 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    text = f"{value:.0f}" if index == 0 or value >= 10 else f"{value:.1f}"
    if lang != "en":
        text = text.replace(".", ",")
    return f"{text} {units[index]}"


def format_ago(lang: str, seconds: int) -> str:
    if seconds < 60:
        return t(lang, "time.now")
    if seconds < 3600:
        return t(lang, "time.min", n=seconds // 60)
    if seconds < 86400:
        return t(lang, "time.hour", n=seconds // 3600)
    return t(lang, "time.day", n=seconds // 86400)
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/i18n backend/app/i18n.py backend/tests/test_i18n.py
git commit -m "feat(i18n): English, Russian and Kazakh catalogs with negotiation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Site names and access control

**Files:**
- Create: `backend/app/deploys/__init__.py` (empty), `backend/app/deploys/names.py`, `backend/app/access.py`
- Test: `backend/tests/test_names.py`, `backend/tests/test_access.py`

**Interfaces:**
- Consumes: `db.now`, `db.connect`, `db.init_db`, `Settings`.
- Produces: `names.RESERVED: frozenset[str]`, `names.normalize_name(raw) -> str`, `names.is_valid_name(name) -> bool`, `names.generate_name(exists: Callable[[str], bool]) -> str`.
- Produces: `access.normalize_email(raw) -> str | None`, `access.normalize_pattern(raw) -> str | None` (email or `@domain`, lowercased), `access.is_admin(settings, email) -> bool`, `access.can_deploy(conn, settings, email) -> bool`, `access.list_allowlist(conn) -> list[Row]`, `access.add_to_allowlist(conn, pattern, added_by, now=None) -> bool` (False if duplicate), `access.remove_from_allowlist(conn, entry_id) -> bool`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_names.py`:
```python
import re

import pytest

from app.deploys import names


@pytest.mark.parametrize("name", ["abc", "my-site", "a1b", "x" * 40, "quiet-river-42"])
def test_valid_names(name):
    assert names.is_valid_name(name)


@pytest.mark.parametrize(
    "name", ["ab", "x" * 41, "-abc", "abc-", "a--b", "ABC", "a_b", "a.b", "абв", "www", "admin", "deploy", ""]
)
def test_invalid_names(name):
    assert not names.is_valid_name(name)


def test_normalize_name():
    assert names.normalize_name("  My-Site ") == "my-site"


def test_generate_name_is_valid_and_avoids_taken():
    taken = set()
    for _ in range(200):
        name = names.generate_name(lambda n: n in taken)
        assert re.fullmatch(r"[a-z]+-[a-z]+-\d{2}", name)
        assert names.is_valid_name(name)
        assert name not in taken
        taken.add(name)


def test_generate_name_gives_up():
    with pytest.raises(RuntimeError):
        names.generate_name(lambda n: True)
```

`backend/tests/test_access.py`:
```python
import pytest

from app import access, db
from app.config import Settings


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "app.db")
    db.init_db(c)
    yield c
    c.close()


SETTINGS = Settings(admin_emails=frozenset({"boss@zerde.ai"}))


@pytest.mark.parametrize(
    "raw,expected",
    [
        (" Ann@Example.com ", "ann@example.com"),
        ("@Zerde.AI", "@zerde.ai"),
        ("@sub.zerde.ai", "@sub.zerde.ai"),
        ("not-an-email", None),
        ("@nodot", None),
        ("a@b", None),
        ("@", None),
        ("", None),
    ],
)
def test_normalize_pattern(raw, expected):
    assert access.normalize_pattern(raw) == expected


def test_normalize_email_rejects_domains():
    assert access.normalize_email("@zerde.ai") is None
    assert access.normalize_email("x" * 250 + "@a.co") is None


def test_admin_can_always_deploy(conn):
    assert access.is_admin(SETTINGS, "Boss@Zerde.ai")
    assert access.can_deploy(conn, SETTINGS, "boss@zerde.ai")


def test_allowlist_by_email_and_domain(conn):
    assert not access.can_deploy(conn, SETTINGS, "ann@example.com")
    assert access.add_to_allowlist(conn, "ann@example.com", "boss@zerde.ai")
    assert access.can_deploy(conn, SETTINGS, "Ann@Example.com")
    assert not access.can_deploy(conn, SETTINGS, "bob@example.com")
    assert access.add_to_allowlist(conn, "@example.com", "boss@zerde.ai")
    assert access.can_deploy(conn, SETTINGS, "bob@example.com")
    assert not access.can_deploy(conn, SETTINGS, "bob@sub.example.com")


def test_duplicates_and_removal(conn):
    assert access.add_to_allowlist(conn, "@example.com", "boss@zerde.ai")
    assert not access.add_to_allowlist(conn, "@example.com", "boss@zerde.ai")
    entries = access.list_allowlist(conn)
    assert [e["pattern"] for e in entries] == ["@example.com"]
    assert access.remove_from_allowlist(conn, entries[0]["id"])
    assert not access.remove_from_allowlist(conn, entries[0]["id"])
    assert access.list_allowlist(conn) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_names.py tests/test_access.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.deploys'`.

- [ ] **Step 3: Implement**

Create empty `backend/app/deploys/__init__.py`.

`backend/app/deploys/names.py`:
```python
"""Site names: the left-most label of <name>.<SITES_DOMAIN>."""
from __future__ import annotations

import re
import secrets
from typing import Callable

RESERVED = frozenset(
    "www api app admin deploy deploys mail smtp static assets cdn auth login logout signup "
    "status internal dashboard help support docs blog zerde root test dev staging".split()
)
ADJECTIVES = (
    "amber azure bold brave bright calm clever cosmic crisp eager gentle golden green happy "
    "humble jolly keen lively lucky mellow misty noble proud quick quiet rapid royal shiny "
    "silent smooth snowy solar steady sunny swift tidy vivid warm wild windy"
).split()
NOUNS = (
    "apple aspen birch breeze brook canyon cedar cloud comet coral delta dune eagle falcon "
    "fern field forest garden glade harbor hill island lake lark leaf meadow mesa moon oasis "
    "orbit otter peak pine planet prairie river rock sky steppe stone stream tulip valley willow"
).split()
_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")


def normalize_name(raw: str) -> str:
    return raw.strip().lower()


def is_valid_name(name: str) -> bool:
    return bool(_NAME.match(name)) and "--" not in name and name not in RESERVED


def generate_name(exists: Callable[[str], bool]) -> str:
    for _ in range(50):
        name = f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{secrets.randbelow(90) + 10}"
        if not exists(name):
            return name
    raise RuntimeError("could not find a free site name")
```

`backend/app/access.py`:
```python
"""Who may deploy: admins from settings, plus an allowlist of emails and @domains."""
from __future__ import annotations

import re
import sqlite3

from . import db
from .config import Settings

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
DOMAIN_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")
EMAIL_RE = re.compile(rf"^[^@\s]{{1,64}}@{_LABEL}(?:\.{_LABEL})+$")


def normalize_email(raw: str) -> str | None:
    email = raw.strip().lower()
    return email if len(email) <= 254 and EMAIL_RE.match(email) else None


def normalize_pattern(raw: str) -> str | None:
    value = raw.strip().lower()
    if value.startswith("@"):
        return value if DOMAIN_RE.match(value[1:]) else None
    return normalize_email(value)


def is_admin(settings: Settings, email: str) -> bool:
    return email.strip().lower() in settings.admin_emails


def can_deploy(conn: sqlite3.Connection, settings: Settings, email: str) -> bool:
    email = email.strip().lower()
    if is_admin(settings, email):
        return True
    domain = "@" + email.rsplit("@", 1)[-1]
    row = conn.execute(
        "SELECT 1 FROM allowlist WHERE pattern IN (?, ?) LIMIT 1", (email, domain)
    ).fetchone()
    return row is not None


def list_allowlist(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM allowlist ORDER BY pattern").fetchall()


def add_to_allowlist(
    conn: sqlite3.Connection, pattern: str, added_by: str, now: int | None = None
) -> bool:
    try:
        conn.execute(
            "INSERT INTO allowlist (pattern, added_by, created_at) VALUES (?, ?, ?)",
            (pattern, added_by, db.now() if now is None else now),
        )
    except sqlite3.IntegrityError:
        return False
    return True


def remove_from_allowlist(conn: sqlite3.Connection, entry_id: int) -> bool:
    return conn.execute("DELETE FROM allowlist WHERE id = ?", (entry_id,)).rowcount > 0
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/deploys backend/app/access.py backend/tests/test_names.py backend/tests/test_access.py
git commit -m "feat(backend): site name rules and deploy allowlist

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: Signed cookies and sessions

**Files:**
- Create: `backend/app/signing.py`, `backend/app/auth/__init__.py` (empty), `backend/app/auth/sessions.py`
- Test: `backend/tests/test_signing.py`, `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: `db`, `users.upsert_user`, `Settings`.
- Produces: `signing.sign(value: str, secret: str, now: int | None = None) -> str` (URL-safe ASCII), `signing.unsign(token: str, secret: str, max_age: int, now: int | None = None) -> str | None`.
- Produces: `sessions.SESSION_TTL = 30*86400`, `sessions.create_session(conn, user_id, now=None) -> str` (raw token), `sessions.user_for_token(conn, token, now=None) -> Row | None` (row has all `users` columns, `id` = user id), `sessions.delete_session(conn, token)`, `sessions.set_session_cookie(response, settings, token)`, `sessions.clear_session_cookie(response, settings)`, `sessions.origin_allowed(method, headers: Mapping[str,str], app_origin) -> bool`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_signing.py`:
```python
from app import signing


def test_round_trip_with_unicode_and_pipes():
    token = signing.sign("ok:Опубликовано | done", "secret", now=1000)
    assert token.isascii()
    assert signing.unsign(token, "secret", max_age=60, now=1030) == "ok:Опубликовано | done"


def test_rejects_wrong_secret_expired_and_garbage():
    token = signing.sign("a@b.co", "secret", now=1000)
    assert signing.unsign(token, "other", max_age=60, now=1001) is None
    assert signing.unsign(token, "secret", max_age=60, now=1061) is None
    assert signing.unsign("not-a-token", "secret", max_age=60) is None
    assert signing.unsign(token[:-2] + "xx", "secret", max_age=60, now=1001) is None
```

`backend/tests/test_sessions.py`:
```python
import pytest
from starlette.responses import Response

from app import db, users
from app.auth import sessions
from app.config import Settings


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "app.db")
    db.init_db(c)
    yield c
    c.close()


def test_session_lifecycle(conn):
    user = users.upsert_user(conn, "a@b.co", now=1)
    token = sessions.create_session(conn, user["id"], now=100)
    stored = conn.execute("SELECT token_hash FROM sessions").fetchone()["token_hash"]
    assert stored != token and len(stored) == 64
    found = sessions.user_for_token(conn, token, now=200)
    assert found["id"] == user["id"] and found["email"] == "a@b.co"
    assert sessions.user_for_token(conn, "wrong", now=200) is None
    sessions.delete_session(conn, token)
    assert sessions.user_for_token(conn, token, now=200) is None


def test_expired_session_is_removed(conn):
    user = users.upsert_user(conn, "a@b.co", now=1)
    token = sessions.create_session(conn, user["id"], now=100)
    assert sessions.user_for_token(conn, token, now=100 + sessions.SESSION_TTL) is None
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


@pytest.mark.parametrize("https,name,secure", [(False, "zd_session", False), (True, "__Host-zd_session", True)])
def test_cookie_flags(https, name, secure):
    settings = Settings(https=https, secret_key="s" * 40)
    response = Response()
    sessions.set_session_cookie(response, settings, "tok")
    header = response.headers["set-cookie"]
    assert header.startswith(f"{name}=tok;")
    assert "HttpOnly" in header and "SameSite=lax" in header and "Path=/" in header
    assert ("Secure" in header) is secure
    assert "Domain" not in header


ORIGIN = "https://deploy.zerde.ai"


@pytest.mark.parametrize(
    "method,headers,allowed",
    [
        ("GET", {}, True),
        ("HEAD", {"origin": "https://evil.test"}, True),
        ("POST", {"origin": ORIGIN}, True),
        ("POST", {"origin": "https://site.deploy.zerde.ai"}, False),
        ("POST", {"origin": "null"}, False),
        ("POST", {"referer": ORIGIN + "/sites/x"}, True),
        ("POST", {"referer": ORIGIN + ".evil.test/"}, False),
        ("DELETE", {}, False),
    ],
)
def test_origin_allowed(method, headers, allowed):
    assert sessions.origin_allowed(method, headers, ORIGIN) is allowed
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_signing.py tests/test_sessions.py -q`
Expected: FAIL — `ImportError: cannot import name 'signing'`.

- [ ] **Step 3: Implement**

`backend/app/signing.py`:
```python
"""Tamper-proof, expiring values for short-lived cookies (pending email, flash messages)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time


def _mac(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def sign(value: str, secret: str, now: int | None = None) -> str:
    stamp = str(int(time.time()) if now is None else now)
    payload = f"{value}|{stamp}"
    raw = f"{payload}|{_mac(payload, secret)}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def unsign(token: str, secret: str, max_age: int, now: int | None = None) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        value, stamp, mac = raw.rsplit("|", 2)
        issued = int(stamp)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    if not hmac.compare_digest(mac, _mac(f"{value}|{stamp}", secret)):
        return None
    current = int(time.time()) if now is None else now
    if current - issued > max_age:
        return None
    return value
```

Create empty `backend/app/auth/__init__.py`.

`backend/app/auth/sessions.py`:
```python
"""Server-side sessions: a random token in a cookie, only its SHA-256 stored."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import Mapping

from starlette.responses import Response

from .. import db
from ..config import Settings

SESSION_TTL = 30 * 86400
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int, now: int | None = None) -> str:
    ts = db.now() if now is None else now
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_hash(token), user_id, ts, ts + SESSION_TTL),
    )
    return token


def user_for_token(conn: sqlite3.Connection, token: str, now: int | None = None) -> sqlite3.Row | None:
    ts = db.now() if now is None else now
    key = _hash(token)
    row = conn.execute(
        "SELECT s.expires_at AS session_expires_at, u.* FROM sessions s"
        " JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    if row["session_expires_at"] <= ts:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))
        return None
    return row


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie, token, max_age=SESSION_TTL, path="/",
        secure=settings.https, httponly=True, samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie, path="/", secure=settings.https, httponly=True, samesite="lax"
    )


def origin_allowed(method: str, headers: Mapping[str, str], app_origin: str) -> bool:
    if method.upper() in SAFE_METHODS:
        return True
    origin = headers.get("origin")
    if origin:
        return origin == app_origin
    referer = headers.get("referer")
    if referer:
        return referer == app_origin or referer.startswith(app_origin + "/")
    return False
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/signing.py backend/app/auth backend/tests/test_signing.py backend/tests/test_sessions.py
git commit -m "feat(auth): hashed server-side sessions, signed cookies, origin check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Mailer and email confirmation codes

**Files:**
- Create: `backend/app/auth/mailer.py`, `backend/app/auth/email_code.py`
- Test: `backend/tests/test_mailer.py`, `backend/tests/test_email_code.py`

**Interfaces:**
- Produces: `mailer.Message(to, subject, body)` (frozen dataclass), `mailer.ConsoleMailer` (has `.sent: list[Message]`), `mailer.SmtpMailer(settings)`, `mailer.make_mailer(settings)`; all mailers have `send(message) -> None`.
- Produces: `email_code.CODE_TTL=600, MAX_ATTEMPTS=5, EMAIL_INTERVAL=60, EMAIL_PER_HOUR=5, IP_PER_HOUR=20`; `RateLimited(scope: str, retry_after: int)` with `scope in {"email_interval","email_hour","ip"}`; `seconds_until_resend(conn, email, now=None) -> int`; `issue_code(conn, secret, email, ip, now=None) -> str`; `verify_code(conn, secret, email, code, now=None) -> bool`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_mailer.py`:
```python
from app.auth import mailer as mailer_mod
from app.auth.mailer import ConsoleMailer, Message, SmtpMailer, make_mailer
from app.config import Settings


def test_make_mailer_picks_console_without_smtp():
    assert isinstance(make_mailer(Settings()), ConsoleMailer)
    assert isinstance(make_mailer(Settings(smtp_host="smtp.test")), SmtpMailer)


def test_console_mailer_records_messages():
    m = ConsoleMailer()
    m.send(Message("a@b.co", "Hi", "Body"))
    assert m.sent == [Message("a@b.co", "Hi", "Body")]


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, email):
        self.calls.append(("send", email["To"], email["Subject"], email["From"]))


def test_smtp_mailer_uses_starttls_and_login(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP", FakeSMTP)
    settings = Settings(smtp_host="smtp.test", smtp_port=587, smtp_user="u", smtp_password="p", smtp_from="noreply@zerde.ai")
    SmtpMailer(settings).send(Message("a@b.co", "Код", "Тело"))
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.test", 587)
    assert smtp.calls == ["starttls", ("login", "u", "p"), ("send", "a@b.co", "Код", "noreply@zerde.ai")]


def test_smtp_mailer_uses_ssl_on_465(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP_SSL", FakeSMTP)
    settings = Settings(smtp_host="smtp.test", smtp_port=465, smtp_from="noreply@zerde.ai")
    SmtpMailer(settings).send(Message("a@b.co", "S", "B"))
    assert FakeSMTP.instances[0].calls == [("send", "a@b.co", "S", "noreply@zerde.ai")]
```

`backend/tests/test_email_code.py`:
```python
import re

import pytest

from app import db
from app.auth import email_code as ec

SECRET = "test-secret"
EMAIL = "a@b.co"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "app.db")
    db.init_db(c)
    yield c
    c.close()


def test_issue_and_verify_once(conn):
    code = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    assert re.fullmatch(r"\d{6}", code)
    stored = conn.execute("SELECT code_hash FROM login_codes").fetchone()["code_hash"]
    assert code not in stored
    assert ec.verify_code(conn, SECRET, EMAIL, code, now=1010) is True
    assert ec.verify_code(conn, SECRET, EMAIL, code, now=1011) is False


def test_spaces_are_ignored(conn):
    code = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    assert ec.verify_code(conn, SECRET, EMAIL, f" {code[:3]} {code[3:]} ", now=1001)


def test_code_expires(conn):
    code = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    assert ec.verify_code(conn, SECRET, EMAIL, code, now=1000 + ec.CODE_TTL) is False


def test_wrong_attempts_burn_the_code(conn):
    code = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(ec.MAX_ATTEMPTS):
        assert ec.verify_code(conn, SECRET, EMAIL, wrong, now=1001) is False
    assert ec.verify_code(conn, SECRET, EMAIL, code, now=1002) is False


def test_new_code_replaces_old(conn):
    old = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    new = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000 + ec.EMAIL_INTERVAL)
    if old != new:
        assert ec.verify_code(conn, SECRET, EMAIL, old, now=1061) is False
    assert ec.verify_code(conn, SECRET, EMAIL, new, now=1062) is True


def test_resend_interval(conn):
    ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    assert ec.seconds_until_resend(conn, EMAIL, now=1030) == 30
    with pytest.raises(ec.RateLimited) as info:
        ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1030)
    assert info.value.scope == "email_interval" and info.value.retry_after == 30
    assert ec.seconds_until_resend(conn, "new@b.co", now=1030) == 0


def test_hourly_limit_per_email(conn):
    for i in range(ec.EMAIL_PER_HOUR):
        ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000 + i * 61)
    with pytest.raises(ec.RateLimited) as info:
        ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000 + 5 * 61)
    assert info.value.scope == "email_hour"
    assert info.value.retry_after == 1000 + 3600 - (1000 + 5 * 61)


def test_hourly_limit_per_ip(conn):
    for i in range(ec.IP_PER_HOUR):
        ec.issue_code(conn, SECRET, f"u{i}@b.co", "9.9.9.9", now=1000 + i)
    with pytest.raises(ec.RateLimited) as info:
        ec.issue_code(conn, SECRET, "late@b.co", "9.9.9.9", now=1100)
    assert info.value.scope == "ip"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_mailer.py tests/test_email_code.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth.mailer'`.

- [ ] **Step 3: Implement**

`backend/app/auth/mailer.py`:
```python
"""Outgoing email: SMTP when configured, otherwise the console (development)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from ..config import Settings

log = logging.getLogger("zerde.mail")


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str


class Mailer(Protocol):
    def send(self, message: Message) -> None: ...


class ConsoleMailer:
    """Development mailer: keeps messages in memory and logs them."""

    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)
        log.warning("Email to %s | %s\n%s", message.to, message.subject, message.body)


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, message: Message) -> None:
        s = self.settings
        email = EmailMessage()
        email["From"] = s.smtp_from
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.body)
        context = ssl.create_default_context()
        if s.smtp_port == 465:
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15, context=context)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
        with server:
            if s.smtp_port != 465 and s.smtp_starttls:
                server.starttls(context=context)
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(email)


def make_mailer(settings: Settings) -> Mailer:
    return SmtpMailer(settings) if settings.smtp_host else ConsoleMailer()
```

`backend/app/auth/email_code.py`:
```python
"""Six-digit email confirmation codes with expiry, attempt limits and rate limits."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3

from .. import db

CODE_TTL = 600
MAX_ATTEMPTS = 5
EMAIL_INTERVAL = 60
EMAIL_PER_HOUR = 5
IP_PER_HOUR = 20
HOUR = 3600
DIGITS = "0123456789"


class RateLimited(Exception):
    def __init__(self, scope: str, retry_after: int) -> None:
        super().__init__(f"{scope}: retry after {retry_after}s")
        self.scope = scope
        self.retry_after = max(1, retry_after)


def _digest(secret: str, email: str, code: str) -> str:
    return hmac.new(secret.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def seconds_until_resend(conn: sqlite3.Connection, email: str, now: int | None = None) -> int:
    ts = db.now() if now is None else now
    last = conn.execute(
        "SELECT MAX(created_at) AS last FROM login_codes WHERE email = ?", (email,)
    ).fetchone()["last"]
    return 0 if last is None else max(0, EMAIL_INTERVAL - (ts - last))


def issue_code(conn: sqlite3.Connection, secret: str, email: str, ip: str, now: int | None = None) -> str:
    ts = db.now() if now is None else now
    wait = seconds_until_resend(conn, email, ts)
    if wait:
        raise RateLimited("email_interval", wait)
    since = ts - HOUR
    recent = conn.execute(
        "SELECT created_at FROM login_codes WHERE email = ? AND created_at > ? ORDER BY created_at",
        (email, since),
    ).fetchall()
    if len(recent) >= EMAIL_PER_HOUR:
        raise RateLimited("email_hour", recent[0]["created_at"] + HOUR - ts)
    recent = conn.execute(
        "SELECT created_at FROM login_codes WHERE ip = ? AND created_at > ? ORDER BY created_at",
        (ip, since),
    ).fetchall()
    if len(recent) >= IP_PER_HOUR:
        raise RateLimited("ip", recent[0]["created_at"] + HOUR - ts)
    code = f"{secrets.randbelow(1_000_000):06d}"
    with db.transaction(conn):
        conn.execute("DELETE FROM login_codes WHERE created_at < ?", (ts - 86400,))
        conn.execute(
            "UPDATE login_codes SET consumed_at = ? WHERE email = ? AND consumed_at IS NULL", (ts, email)
        )
        conn.execute(
            "INSERT INTO login_codes (email, code_hash, ip, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (email, _digest(secret, email, code), ip, ts, ts + CODE_TTL),
        )
    return code


def verify_code(conn: sqlite3.Connection, secret: str, email: str, code: str, now: int | None = None) -> bool:
    ts = db.now() if now is None else now
    digits = "".join(ch for ch in code if ch in DIGITS)
    with db.transaction(conn):
        row = conn.execute(
            "SELECT id, code_hash, attempts FROM login_codes"
            " WHERE email = ? AND consumed_at IS NULL AND expires_at > ?"
            " ORDER BY created_at DESC LIMIT 1",
            (email, ts),
        ).fetchone()
        if row is None:
            return False
        if len(digits) == 6 and hmac.compare_digest(row["code_hash"], _digest(secret, email, digits)):
            conn.execute("UPDATE login_codes SET consumed_at = ? WHERE id = ?", (ts, row["id"]))
            return True
        attempts = row["attempts"] + 1
        conn.execute(
            "UPDATE login_codes SET attempts = ?,"
            " consumed_at = CASE WHEN ? >= ? THEN ? ELSE NULL END WHERE id = ?",
            (attempts, attempts, MAX_ATTEMPTS, ts, row["id"]),
        )
        return False
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth/mailer.py backend/app/auth/email_code.py backend/tests/test_mailer.py backend/tests/test_email_code.py
git commit -m "feat(auth): email confirmation codes with rate limits and SMTP mailer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Google ID-token verification

**Files:**
- Create: `backend/app/auth/google.py`
- Test: `backend/tests/test_google.py`

**Interfaces:**
- Produces: `GoogleAuthError(Exception)`, `GoogleIdentity(sub, email, name, picture)` (frozen dataclass; email lowercased), `GoogleVerifier(client_id: str, jwks_client=None)` with `verify(token: str) -> GoogleIdentity`. `jwks_client` must offer `get_signing_key_from_jwt(token) -> object with .key` (PyJWT's `PyJWKClient` in production).

- [ ] **Step 1: Write the failing test**

`backend/tests/test_google.py`:
```python
import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.google import GoogleAuthError, GoogleVerifier

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CLIENT_ID = "cid.apps.googleusercontent.com"


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return types.SimpleNamespace(key=KEY.public_key())


def make_token(key=KEY, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "Ann@Zerde.ai",
        "email_verified": True,
        "name": "Ann",
        "picture": "https://example.test/a.png",
        "iat": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def verifier():
    return GoogleVerifier(CLIENT_ID, jwks_client=FakeJwks())


def test_valid_token(verifier):
    identity = verifier.verify(make_token())
    assert identity.email == "ann@zerde.ai"
    assert identity.sub == "1234567890" and identity.name == "Ann"


def test_short_issuer_is_accepted(verifier):
    assert verifier.verify(make_token(iss="accounts.google.com")).sub == "1234567890"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.test"},
        {"exp": int(time.time()) - 120},
        {"email_verified": False},
    ],
)
def test_rejected_claims(verifier, overrides):
    with pytest.raises(GoogleAuthError):
        verifier.verify(make_token(**overrides))


def test_wrong_signature(verifier):
    with pytest.raises(GoogleAuthError):
        verifier.verify(make_token(key=OTHER_KEY))


def test_garbage_and_disabled():
    with pytest.raises(GoogleAuthError):
        GoogleVerifier(CLIENT_ID, jwks_client=FakeJwks()).verify("not.a.jwt")
    with pytest.raises(GoogleAuthError):
        GoogleVerifier("", jwks_client=FakeJwks()).verify(make_token())
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_google.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth.google'`.

- [ ] **Step 3: Implement**

`backend/app/auth/google.py`:
```python
"""Verify "Sign in with Google" ID tokens. Needs only the public OAuth client ID."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt

JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


class GoogleAuthError(Exception):
    pass


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str
    picture: str


class GoogleVerifier:
    def __init__(self, client_id: str, jwks_client: Any = None) -> None:
        self.client_id = client_id
        self._jwks = jwks_client or jwt.PyJWKClient(JWKS_URL, lifespan=3600)

    def verify(self, token: str) -> GoogleIdentity:
        if not self.client_id:
            raise GoogleAuthError("Google sign-in is not configured")
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.client_id,
                options={"require": ["iss", "aud", "sub", "exp", "iat", "email"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise GoogleAuthError(str(exc)) from exc
        if claims["iss"] not in ISSUERS:
            raise GoogleAuthError("wrong issuer")
        if claims.get("email_verified") not in (True, "true"):
            raise GoogleAuthError("email is not verified")
        return GoogleIdentity(
            sub=str(claims["sub"]),
            email=str(claims["email"]).strip().lower(),
            name=str(claims.get("name", "")),
            picture=str(claims.get("picture", "")),
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth/google.py backend/tests/test_google.py
git commit -m "feat(auth): verify Google ID tokens against Google JWKS

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Upload ingestion (safe file trees)

**Files:**
- Create: `backend/app/deploys/ingest.py`
- Test: `backend/tests/test_ingest.py`

**Interfaces:**
- Produces: `MB`, `UploadError(code: str, **params)` with `.code`/`.params` (codes: `empty, too_big, file_too_big, too_many_files, no_index, unsafe_path, duplicate_path, bad_zip` — each maps to i18n key `upload.error.<code>`), `Limits(max_total_bytes, max_files, max_file_bytes, max_unzipped_bytes=200*MB, max_ratio=100)`, `StagedTree(root: Path, file_count: int, total_bytes: int)`, `clean_path(raw) -> str | None`, `stage_files(items: Iterable[tuple[str, BinaryIO]], dest: Path, limits) -> StagedTree`, `stage_zip(source: Path | BinaryIO, dest: Path, limits) -> StagedTree`, `precompress(root: Path)`.
- Behaviour: skip `.git/`, `node_modules/`, `__MACOSX/`, `.DS_Store`, `Thumbs.db`; unwrap a single top-level folder (up to 3 levels); a lone `.html`/`.htm` file becomes `index.html`; require `index.html`; write `<file>.gz` siblings for text files ≥ 1 KB.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_ingest.py`:
```python
import gzip
import io
import stat
import zipfile

import pytest

from app.deploys.ingest import MB, Limits, UploadError, clean_path, stage_files, stage_zip

LIMITS = Limits(max_total_bytes=1_000_000, max_files=10, max_file_bytes=500_000, max_unzipped_bytes=3 * MB)


def zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


def error_code(fn, *args):
    with pytest.raises(UploadError) as info:
        fn(*args)
    return info.value.code


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("index.html", "index.html"),
        ("a\\b.css", "a/b.css"),
        ("./a//b.js", "a/b.js"),
        ("site/.git/config", None),
        ("node_modules/x/y.js", None),
        (".DS_Store", None),
        ("__MACOSX/._a", None),
        ("сайт/главная.html", "сайт/главная.html"),
    ],
)
def test_clean_path(raw, expected):
    assert clean_path(raw) == expected


@pytest.mark.parametrize("raw", ["../x", "a/../../x", "/etc/passwd", "C:/x", "a\x00b", "a\nb", "", "x" * 300])
def test_clean_path_rejects_unsafe(raw):
    assert error_code(clean_path, raw) == "unsafe_path"


def test_stage_files_writes_tree_and_precompresses(tmp_path):
    page = b"<html>" + b"x" * 2000 + b"</html>"
    tree = stage_files([("index.html", io.BytesIO(page)), ("css/a.css", io.BytesIO(b"body{}"))], tmp_path / "d", LIMITS)
    assert (tree.file_count, tree.total_bytes) == (2, len(page) + 6)
    assert (tmp_path / "d/index.html").read_bytes() == page
    assert gzip.decompress((tmp_path / "d/index.html.gz").read_bytes()) == page
    assert not (tmp_path / "d/css/a.css.gz").exists()


def test_single_html_becomes_index(tmp_path):
    stage_files([("landing.html", io.BytesIO(b"hi"))], tmp_path / "d", LIMITS)
    assert (tmp_path / "d/index.html").read_bytes() == b"hi"
    assert not (tmp_path / "d/landing.html").exists()


def test_single_top_folder_is_unwrapped(tmp_path):
    tree = stage_files([("proj/index.html", io.BytesIO(b"i")), ("proj/css/a.css", io.BytesIO(b"c"))], tmp_path / "d", LIMITS)
    assert (tmp_path / "d/index.html").read_bytes() == b"i"
    assert (tmp_path / "d/css/a.css").read_bytes() == b"c"
    assert tree.file_count == 2


def test_folder_named_like_its_parent_is_unwrapped(tmp_path):
    stage_files([("site/site/index.html", io.BytesIO(b"i"))], tmp_path / "d", LIMITS)
    assert (tmp_path / "d/index.html").read_bytes() == b"i"


def test_skipped_files_are_not_written(tmp_path):
    tree = stage_files(
        [("index.html", io.BytesIO(b"i")), ("node_modules/a.js", io.BytesIO(b"x")), (".DS_Store", io.BytesIO(b"x"))],
        tmp_path / "d",
        LIMITS,
    )
    assert tree.file_count == 1
    assert not (tmp_path / "d/node_modules").exists()


def test_stage_files_errors(tmp_path):
    assert error_code(stage_files, [], tmp_path / "a", LIMITS) == "empty"
    assert error_code(stage_files, [("readme.txt", io.BytesIO(b"x")), ("a.css", io.BytesIO(b"y"))], tmp_path / "b", LIMITS) == "no_index"
    assert error_code(stage_files, [("index.html", io.BytesIO(b"x" * 600_000))], tmp_path / "c", LIMITS) == "file_too_big"
    big = [(f"f{i}.html", io.BytesIO(b"x" * 400_000)) for i in range(3)]
    assert error_code(stage_files, big, tmp_path / "d", LIMITS) == "too_big"
    many = [(f"f{i}.txt", io.BytesIO(b"x")) for i in range(11)]
    assert error_code(stage_files, many, tmp_path / "e", LIMITS) == "too_many_files"
    dup = [("index.html", io.BytesIO(b"a")), ("./index.html", io.BytesIO(b"b"))]
    assert error_code(stage_files, dup, tmp_path / "f", LIMITS) == "duplicate_path"


def test_stage_zip(tmp_path):
    tree = stage_zip(zip_bytes({"site/index.html": b"<h1>hi</h1>", "site/img/a.png": b"\x89PNG"}), tmp_path / "d", LIMITS)
    assert (tmp_path / "d/index.html").read_bytes() == b"<h1>hi</h1>"
    assert (tmp_path / "d/img/a.png").exists()
    assert tree.file_count == 2


def test_zip_slip_is_rejected(tmp_path):
    assert error_code(stage_zip, zip_bytes({"../evil.html": b"x", "index.html": b"i"}), tmp_path / "d", LIMITS) == "unsafe_path"
    assert not (tmp_path / "evil.html").exists()


def test_zip_symlink_is_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("index.html")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    buf.seek(0)
    assert error_code(stage_zip, buf, tmp_path / "d", LIMITS) == "unsafe_path"


def test_zip_bomb_is_rejected(tmp_path):
    bomb = zip_bytes({"index.html": b"i", "zeros.txt": b"\0" * (2 * MB)})
    assert error_code(stage_zip, bomb, tmp_path / "d", LIMITS) == "too_big"


def test_bad_and_oversized_zips(tmp_path):
    assert error_code(stage_zip, io.BytesIO(b"not a zip"), tmp_path / "a", LIMITS) == "bad_zip"
    many = zip_bytes({f"f{i}.txt": b"x" for i in range(11)})
    assert error_code(stage_zip, many, tmp_path / "b", LIMITS) == "too_many_files"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_ingest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.deploys.ingest'`.

- [ ] **Step 3: Implement**

`backend/app/deploys/ingest.py`:
```python
"""Turn an upload (files with relative paths, or a .zip) into a safe, publishable file tree."""
from __future__ import annotations

import gzip
import re
import shutil
import stat
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

MB = 1024 * 1024
CHUNK = 64 * 1024
SKIP_DIRS = frozenset({".git", "node_modules", "__MACOSX"})
SKIP_FILES = frozenset({".DS_Store", "Thumbs.db"})
COMPRESSIBLE = frozenset(
    {".html", ".htm", ".css", ".js", ".mjs", ".svg", ".json", ".xml", ".txt", ".map", ".webmanifest"}
)
PRECOMPRESS_MIN = 1024
_DRIVE = re.compile(r"^[A-Za-z]:")


class UploadError(Exception):
    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class Limits:
    max_total_bytes: int = 50 * MB
    max_files: int = 2000
    max_file_bytes: int = 25 * MB
    max_unzipped_bytes: int = 200 * MB
    max_ratio: int = 100


@dataclass(frozen=True)
class StagedTree:
    root: Path
    file_count: int
    total_bytes: int


class _Budget:
    def __init__(self, limits: Limits, max_bytes: int) -> None:
        self.limits = limits
        self.max_bytes = max_bytes
        self.files = 0
        self.bytes = 0


def _short(value: str) -> str:
    return value if len(value) <= 80 else value[:77] + "..."


def clean_path(raw: str) -> str | None:
    """Safe relative POSIX path, or None when the file should be skipped silently."""
    path = raw.replace("\\", "/")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path) or path.startswith("/") or _DRIVE.match(path):
        raise UploadError("unsafe_path", path=_short(raw))
    parts = [part for part in path.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise UploadError("unsafe_path", path=_short(raw))
    if any(part in SKIP_DIRS for part in parts) or parts[-1] in SKIP_FILES:
        return None
    joined = "/".join(parts)
    if len(joined) > 255:
        raise UploadError("unsafe_path", path=_short(raw))
    return joined


def _write(stream: BinaryIO, dest: Path, rel: str, budget: _Budget) -> None:
    budget.files += 1
    if budget.files > budget.limits.max_files:
        raise UploadError("too_many_files", n=budget.limits.max_files)
    target = dest / rel
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        out = open(target, "xb")
    except FileExistsError:
        raise UploadError("duplicate_path", path=_short(rel)) from None
    except OSError:
        raise UploadError("unsafe_path", path=_short(rel)) from None
    written = 0
    with out:
        while chunk := stream.read(CHUNK):
            written += len(chunk)
            budget.bytes += len(chunk)
            if written > budget.limits.max_file_bytes:
                raise UploadError("file_too_big", path=_short(rel), mb=budget.limits.max_file_bytes // MB)
            if budget.bytes > budget.max_bytes:
                raise UploadError("too_big", mb=budget.max_bytes // MB)
            out.write(chunk)


def stage_files(items: Iterable[tuple[str, BinaryIO]], dest: Path, limits: Limits) -> StagedTree:
    dest.mkdir(parents=True, exist_ok=True)
    budget = _Budget(limits, limits.max_total_bytes)
    for raw, stream in items:
        rel = clean_path(raw)
        if rel is not None:
            _write(stream, dest, rel, budget)
    return _finish(dest, budget)


def stage_zip(source: Path | BinaryIO, dest: Path, limits: Limits) -> StagedTree:
    dest.mkdir(parents=True, exist_ok=True)
    budget = _Budget(limits, limits.max_unzipped_bytes)
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise UploadError("bad_zip") from None
    with archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        if len(entries) > limits.max_files:
            raise UploadError("too_many_files", n=limits.max_files)
        if sum(info.file_size for info in entries) > limits.max_unzipped_bytes:
            raise UploadError("too_big", mb=limits.max_unzipped_bytes // MB)
        for info in entries:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise UploadError("unsafe_path", path=_short(info.filename))
            if info.flag_bits & 0x1:
                raise UploadError("bad_zip")
            if info.file_size > MB and info.file_size > info.compress_size * limits.max_ratio:
                raise UploadError("too_big", mb=limits.max_unzipped_bytes // MB)
            rel = clean_path(info.filename)
            if rel is None:
                continue
            try:
                with archive.open(info) as stream:
                    _write(stream, dest, rel, budget)
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError):
                raise UploadError("bad_zip") from None
    return _finish(dest, budget)


def _normalize_layout(dest: Path) -> None:
    for _ in range(3):
        if (dest / "index.html").exists():
            return
        children = list(dest.iterdir())
        if len(children) == 1 and children[0].is_dir():
            inner = children[0].rename(dest / ".zd-unwrap")
            for child in list(inner.iterdir()):
                child.rename(dest / child.name)
            inner.rmdir()
            continue
        if len(children) == 1 and children[0].suffix.lower() in {".html", ".htm"}:
            children[0].rename(dest / "index.html")
        return


def precompress(root: Path) -> None:
    for path in list(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in COMPRESSIBLE and path.stat().st_size >= PRECOMPRESS_MIN:
            with open(path, "rb") as src, open(path.with_name(path.name + ".gz"), "wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as gz:
                    shutil.copyfileobj(src, gz, CHUNK)


def _finish(dest: Path, budget: _Budget) -> StagedTree:
    if budget.files == 0:
        raise UploadError("empty")
    _normalize_layout(dest)
    if not (dest / "index.html").is_file():
        raise UploadError("no_index")
    precompress(dest)
    return StagedTree(dest, budget.files, budget.bytes)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/deploys/ingest.py backend/tests/test_ingest.py
git commit -m "feat(deploys): safe upload ingestion with zip guards and gzip precompression

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Site store and deploy service

**Files:**
- Create: `backend/app/deploys/store.py`, `backend/app/deploys/service.py`
- Test: `backend/tests/test_store.py`, `backend/tests/test_service.py`

**Interfaces:**
- Consumes: `db`, `users.upsert_user`, `names.generate_name/is_valid_name/normalize_name`, `ingest.StagedTree`, `ingest.stage_files`, `ingest.Limits`.
- Produces: `SiteStore(sites_dir: Path, tmp_dir: Path)` with `site_dir(name)`, `current_dir(name)`, `deploy_dir(name, id)`, `new_staging_dir() -> Path` (inside `tmp_dir`), `cleanup_temp()`, `publish(name, staging: Path, deploy_id)`, `activate(name, deploy_id)` (raises `FileNotFoundError`), `current_deploy(name) -> int | None`, `prune(name, keep: set[int])`, `rename(old, new)` (raises `FileExistsError`), `delete(name)`, `lock(name)` context manager. `current` is a relative symlink `deploys/<id>`.
- Produces: `service.KEEP_DEPLOYS = 10`, `SiteError(code)` (codes `not_found, name_invalid, name_taken`), `get_site(conn, name)`, `list_sites(conn, owner_id=None)` (rows have site columns + `owner_email, total_bytes, file_count, deployed_at`), `list_deploys(conn, site_id)` (rows + `author_email`, newest first), `can_manage(site, user, admin: bool)`, `publish(conn, store, *, site_name: str | None, user_id, staged, now=None) -> tuple[str, int]`, `restore(conn, store, site, deploy_id, now=None)`, `rename(conn, store, site, new_name, now=None) -> str`, `delete(conn, store, site)`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_store.py`:
```python
import os

import pytest

from app.deploys.store import SiteStore


@pytest.fixture
def store(tmp_path):
    return SiteStore(tmp_path / "sites", tmp_path / "tmp")


def staged(store, text):
    path = store.new_staging_dir()
    (path / "index.html").write_text(text)
    return path


def test_publish_points_current_at_the_deploy(store):
    store.publish("demo", staged(store, "v1"), 1)
    assert (store.current_dir("demo") / "index.html").read_text() == "v1"
    assert os.readlink(store.current_dir("demo")) == "deploys/1"
    assert store.current_deploy("demo") == 1


def test_publish_again_and_activate_older(store):
    store.publish("demo", staged(store, "v1"), 1)
    store.publish("demo", staged(store, "v2"), 2)
    assert (store.current_dir("demo") / "index.html").read_text() == "v2"
    store.activate("demo", 1)
    assert (store.current_dir("demo") / "index.html").read_text() == "v1"
    with pytest.raises(FileNotFoundError):
        store.activate("demo", 99)


def test_prune_keeps_only_given_ids(store):
    for i in (1, 2, 3):
        store.publish("demo", staged(store, f"v{i}"), i)
    store.prune("demo", {2, 3})
    assert not store.deploy_dir("demo", 1).exists()
    assert store.deploy_dir("demo", 2).exists() and store.deploy_dir("demo", 3).exists()


def test_rename_and_delete(store):
    store.publish("old", staged(store, "v1"), 1)
    store.publish("taken", staged(store, "t"), 2)
    with pytest.raises(FileExistsError):
        store.rename("old", "taken")
    store.rename("old", "new")
    assert (store.current_dir("new") / "index.html").read_text() == "v1"
    assert not store.site_dir("old").exists()
    store.delete("new")
    assert not store.site_dir("new").exists()


def test_cleanup_temp_and_lock(store):
    leftover = store.new_staging_dir()
    (leftover / "x").write_text("x")
    store.cleanup_temp()
    assert not leftover.exists()
    with store.lock("demo"):
        pass
```

`backend/tests/test_service.py`:
```python
import io

import pytest

from app import db, users
from app.deploys import service
from app.deploys.ingest import Limits, stage_files
from app.deploys.store import SiteStore


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "app.db")
    db.init_db(conn)
    store = SiteStore(tmp_path / "sites", tmp_path / "tmp")
    owner = users.upsert_user(conn, "owner@zerde.ai", now=1)
    yield conn, store, owner
    conn.close()


def tree(store, text):
    return stage_files([("index.html", io.BytesIO(text.encode()))], store.new_staging_dir(), Limits())


def served(store, name):
    return (store.current_dir(name) / "index.html").read_text()


def test_publish_new_site_then_redeploy(env):
    conn, store, owner = env
    name, first = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "v1"), now=10)
    site = service.get_site(conn, name)
    assert site["owner_id"] == owner["id"] and site["current_deploy_id"] == first
    assert served(store, name) == "v1"
    same, second = service.publish(conn, store, site_name=name, user_id=owner["id"], staged=tree(store, "v2"), now=20)
    assert same == name and second > first
    assert served(store, name) == "v2"
    rows = service.list_sites(conn, owner["id"])
    assert [r["name"] for r in rows] == [name]
    assert rows[0]["deployed_at"] == 20 and rows[0]["owner_email"] == "owner@zerde.ai"
    assert [d["id"] for d in service.list_deploys(conn, site["id"])] == [second, first]


def test_publish_to_missing_site_fails(env):
    conn, store, owner = env
    with pytest.raises(service.SiteError) as info:
        service.publish(conn, store, site_name="nope-site", user_id=owner["id"], staged=tree(store, "x"))
    assert info.value.code == "not_found"


def test_only_ten_deploys_are_kept(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "v0"))
    for i in range(1, 12):
        service.publish(conn, store, site_name=name, user_id=owner["id"], staged=tree(store, f"v{i}"))
    site = service.get_site(conn, name)
    ids = [d["id"] for d in service.list_deploys(conn, site["id"])]
    assert len(ids) == service.KEEP_DEPLOYS
    kept_dirs = sorted(int(p.name) for p in (store.site_dir(name) / "deploys").iterdir())
    assert kept_dirs == sorted(ids)


def test_restore(env):
    conn, store, owner = env
    name, first = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "v1"))
    service.publish(conn, store, site_name=name, user_id=owner["id"], staged=tree(store, "v2"))
    service.restore(conn, store, service.get_site(conn, name), first)
    assert served(store, name) == "v1"
    assert service.get_site(conn, name)["current_deploy_id"] == first
    with pytest.raises(service.SiteError):
        service.restore(conn, store, service.get_site(conn, name), 9999)


def test_rename(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "v1"))
    other, _ = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "o"))
    site = service.get_site(conn, name)
    for bad, code in [("x", "name_invalid"), ("admin", "name_invalid"), (other, "name_taken")]:
        with pytest.raises(service.SiteError) as info:
            service.rename(conn, store, site, bad)
        assert info.value.code == code
    assert service.rename(conn, store, site, " My-Site ") == "my-site"
    assert service.get_site(conn, name) is None
    assert served(store, "my-site") == "v1"


def test_delete(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "v1"))
    service.delete(conn, store, service.get_site(conn, name))
    assert service.get_site(conn, name) is None
    assert not store.site_dir(name).exists()
    assert conn.execute("SELECT COUNT(*) FROM deploys").fetchone()[0] == 0


def test_can_manage():
    site = {"owner_id": 1}
    assert service.can_manage(site, {"id": 1}, admin=False)
    assert not service.can_manage(site, {"id": 2}, admin=False)
    assert service.can_manage(site, {"id": 2}, admin=True)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_store.py tests/test_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.deploys.store'`.

- [ ] **Step 3: Implement**

`backend/app/deploys/store.py`:
```python
"""On-disk layout: sites/<name>/deploys/<id>/ trees plus an atomically swapped `current` symlink."""
from __future__ import annotations

import fcntl
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SiteStore:
    def __init__(self, sites_dir: Path, tmp_dir: Path) -> None:
        self.sites_dir = sites_dir
        self.tmp_dir = tmp_dir
        self.locks_dir = sites_dir / ".locks"
        for directory in (sites_dir, tmp_dir, self.locks_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def site_dir(self, name: str) -> Path:
        return self.sites_dir / name

    def current_dir(self, name: str) -> Path:
        return self.site_dir(name) / "current"

    def deploy_dir(self, name: str, deploy_id: int) -> Path:
        return self.site_dir(name) / "deploys" / str(deploy_id)

    def new_staging_dir(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="upload-", dir=self.tmp_dir))

    def cleanup_temp(self) -> None:
        for child in self.tmp_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    def publish(self, name: str, staging: Path, deploy_id: int) -> None:
        target = self.deploy_dir(name, deploy_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(target)
        target.chmod(0o755)  # mkdtemp creates 0700; the web server must be able to read it
        self.activate(name, deploy_id)

    def activate(self, name: str, deploy_id: int) -> None:
        if not self.deploy_dir(name, deploy_id).is_dir():
            raise FileNotFoundError(f"deploy {deploy_id} of {name} is missing")
        link = self.current_dir(name)
        tmp = link.with_name("current.new")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(f"deploys/{deploy_id}", tmp)
        os.replace(tmp, link)

    def current_deploy(self, name: str) -> int | None:
        link = self.current_dir(name)
        if not link.is_symlink():
            return None
        return int(os.readlink(link).rsplit("/", 1)[-1])

    def prune(self, name: str, keep: set[int]) -> None:
        root = self.site_dir(name) / "deploys"
        if not root.is_dir():
            return
        for child in root.iterdir():
            if child.name.isdigit() and int(child.name) not in keep:
                shutil.rmtree(child, ignore_errors=True)

    def rename(self, old: str, new: str) -> None:
        target = self.site_dir(new)
        if target.exists():
            raise FileExistsError(new)
        self.site_dir(old).rename(target)

    def delete(self, name: str) -> None:
        shutil.rmtree(self.site_dir(name), ignore_errors=True)

    @contextmanager
    def lock(self, name: str) -> Iterator[None]:
        with open(self.locks_dir / f"{name}.lock", "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
```

`backend/app/deploys/service.py`:
```python
"""Site operations that touch both the database and the file store."""
from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from .. import db
from . import names
from .ingest import StagedTree
from .store import SiteStore

KEEP_DEPLOYS = 10
_SITE_COLUMNS = (
    "SELECT s.*, u.email AS owner_email, d.total_bytes, d.file_count, d.created_at AS deployed_at"
    " FROM sites s JOIN users u ON u.id = s.owner_id"
    " LEFT JOIN deploys d ON d.id = s.current_deploy_id"
)


class SiteError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def get_site(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sites WHERE name = ?", (name,)).fetchone()


def list_sites(conn: sqlite3.Connection, owner_id: int | None = None) -> list[sqlite3.Row]:
    order = " ORDER BY s.updated_at DESC, s.id DESC"
    if owner_id is None:
        return conn.execute(_SITE_COLUMNS + order).fetchall()
    return conn.execute(_SITE_COLUMNS + " WHERE s.owner_id = ?" + order, (owner_id,)).fetchall()


def list_deploys(conn: sqlite3.Connection, site_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT d.*, u.email AS author_email FROM deploys d JOIN users u ON u.id = d.created_by"
        " WHERE d.site_id = ? ORDER BY d.id DESC",
        (site_id,),
    ).fetchall()


def can_manage(site: Mapping[str, Any], user: Mapping[str, Any], admin: bool) -> bool:
    return admin or site["owner_id"] == user["id"]


def publish(
    conn: sqlite3.Connection,
    store: SiteStore,
    *,
    site_name: str | None,
    user_id: int,
    staged: StagedTree,
    now: int | None = None,
) -> tuple[str, int]:
    ts = db.now() if now is None else now
    created = site_name is None
    if created:
        with db.transaction(conn):
            name = names.generate_name(lambda n: get_site(conn, n) is not None)
            site_id = conn.execute(
                "INSERT INTO sites (name, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (name, user_id, ts, ts),
            ).lastrowid
    else:
        site = get_site(conn, site_name)
        if site is None:
            raise SiteError("not_found")
        name, site_id = site["name"], site["id"]
    with store.lock(name):
        deploy_id = conn.execute(
            "INSERT INTO deploys (site_id, created_by, file_count, total_bytes, created_at) VALUES (?, ?, ?, ?, ?)",
            (site_id, user_id, staged.file_count, staged.total_bytes, ts),
        ).lastrowid
        try:
            store.publish(name, staged.root, deploy_id)
        except BaseException:
            conn.execute("DELETE FROM deploys WHERE id = ?", (deploy_id,))
            if created:
                conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
                store.delete(name)
            raise
        conn.execute(
            "UPDATE sites SET current_deploy_id = ?, updated_at = ? WHERE id = ?", (deploy_id, ts, site_id)
        )
        _prune(conn, store, name, site_id, deploy_id)
    return name, deploy_id


def _prune(conn: sqlite3.Connection, store: SiteStore, name: str, site_id: int, current_id: int) -> None:
    rows = conn.execute("SELECT id FROM deploys WHERE site_id = ? ORDER BY id DESC", (site_id,)).fetchall()
    keep = {row["id"] for row in rows[:KEEP_DEPLOYS]} | {current_id}
    stale = [row["id"] for row in rows if row["id"] not in keep]
    if stale:
        conn.execute(f"DELETE FROM deploys WHERE id IN ({','.join('?' * len(stale))})", stale)
    store.prune(name, keep)


def restore(conn: sqlite3.Connection, store: SiteStore, site: sqlite3.Row, deploy_id: int, now: int | None = None) -> None:
    ts = db.now() if now is None else now
    row = conn.execute("SELECT id FROM deploys WHERE id = ? AND site_id = ?", (deploy_id, site["id"])).fetchone()
    if row is None:
        raise SiteError("not_found")
    with store.lock(site["name"]):
        store.activate(site["name"], deploy_id)
        conn.execute(
            "UPDATE sites SET current_deploy_id = ?, updated_at = ? WHERE id = ?", (deploy_id, ts, site["id"])
        )


def rename(conn: sqlite3.Connection, store: SiteStore, site: sqlite3.Row, new_name: str, now: int | None = None) -> str:
    ts = db.now() if now is None else now
    new = names.normalize_name(new_name)
    if new == site["name"]:
        return new
    if not names.is_valid_name(new):
        raise SiteError("name_invalid")
    if get_site(conn, new) is not None:
        raise SiteError("name_taken")
    with store.lock(site["name"]):
        try:
            store.rename(site["name"], new)
        except FileExistsError:
            raise SiteError("name_taken") from None
        try:
            conn.execute("UPDATE sites SET name = ?, updated_at = ? WHERE id = ?", (new, ts, site["id"]))
        except sqlite3.IntegrityError:
            store.rename(new, site["name"])
            raise SiteError("name_taken") from None
    return new


def delete(conn: sqlite3.Connection, store: SiteStore, site: sqlite3.Row) -> None:
    with store.lock(site["name"]):
        with db.transaction(conn):
            conn.execute("DELETE FROM deploys WHERE site_id = ?", (site["id"],))
            conn.execute("DELETE FROM sites WHERE id = ?", (site["id"],))
        store.delete(site["name"])
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/deploys/store.py backend/app/deploys/service.py backend/tests/test_store.py backend/tests/test_service.py
git commit -m "feat(deploys): atomic site store, publish/restore/rename/delete service

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 10: App shell — dependencies, rendering, frontend foundation

This task wires everything into the real app factory and lays down the complete visual system (CSS, JS, base template). Later tasks only add routes and page templates.

**Files:**
- Create: `backend/app/deps.py`, `backend/app/web.py`, `backend/app/devsites.py`
- Create: `backend/app/routes/__init__.py` (empty), `backend/app/routes/pages.py`, `backend/app/routes/auth.py`, `backend/app/routes/sites.py`, `backend/app/routes/admin.py` (each just `router = APIRouter()` for now), `backend/app/routes/internal.py`
- Replace: `backend/app/main.py`
- Create: `frontend/templates/base.html`, `frontend/templates/error.html`, `frontend/templates/_icons.html`
- Create: `frontend/static/css/app.css`, `frontend/static/js/app.js`, `frontend/static/favicon.svg`, `frontend/static/site-404.html`, `frontend/static/fonts/LICENSE.txt`, `frontend/tools/fetch_fonts.py`
- Generated (controller runs it beforehand, see Step 1): `frontend/static/css/fonts.css`, `frontend/static/fonts/*.woff2`
- Create: `backend/tests/helpers.py`; Replace: `backend/tests/conftest.py`; Test: `backend/tests/test_app_shell.py`

**Interfaces:**
- Produces (`deps`): `NotSignedIn`, `Forbidden`, `NotFound` exceptions; dependencies `get_conn`, `current_user -> Row | None`, `require_user -> Row`, `require_deployer -> Row`, `require_admin -> Row`.
- Produces (`web`): `settings_of(request) -> Settings`, `wants_json(request) -> bool`, `safe_next(value) -> str`, `redirect(url, *, settings=None, flash: tuple[str, str] | None = None) -> RedirectResponse` (303), `set_flash(response, settings, kind, text)`, `pop_flash(request)`, `render(request, template, *, status=200, user=None, **context) -> HTMLResponse`. Templates always get: `lang, langs, lang_labels, t, size, ago, settings, asset_v, flash, user, is_admin, js_strings, path`.
- Produces (`devsites`): `resolve_site_file(current: Path, url_path: str) -> Path | None`, `serve_site_file(settings, name, url_path) -> Response`.
- Produces (`main`): `create_app(settings=None, *, mailer=None, google=None)`; `app.state.settings/mailer/google/store`.
- Produces (test helpers): `APP`, `ADMIN`, `FakeGoogle`, `last_code(mailer)`, `sign_in(client, mailer, email)`, `make_zip(files)`, `upload_zip(client, files, url="/deploy", json=True)`, `publish_site(app, conn, files=None) -> str`, `new_client(app)`.
- JS hooks used by later templates: `#drop` form with `.drop-idle`, `.drop-busy`, `.drop-status`, `.bar > span`, `.drop-error`, `.drop-cancel`; `[data-copy]`; `[data-confirm]`; `[data-wait]`; `.code-input`; `#g-btn[data-client-id][data-next][data-locale]` + `.g-error`.

- [ ] **Step 1: Fonts (controller step, needs the user's permission to download ~90 KB from fonts.gstatic.com)**

Create `frontend/tools/fetch_fonts.py`:
```python
"""One-off: download IBM Plex Sans 400/600 WOFF2 (Latin, Cyrillic, Cyrillic Ext — covers Kazakh)
and write frontend/static/css/fonts.css. Run from the repo root: python3 frontend/tools/fetch_fonts.py
"""
import re
import urllib.request
from pathlib import Path

CSS_URL = "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap"
SUBSETS = ("latin", "cyrillic", "cyrillic-ext")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
STATIC = Path(__file__).resolve().parents[1] / "static"
BLOCK = re.compile(r"/\* ([a-z-]+) \*/\s*(@font-face \{.*?\})", re.S)


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main() -> None:
    css = get(CSS_URL).decode()
    (STATIC / "fonts").mkdir(parents=True, exist_ok=True)
    saved: dict[str, str] = {}
    out = ["/* IBM Plex Sans (c) IBM Corp., SIL Open Font License 1.1. Generated by frontend/tools/fetch_fonts.py */"]
    for subset, block in BLOCK.findall(css):
        if subset not in SUBSETS:
            continue
        weight = re.search(r"font-weight: (\d+)", block).group(1)
        url = re.search(r"url\((https://[^)]+\.woff2)\)", block).group(1)
        if url not in saved:
            saved[url] = f"plex-sans-{weight}-{subset}.woff2"
            (STATIC / "fonts" / saved[url]).write_bytes(get(url))
        out.append(block.replace(url, f"../fonts/{saved[url]}"))
    (STATIC / "css" / "fonts.css").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"{len(out) - 1} font faces, {len(saved)} files")


if __name__ == "__main__":
    main()
```

`frontend/static/fonts/LICENSE.txt`:
```text
IBM Plex Sans — Copyright © 2017 IBM Corp. with Reserved Font Name "Plex".
Licensed under the SIL Open Font License, Version 1.1: https://openfontlicense.org
```

The controller (not a subagent) runs `mkdir -p frontend/static/css && python3 frontend/tools/fetch_fonts.py` after the user approves the download. If the download is declined or fails, create `frontend/static/css/fonts.css` containing only `/* System fonts only. */` — the CSS falls back to system fonts. Subagents: confirm `frontend/static/css/fonts.css` exists; do not run the script.

- [ ] **Step 2: Write test helpers, the final conftest and failing shell tests**

`backend/tests/helpers.py`:
```python
"""Shared test helpers (import as `from tests.helpers import ...`)."""
import io
import re
import zipfile

from fastapi.testclient import TestClient

from app import users
from app.auth.google import GoogleAuthError, GoogleIdentity
from app.deploys import service
from app.deploys.ingest import Limits, stage_files

APP = "http://localhost:8000"
ADMIN = "admin@zerde.ai"


class FakeGoogle:
    def __init__(self) -> None:
        self.tokens: dict[str, GoogleIdentity] = {}

    def verify(self, token: str) -> GoogleIdentity:
        try:
            return self.tokens[token]
        except KeyError:
            raise GoogleAuthError("unknown token") from None


def new_client(app) -> TestClient:
    client = TestClient(app, base_url=APP)
    client.headers["origin"] = APP
    return client


def last_code(mailer) -> str:
    return re.search(r"\b(\d{6})\b", mailer.sent[-1].body).group(1)


def sign_in(client, mailer, email: str) -> None:
    response = client.post("/auth/email/start", data={"email": email, "next": "/"}, follow_redirects=False)
    assert response.status_code == 303, response.text
    response = client.post("/auth/email/verify", data={"code": last_code(mailer), "next": "/"}, follow_redirects=False)
    assert response.status_code == 303, response.text


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def upload_zip(client, files: dict[str, bytes], url: str = "/deploy", json: bool = True):
    headers = {"accept": "application/json"} if json else {}
    return client.post(
        url, files={"files": ("site.zip", make_zip(files), "application/zip")}, headers=headers, follow_redirects=False
    )


def publish_site(app, conn, files: dict[str, bytes] | None = None) -> str:
    store = app.state.store
    files = files or {"index.html": b"hello"}
    staged = stage_files([(path, io.BytesIO(data)) for path, data in files.items()], store.new_staging_dir(), Limits())
    owner = users.upsert_user(conn, ADMIN)
    name, _ = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=staged)
    return name
```

Replace `backend/tests/conftest.py`:
```python
import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth.mailer import ConsoleMailer
from app.config import Settings
from app.main import create_app
from tests.helpers import ADMIN, APP, FakeGoogle


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        admin_emails=frozenset({ADMIN}),
        google_client_id="test-client.apps.googleusercontent.com",
        max_upload_mb=5,
        max_files=50,
        max_file_mb=2,
    )


@pytest.fixture
def mailer():
    return ConsoleMailer()


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def app(settings, mailer, google):
    return create_app(settings, mailer=mailer, google=google)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=APP) as c:
        c.headers["origin"] = APP
        yield c


@pytest.fixture
def conn(app, settings):
    c = db.connect(settings.db_path)
    yield c
    c.close()
```

`backend/tests/test_app_shell.py`:
```python
from tests.helpers import publish_site


def test_unknown_page_renders_translated_404(client):
    response = client.get("/nope?lang=ru")
    assert response.status_code == 404
    assert '<html lang="ru">' in response.text
    assert "Страница не найдена" in response.text


def test_language_choice_is_remembered(client):
    client.get("/nope?lang=kk")
    response = client.get("/nope")
    assert '<html lang="kk">' in response.text
    assert "Бет табылмады" in response.text


def test_accept_language_is_used(client):
    response = client.get("/nope", headers={"accept-language": "ru-RU,ru;q=0.9"})
    assert '<html lang="ru">' in response.text


def test_cross_origin_post_is_blocked(client):
    assert client.post("/healthz", headers={"origin": "http://evil.localhost:8000"}).status_code == 403
    assert client.post("/healthz").status_code == 405


def test_pages_link_versioned_assets_and_strings(client):
    response = client.get("/nope")
    assert "/static/css/app.css?v=" in response.text
    assert 'id="zd-strings"' in response.text


def test_static_files_are_cached(client):
    response = client.get("/static/css/app.css")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_json_errors(client):
    response = client.get("/nope", headers={"accept": "application/json"})
    assert response.status_code == 404
    assert response.json() == {"error": "Page not found"}


def test_tls_ask(client, app, conn):
    name = publish_site(app, conn)
    assert client.get(f"/internal/tls-ask?domain={name}.localhost").status_code == 200
    assert client.get("/internal/tls-ask?domain=missing-site.localhost").status_code == 404
    assert client.get(f"/internal/tls-ask?domain={name}.evil.test").status_code == 404
    assert client.get("/internal/tls-ask?domain=localhost").status_code == 404


def test_dev_site_serving(client, app, conn):
    name = publish_site(
        app, conn, {"index.html": b"home", "about.html": b"about", "docs/index.html": b"docs", "404.html": b"custom"}
    )
    base = f"http://{name}.localhost:8000"
    assert client.get(base + "/").text == "home"
    assert client.get(base + "/about").text == "about"
    assert client.get(base + "/docs/").text == "docs"
    assert client.get(base + "/docs").text == "docs"
    missing = client.get(base + "/missing")
    assert missing.status_code == 404 and missing.text == "custom"
    escape = client.get(base + "/%2e%2e/%2e%2e/app.db")
    assert escape.status_code == 404
    unknown = client.get("http://missing-site.localhost:8000/")
    assert unknown.status_code == 404 and "404" in unknown.text
```

- [ ] **Step 3: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_app_shell.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.helpers'` / import errors from `app.main`.

- [ ] **Step 4: Implement the backend shell**

`backend/app/web.py`:
```python
"""Template rendering, redirects and one-shot flash messages."""
from __future__ import annotations

import hashlib
from functools import partial

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from . import access, db, i18n, signing
from .config import FRONTEND_DIR, MB, Settings

FLASH_COOKIE = "zd_flash"
FLASH_TTL = 60
templates = Jinja2Templates(directory=str(FRONTEND_DIR / "templates"))


def _asset_version() -> str:
    digest = hashlib.sha256()
    for path in sorted((FRONTEND_DIR / "static").rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def set_flash(response: Response, settings: Settings, kind: str, text: str) -> None:
    response.set_cookie(
        FLASH_COOKIE, signing.sign(f"{kind}:{text}", settings.secret_key), max_age=FLASH_TTL, path="/",
        secure=settings.https, httponly=True, samesite="lax",
    )


def pop_flash(request: Request) -> dict[str, str] | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    value = signing.unsign(raw, settings_of(request).secret_key, max_age=FLASH_TTL)
    if not value or ":" not in value:
        return None
    kind, text = value.split(":", 1)
    return {"kind": kind, "text": text}


def redirect(url: str, *, settings: Settings | None = None, flash: tuple[str, str] | None = None) -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    if flash is not None and settings is not None:
        set_flash(response, settings, *flash)
    return response


def js_strings(lang: str, settings: Settings) -> dict[str, object]:
    t = partial(i18n.t, lang)
    return {
        "copied": t("js.copied"),
        "reading": t("js.reading"),
        "uploading": t("js.uploading"),
        "processing": t("js.processing"),
        "failed": t("js.failed"),
        "empty": t("upload.error.empty"),
        "tooBig": t("upload.error.too_big", mb=settings.max_upload_mb),
        "tooMany": t("upload.error.too_many_files", n=settings.max_files),
        "maxBytes": settings.max_upload_mb * MB,
        "maxFiles": settings.max_files,
        "units": [t("unit.b"), t("unit.kb"), t("unit.mb"), t("unit.gb")],
        "dec": "." if lang == "en" else ",",
    }


def render(request: Request, template: str, *, status: int = 200, user=None, **context: object) -> HTMLResponse:
    settings = settings_of(request)
    lang = getattr(request.state, "lang", i18n.DEFAULT_LANG)
    flash = pop_flash(request)
    base = {
        "lang": lang,
        "langs": i18n.LANGS,
        "lang_labels": i18n.LANG_LABELS,
        "t": partial(i18n.t, lang),
        "size": partial(i18n.format_size, lang),
        "ago": lambda ts: i18n.format_ago(lang, max(0, db.now() - int(ts or 0))),
        "settings": settings,
        "asset_v": ASSET_VERSION,
        "flash": flash,
        "user": user,
        "is_admin": bool(user) and access.is_admin(settings, user["email"]),
        "js_strings": js_strings(lang, settings),
        "path": request.url.path,
    }
    response = templates.TemplateResponse(request, template, {**base, **context}, status_code=status)
    if flash:
        response.delete_cookie(FLASH_COOKIE, path="/")
    return response
```

`backend/app/deps.py`:
```python
"""FastAPI dependencies shared by the route modules."""
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, Request

from . import access, db
from .auth import sessions
from .web import settings_of


class NotSignedIn(Exception):
    """No valid session."""


class Forbidden(Exception):
    """Signed in, but not allowed."""


class NotFound(Exception):
    """Missing, or not visible to this user."""


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(settings_of(request).db_path)
    try:
        yield conn
    finally:
        conn.close()


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> sqlite3.Row | None:
    token = request.cookies.get(settings_of(request).session_cookie)
    return sessions.user_for_token(conn, token) if token else None


def require_user(user: sqlite3.Row | None = Depends(current_user)) -> sqlite3.Row:
    if user is None:
        raise NotSignedIn()
    return user


def require_deployer(
    request: Request, user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)
) -> sqlite3.Row:
    if not access.can_deploy(conn, settings_of(request), user["email"]):
        raise Forbidden()
    return user


def require_admin(request: Request, user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    if not access.is_admin(settings_of(request), user["email"]):
        raise Forbidden()
    return user
```

`backend/app/devsites.py`:
```python
"""Development only: serve deployed sites from the app. Production uses Caddy (deploys/sites.caddy)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from starlette.responses import FileResponse, HTMLResponse, Response

from .config import FRONTEND_DIR, Settings
from .deploys import names

SITE_404 = FRONTEND_DIR / "static" / "site-404.html"
HEADERS = {"Cache-Control": "public, max-age=0, must-revalidate", "X-Content-Type-Options": "nosniff"}


def resolve_site_file(current: Path, url_path: str) -> Path | None:
    root = current.resolve()
    rel = unquote(url_path).lstrip("/")
    candidates = [rel, f"{rel}/index.html", f"{rel}.html"] if rel else ["index.html"]
    for candidate in candidates:
        path = (root / candidate).resolve()
        if path != root and root not in path.parents:
            return None
        if path.is_file():
            return path
    return None


def serve_site_file(settings: Settings, name: str, url_path: str) -> Response:
    current = settings.sites_dir / name / "current"
    if not names.is_valid_name(name) or not current.is_dir():
        return _not_found(None)
    path = resolve_site_file(current, url_path)
    return FileResponse(path, headers=HEADERS) if path else _not_found(current)


def _not_found(current: Path | None) -> Response:
    if current is not None and (current / "404.html").is_file():
        return FileResponse(current / "404.html", status_code=404, headers=HEADERS)
    return HTMLResponse(SITE_404.read_text(encoding="utf-8"), status_code=404)
```

`backend/app/routes/internal.py`:
```python
"""Endpoints for Caddy on the private Docker network. Caddy answers 404 for /internal/* publicly."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from ..deploys import names, service
from ..deps import get_conn
from ..web import settings_of

router = APIRouter()


@router.get("/internal/tls-ask", include_in_schema=False)
def tls_ask(request: Request, domain: str = "", conn: sqlite3.Connection = Depends(get_conn)) -> PlainTextResponse:
    """Caddy on-demand TLS: issue a certificate only for sites that exist."""
    suffix = "." + settings_of(request).sites_domain.split(":")[0]
    host = domain.strip().lower().rstrip(".")
    if host.endswith(suffix):
        name = host[: -len(suffix)]
        if names.is_valid_name(name) and service.get_site(conn, name) is not None:
            return PlainTextResponse("ok")
    return PlainTextResponse("unknown", status_code=404)
```

Create `backend/app/routes/__init__.py` (empty) and these four files, each exactly:
```python
"""<one-line purpose: "Dashboard page." / "Sign-in pages and actions." / "Upload, site page and site actions." / "Allowlist administration.">"""
from fastapi import APIRouter

router = APIRouter()
```
(`pages.py` → "Dashboard page.", `auth.py` → "Sign-in pages and actions.", `sites.py` → "Upload, site page and site actions.", `admin.py` → "Allowlist administration.")

Replace `backend/app/main.py`:
```python
"""Application factory. Run with: uvicorn app.main:create_app --factory"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from . import db, i18n
from .auth import sessions
from .auth.google import GoogleVerifier
from .auth.mailer import make_mailer
from .config import FRONTEND_DIR, Settings, load_settings
from .deploys.store import SiteStore
from .deps import Forbidden, NotFound, NotSignedIn
from .devsites import serve_site_file
from .routes import admin, auth, internal, pages, sites
from .web import redirect, render, wants_json

ERROR_KEYS = {403: "error.forbidden", 404: "error.not_found", 405: "error.not_found"}


class CachedStatic(StaticFiles):
    """Assets are linked with ?v=<content hash>, so browsers may keep them for a year."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app(settings: Settings | None = None, *, mailer=None, google=None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(level=logging.INFO)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.mailer = mailer or make_mailer(settings)
    app.state.google = google or GoogleVerifier(settings.google_client_id)
    app.state.store = SiteStore(settings.sites_dir, settings.tmp_dir)
    app.state.store.cleanup_temp()
    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
    finally:
        conn.close()

    app.mount("/static", CachedStatic(directory=FRONTEND_DIR / "static"), name="static")
    for module in (pages, auth, sites, admin, internal):
        app.include_router(module.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        host = request.headers.get("host", "").lower()
        if (
            settings.serve_sites_in_app
            and host != settings.app_domain
            and host.endswith("." + settings.sites_domain)
        ):
            name = host[: -len(settings.sites_domain) - 1]
            return await run_in_threadpool(serve_site_file, settings, name, request.url.path)
        if not sessions.origin_allowed(request.method, request.headers, settings.app_origin):
            return PlainTextResponse("Cross-origin request blocked", status_code=403)
        query_lang = request.query_params.get("lang")
        request.state.lang = i18n.negotiate(
            query_lang, request.cookies.get(i18n.LANG_COOKIE), request.headers.get("accept-language")
        )
        response = await call_next(request)
        if query_lang in i18n.LANGS:
            response.set_cookie(
                i18n.LANG_COOKIE, query_lang, max_age=365 * 86400, path="/",
                secure=settings.https, httponly=True, samesite="lax",
            )
        return response

    @app.exception_handler(NotSignedIn)
    async def handle_not_signed_in(request: Request, exc: NotSignedIn) -> Response:
        if wants_json(request):
            return JSONResponse({"error": i18n.t(_lang(request), "error.signed_out")}, status_code=401)
        target = request.url.path if request.method == "GET" else "/"
        return redirect(f"/login?next={quote(target)}")

    @app.exception_handler(Forbidden)
    async def handle_forbidden(request: Request, exc: Forbidden) -> Response:
        return _error(request, 403, "error.forbidden")

    @app.exception_handler(NotFound)
    async def handle_not_found(request: Request, exc: NotFound) -> Response:
        return _error(request, 404, "error.not_found")

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> Response:
        return _error(request, exc.status_code, ERROR_KEYS.get(exc.status_code, "error.generic"))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> Response:
        return _error(request, 400, "error.generic")

    return app


def _lang(request: Request) -> str:
    return getattr(request.state, "lang", i18n.DEFAULT_LANG)


def _error(request: Request, status: int, key: str) -> Response:
    request.state.lang = _lang(request)
    message = i18n.t(request.state.lang, key)
    if wants_json(request):
        return JSONResponse({"error": message}, status_code=status)
    return render(request, "error.html", status=status, message=message, code=status)
```

- [ ] **Step 5: Implement the frontend foundation**

`frontend/templates/_icons.html`:
```html
{% macro copy_icon() %}<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>{% endmacro %}
{% macro upload_icon() %}<svg class="drop-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 15V4"/><path d="m7 9 5-5 5 5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg>{% endmacro %}
```

`frontend/templates/base.html`:
```html
<!doctype html>
<html lang="{{ lang }}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{% block title %}{% endblock %} — zerde/deploy</title>
<link rel="icon" href="/static/favicon.svg?v={{ asset_v }}" type="image/svg+xml">
<link rel="stylesheet" href="/static/css/fonts.css?v={{ asset_v }}">
<link rel="stylesheet" href="/static/css/app.css?v={{ asset_v }}">
</head>
<body>
<a class="skip" href="#main">{{ t("nav.skip") }}</a>
<header class="top">
  <div class="wrap top-in">
    <a class="mark" href="/">zerde<span>/</span>deploy</a>
    {% if user %}
    <nav class="top-nav" aria-label="{{ t('nav.main') }}">
      <a href="/"{% if path == "/" or path.startswith("/sites/") %} aria-current="page"{% endif %}>{{ t("nav.sites") }}</a>
      {% if is_admin %}<a href="/admin"{% if path.startswith("/admin") %} aria-current="page"{% endif %}>{{ t("nav.admin") }}</a>{% endif %}
    </nav>
    {% endif %}
    <div class="top-end">
      <nav class="langs" aria-label="{{ t('lang.label') }}">
        {%- for code in langs %}<a href="?lang={{ code }}" hreflang="{{ code }}" lang="{{ code }}"{% if code == lang %} aria-current="true"{% endif %}>{{ lang_labels[code] }}</a>{% endfor -%}
      </nav>
      {% if user %}
      <details class="menu">
        <summary aria-label="{{ t('nav.account') }}"><span class="avatar" aria-hidden="true">{{ user.email[:1]|upper }}</span></summary>
        <div class="menu-pop">
          <p class="menu-email">{{ user.email }}</p>
          <nav class="menu-links" aria-label="{{ t('nav.main') }}">
            <a href="/">{{ t("nav.sites") }}</a>
            {% if is_admin %}<a href="/admin">{{ t("nav.admin") }}</a>{% endif %}
          </nav>
          <form method="post" action="/auth/logout"><button class="btn btn-secondary btn-block" type="submit">{{ t("nav.sign_out") }}</button></form>
        </div>
      </details>
      {% endif %}
    </div>
  </div>
</header>
<main id="main" class="wrap">
{% if flash %}<p class="notice {{ 'notice-error' if flash.kind == 'error' else 'notice-ok' }}" role="{{ 'alert' if flash.kind == 'error' else 'status' }}">{{ flash.text }}</p>{% endif %}
{% block main %}{% endblock %}
</main>
<script type="application/json" id="zd-strings">{{ js_strings|tojson }}</script>
{% block scripts %}<script src="/static/js/app.js?v={{ asset_v }}" defer></script>{% endblock %}
</body>
</html>
```

`frontend/templates/error.html`:
```html
{% extends "base.html" %}
{% block title %}{{ message }}{% endblock %}
{% block main %}
<section class="auth">
  <p class="eyebrow mono">{{ code }}</p>
  <h1>{{ message }}</h1>
  <p class="lead"><a href="/">{{ t("error.back_home") }}</a></p>
</section>
{% endblock %}
```

`frontend/static/favicon.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#17181b"/><path d="M19.5 7 12.5 25" stroke="#4cc38a" stroke-width="3" stroke-linecap="round"/></svg>
```

`frontend/static/site-404.html` (served for unknown sites/pages without their own 404.html, by the dev server and by Caddy):
```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>404</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#f6f5f1;color:#17181b}
@media (prefers-color-scheme:dark){body{background:#111213;color:#ecebe6}}
main{padding:24px;max-width:420px}h1{margin:0 0 12px;font:600 14px ui-monospace,Menlo,Consolas,monospace}p{margin:0 0 4px}
</style>
</head>
<body>
<main>
<h1>404</h1>
<p>This site or page doesn't exist.</p>
<p lang="ru">Такого сайта или страницы нет.</p>
<p lang="kk">Мұндай сайт немесе бет жоқ.</p>
</main>
</body>
</html>
```

`frontend/static/css/app.css`:
```css
/* zerde/deploy — hand-written CSS, no framework. Tokens, base, layout, components. */

:root {
  --bg: #f6f5f1;
  --surface: #ffffff;
  --ink: #17181b;
  --ink-2: #5b5d63;
  --line: #e4e2db;
  --line-2: #cdcac0;
  --accent: #1d6f47;
  --accent-hover: #175c3b;
  --accent-ink: #ffffff;
  --accent-soft: #e3efe7;
  --danger: #b42318;
  --r-sm: 6px;
  --r: 10px;
  --s1: 4px;
  --s2: 8px;
  --s3: 12px;
  --s4: 16px;
  --s5: 24px;
  --s6: 32px;
  --s7: 48px;
  --sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", Menlo, Consolas, monospace;
  --ease: 150ms cubic-bezier(0.2, 0, 0, 1);
  color-scheme: light dark;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111213;
    --surface: #18191b;
    --ink: #ecebe6;
    --ink-2: #a3a29c;
    --line: #27282b;
    --line-2: #3a3b3f;
    --accent: #4cc38a;
    --accent-hover: #6dd3a1;
    --accent-ink: #06130c;
    --accent-soft: #14281c;
    --danger: #f97066;
  }
}

/* Base */
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--ink); font: 400 16px/1.5 var(--sans); -webkit-font-smoothing: antialiased; }
a { color: inherit; text-decoration-thickness: 1px; text-underline-offset: 3px; }
a:hover { color: var(--accent); }
h1, h2, p, ol, ul { margin: 0; }
h1 { font-size: 28px; line-height: 1.2; font-weight: 600; letter-spacing: -0.015em; }
h2 { font-size: 17px; line-height: 1.3; font-weight: 600; }
button, input { font: inherit; color: inherit; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
[hidden] { display: none !important; }
.mono { font-family: var(--mono); font-size: 0.93em; }
.muted { color: var(--ink-2); }
.visually-hidden { position: absolute !important; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.skip { position: absolute; left: -9999px; top: var(--s2); z-index: 20; padding: var(--s2) var(--s3); background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--r-sm); }
.skip:focus { left: var(--s2); }
.wrap { width: 100%; max-width: 880px; margin: 0 auto; padding: 0 var(--s4); }
main { padding-top: var(--s6); padding-bottom: var(--s7); }

/* Header */
.top { border-bottom: 1px solid var(--line); }
.top-in { display: flex; align-items: center; gap: var(--s5); min-height: 56px; }
.mark { font: 600 15px/1 var(--mono); letter-spacing: -0.02em; text-decoration: none; white-space: nowrap; }
.mark span { color: var(--accent); }
.top-nav { display: flex; gap: var(--s4); }
.top-nav a { padding: var(--s2) 0; font-size: 15px; color: var(--ink-2); text-decoration: none; }
.top-nav a:hover { color: var(--ink); }
.top-nav a[aria-current="page"] { color: var(--ink); box-shadow: inset 0 -2px 0 var(--ink); }
.top-end { display: flex; align-items: center; gap: var(--s3); margin-left: auto; }
.langs { display: flex; border: 1px solid var(--line-2); border-radius: var(--r-sm); overflow: hidden; }
.langs a { min-width: 44px; padding: 6px var(--s2); font-size: 13px; font-weight: 500; text-align: center; color: var(--ink-2); text-decoration: none; }
.langs a + a { border-left: 1px solid var(--line-2); }
.langs a:hover { color: var(--ink); }
.langs a[aria-current] { background: var(--ink); color: var(--bg); }
.menu { position: relative; }
.menu > summary { display: block; list-style: none; cursor: pointer; border-radius: 50%; }
.menu > summary::-webkit-details-marker { display: none; }
.avatar { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%; background: var(--ink); color: var(--bg); font-size: 14px; font-weight: 600; }
.menu-pop { position: absolute; right: 0; top: calc(100% + var(--s2)); z-index: 10; display: grid; gap: var(--s3); width: 240px; padding: var(--s3); background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--r); }
.menu-email { font-size: 14px; color: var(--ink-2); overflow-wrap: anywhere; }
.menu-links { display: none; }
.menu-links a { padding: var(--s1) 0; text-decoration: none; }

/* Buttons */
.btn { display: inline-flex; align-items: center; justify-content: center; gap: var(--s2); min-height: 40px; padding: 0 var(--s4); border: 1px solid transparent; border-radius: var(--r-sm); font-size: 15px; font-weight: 500; line-height: 1; text-decoration: none; white-space: nowrap; cursor: pointer; transition: background-color var(--ease), border-color var(--ease), color var(--ease); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn svg { flex: none; width: 16px; height: 16px; }
.btn-primary { background: var(--accent); color: var(--accent-ink); }
.btn-primary:hover:not(:disabled) { background: var(--accent-hover); color: var(--accent-ink); }
.btn-secondary { background: var(--surface); border-color: var(--line-2); color: var(--ink); }
.btn-secondary:hover:not(:disabled) { border-color: var(--ink); color: var(--ink); }
.btn-quiet { min-height: 36px; padding: 0 var(--s3); background: transparent; color: var(--ink-2); }
.btn-quiet:hover:not(:disabled) { background: var(--line); color: var(--ink); }
.btn-danger { background: var(--surface); border-color: var(--danger); color: var(--danger); }
.btn-danger:hover:not(:disabled) { background: var(--danger); color: var(--surface); }
.btn-block { width: 100%; }
.btn-link { padding: 0; border: 0; background: none; color: var(--ink-2); font-size: 15px; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.btn-link:hover:not(:disabled) { color: var(--ink); }
.btn-link:disabled { text-decoration: none; cursor: default; }
label.btn:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }

/* Forms */
.stack { display: grid; gap: var(--s4); }
.field { display: grid; gap: var(--s2); }
.field > label { font-size: 15px; font-weight: 500; }
.input { width: 100%; min-height: 44px; padding: 0 var(--s3); background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--r-sm); transition: border-color var(--ease), box-shadow var(--ease); }
.input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.hint { font-size: 14px; color: var(--ink-2); }
.code-input { max-width: 220px; font: 500 26px/1 var(--mono); letter-spacing: 0.3em; text-align: center; }
.row-form { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); }
.row-form > .input, .row-form > .affix { flex: 1 1 240px; width: auto; }
.affix { display: flex; align-items: center; min-height: 44px; background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--r-sm); transition: border-color var(--ease), box-shadow var(--ease); }
.affix:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.affix > .input { flex: 1; min-width: 0; min-height: 42px; border: 0; background: transparent; box-shadow: none; }
.suffix { padding-right: var(--s3); color: var(--ink-2); white-space: nowrap; }

/* Notices */
.notice { margin-bottom: var(--s5); padding: var(--s3) var(--s4); background: var(--surface); border: 1px solid var(--line); border-left: 3px solid var(--accent); border-radius: var(--r-sm); font-size: 15px; }
.notice-error { border-left-color: var(--danger); }

/* Sign-in and message pages */
.auth { max-width: 400px; margin: var(--s6) auto 0; }
.auth h1 { margin-bottom: var(--s2); }
.auth .lead { margin-bottom: var(--s5); color: var(--ink-2); overflow-wrap: anywhere; }
.or { display: flex; align-items: center; gap: var(--s3); margin: var(--s5) 0; font-size: 14px; color: var(--ink-2); }
.or::before, .or::after { content: ""; flex: 1; border-top: 1px solid var(--line); }
.g-slot { min-height: 44px; }
.g-error { margin: var(--s3) 0 0; }
.auth-foot { display: flex; flex-wrap: wrap; justify-content: space-between; gap: var(--s3); margin-top: var(--s5); font-size: 15px; }
.auth-foot a { color: var(--ink-2); }
.eyebrow { margin-bottom: var(--s2); color: var(--ink-2); }

/* Page heads */
.page-head { display: grid; gap: var(--s2); margin-bottom: var(--s5); }
.crumb { margin-bottom: var(--s3); font-size: 15px; }
.crumb a { color: var(--ink-2); text-decoration: none; }
.crumb a:hover { color: var(--ink); }
.link-line { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s2); }
.link-line a { font-family: var(--mono); font-size: 15px; overflow-wrap: anywhere; }

/* Drop zone */
.drop { padding: var(--s6) var(--s5); background: var(--surface); border: 1.5px dashed var(--line-2); border-radius: var(--r); transition: border-color var(--ease), background-color var(--ease); }
.drop.is-over { border-color: var(--accent); background: var(--accent-soft); }
.drop-icon { display: block; width: 28px; height: 28px; margin-bottom: var(--s3); color: var(--ink-2); }
.drop-title { font-size: 18px; font-weight: 600; }
.drop-hint { margin-top: var(--s1); font-size: 14px; color: var(--ink-2); }
.drop-buttons { display: flex; flex-wrap: wrap; gap: var(--s2); margin-top: var(--s4); }
.drop-error { margin-top: var(--s4); font-size: 15px; color: var(--danger); }
.drop-status { font-size: 15px; font-variant-numeric: tabular-nums; }
.bar { height: 4px; margin: var(--s3) 0 var(--s4); background: var(--line); border-radius: 2px; overflow: hidden; }
.bar > span { display: block; width: 0; height: 100%; background: var(--accent); transition: width 120ms linear; }

/* Lists */
.list { margin-top: var(--s3); padding: 0; list-style: none; border-top: 1px solid var(--line); }
.sites { margin-top: var(--s6); }
.site-row { display: flex; align-items: center; gap: var(--s3); padding: var(--s3) 0; border-bottom: 1px solid var(--line); }
.site-main { display: grid; flex: 1; gap: 2px; min-width: 0; }
.site-link { overflow: hidden; font-family: var(--mono); font-size: 15px; text-decoration: none; text-overflow: ellipsis; white-space: nowrap; }
.site-link:hover { text-decoration: underline; }
.site-meta { font-size: 13px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.site-actions { display: flex; flex: none; gap: var(--s1); }
.empty { padding: var(--s4) 0; color: var(--ink-2); border-bottom: 1px solid var(--line); }
p.empty { margin-top: var(--s3); border-top: 1px solid var(--line); }
.entry { display: flex; align-items: center; justify-content: space-between; gap: var(--s3); min-height: 48px; border-bottom: 1px solid var(--line); }
.entry > .mono { overflow-wrap: anywhere; }

/* Panels */
.panel { margin-top: var(--s6); padding-top: var(--s5); border-top: 1px solid var(--line); }
.panel > h2 { margin-bottom: var(--s2); }
.panel > .hint { margin-top: var(--s2); }
.panel-danger > h2 { color: var(--danger); }
.panel-danger > .hint { margin: 0 0 var(--s3); }
.version { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s1) var(--s4); padding: var(--s3) 0; border-bottom: 1px solid var(--line); }
.version-id { width: 48px; font-family: var(--mono); font-size: 14px; color: var(--ink-2); }
.version-main { display: grid; flex: 1; gap: 2px; min-width: 180px; }
.version-meta { font-size: 13px; color: var(--ink-2); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.tag { padding: 5px var(--s2); border: 1px solid currentColor; border-radius: 999px; font-size: 12px; font-weight: 600; line-height: 1; letter-spacing: 0.05em; text-transform: uppercase; color: var(--accent); }

@media (max-width: 600px) {
  h1 { font-size: 24px; }
  .top-in { gap: var(--s3); }
  .top-nav { display: none; }
  .menu-links { display: grid; }
  .drop { padding: var(--s5) var(--s4); }
  .site-actions .btn > span { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; }
}
```

`frontend/static/js/app.js`:
```js
"use strict";
// zerde/deploy — progressive enhancement for server-rendered pages. No dependencies.

const S = JSON.parse(document.getElementById("zd-strings")?.textContent || "{}");
const $ = (selector, root = document) => root.querySelector(selector);
const fmt = (text, params) => String(text).replace(/\{(\w+)\}/g, (match, key) => (key in params ? params[key] : match));
const SKIP = new Set([".git", "node_modules", "__MACOSX", ".DS_Store", "Thumbs.db"]);

function humanSize(bytes) {
  const units = S.units || ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const text = index === 0 || value >= 10 ? String(Math.round(value)) : value.toFixed(1);
  return `${text.replace(".", S.dec || ".")} ${units[index]}`;
}

/* Copy buttons: <button data-copy="text"><span>Label</span></button> */
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
  } catch {
    return;
  }
  const label = button.querySelector("span") || button;
  const original = label.textContent;
  label.textContent = S.copied;
  setTimeout(() => { label.textContent = original; }, 1500);
});

/* Type-the-name confirmation: <input data-confirm="name"> enables its form's submit button */
for (const input of document.querySelectorAll("[data-confirm]")) {
  const submit = input.form.querySelector("[type=submit]");
  const sync = () => { submit.disabled = input.value.trim().toLowerCase() !== input.dataset.confirm; };
  input.addEventListener("input", sync);
  sync();
}

/* Resend countdown: <button data-wait="seconds" data-label="…" data-wait-label="… {s} …"> */
{
  const button = $("[data-wait]");
  let left = Number(button?.dataset.wait || 0);
  const tick = () => {
    if (left <= 0) {
      button.disabled = false;
      button.textContent = button.dataset.label;
      return;
    }
    button.disabled = true;
    button.textContent = fmt(button.dataset.waitLabel, { s: left });
    left -= 1;
    setTimeout(tick, 1000);
  };
  if (button && left > 0) tick();
}

/* Code field: submit as soon as six digits are typed or pasted */
{
  const field = $(".code-input");
  field?.addEventListener("input", () => {
    if (field.value.replace(/\D/g, "").length === 6) field.form.requestSubmit();
  });
}

/* Sign in with Google (Google Identity Services, popup mode) */
{
  const slot = $("#g-btn");
  const onCredential = async ({ credential }) => {
    try {
      const response = await fetch("/auth/google", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ credential, next: slot.dataset.next }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      location.assign(data.redirect || "/");
    } catch {
      $(".g-error").hidden = false;
    }
  };
  let tries = 0;
  const start = () => {
    const gis = window.google?.accounts?.id;
    if (!gis) {
      if (++tries < 100) setTimeout(start, 100);
      return;
    }
    gis.initialize({ client_id: slot.dataset.clientId, callback: onCredential, ux_mode: "popup", itp_support: true });
    gis.renderButton(slot, {
      type: "standard",
      size: "large",
      text: "continue_with",
      shape: "rectangular",
      logo_alignment: "left",
      theme: matchMedia("(prefers-color-scheme: dark)").matches ? "filled_black" : "outline",
      width: Math.min(slot.clientWidth || 400, 400),
      locale: slot.dataset.locale,
    });
  };
  if (slot) start();
}

/* Drop zone: drag a folder, files or a .zip — or pick them — then upload with progress */
{
  const form = $("#drop");
  if (form) {
    const idle = $(".drop-idle", form);
    const busy = $(".drop-busy", form);
    const status = $(".drop-status", form);
    const bar = $(".bar > span", form);
    const error = $(".drop-error", form);
    let request = null;

    const show = (isBusy) => { idle.hidden = isBusy; busy.hidden = !isBusy; };
    const fail = (message) => {
      show(false);
      error.textContent = message;
      error.hidden = false;
    };

    const readEntry = (entry, prefix, out) => new Promise((resolve, reject) => {
      if (SKIP.has(entry.name)) return resolve();
      if (entry.isFile) {
        entry.file((file) => { out.push([prefix + file.name, file]); resolve(); }, reject);
        return;
      }
      const reader = entry.createReader();
      const nextBatch = () => reader.readEntries(async (entries) => {
        if (!entries.length) return resolve();
        try {
          for (const child of entries) await readEntry(child, `${prefix}${entry.name}/`, out);
          nextBatch();
        } catch (err) {
          reject(err);
        }
      }, reject);
      nextBatch();
    });

    const collect = async (transfer) => {
      const entries = [...transfer.items].map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
      if (!entries.length) return [...transfer.files].map((file) => [file.name, file]);
      const out = [];
      for (const entry of entries) await readEntry(entry, "", out);
      return out;
    };

    const upload = (files) => {
      error.hidden = true;
      if (!files.length) return fail(S.empty);
      const total = files.reduce((sum, [, file]) => sum + file.size, 0);
      if (files.length > S.maxFiles) return fail(S.tooMany);
      if (total > S.maxBytes) return fail(S.tooBig);
      const data = new FormData();
      for (const [path, file] of files) {
        data.append("files", file, file.name);
        data.append("paths", path);
      }
      show(true);
      bar.style.width = "0%";
      request = new XMLHttpRequest();
      request.open("POST", form.action);
      request.setRequestHeader("Accept", "application/json");
      request.upload.onprogress = (event) => {
        const all = event.lengthComputable ? event.total : total;
        const done = event.lengthComputable ? event.loaded : 0;
        bar.style.width = `${Math.round((done / Math.max(all, 1)) * 100)}%`;
        status.textContent = done >= all ? S.processing : fmt(S.uploading, { done: humanSize(done), total: humanSize(all) });
      };
      request.onload = () => {
        let body = {};
        try { body = JSON.parse(request.responseText); } catch { /* not JSON */ }
        if (request.status === 201) location.assign(body.manage_url);
        else fail(body.error || S.failed);
      };
      request.onerror = () => fail(S.failed);
      request.send(data);
    };

    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => event.preventDefault());
    form.addEventListener("dragover", (event) => {
      event.preventDefault();
      form.classList.add("is-over");
    });
    form.addEventListener("dragleave", (event) => {
      if (!form.contains(event.relatedTarget)) form.classList.remove("is-over");
    });
    form.addEventListener("drop", async (event) => {
      event.preventDefault();
      form.classList.remove("is-over");
      const pending = collect(event.dataTransfer);
      show(true);
      status.textContent = S.reading;
      bar.style.width = "0%";
      try {
        upload(await pending);
      } catch {
        fail(S.failed);
      }
    });
    for (const input of form.querySelectorAll("input[type=file]")) {
      input.addEventListener("change", () => {
        const files = [...input.files]
          .map((file) => [file.webkitRelativePath || file.name, file])
          .filter(([path]) => !path.split("/").some((part) => SKIP.has(part)));
        input.value = "";
        upload(files);
      });
    }
    $(".drop-cancel", form).addEventListener("click", () => {
      request?.abort();
      show(false);
    });
  }
}
```

- [ ] **Step 6: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend frontend
git commit -m "feat: app shell, i18n middleware, origin check, dev site serving and visual system

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 11: Sign-in — Google and email code

**Files:**
- Replace: `backend/app/routes/auth.py`
- Create: `frontend/templates/login.html`, `frontend/templates/code.html`
- Test: `backend/tests/test_auth_routes.py`

**Interfaces:**
- Consumes: `access.normalize_email`, `email_code.*`, `sessions.*`, `users.upsert_user`, `signing`, `GoogleAuthError`, `Message`, `deps.get_conn/current_user`, `web.render/redirect/safe_next/settings_of`, `app.state.mailer`, `app.state.google`.
- Produces routes: `GET /login?next=`, `GET /login/code?next=`, `POST /auth/email/start (email, next)`, `POST /auth/email/verify (code, next)`, `POST /auth/email/resend (next)`, `POST /auth/google (JSON {credential, next}) -> {redirect}`, `POST /auth/logout`. Pending email lives in signed cookie `zd_pending` (15 min).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_auth_routes.py`:
```python
import dataclasses
import re

from app.auth.google import GoogleIdentity
from tests.helpers import ADMIN, last_code, sign_in

JSON = {"accept": "application/json"}


def test_login_page(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert 'id="g-btn"' in response.text and "accounts.google.com/gsi/client" in response.text
    assert 'name="email"' in response.text and "<h1>Sign in</h1>" in response.text


def test_login_page_without_google(client, app):
    app.state.settings = dataclasses.replace(app.state.settings, google_client_id="")
    response = client.get("/login")
    assert 'id="g-btn"' not in response.text and "gsi/client" not in response.text


def test_login_page_in_kazakh(client):
    response = client.get("/login?lang=kk")
    assert '<html lang="kk">' in response.text and "<h1>Кіру</h1>" in response.text


def test_invalid_email(client, mailer):
    response = client.post("/auth/email/start", data={"email": "nope"})
    assert response.status_code == 400
    assert "Enter a valid email address." in response.text
    assert mailer.sent == []


def test_email_sign_in_flow(client, mailer):
    start = client.post("/auth/email/start", data={"email": " Ann@Example.com ", "next": "/sites/x"}, follow_redirects=False)
    assert start.status_code == 303 and start.headers["location"] == "/login/code?next=/sites/x"
    assert mailer.sent[-1].to == "ann@example.com"
    assert re.search(r"\b\d{6}\b", mailer.sent[-1].subject)

    page = client.get("/login/code")
    assert "ann@example.com" in page.text and 'autocomplete="one-time-code"' in page.text

    wrong = client.post("/auth/email/verify", data={"code": "abcdef", "next": "/sites/x"})
    assert wrong.status_code == 400 and "wrong or has expired" in wrong.text

    ok = client.post("/auth/email/verify", data={"code": last_code(mailer), "next": "/sites/x"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/sites/x"
    assert ok.cookies.get("zd_session")
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"


def test_code_email_is_translated(client, mailer):
    client.post("/auth/email/start?lang=ru", data={"email": "a@b.co"}, follow_redirects=False)
    assert "ваш код" in mailer.sent[-1].subject
    assert "Код действует 10 минут" in mailer.sent[-1].body


def test_unsafe_next_is_ignored(client, mailer):
    start = client.post("/auth/email/start", data={"email": "a@b.co", "next": "//evil.test"}, follow_redirects=False)
    assert start.headers["location"] == "/login/code?next=/"
    done = client.post("/auth/email/verify", data={"code": last_code(mailer), "next": "https://evil.test"}, follow_redirects=False)
    assert done.headers["location"] == "/"


def test_code_page_requires_pending_email(client):
    assert client.get("/login/code", follow_redirects=False).headers["location"] == "/login"


def test_repeat_start_within_a_minute_reuses_the_code(client, mailer):
    client.post("/auth/email/start", data={"email": "a@b.co"}, follow_redirects=False)
    again = client.post("/auth/email/start", data={"email": "a@b.co"}, follow_redirects=False)
    assert again.status_code == 303
    assert len(mailer.sent) == 1


def test_resend_is_rate_limited_then_allowed(client, mailer, monkeypatch):
    now = [1_000_000]
    monkeypatch.setattr("app.db.now", lambda: now[0])
    client.post("/auth/email/start", data={"email": "a@b.co"}, follow_redirects=False)
    early = client.post("/auth/email/resend", data={"next": "/"})
    assert early.status_code == 429 and "Too many attempts" in early.text
    now[0] += 61
    later = client.post("/auth/email/resend", data={"next": "/"}, follow_redirects=False)
    assert later.status_code == 303 and len(mailer.sent) == 2
    assert "We sent a new code." in client.get(later.headers["location"]).text


def test_google_sign_in(client, google):
    google.tokens["good"] = GoogleIdentity(sub="g-1", email="ann@zerde.ai", name="Ann", picture="")
    ok = client.post("/auth/google", json={"credential": "good", "next": "/admin"}, headers=JSON)
    assert ok.status_code == 200 and ok.json() == {"redirect": "/admin"}
    assert ok.cookies.get("zd_session")
    bad = client.post("/auth/google", json={"credential": "bad"}, headers=JSON)
    assert bad.status_code == 400 and "Google" in bad.json()["error"]


def test_logout(client, mailer):
    sign_in(client, mailer, ADMIN)
    out = client.post("/auth/logout", follow_redirects=False)
    assert out.headers["location"] == "/login"
    assert client.get("/login", follow_redirects=False).status_code == 200


def test_cross_site_form_post_is_rejected(client):
    response = client.post("/auth/email/start", data={"email": "a@b.co"}, headers={"origin": "http://site.localhost:8000"})
    assert response.status_code == 403
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_auth_routes.py -q`
Expected: FAIL — 404s for `/login` etc.

- [ ] **Step 3: Implement the routes**

Replace `backend/app/routes/auth.py`:
```python
"""Sign-in pages and actions: Google ID token, or email + 6-digit code."""
from __future__ import annotations

import logging
import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

from .. import access, i18n, signing, users
from ..auth import email_code, sessions
from ..auth.google import GoogleAuthError
from ..auth.mailer import Mailer, Message
from ..deps import current_user, get_conn
from ..web import redirect, render, safe_next, settings_of

log = logging.getLogger("zerde.auth")
router = APIRouter()
PENDING_COOKIE = "zd_pending"
PENDING_TTL = 15 * 60


class GoogleLogin(BaseModel):
    credential: str
    next: str = "/"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def pending_email(request: Request) -> str | None:
    raw = request.cookies.get(PENDING_COOKIE)
    return signing.unsign(raw, settings_of(request).secret_key, PENDING_TTL) if raw else None


def send_code(mailer: Mailer, email: str, code: str, lang: str, domain: str) -> None:
    message = Message(
        to=email,
        subject=i18n.t(lang, "email.subject", code=code),
        body=i18n.t(lang, "email.body", code=code, domain=domain),
    )
    try:
        mailer.send(message)
    except Exception:
        log.exception("Could not send a sign-in code to %s", email)


def start_session(request: Request, conn: sqlite3.Connection, user: sqlite3.Row, response: Response) -> Response:
    sessions.set_session_cookie(response, settings_of(request), sessions.create_session(conn, user["id"]))
    return response


@router.get("/login")
def login_page(
    request: Request, next_url: str = Query("/", alias="next"), user: sqlite3.Row | None = Depends(current_user)
) -> Response:
    target = safe_next(next_url)
    if user is not None:
        return redirect(target)
    return render(request, "login.html", next=target, email="", error=None)


@router.get("/login/code")
def code_page(
    request: Request, next_url: str = Query("/", alias="next"), conn: sqlite3.Connection = Depends(get_conn)
) -> Response:
    email = pending_email(request)
    if email is None:
        return redirect("/login")
    return render(
        request, "code.html", email=email, next=safe_next(next_url), error=None,
        resend_wait=email_code.seconds_until_resend(conn, email),
    )


@router.post("/auth/email/start")
def email_start(
    request: Request,
    background: BackgroundTasks,
    email: str = Form(""),
    next_url: str = Form("/", alias="next"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    settings = settings_of(request)
    lang = request.state.lang
    target = safe_next(next_url)
    address = access.normalize_email(email)
    if address is None:
        return render(request, "login.html", status=400, next=target, email=email, error=i18n.t(lang, "error.email_invalid"))
    try:
        code = email_code.issue_code(conn, settings.secret_key, address, client_ip(request))
    except email_code.RateLimited as exc:
        if exc.scope != "email_interval":
            error = i18n.t(lang, "error.rate_limited", s=exc.retry_after)
            return render(request, "login.html", status=429, next=target, email=email, error=error)
    else:
        background.add_task(send_code, request.app.state.mailer, address, code, lang, settings.app_domain)
    response = redirect(f"/login/code?next={quote(target)}")
    response.set_cookie(
        PENDING_COOKIE, signing.sign(address, settings.secret_key), max_age=PENDING_TTL, path="/",
        secure=settings.https, httponly=True, samesite="lax",
    )
    return response


@router.post("/auth/email/verify")
def email_verify(
    request: Request,
    code: str = Form(""),
    next_url: str = Form("/", alias="next"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    settings = settings_of(request)
    target = safe_next(next_url)
    email = pending_email(request)
    if email is None:
        return redirect("/login")
    if not email_code.verify_code(conn, settings.secret_key, email, code):
        return render(
            request, "code.html", status=400, email=email, next=target,
            error=i18n.t(request.state.lang, "error.code_invalid"),
            resend_wait=email_code.seconds_until_resend(conn, email),
        )
    response = redirect(target)
    response.delete_cookie(PENDING_COOKIE, path="/")
    return start_session(request, conn, users.upsert_user(conn, email), response)


@router.post("/auth/email/resend")
def email_resend(
    request: Request,
    background: BackgroundTasks,
    next_url: str = Form("/", alias="next"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    settings = settings_of(request)
    lang = request.state.lang
    target = safe_next(next_url)
    email = pending_email(request)
    if email is None:
        return redirect("/login")
    try:
        code = email_code.issue_code(conn, settings.secret_key, email, client_ip(request))
    except email_code.RateLimited as exc:
        return render(
            request, "code.html", status=429, email=email, next=target,
            error=i18n.t(lang, "error.rate_limited", s=exc.retry_after),
            resend_wait=email_code.seconds_until_resend(conn, email),
        )
    background.add_task(send_code, request.app.state.mailer, email, code, lang, settings.app_domain)
    return redirect(f"/login/code?next={quote(target)}", settings=settings, flash=("ok", i18n.t(lang, "code.sent")))


@router.post("/auth/google")
def google_login(request: Request, body: GoogleLogin, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    try:
        identity = request.app.state.google.verify(body.credential)
    except GoogleAuthError:
        return JSONResponse({"error": i18n.t(request.state.lang, "error.google_failed")}, status_code=400)
    user = users.upsert_user(
        conn, identity.email, name=identity.name, avatar_url=identity.picture, google_sub=identity.sub
    )
    return start_session(request, conn, user, JSONResponse({"redirect": safe_next(body.next)}))


@router.post("/auth/logout")
def logout(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    settings = settings_of(request)
    token = request.cookies.get(settings.session_cookie)
    if token:
        sessions.delete_session(conn, token)
    response = redirect("/login")
    sessions.clear_session_cookie(response, settings)
    return response
```

- [ ] **Step 4: Implement the templates**

`frontend/templates/login.html`:
```html
{% extends "base.html" %}
{% block title %}{{ t("login.title") }}{% endblock %}
{% block main %}
<section class="auth">
  <h1>{{ t("login.title") }}</h1>
  <p class="lead">{{ t("login.lead", domain=settings.sites_domain) }}</p>
  {% if error %}<p class="notice notice-error" role="alert">{{ error }}</p>{% endif %}
  {% if settings.google_client_id %}
  <div id="g-btn" class="g-slot" data-client-id="{{ settings.google_client_id }}" data-next="{{ next }}" data-locale="{{ lang }}"></div>
  <p class="notice notice-error g-error" role="alert" hidden>{{ t("error.google_failed") }}</p>
  <p class="or">{{ t("login.or") }}</p>
  {% endif %}
  <form class="stack" method="post" action="/auth/email/start">
    <input type="hidden" name="next" value="{{ next }}">
    <div class="field">
      <label for="email">{{ t("login.email_label") }}</label>
      <input class="input" id="email" name="email" type="email" autocomplete="email" inputmode="email" autocapitalize="none" spellcheck="false" required value="{{ email }}" aria-describedby="email-hint">
      <p class="hint" id="email-hint">{{ t("login.email_hint") }}</p>
    </div>
    <button class="btn btn-primary" type="submit">{{ t("login.email_submit") }}</button>
  </form>
</section>
{% endblock %}
{% block scripts %}
{% if settings.google_client_id %}<script src="https://accounts.google.com/gsi/client" async></script>{% endif %}
{{ super() }}
{% endblock %}
```

`frontend/templates/code.html`:
```html
{% extends "base.html" %}
{% block title %}{{ t("code.title") }}{% endblock %}
{% block main %}
<section class="auth">
  <h1>{{ t("code.title") }}</h1>
  <p class="lead">{{ t("code.lead", email=email) }}</p>
  {% if error %}<p class="notice notice-error" role="alert">{{ error }}</p>{% endif %}
  <form class="stack" method="post" action="/auth/email/verify">
    <input type="hidden" name="next" value="{{ next }}">
    <div class="field">
      <label for="code">{{ t("code.label") }}</label>
      <input class="input code-input" id="code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9 ]{6,7}" maxlength="7" required autofocus>
    </div>
    <button class="btn btn-primary" type="submit">{{ t("code.submit") }}</button>
  </form>
  <div class="auth-foot">
    <form method="post" action="/auth/email/resend">
      <input type="hidden" name="next" value="{{ next }}">
      <button class="btn-link" type="submit" data-wait="{{ resend_wait }}" data-label="{{ t('code.resend') }}" data-wait-label="{{ t('code.resend_in', s='{s}') }}"{% if resend_wait %} disabled{% endif %}>{{ t("code.resend_in", s=resend_wait) if resend_wait else t("code.resend") }}</button>
    </form>
    <a href="/login?next={{ next|urlencode }}">{{ t("code.change_email") }}</a>
  </div>
</section>
{% endblock %}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/auth.py frontend/templates/login.html frontend/templates/code.html backend/tests/test_auth_routes.py
git commit -m "feat(auth): Google and email-code sign-in pages and actions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Dashboard and uploads

**Files:**
- Replace: `backend/app/routes/pages.py`, `backend/app/routes/sites.py`
- Create: `frontend/templates/dashboard.html`, `frontend/templates/_drop.html`, `frontend/templates/noaccess.html`
- Test: `backend/tests/test_dashboard_upload.py`

**Interfaces:**
- Consumes: `service.list_sites/get_site/can_manage/publish`, `ingest.stage_files/stage_zip/Limits/UploadError`, `access.can_deploy/is_admin`, `deps.require_deployer`, `web.set_flash/redirect/render/wants_json`.
- Produces routes: `GET /` (redirect to `/login`, no-access page, or dashboard), `POST /deploy` (new site), `POST /sites/{name}/deploy` (new version). Upload form fields: `files` (repeated) + optional parallel `paths` (relative paths). JSON responses (when `Accept: application/json`): 201 `{name, url, deploy_id, manage_url}` (also sets the "Published." flash), 400 `{error}`. Without JSON: 303 to `/sites/<name>` or back with an error flash.
- Produces helpers in `routes/sites.py`: `limits_for(settings) -> Limits`, `managed_site(request, conn, user, name) -> Row` (raises `NotFound`), `stage_upload(pairs: list[tuple[UploadFile, str]], dest, limits) -> StagedTree`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_dashboard_upload.py`:
```python
from app import access
from tests.helpers import ADMIN, sign_in, upload_zip

JSON = {"accept": "application/json"}


def test_dashboard_requires_sign_in(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_user_without_access_sees_explanation(client, mailer):
    sign_in(client, mailer, "guest@example.com")
    page = client.get("/")
    assert page.status_code == 200
    assert "guest@example.com" in page.text and "No access yet" in page.text
    assert 'id="drop"' not in page.text
    assert upload_zip(client, {"index.html": b"hi"}).status_code == 403


def test_allowlisted_user_sees_drop_zone(client, mailer, conn):
    access.add_to_allowlist(conn, "@example.com", ADMIN)
    sign_in(client, mailer, "ann@example.com")
    page = client.get("/")
    assert 'id="drop"' in page.text and 'action="/deploy"' in page.text
    assert "No sites yet" in page.text


def test_deploy_zip_and_visit(client, mailer):
    sign_in(client, mailer, ADMIN)
    response = upload_zip(client, {"index.html": b"<h1>Hello</h1>", "about.html": b"about", "docs/index.html": b"docs"})
    assert response.status_code == 201, response.text
    body = response.json()
    name = body["name"]
    assert body["url"] == f"http://{name}.localhost:8000/"
    assert body["manage_url"] == f"/sites/{name}"
    site = f"http://{name}.localhost:8000"
    assert client.get(site + "/").text == "<h1>Hello</h1>"
    assert client.get(site + "/about").text == "about"
    assert client.get(site + "/docs/").text == "docs"
    assert client.get(site + "/nope").status_code == 404
    dashboard = client.get("/")
    assert f"{name}.localhost:8000" in dashboard.text
    assert "Published." in dashboard.text


def test_deploy_files_with_relative_paths(client, mailer):
    sign_in(client, mailer, ADMIN)
    response = client.post(
        "/deploy",
        files=[("files", ("index.html", b"root", "text/html")), ("files", ("a.css", b"body{}", "text/css"))],
        data={"paths": ["proj/index.html", "proj/css/a.css"]},
        headers=JSON,
    )
    assert response.status_code == 201, response.text
    name = response.json()["name"]
    assert client.get(f"http://{name}.localhost:8000/css/a.css").text == "body{}"


def test_upload_errors_are_translated(client, mailer):
    sign_in(client, mailer, ADMIN)
    english = upload_zip(client, {"readme.txt": b"x"})
    assert english.status_code == 400 and english.json() == {"error": "There is no index.html at the top level."}
    client.get("/?lang=ru")
    russian = upload_zip(client, {"readme.txt": b"x"})
    assert russian.json() == {"error": "В корне нет index.html."}
    empty = client.post("/deploy", data={"paths": []}, headers=JSON)
    assert empty.status_code == 400


def test_form_upload_without_javascript_redirects(client, mailer):
    sign_in(client, mailer, ADMIN)
    response = upload_zip(client, {"index.html": b"hi"}, json=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/sites/")
    failed = upload_zip(client, {"readme.txt": b"x"}, json=False)
    assert failed.status_code == 303 and failed.headers["location"] == "/"
    assert "no index.html" in client.get("/").text


def test_redeploy_endpoint_and_ownership(client, mailer, app, conn):
    sign_in(client, mailer, ADMIN)
    name = upload_zip(client, {"index.html": b"v1"}).json()["name"]
    again = upload_zip(client, {"index.html": b"v2"}, url=f"/sites/{name}/deploy")
    assert again.status_code == 201 and again.json()["name"] == name
    assert client.get(f"http://{name}.localhost:8000/").text == "v2"
    assert upload_zip(client, {"index.html": b"x"}, url="/sites/missing-site/deploy").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_dashboard_upload.py -q`
Expected: FAIL — `/` returns 404 / upload routes missing.

- [ ] **Step 3: Implement the routes**

Replace `backend/app/routes/pages.py`:
```python
"""Dashboard page."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from .. import access
from ..deploys import service
from ..deps import current_user, get_conn
from ..web import redirect, render, settings_of

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    user: sqlite3.Row | None = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    if user is None:
        return redirect("/login")
    if not access.can_deploy(conn, settings_of(request), user["email"]):
        return render(request, "noaccess.html", user=user)
    return render(request, "dashboard.html", user=user, sites=service.list_sites(conn, user["id"]))
```

Replace `backend/app/routes/sites.py`:
```python
"""Upload, site page and site actions."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from .. import access, i18n
from ..config import MB, Settings
from ..deploys import ingest, service
from ..deploys.ingest import Limits, StagedTree, UploadError
from ..deps import NotFound, get_conn, require_deployer
from ..web import redirect, render, set_flash, settings_of, wants_json

router = APIRouter()


def limits_for(settings: Settings) -> Limits:
    return Limits(
        max_total_bytes=settings.max_upload_mb * MB,
        max_files=settings.max_files,
        max_file_bytes=settings.max_file_mb * MB,
    )


def managed_site(request: Request, conn: sqlite3.Connection, user: sqlite3.Row, name: str) -> sqlite3.Row:
    site = service.get_site(conn, name)
    admin = access.is_admin(settings_of(request), user["email"])
    if site is None or not service.can_manage(site, user, admin):
        raise NotFound()
    return site


def stage_upload(pairs: list[tuple[UploadFile, str]], dest: Path, limits: Limits) -> StagedTree:
    if not pairs:
        raise UploadError("empty")
    if len(pairs) > limits.max_files:
        raise UploadError("too_many_files", n=limits.max_files)
    first = pairs[0][0]
    if len(pairs) == 1 and (first.filename or "").lower().endswith(".zip"):
        return ingest.stage_zip(first.file, dest, limits)
    return ingest.stage_files([(path or upload.filename or "", upload.file) for upload, path in pairs], dest, limits)


def upload_failed(request: Request, error: UploadError, back: str) -> Response:
    message = i18n.t(request.state.lang, f"upload.error.{error.code}", **error.params)
    if wants_json(request):
        return JSONResponse({"error": message}, status_code=400)
    return redirect(back, settings=settings_of(request), flash=("error", message))


async def handle_upload(
    request: Request, conn: sqlite3.Connection, user: sqlite3.Row, site: sqlite3.Row | None
) -> Response:
    settings = settings_of(request)
    store = request.app.state.store
    limits = limits_for(settings)
    back = f"/sites/{site['name']}" if site else "/"
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limits.max_total_bytes + MB:
        return upload_failed(request, UploadError("too_big", mb=settings.max_upload_mb), back)
    try:
        form = await request.form(max_files=limits.max_files + 1, max_fields=limits.max_files + 32)
    except StarletteHTTPException:
        return upload_failed(request, UploadError("too_many_files", n=limits.max_files), back)
    staging = store.new_staging_dir()
    try:
        uploads = form.getlist("files")
        paths = [str(path) for path in form.getlist("paths")]
        pairs = [
            (upload, paths[index] if index < len(paths) else "")
            for index, upload in enumerate(uploads)
            if isinstance(upload, UploadFile) and upload.filename
        ]
        staged = await run_in_threadpool(stage_upload, pairs, staging, limits)
        name, deploy_id = await run_in_threadpool(
            service.publish, conn, store,
            site_name=site["name"] if site else None, user_id=user["id"], staged=staged,
        )
    except UploadError as error:
        return upload_failed(request, error, back)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        await form.close()
    message = i18n.t(request.state.lang, "flash.deployed")
    if wants_json(request):
        response = JSONResponse(
            {"name": name, "url": settings.site_url(name), "deploy_id": deploy_id, "manage_url": f"/sites/{name}"},
            status_code=201,
        )
        set_flash(response, settings, "ok", message)
        return response
    return redirect(f"/sites/{name}", settings=settings, flash=("ok", message))


@router.post("/deploy")
async def deploy_new(
    request: Request,
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    return await handle_upload(request, conn, user, None)


@router.post("/sites/{name}/deploy")
async def deploy_version(
    name: str,
    request: Request,
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    site = await run_in_threadpool(managed_site, request, conn, user, name)
    return await handle_upload(request, conn, user, site)
```
(`Form` and `render` are imported now because Task 13 adds the site page and form actions to this module.)

- [ ] **Step 4: Implement the templates**

`frontend/templates/_drop.html`:
```html
{% from "_icons.html" import upload_icon %}
{% macro drop_zone(action, title) %}
<form class="drop" id="drop" method="post" action="{{ action }}" enctype="multipart/form-data">
  <div class="drop-idle">
    {{ upload_icon() }}
    <p class="drop-title">{{ title }}</p>
    <p class="drop-hint">{{ t("drop.hint", mb=settings.max_upload_mb, files=settings.max_files) }}</p>
    <div class="drop-buttons">
      <label class="btn btn-primary"><input class="visually-hidden" type="file" name="files" multiple>{{ t("drop.choose_files") }}</label>
      <label class="btn btn-secondary"><input class="visually-hidden" type="file" name="files" webkitdirectory multiple>{{ t("drop.choose_folder") }}</label>
      <noscript><button class="btn btn-secondary" type="submit">{{ t("drop.submit") }}</button></noscript>
    </div>
    <p class="drop-error" role="alert" hidden></p>
  </div>
  <div class="drop-busy" hidden>
    <p class="drop-status" aria-live="polite"></p>
    <div class="bar" aria-hidden="true"><span></span></div>
    <button class="btn btn-secondary drop-cancel" type="button">{{ t("js.cancel") }}</button>
  </div>
</form>
{% endmacro %}
```

`frontend/templates/dashboard.html`:
```html
{% extends "base.html" %}
{% from "_drop.html" import drop_zone with context %}
{% from "_icons.html" import copy_icon %}
{% block title %}{{ t("dash.title") }}{% endblock %}
{% block main %}
<h1 class="visually-hidden">{{ t("dash.title") }}</h1>
{{ drop_zone("/deploy", t("drop.title")) }}
<section class="sites" aria-labelledby="sites-title">
  <h2 id="sites-title">{{ t("dash.yours") }}</h2>
  {% if sites %}
  <ul class="list" role="list">
    {% for site in sites %}{% set url = settings.site_url(site.name) %}
    <li class="site-row">
      <div class="site-main">
        <a class="site-link" href="{{ url }}" target="_blank" rel="noopener">{{ site.name }}.{{ settings.sites_domain }}</a>
        <span class="site-meta">{{ ago(site.deployed_at or site.updated_at) }} · {{ size(site.total_bytes or 0) }}</span>
      </div>
      <div class="site-actions">
        <button class="btn btn-quiet" type="button" data-copy="{{ url }}">{{ copy_icon() }}<span>{{ t("sites.copy") }}</span></button>
        <a class="btn btn-quiet" href="/sites/{{ site.name }}">{{ t("sites.settings") }}</a>
      </div>
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p class="empty">{{ t("sites.empty") }}</p>
  {% endif %}
</section>
{% endblock %}
```

`frontend/templates/noaccess.html`:
```html
{% extends "base.html" %}
{% block title %}{{ t("noaccess.title") }}{% endblock %}
{% block main %}
<section class="auth">
  <h1>{{ t("noaccess.title") }}</h1>
  <p class="lead">{{ t("noaccess.body", email=user.email) }}</p>
  <form method="post" action="/auth/logout"><button class="btn btn-secondary" type="submit">{{ t("nav.sign_out") }}</button></form>
</section>
{% endblock %}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/pages.py backend/app/routes/sites.py frontend/templates backend/tests/test_dashboard_upload.py
git commit -m "feat: dashboard with drop zone, uploads for new sites and new versions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Site page — versions, restore, rename, delete

**Files:**
- Modify: `backend/app/routes/sites.py` (append routes)
- Create: `frontend/templates/site.html`
- Test: `backend/tests/test_site_page.py`

**Interfaces:**
- Consumes: `managed_site`, `service.list_deploys/restore/rename/delete/SiteError`, `web.render/redirect`.
- Produces routes: `GET /sites/{name}`, `POST /sites/{name}/restore (deploy_id)`, `POST /sites/{name}/rename (new_name)`, `POST /sites/{name}/delete (confirm)`. All POSTs answer 303 with a flash.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_site_page.py`:
```python
from app import access
from tests.helpers import ADMIN, new_client, sign_in, upload_zip


def deploy(client, text, url="/deploy"):
    response = upload_zip(client, {"index.html": text.encode()}, url=url)
    assert response.status_code == 201, response.text
    return response.json()


def served(client, name):
    return client.get(f"http://{name}.localhost:8000/")


def test_site_page_lists_versions(client, mailer):
    sign_in(client, mailer, ADMIN)
    name = deploy(client, "v1")["name"]
    deploy(client, "v2", url=f"/sites/{name}/deploy")
    page = client.get(f"/sites/{name}")
    assert page.status_code == 200
    assert page.text.count('class="version"') == 2
    assert f"http://{name}.localhost:8000/" in page.text
    assert 'action="/sites/' + name + '/deploy"' in page.text


def test_restore_previous_version(client, mailer):
    sign_in(client, mailer, ADMIN)
    first = deploy(client, "v1")
    name = first["name"]
    deploy(client, "v2", url=f"/sites/{name}/deploy")
    response = client.post(f"/sites/{name}/restore", data={"deploy_id": first["deploy_id"]}, follow_redirects=False)
    assert response.status_code == 303
    assert served(client, name).text == "v1"
    assert "Version restored." in client.get(f"/sites/{name}").text
    assert client.post(f"/sites/{name}/restore", data={"deploy_id": 999999}).status_code == 404


def test_rename(client, mailer):
    sign_in(client, mailer, ADMIN)
    name = deploy(client, "v1")["name"]
    other = deploy(client, "o")["name"]
    invalid = client.post(f"/sites/{name}/rename", data={"new_name": "x"}, follow_redirects=False)
    assert invalid.headers["location"] == f"/sites/{name}"
    assert "3–40 lowercase" in client.get(invalid.headers["location"]).text
    taken = client.post(f"/sites/{name}/rename", data={"new_name": other}, follow_redirects=False)
    assert "This address is taken." in client.get(taken.headers["location"]).text
    ok = client.post(f"/sites/{name}/rename", data={"new_name": "My-Landing"}, follow_redirects=False)
    assert ok.headers["location"] == "/sites/my-landing"
    assert served(client, "my-landing").text == "v1"
    assert served(client, name).status_code == 404


def test_delete_needs_the_name(client, mailer):
    sign_in(client, mailer, ADMIN)
    name = deploy(client, "v1")["name"]
    wrong = client.post(f"/sites/{name}/delete", data={"confirm": "nope"}, follow_redirects=False)
    assert wrong.headers["location"] == f"/sites/{name}"
    assert served(client, name).status_code == 200
    done = client.post(f"/sites/{name}/delete", data={"confirm": name}, follow_redirects=False)
    assert done.headers["location"] == "/"
    assert served(client, name).status_code == 404
    assert "Site deleted." in client.get("/").text


def test_other_users_sites_are_hidden(client, mailer, app, conn):
    access.add_to_allowlist(conn, "@example.com", ADMIN)
    sign_in(client, mailer, "ann@example.com")
    name = deploy(client, "ann")["name"]
    bob = new_client(app)
    sign_in(bob, mailer, "bob@example.com")
    assert bob.get(f"/sites/{name}").status_code == 404
    assert bob.post(f"/sites/{name}/delete", data={"confirm": name}).status_code == 404
    assert upload_zip(bob, {"index.html": b"x"}, url=f"/sites/{name}/deploy").status_code == 404
    admin = new_client(app)
    sign_in(admin, mailer, ADMIN)
    assert admin.get(f"/sites/{name}").status_code == 200


def test_site_page_in_russian(client, mailer):
    sign_in(client, mailer, ADMIN)
    name = deploy(client, "v1")["name"]
    page = client.get(f"/sites/{name}?lang=ru")
    assert "Версии" in page.text and "Удалить сайт" in page.text
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_site_page.py -q`
Expected: FAIL — 404/405 for `/sites/{name}` routes.

- [ ] **Step 3: Implement the routes**

Append to `backend/app/routes/sites.py`:
```python
@router.get("/sites/{name}")
def site_page(
    name: str,
    request: Request,
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    site = managed_site(request, conn, user, name)
    return render(
        request, "site.html", user=user, site=site,
        deploys=service.list_deploys(conn, site["id"]), url=settings_of(request).site_url(site["name"]),
    )


@router.post("/sites/{name}/restore")
def restore_version(
    name: str,
    request: Request,
    deploy_id: int = Form(...),
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    site = managed_site(request, conn, user, name)
    try:
        service.restore(conn, request.app.state.store, site, deploy_id)
    except service.SiteError:
        raise NotFound() from None
    message = i18n.t(request.state.lang, "flash.restored")
    return redirect(f"/sites/{name}", settings=settings_of(request), flash=("ok", message))


@router.post("/sites/{name}/rename")
def rename_site(
    name: str,
    request: Request,
    new_name: str = Form(""),
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    site = managed_site(request, conn, user, name)
    settings = settings_of(request)
    lang = request.state.lang
    try:
        renamed = service.rename(conn, request.app.state.store, site, new_name)
    except service.SiteError as error:
        return redirect(f"/sites/{name}", settings=settings, flash=("error", i18n.t(lang, f"error.{error.code}")))
    return redirect(f"/sites/{renamed}", settings=settings, flash=("ok", i18n.t(lang, "flash.renamed")))


@router.post("/sites/{name}/delete")
def delete_site(
    name: str,
    request: Request,
    confirm: str = Form(""),
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    site = managed_site(request, conn, user, name)
    settings = settings_of(request)
    lang = request.state.lang
    if confirm.strip().lower() != site["name"]:
        return redirect(f"/sites/{name}", settings=settings, flash=("error", i18n.t(lang, "error.confirm_mismatch")))
    service.delete(conn, request.app.state.store, site)
    return redirect("/", settings=settings, flash=("ok", i18n.t(lang, "flash.deleted")))
```

- [ ] **Step 4: Implement the template**

`frontend/templates/site.html`:
```html
{% extends "base.html" %}
{% from "_drop.html" import drop_zone with context %}
{% from "_icons.html" import copy_icon %}
{% block title %}{{ site.name }}{% endblock %}
{% block main %}
<p class="crumb"><a href="/">← {{ t("site.back") }}</a></p>
<div class="page-head">
  <h1 class="mono">{{ site.name }}</h1>
  <div class="link-line">
    <a href="{{ url }}" target="_blank" rel="noopener">{{ url }}</a>
    <button class="btn btn-quiet" type="button" data-copy="{{ url }}">{{ copy_icon() }}<span>{{ t("sites.copy") }}</span></button>
  </div>
</div>

{{ drop_zone("/sites/" ~ site.name ~ "/deploy", t("drop.redeploy_title")) }}

<section class="panel" aria-labelledby="versions-title">
  <h2 id="versions-title">{{ t("site.history") }}</h2>
  <ol class="list" role="list">
    {% for d in deploys %}
    <li class="version">
      <span class="version-id">#{{ d.id }}</span>
      <div class="version-main">
        <span>{{ ago(d.created_at) }}</span>
        <span class="version-meta">{{ t("site.files", n=d.file_count) }} · {{ size(d.total_bytes) }} · {{ d.author_email }}</span>
      </div>
      {% if d.id == site.current_deploy_id %}
      <span class="tag">{{ t("site.current") }}</span>
      {% else %}
      <form method="post" action="/sites/{{ site.name }}/restore">
        <input type="hidden" name="deploy_id" value="{{ d.id }}">
        <button class="btn btn-secondary" type="submit">{{ t("site.restore") }}</button>
      </form>
      {% endif %}
    </li>
    {% endfor %}
  </ol>
</section>

<section class="panel" aria-labelledby="rename-title">
  <h2 id="rename-title">{{ t("site.rename_title") }}</h2>
  <form class="row-form" method="post" action="/sites/{{ site.name }}/rename">
    <div class="affix">
      <input class="input mono" id="new-name" name="new_name" value="{{ site.name }}" aria-labelledby="rename-title" aria-describedby="rename-hint" maxlength="40" autocomplete="off" autocapitalize="none" spellcheck="false" required>
      <span class="suffix mono">.{{ settings.sites_domain }}</span>
    </div>
    <button class="btn btn-secondary" type="submit">{{ t("site.rename_submit") }}</button>
  </form>
  <p class="hint" id="rename-hint">{{ t("site.rename_hint") }}</p>
</section>

<section class="panel panel-danger" aria-labelledby="delete-title">
  <h2 id="delete-title">{{ t("site.delete_title") }}</h2>
  <p class="hint" id="delete-hint">{{ t("site.delete_hint", name=site.name) }}</p>
  <form class="row-form" method="post" action="/sites/{{ site.name }}/delete">
    <label class="visually-hidden" for="confirm">{{ t("site.delete_label") }}</label>
    <input class="input mono" id="confirm" name="confirm" data-confirm="{{ site.name }}" aria-describedby="delete-hint" autocomplete="off" autocapitalize="none" spellcheck="false" required>
    <button class="btn btn-danger" type="submit">{{ t("site.delete_submit") }}</button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/sites.py frontend/templates/site.html backend/tests/test_site_page.py
git commit -m "feat: site page with version history, restore, rename and delete

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Access administration

**Files:**
- Replace: `backend/app/routes/admin.py`
- Create: `frontend/templates/admin.html`
- Test: `backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `access.*`, `service.list_sites`, `deps.require_admin`.
- Produces routes: `GET /admin`, `POST /admin/allowlist (pattern)`, `POST /admin/allowlist/{entry_id}/delete`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_admin.py`:
```python
from tests.helpers import ADMIN, new_client, sign_in, upload_zip


def test_non_admins_are_forbidden(client, mailer):
    sign_in(client, mailer, "guest@example.com")
    assert client.get("/admin").status_code == 403
    assert client.post("/admin/allowlist", data={"pattern": "@example.com"}).status_code == 403


def test_admin_manages_the_allowlist(client, mailer, app):
    sign_in(client, mailer, ADMIN)
    page = client.get("/admin")
    assert page.status_code == 200 and ADMIN in page.text and "Nobody besides administrators yet." in page.text

    added = client.post("/admin/allowlist", data={"pattern": " @Example.com "}, follow_redirects=False)
    assert added.headers["location"] == "/admin"
    page = client.get("/admin")
    assert "Added." in page.text and "@example.com" in page.text

    duplicate = client.post("/admin/allowlist", data={"pattern": "@example.com"}, follow_redirects=False)
    assert "Already on the list." in client.get(duplicate.headers["location"]).text
    invalid = client.post("/admin/allowlist", data={"pattern": "example"}, follow_redirects=False)
    assert "Enter an email or a domain" in client.get(invalid.headers["location"]).text

    ann = new_client(app)
    sign_in(ann, mailer, "ann@example.com")
    assert 'id="drop"' in ann.get("/").text

    entry_id = page.text.split('action="/admin/allowlist/')[1].split("/delete")[0]
    removed = client.post(f"/admin/allowlist/{entry_id}/delete", follow_redirects=False)
    assert "Removed." in client.get(removed.headers["location"]).text
    assert "No access yet" in ann.get("/").text


def test_admin_sees_all_sites(client, mailer, app, conn):
    from app import access

    access.add_to_allowlist(conn, "ann@example.com", ADMIN)
    ann = new_client(app)
    sign_in(ann, mailer, "ann@example.com")
    name = upload_zip(ann, {"index.html": b"hi"}).json()["name"]
    sign_in(client, mailer, ADMIN)
    page = client.get("/admin?lang=kk")
    assert name in page.text and "ann@example.com" in page.text and "Барлық сайттар" in page.text
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_admin.py -q`
Expected: FAIL — `/admin` is 404.

- [ ] **Step 3: Implement the routes**

Replace `backend/app/routes/admin.py`:
```python
"""Allowlist administration."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from .. import access, i18n
from ..deploys import service
from ..deps import get_conn, require_admin
from ..web import redirect, render, settings_of

router = APIRouter()


@router.get("/admin")
def admin_page(
    request: Request, user: sqlite3.Row = Depends(require_admin), conn: sqlite3.Connection = Depends(get_conn)
) -> Response:
    return render(
        request, "admin.html", user=user,
        entries=access.list_allowlist(conn),
        admins=sorted(settings_of(request).admin_emails),
        sites=service.list_sites(conn),
    )


@router.post("/admin/allowlist")
def add_entry(
    request: Request,
    pattern: str = Form(""),
    user: sqlite3.Row = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    lang = request.state.lang
    normalized = access.normalize_pattern(pattern)
    if normalized is None:
        flash = ("error", i18n.t(lang, "error.pattern_invalid"))
    elif not access.add_to_allowlist(conn, normalized, user["email"]):
        flash = ("error", i18n.t(lang, "error.pattern_exists"))
    else:
        flash = ("ok", i18n.t(lang, "flash.added"))
    return redirect("/admin", settings=settings_of(request), flash=flash)


@router.post("/admin/allowlist/{entry_id}/delete")
def remove_entry(
    entry_id: int,
    request: Request,
    user: sqlite3.Row = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    access.remove_from_allowlist(conn, entry_id)
    message = i18n.t(request.state.lang, "flash.removed")
    return redirect("/admin", settings=settings_of(request), flash=("ok", message))
```

- [ ] **Step 4: Implement the template**

`frontend/templates/admin.html`:
```html
{% extends "base.html" %}
{% block title %}{{ t("admin.title") }}{% endblock %}
{% block main %}
<div class="page-head">
  <h1>{{ t("admin.title") }}</h1>
  <p class="muted">{{ t("admin.lead") }}</p>
</div>

<form class="field" method="post" action="/admin/allowlist">
  <label for="pattern">{{ t("admin.pattern_label") }}</label>
  <div class="row-form">
    <input class="input" id="pattern" name="pattern" autocomplete="off" autocapitalize="none" spellcheck="false" required>
    <button class="btn btn-primary" type="submit">{{ t("admin.add") }}</button>
  </div>
</form>

<ul class="list" role="list">
  {% for entry in entries %}
  <li class="entry">
    <span class="mono">{{ entry.pattern }}</span>
    <form method="post" action="/admin/allowlist/{{ entry.id }}/delete"><button class="btn btn-quiet" type="submit">{{ t("admin.remove") }}</button></form>
  </li>
  {% else %}
  <li class="empty">{{ t("admin.empty") }}</li>
  {% endfor %}
</ul>

<section class="panel" aria-labelledby="admins-title">
  <h2 id="admins-title">{{ t("admin.admins") }}</h2>
  <p class="hint">{{ t("admin.admins_hint") }}</p>
  <ul class="list" role="list">
    {% for email in admins %}<li class="entry"><span class="mono">{{ email }}</span></li>{% endfor %}
  </ul>
</section>

<section class="panel" aria-labelledby="all-sites-title">
  <h2 id="all-sites-title">{{ t("admin.all_sites") }}</h2>
  <ul class="list" role="list">
    {% for site in sites %}
    <li class="site-row">
      <div class="site-main">
        <a class="site-link" href="/sites/{{ site.name }}">{{ site.name }}</a>
        <span class="site-meta">{{ t("admin.owner") }}: {{ site.owner_email }} · {{ ago(site.deployed_at or site.updated_at) }} · {{ size(site.total_bytes or 0) }}</span>
      </div>
    </li>
    {% else %}
    <li class="empty">{{ t("sites.empty") }}</li>
    {% endfor %}
  </ul>
</section>
{% endblock %}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/admin.py frontend/templates/admin.html backend/tests/test_admin.py
git commit -m "feat(admin): manage the deploy allowlist and see all sites

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Weight budget and accessibility guards

**Files:**
- Test: `backend/tests/test_budget.py`

**Interfaces:**
- Consumes: everything above; no production code changes unless a guard fails (then shrink the offending asset).

- [ ] **Step 1: Write the tests**

`backend/tests/test_budget.py`:
```python
import gzip
import re

import pytest

from app.config import FRONTEND_DIR
from tests.helpers import ADMIN, sign_in, upload_zip

STATIC = FRONTEND_DIR / "static"


def gz(data: bytes) -> int:
    return len(gzip.compress(data, 9))


def test_static_asset_budget():
    assert gz((STATIC / "css/app.css").read_bytes()) <= 12_000
    assert gz((STATIC / "js/app.js").read_bytes()) <= 8_000
    assert sum(p.stat().st_size for p in (STATIC / "fonts").glob("*.woff2")) <= 100_000


def test_dashboard_html_budget(client, mailer):
    sign_in(client, mailer, ADMIN)
    for _ in range(5):
        upload_zip(client, {"index.html": b"hi"})
    response = client.get("/")
    assert gz(response.content) <= 15_000


@pytest.mark.parametrize("lang", ["en", "ru", "kk"])
def test_pages_declare_language_and_label_fields(client, lang):
    page = client.get(f"/login?lang={lang}").text
    assert f'<html lang="{lang}">' in page
    assert '<label for="email">' in page
    assert 'name="viewport"' in page


def test_no_third_party_assets_outside_login(client, mailer):
    sign_in(client, mailer, ADMIN)
    page = client.get("/").text
    external = re.findall(r'<(?:script|link)[^>]+(?:src|href)="https?://', page)
    assert external == []


def test_no_emoji_in_templates_or_translations():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
    for path in list((FRONTEND_DIR / "templates").glob("*.html")) + list((FRONTEND_DIR / "i18n").glob("*.json")):
        assert not emoji.search(path.read_text(encoding="utf-8")), path.name
```

- [ ] **Step 2: Run**

Run: `cd backend && uv run pytest tests/test_budget.py -q`
Expected: PASS. If a budget test fails, reduce the asset (remove unused CSS rules, shorten JS) — do not raise the limit.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_budget.py
git commit -m "test: guard page weight, language attributes and third-party assets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 16: Production stack (Docker + Caddy), local smoke test and 1000-connection load test

**Files:**
- Create: `.dockerignore` (repo root)
- Create: `deploys/Dockerfile`, `deploys/docker-compose.yml`, `deploys/docker-compose.local.yml`, `deploys/Caddyfile`, `deploys/Caddyfile.local`, `deploys/sites.caddy`, `deploys/.env.example`, `deploys/deploy.sh`, `deploys/backup.sh`, `deploys/smoke_local.sh`, `deploys/server-setup.md`
- Create (results): `deploys/loadtest-results.md`

**Interfaces:**
- Consumes: `uvicorn app.main:create_app --factory`, `/healthz`, `/internal/tls-ask?domain=`, `/auth/email/start`, `/auth/email/verify`, `/deploy`, `frontend/static/site-404.html`, data layout `/data/sites/<name>/current`.
- Produces: production stack (`app` + `caddy`) and the local variant on `http://localhost:8080`.

- [ ] **Step 1: Docker image**

`.dockerignore`:
```text
.git
.idea
docs
**/__pycache__
**/.venv
**/.pytest_cache
backend/data
backend/tests
deploys/.env
main.py
```

`deploys/Dockerfile` (build context = repo root):
```dockerfile
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
RUN useradd --system --uid 10001 --home-dir /srv zerde && mkdir -p /data && chown zerde:zerde /data
USER zerde
ENV PATH="/srv/venv/bin:$PATH" DATA_DIR=/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
```

- [ ] **Step 2: Compose and Caddy**

`deploys/docker-compose.yml`:
```yaml
name: zerde-deploy

services:
  app:
    build:
      context: ..
      dockerfile: deploys/Dockerfile
    env_file:
      - path: .env
        required: false
    environment:
      DATA_DIR: /data
      SERVE_SITES_IN_APP: "false"
    volumes:
      - sitedata:/data
    restart: unless-stopped

  caddy:
    image: caddy:2.10
    depends_on:
      - app
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    env_file:
      - path: .env
        required: false
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./sites.caddy:/etc/caddy/sites.caddy:ro
      - ../frontend/static/site-404.html:/srv/errors/site-404.html:ro
      - sitedata:/srv/data:ro
      - caddy_data:/data
      - caddy_config:/config
    ulimits:
      nofile:
        soft: 65535
        hard: 65535

volumes:
  sitedata:
  caddy_data:
  caddy_config:
```

`deploys/docker-compose.local.yml`:
```yaml
# Run the production stack locally over plain HTTP on port 8080:
#   cd deploys && docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
services:
  app:
    environment:
      APP_DOMAIN: localhost:8080
      SITES_DOMAIN: localhost:8080
      HTTPS: "false"
      SECRET_KEY: local-only-secret-not-for-production
      ADMIN_EMAILS: admin@example.com
  caddy:
    ports: !override
      - "8080:80"
    volumes:
      - ./Caddyfile.local:/etc/caddy/Caddyfile:ro
```

`deploys/Caddyfile`:
```caddyfile
{
	email {$ACME_EMAIL}
	on_demand_tls {
		ask http://app:8000/internal/tls-ask
	}
}

{$APP_DOMAIN} {
	encode zstd gzip
	@internal path /internal/*
	respond @internal 404
	request_body {
		max_size 60MB
	}
	reverse_proxy app:8000
}

*.{$SITES_DOMAIN} {
	tls {
		on_demand
	}
	import sites.caddy
}
```

`deploys/Caddyfile.local`:
```caddyfile
{
	auto_https off
	admin off
}

http://localhost {
	@internal path /internal/*
	respond @internal 404
	request_body {
		max_size 60MB
	}
	reverse_proxy app:8000
}

http://*.localhost {
	import sites.caddy
}
```

`deploys/sites.caddy` (shared by both Caddyfiles):
```caddyfile
# Serve /srv/data/sites/<name>/current for <name>.<SITES_DOMAIN>. Python is never involved.
map {host} {site} {
	~^([a-z0-9-]+)\. "${1}"
	default "_"
}
map {path} {cache_control} {
	~\.(css|js|mjs|png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|otf|mp4|webm|mp3|pdf|json|wasm)$ "public, max-age=300"
	default "public, max-age=0, must-revalidate"
}
root * /srv/data/sites/{site}/current
encode zstd gzip
header {
	Cache-Control {cache_control}
	X-Content-Type-Options nosniff
	Referrer-Policy strict-origin-when-cross-origin
	-Server
}
try_files {path} {path}/index.html {path}.html
file_server {
	precompressed gzip
}
handle_errors 404 {
	@custom file /404.html
	handle @custom {
		rewrite * /404.html
		file_server {
			status 404
		}
	}
	handle {
		root * /srv/errors
		rewrite * /site-404.html
		file_server {
			status 404
		}
	}
}
```

- [ ] **Step 3: Configuration and scripts**

`deploys/.env.example`:
```dotenv
# Copy to deploys/.env on the server and fill in. Never commit .env.

# Public addresses
APP_DOMAIN=deploy.zerde.ai
SITES_DOMAIN=deploy.zerde.ai
HTTPS=true
ACME_EMAIL=admin@zerde.ai

# Generate: python3 -c "import secrets; print(secrets.token_urlsafe(48))"
SECRET_KEY=

# Google Identity Services Web client ID (public; no client secret is used)
GOOGLE_CLIENT_ID=

# Comma-separated administrator emails (always allowed to deploy, manage the allowlist)
ADMIN_EMAILS=

# SMTP for confirmation codes. If SMTP_HOST is empty, codes are only written to the app log.
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_STARTTLS=true

# Upload limits
MAX_UPLOAD_MB=50
MAX_FILES=2000
MAX_FILE_MB=25
```

`deploys/deploy.sh`:
```bash
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
```

`deploys/backup.sh`:
```bash
#!/usr/bin/env bash
# Nightly SQLite backup (online backup API) into /data/backups, keeping 14 days.
# Root crontab on the server:  15 3 * * * /opt/zerde-deploy/deploys/backup.sh >> /var/log/zerde-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
docker compose exec -T app python - <<'PY'
import datetime
import pathlib
import sqlite3

out = pathlib.Path("/data/backups")
out.mkdir(exist_ok=True)
target = out / f"app-{datetime.date.today():%Y-%m-%d}.db"
source, copy = sqlite3.connect("/data/app.db"), sqlite3.connect(target)
source.backup(copy)
copy.close()
source.close()
for old in sorted(out.glob("app-*.db"))[:-14]:
    old.unlink()
print("backup written:", target)
PY
```

`deploys/smoke_local.sh`:
```bash
#!/usr/bin/env bash
# End-to-end check of the local Docker stack on http://localhost:8080:
# sign in with an email code (read from the app log), upload a sample site, check how Caddy
# serves it, then load-test it with 1000 concurrent connections (needs `oha`: brew install oha).
set -euo pipefail
cd "$(dirname "$0")"
BASE=http://localhost:8080
EMAIL=admin@example.com
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.local.yml"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
JAR="$WORK/cookies"
fail() { echo "FAIL: $1" >&2; exit 1; }

echo "1. Sign in as $EMAIL"
curl -fsS -o /dev/null -c "$JAR" -b "$JAR" -H "Origin: $BASE" --data-urlencode "email=$EMAIL" "$BASE/auth/email/start"
sleep 1
CODE=$($COMPOSE logs app 2>/dev/null | grep -oE '\b[0-9]{6}\b' | tail -1)
curl -fsS -o /dev/null -c "$JAR" -b "$JAR" -H "Origin: $BASE" --data-urlencode "code=$CODE" "$BASE/auth/email/verify"

echo "2. Upload a sample site"
mkdir -p "$WORK/site/css"
python3 - "$WORK/site" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
rows = "\n".join(f"<p>Row {i}: this page is served straight from disk.</p>" for i in range(400))
(root / "index.html").write_text(f'<!doctype html><html><head><link rel="stylesheet" href="css/app.css"></head><body>{rows}</body></html>')
(root / "css/app.css").write_text("body{font-family:system-ui}\n" * 100)
(root / "404.html").write_text("custom 404")
PY
(cd "$WORK/site" && zip -qr "$WORK/site.zip" .)
NAME=$(curl -fsS -b "$JAR" -H "Origin: $BASE" -H "Accept: application/json" -F "files=@$WORK/site.zip" "$BASE/deploy" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["name"])')
SITE="$NAME.localhost:8080"
echo "   http://$SITE/"

echo "3. Serving checks"
status() { curl -s -o /dev/null -w '%{http_code}' -H "Host: $SITE" "$@"; }
headers() { curl -s -o /dev/null -D - -H "Host: $SITE" "$@"; }
[ "$(status http://127.0.0.1:8080/)" = 200 ] || fail "index is not 200"
headers -H 'Accept-Encoding: gzip' http://127.0.0.1:8080/ | grep -qi '^content-encoding: gzip' || fail "no precompressed gzip"
headers http://127.0.0.1:8080/ | grep -qi '^cache-control: public, max-age=0, must-revalidate' || fail "HTML cache header"
headers http://127.0.0.1:8080/css/app.css | grep -qi '^cache-control: public, max-age=300' || fail "asset cache header"
ETAG=$(headers http://127.0.0.1:8080/ | awk 'tolower($1)=="etag:" {print $2}' | tr -d '\r')
[ "$(status -H "If-None-Match: $ETAG" http://127.0.0.1:8080/)" = 304 ] || fail "ETag revalidation"
[ "$(status http://127.0.0.1:8080/missing)" = 404 ] || fail "missing page is not 404"
curl -s -H "Host: $SITE" http://127.0.0.1:8080/missing | grep -q 'custom 404' || fail "site 404.html not used"
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: localhost:8080' 'http://127.0.0.1:8080/internal/tls-ask?domain=x')" = 404 ] \
  || fail "/internal is reachable through Caddy"
echo "   all serving checks passed"

echo "4. Load test: 1000 concurrent connections for 30 s"
command -v oha >/dev/null || fail "oha is not installed (brew install oha)"
ulimit -n 10240 2>/dev/null || true
if oha --help | grep -q -- '--host'; then HOST_ARGS=(--host "$SITE"); else HOST_ARGS=(-H "Host: $SITE"); fi
oha -c 1000 -z 30s --no-tui "${HOST_ARGS[@]}" -H "Accept-Encoding: gzip" http://127.0.0.1:8080/ | tee "$WORK/oha.txt"
grep -q 'Success rate: *100' "$WORK/oha.txt" || fail "some requests failed under load"
cp "$WORK/oha.txt" ./last-loadtest.txt
echo "Load test passed; full output in deploys/last-loadtest.txt"
```

Make the scripts executable: `chmod +x deploys/*.sh`. Add `deploys/last-loadtest.txt` to `.gitignore`.

`deploys/server-setup.md`:
````markdown
# Server setup — deploy.zerde.ai

Server: Ubuntu, `212.116.232.74` / `2a00:5da0:1:201::522`, user `ubuntu`.

## 1. DNS (at your zerde.ai DNS provider)

| Type | Name       | Value                    |
|------|------------|--------------------------|
| A    | `deploy`   | `212.116.232.74`         |
| A    | `*.deploy` | `212.116.232.74`         |
| AAAA | `deploy`   | `2a00:5da0:1:201::522`   |
| AAAA | `*.deploy` | `2a00:5da0:1:201::522`   |

Check: `dig +short deploy.zerde.ai` and `dig +short anything.deploy.zerde.ai` both print the IP.

## 2. SSH key instead of a password

On your Mac:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/zerde_deploy -C "zerde-deploy"
ssh-copy-id -i ~/.ssh/zerde_deploy.pub ubuntu@212.116.232.74
```

Then on the server change the password (`passwd`). Optional hardening: set `PasswordAuthentication no` in `/etc/ssh/sshd_config` and `sudo systemctl reload ssh` once key login works.

## 3. Docker and firewall (on the server)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu   # log out and back in
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 443/udp && sudo ufw enable
sudo mkdir -p /opt/zerde-deploy && sudo chown ubuntu:ubuntu /opt/zerde-deploy
```

## 4. First deploy

From the repository on your Mac:

```bash
DEPLOY_HOST=ubuntu@212.116.232.74 ./deploys/deploy.sh   # first run stops and asks for .env
```

On the server: `cp /opt/zerde-deploy/deploys/.env.example /opt/zerde-deploy/deploys/.env`, then fill in `SECRET_KEY`, `GOOGLE_CLIENT_ID`, `ADMIN_EMAILS` and the `SMTP_*` values. Run `deploy.sh` again.

Check `https://deploy.zerde.ai/healthz` → `ok`. The first visit to a new site's address takes a few seconds while its certificate is issued.

## 5. Backups

```bash
sudo crontab -e
# add:
15 3 * * * /opt/zerde-deploy/deploys/backup.sh >> /var/log/zerde-backup.log 2>&1
```

Backups land in the `sitedata` volume under `/data/backups` (14 days). Copy them off the server regularly if the sites matter.

## 6. Everyday operations

- Update: run `deploy.sh` again.
- Logs: `cd /opt/zerde-deploy/deploys && docker compose logs -f app caddy`.
- Add an admin: edit `ADMIN_EMAILS` in `.env`, then `docker compose up -d`.
- Without SMTP configured, sign-in codes appear in `docker compose logs app`.

## 7. Google sign-in client

Google Cloud Console → APIs & Services → Credentials → Create credentials → OAuth client ID → Web application.
Authorised JavaScript origins: `https://deploy.zerde.ai`, `http://localhost:8000`, `http://localhost`.
No redirect URIs are needed. Copy the client ID into `GOOGLE_CLIENT_ID`.

## Limits worth knowing

- Let's Encrypt issues at most 50 new certificates per week for zerde.ai; Caddy falls back to ZeroSSL. For more new sites per week, switch `*.deploy` to one wildcard certificate via DNS-01 (needs your DNS provider's API token and Caddy DNS plugin).
- Sites on `*.deploy.zerde.ai` share cookies' "site" with `zerde.ai`. Keep the allowlist to trusted people, or move sites to a separate domain by changing `SITES_DOMAIN` and DNS.
````

Create `README.md` at the repository root:
````markdown
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
````

- [ ] **Step 4: Validate the Caddy configuration**

Run:
```bash
docker run --rm -v "$PWD/deploys":/etc/caddy -e APP_DOMAIN=deploy.zerde.ai -e SITES_DOMAIN=deploy.zerde.ai -e ACME_EMAIL=admin@zerde.ai caddy:2.10 caddy validate --config /etc/caddy/Caddyfile
docker run --rm -v "$PWD/deploys":/etc/caddy caddy:2.10 caddy validate --config /etc/caddy/Caddyfile.local
```
Expected: both print `Valid configuration`.

- [ ] **Step 5: Run the local stack and the smoke + load test**

Run:
```bash
cd deploys && docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.local.yml ps   # both services "running"/"healthy"
brew list oha >/dev/null 2>&1 || brew install oha
./smoke_local.sh
```
Expected: "all serving checks passed", oha summary with `Success rate: 100.00%`, "Load test passed".

If a Caddy check fails (for example the custom 404 inside `handle_errors`), fix `sites.caddy`, re-run `docker compose ... up -d` and the script. Do not weaken the checks.

- [ ] **Step 6: Record results and stop the local stack**

Write `deploys/loadtest-results.md` with: date, machine (CPU/RAM from `sysctl -n machdep.cpu.brand_string` and `sysctl -n hw.memsize`), Docker Desktop note (local numbers include Docker Desktop's port-forwarding overhead), and the oha summary lines (Success rate, Requests/sec, p50/p95/p99 latency). Then:
```bash
cd deploys && docker compose -f docker-compose.yml -f docker-compose.local.yml down
```

- [ ] **Step 7: Commit**

```bash
git add .dockerignore .gitignore README.md deploys
git commit -m "feat(deploys): Docker + Caddy stack, on-demand TLS, smoke and load test scripts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Browser QA and design review (controller)

Done by the controller with the built-in browser (it needs eyes on the result), using the `ui-ux-pro-max` pre-delivery checklist.

- [ ] **Step 1:** Start the dev server: `cd backend && ADMIN_EMAILS=admin@example.com uv run uvicorn app.main:create_app --factory --port 8000` (sign-in codes appear in this terminal).
- [ ] **Step 2:** For each language (EN, RU, KK) at 1280 px and 390 px, light and dark: login, code entry, dashboard (empty and with 3 sites), drop-zone states (idle, drag-over, uploading, error), site page, admin, no-access page, 404. Check: no overflow or clipped Kazakh/Russian strings, visible focus rings, keyboard flow (Tab through login → code → dashboard → site actions), 44 px touch targets on mobile, contrast.
- [ ] **Step 3:** Exercise real drag-and-drop of a folder, a `.zip` and a single `.html`; redeploy; restore; rename; delete; copy link.
- [ ] **Step 4:** Fix findings in the templates/CSS/JS with small commits; re-run `cd backend && uv run pytest -q` after each.

---

### Task 18: Google client ID and production deploy (controller, with the user)

Each outward-facing step needs the user's explicit OK in chat at the time.

- [ ] **Step 1 — Google client ID:** with the user's permission, in their signed-in Chrome (Claude in Chrome): Google Cloud Console → create project "zerde-deploy" → OAuth consent screen (External, app name "zerde/deploy", support email = user's, authorised domain `zerde.ai`, scopes: openid, email, profile only) → Credentials → OAuth client ID (Web) with origins `https://deploy.zerde.ai`, `http://localhost:8000`, `http://localhost`. Pause before every create/accept/publish click. The client ID is public; put it in `backend/.env` for local testing (`GOOGLE_CLIENT_ID=...`) and give it to the user for the server `.env`.
- [ ] **Step 2 — Local Google check:** restart the dev server with `GOOGLE_CLIENT_ID` set and sign in with Google in the browser.
- [ ] **Step 3 — SSH key:** generate `~/.ssh/zerde_deploy`, give the user the `ssh-copy-id` command to run themselves (they type their password), and remind them to change the password that was shared in chat.
- [ ] **Step 4 — DNS check:** `dig +short deploy.zerde.ai` and `dig +short test.deploy.zerde.ai` return `212.116.232.74`. If not, give the user the DNS table from `server-setup.md`.
- [ ] **Step 5 — Server prep and deploy (ask first):** Docker + ufw per `server-setup.md`, `deploy.sh`, user fills `.env` secrets (`SECRET_KEY`, SMTP password) themselves.
- [ ] **Step 6 — Production verification:** `https://deploy.zerde.ai/healthz` is `ok`; sign in; deploy a sample; open `https://<name>.deploy.zerde.ai/`; run `oha -c 1000 -z 30s https://<name>.deploy.zerde.ai/` from the Mac and append the results to `deploys/loadtest-results.md`.
