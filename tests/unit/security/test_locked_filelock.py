from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXED_FILELOCK = (4, 2, 1)


def _load(name: str) -> dict:
    return tomllib.loads((REPO_ROOT / name).read_text(encoding="utf-8"))


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def test_lock_pins_a_filelock_fixed_for_the_advisories() -> None:
    entries = [package for package in _load("uv.lock")["package"] if package["name"] == "filelock"]
    assert len(entries) == 1
    assert _version_tuple(entries[0]["version"]) >= FIXED_FILELOCK


def test_lock_artifacts_are_hash_pinned() -> None:
    (entry,) = [package for package in _load("uv.lock")["package"] if package["name"] == "filelock"]
    assert entry["sdist"]["hash"].startswith("sha256:")
    assert entry["wheels"]
    assert all(wheel["hash"].startswith("sha256:") for wheel in entry["wheels"])


def test_override_keeps_filelock_inside_the_verified_major() -> None:
    overrides = _load("pyproject.toml")["tool"]["uv"]["override-dependencies"]
    assert "filelock>=4.2.1,<5" in overrides
    assert {"name": "filelock", "specifier": ">=4.2.1,<5"} in _load("uv.lock")["manifest"]["overrides"]
