from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from . import credentials, protocol


class Unredactable(Exception):
    def __init__(self, path: Path) -> None:
        super().__init__(f"a secret value remains in {path}")
        self.path = path


def _keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _text(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("text"), str):
        return item["text"]
    return None


def _joined(items: list[Any]) -> str:
    return "".join(text for item in items if (text := _text(item)) is not None)


def _rejoined(items: list[Any], extra: tuple[str, ...]) -> list[Any]:
    joined = _joined(items)
    cleaned = credentials.redact(joined, extra)
    if cleaned == joined:
        return items
    first = True
    result: list[Any] = []
    for item in items:
        if _text(item) is None:
            result.append(item)
            continue
        text = cleaned if first else ""
        first = False
        result.append(text if isinstance(item, str) else {**item, "text": text})
    return result


def redact(value: Any, extra: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return credentials.redact(value, extra)
    if isinstance(value, list):
        return [redact(item, extra) for item in _rejoined(value, extra)]
    if isinstance(value, dict):
        return {key: redact(item, extra) for key, item in value.items()}
    return value


def _flat(payload: Any) -> Iterator[str]:
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                yield str(key)
                pending.append(item)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
        payload = protocol.marker_payload(value)
        if payload is not None:
            yield from _flat(payload)
    elif isinstance(value, list):
        yield _joined(value)
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def json_files(paths: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(item for item in path.rglob("*.json") if item.is_file()))
        elif path.is_file():
            found.append(path)
    return found


def sanitize(path: Path, extra: tuple[str, ...]) -> bool:
    original = path.read_text(encoding="utf-8")
    try:
        data = json.loads(original)
    except ValueError as exc:
        raise Unredactable(path) from exc
    cleaned = redact(data, extra)
    text = json.dumps(cleaned, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    remaining = (*_keys(cleaned), *_strings(cleaned), text)
    if any(credentials.contains(item, extra) for item in remaining):
        raise Unredactable(path)
    changed = cleaned != data
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed
