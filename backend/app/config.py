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
