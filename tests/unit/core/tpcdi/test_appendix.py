from __future__ import annotations

import hashlib
import tempfile
from datetime import date
from pathlib import Path

import duckdb
import pytest

from benchbox.core.tpcdi import appendix
from benchbox.core.tpcdi.appendix import (
    APPENDIX_FILES,
    load_all_appendix_sql,
    load_appendix_sql,
    stored_sha256,
)
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.utils.dialect_utils import (
    UnknownSqlDialectError,
    sql_translation_context,
    summarize_sql_translation_outcomes,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).parents[4]
SOURCES_DIR = REPO_ROOT / "_sources" / "tpc-di"

SHARED_COPY_SHA256 = {
    "audit": "32eb0f55a08b8cf5a906d4aff563539d42c873c72d6dc662798218b2bc3107fc",
    "validation": "9ee9ef8144c7f53bb6fb0d4ac489dda2379e7aa1b8e24d7a12df1539b7f86c02",
    "visibility_1": "209454549598d8147a2a0eb9ced8bd86eb3548f32fc90c1765975b0076e8bde7",
    "visibility_2": "a22645d5c9d2095d8d77549db3e7ecbc8d5d5417e8f0973fbd92150347ea894a",
}
EXPECTED_STATEMENTS = {"audit": 1, "validation": 2, "visibility_1": 1, "visibility_2": 1}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_stored_files_match_the_recorded_hashes():
    assert set(APPENDIX_FILES) == set(SHARED_COPY_SHA256)
    for name, expected in SHARED_COPY_SHA256.items():
        assert stored_sha256(name) == expected


def test_source_and_runtime_copies_are_identical():
    for filename in APPENDIX_FILES.values():
        assert digest(SOURCES_DIR / filename) == digest(appendix.APPENDIX_DIR / filename)


def test_loading_leaves_the_stored_files_untouched():
    before = {name: appendix.appendix_path(name).read_bytes() for name in APPENDIX_FILES}
    for platform in ("duckdb", "postgresql", "snowflake"):
        load_all_appendix_sql(platform)
    after = {name: appendix.appendix_path(name).read_bytes() for name in APPENDIX_FILES}
    assert before == after
    assert {name: stored_sha256(name) for name in APPENDIX_FILES} == SHARED_COPY_SHA256


def test_unknown_name_is_refused():
    with pytest.raises(ValueError, match="Unknown appendix SQL"):
        load_appendix_sql("missing")


@pytest.mark.parametrize("platform", ["duckdbb", "", "no-such-platform"])
def test_unknown_platform_is_refused(platform: str):
    with pytest.raises(UnknownSqlDialectError):
        load_appendix_sql("audit", platform)


def test_audit_load_records_the_placeholder_pre_step_and_dialects():
    loaded = load_appendix_sql("audit", "duckdb")
    raw = appendix.appendix_path("audit").read_text(encoding="utf-8")
    assert loaded.source_dialect == "postgres"
    assert loaded.target_dialect == "duckdb"
    assert loaded.stored_sha256 == SHARED_COPY_SHA256["audit"]
    assert len(loaded.pre_steps) == 1
    step = loaded.pre_steps[0]
    assert step.token == "FROM DUMMY TABLE"
    assert step.replacement == ""
    assert step.occurrences == raw.upper().count("FROM DUMMY TABLE") == 103
    assert "DUMMY TABLE" not in loaded.sql.upper()


def test_translation_is_recorded_by_the_standard_outcome_mechanism():
    with sql_translation_context(strict=True) as outcomes:
        load_appendix_sql("audit", "duckdb")
        load_appendix_sql("validation", "snowflake")
    summary = summarize_sql_translation_outcomes(outcomes)
    assert summary["status"] == "success"
    assert summary["translators"] == ["sqlglot"]
    assert summary["source_dialects"] == ["postgres"]
    assert summary["target_dialects"] == ["duckdb", "snowflake"]
    assert summary["attempt_count"] == 2


def test_platform_names_resolve_to_the_fork_dialect():
    assert load_appendix_sql("validation", "postgresql").target_dialect == "postgres"
    assert load_appendix_sql("validation", "snowflake").target_dialect == "snowflake"


def test_files_without_placeholders_record_no_pre_step():
    for name in ("validation", "visibility_1", "visibility_2"):
        assert load_appendix_sql(name, "duckdb").pre_steps == ()


def test_statement_counts_use_the_standard_splitter():
    for name, expected in EXPECTED_STATEMENTS.items():
        assert len(load_appendix_sql(name).statements()) == expected


def test_translated_date_expression_equals_a_one_day_subtraction_on_duckdb():
    connection = duckdb.connect(":memory:")
    try:
        translated = load_appendix_sql("audit", "duckdb").sql
        assert "Date - 1 AS day" in translated
        value = connection.execute("select DATE '2020-03-01' - 1 as day").fetchone()[0]
        interval = connection.execute("select DATE '2020-03-01' - INTERVAL 1 DAY as day").fetchone()[0]
    finally:
        connection.close()
    assert value == date(2020, 2, 29)
    assert interval.date() == value


@pytest.fixture(scope="module")
def schema_connection():
    adapter = DuckDBAdapter(database_path=":memory:")
    connection = adapter.create_connection()
    benchmark = TPCDIBenchmark(scale_factor=3, output_dir=tempfile.mkdtemp(prefix="tpcdi_appendix_bind_"))
    adapter.create_schema(benchmark, connection)
    yield connection
    connection.close()


@pytest.mark.parametrize("name", list(APPENDIX_FILES))
def test_every_statement_binds_against_the_adapter_created_schema_on_duckdb(schema_connection, name: str):
    loaded = load_appendix_sql(name, "duckdb")
    statements = loaded.statements()
    assert len(statements) == EXPECTED_STATEMENTS[name]
    for statement in statements:
        schema_connection.execute(statement)
