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


def test_sources_are_compiled_in_parallel_before_collection() -> None:
    steps = _steps()
    compile_step = steps[_index(steps, "Compile sources in parallel before collection")]
    assert "compileall" in compile_step["run"]
    assert "-j 0" in compile_step["run"]
    assert "--invalidation-mode" not in compile_step["run"]
    assert not any(step.get("uses", "").startswith("actions/cache@") for step in steps)
    assert _index(steps, "Compile sources in parallel before collection") < _index(
        steps, "Collect the complete medium tier"
    )


def test_collection_command_and_evidence_are_unchanged() -> None:
    steps = _steps()
    run = steps[_index(steps, "Collect the complete medium tier")]["run"]
    assert "--collect-only -q -n 0 --assert=plain" in run
    assert "medium and not (slow or stress or resource_heavy or live_integration)" in run
    assert "--expected-count" in run and "--shard-count 4" in run
