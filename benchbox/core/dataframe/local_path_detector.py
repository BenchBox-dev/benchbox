from __future__ import annotations

import contextlib
import functools
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

REDACTED_PLACEHOLDER = "<redacted>"

NEUTRAL_PLACEHOLDERS = frozenset({"/data", "/output"})

_PATH_TAIL = r"[^\s'\",;)]*"
_PATH_BOUNDARY = r"(?=[/\\\s'\",;)\]}]|$)"

PRIVATE_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    rf"(?:~|/Users|/home|/root|/private|/var/folders|/var/run|/Volumes|/tmp|/mnt|/opt)/{_PATH_TAIL}"
    rf"|[A-Za-z]:\\{_PATH_TAIL}"
    rf"|[A-Za-z]:/(?!/){_PATH_TAIL}"
    r")"
    rf"|(?<![A-Za-z0-9_\\])\\{{2,}}[A-Za-z0-9_.$-]+\\+{_PATH_TAIL}"
    rf"|(?<![A-Za-z0-9_:/\\])//[A-Za-z0-9_.$-]+/[A-Za-z0-9_.$-]+{_PATH_TAIL}",
    flags=re.IGNORECASE,
)


def is_neutral_placeholder(text: str) -> bool:
    return text.rstrip("/\\") in NEUTRAL_PLACEHOLDERS


def _root_variants(root: str | Path) -> set[str]:
    text = str(root).rstrip("/\\")
    if len(text) <= 1 or not (text.startswith("/") or text[1:3] in (":\\", ":/")):
        return set()
    if is_neutral_placeholder(text):
        return set()
    variants = {text}
    with contextlib.suppress(OSError, RuntimeError):
        variants.add(str(Path(text).resolve()).rstrip("/\\"))
    return {variant for variant in variants if len(variant) > 1 and not is_neutral_placeholder(variant)}


def _root_alternatives(variants: Iterable[str]) -> list[str]:
    alternatives = []
    for variant in sorted(variants, key=len, reverse=True):
        alternatives.append(rf"(?<![A-Za-z0-9_]){re.escape(variant)}")
        if variant.startswith("/") and len(variant.strip("/").split("/")) >= 2:
            alternatives.append(rf"(?<![A-Za-z0-9_/.]){re.escape(variant[1:])}")
    return alternatives


def _is_home_or_ancestor(root: str, home: Path) -> bool:
    with contextlib.suppress(ValueError):
        return home.is_relative_to(Path(root))
    return False


def _keep_closing_brackets(match: re.Match[str]) -> str:
    matched = match.group(0)
    return REDACTED_PLACEHOLDER + matched[len(matched.rstrip("]}")) :]


class LocalPathDetector:
    def __init__(self, roots: Iterable[str | Path] = (), *, redact_home: bool = True):
        home = Path.home() if redact_home else None
        data_variants: set[str] = set()
        for root in roots:
            data_variants |= {v for v in _root_variants(root) if home is None or not _is_home_or_ancestor(v, home)}
        self._data_re = (
            re.compile("(?:" + "|".join(_root_alternatives(data_variants)) + ")" + _PATH_BOUNDARY)
            if data_variants
            else None
        )
        home_variants = _root_variants(home) if home is not None else set()
        self._home_re = (
            re.compile(
                "(?:" + "|".join(_root_alternatives(home_variants)) + ")" + _PATH_BOUNDARY + r"(?:[/\\][^\s'\",;)]*)?"
            )
            if home_variants
            else None
        )

    def contains(self, text: str) -> bool:
        if self._data_re is not None and self._data_re.search(text):
            return True
        if self._home_re is not None and self._home_re.search(text):
            return True
        return any(not is_neutral_placeholder(match.group(0)) for match in PRIVATE_LOCAL_PATH_RE.finditer(text))

    def redact_text(self, text: str) -> str:
        if self._data_re is not None:
            text = self._data_re.sub(REDACTED_PLACEHOLDER, text)
        if self._home_re is not None:
            text = self._home_re.sub(_keep_closing_brackets, text)
        return PRIVATE_LOCAL_PATH_RE.sub(
            lambda match: match.group(0) if is_neutral_placeholder(match.group(0)) else _keep_closing_brackets(match),
            text,
        )

    def redact_value(self, value: Any) -> Any:
        return redact_structure(value, self.redact_text)

    def find_leaks(self, value: Any, key_path: tuple[str, ...] = ()) -> list[str]:
        leaks: list[str] = []
        stack: list[tuple[Any, tuple[str, ...]]] = [(value, key_path)]
        while stack:
            node, path = stack.pop()
            if isinstance(node, dict):
                children = []
                for key, child in node.items():
                    label = str(key)
                    if isinstance(key, str) and self.contains(key):
                        label = "<key>"
                        leaks.append(".".join((*path, label)))
                    children.append((child, (*path, label)))
                stack.extend(reversed(children))
            elif isinstance(node, (list, tuple, set, frozenset)):
                stack.extend(reversed([(child, (*path, str(index))) for index, child in enumerate(node)]))
            elif isinstance(node, str) and self.contains(node):
                leaks.append(".".join(path) or "<root>")
        return leaks


def redact_structure(value: Any, redact_text: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_structure(child, redact_text) for key, child in value.items()}
    if isinstance(value, list):
        return [redact_structure(child, redact_text) for child in value]
    if isinstance(value, tuple):
        return tuple(redact_structure(child, redact_text) for child in value)
    return value


@functools.lru_cache(maxsize=32)
def _cached_detector(roots: tuple[str, ...], redact_home: bool, home: str) -> LocalPathDetector:
    return LocalPathDetector(roots, redact_home=redact_home)


def local_path_detector(roots: Iterable[str | Path] = (), *, redact_home: bool = True) -> LocalPathDetector:
    return _cached_detector(
        tuple(sorted({str(root) for root in roots if root})), redact_home, str(Path.home()) if redact_home else ""
    )


def find_public_path_leaks(value: Any, key_path: tuple[str, ...] = (), roots: Iterable[str | Path] = ()) -> list[str]:
    return local_path_detector(roots, redact_home=False).find_leaks(value, key_path)
