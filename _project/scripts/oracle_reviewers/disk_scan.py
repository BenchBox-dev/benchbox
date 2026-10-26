from __future__ import annotations

import base64
import os
import stat
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

CHUNK_BYTES = 8 * 1024 * 1024


def secret_values(names: Iterable[str], environ: Mapping[str, str]) -> dict[str, bytes]:
    needles: dict[str, bytes] = {}
    for name in names:
        if value := environ.get(name, ""):
            needles[name] = value.encode("utf-8")
            needles[f"{name} (as a basic-auth header)"] = base64.b64encode(f"x-access-token:{value}".encode())
    return needles


def _files(root: Path) -> Iterator[Path]:
    if root.is_symlink() or not root.exists():
        return
    if root.is_file():
        yield root
        return
    for directory, directories, names in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if not (Path(directory) / name).is_symlink()]
        for name in names:
            yield Path(directory) / name


def _contains(path: Path, needles: Mapping[str, bytes]) -> set[str]:
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return set()
        found: set[str] = set()
        overlap = max(len(needle) for needle in needles.values()) - 1
        carry = b""
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK_BYTES):
                window = carry + chunk
                found.update(name for name, needle in needles.items() if needle in window)
                carry = window[-overlap:] if overlap else b""
        return found
    except OSError:
        return set()


def _outermost(roots: Iterable[Path]) -> list[Path]:
    resolved = sorted({root.resolve() for root in roots if root.exists()}, key=lambda path: len(path.parts))
    kept: list[Path] = []
    for root in resolved:
        if not any(root.is_relative_to(parent) for parent in kept):
            kept.append(root)
    return kept


def scan(roots: Iterable[Path], needles: Mapping[str, bytes]) -> list[tuple[str, Path]]:
    if not needles:
        return []
    hits: list[tuple[str, Path]] = []
    for root in _outermost(roots):
        for path in _files(root):
            hits.extend((name, path) for name in sorted(_contains(path, needles)))
    return hits
