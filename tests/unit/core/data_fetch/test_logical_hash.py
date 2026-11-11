from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

from benchbox.core.data_fetch.logical_hash import (
    LOGICAL_CONTENT_VERSION,
    LogicalColumn,
    LogicalTableHash,
    aggregate_logical_content_hash,
    canonical_logical_value,
    is_integer_postgres_type,
    logical_columns_from_schema,
    logical_table_hash_from_parquet,
    quote_ident,
    update_logical_row_hash,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[4]
TINY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "joinorder_canonical_tiny"


_GOLDEN = {
    "kind_type": (7, "8fbf00d7899a61594d08ac49ec54842f8bad6d996e91ae4f1d17cbb1f9da9649"),
    "company_type": (4, "02fb1bd2ed8cc9e2bb3771e202f3323aedca306b7fbdf8129e383aba1b56f57e"),
    "comp_cast_type": (4, "a21f33b0ac7cda4af4ec3ac5623b530659248e031ee71216c039458018e45627"),
    "role_type": (12, "60de7318954c4b7b8847e92bbd8882e051cdc208c052451863a000dc5f0093fe"),
}


_LOOKUP_SCHEMAS = {
    "kind_type": {"id": "integer", "kind": "character varying"},
    "company_type": {"id": "integer", "kind": "character varying"},
    "comp_cast_type": {"id": "integer", "kind": "character varying"},
    "role_type": {"id": "integer", "role": "character varying"},
}


@pytest.mark.parametrize("table", sorted(_GOLDEN))
def test_golden_logical_hash_from_tiny_fixture(table: str) -> None:
    expected_rows, expected_sha = _GOLDEN[table]
    con = duckdb.connect()
    result = logical_table_hash_from_parquet(
        con=con,
        parquet_path=TINY_FIXTURE / f"{table}.parquet",
        table=table,
        columns=logical_columns_from_schema(_LOOKUP_SCHEMAS[table]),
    )
    assert result == LogicalTableHash(table=table, row_count=expected_rows, sha256=expected_sha)


def test_logical_hash_is_deterministic() -> None:
    con = duckdb.connect()
    cols = logical_columns_from_schema(_LOOKUP_SCHEMAS["kind_type"])
    a = logical_table_hash_from_parquet(
        con=con, parquet_path=TINY_FIXTURE / "kind_type.parquet", table="kind_type", columns=cols
    )
    b = logical_table_hash_from_parquet(
        con=con, parquet_path=TINY_FIXTURE / "kind_type.parquet", table="kind_type", columns=cols
    )
    assert a == b


def test_integer_column_normalises_across_encodings() -> None:
    col = LogicalColumn(name="id", is_integer=True)
    assert canonical_logical_value(123, col) == canonical_logical_value("123", col)
    assert canonical_logical_value(0, col) == ("I", b"0")
    assert canonical_logical_value(None, col) == ("N", b"")


def test_string_column_preserves_value() -> None:
    col = LogicalColumn(name="kind", is_integer=False)
    assert canonical_logical_value("movie", col) == ("S", b"movie")
    assert canonical_logical_value("", col) == ("S", b"")
    assert canonical_logical_value(None, col) == ("N", b"")


def test_row_hash_detects_column_name_and_value_changes() -> None:
    cols = [LogicalColumn("id", True), LogicalColumn("kind", False)]
    base = hashlib.sha256()
    update_logical_row_hash(base, cols, [1, "movie"])

    changed_value = hashlib.sha256()
    update_logical_row_hash(changed_value, cols, [1, "series"])
    assert base.hexdigest() != changed_value.hexdigest()

    null_row = hashlib.sha256()
    update_logical_row_hash(null_row, cols, [1, None])
    empty_row = hashlib.sha256()
    update_logical_row_hash(empty_row, cols, [1, ""])
    assert null_row.hexdigest() != empty_row.hexdigest()


def test_row_hash_rejects_arity_mismatch() -> None:
    cols = [LogicalColumn("id", True), LogicalColumn("kind", False)]
    with pytest.raises(ValueError, match="values for"):
        update_logical_row_hash(hashlib.sha256(), cols, [1])


def test_aggregate_is_order_independent_but_content_sensitive() -> None:
    a = LogicalTableHash("aka_name", 10, "aa")
    b = LogicalTableHash("title", 20, "bb")
    assert aggregate_logical_content_hash([a, b]) == aggregate_logical_content_hash([b, a])

    b2 = LogicalTableHash("title", 20, "cc")
    assert aggregate_logical_content_hash([a, b]) != aggregate_logical_content_hash([a, b2])

    b3 = LogicalTableHash("title", 21, "bb")
    assert aggregate_logical_content_hash([a, b]) != aggregate_logical_content_hash([a, b3])


def test_is_integer_postgres_type() -> None:
    assert is_integer_postgres_type("integer")
    assert is_integer_postgres_type("BIGINT")
    assert is_integer_postgres_type(" smallint ")
    assert not is_integer_postgres_type("character varying")
    assert not is_integer_postgres_type("text")


def test_logical_columns_from_schema_preserves_order_and_types() -> None:
    cols = logical_columns_from_schema({"id": "integer", "name": "character varying"})
    assert cols == [LogicalColumn("id", True), LogicalColumn("name", False)]


def test_quote_ident_rejects_injection() -> None:
    assert quote_ident("id") == '"id"'
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        quote_ident("id; DROP TABLE x")


def test_version_tag_is_stable() -> None:
    assert LOGICAL_CONTENT_VERSION == "joinorder-logical-content-v1"


