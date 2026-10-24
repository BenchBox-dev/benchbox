from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from _project.scripts.cross_surface_applicability_sweep import (
    ABANDONED,
    ARTIFACT,
    BLOCKED,
    CANDIDATE_UNVERIFIED,
    GATEABLE,
    NO_DF_QUERY_SURFACE,
    NOT_CHEAPLY_GATEABLE,
    build_applicability_sweep,
    render_markdown,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

_W2_FALLBACK_BENCHMARKS = {"metadata_primitives", "tpcdi", "transaction_primitives", "write_primitives"}


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return build_applicability_sweep()


def test_w2_fallback_set_is_exactly_the_registry_less_benchmarks(rows):
    no_surface = {r["benchmark"] for r in rows if r["status"] == NO_DF_QUERY_SURFACE}
    assert no_surface == _W2_FALLBACK_BENCHMARKS, f"w2-fallback set changed: {sorted(no_surface)}"


_CANDIDATE_UNVERIFIED_BENCHMARKS: set[str] = set()

_NOT_CHEAPLY_GATEABLE_BENCHMARKS = {"joinorder"}

_ABANDONED_BENCHMARKS = {"tpcds_obt"}


def test_registry_bearing_benchmarks_are_gateable(rows):
    by_id = {r["benchmark"]: r["status"] for r in rows}
    assert "flightdata" not in by_id, "flightdata graduated to enforced GATES and must leave the candidates"
    assert by_id.get("joinorder") == NOT_CHEAPLY_GATEABLE
    gateable = {r["benchmark"] for r in rows if r["status"] == GATEABLE}
    assert gateable == set(), f"unexpected gateable candidates: {sorted(gateable)}"
    assert "datavault" not in by_id


def test_zero_overlap_registries_are_candidate_unverified_not_gateable(rows):
    unverified = {r["benchmark"] for r in rows if r["status"] == CANDIDATE_UNVERIFIED}
    assert unverified == _CANDIDATE_UNVERIFIED_BENCHMARKS, f"candidate-unverified set changed: {sorted(unverified)}"
    gateable = {r["benchmark"] for r in rows if r["status"] == GATEABLE}
    assert _CANDIDATE_UNVERIFIED_BENCHMARKS.isdisjoint(gateable), "a zero-overlap benchmark was counted as gateable"


def test_bounded_scale_rejecting_benchmarks_are_not_cheaply_gateable(rows):
    by_id = {r["benchmark"]: r for r in rows}
    not_cheaply = {r["benchmark"] for r in rows if r["status"] == NOT_CHEAPLY_GATEABLE}
    assert not_cheaply == _NOT_CHEAPLY_GATEABLE_BENCHMARKS, f"not-cheaply-gateable set changed: {sorted(not_cheaply)}"
    assert by_id["joinorder"]["status"] == NOT_CHEAPLY_GATEABLE
    assert "SF=0.01" in by_id["joinorder"].get("reason", "")
    assert "data_manifest.toml" in by_id["joinorder"].get("reason", "")
    assert "joinorder_synthetic" in by_id["joinorder"].get("reason", "")
    gateable = {r["benchmark"] for r in rows if r["status"] == GATEABLE}
    assert _NOT_CHEAPLY_GATEABLE_BENCHMARKS.isdisjoint(gateable)


def test_abandoned_correspondences_stay_abandoned(rows):
    by_id = {r["benchmark"]: r for r in rows}
    abandoned = {r["benchmark"] for r in rows if r["status"] == ABANDONED}
    assert abandoned == _ABANDONED_BENCHMARKS, f"abandoned set changed: {sorted(abandoned)}"
    assert by_id["tpcds_obt"]["status"] == ABANDONED
    assert "no correspondence" in by_id["tpcds_obt"].get("reason", "")
    assert "id_mapping_decision" in by_id["tpcds_obt"].get("reason", "")
    gateable = {r["benchmark"] for r in rows if r["status"] == GATEABLE}
    assert _ABANDONED_BENCHMARKS.isdisjoint(gateable)
    unverified = {r["benchmark"] for r in rows if r["status"] == CANDIDATE_UNVERIFIED}
    assert _ABANDONED_BENCHMARKS.isdisjoint(unverified), "an abandoned verdict was re-opened as candidate-unverified"


def test_data_provenance_detects_downloaders_with_bounded_offline_exception():
    from _project.scripts.cross_surface_applicability_sweep import _data_provenance

    assert _data_provenance("nyctaxi") == "network-fetch"
    assert _data_provenance("joinorder") == "manifest-fetch"
    assert _data_provenance("flightdata") == "generated"
    assert _data_provenance("tpch") == "generated"


def test_staged_gates_are_marked_not_unguarded(rows):
    by_id = {r["benchmark"]: r for r in rows}
    assert "flightdata" not in by_id
    assert "datavault" not in by_id
    assert "tpcds" not in by_id
    staged = {r["benchmark"] for r in rows if r.get("staged")}
    assert staged == set(), f"staged set changed: {sorted(staged)}"
    assert by_id["joinorder"].get("staged") is False


def test_no_candidate_is_silently_dropped(rows):
    assert rows, "applicability sweep produced no candidates"
    for r in rows:
        assert r["status"] in {
            GATEABLE,
            CANDIDATE_UNVERIFIED,
            NOT_CHEAPLY_GATEABLE,
            NO_DF_QUERY_SURFACE,
            BLOCKED,
            ABANDONED,
        }, r


def test_committed_artifact_is_current(rows):
    assert ARTIFACT.exists(), f"missing {ARTIFACT}"
    assert ARTIFACT.read_text(encoding="utf-8") == render_markdown(rows), (
        "cross-surface applicability artifact is stale; run `make cross-surface-applicability-report` and commit"
    )
