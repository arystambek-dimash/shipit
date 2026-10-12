import errno
import gzip
import io
import os
import stat
import zipfile

import pytest

from app.deploys import ingest
from app.deploys.ingest import MB, Limits, UploadError, clean_path, stage_files, stage_zip

LIMITS = Limits(max_total_bytes=1_000_000, max_files=10, max_file_bytes=500_000, max_unzipped_bytes=3 * MB)


def zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


def error_code(fn, *args):
    with pytest.raises(UploadError) as info:
        fn(*args)
    return info.value.code


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("index.html", "index.html"),
        ("a\\b.css", "a/b.css"),
        ("./a//b.js", "a/b.js"),
        ("site/.git/config", None),
        ("node_modules/x/y.js", None),
        (".DS_Store", None),
        ("__MACOSX/._a", None),
        ("сайт/главная.html", "сайт/главная.html"),
    ],
)
def test_clean_path(raw, expected):
    assert clean_path(raw) == expected


@pytest.mark.parametrize("raw", ["../x", "a/../../x", "/etc/passwd", "C:/x", "a\x00b", "a\nb", "", "x" * 300])
def test_clean_path_rejects_unsafe(raw):
    assert error_code(clean_path, raw) == "unsafe_path"


def test_stage_files_writes_tree_and_precompresses(tmp_path):
    page = b"<html>" + b"x" * 2000 + b"</html>"
    tree = stage_files([("index.html", io.BytesIO(page)), ("css/a.css", io.BytesIO(b"body{}"))], tmp_path / "d", LIMITS)
    assert (tree.file_count, tree.total_bytes) == (2, len(page) + 6)
    assert (tmp_path / "d/index.html").read_bytes() == page
    assert gzip.decompress((tmp_path / "d/index.html.gz").read_bytes()) == page
    assert not (tmp_path / "d/css/a.css.gz").exists()


def test_single_html_becomes_index(tmp_path):
    stage_files([("landing.html", io.BytesIO(b"hi"))], tmp_path / "d", LIMITS)
    assert (tmp_path / "d/index.html").read_bytes() == b"hi"
    assert not (tmp_path / "d/landing.html").exists()


def test_single_top_folder_is_unwrapped(tmp_path):
    tree = stage_files([("proj/index.html", io.BytesIO(b"i")), ("proj/css/a.css", io.BytesIO(b"c"))], tmp_path / "d", LIMITS)
    assert (tmp_path / "d/index.html").read_bytes() == b"i"
    assert (tmp_path / "d/css/a.css").read_bytes() == b"c"
    assert tree.file_count == 2


def test_folder_named_like_its_parent_is_unwrapped(tmp_path):
    stage_files([("site/site/index.html", io.BytesIO(b"i"))], tmp_path / "d", LIMITS)
    assert (tmp_path / "d/index.html").read_bytes() == b"i"


def test_skipped_files_are_not_written(tmp_path):
    tree = stage_files(
        [("index.html", io.BytesIO(b"i")), ("node_modules/a.js", io.BytesIO(b"x")), (".DS_Store", io.BytesIO(b"x"))],
        tmp_path / "d",
        LIMITS,
    )
    assert tree.file_count == 1
    assert not (tmp_path / "d/node_modules").exists()


def test_stage_files_errors(tmp_path):
    assert error_code(stage_files, [], tmp_path / "a", LIMITS) == "empty"
    assert error_code(stage_files, [("readme.txt", io.BytesIO(b"x")), ("a.css", io.BytesIO(b"y"))], tmp_path / "b", LIMITS) == "no_index"
    assert error_code(stage_files, [("index.html", io.BytesIO(b"x" * 600_000))], tmp_path / "c", LIMITS) == "file_too_big"
    big = [(f"f{i}.html", io.BytesIO(b"x" * 400_000)) for i in range(3)]
    assert error_code(stage_files, big, tmp_path / "d", LIMITS) == "too_big"
    many = [(f"f{i}.txt", io.BytesIO(b"x")) for i in range(11)]
    assert error_code(stage_files, many, tmp_path / "e", LIMITS) == "too_many_files"
    dup = [("index.html", io.BytesIO(b"a")), ("./index.html", io.BytesIO(b"b"))]
    assert error_code(stage_files, dup, tmp_path / "f", LIMITS) == "duplicate_path"


def test_stage_zip(tmp_path):
    tree = stage_zip(zip_bytes({"site/index.html": b"<h1>hi</h1>", "site/img/a.png": b"\x89PNG"}), tmp_path / "d", LIMITS)
    assert (tmp_path / "d/index.html").read_bytes() == b"<h1>hi</h1>"
    assert (tmp_path / "d/img/a.png").exists()
    assert tree.file_count == 2


def test_zip_slip_is_rejected(tmp_path):
    assert error_code(stage_zip, zip_bytes({"../evil.html": b"x", "index.html": b"i"}), tmp_path / "d", LIMITS) == "unsafe_path"
    assert not (tmp_path / "evil.html").exists()


def test_zip_symlink_is_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("index.html")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    buf.seek(0)
    assert error_code(stage_zip, buf, tmp_path / "d", LIMITS) == "unsafe_path"


def test_zip_bomb_is_rejected(tmp_path):
    bomb = zip_bytes({"index.html": b"i", "zeros.txt": b"\0" * (2 * MB)})
    assert error_code(stage_zip, bomb, tmp_path / "d", LIMITS) == "too_big"


def test_bad_and_oversized_zips(tmp_path):
    assert error_code(stage_zip, io.BytesIO(b"not a zip"), tmp_path / "a", LIMITS) == "bad_zip"
    many = zip_bytes({f"f{i}.txt": b"x" for i in range(11)})
    assert error_code(stage_zip, many, tmp_path / "b", LIMITS) == "too_many_files"


def test_unwrap_survives_a_folder_named_like_the_staging_dir(tmp_path):
    stage_files([("a/.zd-unwrap/index.html", io.BytesIO(b"i"))], tmp_path / "d", LIMITS)
    assert (tmp_path / "d/index.html").read_bytes() == b"i"


def test_precompress_skips_names_whose_gz_sibling_would_be_too_long(tmp_path):
    name = "x" * 250 + ".css"
    stage_files([("index.html", io.BytesIO(b"i")), (name, io.BytesIO(b"c" * 2000))], tmp_path / "d", LIMITS)
    names = {p.name for p in (tmp_path / "d").iterdir()}
    assert name in names
    assert name + ".gz" not in names


def flagless_utf8_zip(entries):
    """A zip like macOS Finder (ditto) or Info-ZIP writes: UTF-8 names, but no UTF-8 flag (0x800).

    zipfile always sets the flag for a non-ASCII name, so each name is written as an ASCII placeholder of the
    same byte length and the bytes are swapped afterwards (names are stored twice: local and central header).
    """
    buf = io.BytesIO()
    swaps = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for index, (name, data) in enumerate(entries.items()):
            raw = name.encode("utf-8")
            placeholder = f"~{index:03d}".ljust(len(raw), "~").encode()
            if placeholder == raw:
                zf.writestr(name, data)
                continue
            swaps.append((placeholder, raw))
            zf.writestr(placeholder.decode(), data)
    archive = buf.getvalue()
    for placeholder, raw in swaps:
        assert archive.count(placeholder) == 2
        archive = archive.replace(placeholder, raw)
    return io.BytesIO(archive)


def test_zip_without_the_utf8_flag_keeps_cyrillic_names(tmp_path):
    archive = flagless_utf8_zip({
        "сайт/index.html": '<a href="привет.html">'.encode(),
        "сайт/привет.html": b"hi",
        "сайт/картинки/фото.png": b"\x89PNG",
    })
    with zipfile.ZipFile(archive) as zf:
        assert all(not info.flag_bits & 0x800 for info in zf.infolist())  # really flag-less
        assert "привет" not in " ".join(zf.namelist())  # and zipfile alone garbles the names
    archive.seek(0)
    stage_zip(archive, tmp_path / "d", LIMITS)
    names = sorted(str(p.relative_to(tmp_path / "d")) for p in (tmp_path / "d").rglob("*") if p.is_file())
    assert names == ["index.html", "картинки/фото.png", "привет.html"]


def test_zip_names_that_are_not_utf8_keep_the_cp437_reading(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", b"i")
        zf.writestr("~~~~.html", b"c")
    archive = buf.getvalue().replace(b"~~~~.html", "café.html".encode("cp437"))  # é is 0x82: not UTF-8
    stage_zip(io.BytesIO(archive), tmp_path / "d", LIMITS)
    assert (tmp_path / "d" / "café.html").read_bytes() == b"c"


def test_zip_skips_ignored_entries_before_any_limit(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", b"i")
        # Archive Utility stores node_modules/.bin/* as symlink entries; a skipped symlink is no threat.
        link = zipfile.ZipInfo("node_modules/.bin/vite")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "../vite/bin/vite.js")
        for i in range(LIMITS.max_files + 2):  # more skipped entries than the file limit
            zf.writestr(f".git/objects/{i:02d}", b"x")
            zf.writestr(f"__MACOSX/._f{i}.html", b"x")
        zf.writestr(".git/objects/pack/big.pack", b"\0" * (LIMITS.max_unzipped_bytes + MB))  # over size and ratio
        zf.writestr("Thumbs.db", b"x")
    buf.seek(0)
    tree = stage_zip(buf, tmp_path / "d", LIMITS)
    assert tree.file_count == 1
    assert sorted(p.name for p in (tmp_path / "d").iterdir()) == ["index.html"]


def test_zip_skipped_folders_still_reject_unsafe_paths(tmp_path):
    escape = zip_bytes({"index.html": b"i", "node_modules/../../evil.html": b"x"})
    assert error_code(stage_zip, escape, tmp_path / "d", LIMITS) == "unsafe_path"


def failing_open(code):
    def fake_open(*args, **kwargs):
        raise OSError(code, os.strerror(code))

    return fake_open


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EACCES, errno.EROFS, errno.EMFILE, errno.EIO])
def test_server_io_errors_are_not_blamed_on_the_file_name(tmp_path, monkeypatch, code):
    monkeypatch.setattr(ingest, "open", failing_open(code), raising=False)
    with pytest.raises(OSError) as info:
        stage_files([("index.html", io.BytesIO(b"i"))], tmp_path / "d", LIMITS)
    assert info.value.errno == code and not isinstance(info.value, UploadError)


@pytest.mark.parametrize("code", [errno.ENAMETOOLONG, errno.EILSEQ, errno.EINVAL])
def test_names_the_file_system_refuses_are_unsafe_paths(tmp_path, monkeypatch, code):
    monkeypatch.setattr(ingest, "open", failing_open(code), raising=False)
    assert error_code(stage_files, [("index.html", io.BytesIO(b"i"))], tmp_path / "d", LIMITS) == "unsafe_path"
