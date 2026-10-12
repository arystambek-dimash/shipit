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
