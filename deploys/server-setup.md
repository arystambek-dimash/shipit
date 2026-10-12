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

The AAAA records rely on IPv6 being enabled on the compose network (`docker-compose.yml`, `networks.default.enable_ipv6`), so that Docker hands IPv6 visitors to Caddy with their real address. Docker 27 and later allocate the network's IPv6 subnet themselves and have `ip6tables` on by default; do not set `"ip6tables": false` in `/etc/docker/daemon.json`. After the first deploy, `docker network inspect zerde-deploy_default --format '{{.EnableIPv6}}'` must print `true`. If it prints `false` (a network created before this setting), run `docker compose down && docker compose up -d` once in `/opt/zerde-deploy/deploys`; volumes and certificates are kept. Until it prints `true`, leave the AAAA records out: every IPv6 visitor would reach the app from the same Docker gateway address and share one sign-in rate limit (20 codes per hour).

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

`deploy.sh` finishes by checking that the app answers `/healthz` and that `https://deploy.zerde.ai/healthz` answers through Caddy, and prints `OK: …`. If either check fails, it exits non-zero and prints the last app or Caddy log lines (an empty `SECRET_KEY` is the usual cause). The first visit to a new site's address takes a few seconds while its certificate is issued.

## 5. Backups

```bash
sudo crontab -e
# add:
15 3 * * * /opt/zerde-deploy/deploys/backup.sh >> /var/log/zerde-backup.log 2>&1
```

Backups land in the `sitedata` volume under `/data/backups` (14 days). Copy them off the server regularly if the sites matter.

## 6. Everyday operations

- Update: run `deploy.sh` again. It rebuilds the app, and when the `Caddyfile`, `sites.caddy` or the sites' 404 page changed it validates the new Caddy configuration and recreates the caddy container (certificates are kept; open connections drop once). An invalid configuration stops the script and leaves the running Caddy as it was.
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
