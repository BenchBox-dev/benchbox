from __future__ import annotations

import os
import re
from collections.abc import Iterable
from functools import lru_cache

REDACTED = "[redacted]"
MIN_LENGTH = 8
GATEWAY_ENV = ("OMP_GATEWAY_TOKEN",)
BROKER_ENV = ("OMP_AUTH_BROKER_URL", "OMP_AUTH_BROKER_TOKEN")
PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "OPENAI_CODEX_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "META_API_KEY",
)
LEGACY_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "CODEX_API_KEY", "TYPESAFE_API_KEY")
NAMES = tuple(dict.fromkeys((*GATEWAY_ENV, *BROKER_ENV, *PROVIDER_ENV, *LEGACY_ENV)))
_CONTROL_CLASS = r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*"


def known(extra: Iterable[str] = ()) -> tuple[str, ...]:
    values = (*extra, *(os.environ.get(name, "") for name in NAMES))
    return tuple(dict.fromkeys(value for value in values if len(value) >= MIN_LENGTH))


@lru_cache(maxsize=256)
def _pattern(value: str) -> re.Pattern[str]:
    return re.compile(_CONTROL_CLASS.join(re.escape(char) for char in value))


def _spans(text: str, values: tuple[str, ...]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for value in values:
        pattern = _pattern(value)
        position = 0
        while (match := pattern.search(text, position)) is not None:
            spans.append(match.span())
            position = match.start() + 1
    return spans


def contains(text: str, extra: Iterable[str] = ()) -> bool:
    return bool(_spans(text, known(extra)))


def redact(text: str, extra: Iterable[str] = ()) -> str:
    spans = sorted(_spans(text, known(extra)))
    if not spans:
        return text
    pieces: list[str] = []
    cursor = 0
    start, end = spans[0]
    for next_start, next_end in spans[1:]:
        if next_start <= end:
            end = max(end, next_end)
            continue
        pieces.extend((text[cursor:start], REDACTED))
        cursor = end
        start, end = next_start, next_end
    pieces.extend((text[cursor:start], REDACTED, text[end:]))
    return "".join(pieces)
