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

    @contextmanager
    def staging(self) -> Iterator[Path]:
        """A new staging dir for one upload: locked while in use, removed afterwards.

        Every worker process runs cleanup_temp() when it starts, and uvicorn starts a new worker whenever
        one dies, possibly while another worker is still staging an upload. The lock tells cleanup_temp to
        leave this dir alone. A process that dies releases its locks, so its leftovers do get cleaned.
        """
        while True:
            path = self.new_staging_dir()
            try:
                fd = os.open(path, os.O_RDONLY)
            except FileNotFoundError:  # a starting worker's cleanup removed it before we could lock it
                continue
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                if os.path.samestat(os.fstat(fd), os.stat(path)):
                    break
            except FileNotFoundError:
                pass
            except BaseException:
                os.close(fd)
                raise
            os.close(fd)  # removed while we waited for the lock: draw another one
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)  # already gone if it was published
            os.close(fd)

    def cleanup_temp(self) -> None:
        """Remove what earlier processes left in tmp_dir, sparing staging dirs a live upload holds."""
        for child in self.tmp_dir.iterdir():
            if child.is_symlink() or not child.is_dir():
                child.unlink(missing_ok=True)
                continue
            try:
                fd = os.open(child, os.O_RDONLY)
            except FileNotFoundError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue  # another worker is staging an upload in it
                shutil.rmtree(child, ignore_errors=True)
            finally:
                os.close(fd)

    def publish(self, name: str, staging: Path, deploy_id: int) -> None:
        target = self.deploy_dir(name, deploy_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Deploy ids are not unique forever (SQLite reuses a freed rowid), so whatever sits at the
        # target is a leftover of an earlier failed or crashed publish: replace it.
        self._remove(target, ignore_errors=False)
        staging.rename(target)
        try:
            target.chmod(0o755)  # mkdtemp creates 0700; the web server must be able to read it
            self.activate(name, deploy_id)
        except BaseException:
            self._remove(target)  # no half-published tree for the next attempt to trip over
            raise

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

    def discard(self, name: str, deploy_id: int) -> None:
        """Remove one deploy tree, and `current` as well if it still points at it (never left dangling)."""
        if self.current_deploy(name) == deploy_id:
            self.current_dir(name).unlink()
        self._remove(self.deploy_dir(name, deploy_id))

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

    @staticmethod
    def _remove(path: Path, *, ignore_errors: bool = True) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path, ignore_errors=ignore_errors)

    @contextmanager
    def lock(self, name: str) -> Iterator[None]:
        with open(self.locks_dir / f"{name}.lock", "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
