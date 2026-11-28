from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import duckdb
import pytest

from _project.scripts.explorer_pipeline import corpus_outcome
from _project.scripts.explorer_pipeline.medians import version_order
from _project.scripts.explorer_pipeline.models import get_ranking_config
from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from _project.scripts.explorer_pipeline.ranking import MINIMUM_RANKED_COHORT, rank_scores
from benchbox.core.results import corpus_rule, corpus_score
from tests.unit.scripts.explorer_pipeline.conftest import combined_bundle, scored_minimal_bundle

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "explorer_read_model" / "next_read_model_contract.json"
CHECKER = REPO_ROOT / "scripts" / "publication" / "check_explorer_compat.py"
PIPELINE_DIR = REPO_ROOT / "_project" / "scripts" / "explorer_pipeline"
BASE_SCORE = 1000.0
SLOW_FACTORS = (10.0, 20.0, 40.0)


def _checker() -> Any:
    spec = importlib.util.spec_from_file_location("check_explorer_compat_corpus", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _power_run(name: str, *, version: str = "1.2.0", slow: tuple[str, float] | None = None) -> dict:
    data = scored_minimal_bundle(BASE_SCORE)
    data["run"]["id"] = name
    data["run"]["timestamp"] = f"2026-03-15T10:{len(name) % 60:02d}:{sum(map(ord, name)) % 60:02d}.000000"
    data["platform"]["version"] = version
    data["execution"].update(driver_actual_version=version, driver_resolved_version=version)
    if slow is not None:
        query_id, factor = slow
        for query in data["queries"]:
            if query["id"] == query_id:
                query["ms"] *= factor
    return data


def _build(tmp_path: Path, bundles: dict[str, dict]) -> Path:
    bundles_dir = tmp_path / "data" / "bundles"
    bundles_dir.mkdir(parents=True)
    for name, payload in bundles.items():
        (bundles_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    ExplorerPipeline().run(tmp_path / "data", tmp_path / "out")
    return tmp_path / "out" / "results.duckdb"


def _medians(db: Path) -> list[tuple]:
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(
            "SELECT comparison_version, version_is_release, run_count, median_score, spread, is_leaderboard_version "
            "FROM identity_medians ORDER BY comparison_version"
        ).fetchall()


def _stored_types(con: Any, checker: Any, table: str) -> dict[str, str]:
    return {
        row[0]: checker.normalize_type(row[1])
        for row in con.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?", [table]
        ).fetchall()
    }


def _engine_run(engine: str, power: float, *, version: str = "1.2.0") -> dict:
    data = scored_minimal_bundle(power)
    data["run"]["id"] = f"{engine}-{version}"
    data["platform"] = {"name": engine, "version": version}
    data["execution"] = {}
    return data


def _ranked_medians(db: Path) -> list[tuple]:
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(
            "SELECT variant_id, comparison_version, median_score, rank, total_in_cohort, ranking_exclusion_reason "
            "FROM identity_medians ORDER BY variant_id, comparison_version"
        ).fetchall()


def test_the_emitted_read_model_matches_the_contract_fixture(tmp_path: Path) -> None:
    contract = json.loads(FIXTURE.read_text(encoding="utf-8"))
    checker = _checker()
    db = _build(
        tmp_path,
        {"partial": _partial_run("partial"), "full": _full_run("full")},
    )

    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT read_model_version FROM metadata").fetchone() == (contract["read_model_version"],)
        assert checker.validate_database_schema(con, contract["read_model_version"]) == []
        assert checker.validate_database_queries(con, contract["read_model_version"]) == []
        columns = {
            row[0]: row[1]
            for row in con.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'results'"
            ).fetchall()
        }
        view_columns = {row[0] for row in con.execute("DESCRIBE result_detail_metrics").fetchall()}
        for column in contract["tables"]["results"]["added_columns"]:
            assert checker.normalize_type(columns[column["name"]]) == column["type"]
        assert set(contract["views"]["result_detail_metrics"]) <= view_columns
        for table in ("result_lane_scores", "identity_medians"):
            declared = {column["name"]: column["type"] for column in contract["tables"][table]["columns"]}
            assert _stored_types(con, checker, table) == declared
        ranking_types = _stored_types(con, checker, "benchmark_rankings")
        for column in contract["tables"]["benchmark_rankings"]["added_columns"]:
            assert ranking_types[column["name"]] == column["type"]
        median_reasons = next(
            set(column["allowed_values"])
            for column in contract["tables"]["identity_medians"]["columns"]
            if column["name"] == "ranking_exclusion_reason"
        )
        for (reason,) in con.execute("SELECT DISTINCT ranking_exclusion_reason FROM identity_medians").fetchall():
            assert reason is None or reason in median_reasons
        allowed = {
            column["name"]: set(column["allowed_values"])
            for column in contract["tables"]["results"]["added_columns"]
            if "allowed_values" in column
        }
        for corpus_class, lanes in con.execute("SELECT corpus_class, corpus_lanes FROM results").fetchall():
            assert corpus_class in allowed["corpus_class"]
            assert set(json.loads(lanes)) <= allowed["corpus_lanes"]
        for lane, source in con.execute("SELECT lane, score_source FROM result_lane_scores").fetchall():
            assert lane in allowed["corpus_lanes"]
            assert source in {
                value
                for name, value in vars(corpus_rule).items()
                if name.startswith("SCORE_") and isinstance(value, str)
            }


def _stamped(data: dict) -> dict:
    data["benchmark"]["compliance_class"] = "unofficial_nonstandard"
    data["summary"]["tpc_metrics"] = {"suppressed": True}
    data["run"]["id"] = "stamped"
    data["run"]["timestamp"] = "2026-03-16T10:00:00.000000"
    return data


def _full_run(name: str) -> dict:
    data = combined_bundle(name)
    data["run"]["id"] = name
    return data


def _partial_run(name: str) -> dict:
    data = combined_bundle(name)
    data["run"]["id"] = name
    data["phases"]["throughput_test"].pop("refresh_stream")
    return data


def test_a_stamp_the_writer_recorded_does_not_change_the_outcome(tmp_path: Path) -> None:
    db = _build(tmp_path, {"plain": _power_run("plain"), "stamped": _stamped(_power_run("other"))})

    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute(
            "SELECT corpus_class, corpus_lanes, is_ranking_eligible, ranking_exclusion_reason FROM results"
        ).fetchall()
        ranks = con.execute("SELECT rank FROM benchmark_rankings ORDER BY rank").fetchall()

    assert rows == [("power_only", '["power_only"]', True, None)] * 2
    assert len(ranks) == 2
    assert all(rank is not None for (rank,) in ranks)


def test_the_median_row_takes_the_score_of_per_query_medians(tmp_path: Path) -> None:
    runs = {
        f"run{index}": _power_run(f"run{index}", slow=(f"Q{index + 1}", factor))
        for index, factor in enumerate(SLOW_FACTORS)
    }
    run_scores = sorted(corpus_score.power_at_size(run) for run in runs.values())
    median_of_scores = run_scores[1]
    db = _build(tmp_path, runs)

    ((version, is_release, run_count, median_score, spread, standing),) = _medians(db)

    assert (version, is_release, run_count, standing) == ("1.2.0", True, 3, True)
    assert median_score == pytest.approx(BASE_SCORE)
    assert abs(median_score - median_of_scores) / BASE_SCORE > 0.01
    assert spread == pytest.approx((run_scores[-1] - run_scores[0]) / median_score)
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM benchmark_rankings WHERE rank IS NOT NULL").fetchone() == (3,)


def test_a_release_stands_for_the_platform_and_a_pre_release_stays_in_the_version_view(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        {
            "release": _power_run("release", version="1.2.0"),
            "pre": _power_run("pre", version="1.3.0.dev4"),
        },
    )

    assert _medians(db) == [
        ("1.2.0", True, 1, pytest.approx(BASE_SCORE), 0.0, True),
        ("1.3.0.dev4", False, 1, pytest.approx(BASE_SCORE), 0.0, False),
    ]


def test_a_newest_version_without_a_ranked_result_does_not_hide_an_older_ranked_one(tmp_path: Path) -> None:
    failed = _power_run("failed", version="1.3.0")
    failed["queries"].pop()
    db = _build(tmp_path, {"old": _power_run("old", version="1.2.0"), "new": failed})

    assert _medians(db) == [
        ("1.2.0", True, 1, pytest.approx(BASE_SCORE), 0.0, True),
        ("1.3.0", True, 0, None, None, False),
    ]


def test_a_partial_result_enters_both_the_power_and_the_throughput_cohort(tmp_path: Path) -> None:
    db = _build(tmp_path, {"partial": _partial_run("partial")})

    with duckdb.connect(str(db), read_only=True) as con:
        result_row = con.execute("SELECT corpus_class, corpus_lanes FROM results").fetchone()
        phases = con.execute("SELECT phase, rank FROM benchmark_rankings ORDER BY phase").fetchall()
        scores = con.execute("SELECT lane, score IS NOT NULL FROM result_lane_scores ORDER BY lane").fetchall()
        lanes_of_medians = con.execute("SELECT lane FROM identity_medians ORDER BY lane").fetchall()

    assert result_row is not None
    corpus_class, lanes = result_row
    assert corpus_class == corpus_rule.CLASS_PARTIAL
    assert json.loads(lanes) == [corpus_rule.LANE_POWER_ONLY, corpus_rule.LANE_THROUGHPUT]
    assert phases == [("power", 1), ("throughput", 1)]
    assert scores == [("power_only", True), ("throughput", True)]
    assert lanes_of_medians == [("power_only",), ("throughput",)]


def test_median_rows_carry_the_rank_the_pipeline_ranker_gives_their_scores(tmp_path: Path) -> None:
    powers = {"engine-a": 2000.0, "engine-b": 1000.0, "engine-c": 1000.0, "engine-d": 500.0}
    db = _build(tmp_path, {engine: _engine_run(engine, power) for engine, power in powers.items()})

    medians = _ranked_medians(db)
    scores = [score for _, _, score, *_ in medians]
    higher_is_better = get_ranking_config("tpch", "power").primary_order == "desc"

    assert [variant for variant, *_ in medians] == list(powers)
    assert [rank for *_, rank, _, _ in medians] == rank_scores(scores, higher_is_better=higher_is_better)
    assert [(rank, total, reason) for *_, rank, total, reason in medians] == [
        (1, 4, None),
        (2, 4, None),
        (2, 4, None),
        (4, 4, None),
    ]


def test_a_cohort_below_the_minimum_ranks_no_median_row(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        {
            "release": _engine_run("engine-a", 1000.0),
            "pre": _engine_run("engine-a", 1500.0, version="1.3.0.dev1"),
        },
    )

    assert MINIMUM_RANKED_COHORT == 2
    assert [(version, rank, total, reason) for _, version, _, rank, total, reason in _ranked_medians(db)] == [
        ("1.2.0", None, 1, "below_cohort_minimum"),
        ("1.3.0.dev1", None, 1, "not_leaderboard_version"),
    ]


def _unrecorded_run(engine: str, power: float, name: str) -> dict:
    data = scored_minimal_bundle(power)
    data["run"]["id"] = name
    data["platform"] = {"name": engine}
    data["execution"] = {}
    return data


def test_an_identity_with_no_recorded_version_is_ranked_on_its_unrecorded_version_median(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        {
            "a": _unrecorded_run("engine-a", 1000.0, "engine-a-unrecorded"),
            "b": _unrecorded_run("engine-b", 2000.0, "engine-b-unrecorded"),
        },
    )

    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute(
            "SELECT variant_id, comparison_version, is_leaderboard_version, rank, total_in_cohort, "
            "ranking_exclusion_reason FROM identity_medians ORDER BY variant_id"
        ).fetchall()

    assert [(variant, version, standing, total, reason) for variant, version, standing, _, total, reason in rows] == [
        ("engine-a", "version not recorded", True, 2, None),
        ("engine-b", "version not recorded", True, 2, None),
    ]
    assert sorted(rank for *_, rank, _, _ in rows) == [1, 2]


def test_an_unrecorded_version_median_gives_way_to_a_released_version_of_the_same_identity(tmp_path: Path) -> None:
    db = _build(
        tmp_path,
        {
            "a-unrecorded": _unrecorded_run("engine-a", 1500.0, "engine-a-unrecorded"),
            "a-release": _engine_run("engine-a", 1000.0),
            "b-release": _engine_run("engine-b", 2000.0),
        },
    )

    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute(
            "SELECT variant_id, comparison_version, is_leaderboard_version, rank, ranking_exclusion_reason "
            "FROM identity_medians ORDER BY variant_id, comparison_version"
        ).fetchall()

    assert [(variant, version, standing, reason) for variant, version, standing, _, reason in rows] == [
        ("engine-a", "1.2.0", True, None),
        ("engine-a", "version not recorded", False, "not_leaderboard_version"),
        ("engine-b", "1.2.0", True, None),
    ]
    assert [rank for _, version, _, rank, _ in rows if version == "1.2.0"] == [2, 1]


def test_run_rows_join_their_median_row_on_equal_keys(tmp_path: Path) -> None:
    runs = {
        "a1": _engine_run("engine-a", 2000.0),
        "a2": _engine_run("engine-a", 1800.0),
        "b1": _engine_run("engine-b", 1000.0),
        "a-pre": _engine_run("engine-a", 2500.0, version="1.3.0.dev1"),
    }
    runs["a2"]["run"]["id"] = "engine-a-second"
    db = _build(tmp_path, runs)

    with duckdb.connect(str(db), read_only=True) as con:
        joined = con.execute(
            "SELECT m.variant_id, m.comparison_version, m.run_count, count(r.result_id), m.rank "
            "FROM identity_medians m JOIN benchmark_rankings r "
            "USING (cohort_key, lane, variant_id, comparison_version) "
            "GROUP BY ALL ORDER BY m.variant_id, m.comparison_version"
        ).fetchall()
        unmatched = con.execute(
            "SELECT count(*) FROM benchmark_rankings r ANTI JOIN identity_medians m "
            "USING (cohort_key, lane, variant_id, comparison_version)"
        ).fetchone()

    assert joined == [
        ("engine-a", "1.2.0", 2, 2, 1),
        ("engine-a", "1.3.0.dev1", 1, 1, None),
        ("engine-b", "1.2.0", 1, 1, 2),
    ]
    assert unmatched == (0,)


def test_a_result_the_rule_cannot_place_is_not_published(tmp_path: Path) -> None:
    unknown = _power_run("unknown")
    unknown["benchmark"]["id"] = "not-a-registered-benchmark"
    bundles_dir = tmp_path / "data" / "bundles"
    bundles_dir.mkdir(parents=True)
    (bundles_dir / "known.json").write_text(json.dumps(_power_run("known")), encoding="utf-8")
    (bundles_dir / "unknown.json").write_text(json.dumps(unknown), encoding="utf-8")

    stats = ExplorerPipeline().run(tmp_path / "data", tmp_path / "out")

    assert (stats.processed, stats.skipped) == (1, 1)


def test_labels_and_identity_come_from_the_rule_modules(tmp_path: Path) -> None:
    single = _power_run("single")
    single["phases"] = {"power_test": {"status": "COMPLETED"}}
    db = _build(tmp_path, {"single": single})

    with duckdb.connect(str(db), read_only=True) as con:
        row = con.execute(
            "SELECT corpus_labels, comparison_identity, comparison_version, version_is_release, observed_engine, "
            "row_count_evidence_label, iteration_label FROM results"
        ).fetchone()

    assert row is not None
    labels, identity, version, is_release, observed, row_counts, iterations = row
    assert json.loads(labels) == [
        "Derived from TPC-H. These results do not comply with the TPC-H Specification "
        "and must not be compared with official TPC-H results.",
        corpus_rule.CLASS_WORDS[corpus_rule.CLASS_POWER_ONLY],
        corpus_rule.ROW_COUNTS_NOT_CHECKED_LABEL,
        corpus_rule.SINGLE_ITERATION_LABEL,
    ]
    assert (identity, version, is_release) == ("duckdb v1.2.0", "1.2.0", True)
    assert observed is None
    assert row_counts == corpus_rule.ROW_COUNTS_NOT_CHECKED_LABEL
    assert iterations == corpus_rule.SINGLE_ITERATION_LABEL


@pytest.mark.parametrize(
    ("older", "newer"),
    [("1.9.0", "1.10.0"), ("1.2.0", "1.2.1"), ("0.9", "1.0")],
)
def test_version_order_compares_numbers_not_text(older: str, newer: str) -> None:
    assert version_order(older) < version_order(newer)


FORBIDDEN_IN_THE_PIPELINE = (
    "RANKING_EXCLUDED_COMPLIANCE_CLASSES",
    "effective_compliance_class",
    "subscale_policy",
    "unofficial_nonstandard",
    "unofficial_subscale",
    "is_nonstandard_scale",
    "is_subscale_tpc",
)


def test_the_pipeline_holds_no_class_or_stamp_logic_of_its_own() -> None:
    offenders = {
        path.name: [token for token in FORBIDDEN_IN_THE_PIPELINE if re.search(rf"\b{token}\b", text)]
        for path in sorted(PIPELINE_DIR.glob("*.py"))
        if (text := path.read_text(encoding="utf-8"))
    }

    assert {name: tokens for name, tokens in offenders.items() if tokens} == {}


def test_the_pipeline_takes_class_and_identity_from_the_rule_modules() -> None:
    text = (PIPELINE_DIR / "corpus_outcome.py").read_text(encoding="utf-8")

    assert "corpus_rule.derive_outcome" in text
    assert "comparison_identity.comparison_identity" in text


@pytest.mark.parametrize(
    ("scale", "recorded", "expected"),
    [
        (0.01, "official", "unofficial_subscale"),
        (2.0, "official", "unofficial_nonstandard"),
        (1.0, "unofficial_nonstandard", None),
    ],
)
def test_the_compliance_class_column_follows_the_rule_not_the_stamp(
    tmp_path: Path, scale: float, recorded: str, expected: str | None
) -> None:
    data = _power_run("one")
    data["benchmark"]["scale_factor"] = scale
    data["benchmark"]["compliance_class"] = recorded
    db = _build(tmp_path, {"one": data})

    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT compliance_class FROM results").fetchone() == (expected,)


def _full_spec_run(stamp: str | None, validation: str) -> dict:
    data = combined_bundle("one")
    data["benchmark"]["compliance_class"] = stamp
    data["summary"]["validation"] = validation
    return data


@pytest.mark.parametrize(
    ("stamp", "validation", "expected"),
    [
        ("official", "passed", "official"),
        (None, "passed", None),
        ("unofficial_subscale", "passed", None),
        ("official", "failed", None),
    ],
)
def test_the_compliance_class_column_claims_official_only_where_the_writer_would(
    tmp_path: Path, stamp: str | None, validation: str, expected: str | None
) -> None:
    db = _build(tmp_path, {"one": _full_spec_run(stamp, validation)})

    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT corpus_class, compliance_class FROM results").fetchone() == ("full_spec", expected)
