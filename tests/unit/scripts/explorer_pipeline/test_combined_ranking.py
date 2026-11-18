from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from _project.scripts.explorer_pipeline.contract import EXPLORER_QPH_READ_MODEL_VERSION, QPH_RESULT_COLUMNS
from _project.scripts.explorer_pipeline.models import canonical_phase, get_ranking_config, primary_metric_value
from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from scripts.publication.check_explorer_compat import (
    check_schema_compatibility,
    create_in_memory_schema,
    validate_database_queries,
)
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE, combined_bundle

pytestmark = [pytest.mark.unit, pytest.mark.medium]

QPH_COLUMNS = [name for name, _ in QPH_RESULT_COLUMNS]


def _write(directory: Path, name: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _bundle(name: str, *, benchmark: str = "tpch", qph: float = 250.0, power: float = 1234.5, **kwargs) -> dict:
    payload = combined_bundle(name, benchmark=benchmark, qph=qph, **kwargs)
    payload["run"]["id"] = name
    payload["summary"]["tpc_metrics"]["power_at_size"] = power
    return payload


def _entry(tmp_path: Path, payload: dict):
    path = _write(tmp_path / "bundles", "bundle.json", payload)
    transformer = BundleTransformer()
    entry = transformer.to_manifest_entry(path)
    return entry, transformer.to_detail_result(path, entry.result_id)


def _corpus(tmp_path: Path, bundles: dict[str, dict]) -> Path:
    for name, payload in bundles.items():
        _write(tmp_path / "data" / "bundles", f"{name}.json", payload)
    return tmp_path / "data"


def _build(tmp_path: Path, bundles: dict[str, dict], **kwargs: int) -> Path:
    out = tmp_path / "out"
    ExplorerPipeline().run(_corpus(tmp_path, bundles), out, **kwargs)
    return out / "results.duckdb"


@pytest.mark.parametrize(
    ("family", "metric"),
    [("tpch", "qphh_at_size"), ("tpcds", "qphds_at_size")],
)
def test_combined_phase_ranks_on_the_composite_with_power_as_the_secondary(family: str, metric: str) -> None:
    config = get_ranking_config(family, "combined")

    assert (config.primary_metric, config.secondary_metric, config.primary_order) == (metric, "power_score", "desc")


@pytest.mark.parametrize("family", ["tpch", "tpcds"])
def test_power_and_throughput_phases_keep_their_metrics(family: str) -> None:
    assert get_ranking_config(family, "power").primary_metric == "power_score"
    assert get_ranking_config(family, "throughput").primary_metric == "throughput_at_size"


@pytest.mark.parametrize(
    ("family_id", "field"),
    [("tpch", "qphh_at_size"), ("tpcds", "qphds_at_size")],
)
def test_clean_combined_bundle_exposes_the_composite_beside_power_and_throughput(
    tmp_path: Path, family_id: str, field: str
) -> None:
    entry, detail = _entry(tmp_path, _bundle("duckdb", benchmark=family_id, qph=321.5))

    assert canonical_phase(entry.test_type) == "combined"
    assert getattr(entry, field) == pytest.approx(321.5)
    assert getattr(detail, field) == pytest.approx(321.5)
    assert entry.power_score == pytest.approx(1234.5)
    assert entry.throughput_at_size == pytest.approx(3741.26)
    assert primary_metric_value(entry, get_ranking_config(family_id, "combined").primary_metric) == pytest.approx(321.5)
    assert entry.ranking_exclusion_reason is None
    assert detail.ranking_exclusion_reason is None
    assert entry.load_scope == ["schema_creation", "data_loading"]


def _fail_stream(payload: dict) -> None:
    payload["phases"]["throughput_test"]["stream_results"][0]["success"] = False


def _fail_power_refresh(payload: dict) -> None:
    payload["phases"]["power_test"]["refresh_functions"][0]["status"] = "FAILED"


def _fail_refresh_stream(payload: dict) -> None:
    payload["phases"]["throughput_test"]["refresh_stream"]["status"] = "FAILED"


def _fail_second_throughput(payload: dict) -> None:
    payload["phases"]["throughput_test_2"]["status"] = "FAILED"


def _fail_maintenance(payload: dict) -> None:
    payload["phases"]["maintenance_test"]["operations"][0]["status"] = "FAILED"


@pytest.mark.parametrize(
    ("family_id", "mutate", "reason"),
    [
        ("tpch", _fail_stream, "combined_throughput_not_clean"),
        ("tpch", _fail_power_refresh, "combined_refresh_not_clean"),
        ("tpch", _fail_refresh_stream, "combined_refresh_not_clean"),
        ("tpcds", _fail_second_throughput, "combined_throughput_not_clean"),
        ("tpcds", _fail_maintenance, "combined_refresh_not_clean"),
    ],
)
def test_combined_bundle_with_a_failed_throughput_or_refresh_step_is_not_rankable(
    tmp_path: Path, family_id: str, mutate: Callable[[dict], None], reason: str
) -> None:
    payload = _bundle("duckdb", benchmark=family_id)
    mutate(payload)
    entry, detail = _entry(tmp_path, payload)

    assert entry.qphh_at_size is None
    assert entry.qphds_at_size is None
    assert entry.ranking_exclusion_reason == reason
    assert detail.ranking_exclusion_reason == reason


def test_power_bundles_leave_the_composite_columns_empty(tmp_path: Path) -> None:
    payload = json.loads(json.dumps(MINIMAL_BUNDLE))
    payload["summary"]["tpc_metrics"]["qphh_at_size"] = 5.0
    entry, _ = _entry(tmp_path, payload)

    assert canonical_phase(entry.test_type) == "power"
    assert entry.power_score == pytest.approx(1234.56)
    assert entry.qphh_at_size is None
    assert entry.qphds_at_size is None
    assert entry.load_scope is None
    assert entry.transactional_steps is None
    assert entry.ranking_exclusion_reason is None


def test_transactional_disclosure_is_read_per_step_when_recorded(tmp_path: Path) -> None:
    entry, detail = _entry(tmp_path, _bundle("chdb", transactional=False))

    assert entry.transactional_steps == {"RF1": False, "RF2": False}
    assert detail.transactional_steps == {"RF1": False, "RF2": False}
    assert entry.ranking_exclusion_reason is None


def test_transactional_disclosure_is_absent_when_the_bundle_does_not_record_it(tmp_path: Path) -> None:
    entry, _ = _entry(tmp_path, _bundle("duckdb"))

    assert entry.transactional_steps is None


def test_tpcds_transactional_disclosure_names_the_maintenance_operations(tmp_path: Path) -> None:
    entry, _ = _entry(tmp_path, _bundle("chdb", benchmark="tpcds", transactional=False))

    assert entry.transactional_steps == {"DF_SS": False, "LF_SS": False}


def _ranked_rows(db: Path, benchmark: str) -> list[tuple]:
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(
            "SELECT platform, rank, primary_metric, qphh_at_size, qphds_at_size, power_score, throughput_at_size, "
            "load_scope, transactional_steps FROM benchmark_rankings WHERE benchmark = ? AND phase = 'combined' "
            "ORDER BY rank",
            [benchmark],
        ).fetchall()


def test_combined_cohort_orders_by_the_composite_not_the_power_score(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        {
            "slow_power_fast_qph": _bundle("alpha", qph=900.0, power=10.0, transactional=False),
            "fast_power_slow_qph": _bundle("beta", qph=100.0, power=9000.0),
        },
        read_model_version=EXPLORER_QPH_READ_MODEL_VERSION,
    )

    rows = _ranked_rows(db, "tpch")

    assert [row[0] for row in rows] == ["alpha", "beta"]
    assert [row[2] for row in rows] == ["qphh_at_size", "qphh_at_size"]
    assert [row[3] for row in rows] == [900.0, 100.0]
    assert rows[0][5] == 10.0
    assert rows[0][6] == pytest.approx(3741.26)
    assert json.loads(rows[0][7]) == ["schema_creation", "data_loading"]
    assert json.loads(rows[0][8]) == {"RF1": False, "RF2": False}
    assert rows[1][8] is None


def test_tpcds_combined_cohort_ranks_on_qphds(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        {
            "one": _bundle("alpha", benchmark="tpcds", qph=50.0, power=9000.0),
            "two": _bundle("beta", benchmark="tpcds", qph=80.0, power=10.0),
        },
        read_model_version=EXPLORER_QPH_READ_MODEL_VERSION,
    )

    rows = _ranked_rows(db, "tpcds")

    assert [(row[0], row[2], row[4]) for row in rows] == [
        ("beta", "qphds_at_size", 80.0),
        ("alpha", "qphds_at_size", 50.0),
    ]


def test_a_dirty_combined_bundle_is_listed_but_not_ranked(tmp_path: Path) -> None:
    dirty = _bundle("beta", qph=9999.0)
    _fail_stream(dirty)
    db = _build(
        tmp_path,
        {"clean": _bundle("alpha", qph=100.0), "dirty": dirty},
        read_model_version=EXPLORER_QPH_READ_MODEL_VERSION,
    )

    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute(
            "SELECT platform, rank, is_ranking_eligible, ranking_exclusion_reason FROM benchmark_rankings "
            "WHERE phase = 'combined' ORDER BY platform"
        ).fetchall()

    assert rows == [
        ("alpha", 1, True, None),
        ("beta", None, False, "combined_throughput_not_clean"),
    ]


def test_cohort_metadata_names_the_composite_as_the_primary_metric(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        {"one": _bundle("alpha"), "two": _bundle("beta", qph=300.0)},
        read_model_version=EXPLORER_QPH_READ_MODEL_VERSION,
    )

    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute("SELECT DISTINCT cohort_key, primary_metric, primary_order FROM cohort_metadata").fetchall()

    assert rows == [("tpch-sf1-combined", "qphh_at_size", "desc")]


def test_read_model_18_omits_the_composite_columns(tmp_path: Path) -> None:
    db = _build(tmp_path, {"one": _bundle("alpha"), "power": _power_bundle()}, read_model_version=18)

    with duckdb.connect(str(db), read_only=True) as con:
        results = {row[0] for row in con.execute("DESCRIBE results").fetchall()}
        rankings = {row[0] for row in con.execute("DESCRIBE benchmark_rankings").fetchall()}

    assert not set(QPH_COLUMNS) & results
    assert not set(QPH_COLUMNS) & rankings


def test_read_model_19_adds_exactly_the_composite_columns(tmp_path: Path) -> None:
    base = _build(tmp_path / "base", {"one": _bundle("alpha"), "power": _power_bundle()}, read_model_version=18)
    upgraded = _build(tmp_path / "new", {"one": _bundle("alpha"), "power": _power_bundle()}, read_model_version=19)

    with duckdb.connect(str(base), read_only=True) as old, duckdb.connect(str(upgraded), read_only=True) as new:
        for table in ("results", "benchmark_rankings"):
            old_columns = [row[0] for row in old.execute(f"DESCRIBE {table}").fetchall()]
            new_columns = [row[0] for row in new.execute(f"DESCRIBE {table}").fetchall()]
            assert new_columns == [*old_columns, *QPH_COLUMNS]
        assert new.execute("SELECT read_model_version FROM metadata").fetchone() == (19,)
        view_columns = {row[0] for row in new.execute("DESCRIBE platform_index_rows").fetchall()}
        assert {"qphh_at_size", "qphds_at_size"} <= view_columns


def test_compat_checker_accepts_read_model_19() -> None:
    results = check_schema_compatibility([19])

    assert results == {19: []}
    con = create_in_memory_schema(19)
    assert validate_database_queries(con, 19) == []


def test_compat_checker_validates_a_built_read_model_19(tmp_path: Path) -> None:
    from scripts.publication.check_explorer_compat import validate_database_schema

    db = _build(tmp_path, {"one": _bundle("alpha"), "power": _power_bundle()}, read_model_version=19)

    with duckdb.connect(str(db), read_only=True) as con:
        assert validate_database_schema(con, expected_version=19) == []
        assert validate_database_queries(con, 19) == []


def test_read_model_18_leaves_combined_bundles_out(tmp_path: Path) -> None:
    db = _build(tmp_path, {"combined": _bundle("alpha"), "power": _power_bundle()}, read_model_version=18)

    with duckdb.connect(str(db), read_only=True) as con:
        phases = {row[0] for row in con.execute("SELECT DISTINCT phase FROM benchmark_rankings").fetchall()}
        metrics = {row[0] for row in con.execute("SELECT DISTINCT primary_metric FROM benchmark_rankings").fetchall()}

    assert phases == {"power"}
    assert metrics == {"power_score"}


def _power_bundle() -> dict:
    payload = json.loads(json.dumps(MINIMAL_BUNDLE))
    payload["run"]["id"] = "power-run"
    return payload


def test_default_build_publishes_combined_cohorts(tmp_path: Path) -> None:
    db = _build(tmp_path, {"combined": _bundle("alpha"), "power": _power_bundle()})

    with duckdb.connect(str(db), read_only=True) as con:
        version = con.execute("SELECT read_model_version FROM metadata").fetchone()
        metrics = {row[0] for row in con.execute("SELECT DISTINCT primary_metric FROM benchmark_rankings").fetchall()}

    assert version == (19,)
    assert metrics == {"power_score", "qphh_at_size"}
