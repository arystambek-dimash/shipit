import re

import pytest

from app.deploys import names


@pytest.mark.parametrize("name", ["abc", "my-site", "a1b", "x" * 40, "quiet-river-42"])
def test_valid_names(name):
    assert names.is_valid_name(name)


@pytest.mark.parametrize(
    "name", ["ab", "x" * 41, "-abc", "abc-", "a--b", "ABC", "a_b", "a.b", "абв", "www", "admin", "deploy", "", "abc\n"]
)
def test_invalid_names(name):
    assert not names.is_valid_name(name)


def test_normalize_name():
    assert names.normalize_name("  My-Site ") == "my-site"


def test_generate_name_is_valid_and_avoids_taken():
    taken = set()
    for _ in range(200):
        name = names.generate_name(lambda n: n in taken)
        assert re.fullmatch(r"[a-z]+-[a-z]+-\d{2}", name)
        assert names.is_valid_name(name)
        assert name not in taken
        taken.add(name)


def test_generate_name_gives_up():
    with pytest.raises(RuntimeError):
        names.generate_name(lambda n: True)
