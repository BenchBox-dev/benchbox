from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

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
REQUIRED_MEDIAN_FIELDS = ("run_count", "median_score", "spread")
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
    for column in definitions["results"]:
        nullable = "" if column["nullable"] else " DEFAULT ''" if column["type"] == "VARCHAR" else " DEFAULT false"
        con.execute(f"ALTER TABLE results ADD COLUMN {column['name']} {column['type']}{nullable}")
    for table in ("result_lane_scores", "identity_medians"):
        con.execute(_ddl(table, definitions[table], contract["tables"][table].get("primary_key")))
    return con


def _insert_base_result(con: Any, result_id: str) -> None:
    required = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'results' AND is_nullable = 'NO' AND column_default IS NULL ORDER BY ordinal_position"
    ).fetchall()
    values = {name: result_id if name == "result_id" else DEFAULTS_BY_TYPE[kind] for name, kind in required}
    con.execute(
        f"INSERT INTO results ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", list(values.values())
    )


def test_fixture_declares_the_version_after_the_one_the_pipeline_emits(contract, checker) -> None:
    assert contract["read_model_version"] == NEXT_VERSION
    assert contract["extends_version"] < NEXT_VERSION
    assert contract["extends_version"] in checker.SUPPORTED_SCHEMA_VERSIONS


def test_fixture_carries_every_field_the_site_and_pipeline_agree_on(contract) -> None:
    definitions = _column_definitions(contract)

    assert tuple(column["name"] for column in definitions["results"]) == REQUIRED_RESULT_FIELDS
    assert {column["name"] for column in definitions["identity_medians"]} >= set(REQUIRED_MEDIAN_FIELDS)
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
    assert (set(definitions) - {"results"}) & set(existing) == set()


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
        _insert_base_result(con, row["result_id"])
        assignments = [name for name in row if name != "result_id"]
        con.execute(
            f"UPDATE results SET {', '.join(f'{name} = ?' for name in assignments)} WHERE result_id = ?",
            [row[name] for name in assignments] + [row["result_id"]],
        )
    for table in ("result_lane_scores", "identity_medians"):
        for row in contract["rows"][table]:
            con.execute(
                f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values())
            )

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


def test_every_declared_column_type_is_a_type_the_checker_normalises(contract, checker) -> None:
    for columns in _column_definitions(contract).values():
        assert {column["type"] for column in columns} <= {"VARCHAR", "DOUBLE", "INTEGER", "BOOLEAN"}
        assert all(checker.normalize_type(column["type"]) == column["type"] for column in columns)
