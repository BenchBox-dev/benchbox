from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]
ROOT = Path(__file__).resolve().parents[3]


def _steps() -> list[dict]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]["medium-collect"]["steps"]


def _index(steps: list[dict], name: str) -> int:
    return next(i for i, step in enumerate(steps) if step.get("name") == name)


def test_bytecode_cache_is_restored_before_collection() -> None:
    steps = _steps()
    cache = steps[_index(steps, "Restore the collection bytecode cache")]
    assert cache["uses"].startswith("actions/cache@")
    assert len(cache["uses"].split("@", 1)[1]) == 40
    paths = cache["with"]["path"].split()
    assert paths == ["tests/**/__pycache__", "benchbox/**/__pycache__"]
    key = cache["with"]["key"]
    assert "hashFiles('uv.lock')" in key
    assert "hashFiles('tests/**/*.py', 'benchbox/**/*.py')" in key
    restore = cache["with"]["restore-keys"].splitlines()
    assert restore[0] == key.rsplit("-${{ hashFiles('tests", 1)[0] + "-"
    assert _index(steps, "Restore the collection bytecode cache") < _index(steps, "Collect the complete medium tier")


def test_bytecode_uses_source_hash_invalidation_so_restored_files_survive_checkout_mtimes() -> None:
    steps = _steps()
    compile_step = steps[_index(steps, "Compile sources with source-hash bytecode")]
    assert "--invalidation-mode checked-hash" in compile_step["run"]
    assert (
        _index(steps, "Restore the collection bytecode cache")
        < _index(steps, "Compile sources with source-hash bytecode")
        < _index(steps, "Collect the complete medium tier")
    )


def test_collection_command_and_evidence_are_unchanged() -> None:
    steps = _steps()
    run = steps[_index(steps, "Collect the complete medium tier")]["run"]
    assert "--collect-only -q -n 0 --assert=plain" in run
    assert "medium and not (slow or stress or resource_heavy or live_integration)" in run
    assert "--expected-count" in run and "--shard-count 4" in run
