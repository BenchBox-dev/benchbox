from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

from benchbox.core.tpcdi import appendix
from benchbox.core.tpcdi.appendix import (
    APPENDIX_FILES,
    load_all_appendix_sql,
    load_appendix_sql,
    split_statements,
    stored_sha256,
)
from benchbox.core.tpcdi.schema import TPCDISchemaManager

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
    for platform in ("duckdb", "db2", "oracle", "sqlserver", "postgresql"):
        load_all_appendix_sql(platform)
    after = {name: appendix.appendix_path(name).read_bytes() for name in APPENDIX_FILES}
    assert before == after


def test_unknown_name_is_refused():
    with pytest.raises(ValueError, match="Unknown appendix SQL"):
        load_appendix_sql("missing")


def test_duckdb_records_dummy_table_and_date_duration_substitutions():
    loaded = load_appendix_sql("audit", "duckdb")
    raw = appendix.appendix_path("audit").read_text(encoding="utf-8")
    by_token = {item.token: item for item in loaded.substitutions}
    assert set(by_token) == {"FROM DUMMY TABLE", "Date-1 day"}
    assert by_token["FROM DUMMY TABLE"].replacement == ""
    assert by_token["FROM DUMMY TABLE"].occurrences == raw.upper().count("FROM DUMMY TABLE") == 103
    assert by_token["FROM DUMMY TABLE"].in_specification_table is True
    assert by_token["Date-1 day"].replacement == "Date - INTERVAL 1 DAY"
    assert by_token["Date-1 day"].occurrences == 1
    assert by_token["Date-1 day"].in_specification_table is False
    assert "DUMMY TABLE" not in loaded.sql.upper().replace("DUMMY_TABLE", "")
    assert loaded.stored_sha256 == SHARED_COPY_SHA256["audit"]


@pytest.mark.parametrize(
    ("platform", "clause"),
    [("db2", "from sysibm.sysdummy1"), ("oracle", "from dual"), ("sqlserver", "")],
)
def test_dummy_table_clause_per_platform(platform: str, clause: str):
    loaded = load_appendix_sql("audit", platform)
    dummy = next(item for item in loaded.substitutions if item.token == "FROM DUMMY TABLE")
    assert dummy.replacement == clause
    assert dummy.occurrences == 103
    assert "Date-1 day" in loaded.sql


def test_sqlserver_concatenation_is_recorded_and_header_comment_is_kept():
    loaded = load_appendix_sql("audit", "sqlserver")
    concat = next(item for item in loaded.substitutions if item.token == "||")
    raw = appendix.appendix_path("audit").read_text(encoding="utf-8")
    assert concat.replacement == "+"
    assert concat.occurrences == raw.count("||") - 1
    assert "[||] (String concatenation)" in loaded.sql
    assert loaded.sql.count("||") == 1


def test_files_without_dummy_table_record_no_substitution():
    for name in ("validation", "visibility_1", "visibility_2"):
        assert load_appendix_sql(name, "duckdb").substitutions == ()


def test_statement_counts():
    for name, expected in EXPECTED_STATEMENTS.items():
        assert len(load_appendix_sql(name).statements()) == expected


def test_split_statements_ignores_semicolons_in_quotes_and_comments():
    line_comment = "-" * 2 + " c; d\n"
    block_comment = "/" + "* e; f *" + "/"
    sql = f"select 'a;b' as x; {line_comment}{block_comment} select 2;\n{block_comment}\n"
    assert split_statements(sql) == ["select 'a;b' as x", f"{line_comment}{block_comment} select 2"]


@pytest.fixture(scope="module")
def schema_connection():
    connection = duckdb.connect(":memory:")
    TPCDISchemaManager().create_schema(connection, "duckdb")
    yield connection
    connection.close()


@pytest.mark.parametrize("name", list(APPENDIX_FILES))
def test_every_statement_binds_against_the_specification_schema_on_duckdb(schema_connection, name: str):
    loaded = load_appendix_sql(name, "duckdb")
    statements = loaded.statements()
    assert len(statements) == EXPECTED_STATEMENTS[name]
    for statement in statements:
        schema_connection.execute(statement)
    assert all(item.token in {"FROM DUMMY TABLE", "Date-1 day"} for item in loaded.substitutions)
