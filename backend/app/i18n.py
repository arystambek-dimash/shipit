"""Translations from frontend/i18n/<lang>.json, language negotiation and small formatters."""
from __future__ import annotations

import json
from functools import lru_cache

from .config import FRONTEND_DIR

LANGS = ("en", "ru", "kk")
DEFAULT_LANG = "en"
LANG_COOKIE = "zd_lang"
LANG_LABELS = {"en": "Eng", "ru": "Рус", "kk": "Қаз"}
_ALIASES = {"kz": "kk"}


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    path = FRONTEND_DIR / "i18n" / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def t(lang: str, key: str, **params: object) -> str:
    text = catalog(lang).get(key) or catalog(DEFAULT_LANG).get(key) or key
    return text.format(**params) if params else text


def negotiate(query: str | None, cookie: str | None, accept_language: str | None) -> str:
    for candidate in (query, cookie):
        if candidate in LANGS:
            return candidate
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        code = _ALIASES.get(code, code)
        if code in LANGS:
            return code
    return DEFAULT_LANG


def format_size(lang: str, n_bytes: int) -> str:
    units = [t(lang, "unit.b"), t(lang, "unit.kb"), t(lang, "unit.mb"), t(lang, "unit.gb")]
    value, index = float(n_bytes), 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    text = f"{value:.0f}" if index == 0 or value >= 10 else f"{value:.1f}"
    if lang != "en":
        text = text.replace(".", ",")
    return f"{text} {units[index]}"


def format_ago(lang: str, seconds: int) -> str:
    if seconds < 60:
        return t(lang, "time.now")
    if seconds < 3600:
        return t(lang, "time.min", n=seconds // 60)
    if seconds < 86400:
        return t(lang, "time.hour", n=seconds // 3600)
    return t(lang, "time.day", n=seconds // 86400)
