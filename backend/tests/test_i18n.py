import re

import pytest

from app import i18n

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_all_languages_have_the_same_keys():
    keys = {lang: set(i18n.catalog(lang)) for lang in i18n.LANGS}
    assert keys["ru"] == keys["en"]
    assert keys["kk"] == keys["en"]


@pytest.mark.parametrize("lang", ["en", "ru", "kk"])
def test_no_empty_strings_and_same_placeholders(lang):
    for key, text in i18n.catalog(lang).items():
        assert text.strip(), f"{lang}:{key} is empty"
        assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(i18n.catalog("en")[key])), key


def test_kazakh_uses_kazakh_letters():
    text = " ".join(i18n.catalog("kk").values())
    for letter in "әғқңөұүі":
        assert letter in text


def test_t_formats_and_falls_back():
    assert i18n.t("en", "time.min", n=5) == "5 min ago"
    assert i18n.t("ru", "time.min", n=5) == "5 мин назад"
    assert i18n.t("kk", "no.such.key") == "no.such.key"


@pytest.mark.parametrize(
    "query,cookie,header,expected",
    [
        ("ru", "kk", "en", "ru"),
        (None, "kk", "ru-RU,ru;q=0.9", "kk"),
        (None, None, "ru-RU,ru;q=0.9,en;q=0.8", "ru"),
        (None, None, "kk-KZ", "kk"),
        (None, None, "kz", "kk"),
        ("de", "xx", "fr-FR", "en"),
        (None, None, None, "en"),
    ],
)
def test_negotiate(query, cookie, header, expected):
    assert i18n.negotiate(query, cookie, header) == expected


def test_format_size():
    assert i18n.format_size("en", 512) == "512 B"
    assert i18n.format_size("en", 1536) == "1.5 KB"
    assert i18n.format_size("ru", 1536) == "1,5 КБ"
    assert i18n.format_size("kk", 25 * 1024 * 1024) == "25 МБ"


def test_format_ago():
    assert i18n.format_ago("en", 10) == "just now"
    assert i18n.format_ago("ru", 125) == "2 мин назад"
    assert i18n.format_ago("kk", 7200) == "2 сағ бұрын"
    assert i18n.format_ago("en", 3 * 86400) == "3 d ago"
