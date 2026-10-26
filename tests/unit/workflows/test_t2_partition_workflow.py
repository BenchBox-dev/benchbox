from __future__ import annotations

from collections import Counter
from math import prod
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]
ROOT = Path(__file__).resolve().parents[3]


MOVED_STEPS = {
    "a": (
        "Run the required local-engine cases",
        "Run TPC-Havoc DataFusion variant-equivalence sample and .tbl load regression",
    ),
    "b": ("Run in-process plan-capture gate", "Run the polars-df smoke and cross-surface gates on Polars 2.0.0"),
}
HEAVY_CONDITION = "${{ needs.ci-paths.outputs.heavy-needed == 'true' }}"


def _jobs(filename: str) -> dict:
    return yaml.safe_load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))["jobs"]


def _moved(job: dict) -> list[dict]:
    names = {name for steps in MOVED_STEPS.values() for name in steps}
    return [step for step in job["steps"] if step.get("name") in names]


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
    moved = _moved(job)
    steps = [step for step in job["steps"] if str(step.get("run", "")).startswith("make ") and step not in moved]
    assert Counter(step["run"] for step in steps) == Counter(dict.fromkeys(expected, 1))
    groups = {
        name: {step["run"] for step in steps if step["if"] == f"matrix.partition == '{name}'"} for name in ("a", "b")
    }
    assert groups["a"] and groups["b"]
    assert groups["a"].isdisjoint(groups["b"])
    assert groups["a"] | groups["b"] == expected
    assert not any(step.get("continue-on-error") for step in steps)


def test_heavy_checks_run_last_in_their_partition_even_after_a_failure() -> None:
    jobs = _jobs("ci.yml")
    job = jobs["correctness-gate"]
    assert job["if"] == HEAVY_CONDITION
    assert job["timeout-minutes"] == 65
    names = [step.get("name") for step in job["steps"]]
    for partition, moved in MOVED_STEPS.items():
        partition_steps = [step for step in job["steps"] if f"matrix.partition == '{partition}'" in str(step.get("if"))]
        assert [step["name"] for step in partition_steps[-len(moved) :]] == list(moved)
        for name in moved:
            step = job["steps"][names.index(name)]
            assert step["if"] == f"${{{{ !cancelled() && matrix.partition == '{partition}' }}}}"
            assert step["timeout-minutes"] > 0
    assert [step["name"] for step in _moved(job) if step.get("continue-on-error")] == [MOVED_STEPS["a"][1]]
    assert names[-1] == MOVED_STEPS["b"][1]
    polars = job["steps"][-1]["run"]
    assert polars.index('uv pip install "polars==2.0.0"') < polars.index("make polars-df-compat-check")


def test_required_local_cases_run_before_merge_and_gate_core() -> None:
    jobs = _jobs("ci.yml")
    job = jobs["correctness-gate"]
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert runs.count("make test-required-local-cases") == 1
    step = next(step for step in job["steps"] if step.get("run") == "make test-required-local-cases")
    assert step["timeout-minutes"] == 20
    assert not step.get("continue-on-error")
    assert not job.get("continue-on-error")
    for retired in ("required-local-cases", "plan-capture-gate", "datafusion-integration", "polars-2-compat"):
        assert retired not in jobs
        assert retired not in jobs["core"]["needs"]
    assert "correctness-gate" in jobs["core"]["needs"]
    core_text = "\n".join(step.get("run", "") for step in jobs["core"]["steps"])
    assert f"--expect correctness-gate={HEAVY_CONDITION}" in core_text


def test_medium_selection_and_receipts_gate_core() -> None:
    jobs = _jobs("ci.yml")
    medium = jobs["medium-test"]
    assert medium["strategy"] == {
        "fail-fast": False,
        "matrix": {"shard_index": "${{ fromJSON(needs.ci-paths.outputs.medium-shards) }}"},
    }
    assert medium["needs"] == ["ci-paths", "medium-collect"]
    for name in ("medium-collect", "correctness-gate"):
        assert jobs[name]["if"].endswith("needs.ci-paths.outputs.heavy-needed == 'true' }}")
        assert "unit-core" not in jobs[name]["if"]
    assert "needs.ci-paths.outputs.medium-shards != '[]'" in medium["if"]
    assert "needs.ci-paths.result == 'success'" in medium["if"]
    assert "(needs.ci-paths.outputs.heavy-needed != 'true' || needs.medium-collect.result == 'success')" in medium["if"]
    assert "unit-core" not in medium["if"]
    selector = "medium and not (slow or stress or resource_heavy or live_integration)"
    collect_text = "\n".join(step.get("run", "") for step in jobs["medium-collect"]["steps"])
    medium_text = "\n".join(step.get("run", "") for step in medium["steps"])
    assert selector in collect_text and selector in medium_text
    assert "--collect-only -q -n 0" in collect_text
    assert "--tb=short --timeout=60 -n 5 --dist loadscope" in medium_text
    assert "-p scripts.pytest_shard_evidence" in medium_text
    assert "--collection-summary" in medium_text and "--checked-sha" in medium_text
    assert "test -s" in medium_text
    assert not medium.get("continue-on-error")
    core_text = "\n".join(step.get("run", "") for step in jobs["core"]["steps"])
    for job in ("medium-collect", "correctness-gate"):
        assert job in jobs["core"]["needs"]
        assert f"--expect {job}={HEAVY_CONDITION}" in core_text
    assert "medium-test" in jobs["core"]["needs"]
    assert "--expect medium-test=${{ needs.ci-paths.outputs.medium-shards != '[]' }}" in core_text
    assert "verify-medium" in core_text


def _steps_by_name(job: dict) -> dict[str, dict]:
    return {step["name"]: step for step in job["steps"] if "name" in step}


def test_ci_paths_decides_the_medium_shards() -> None:
    ci_paths = _jobs("ci.yml")["ci-paths"]
    assert ci_paths["outputs"]["medium-shards"] == "${{ steps.medium-shards.outputs.medium-shards }}"
    step = _steps_by_name(ci_paths)["Decide medium shards"]
    names = [candidate.get("name") for candidate in ci_paths["steps"]]
    assert names.index("Decide medium shards") > names.index("Decide heavy-tier need")
    assert step["id"] == "medium-shards"
    run = step["run"]
    assert "grep -c . coverage-nodeids.txt" in run
    assert "steps.heavy.outputs.heavy-needed" in run
    assert "steps.units.outputs.code-tests-needed" in run
    assert run.count("shards='[0,1,2,3]'") == 2
    assert "shards='[0]'" in run and "shards='[]'" in run


def test_code_test_no_longer_runs_the_medium_work() -> None:
    steps = _steps_by_name(_jobs("ci.yml")["code-test"])
    for name in (
        "Run changed tests on the curated release tree",
        "Download coverage selection",
        "Run medium tests for changed paths",
    ):
        assert name not in steps
    assert "Pre-build the assertion-rewrite cache" in steps
    text = "\n".join(step.get("run", "") for step in steps.values())
    assert "release_curation_dry_run" not in text
    assert "coverage-selection" not in text


def test_release_tree_check_runs_once_in_the_first_shard() -> None:
    medium = _jobs("ci.yml")["medium-test"]
    steps = _steps_by_name(medium)
    step = steps["Run changed tests on the curated release tree"]
    assert step["if"] == (
        "${{ !cancelled() && matrix.shard_index == 0 && needs.ci-paths.outputs.code-tests-needed == 'true' }}"
    )
    assert step["env"] == {"BASE_SHA": "${{ github.event.pull_request.base.sha }}", "UV_PYTHON": "3.14"}
    assert step["run"] == 'uv run -- python scripts/release_curation_dry_run.py --changed-since "$BASE_SHA"'
    setup = next(s for s in medium["steps"] if s.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["python-version"] == "${{ needs.ci-paths.outputs.heavy-needed == 'true' && '3.12' || '3.14' }}"
    checkout = next(s for s in medium["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"fetch-depth": 0}


def test_non_heavy_shards_run_their_share_of_the_coverage_selection() -> None:
    medium = _jobs("ci.yml")["medium-test"]
    steps = _steps_by_name(medium)
    names = list(steps)
    non_heavy = "needs.ci-paths.outputs.heavy-needed != 'true'"
    heavy = "needs.ci-paths.outputs.heavy-needed == 'true'"
    download = steps["Download coverage selection"]
    assert download["if"] == non_heavy
    assert download["with"]["name"] == (
        "coverage-selection-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}"
    )
    selected = steps["Run medium tests for changed paths"]
    assert selected["if"] == non_heavy
    run = selected["run"]
    assert "scripts/coverage_test_selection.py prune" in run
    assert "release_canary_sharding.py shard" in run
    assert "--durations tests/fixtures/medium_shard_durations.json" in run
    assert '--shard-index "$SHARD_INDEX" --shard-count 4' in run
    assert '-m "medium and not (slow or stress or resource_heavy or live_integration)"' in run
    assert "--tb=short --timeout=60 -q" in run
    assert '[ "$status" -ne 0 ] && [ "$status" -ne 5 ]' in run
    assert names.index("Download coverage selection") < names.index("Run medium tests for changed paths")
    for name in (
        "Download medium collection evidence",
        "Partition the complete medium tier",
        "Run medium speed tier",
    ):
        assert steps[name]["if"] == heavy
    assert steps["Upload medium shard evidence"]["if"] == f"${{{{ always() && {heavy} }}}}"


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


def test_heavy_payload_uses_at_most_nine_standard_linux_runners() -> None:
    jobs = _jobs("ci.yml")
    payload = (
        "medium-collect",
        "medium-test",
        "correctness-gate",
        "package-smoke",
        "dependency-audit",
    )
    count = 0
    for name in payload:
        assert jobs[name]["runs-on"] == "ubuntu-latest"
        matrix = jobs[name].get("strategy", {}).get("matrix", {})
        count += prod(len(values) if isinstance(values, list) else 4 for values in matrix.values())
    assert count == 9


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


@pytest.mark.parametrize("filename", ["ci.yml", "trunk.yml", "trunk-coverage.yml", "test.yml"])
def test_every_medium_sharding_command_binds_the_duration_fixture(filename: str) -> None:
    runs = [
        str(step.get("run", ""))
        for job in _jobs(filename).values()
        for step in job.get("steps", [])
        if "release_canary_sharding.py" in str(step.get("run", ""))
    ]
    commands = [
        " ".join(chunk.split())
        for run in runs
        for chunk in run.split("release_canary_sharding.py")[1:]
        if chunk.lstrip().split()[0] in {"collect", "shard", "verify-medium"}
    ]

    assert len(commands) >= 2
    assert all("--durations tests/fixtures/medium_shard_durations.json" in command for command in commands)
