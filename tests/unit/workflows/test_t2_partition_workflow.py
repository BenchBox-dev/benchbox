from __future__ import annotations

from collections import Counter
from math import prod
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]
ROOT = Path(__file__).resolve().parents[3]


def _jobs(filename: str) -> dict:
    return yaml.safe_load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))["jobs"]


def test_correctness_partitions_preserve_each_gate_once() -> None:
    from benchbox.core.equivalence.cross_surface import GATES

    job = _jobs("ci.yml")["correctness-gate"]
    assert job["strategy"] == {"fail-fast": False, "matrix": {"partition": ["a", "b"]}}
    expected = {
        "make test-correctness-gate",
        "make tpchavoc-equivalence-report",
        "make tpchavoc-dataframe-equivalence-report",
    }
    for gate in GATES:
        target = gate.replace("_", "-")
        if gate == "joinorder":
            target = "joinorder-synthetic"
        expected.add(f"make {target}-cross-surface-equivalence-report")
    expected.add("make tpcds-pandas-cross-surface-equivalence-report")
    steps = [step for step in job["steps"] if str(step.get("run", "")).startswith("make ")]
    assert Counter(step["run"] for step in steps) == Counter(dict.fromkeys(expected, 1))
    groups = {
        name: {step["run"] for step in steps if step["if"] == f"matrix.partition == '{name}'"} for name in ("a", "b")
    }
    assert groups["a"] and groups["b"]
    assert groups["a"].isdisjoint(groups["b"])
    assert groups["a"] | groups["b"] == expected
    assert not any(step.get("continue-on-error") for step in steps)


def test_required_local_cases_run_before_merge_and_gate_core() -> None:
    jobs = _jobs("ci.yml")
    job = jobs["required-local-cases"]
    assert job["needs"] == "ci-paths"
    assert job["if"] == jobs["correctness-gate"]["if"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 20
    assert "strategy" not in job
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert runs.count("make test-required-local-cases") == 1
    assert not job.get("continue-on-error")
    assert not any(step.get("continue-on-error") for step in job["steps"])
    assert "required-local-cases" in jobs["core"]["needs"]
    core_text = "\n".join(step.get("run", "") for step in jobs["core"]["steps"])
    condition = "${{ needs.ci-paths.outputs.heavy-needed == 'true' && needs.ci-paths.outputs.unit-core == 'true' }}"
    assert f"--expect required-local-cases={condition}" in core_text


def test_medium_selection_and_receipts_gate_core() -> None:
    jobs = _jobs("ci.yml")
    medium = jobs["medium-test"]
    assert medium["strategy"] == {"fail-fast": False, "matrix": {"shard_index": [0, 1, 2, 3]}}
    assert medium["needs"] == ["ci-paths", "medium-collect"]
    selector = "medium and not (slow or stress or resource_heavy or live_integration)"
    collect_text = "\n".join(step.get("run", "") for step in jobs["medium-collect"]["steps"])
    medium_text = "\n".join(step.get("run", "") for step in medium["steps"])
    assert selector in collect_text and selector in medium_text
    assert "--collect-only -q -n 0" in collect_text
    assert "--tb=short --timeout=60 -n 5" in medium_text
    assert "-p scripts.pytest_shard_evidence" in medium_text
    assert "--collection-summary" in medium_text and "--checked-sha" in medium_text
    assert "test -s" in medium_text
    assert not medium.get("continue-on-error")
    core_text = "\n".join(step.get("run", "") for step in jobs["core"]["steps"])
    for job in ("medium-collect", "medium-test", "correctness-gate"):
        assert job in jobs["core"]["needs"]
        assert f"--expect {job}=" in core_text
    assert "verify-medium" in core_text


def test_the_medium_shard_records_memory_and_stalled_stacks() -> None:
    medium = _jobs("ci.yml")["medium-test"]
    run_step = next(step for step in medium["steps"] if step["name"] == "Run medium speed tier")
    assert "sample_resources &" in run_step["run"]
    assert 'pkill -P "${sampler}"' in run_step["run"]
    assert 'kill "${sampler}"' in run_step["run"]
    sampler_body = run_step["run"].split("sample_resources()")[1].split("sample_resources &")[0]
    assert "comm" in sampler_body and "args" not in sampler_body
    names = [step["name"] for step in medium["steps"]]
    memory_step = next(
        step for step in medium["steps"] if step["name"] == "Record kernel memory events for the medium shard"
    )
    assert memory_step["if"] == "always()"
    assert names.index(memory_step["name"]) == names.index(run_step["name"]) + 1


def test_heavy_payload_uses_at_most_twelve_standard_linux_runners() -> None:
    jobs = _jobs("ci.yml")
    payload = (
        "medium-collect",
        "medium-test",
        "correctness-gate",
        "required-local-cases",
        "plan-capture-gate",
        "datafusion-integration",
        "package-smoke",
        "dependency-audit",
    )
    count = 0
    for name in payload:
        assert jobs[name]["runs-on"] == "ubuntu-latest"
        matrix = jobs[name].get("strategy", {}).get("matrix", {})
        count += prod(len(values) for values in matrix.values())
    assert count == 12


def test_native_binary_framing_remains_required_before_merge() -> None:
    jobs = _jobs("ci.yml")
    native = jobs["tpch-binary-framing"]
    assert native["needs"] == "ci-paths"
    framing_condition = (
        "${{ needs.ci-paths.outputs.heavy-needed == 'true' || needs.ci-paths.outputs.framing-needed == 'true' }}"
    )
    assert native["if"] == framing_condition
    assert native["runs-on"] == "${{ matrix.os }}"
    assert native["timeout-minutes"] == 15
    assert native["strategy"] == {
        "fail-fast": False,
        "matrix": {"os": ["macos-latest", "windows-latest"]},
    }
    framing = next(
        step for step in native["steps"] if step["name"] == "Verify bundled dbgen binaries emit clean framing"
    )
    assert framing["run"] == (
        "uv run -- python -m pytest tests/unit/core/tpch/test_tpch_dbgen_framing_binaries.py -m 'unit or slow' -v"
    )
    assert not native.get("continue-on-error")
    assert not framing.get("continue-on-error")
    assert "tpch-binary-framing" in jobs["core"]["needs"]
    core_text = "\n".join(step.get("run", "") for step in jobs["core"]["steps"])
    expectation = framing_condition.removeprefix("${{ ").removesuffix(" }}")
    assert f"--expect tpch-binary-framing=${{{{ {expectation} }}}}" in core_text
    assert "framing-needed" in _jobs("ci.yml")["ci-paths"]["outputs"]


def test_three_os_nightly_cells_retain_raw_framing_guard() -> None:
    matrix = _jobs("nightly-v2.yml")["matrix"]
    assert set(matrix["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "macos-latest", "windows-latest"}
    framing = next(
        step for step in matrix["steps"] if step["name"] == "Verify bundled dbgen binaries emit clean framing"
    )
    assert framing["if"] == "matrix.python-version == '3.12'"
    assert (
        framing["run"]
        == "uv run -- python -m pytest tests/unit/core/tpch/test_tpch_dbgen_framing_binaries.py -m slow -v"
    )
    assert not framing.get("continue-on-error")
    from tests.unit.core.tpch.test_tpch_dbgen_framing_binaries import _assert_no_trailing_delimiter

    with pytest.raises(AssertionError, match="trailing"):
        _assert_no_trailing_delimiter(["1|ARGENTINA|1|comment|"], "raw fixture")
