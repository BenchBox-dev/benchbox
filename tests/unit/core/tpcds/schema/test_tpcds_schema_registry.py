from __future__ import annotations

import pytest

import benchbox.core.tpcds.schema.registry as registry
from benchbox.core.tpcds.schema.registry import (
    TABLES,
    TABLES_BY_NAME,
    get_create_all_tables_sql,
    get_tunings,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_get_table_returns_expected_table_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "TABLES_BY_NAME", {"STORE_SALES": TABLES_BY_NAME["store_sales"]})
    table = registry.get_table("store_sales")
    assert table.name == "store_sales"


def test_get_table_raises_for_unknown_table() -> None:
    with pytest.raises(ValueError, match="Invalid table name"):
        registry.get_table("not_a_table")


def test_create_all_tables_sql_contains_multiple_known_tables() -> None:
    ddl = get_create_all_tables_sql(enable_primary_keys=False, enable_foreign_keys=False)
    assert "CREATE TABLE store_sales" in ddl
    assert "CREATE TABLE date_dim" in ddl
    assert ddl.count("CREATE TABLE") == len(TABLES)


def test_get_tunings_includes_expected_fact_and_dimension_tables() -> None:
    tunings = get_tunings()
    names = set(tunings.table_tunings.keys())
    assert "STORE_SALES" in names
    assert "WEB_SALES" in names
    assert "CATALOG_SALES" in names
    assert "DATE_DIM" in names


