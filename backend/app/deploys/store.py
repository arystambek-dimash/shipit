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
