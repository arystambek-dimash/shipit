"""Turn an upload (files with relative paths, or a .zip) into a safe, publishable file tree."""
from __future__ import annotations

import gzip
import re
import shutil
import stat
import zipfile
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

MB = 1024 * 1024
CHUNK = 64 * 1024
SKIP_DIRS = frozenset({".git", "node_modules", "__MACOSX"})
SKIP_FILES = frozenset({".DS_Store", "Thumbs.db"})
COMPRESSIBLE = frozenset(
    {".html", ".htm", ".css", ".js", ".mjs", ".svg", ".json", ".xml", ".txt", ".map", ".webmanifest"}
)
PRECOMPRESS_MIN = 1024
ZIP_UTF8_FLAG = 0x800
_DRIVE = re.compile(r"^[A-Za-z]:")


class UploadError(Exception):
    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class Limits:
    max_total_bytes: int = 50 * MB
    max_files: int = 2000
    max_file_bytes: int = 25 * MB
    max_unzipped_bytes: int = 200 * MB
    max_ratio: int = 100


@dataclass(frozen=True)
class StagedTree:
    root: Path
    file_count: int
    total_bytes: int


class _Budget:
    def __init__(self, limits: Limits, max_bytes: int) -> None:
        self.limits = limits
        self.max_bytes = max_bytes
        self.files = 0
        self.bytes = 0


def _short(value: str) -> str:
    return value if len(value) <= 80 else value[:77] + "..."


def clean_path(raw: str) -> str | None:
    """Safe relative POSIX path, or None when the file should be skipped silently."""
    path = raw.replace("\\", "/")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path) or path.startswith("/") or _DRIVE.match(path):
        raise UploadError("unsafe_path", path=_short(raw))
    parts = [part for part in path.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise UploadError("unsafe_path", path=_short(raw))
    if any(part in SKIP_DIRS for part in parts) or parts[-1] in SKIP_FILES:
        return None
    joined = "/".join(parts)
    if len(joined) > 255:
        raise UploadError("unsafe_path", path=_short(raw))
    return joined


def _write(stream: BinaryIO, dest: Path, rel: str, budget: _Budget) -> None:
    budget.files += 1
    if budget.files > budget.limits.max_files:
        raise UploadError("too_many_files", n=budget.limits.max_files)
    target = dest / rel
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        out = open(target, "xb")
    except FileExistsError:
        raise UploadError("duplicate_path", path=_short(rel)) from None
    except OSError:
        raise UploadError("unsafe_path", path=_short(rel)) from None
    written = 0
    with out:
        while chunk := stream.read(CHUNK):
            written += len(chunk)
            budget.bytes += len(chunk)
            if written > budget.limits.max_file_bytes:
                raise UploadError("file_too_big", path=_short(rel), mb=budget.limits.max_file_bytes // MB)
            if budget.bytes > budget.max_bytes:
                raise UploadError("too_big", mb=budget.max_bytes // MB)
            out.write(chunk)


def stage_files(items: Iterable[tuple[str, BinaryIO]], dest: Path, limits: Limits) -> StagedTree:
    dest.mkdir(parents=True, exist_ok=True)
    budget = _Budget(limits, limits.max_total_bytes)
    for raw, stream in items:
        rel = clean_path(raw)
        if rel is not None:
            _write(stream, dest, rel, budget)
    return _finish(dest, budget)


def zip_entry_name(info: zipfile.ZipInfo) -> str:
    """The entry's file name as its author's system wrote it.

    Without the UTF-8 flag, zipfile decodes a name as cp437, as the format says. But macOS Finder (ditto)
    and Info-ZIP store UTF-8 names without setting the flag, so "привет.html" would come out as
    "╨┐╤Ç╨╕╨▓╨╡╤é.html" and every link to it would break. A name whose bytes are valid UTF-8 is read as
    UTF-8; any other name keeps the cp437 reading. ASCII names read the same either way.
    """
    if info.flag_bits & ZIP_UTF8_FLAG:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except UnicodeError:
        return info.filename


def stage_zip(source: Path | BinaryIO, dest: Path, limits: Limits) -> StagedTree:
    dest.mkdir(parents=True, exist_ok=True)
    budget = _Budget(limits, limits.max_unzipped_bytes)
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise UploadError("bad_zip") from None
    with archive:
        # Every name is checked (clean_path raises on "..", absolute and control-character paths), but
        # skipped entries (.git/, node_modules/, __MACOSX/, .DS_Store, Thumbs.db) are dropped before any
        # limit applies and are never opened: the limits are about the published tree.
        kept = []
        for info in archive.infolist():
            if not info.is_dir():
                name = zip_entry_name(info)
                rel = clean_path(name)
                if rel is not None:
                    kept.append((info, name, rel))
        if len(kept) > limits.max_files:
            raise UploadError("too_many_files", n=limits.max_files)
        if sum(info.file_size for info, _, _ in kept) > limits.max_unzipped_bytes:
            raise UploadError("too_big", mb=limits.max_unzipped_bytes // MB)
        for info, name, rel in kept:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise UploadError("unsafe_path", path=_short(name))
            if info.flag_bits & 0x1:
                raise UploadError("bad_zip")
            if info.file_size > MB and info.file_size > info.compress_size * limits.max_ratio:
                raise UploadError("too_big", mb=limits.max_unzipped_bytes // MB)
            try:
                with archive.open(info) as stream:
                    _write(stream, dest, rel, budget)
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError):
                raise UploadError("bad_zip") from None
    return _finish(dest, budget)


def _normalize_layout(dest: Path) -> None:
    for _ in range(3):
        if (dest / "index.html").exists():
            return
        children = list(dest.iterdir())
        if len(children) == 1 and children[0].is_dir():
            inner = children[0].rename(dest / f".zd-unwrap-{uuid.uuid4().hex}")
            for child in list(inner.iterdir()):
                child.rename(dest / child.name)
            inner.rmdir()
            continue
        if len(children) == 1 and children[0].suffix.lower() in {".html", ".htm"}:
            children[0].rename(dest / "index.html")
        return


def precompress(root: Path) -> None:
    for path in list(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in COMPRESSIBLE and path.stat().st_size >= PRECOMPRESS_MIN:
            gz_name = path.name + ".gz"
            if len(gz_name.encode()) > 255:
                continue
            with open(path, "rb") as src, open(path.with_name(gz_name), "wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as gz:
                    shutil.copyfileobj(src, gz, CHUNK)


def _finish(dest: Path, budget: _Budget) -> StagedTree:
    if budget.files == 0:
        raise UploadError("empty")
    _normalize_layout(dest)
    if not (dest / "index.html").is_file():
        raise UploadError("no_index")
    precompress(dest)
    return StagedTree(dest, budget.files, budget.bytes)
