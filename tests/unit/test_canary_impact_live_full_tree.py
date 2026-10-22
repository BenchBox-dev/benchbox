from __future__ import annotations

from pathlib import Path

import pytest

from scripts.canary_impact import (
    MEDIUM_WHOLE_SUITE_PATHS,
    FileDeps,
    _build_shared_deps,
    _conftest_chain,
    _parent_inits,
    _rel,
    analyze_python_file,
    build_dependency_map,
    collect_canary_node_ids,
    compute_selection,
    files_from_node_ids,
)

pytestmark = [pytest.mark.unit, pytest.mark.slow, pytest.mark.timeout(900)]

REPO_ROOT = Path(__file__).resolve().parents[2]
MEDIUM_MARKER = "medium and not (slow or stress or resource_heavy or live_integration)"
EXACTNESS_TARGETS = [
    "tests/integration/test_tpcdi_full_benchmark.py",
    "tests/unit/core/flightdata/test_flightdata_benchmark_resource_heavy.py",
    "tests/unit/scripts/test_validate_submission_resource_heavy.py",
]


@pytest.fixture(scope="module")
def live_canary() -> tuple[list[str], dict[str, FileDeps]]:
    node_ids = collect_canary_node_ids(REPO_ROOT)
    assert node_ids, "expected a non-empty canary collection"
    dep_map, fallback, _sites = build_dependency_map(REPO_ROOT, files_from_node_ids(node_ids))
    assert fallback is None, fallback
    return node_ids, dep_map


@pytest.fixture(scope="module")
def live_medium() -> tuple[list[str], dict[str, FileDeps]]:
    node_ids = collect_canary_node_ids(REPO_ROOT, marker_expression=MEDIUM_MARKER)
    assert node_ids
    dep_map, fallback, _sites = build_dependency_map(REPO_ROOT, files_from_node_ids(node_ids))
    assert fallback is None, fallback
    return node_ids, dep_map


def test_every_collected_file_has_a_per_file_edge(live_canary: tuple[list[str], dict[str, FileDeps]]) -> None:
    _, dep_map = live_canary
    root = REPO_ROOT.resolve()
    shared, shared_fallback, _ = _build_shared_deps(root, {})
    assert shared_fallback is None, shared_fallback
    thin = sorted(
        test_file
        for test_file, file_deps in dep_map.items()
        if not file_deps.dynamic
        and not (
            set(file_deps.deps)
            - shared
            - set(_conftest_chain(test_file, root))
            - {rel for rel in (_rel(init, root) for init in _parent_inits(root / test_file, root)) if rel is not None}
        )
        and not analyze_python_file(root / test_file, root).deps <= shared
    )
    assert thin == []


def test_per_file_dependency_map_matches_full_tree_map(live_canary: tuple[list[str], dict[str, FileDeps]]) -> None:
    node_ids, full_map = live_canary
    targeted_ids = collect_canary_node_ids(REPO_ROOT, paths=EXACTNESS_TARGETS)
    targeted_map, fallback, _sites = build_dependency_map(REPO_ROOT, files_from_node_ids(targeted_ids))
    assert fallback is None, fallback
    assert sorted(targeted_map) == sorted(EXACTNESS_TARGETS)
    for test_file in EXACTNESS_TARGETS:
        assert targeted_map[test_file] == full_map[test_file], test_file
        assert sorted(nid for nid in targeted_ids if nid.split("::", 1)[0] == test_file) == sorted(
            nid for nid in node_ids if nid.split("::", 1)[0] == test_file
        )


def test_medium_single_module_diff_selects_subset(live_medium: tuple[list[str], dict[str, FileDeps]]) -> None:
    node_ids, dep_map = live_medium
    selection = compute_selection(
        ["benchbox/monitoring/profiler.py"],
        dep_map,
        node_ids,
        marker_expression=MEDIUM_MARKER,
        cant_affect=frozenset(),
        whole_suite_paths=MEDIUM_WHOLE_SUITE_PATHS,
    )
    assert selection["whole_suite"] is False
    assert 0 < selection["selected_count"] < selection["total_count"]
