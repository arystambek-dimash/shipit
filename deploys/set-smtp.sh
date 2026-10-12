#!/usr/bin/env bash
# Save Gmail SMTP settings into deploys/.env and restart the app.
# The app password is read from stdin (never from arguments, so it stays out of process lists and shell history):
#   read -rs -p "Gmail app password: " P; echo
#   printf '%s' "$P" | ssh ubuntu@212.116.232.74 /opt/zerde-deploy/deploys/set-smtp.sh you@gmail.com; unset P
set -euo pipefail
cd "$(dirname "$0")"
ADDRESS="${1:?usage: set-smtp.sh <gmail address>   (app password on stdin)}"
ZD_SMTP_PASSWORD="$(tr -d ' \r\n')"
[ -n "$ZD_SMTP_PASSWORD" ] || { echo "No app password on stdin" >&2; exit 1; }
test -f .env || { echo "deploys/.env is missing" >&2; exit 1; }
ZD_SMTP_PASSWORD="$ZD_SMTP_PASSWORD" python3 - "$ADDRESS" <<'PY'
import os
import pathlib
import re
import sys

address = sys.argv[1]
values = {
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "SMTP_STARTTLS": "true",
    "SMTP_USER": address,
    "SMTP_PASSWORD": os.environ["ZD_SMTP_PASSWORD"],
    "SMTP_FROM": f'"zerde/deploy <{address}>"',
}
env = pathlib.Path(".env")
text = env.read_text(encoding="utf-8")
for key, value in values.items():
    pattern = re.compile(rf"^{key}=.*$", re.M)
    line = f"{key}={value}"
    text = pattern.sub(lambda _: line, text) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"
env.write_text(text, encoding="utf-8")
os.chmod(env, 0o600)
print("SMTP settings saved for", address)
PY
docker compose up -d --force-recreate app >/dev/null
echo "App restarted with the new SMTP settings."
