# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import importlib.resources

import pytest

from benchbox.core.read_primitives.catalog import (
    PrimitiveQuery,
    PrimitivesCatalogError,
    ResultColumnContract,
    ResultContract,
    load_primitives_catalog,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def mock_catalog_yaml(monkeypatch, tmp_path):

    class MockPath:
        def __init__(self, path):
            self.path = path

        def joinpath(self, name):
            return self.path / name

    def write(catalog_yaml: str):
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)
        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))
        return catalog_file

    return write


class TestPrimitiveQueryDataclass:
    def test_primitive_query_basic_fields(self):
        query = PrimitiveQuery(
            id="test_query",
            category="test",
            sql="SELECT 1",
        )
        assert query.id == "test_query"
        assert query.category == "test"
        assert query.sql == "SELECT 1"
        assert query.description is None
        assert query.variants is None
        assert query.skip_on is None
        assert query.result_contract is None

    def test_primitive_query_with_variants(self):

        query = PrimitiveQuery(
            id="test_query",
            category="test",
            sql="SELECT 1",
            variants={"duckdb": "SELECT 1 AS result", "bigquery": "SELECT 1 as result"},
        )
        assert query.variants == {"duckdb": "SELECT 1 AS result", "bigquery": "SELECT 1 as result"}

    def test_primitive_query_with_skip_on(self):

        query = PrimitiveQuery(id="test_query", category="test", sql="SELECT 1", skip_on=["duckdb", "sqlite"])
        assert query.skip_on == ["duckdb", "sqlite"]

    def test_primitive_query_with_all_fields(self):

        query = PrimitiveQuery(
            id="test_query",
            category="test",
            sql="SELECT 1",
            description="Test query description",
            variants={"duckdb": "SELECT 1 AS result"},
            skip_on=["sqlite"],
            result_contract=ResultContract(
                columns=(ResultColumnContract(name="result"),),
                capability="scalar_projection",
            ),
        )
        assert query.id == "test_query"
        assert query.category == "test"
        assert query.sql == "SELECT 1"
        assert query.description == "Test query description"
        assert query.variants == {"duckdb": "SELECT 1 AS result"}
        assert query.skip_on == ["sqlite"]
        assert query.result_contract is not None
        assert query.result_contract.capability == "scalar_projection"


class TestCatalogLoaderResultContractParsing:
    def test_load_query_with_result_contract(self, mock_catalog_yaml):
        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT k, ARRAY_AGG(v ORDER BY v) AS values FROM t GROUP BY k
    result_contract:
      capability: ordered_array_aggregate
      row_identity: [k]
      columns:
        - k
        - name: values
          type_class: array
          order_sensitive: true
"""
        mock_catalog_yaml(catalog_yaml)

        catalog = load_primitives_catalog()
        contract = catalog.queries["test_query"].result_contract

        assert contract == ResultContract(
            columns=(
                ResultColumnContract(name="k"),
                ResultColumnContract(name="values", type_class="array", order_sensitive=True),
            ),
            row_identity=("k",),
            capability="ordered_array_aggregate",
        )

    def test_result_contract_columns_must_be_non_empty_list(self, mock_catalog_yaml):

        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    result_contract:
      columns: []
"""
        mock_catalog_yaml(catalog_yaml)

        with pytest.raises(PrimitivesCatalogError, match="result_contract.columns must be a non-empty list"):
            load_primitives_catalog()

    def test_result_contract_rejects_unknown_row_identity_column(self, mock_catalog_yaml):
        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1 AS result
    result_contract:
      columns: [result]
      row_identity: [missing]
"""
        mock_catalog_yaml(catalog_yaml)

        with pytest.raises(PrimitivesCatalogError, match="row_identity references unknown column 'missing'"):
            load_primitives_catalog()

    def test_result_contract_rejects_unknown_type_class(self, mock_catalog_yaml):

        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1 AS result
    result_contract:
      columns:
        - name: result
          type_class: nested_blob
"""
        mock_catalog_yaml(catalog_yaml)

        with pytest.raises(PrimitivesCatalogError, match="type_class must be one of"):
            load_primitives_catalog()


class TestCatalogLoaderVariantParsing:
    def test_load_query_with_single_variant(self, monkeypatch, tmp_path):

        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: |
      SELECT * FROM orders
    variants:
      duckdb: |
        SELECT * FROM orders USING SAMPLE 10%
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        catalog = load_primitives_catalog()

        assert "test_query" in catalog.queries
        query = catalog.queries["test_query"]
        assert query.variants is not None
        assert "duckdb" in query.variants
        assert "USING SAMPLE" in query.variants["duckdb"]

    def test_load_query_with_multiple_variants(self, monkeypatch, tmp_path):

        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT * FROM orders
    variants:
      duckdb: SELECT * FROM orders USING SAMPLE 10%
      bigquery: SELECT * FROM orders TABLESAMPLE SYSTEM (10 PERCENT)
      snowflake: SELECT * FROM orders SAMPLE (10)
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        catalog = load_primitives_catalog()
        query = catalog.queries["test_query"]

        assert query.variants is not None
        assert len(query.variants) == 3
        assert "duckdb" in query.variants
        assert "bigquery" in query.variants
        assert "snowflake" in query.variants

    def test_variant_dialect_normalized_to_lowercase(self, monkeypatch, tmp_path):

        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    variants:
      DuckDB: SELECT 1 AS result
      BIGQUERY: SELECT 1 as result
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        catalog = load_primitives_catalog()
        query = catalog.queries["test_query"]

        assert "duckdb" in query.variants
        assert "bigquery" in query.variants
        assert "DuckDB" not in query.variants
        assert "BIGQUERY" not in query.variants

    def test_variant_sql_must_be_non_empty(self, monkeypatch, tmp_path):
        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    variants:
      duckdb: ""
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        with pytest.raises(PrimitivesCatalogError) as exc_info:
            load_primitives_catalog()

        assert "variant SQL for dialect 'duckdb' must be non-empty" in str(exc_info.value)

    def test_variant_dialect_must_be_non_empty_string(self, monkeypatch, tmp_path):
        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    variants:
      "": SELECT 1 AS result
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        with pytest.raises(PrimitivesCatalogError) as exc_info:
            load_primitives_catalog()

        assert "variant dialect must be a non-empty string" in str(exc_info.value)

    def test_variants_must_be_dict(self, monkeypatch, tmp_path):
        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    variants: ["duckdb", "bigquery"]
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        with pytest.raises(PrimitivesCatalogError) as exc_info:
            load_primitives_catalog()

        assert "invalid 'variants'" in str(exc_info.value)
        assert "expected mapping" in str(exc_info.value)


class TestCatalogLoaderSkipOnParsing:
    def test_load_query_with_single_skip_on(self, monkeypatch, tmp_path):

        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    skip_on: [duckdb]
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        catalog = load_primitives_catalog()
        query = catalog.queries["test_query"]

        assert query.skip_on is not None
        assert query.skip_on == ["duckdb"]

    def test_load_query_with_multiple_skip_on(self, monkeypatch, tmp_path):

        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    skip_on: [duckdb, sqlite, mysql]
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        catalog = load_primitives_catalog()
        query = catalog.queries["test_query"]

        assert query.skip_on is not None
        assert len(query.skip_on) == 3
        assert "duckdb" in query.skip_on
        assert "sqlite" in query.skip_on
        assert "mysql" in query.skip_on

    def test_skip_on_normalized_to_lowercase(self, monkeypatch, tmp_path):

        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    skip_on: [DuckDB, SQLITE]
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        catalog = load_primitives_catalog()
        query = catalog.queries["test_query"]

        assert "duckdb" in query.skip_on
        assert "sqlite" in query.skip_on
        assert "DuckDB" not in query.skip_on
        assert "SQLITE" not in query.skip_on

    def test_skip_on_dialect_must_be_non_empty_string(self, monkeypatch, tmp_path):
        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    skip_on: ["duckdb", ""]
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        with pytest.raises(PrimitivesCatalogError) as exc_info:
            load_primitives_catalog()

        assert "skip_on dialect must be a non-empty string" in str(exc_info.value)

    def test_skip_on_must_be_list(self, monkeypatch, tmp_path):
        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT 1
    skip_on: "duckdb"
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        with pytest.raises(PrimitivesCatalogError) as exc_info:
            load_primitives_catalog()

        assert "invalid 'skip_on'" in str(exc_info.value)
        assert "expected list" in str(exc_info.value)


class TestCatalogLoaderCombinedFeatures:
    def test_query_with_both_variants_and_skip_on(self, monkeypatch, tmp_path):

        catalog_yaml = """
version: 1
queries:
  - id: test_query
    category: test
    sql: SELECT * FROM orders
    variants:
      bigquery: SELECT * FROM `orders`
    skip_on: [sqlite]
    description: Test query with both features
"""
        catalog_file = tmp_path / "queries.yaml"
        catalog_file.write_text(catalog_yaml)

        import importlib.resources

        class MockPath:
            def __init__(self, path):
                self.path = path

            def joinpath(self, name):
                return self.path / name

            def open(self, *args, **kwargs):
                return open(self.path / "queries.yaml", *args, **kwargs)

        monkeypatch.setattr(importlib.resources, "files", lambda pkg: MockPath(tmp_path))

        catalog = load_primitives_catalog()
        query = catalog.queries["test_query"]

        assert query.variants is not None
        assert "bigquery" in query.variants
        assert query.skip_on is not None
        assert "sqlite" in query.skip_on
        assert query.description == "Test query with both features"


