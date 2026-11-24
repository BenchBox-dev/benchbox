from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.explorer_pipeline.medians import IdentityMedian, rank_leaderboard_versions
from _project.scripts.explorer_pipeline.models import get_ranking_config
from benchbox.core.results import corpus_rule

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "explorer_read_model" / "next_read_model_contract.json"
CHECKER = REPO_ROOT / "scripts" / "publication" / "check_explorer_compat.py"
NEXT_VERSION = 21
REQUIRED_RESULT_FIELDS = (
    "corpus_class",
    "corpus_lanes",
    "corpus_labels",
    "comparison_identity",
    "comparison_version",
    "version_is_release",
    "observed_engine",
    "row_count_evidence_label",
    "iteration_label",
)
REQUIRED_MEDIAN_FIELDS = ("run_count", "median_score", "spread", "rank", "total_in_cohort", "ranking_exclusion_reason")
REQUIRED_RANKING_FIELDS = ("cohort_key", "lane", "comparison_version")
DEFAULTS_BY_TYPE = {"VARCHAR": "placeholder", "DOUBLE": 0.0, "INTEGER": 0, "BOOLEAN": False, "BIGINT": 0}


def _load_checker() -> Any:
    spec = importlib.util.spec_from_file_location("check_explorer_compat", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def checker() -> Any:
    return _load_checker()


def _column_definitions(contract: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    tables = contract["tables"]
    return {
        "results": tables["results"]["added_columns"],
        "benchmark_rankings": tables["benchmark_rankings"]["added_columns"],
        "result_lane_scores": tables["result_lane_scores"]["columns"],
        "identity_medians": tables["identity_medians"]["columns"],
    }


def _ddl(table: str, columns: list[dict[str, Any]], primary_key: list[str] | None) -> str:
    parts = [f"{column['name']} {column['type']}{'' if column['nullable'] else ' NOT NULL'}" for column in columns]
    if primary_key:
        parts.append(f"PRIMARY KEY ({', '.join(primary_key)})")
    return f"CREATE TABLE {table} ({', '.join(parts)})"


def _schema_at_next_version(checker: Any, contract: dict[str, Any]) -> Any:
    base = contract["extends_version"]
    con = checker.create_in_memory_schema(base)
    definitions = _column_definitions(contract)
    for table in ("results", "benchmark_rankings"):
        for column in definitions[table]:
            default = " DEFAULT ''" if column["type"] == "VARCHAR" else " DEFAULT false"
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN {column['name']} {column['type']}"
                f"{'' if column['nullable'] else default}"
            )
    for table in ("result_lane_scores", "identity_medians"):
        con.execute(_ddl(table, definitions[table], contract["tables"][table].get("primary_key")))
    return con


def _insert_with_defaults(con: Any, table: str, row: dict[str, Any]) -> None:
    required = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ? AND is_nullable = 'NO' AND column_default IS NULL ORDER BY ordinal_position",
        [table],
    ).fetchall()
    values = {name: DEFAULTS_BY_TYPE[kind] for name, kind in required} | row
    con.execute(
        f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", list(values.values())
    )


def test_fixture_declares_the_version_after_the_one_the_pipeline_emits(contract, checker) -> None:
    assert contract["read_model_version"] == NEXT_VERSION
    assert contract["extends_version"] < NEXT_VERSION
    assert contract["extends_version"] in checker.SUPPORTED_SCHEMA_VERSIONS


def test_fixture_carries_every_field_the_site_and_pipeline_agree_on(contract) -> None:
    definitions = _column_definitions(contract)

    assert tuple(column["name"] for column in definitions["results"]) == REQUIRED_RESULT_FIELDS
    assert {column["name"] for column in definitions["identity_medians"]} >= set(REQUIRED_MEDIAN_FIELDS)
    assert tuple(column["name"] for column in definitions["benchmark_rankings"]) == REQUIRED_RANKING_FIELDS
    assert {column["name"] for column in definitions["result_lane_scores"]} == {
        "result_id",
        "lane",
        "score",
        "score_source",
    }


def test_the_new_columns_only_add_to_the_current_schema(contract, checker) -> None:
    existing = checker.get_table_columns_for_version(contract["extends_version"])
    definitions = _column_definitions(contract)

    assert not {column["name"] for column in definitions["results"]} & set(existing["results"])
    assert not {column["name"] for column in definitions["benchmark_rankings"]} & set(existing["benchmark_rankings"])
    assert (set(definitions) - {"results", "benchmark_rankings"}) & set(existing) == set()


def _constants(prefix: str) -> set[str]:
    return {value for name, value in vars(corpus_rule).items() if name.startswith(prefix) and isinstance(value, str)}


def test_class_and_lane_vocabularies_are_the_rule_modules(contract) -> None:
    by_name = {column["name"]: column for column in contract["tables"]["results"]["added_columns"]}

    assert set(by_name["corpus_class"]["allowed_values"]) == _constants("CLASS_") - {corpus_rule.CLASS_UNDETERMINED}
    assert set(by_name["corpus_lanes"]["allowed_values"]) == _constants("LANE_")


def test_the_view_exposes_only_columns_the_table_gains(contract) -> None:
    added = {column["name"] for column in contract["tables"]["results"]["added_columns"]}

    assert set(contract["views"]["result_detail_metrics"]) == added


def test_fixture_rows_load_into_the_schema_at_the_next_version(contract, checker) -> None:
    con = _schema_at_next_version(checker, contract)
    assert checker.validate_database_schema(con, contract["extends_version"]) == []
    results = contract["rows"]["results"]
    for row in results:
        _insert_with_defaults(con, "results", row)
    for row in contract["rows"]["benchmark_rankings"]:
        _insert_with_defaults(con, "benchmark_rankings", row)
    for table in ("result_lane_scores", "identity_medians"):
        for row in contract["rows"][table]:
            con.execute(
                f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values())
            )
    joined = con.execute(
        "SELECT r.result_id, m.rank, m.ranking_exclusion_reason FROM benchmark_rankings r JOIN identity_medians m "
        "USING (cohort_key, lane, variant_id, comparison_version) ORDER BY r.result_id"
    ).fetchall()
    assert joined == [("example-pre-release", None, "not_leaderboard_version"), ("example-ranked-release", 1, None)]

    stored = con.execute(f"SELECT {', '.join(REQUIRED_RESULT_FIELDS)} FROM results ORDER BY result_id").fetchall()
    expected = [
        tuple(row[name] for name in REQUIRED_RESULT_FIELDS) for row in sorted(results, key=lambda r: r["result_id"])
    ]
    assert stored == expected
    con.execute("UPDATE metadata SET read_model_version = ?", [contract["read_model_version"]])
    assert con.execute("SELECT read_model_version FROM metadata").fetchone() == (NEXT_VERSION,)


def test_fixture_rows_obey_the_declared_vocabularies(contract) -> None:
    allowed = {
        column["name"]: set(column["allowed_values"])
        for column in contract["tables"]["results"]["added_columns"]
        if "allowed_values" in column
    }
    score_sources = _constants("SCORE_")
    lanes = allowed["corpus_lanes"]

    for row in contract["rows"]["results"]:
        assert row["corpus_class"] in allowed["corpus_class"]
        assert set(json.loads(row["corpus_lanes"])) <= lanes
        assert isinstance(json.loads(row["corpus_labels"]), list)
        assert row["comparison_version"] in row["comparison_identity"] or row["comparison_version"] == (
            "version not recorded"
        )
        if row["comparison_version"] == "version not recorded":
            assert row["version_is_release"] is False
    for row in contract["rows"]["result_lane_scores"]:
        assert row["lane"] in lanes
        assert row["score_source"] in score_sources


def test_fixture_medians_pick_one_release_per_identity(contract) -> None:
    medians = contract["rows"]["identity_medians"]

    standing = [row for row in medians if row["is_leaderboard_version"]]
    assert standing and all(row["version_is_release"] for row in standing)
    assert any(not row["version_is_release"] and not row["is_leaderboard_version"] for row in medians)
    assert all(row["run_count"] >= 1 and (row["run_count"] > 1 or row["spread"] == 0) for row in medians)


def test_fixture_median_ranks_are_the_ones_the_pipeline_ranker_gives(contract) -> None:
    rows = contract["rows"]["identity_medians"]
    benchmark, _, phase = rows[0]["cohort_key"].split("/")
    assert all(row["cohort_key"] == rows[0]["cohort_key"] for row in rows)
    medians = [
        IdentityMedian(
            key=(benchmark, 1.0, phase, None),
            **{name: row[name] for name in row if name not in {"rank", "total_in_cohort", "ranking_exclusion_reason"}},
        )
        for row in rows
    ]

    ranked = rank_leaderboard_versions(
        medians, higher_is_better=get_ranking_config(benchmark, phase).primary_order == "desc"
    )

    assert [(m.rank, m.total_in_cohort, m.ranking_exclusion_reason) for m in ranked] == [
        (row["rank"], row["total_in_cohort"], row["ranking_exclusion_reason"]) for row in rows
    ]


def test_every_declared_column_type_is_a_type_the_checker_normalises(contract, checker) -> None:
    for columns in _column_definitions(contract).values():
        assert {column["type"] for column in columns} <= {"VARCHAR", "DOUBLE", "INTEGER", "BOOLEAN"}
        assert all(checker.normalize_type(column["type"]) == column["type"] for column in columns)
