from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.canary_impact import (
    MEDIUM_WHOLE_SUITE_PATHS,
    FileDeps,
    _change_covers,
    build_dependency_map,
    collect_canary_node_ids,
    compute_selection,
    files_from_node_ids,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.timeout(180)]

REPO_ROOT = Path(__file__).resolve().parents[2]
MEDIUM_MARKER = "medium and not (slow or stress or resource_heavy or live_integration)"
MONITORING_TEST_FILE = "tests/unit/monitoring/test_resource_monitoring.py"
KNOWN_REGRESSIONS = [
    ("68fb5a071", "tests/integration/test_tpcdi_full_benchmark.py"),
    ("5e9284a2c", "tests/unit/core/flightdata/test_flightdata_benchmark_resource_heavy.py"),
    ("ed3e39a9c", "tests/unit/scripts/test_validate_submission_resource_heavy.py"),
    ("2384c3ee7", "tests/unit/scripts/test_validate_submission_resource_heavy.py"),
]
REGRESSION_TARGETS = sorted({test_file for _, test_file in KNOWN_REGRESSIONS})


FROZEN_CHANGED_PATHS: dict[str, list[str]] = {
    "68fb5a071": [
        "benchbox/core/expected_results/tpcds_results.py",
        "benchbox/core/platform_manifest.py",
        "benchbox/core/results/loader.py",
        "benchbox/core/results/models.py",
        "benchbox/core/results/schema.py",
        "benchbox/core/tpcdi/benchmark.py",
        "benchbox/core/tpcdi/generator/data.py",
        "benchbox/core/tpcds/official_benchmark.py",
        "benchbox/core/validation/query_validation.py",
        "benchbox/mcp/jobs.py",
        "benchbox/mcp/security.py",
        "benchbox/platforms/base/connection_wrappers.py",
        "benchbox/platforms/base/execution.py",
        "benchbox/platforms/base/result_capture.py",
        "benchbox/platforms/cloud_shared.py",
        "tests/integration/mcp/_security.py",
        "tests/integration/mcp/test_durable_jobs.py",
        "tests/integration/test_tpcdi_phase3_benchmark.py",
        "tests/unit/core/expected_results/test_singleflight_run_policy.py",
        "tests/unit/core/results/test_schema_timing_contract.py",
        "tests/unit/core/test_tpc_reporting_execution.py",
        "tests/unit/mcp/test_durable_jobs.py",
        "tests/unit/mcp/test_run_surface_contract.py",
        "tests/unit/platforms/test_base_adapter.py",
        "tests/unit/platforms/test_bigquery_adapter.py",
        "tests/unit/platforms/test_throughput_session_capability_sweep.py",
        "tests/unit/platforms/throughput_session_capability_snapshot.json",
        "tests/unit/test_tpcdi_phase1.py",
    ],
    "5e9284a2c": [
        ".github/workflows/pr.yml",
        "benchbox/cli/commands/run.py",
        "benchbox/core/equivalence/cross_surface.py",
        "benchbox/core/flightdata/downloader.py",
        "benchbox/core/flightdata/downloader_specs.yaml",
        "benchbox/core/nyctaxi/benchmark.py",
        "benchbox/core/nyctaxi/downloader.py",
        "benchbox/core/nyctaxi/downloader_specs.yaml",
        "benchbox/core/tpchavoc/equivalence.py",
        "tests/integration/test_datavault_cross_surface_staged.py",
        "tests/integration/test_ducklake_integration.py",
        "tests/integration/test_read_primitives_cross_surface_equivalence.py",
        "tests/integration/test_tpchavoc_equivalence_engine_agnostic.py",
        "tests/unit/cli/test_run_command_interactive_paths.py",
        "tests/unit/core/equivalence/test_cross_surface.py",
        "tests/unit/core/flightdata/test_source_contract.py",
        "tests/unit/core/nyctaxi/test_multi_type.py",
        "tests/unit/core/nyctaxi/test_source_contract.py",
        "tests/unit/core/test_benchmark_api_contract.py",
        "tests/unit/core/tpchavoc/test_equivalence_execute_transform.py",
        "tests/unit/test_cross_surface_dtype_cell.py",
    ],
    "ed3e39a9c": [
        "_project/scripts/explorer_pipeline/transformer.py",
        "benchbox/cli/commands/compare.py",
        "benchbox/core/analysis/comparison.py",
        "benchbox/core/results/exporter.py",
        "benchbox/core/results/loader.py",
        "benchbox/core/results/schema.py",
        "benchbox/core/results/schema_policy.py",
        "benchbox/core/results/schema_specs.yaml",
        "benchbox/validation/bundle.py",
        "results-explorer/src/lib/localResult.ts",
        "tests/unit/cli/test_cli_output.py",
        "tests/unit/core/results/test_loader.py",
        "tests/unit/core/results/test_query_plan_serialization.py",
        "tests/unit/core/results/test_result_extension_contract.py",
        "tests/unit/core/results/test_result_parity.py",
        "tests/unit/scripts/test_validate_submission.py",
        "tests/unit/test_results_exporter.py",
    ],
    "2384c3ee7": [
        "CHANGELOG.md",
        "_project/audits/result-bundle-downstream-compat-2026-09-18.md",
        "benchbox/validation/bundle.py",
        "scripts/publication/verify_lane_isolation.py",
        "scripts/validate_corpus.py",
        "tests/unit/scripts/test_validate_submission.py",
    ],
}


def _changed_paths(commit: str) -> list[str]:
    try:
        changed = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "diff", "--name-only", f"{commit}^", commit],
            text=True,
            stderr=subprocess.DEVNULL,
        ).split()
        if changed:
            return changed
    except subprocess.CalledProcessError:
        pass
    if commit in FROZEN_CHANGED_PATHS:
        return list(FROZEN_CHANGED_PATHS[commit])
    raise AssertionError(f"expected changed paths for {commit}")


@pytest.fixture(scope="module")
def regression_targets() -> tuple[list[str], dict[str, FileDeps]]:
    node_ids = collect_canary_node_ids(REPO_ROOT, paths=REGRESSION_TARGETS)
    assert set(files_from_node_ids(node_ids)) == set(REGRESSION_TARGETS)
    dep_map, fallback, _sites = build_dependency_map(REPO_ROOT, files_from_node_ids(node_ids))
    assert fallback is None, fallback
    return node_ids, dep_map


@pytest.fixture(scope="module")
def monitoring_target() -> tuple[list[str], dict[str, FileDeps]]:
    node_ids = collect_canary_node_ids(REPO_ROOT, marker_expression=MEDIUM_MARKER, paths=[MONITORING_TEST_FILE])
    assert node_ids
    dep_map, fallback, _sites = build_dependency_map(REPO_ROOT, files_from_node_ids(node_ids))
    assert fallback is None, fallback
    return node_ids, dep_map


@pytest.mark.parametrize("commit,test_file", KNOWN_REGRESSIONS)
def test_known_regressions_are_selected_through_dependency_edges(
    regression_targets: tuple[list[str], dict[str, FileDeps]], commit: str, test_file: str
) -> None:
    node_ids, dep_map = regression_targets
    mapped = [
        path for path in _changed_paths(commit) if any(_change_covers(path, dep) for dep in dep_map[test_file].deps)
    ]
    assert mapped, f"{commit} changed no path with a dependency edge to {test_file}"
    selection = compute_selection(mapped, dep_map, node_ids)
    assert selection["whole_suite"] is False, selection["whole_suite_reason"]
    expected = {node_id for node_id in node_ids if node_id.split("::", 1)[0] == test_file}
    assert expected, f"expected {test_file} in the targeted collection"
    selected = {entry["node_id"] for entry in selection["selected"]}
    assert expected <= selected, f"{commit} missed {sorted(expected - selected)}"
    for entry in selection["selected"]:
        if entry["file"] == test_file:
            assert any(reason["edge"] == "dependency" for reason in entry["reasons"]), entry["reasons"]


@pytest.mark.parametrize("commit,test_file", KNOWN_REGRESSIONS)
def test_known_regressions_full_diff_still_selects_target(
    regression_targets: tuple[list[str], dict[str, FileDeps]], commit: str, test_file: str
) -> None:
    node_ids, dep_map = regression_targets
    selection = compute_selection(_changed_paths(commit), dep_map, node_ids)
    expected = {node_id for node_id in node_ids if node_id.split("::", 1)[0] == test_file}
    assert expected
    selected = {entry["node_id"] for entry in selection["selected"]}
    assert expected <= selected, f"{commit} missed {sorted(expected - selected)}"


def test_monitoring_lazy_export_selects_resource_monitoring(
    monitoring_target: tuple[list[str], dict[str, FileDeps]],
) -> None:
    node_ids, dep_map = monitoring_target
    selection = compute_selection(
        ["benchbox/monitoring/performance.py"],
        dep_map,
        node_ids,
        marker_expression=MEDIUM_MARKER,
        cant_affect=frozenset(),
        whole_suite_paths=MEDIUM_WHOLE_SUITE_PATHS,
    )
    assert selection["whole_suite"] is False, selection["whole_suite_reason"]
    expected = {node for node in node_ids if node.startswith(f"{MONITORING_TEST_FILE}::")}
    assert expected
    selected = {entry["node_id"]: entry["reasons"] for entry in selection["selected"]}
    assert expected <= set(selected)
    for node_id in expected:
        assert any(
            reason["edge"] == "dependency" and reason["changed_path"] == "benchbox/monitoring/performance.py"
            for reason in selected[node_id]
        ), selected[node_id]


