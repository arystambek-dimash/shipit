# Load test results — local Docker stack

Target from the spec (§8): 1000 concurrent connections for 30 s against a deployed sample site,
**0 errors**, p95 < 300 ms locally. Re-run on the server after deploy.

## Setup

- Date: 2026-10-12
- Machine: Apple M3 Pro, 11 cores, 18 GB RAM (`hw.memsize` 19327352832), macOS 27.0.1
- Docker Desktop 4.37.2 (Engine 27.4.0), Linux VM with 6 vCPUs and 6 GB RAM, gvisor networking.
  The VM was shared with 24 containers from other projects (one of them crash-looping), so the
  stack did not have the machine to itself.
- Stack: `caddy:2.10` (v2.10.2) + app image from `deploys/Dockerfile` (Python 3.12, uvicorn, 2 workers),
  started with `docker-compose.yml` + `docker-compose.local.yml`.
- Load generator: `oha` 1.16.0 on the Mac, run by `deploys/smoke_local.sh`
  (`oha -c 1000 -z 30s --host <site> -H "Accept-Encoding: gzip" http://127.0.0.1:<port>/`).
- Sample site: `index.html` (22 KB, 1.1 KB precompressed), `css/app.css`, `404.html`, uploaded by the script.
- Port: 8080 was taken by another project's `traefik` container, so these runs used host port **18080**
  (a scratch override that maps `18080:80` and sets `APP_DOMAIN`/`SITES_DOMAIN` to `localhost:18080`,
  plus a copy of `smoke_local.sh` with `8080` → `18080`). Nothing else about the stack or the checks changed.

**Local numbers include Docker Desktop's port forwarding**: every request from `oha` crosses the macOS →
VM user-space proxy before it reaches Caddy. On a Linux server, published ports go through iptables
instead, so the server run is the one that counts.

## Smoke checks

All serving checks passed in every run: sign-in by email code, zip upload, 200 for the index,
precompressed gzip, HTML and asset `Cache-Control`, ETag → 304, 404 for a missing page with the
site's own `404.html`, and `/internal/*` answered 404 by Caddy.

## `smoke_local.sh` load test (1000 connections, 30 s, through Docker Desktop)

| Run (local time) | Success rate | Requests/sec | p50 | p95 | p99 | Failed requests |
|---|---|---|---|---|---|---|
| 1 (03:17) | 99.55% | 21,383 | 15.4 ms | 215.5 ms | 299.7 ms | 2,871 of 640,651 |
| 2 (09:26) | 99.86% | 19,019 | 20.3 ms | 204.7 ms | 392.0 ms | 782 of 569,758 |
| 3 (09:29) | 99.40% | 22,644 | 14.2 ms | 190.0 ms | 308.7 ms | 4,054 of 678,510 |

The failures were `Connection reset by peer`, `connection error`, `error writing a body to connection`,
a few `operation was canceled`, and 9 `Invalid argument (os error 22)` in run 3. The 1000 requests in flight at the 30 s deadline
(`aborted due to deadline`) are not counted by `oha` and are left out above. The script's
`Success rate: 100` check failed in all three runs, so `deploys/last-loadtest.txt` was not written.

**p95 < 300 ms: met (190–216 ms). 0 errors: not met through Docker Desktop's port forwarding.**

## Where the errors come from

Caddy logged no errors in any run. Three extra measurements, using the same site:

| Path | Connections | Rate | Success | Requests/sec | p95 | p99 |
|---|---|---|---|---|---|---|
| Inside the Docker network (Python asyncio client, 5 processes, 30 s) | 1000 | unlimited | 100.00% (1,122,820 requests, 0 errors) | 37,093 | 155.6 ms | 284.4 ms |
| Through Docker Desktop, `oha -q 5000` | 1000 | 5,000/s cap | 100.00% | 4,997 | 64.0 ms | 313.0 ms |
| Through Docker Desktop, `oha -c 250` | 250 | unlimited | 100.00% | 20,810 | 37.2 ms | 68.5 ms |

Caddy serves 1000 concurrent keep-alive connections at 37k requests/sec with no errors when the
client sits on the Docker network. Through Docker Desktop's forwarder, either 1000 connections or
full speed alone is fine. Only the combination, 1000 connections at about 20k requests/sec, makes
the forwarder reset some connections. The client side was ruled out: the script sets
`ulimit -n` to 10240, and the shell started at 1,048,576. The Caddy container has
`nofile` 65535, and its `somaxconn` is 4096. The macOS `kern.ipc.somaxconn` (128) and Docker
Desktop's VM size (6 vCPUs, 6 GB) were left unchanged. The VM was also busy with other
projects' containers.

## Also seen: 206 for precompressed files

Every response in the table is `206 Partial Content` with `Content-Range: bytes 0-N/N+1`.
Caddy 2.10.x injects `Range: bytes=0-` when it serves a precompressed `.gz` sidecar, so that
Go sets `Content-Length`. As a result, any client that accepts gzip, browsers included, gets
206 for a full response. Upstream removed this in v2.11.0, which sets `Content-Length`
directly. Moving the image to `caddy:2.11` (and adding a smoke check that a gzip request
returns 200) fixes it.

## Still to do

Re-run `oha -c 1000 -z 30s` on the server against a deployed site after the first deploy,
after checking the server's CPU and RAM, and add the numbers here.
