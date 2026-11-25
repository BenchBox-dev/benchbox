import logging
import sqlite3
from typing import Any, cast

import pytest

import benchbox.utils.dialect_utils as dialect_utils
from benchbox.utils.dialect_utils import (
    SqliteVersionUnsupportedError,
    SQLTranslationError,
    UnknownSqlDialectError,
    apply_rewrite,
    apply_rewrites,
    resolve_sql_dialect,
    rewrite_sql_tree,
    sql_translation_context,
    sqlite_engine_version as real_sqlite_engine_version,
    summarize_sql_translation_outcomes,
    translate_sql_query,
)
from tests.sqlite_engine_pin import pinned_sqlite_engine_version

pytestmark = [pytest.mark.unit, pytest.mark.medium]

MONTH_OFFSET = "SELECT DATE '2024-01-31' + INTERVAL '1' MONTH"
SAFE_MONTH_OFFSET = "SELECT DATE '2024-01-15' + INTERVAL '1' MONTH"
UNSAFE_YEAR_OFFSET = "SELECT DATE '2024-02-29' + INTERVAL '1' YEAR"
COLUMN_MONTH_OFFSET = "SELECT CAST(d AS DATE) + INTERVAL '1' MONTH FROM t"
BELOW = "3.45.3"
AT_LEAST = "3.46.0"


def test_engine_version_reads_the_standard_library(monkeypatch):
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.9.9")
    assert real_sqlite_engine_version() == "3.9.9"


def test_generation_dialect_carries_the_parsed_version():
    assert dialect_utils._sqlite_generation_dialect("3.45.1").version == (3, 45, 1)


@pytest.mark.parametrize("version", [BELOW, AT_LEAST])
def test_platform_and_dialect_names_stay_plain_sqlite(version):
    with pinned_sqlite_engine_version(version), sql_translation_context(strict=True) as outcomes:
        assert resolve_sql_dialect("sqlite") == "sqlite"
        translate_sql_query("SELECT 1", "sqlite", source_dialect="sqlite")
    assert outcomes[0].source_dialect == outcomes[0].target_dialect == "sqlite"
    assert outcomes[0].normalized_target_dialect == "sqlite"
    assert outcomes[0].sqlite_version == version


def test_versioned_name_is_not_a_dialect():
    with pytest.raises(UnknownSqlDialectError):
        resolve_sql_dialect(f"sqlite, version={AT_LEAST}")


def test_platform_manifest_dialect_for_sqlite_is_plain():
    from benchbox.core.platform_manifest import get_platform_sql_dialect

    with pinned_sqlite_engine_version(BELOW):
        assert get_platform_sql_dialect("sqlite") == "sqlite"


def test_outcome_records_version_only_for_sqlite():
    with pinned_sqlite_engine_version(AT_LEAST), sql_translation_context() as outcomes:
        translate_sql_query("SELECT 1", "sqlite", source_dialect="postgres")
        translate_sql_query("SELECT 1", "duckdb", source_dialect="postgres")
    assert outcomes[0].to_dict()["sqlite_version"] == AT_LEAST
    assert "sqlite_version" not in outcomes[1].to_dict()
    summary = cast(dict[str, Any], summarize_sql_translation_outcomes(outcomes))
    assert {entry.get("sqlite_version") for entry in summary["outcomes"]} == {AT_LEAST, None}


def test_rewrite_outcome_records_version():
    with pinned_sqlite_engine_version(AT_LEAST), sql_translation_context() as outcomes:
        apply_rewrite("fold_between_literal_arithmetic", "SELECT 1 WHERE x BETWEEN 0.06 - 0.01 AND 0.07", "sqlite")
    assert outcomes[-1].translator == "sqlglot_rewrite"
    assert outcomes[-1].sqlite_version == AT_LEAST


class TestMonthEndClampingBelowThreshold:
    def test_strict_mode_raises(self):
        with pinned_sqlite_engine_version(BELOW), sql_translation_context(strict=True) as outcomes:
            with pytest.raises(SQLTranslationError) as raised:
                translate_sql_query(MONTH_OFFSET, "sqlite", source_dialect="postgres", strict=True)
        assert "SQLite 3.46" in str(raised.value)
        assert outcomes[0].status == "failed"
        assert outcomes[0].error_category == "sqlite_version_unsupported"
        assert outcomes[0].sqlite_version == BELOW

    def test_non_strict_mode_never_returns_the_source_sql(self):
        with pinned_sqlite_engine_version(BELOW), sql_translation_context(strict=False) as outcomes:
            with pytest.raises(SQLTranslationError) as raised:
                translate_sql_query(MONTH_OFFSET, "sqlite", source_dialect="postgres", strict=False)
        assert raised.value.outcome.status == "failed"
        assert raised.value.outcome.error_category == "sqlite_version_unsupported"
        assert [outcome.status for outcome in outcomes] == ["failed"]
        assert summarize_sql_translation_outcomes(outcomes)["status"] == "failed"

    def test_at_or_above_the_threshold_translates(self):
        with pinned_sqlite_engine_version(AT_LEAST):
            sql = translate_sql_query(MONTH_OFFSET, "sqlite", source_dialect="postgres", strict=True)
        assert "'floor'" in sql

    @pytest.mark.parametrize("query", [SAFE_MONTH_OFFSET, "SELECT DATE '1994-01-01' + INTERVAL '1' YEAR"])
    @pytest.mark.parametrize("strict", [True, False])
    def test_safe_literal_offsets_translate_without_floor(self, query, strict):
        with pinned_sqlite_engine_version(BELOW):
            sql = translate_sql_query(query, "sqlite", source_dialect="postgres", strict=strict)
        assert "floor" not in sql.lower()
        row = sqlite3.connect(":memory:").execute(sql).fetchone()
        assert row[0] in {"2024-02-15", "1995-01-01"}

    @pytest.mark.parametrize(
        "query", [UNSAFE_YEAR_OFFSET, COLUMN_MONTH_OFFSET, "SELECT DATE '2024-01-31' + INTERVAL '3' MONTH"]
    )
    def test_every_unsafe_unit_raises(self, query):
        with pinned_sqlite_engine_version(BELOW), pytest.raises(SQLTranslationError):
            translate_sql_query(query, "sqlite", source_dialect="postgres", strict=True)

    def test_other_targets_are_unaffected(self):
        with pinned_sqlite_engine_version(BELOW):
            assert translate_sql_query(MONTH_OFFSET, "duckdb", source_dialect="postgres", strict=True)

    @pytest.mark.parametrize("strict", [True, False])
    def test_rewrite_generation_raises(self, strict):
        with pinned_sqlite_engine_version(BELOW), sql_translation_context(strict=strict) as outcomes:
            with pytest.raises(SqliteVersionUnsupportedError):
                apply_rewrites(MONTH_OFFSET, "sqlite", [("fold_between_literal_arithmetic", {})], keep_unchanged=False)
        assert outcomes[-1].status == "failed"
        assert outcomes[-1].error_category == "sqlite_version_unsupported"
        assert outcomes[-1].sqlite_version == BELOW

    @pytest.mark.parametrize("strict", [True, False])
    def test_tree_rewrite_raises_instead_of_returning_the_source(self, strict):
        with pinned_sqlite_engine_version(BELOW), sql_translation_context(strict=strict):
            with pytest.raises(SqliteVersionUnsupportedError):
                rewrite_sql_tree(MONTH_OFFSET, "sqlite", lambda tree: tree.limit(1))


@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("version", [BELOW, AT_LEAST])
def test_other_unsupported_messages_keep_warning_behaviour(version, strict, caplog):
    query = "SELECT BIT_AND(x) FROM t"
    with pinned_sqlite_engine_version(version), caplog.at_level(logging.WARNING):
        sql = translate_sql_query(query, "sqlite", source_dialect="postgres", strict=strict)
    assert "BIT_AND" in sql.upper() or "BITWISE" in sql.upper()
    assert "not supported in SQLite" in caplog.text


def _unversioned_sqlite(monkeypatch):
    from benchbox._vendor.sqlglot.dialects.dialect import Dialect

    monkeypatch.setattr(dialect_utils, "_sqlite_generation_dialect", lambda version: Dialect.get_or_raise("sqlite"))


def _tpch_queries():
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    return TPCHBenchmark(scale_factor=0.01).get_queries(dialect="sqlite")


def _tpcds_queries():
    from benchbox.core.tpcds.benchmark import TPCDSBenchmark

    return TPCDSBenchmark(scale_factor=1.0).get_queries(dialect="sqlite")


def _ddl(module: str) -> str:
    import importlib

    base = importlib.import_module(module).get_create_all_tables_sql(enable_primary_keys=True, enable_foreign_keys=True)
    return translate_sql_query(base, "sqlite", source_dialect="standard", identify=False)


@pytest.mark.parametrize("queries", [_tpch_queries, _tpcds_queries], ids=["tpch", "tpcds"])
def test_threshold_or_newer_matches_unversioned_query_sets(queries, monkeypatch):
    with pinned_sqlite_engine_version(AT_LEAST):
        versioned = queries()
    _unversioned_sqlite(monkeypatch)
    unversioned = queries()
    assert versioned.keys() == unversioned.keys()
    assert versioned == unversioned


@pytest.mark.parametrize("module", ["benchbox.core.tpch.schema", "benchbox.core.tpcds.schema"])
def test_threshold_or_newer_matches_unversioned_ddl(module, monkeypatch):
    with pinned_sqlite_engine_version(AT_LEAST):
        versioned = _ddl(module)
    _unversioned_sqlite(monkeypatch)
    assert versioned == _ddl(module)
