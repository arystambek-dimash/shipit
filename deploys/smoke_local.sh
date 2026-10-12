#!/usr/bin/env bash
# End-to-end check of the local Docker stack on http://localhost:8080:
# sign in with an email code (read from the app log), upload a sample site, check how Caddy
# serves it, then load-test it with 1000 concurrent connections. The load generator (`oha`) runs
# in a container on the compose network: the `loadtest` service in docker-compose.local.yml.
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
for path in / /css/app.css; do
  # A gzip-capable client (any browser) must get a plain 200, not 206 Partial Content.
  [ "$(status -H 'Accept-Encoding: gzip' "http://127.0.0.1:8080$path")" = 200 ] || fail "gzip request for $path is not 200"
  GZIP_HEADERS=$(headers -H 'Accept-Encoding: gzip' "http://127.0.0.1:8080$path")
  grep -qi '^content-encoding: gzip' <<<"$GZIP_HEADERS" || fail "no precompressed gzip for $path"
  if grep -qi '^content-range:' <<<"$GZIP_HEADERS"; then fail "gzip response for $path has Content-Range"; fi
done
headers http://127.0.0.1:8080/ | grep -qi '^cache-control: public, max-age=0, must-revalidate' || fail "HTML cache header"
headers http://127.0.0.1:8080/css/app.css | grep -qi '^cache-control: public, max-age=300' || fail "asset cache header"
ETAG=$(headers http://127.0.0.1:8080/ | awk 'tolower($1)=="etag:" {print $2}' | tr -d '\r')
[ "$(status -H "If-None-Match: $ETAG" http://127.0.0.1:8080/)" = 304 ] || fail "ETag revalidation"
[ "$(status http://127.0.0.1:8080/missing)" = 404 ] || fail "missing page is not 404"
curl -s -H "Host: $SITE" http://127.0.0.1:8080/missing | grep -q 'custom 404' || fail "site 404.html not used"
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: localhost:8080' 'http://127.0.0.1:8080/internal/tls-ask?domain=x')" = 404 ] \
  || fail "/internal is reachable through Caddy"
echo "   all serving checks passed"

echo "4. Load test: 1000 concurrent connections for 30 s (oha on the compose network -> caddy:80)"
$COMPOSE run --rm --no-deps -T loadtest -c 1000 -z 30s --no-tui --host "$SITE" -H "Accept-Encoding: gzip" http://caddy/ \
  | tee "$WORK/oha.txt"
grep -qE 'Success rate:[[:space:]]*100(\.0+)?%' "$WORK/oha.txt" || fail "some requests failed under load"
NON_200=$(awk '/^Status code distribution:/ { on = 1; next } on && /^ *\[/ { if ($0 !~ /\[200\]/) print; next } { on = 0 }' "$WORK/oha.txt")
[ -z "$NON_200" ] || fail "non-200 responses under load: $NON_200"
cp "$WORK/oha.txt" ./last-loadtest.txt
echo "Load test passed; full output in deploys/last-loadtest.txt"
