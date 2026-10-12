# Load test results — local Docker stack

Target from the spec (§8): 1000 concurrent connections for 30 s against a deployed sample site,
**0 errors**, p95 < 300 ms locally. Re-run on the server after deploy.

## Setup

- Date: 2026-10-12
- Machine: Apple M3 Pro, 11 cores, 18 GB RAM (`hw.memsize` 19327352832), macOS 27.0.1
- Docker Desktop 4.37.2 (Engine 27.4.0), Linux VM with 6 vCPUs and 6 GB RAM, gvisor networking.
  The VM was shared with 23–24 containers from other projects, so the stack did not have the
  machine to itself.
- Stack: `caddy:2.11` (v2.11.7) + app image from `deploys/Dockerfile` (Python 3.12, uvicorn, 2 workers),
  started with `docker-compose.yml` + `docker-compose.local.yml`.
- Load generator: `oha` 1.16.0 (`ghcr.io/hatoo/oha:1.16.0`, the `loadtest` service in
  `docker-compose.local.yml`), run by `deploys/smoke_local.sh` on the compose network straight
  against Caddy: `oha -c 1000 -z 30s --no-tui --host <site> -H "Accept-Encoding: gzip" http://caddy/`.
  It shares the VM's 6 vCPUs with Caddy and the app.
- Sample site: `index.html` (22 KB, 1.1 KB precompressed), `css/app.css`, `404.html`, uploaded by the script.
- Port: 8080 was taken by another project's `traefik` container, so these runs used host port **18080**
  (a scratch override that maps `18080:80` and sets `APP_DOMAIN`/`SITES_DOMAIN` to `localhost:18080`,
  plus a copy of `smoke_local.sh` with `8080` → `18080`). The override also allowlisted one extra admin
  address per run, because the email-code limit (5 codes per address per hour) had been used up by
  earlier runs. Nothing else about the stack or the checks changed.

## Smoke checks

All serving checks passed in every run: sign-in by email code, zip upload, 200 for the index,
`200` + `Content-Encoding: gzip` + no `Content-Range` for gzip requests to `/` and `/css/app.css`,
HTML and asset `Cache-Control`, ETag → 304, 404 for a missing page with the site's own `404.html`,
and `/internal/*` answered 404 by Caddy.

## `smoke_local.sh` load test (1000 connections, 30 s, on the compose network)

| Run (local time) | Success rate | Requests/sec | p50 | p95 | p99 | Responses | Status codes |
|---|---|---|---|---|---|---|---|
| 1 (09:45) | 100.00% | 45,494 | 1.3 ms | 147.6 ms | 244.4 ms | 1,364,321 | all 200 |
| 2 (09:46) | 100.00% | 45,515 | 1.4 ms | 144.2 ms | 233.9 ms | 1,364,904 | all 200 |
| 3 (09:47) | 100.00% | 45,167 | 1.4 ms | 147.6 ms | 240.8 ms | 1,354,377 | all 200 |

The only entries under `Error distribution` were the ~990 requests in flight at the 30 s deadline
(`aborted due to deadline`), which `oha` does not count as failures. Every run ended with
"Load test passed".

**0 errors: met. p95 < 300 ms: met (144–148 ms).**

## Earlier runs through Docker Desktop's port forwarder (`caddy:2.10`)

The first version of `smoke_local.sh` ran `oha` on the Mac against the published port
(`http://127.0.0.1:<port>/`), so every request crossed Docker Desktop's macOS → VM user-space proxy:

| Run (local time) | Success rate | Requests/sec | p50 | p95 | p99 | Failed requests |
|---|---|---|---|---|---|---|
| 1 (03:17) | 99.55% | 21,383 | 15.4 ms | 215.5 ms | 299.7 ms | 2,871 of 640,651 |
| 2 (09:26) | 99.86% | 19,019 | 20.3 ms | 204.7 ms | 392.0 ms | 782 of 569,758 |
| 3 (09:29) | 99.40% | 22,644 | 14.2 ms | 190.0 ms | 308.7 ms | 4,054 of 678,510 |

The failures were `Connection reset by peer`, `connection error`, `error writing a body to connection`,
a few `operation was canceled`, and 9 `Invalid argument (os error 22)` in run 3. Caddy logged no errors.
Three extra measurements located the cause:

| Path | Connections | Rate | Success | Requests/sec | p95 | p99 |
|---|---|---|---|---|---|---|
| Inside the Docker network (Python asyncio client, 5 processes, 30 s) | 1000 | unlimited | 100.00% (1,122,820 requests, 0 errors) | 37,093 | 155.6 ms | 284.4 ms |
| Through Docker Desktop, `oha -q 5000` | 1000 | 5,000/s cap | 100.00% | 4,997 | 64.0 ms | 313.0 ms |
| Through Docker Desktop, `oha -c 250` | 250 | unlimited | 100.00% | 20,810 | 37.2 ms | 68.5 ms |

Through the forwarder, either 1000 connections or full speed alone is fine; only the combination,
1000 connections at about 20k requests/sec, makes the forwarder reset some connections. On a Linux
server, published ports go through iptables instead of a user-space proxy. That is why the load
phase now runs on the compose network: it measures Caddy, not Docker Desktop.

## Fixed: 206 for precompressed files

With `caddy:2.10` (v2.10.2), every response in the forwarder runs above was `206 Partial Content`
with `Content-Range: bytes 0-N/N+1`: Caddy 2.10.x injects `Range: bytes=0-` when it serves a
precompressed `.gz` sidecar, so any client that accepts gzip, browsers included, got 206 for a full
response. Against `caddy:2.10`, the new smoke check fails with "gzip request for / is not 200"
(`HTTP/1.1 206 Partial Content`, `Content-Range: bytes 0-1166/1167`). With `caddy:2.11` (v2.11.7)
the same request returns `200 OK` with `Content-Encoding: gzip` and no `Content-Range`, and all
responses in the load test are 200.

## Still to do

Re-run the load test on the server against a deployed site after the first deploy, after checking
the server's CPU and RAM (`nproc`, `free -h`), and add the numbers here. From the server:

```bash
docker run --rm --network host --ulimit nofile=65535:65535 ghcr.io/hatoo/oha:1.16.0 \
  -c 1000 -z 30s --no-tui -H "Accept-Encoding: gzip" https://<name>.deploy.zerde.ai/
```

Pass: `Success rate: 100.00%`, only `[200]` under `Status code distribution`, p95 < 300 ms.
