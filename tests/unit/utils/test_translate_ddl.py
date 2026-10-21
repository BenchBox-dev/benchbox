import pytest

from benchbox.utils.dialect_utils import translate_ddl

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TABLE = "CREATE TABLE t (a INT NOT NULL, b VARCHAR(10), PRIMARY KEY (a))"


def test_returns_one_statement_per_input_statement():
    statements = translate_ddl(f"{TABLE}; DROP TABLE IF EXISTS t", "duckdb")
    assert len(statements) == 2
    assert statements[1].startswith("DROP TABLE IF EXISTS")


def test_dialect_names_are_normalised_and_identifiers_are_quoted_where_the_wrapper_quotes():
    assert translate_ddl("CREATE TABLE t (a INT)", "duckdb") == ['CREATE TABLE "t" ("a" INT)']
    assert translate_ddl("CREATE TABLE t (a INT)", "postgres") == ["CREATE TABLE t (a INT)"]
    assert translate_ddl("CREATE TABLE t (a INT)", "greenplum") == ["CREATE TABLE t (a INT)"]
    assert translate_ddl("CREATE TABLE t (a INT)", "duckdb", identify=False) == ["CREATE TABLE t (a INT)"]


def test_options_are_applied():
    [statement] = translate_ddl(TABLE, "postgres", strip_primary_keys=True, or_replace=True)
    assert "PRIMARY KEY" not in statement
    assert statement.startswith("CREATE OR REPLACE TABLE t")


def test_layout_mapping_becomes_a_table_layout():
    [statement] = translate_ddl(
        TABLE, "clickhouse", source_dialect="duckdb", default_layout={"engine": "MergeTree()", "nullable_columns": []}
    )
    assert "ENGINE=MergeTree" in statement.replace(" ", "")


def test_layouts_by_table_and_singlestore_mappings_are_converted():
    [statement] = translate_ddl(
        TABLE, "starrocks", source_dialect="duckdb", layouts={"T": {"key_model": "duplicate", "distribution": "hash"}}
    )
    assert "DUPLICATE KEY" in statement
    [statement] = translate_ddl(TABLE, "singlestore", source_dialect="mysql", singlestore={"shard_keys": {"t": "a"}})
    assert "SHARD KEY" in statement


def test_unknown_option_is_rejected():
    with pytest.raises(ValueError, match="Unknown DDL options"):
        translate_ddl(TABLE, "postgres", strip_everything=True)
