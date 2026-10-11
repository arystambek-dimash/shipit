# Zerde Deploy — design

Date: 2026-10-12 · Status: approved in brainstorming, pending spec review

A small, fast, self-hosted "Netlify Drop" for static HTML projects. Signed-in,
allowlisted people drop a folder / files / `.zip` / single `.html` and get a live
site at `https://<name>.deploy.zerde.ai`.

## 1. Goals and non-goals

Goals
- Deploy static sites by drag-and-drop; redeploy, roll back, rename, delete.
- Sign in with Google, or with email + 6-digit confirmation code.
- Only allowlisted people may deploy; admins manage the allowlist.
- UI in English, Russian, Kazakh (Cyrillic).
- Deployed sites handle **1000 concurrent visitors** without errors.
- Light: small pages, few dependencies, no frontend framework, no build step.
- Plain, comfortable, non-generic interface (no "AI slop").

Non-goals (YAGNI)
- Server-side code for deployed sites (functions, builds, forms).
- Git integration, teams/orgs, billing, custom domains per site, analytics.
- Kazakh Latin script (Cyrillic only for now).

## 2. Hosting and domains

- Server: Ubuntu, `212.116.232.74` / `2a00:5da0:1:201::522`.
- App (dashboard + API): `https://deploy.zerde.ai` (`APP_DOMAIN`).
- Sites: `https://<name>.deploy.zerde.ai` (`SITES_DOMAIN=deploy.zerde.ai`).
- DNS (done by the owner): `A deploy → 212.116.232.74`, `A *.deploy → 212.116.232.74`,
  `AAAA` equivalents → `2a00:5da0:1:201::522`.
- TLS: Caddy. App domain gets a normal certificate; site subdomains use
  **on-demand TLS** gated by an `ask` endpoint on the app that returns 200 only for
  existing site names (no wildcard cert, no DNS-provider API needed).
- Known risk, accepted: `*.deploy.zerde.ai` is same-site with `zerde.ai`. With an
  allowlist of trusted deployers the risk is low; `SITES_DOMAIN` is configurable so
  sites can move to a separate registrable domain later.
- Local development: app at `http://localhost:8000`, sites at
  `http://<name>.localhost:8000` served by the app itself (dev-only handler,
  `SERVE_SITES_IN_APP=true`); production never serves sites from Python.

## 3. Repository layout

```
frontend/                      everything the browser receives
  templates/                   Jinja2: base, login, login_code, dashboard, site, admin, error
  static/css/app.css           hand-written CSS, design tokens, light + dark
  static/js/app.js             drop zone, folder walk, upload progress, copy, small UI bits
  static/fonts/                subset WOFF2 (Latin + Cyrillic incl. Kazakh letters)
  i18n/en.json ru.json kk.json
backend/
  app/main.py                  app factory, route registration, middleware
  app/config.py                settings from environment / .env
  app/db.py                    SQLite connection (WAL), schema, small query helpers
  app/i18n.py                  language negotiation + t() for templates and emails
  app/access.py                allowlist + admin checks
  app/auth/google.py           Google ID-token verification (PyJWT + Google JWKS)
  app/auth/email_code.py       issue / verify codes, rate limits
  app/auth/mailer.py           SMTP sender; console fallback when SMTP is unset
  app/auth/sessions.py         session cookie, current-user dependency, origin check
  app/deploys/names.py         name generation + validation
  app/deploys/ingest.py        upload -> safe staged file tree (+ gzip precompression)
  app/deploys/store.py         atomic publish, rollback, rename, delete, pruning
  app/routes/                  pages.py, auth.py, sites.py, admin.py, internal.py
  tests/                       pytest unit + integration tests
  pyproject.toml
deploys/                       shipping the app to the server
  Dockerfile  docker-compose.yml  Caddyfile  .env.example  deploy.sh  server-setup.md
docs/superpowers/specs/        this document
```

Runtime data (never in git): `/data/app.db`, `/data/sites/`, Caddy's `/data/caddy`.

## 4. Architecture

```
Browser ──HTTPS──► Caddy :443 (HTTP/2 + HTTP/3, automatic TLS)
                    ├─ deploy.zerde.ai     → reverse_proxy app:8000
                    └─ *.deploy.zerde.ai   → file_server root /data/sites/<name>/current
                                             (precompressed gzip, ETag/304, 404.html)
app (FastAPI, uvicorn, 2 workers)
  ├─ SQLite /data/app.db (WAL, busy_timeout)
  ├─ /data/sites/<name>/deploys/<deploy_id>/…   immutable deploy trees
  │   /data/sites/<name>/current -> deploys/<id> (symlink, swapped atomically)
  └─ SMTP (env) for confirmation codes
```

The hot path for visitors of deployed sites is **Caddy → disk only**. Python handles
the dashboard, auth, uploads and the TLS `ask` check.

## 5. Data model (SQLite)

- `users(id, email UNIQUE, name, avatar_url, google_sub UNIQUE NULL, lang, created_at, last_login_at)`
- `allowlist(id, pattern UNIQUE, added_by, created_at)` — pattern is `user@x.com` or `@x.com`
- `sessions(token_hash PK, user_id, created_at, expires_at, last_seen_at)`
- `login_codes(id, email, code_hash, ip, created_at, expires_at, attempts, consumed_at)`
- `sites(id, name UNIQUE, owner_id, current_deploy_id NULL, created_at, updated_at)`
- `deploys(id, site_id, created_by, file_count, total_bytes, created_at)`

Emails are stored lowercased and trimmed. Timestamps are UTC unix seconds.

## 6. Authentication and access

Google
- Frontend uses Google Identity Services "Sign in with Google" button (popup/redirect
  agnostic, returns an ID token). Only `GOOGLE_CLIENT_ID` is needed — no client secret.
- Backend verifies with PyJWT against Google JWKS (cached): signature (RS256),
  `aud == GOOGLE_CLIENT_ID`, `iss in {accounts.google.com, https://accounts.google.com}`,
  `exp`, and `email_verified == true`. Links to an existing user by email or `google_sub`.
- If `GOOGLE_CLIENT_ID` is empty the Google button is hidden.

Email code
- `POST /auth/email/start {email}` → generates a 6-digit code (CSPRNG), stores
  `sha256(secret_key || email || code)`, valid 10 min, sends email in the user's UI language.
- Response is identical whether or not the email exists or is allowlisted.
- Limits: 1 send per 60 s and 5 per hour per email; 20 sends per hour per IP.
  Excess → 429 with retry-after (shown as a countdown in the UI).
- `POST /auth/email/verify {email, code}` → max 5 attempts per code, then the code is
  burned; constant-time comparison; success consumes the code and creates the user if new.
- Without SMTP configured, the code is logged to the console (development).

Sessions
- 32-byte random token in cookie `__Host-zd_session` (HttpOnly, Secure, SameSite=Lax,
  Path=/). On plain-HTTP localhost the cookie is named `zd_session` without `Secure`.
- Only `sha256(token)` is stored; 30-day expiry, sliding refresh at most daily.
- Every state-changing request (POST/PATCH/DELETE) must carry `Origin` (or `Referer`)
  equal to the app origin, else 403. This blocks CSRF from deployed sites.
- Logout deletes the session row and the cookie.

Access
- Anyone may sign in. Deploy/manage actions require: user is admin, or email matches an
  allowlist entry (exact email or `@domain`). Others see a clear "no access yet" screen.
- Admins = `ADMIN_EMAILS` (comma-separated env). Admin page: list / add / remove
  allowlist entries; list all sites.
- Users manage only their own sites; admins can manage all.

## 7. Deploy pipeline

Input (one request, `multipart/form-data`, streamed to a temp dir — never fully in memory)
- Folder or multiple files: parts `files[]` with a parallel `paths[]` list of relative paths
  (browser walks dropped folders with `webkitGetAsEntry`).
- `.zip`: single part; extracted server-side.
- Single `.html`: becomes `index.html`. If a tree has exactly one top-level folder and
  no root `index.html`, that folder is unwrapped.

Validation (reject the whole upload with a translated error)
- Total ≤ 50 MB uploaded, ≤ 2000 files, ≤ 25 MB per file; zip uncompressed total ≤ 200 MB
  and per-entry compression ratio ≤ 100 (zip-bomb guard).
- Paths: normalized, relative, no `..`, no absolute/drive paths, no NUL/control chars,
  no symlink entries, ≤ 255 chars. Skipped silently: `.git/`, `node_modules/`,
  `__MACOSX/`, `.DS_Store`, `Thumbs.db`.
- Must contain an `index.html` at the root after unwrapping.

Publish
1. Build tree in `/data/sites/<name>/deploys/<new_id>.tmp/`.
2. Precompress text files ≥ 1 KB (`html css js mjs svg json xml txt map webmanifest`)
   to `.gz` siblings (stdlib gzip, level 6).
3. `rename` `.tmp` → `<new_id>`; create `current.new` symlink → `deploys/<new_id>`;
   `os.replace(current.new, current)` (atomic swap). Insert `deploys` row, set
   `sites.current_deploy_id` in the same transaction.
4. Keep the newest 10 deploys per site; delete older trees.

Other operations
- Rollback: atomic symlink swap to an earlier kept deploy.
- Rename: validate new name, `rename` the site dir, update DB (old URL stops working).
- Delete: requires typing the site name; removes DB rows and the directory.

Names
- Generated: `adjective-noun-NN` (e.g. `quiet-river-42`), collision-checked.
- Rules: `^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$`, no `--`, not reserved. Reserved:
  `www api app admin deploy deploys mail smtp static assets cdn auth login logout signup
  status internal dashboard help support docs blog zerde root test dev staging`.

## 8. Serving deployed sites (performance target: 1000 concurrent visitors)

Caddy site block for `*.{SITES_DOMAIN}`:
- `tls { on_demand }`; global `on_demand_tls { ask http://app:8000/internal/tls-ask }`.
- Map host → name; `root /data/sites/{name}/current`; `file_server { precompressed gzip }`;
  `encode zstd gzip` fallback for files without `.gz`.
- Headers: HTML `Cache-Control: public, max-age=0, must-revalidate` (ETag → cheap 304s);
  other assets `public, max-age=300`; `X-Content-Type-Options: nosniff`.
- Errors: 404 → site's `/404.html` if present, else a small built-in page.
- HTTP/2 and HTTP/3 (open UDP 443). Container `ulimits nofile 65535`.
- The `ask` endpoint is cheap (indexed lookup) and only hit on first certificate issuance.
  `/internal/*` is reachable only on the Docker network; Caddy returns 404 for it on the
  public app domain.
- Certificate limit: Let's Encrypt allows 50 new certificates per registered domain per
  week (Caddy falls back to ZeroSSL automatically). If sites are created faster than that,
  switch the site block to one wildcard certificate via DNS-01 (needs the DNS provider's
  Caddy plugin and an API token set by the owner) — a Caddyfile-only change.

Acceptance: load test with `oha` (or `hey`) — 1000 concurrent connections for 30 s
against a deployed sample site: **0 errors**, p95 latency < 300 ms locally; re-run on the
server after deploy and record results (server CPU/RAM checked first).

## 9. Frontend

Principles: a tool, not a landing page. Plain sentences, real states, keyboard-friendly.

Pages
- `/login`: product name, "Continue with Google", divider, email field → `/login/code`.
- `/login/code`: single `inputmode=numeric autocomplete=one-time-code` field, resend with
  countdown, "use a different email".
- `/` (dashboard): large drop zone (click to pick files / folder / zip), upload progress
  with cancel, then the site list as rows: name + link (mono), last deploy (relative time),
  size, menu (open, copy link, settings). Empty state explains the three accepted inputs.
- `/sites/<name>`: live link + copy, drop zone for redeploy, deploy history with
  "Roll back", rename, delete (type name to confirm).
- `/admin`: allowlist add/remove, all sites list.
- No-access screen for signed-in but not allowlisted users.
- Header: product name, language switch `EN · RU · KK`, user menu (email, sign out).

Visual system (finalised with the design skill during implementation)
- Warm paper background, near-black ink, one accent colour for primary actions,
  1 px borders instead of shadows, generous spacing, automatic dark mode.
- IBM Plex Sans + IBM Plex Mono, self-hosted subset WOFF2 (must include
  ә ғ қ ң ө ұ ү һ і); `font-display: swap`.
- Banned: gradients, glassmorphism, glow blobs, emoji icons, centered marketing hero,
  three-card feature grids, hype copy.
- Icons: a handful of inline SVGs.

Weight budget (per page, transferred, gzip): HTML ≤ 15 KB, CSS ≤ 12 KB, JS ≤ 8 KB,
fonts ≤ 80 KB total (cached), no third-party requests except Google Identity Services
script on `/login` (only when Google sign-in is enabled).

## 10. Internationalisation

- `en.json`, `ru.json`, `kk.json` with identical key sets (enforced by a test).
- Language: `?lang=` → cookie `zd_lang` (1 year) → `Accept-Language` → `en`.
- Templates use `t("key", **params)`; JS receives only the strings it needs via a
  `<script type="application/json">` block.
- Email subject/body translated. Relative times and sizes formatted per language.

## 11. Configuration (`.env`)

`APP_DOMAIN`, `SITES_DOMAIN`, `APP_ORIGIN` (derived), `SECRET_KEY`, `GOOGLE_CLIENT_ID`,
`ADMIN_EMAILS`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`,
`SMTP_STARTTLS`, `DATA_DIR`, `SERVE_SITES_IN_APP`, `MAX_UPLOAD_MB`, `MAX_FILES`.
The owner sets secrets (SMTP password, SECRET_KEY) on the server; they are never
committed. `.env.example` documents every key.

## 12. Errors and edge cases

- Upload errors are specific and translated (too big, too many files, no index.html,
  unsafe path, bad zip, not allowed).
- Network failure mid-upload → retry button; server cleans temp dirs on failure and on start.
- Concurrent deploys to the same site are serialised with a per-site file lock;
  the last one to finish wins.
- Expired session → redirect to `/login` with a return path.
- JS disabled: login by email and file-input upload still work (progressive enhancement).

## 13. Testing

- Unit (pytest): names; ingest (zip-slip, absolute paths, symlinks, bombs, limits,
  unwrapping, single html, skipped dirs); store (atomic swap, rollback, pruning, rename,
  delete); email codes (expiry, attempts, rate limits, hashing); sessions (cookie flags,
  expiry, origin check); access (email/domain patterns, admin); google (valid token,
  wrong aud/iss/exp/unverified email — JWKS mocked); i18n key parity.
- Integration (FastAPI TestClient): email sign-in → deploy zip → fetch via dev site
  handler → redeploy → rollback → rename → delete; non-allowlisted user is blocked;
  CSRF origin check blocks cross-origin POST; tls-ask returns 200/404 correctly.
- Browser QA (built-in browser, localhost): every page in EN/RU/KK, desktop + 390 px,
  light + dark, keyboard flow; design review pass.
- Load test as in §8.

## 14. Operations

- `docker-compose.yml`: `app` (python:3.12-slim, non-root user, uvicorn 2 workers,
  healthcheck `/healthz`) and `caddy:2` (ports 80, 443/tcp, 443/udp; volumes
  `data`, `caddy_data`, `caddy_config`; `ulimits nofile 65535`).
- `deploy.sh`: rsync repo to the server over SSH (key auth only), `docker compose up -d --build`,
  smoke-check `/healthz`.
- `server-setup.md`: DNS records, `ufw` (22, 80, 443/tcp, 443/udp), Docker install,
  first run, nightly `sqlite3 .backup` cron, how to add admins.
- Google OAuth client: Web application, authorised JavaScript origins
  `https://deploy.zerde.ai`, `http://localhost:8000`, `http://localhost`.
