"""Site names: the left-most label of <name>.<SITES_DOMAIN>."""
from __future__ import annotations

import re
import secrets
from typing import Callable

RESERVED = frozenset(
    "www api app admin deploy deploys mail smtp static assets cdn auth login logout signup "
    "status internal dashboard help support docs blog zerde root test dev staging".split()
)
ADJECTIVES = (
    "amber azure bold brave bright calm clever cosmic crisp eager gentle golden green happy "
    "humble jolly keen lively lucky mellow misty noble proud quick quiet rapid royal shiny "
    "silent smooth snowy solar steady sunny swift tidy vivid warm wild windy"
).split()
NOUNS = (
    "apple aspen birch breeze brook canyon cedar cloud comet coral delta dune eagle falcon "
    "fern field forest garden glade harbor hill island lake lark leaf meadow mesa moon oasis "
    "orbit otter peak pine planet prairie river rock sky steppe stone stream tulip valley willow"
).split()
_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")


def normalize_name(raw: str) -> str:
    return raw.strip().lower()


def is_valid_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name)) and "--" not in name and name not in RESERVED


def generate_name(exists: Callable[[str], bool]) -> str:
    for _ in range(50):
        name = f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{secrets.randbelow(90) + 10}"
        if not exists(name):
            return name
    raise RuntimeError("could not find a free site name")
