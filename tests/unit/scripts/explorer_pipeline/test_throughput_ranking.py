from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import duckdb
import pytest

from _project.scripts.explorer_pipeline.models import (
    BenchmarkSummary,
    PlatformRow,
    canonical_phase,
    get_ranking_config,
)
from _project.scripts.explorer_pipeline.pipeline import (
    ExplorerPipeline,
    _build_benchmark_summaries,
    _build_meta_leaderboard,
)
from _project.scripts.explorer_pipeline.ranking import rank_platforms
from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from _project.scripts.results_explorer_snapshot_invariants import check_snapshot
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE, throughput_bundle

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _write(directory: Path, name: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _pairs(tmp_path: Path, bundles: dict[str, dict]) -> list:
    transformer = BundleTransformer()
    pairs = []
    for name, payload in bundles.items():
        path = _write(tmp_path / "bundles", f"{name}.json", payload)
        entry = transformer.to_manifest_entry(path)
        pairs.append((entry, transformer.to_detail_result(path, entry.result_id)))
    return pairs


@pytest.mark.parametrize(
    ("family", "phase", "metric", "order"),
    [
        ("tpch", "throughput", "throughput_at_size", "desc"),
        ("tpcds", "throughput", "throughput_at_size", "desc"),
        ("tpch", "power", "power_score", "desc"),
        ("tpcds", "power", "power_score", "desc"),
        ("tpch", "unknown", "power_score", "desc"),
        ("ssb", "throughput", "display_geomean_ms", "asc"),
        ("clickbench", "throughput", "display_geomean_ms", "asc"),
    ],
)
def test_ranking_metric_is_chosen_per_family_and_phase(family: str, phase: str, metric: str, order: str) -> None:
    config = get_ranking_config(family, phase)

    assert (config.primary_metric, config.primary_order) == (metric, order)


def test_ranking_config_without_a_phase_keeps_the_power_metric() -> None:
    assert get_ranking_config("tpch").primary_metric == "power_score"
    assert get_ranking_config("tpcds").primary_metric == "power_score"


def test_throughput_bundle_exposes_throughput_at_size_and_stream_count(tmp_path: Path) -> None:
    ((entry, detail),) = _pairs(tmp_path, {"spark": throughput_bundle("spark", streams=3, throughput_at_size=3741.26)})

    assert canonical_phase(entry.test_type) == "throughput"
    assert entry.throughput_at_size == pytest.approx(3741.26)
    assert detail.throughput_at_size == pytest.approx(3741.26)
    assert entry.stream_count == 3
    assert detail.stream_count == 3
    assert entry.power_score is None
    assert entry.ranking_exclusion_reason is None
    assert detail.ranking_exclusion_reason is None


def test_power_bundle_has_no_throughput_fields(tmp_path: Path) -> None:
    ((entry, detail),) = _pairs(tmp_path, {"duckdb": copy.deepcopy(MINIMAL_BUNDLE)})

    assert entry.power_score == pytest.approx(1234.56)
    assert entry.throughput_at_size is None
    assert entry.stream_count is None
    assert detail.stream_count is None
    assert entry.ranking_exclusion_reason is None


def test_stream_count_is_only_derived_for_the_throughput_phase(tmp_path: Path) -> None:
    payload = copy.deepcopy(MINIMAL_BUNDLE)
    payload["phases"] = {
        "power_test": {"status": "COMPLETED"},
        "throughput_test": {"status": "COMPLETED", "stream_results": [{"stream_id": 1}, {"stream_id": 2}]},
    }
    ((entry, _),) = _pairs(tmp_path, {"power": payload})

    assert canonical_phase(entry.test_type) == "power"
    assert entry.stream_count is None


def test_throughput_bundle_without_stream_results_has_no_stream_count(tmp_path: Path) -> None:
    payload = throughput_bundle("spark")
    del payload["phases"]["throughput_test"]["stream_results"]
    ((entry, _),) = _pairs(tmp_path, {"spark": payload})

    assert entry.stream_count is None
    assert entry.throughput_at_size is None
    assert entry.ranking_exclusion_reason == "missing_primary_metric"


def test_stream_count_mismatch_from_run_withholds_throughput_score(tmp_path: Path) -> None:
    payload = throughput_bundle("spark", streams=3, throughput_at_size=3741.26)
    payload["run"]["streams"] = 4
    ((entry, detail),) = _pairs(tmp_path, {"spark": payload})

    assert entry.throughput_at_size is None
    assert entry.ranking_exclusion_reason == "stream_count_mismatch"
    assert detail.throughput_at_size is None
    assert detail.ranking_exclusion_reason == "stream_count_mismatch"


def test_stream_count_mismatch_from_config_withholds_throughput_score(tmp_path: Path) -> None:
    payload = throughput_bundle("spark", streams=3, throughput_at_size=3741.26)
    payload["run"].pop("streams")
    payload["config"]["streams"] = 4
    ((entry, _),) = _pairs(tmp_path, {"spark": payload})

    assert entry.throughput_at_size is None
    assert entry.ranking_exclusion_reason == "stream_count_mismatch"


def test_missing_configured_stream_count_keeps_throughput_score(tmp_path: Path) -> None:
    payload = throughput_bundle("spark", streams=3, throughput_at_size=3741.26)
    payload["run"].pop("streams")
    ((entry, _),) = _pairs(tmp_path, {"spark": payload})

    assert entry.throughput_at_size == pytest.approx(3741.26)


def _timed_out_stream_bundle() -> dict:
    payload = throughput_bundle("spark", streams=3, throughput_at_size=500.0)
    phase = payload["phases"]["throughput_test"]
    phase["status"] = "FAILED"
    phase["stream_results"] = phase["stream_results"][:2]
    phase["errors"] = ["Stream 3 timed out after 600s"]
    return payload


def test_timed_out_stream_with_a_score_and_no_failed_queries_is_not_ranked(tmp_path: Path) -> None:
    payload = _timed_out_stream_bundle()
    assert payload["summary"]["queries"]["failed"] == 0
    ((entry, detail),) = _pairs(tmp_path, {"spark": payload})

    assert canonical_phase(entry.test_type) == "throughput"
    assert entry.stream_count == 2
    assert entry.throughput_at_size is None
    assert detail.throughput_at_size is None
    assert entry.ranking_exclusion_reason == "stream_count_mismatch"
    assert detail.ranking_exclusion_reason == "stream_count_mismatch"


@pytest.mark.parametrize(
    ("phase_changes", "expected_reason"),
    [
        ({"status": "FAILED"}, "missing_primary_metric"),
        ({"status": "PARTIAL"}, "missing_primary_metric"),
        ({"stream_results": []}, "stream_count_mismatch"),
        (
            {"stream_results": [{"stream_id": 1, "success": True}, {"stream_id": 2, "success": False}]},
            "stream_count_mismatch",
        ),
        ({"stream_results": [{"stream_id": 1, "success": "true"}]}, "stream_count_mismatch"),
        ({"errors": ["stream 2 reported an error"]}, "missing_primary_metric"),
        (
            {"outstanding_work": {"stream_ids": [3], "cleanup_state": "outstanding"}},
            "missing_primary_metric",
        ),
    ],
)
def test_throughput_score_is_absent_unless_the_phase_completed_cleanly(
    tmp_path: Path, phase_changes: dict, expected_reason: str
) -> None:
    payload = throughput_bundle("spark", streams=3, throughput_at_size=500.0)
    payload["phases"]["throughput_test"].update(phase_changes)
    ((entry, _),) = _pairs(tmp_path, {"spark": payload})

    assert entry.throughput_at_size is None
    assert entry.ranking_exclusion_reason == expected_reason


def test_completed_phase_with_empty_error_fields_keeps_its_score(tmp_path: Path) -> None:
    payload = throughput_bundle("spark", streams=3, throughput_at_size=500.0)
    payload["phases"]["throughput_test"].update({"errors": [], "outstanding_work": None})
    ((entry, _),) = _pairs(tmp_path, {"spark": payload})

    assert entry.throughput_at_size == pytest.approx(500.0)
    assert entry.ranking_exclusion_reason is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"throughput_at_size": None}, "missing_primary_metric"),
        ({"throughput_at_size": 0.0}, "non_positive_primary_metric"),
        ({"throughput_at_size": -5.0}, "non_positive_primary_metric"),
        ({"throughput_at_size": None, "power_at_size": 1234.0}, "missing_primary_metric"),
    ],
)
def test_throughput_cohort_requires_a_positive_throughput_at_size(tmp_path: Path, changes: dict, reason: str) -> None:
    ((entry, detail),) = _pairs(tmp_path, {"spark": throughput_bundle("spark", **changes)})

    assert entry.ranking_exclusion_reason == reason
    assert detail.ranking_exclusion_reason == reason


def test_unparseable_throughput_at_size_is_missing(tmp_path: Path) -> None:
    payload = throughput_bundle("spark")
    payload["summary"]["tpc_metrics"]["throughput_at_size"] = "abc"
    ((entry, _),) = _pairs(tmp_path, {"spark": payload})

    assert entry.throughput_at_size is None
    assert entry.ranking_exclusion_reason == "missing_primary_metric"


def test_failed_throughput_bundle_is_excluded_even_with_a_throughput_score(tmp_path: Path) -> None:
    payload = throughput_bundle("sqlite", throughput_at_size=120.0)
    payload["summary"]["queries"] = {"total": 66, "passed": 21, "failed": 45}
    payload["summary"]["validation"] = "partial"
    ((entry, _),) = _pairs(tmp_path, {"sqlite": payload})

    assert entry.ranking_exclusion_reason == "failed_queries"


def test_power_phase_still_ranks_on_power_score_not_throughput(tmp_path: Path) -> None:
    payload = copy.deepcopy(MINIMAL_BUNDLE)
    payload["summary"]["tpc_metrics"] = {"throughput_at_size": 3741.0}
    ((entry, _),) = _pairs(tmp_path, {"duckdb": payload})

    assert entry.ranking_exclusion_reason == "missing_primary_metric"


def _platform_row(platform_id: str, throughput_at_size: float | None) -> PlatformRow:
    return PlatformRow(
        result_id=f"result-{platform_id}",
        short_id=platform_id,
        platform_id=platform_id,
        platform=platform_id,
        platform_version=None,
        tuning_mode=None,
        tuning_hash=None,
        execution_mode=None,
        trust_label="maintainer-run",
        run_date="2026-10-03",
        is_ranking_eligible=True,
        power_score=None,
        throughput_at_size=throughput_at_size,
        display_geomean_ms=1000.0,
        sample_geomean_ms=None,
        cost_usd=None,
        timings={},
    )


def test_throughput_cohort_ranks_higher_throughput_first() -> None:
    summary = BenchmarkSummary(
        benchmark="tpch",
        scale_factor=1.0,
        phase="throughput",
        stream_count=3,
        query_ids=[],
        platforms=[
            _platform_row("duckdb", 1500.0),
            _platform_row("spark", 3741.0),
            _platform_row("doris", 2200.0),
            _platform_row("broken", None),
        ],
        ranking=get_ranking_config("tpch", "throughput"),
    )

    ranked = rank_platforms(summary)
    by_platform = {row.row.platform_id: row for row in ranked.rows}

    assert ranked.primary_metric == "throughput_at_size"
    assert ranked.higher_is_better
    assert [row.row.platform_id for row in ranked.rows[:3]] == ["spark", "doris", "duckdb"]
    assert by_platform["spark"].rank == 1
    assert by_platform["duckdb"].rank == 3
    assert by_platform["spark"].metric_value == pytest.approx(3741.0)
    assert by_platform["duckdb"].speedup_vs_best == pytest.approx(1500.0 / 3741.0)
    assert by_platform["broken"].rank is None
    assert by_platform["broken"].ranking_exclusion_reason == "missing_primary_metric"


def _split_accumulator(tmp_path: Path) -> tuple[dict, dict[str, str]]:
    bundles = {
        "spark3": throughput_bundle("spark", streams=3, throughput_at_size=3741.0),
        "duckdb3": throughput_bundle("duckdb", streams=3, throughput_at_size=1500.0),
        "doris3": throughput_bundle("doris", streams=3, throughput_at_size=2200.0),
        "duckdb2": throughput_bundle("duckdb", streams=2, throughput_at_size=1400.0),
        "spark2": throughput_bundle("spark", streams=2, throughput_at_size=3500.0),
    }
    accum: dict = {}
    full_to_short: dict[str, str] = {}
    for entry, detail in _pairs(tmp_path, bundles):
        key = ("tpch", entry.scale_factor, canonical_phase(detail.test_type), detail.stream_count)
        accum.setdefault(key, []).append((entry, detail))
        full_to_short[entry.result_id] = entry.result_id[-8:]
    return accum, full_to_short


def test_throughput_rankings_are_split_by_stream_count(tmp_path: Path) -> None:
    accum, full_to_short = _split_accumulator(tmp_path)

    summaries = dict(_build_benchmark_summaries(accum, full_to_short))

    assert sorted(summaries) == [("tpch", 1.0, "throughput", 2), ("tpch", 1.0, "throughput", 3)]
    three = summaries[("tpch", 1.0, "throughput", 3)]
    two = summaries[("tpch", 1.0, "throughput", 2)]
    assert three.stream_count == 3
    assert two.stream_count == 2
    assert three.ranking.primary_metric == "throughput_at_size"
    assert [row.platform_id for row in three.platforms] == ["spark", "doris", "duckdb"]
    assert [row.platform_id for row in two.platforms] == ["spark", "duckdb"]
    assert all(row.is_ranking_eligible for row in three.platforms + two.platforms)


def test_meta_leaderboard_has_one_cohort_per_stream_count(tmp_path: Path) -> None:
    accum, full_to_short = _split_accumulator(tmp_path)
    summaries = _build_benchmark_summaries(accum, full_to_short)

    meta = _build_meta_leaderboard(summaries, "2026-10-04T00:00:00Z", full_to_short)

    cohorts = {cohort["key"]: cohort for cohort in meta["cohorts"]}
    assert sorted(cohorts) == ["tpch-sf1-throughput-2streams", "tpch-sf1-throughput-3streams"]
    three = cohorts["tpch-sf1-throughput-3streams"]
    assert three["stream_count"] == 3
    assert three["label"] == "TPC-H SF1 Throughput (3 streams)"
    assert three["href"] == "/results/tpch/?sf=1.0&phase=throughput&streams=3"
    assert three["primary_metric"] == "throughput_at_size"
    assert three["primary_order"] == "desc"
    assert [(p["platform_id"], p["rank"]) for p in three["platforms"]] == [("spark", 1), ("doris", 2), ("duckdb", 3)]


def test_power_cohort_label_and_key_are_unchanged(tmp_path: Path) -> None:
    payload = copy.deepcopy(MINIMAL_BUNDLE)
    other = copy.deepcopy(MINIMAL_BUNDLE)
    other["platform"] = {"name": "sqlite", "version": "3.0"}
    other["summary"]["tpc_metrics"] = {"power_at_size": 500.0}
    pairs = _pairs(tmp_path, {"duckdb": payload, "sqlite": other})
    accum = {("tpch", 0.1, "power", None): pairs}
    full_to_short = {entry.result_id: entry.result_id[-8:] for entry, _ in pairs}

    meta = _build_meta_leaderboard(_build_benchmark_summaries(accum, full_to_short), "2026-10-04T00:00:00Z")

    (cohort,) = meta["cohorts"]
    assert cohort["key"] == "tpch-sf0.1-power"
    assert cohort["label"] == "TPC-H SF0.1"
    assert cohort["href"] == "/results/tpch/?sf=0.1&phase=power"
    assert cohort["stream_count"] is None
    assert cohort["primary_metric"] == "power_score"


@pytest.fixture(scope="module")
def throughput_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("throughput_pipeline")
    bundles_dir = base / "data" / "bundles"
    power = copy.deepcopy(MINIMAL_BUNDLE)
    power["benchmark"]["scale_factor"] = 1.0
    power_other = copy.deepcopy(power)
    power_other["platform"] = {"name": "sqlite", "version": "3.0"}
    power_other["summary"]["tpc_metrics"] = {"power_at_size": 900.0}
    bundles = {
        "power-duckdb": power,
        "power-sqlite": power_other,
        "spark3": throughput_bundle("spark", streams=3, throughput_at_size=3741.0),
        "duckdb3": throughput_bundle("duckdb", streams=3, throughput_at_size=1500.0),
        "doris3": throughput_bundle("doris", streams=3, throughput_at_size=2200.0),
        "duckdb2": throughput_bundle("duckdb", streams=2, throughput_at_size=1400.0),
        "spark2": throughput_bundle("spark", streams=2, throughput_at_size=3500.0),
        "broken3": throughput_bundle("sqlite", streams=3, throughput_at_size=None),
    }
    for name, payload in bundles.items():
        _write(bundles_dir, f"{name}.json", payload)
    output_dir = base / "out"
    ExplorerPipeline().run(base / "data", output_dir, bundle_url_prefix="/results/data/bundles")
    return output_dir / "results.duckdb"


def _rows(db: Path, sql: str, params: list | None = None) -> list[tuple]:
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(sql, params or []).fetchall()


def test_results_table_carries_throughput_score_and_stream_count(throughput_db: Path) -> None:
    rows = _rows(
        throughput_db,
        "SELECT platform_id, test_type, throughput_at_size, stream_count, power_score FROM results"
        " WHERE test_type = 'throughput' ORDER BY platform_id, stream_count",
    )

    by_key = {(row[0], row[3]): row[2] for row in rows}
    assert by_key[("sqlite", 3)] is None
    assert by_key[("spark", 3)] == pytest.approx(3741.0)
    assert by_key[("duckdb", 2)] == pytest.approx(1400.0)
    assert all(row[4] is None for row in rows)
    power_rows = _rows(throughput_db, "SELECT DISTINCT stream_count FROM results WHERE test_type = 'power'")
    assert power_rows == [(None,)]


def test_benchmark_rankings_split_throughput_by_stream_count(throughput_db: Path) -> None:
    rows = _rows(
        throughput_db,
        "SELECT stream_count, platform_id, rank, primary_metric, primary_order, throughput_at_size,"
        " cohort_ranked_count, ranking_exclusion_reason FROM benchmark_rankings"
        " WHERE phase = 'throughput' ORDER BY stream_count, rank NULLS LAST, platform_id",
    )

    three = [row for row in rows if row[0] == 3]
    two = [row for row in rows if row[0] == 2]
    assert [(row[1], row[2]) for row in three] == [("spark", 1), ("doris", 2), ("duckdb", 3), ("sqlite", None)]
    assert [(row[1], row[2]) for row in two] == [("spark", 1), ("duckdb", 2)]
    assert {row[3] for row in rows} == {"throughput_at_size"}
    assert {row[4] for row in rows} == {"desc"}
    assert three[0][5] == pytest.approx(3741.0)
    assert {row[6] for row in three} == {3}
    assert {row[6] for row in two} == {2}
    assert three[-1][7] == "missing_primary_metric"


def test_power_rankings_are_unchanged_and_have_no_stream_split(throughput_db: Path) -> None:
    rows = _rows(
        throughput_db,
        "SELECT platform_id, rank, primary_metric, stream_count FROM benchmark_rankings"
        " WHERE phase = 'power' ORDER BY rank",
    )

    assert rows == [("duckdb", 1, "power_score", None), ("sqlite", 2, "power_score", None)]


def test_matrix_cells_and_cohort_metadata_carry_the_stream_split(throughput_db: Path) -> None:
    cells = _rows(
        throughput_db,
        "SELECT DISTINCT stream_count FROM benchmark_matrix_cells WHERE phase = 'throughput' ORDER BY 1",
    )
    cohorts = _rows(
        throughput_db,
        "SELECT cohort_key, stream_count, primary_metric, cohort_label, cohort_href FROM cohort_metadata"
        " WHERE phase = 'throughput' GROUP BY ALL ORDER BY cohort_key",
    )

    assert cells == [(2,), (3,)]
    assert cohorts == [
        (
            "tpch-sf1-throughput-2streams",
            2,
            "throughput_at_size",
            "TPC-H SF1 Throughput (2 streams)",
            "/results/tpch/?sf=1.0&phase=throughput&streams=2",
        ),
        (
            "tpch-sf1-throughput-3streams",
            3,
            "throughput_at_size",
            "TPC-H SF1 Throughput (3 streams)",
            "/results/tpch/?sf=1.0&phase=throughput&streams=3",
        ),
    ]


def test_snapshot_invariants_accept_a_throughput_snapshot(throughput_db: Path) -> None:
    assert check_snapshot(throughput_db) == []


def test_snapshot_invariants_reject_a_ranked_throughput_row_without_a_throughput_score(
    throughput_db: Path, tmp_path: Path
) -> None:
    corrupted = tmp_path / "results.duckdb"
    shutil.copy2(throughput_db, corrupted)
    with duckdb.connect(str(corrupted)) as con:
        con.execute("UPDATE benchmark_rankings SET throughput_at_size = NULL WHERE rank = 1 AND phase = 'throughput'")

    errors = check_snapshot(corrupted)

    assert any("ranked rows must have a valid primary metric" in error for error in errors)
