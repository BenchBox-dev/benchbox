from __future__ import annotations

import io
import re
from types import SimpleNamespace

import pytest

from benchbox.core.write_primitives.catalog import loader as wp_loader
from benchbox.core.write_primitives.catalog.loader import (
    WritePrimitivesCatalogError,
    load_write_primitives_catalog,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _patch_catalog(monkeypatch: pytest.MonkeyPatch, yaml_text: str) -> None:
    class DummyResource:
        def open(self, mode: str = "r", encoding: str | None = None):
            return io.StringIO(yaml_text)

    monkeypatch.setattr(
        wp_loader,
        "resources",
        SimpleNamespace(files=lambda package: SimpleNamespace(joinpath=lambda filename: DummyResource())),
    )


def test_loader_accepts_expected_value_min_and_max(monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_text = """
version: 1
operations:
  - id: sketch_query_demo
    description: demo sketch query op
    write_sql: "SELECT 1;"
    validation_queries:
      - id: distinct_estimate_in_range
        sql: "SELECT 1.0;"
        expected_value_min: 100
        expected_value_max: 200
"""
    _patch_catalog(monkeypatch, yaml_text)
    catalog = load_write_primitives_catalog()
    op = catalog.operations["sketch_query_demo"]
    val = op.validation_queries[0]
    assert val.expected_value_min == pytest.approx(100.0)
    assert val.expected_value_max == pytest.approx(200.0)


def test_loader_rejects_expected_value_min_without_max(monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_text = """
version: 1
operations:
  - id: bad_op
    description: missing max bound
    write_sql: "SELECT 1;"
    validation_queries:
      - id: bad
        sql: "SELECT 1.0;"
        expected_value_min: 100
"""
    _patch_catalog(monkeypatch, yaml_text)
    with pytest.raises(WritePrimitivesCatalogError, match="must set both"):
        load_write_primitives_catalog()


def test_loader_rejects_min_greater_than_max(monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_text = """
version: 1
operations:
  - id: bad_op
    description: inverted bounds
    write_sql: "SELECT 1;"
    validation_queries:
      - id: bad
        sql: "SELECT 1.0;"
        expected_value_min: 200
        expected_value_max: 100
"""
    _patch_catalog(monkeypatch, yaml_text)
    with pytest.raises(WritePrimitivesCatalogError, match="must be <= expected_value_max"):
        load_write_primitives_catalog()


def test_loader_rejects_combination_with_expected_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_text = """
version: 1
operations:
  - id: bad_op
    description: mixed validation kinds
    write_sql: "SELECT 1;"
    validation_queries:
      - id: bad
        sql: "SELECT 1.0;"
        expected_rows: 1
        expected_value_min: 0
        expected_value_max: 10
"""
    _patch_catalog(monkeypatch, yaml_text)
    with pytest.raises(WritePrimitivesCatalogError, match="cannot combine"):
        load_write_primitives_catalog()


def test_loader_rejects_non_numeric_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_text = """
version: 1
operations:
  - id: bad_op
    description: non-numeric bound
    write_sql: "SELECT 1;"
    validation_queries:
      - id: bad
        sql: "SELECT 1.0;"
        expected_value_min: "not a number"
        expected_value_max: 10
"""
    _patch_catalog(monkeypatch, yaml_text)
    with pytest.raises(WritePrimitivesCatalogError, match="must be numeric"):
        load_write_primitives_catalog()


def test_loader_real_catalog_still_loads() -> None:
    catalog = load_write_primitives_catalog()
    assert catalog.operations
    for op in catalog.operations.values():
        for v in op.validation_queries:
            if op.id and not op.id.startswith("sketch_"):
                assert v.expected_value_min is None
                assert v.expected_value_max is None


def test_datafusion_batch_values_overrides_use_unique_projection_names() -> None:
    catalog = load_write_primitives_catalog()

    for operation_id in ("insert_batch_values_100", "insert_batch_values_1000"):
        override = catalog.operations[operation_id].platform_overrides["datafusion"]
        assert override is not None
        assert "unnest(generate_series" in override

        select_list = override.split("FROM")[0]
        assert "SELECT" in select_list
        assert "," in select_list
        for item in select_list.split("SELECT", 1)[1].split(","):
            assert " AS " in item.upper()


def test_batch_values_casts_use_explicit_decimal_scale() -> None:
    catalog = load_write_primitives_catalog()

    for operation_id in ("insert_batch_values_100", "insert_batch_values_1000"):
        operation = catalog.operations[operation_id]
        for source_name, sql in (
            ("base", operation.write_sql),
            ("snowflake", operation.platform_overrides["snowflake"]),
            ("databricks", operation.platform_overrides["databricks"]),
        ):
            assert sql is not None
            for literal in ("0.05", "0.02"):
                assert f"CAST({literal} AS DECIMAL(15,2))" in sql, (operation_id, source_name)
            assert "AS NUMERIC)" not in sql, (operation_id, source_name)

        bigquery_sql = operation.platform_overrides["bigquery"]
        assert bigquery_sql is not None
        assert "DECIMAL(15,2)" not in bigquery_sql
        assert "CAST(0.05 AS NUMERIC)" in bigquery_sql


def test_batch_values_snowflake_keys_stay_inside_cleanup_range() -> None:
    catalog = load_write_primitives_catalog()

    for operation_id, expected_rows in (("insert_batch_values_100", 100), ("insert_batch_values_1000", 1000)):
        operation = catalog.operations[operation_id]
        override = operation.platform_overrides["snowflake"]
        assert override is not None

        assert "ROW_NUMBER() OVER (ORDER BY SEQ4()) - 1 AS n" in override, operation_id
        assert re.search(r"SELECT\s+SEQ4\(\)\s+AS\s+n", override) is None, operation_id

        rowcount_match = re.search(r"GENERATOR\(ROWCOUNT => (\d+)\)", override)
        assert rowcount_match is not None, operation_id
        rowcount = int(rowcount_match.group(1))
        assert rowcount == expected_rows, operation_id

        base_key_match = re.search(r"SELECT (\d+) \+ n", override)
        assert base_key_match is not None, operation_id
        base_key = int(base_key_match.group(1))

        cleanup_match = re.search(r"l_orderkey BETWEEN (\d+) AND (\d+)", operation.cleanup_sql or "")
        assert cleanup_match is not None, operation_id
        low, high = int(cleanup_match.group(1)), int(cleanup_match.group(2))

        assert base_key == low, operation_id
        assert base_key + rowcount - 1 == high, operation_id


def test_datafusion_skips_slash_date_format_bulk_load() -> None:
    catalog = load_write_primitives_catalog()
    operation = catalog.operations["bulk_load_date_format_custom"]
    assert operation.platform_overrides["datafusion"] is None


def test_duckdb_only_cpc_and_req_operations_skip_trino() -> None:
    catalog = load_write_primitives_catalog()

    duckdb_only_ops = [
        operation
        for operation in catalog.operations.values()
        if operation.id.startswith(("sketch_cpc_", "sketch_req_"))
    ]

    assert duckdb_only_ops
    assert all("trino" in operation.platform_overrides for operation in duckdb_only_ops)
    assert all(operation.platform_overrides["trino"] is None for operation in duckdb_only_ops)


def test_real_catalog_version_is_pinned() -> None:
    catalog = load_write_primitives_catalog()
    assert catalog.version == 2


