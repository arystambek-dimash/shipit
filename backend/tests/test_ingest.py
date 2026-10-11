import gzip
import io
import stat
import zipfile

import pytest

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
