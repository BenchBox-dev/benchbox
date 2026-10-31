# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.metadata_primitives import (
    MetadataBenchmarkResult,
    MetadataPrimitivesBenchmark,
    MetadataPrimitivesQueryManager,
    MetadataQueryResult,
)
from benchbox.core.metadata_primitives.catalog import (
    MetadataCatalog,
    MetadataQuery,
    load_metadata_catalog,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.unit
class TestMetadataCatalogLoader:
    def test_load_catalog(self):

        catalog = load_metadata_catalog()
        assert isinstance(catalog, MetadataCatalog)
        assert catalog.version == 1
        assert len(catalog.queries) > 0

    def test_catalog_version(self):

        catalog = load_metadata_catalog()
        assert catalog.version == 1

    def test_catalog_query_structure(self):
        catalog = load_metadata_catalog()

        for query_id, query in catalog.queries.items():
            assert isinstance(query, MetadataQuery)
            assert query.id == query_id
            assert len(query.id) > 0
            assert len(query.category) > 0
            assert len(query.sql.strip()) > 0

    def test_catalog_categories(self):

        catalog = load_metadata_catalog()

        categories = {q.category for q in catalog.queries.values()}
        expected_categories = {"schema", "column", "stats", "query"}
        assert expected_categories.issubset(categories)

    def test_query_variants(self):

        catalog = load_metadata_catalog()

        queries_with_variants = [q for q in catalog.queries.values() if q.variants]
        assert len(queries_with_variants) > 0

        for query in queries_with_variants:
            assert isinstance(query.variants, dict)
            for dialect, sql in query.variants.items():
                assert isinstance(dialect, str)
                assert len(sql.strip()) > 0

    def test_query_skip_on(self):

        catalog = load_metadata_catalog()

        queries_with_skip = [q for q in catalog.queries.values() if q.skip_on]
        assert len(queries_with_skip) > 0

        for query in queries_with_skip:
            assert isinstance(query.skip_on, list)
            for dialect in query.skip_on:
                assert isinstance(dialect, str)
                assert len(dialect) > 0

    def test_large_catalog_column_count_guards_empty_catalog_division(self):
        query = load_metadata_catalog().queries["large_catalog_column_count"]

        assert "NULLIF(COUNT(DISTINCT table_name), 0)" in query.sql
        assert "nullIf(uniq(table), 0)" in query.variants["clickhouse"]


@pytest.mark.unit
class TestMetadataPrimitivesQueryManager:
    def test_query_manager_initialization(self):

        manager = MetadataPrimitivesQueryManager()
        queries = manager.get_all_queries()
        assert len(queries) > 0
        assert isinstance(queries, dict)

    def test_catalog_version(self):

        manager = MetadataPrimitivesQueryManager()
        assert manager.catalog_version == 1

    def test_get_query(self):

        manager = MetadataPrimitivesQueryManager()

        query = manager.get_query("schema_list_tables")
        assert isinstance(query, str)
        assert "SELECT" in query.upper()
        assert "table_name" in query.lower()

    def test_get_query_invalid(self):

        manager = MetadataPrimitivesQueryManager()

        with pytest.raises(ValueError, match="Invalid query ID"):
            manager.get_query("invalid_query_id")

    def test_get_query_with_dialect_variant(self):

        manager = MetadataPrimitivesQueryManager()

        base_query = manager.get_query("schema_list_tables")
        clickhouse_query = manager.get_query("schema_list_tables", dialect="clickhouse")

        assert "system.tables" in clickhouse_query
        assert "information_schema" in base_query.lower()

    def test_get_query_skip_on(self):

        manager = MetadataPrimitivesQueryManager()

        with pytest.raises(ValueError, match="not supported on dialect"):
            manager.get_query("schema_list_views", dialect="clickhouse")

    def test_datafusion_uses_view_definition_column_and_skips_constraints(self):
        manager = MetadataPrimitivesQueryManager()

        view_query = manager.get_query("schema_list_views", dialect="datafusion")
        assert "definition AS view_definition" in view_query

        for query_id in (
            "query_list_constraints",
            "constraint_fk_list",
            "constraint_pk_list",
            "constraint_referential_integrity",
            "constraint_summary",
        ):
            with pytest.raises(ValueError, match="not supported on dialect"):
                manager.get_query(query_id, dialect="datafusion")

        datafusion_queries = manager.get_queries_for_dialect("datafusion")
        assert all(
            query_id not in datafusion_queries
            for query_id in (
                "query_list_constraints",
                "constraint_fk_list",
                "constraint_pk_list",
                "constraint_referential_integrity",
                "constraint_summary",
            )
        )

    def test_clickhouse_role_membership_uses_native_admin_option_name(self):
        manager = MetadataPrimitivesQueryManager()

        query = manager.get_query("acl_role_membership", dialect="clickhouse")

        assert "with_admin_option AS admin_option" in query
        assert "\n    admin_option\n" not in query

    def test_singlestore_reads_roles_and_constraints_from_its_own_catalog(self):
        manager = MetadataPrimitivesQueryManager()

        for query_id in ("acl_role_list", "acl_role_membership", "constraint_referential_integrity"):
            query = manager.get_query(query_id, dialect="singlestore")
            assert "pg_roles" not in query
            assert "pg_auth_members" not in query
            assert "constraint_column_usage" not in query
            assert "information_schema." in query

    def test_databricks_skips_unsupported_acl_queries(self):
        manager = MetadataPrimitivesQueryManager()

        for query_id in (
            "acl_column_privileges_list",
            "acl_role_list",
            "acl_role_membership",
        ):
            with pytest.raises(ValueError, match="not supported on dialect"):
                manager.get_query(query_id, dialect="databricks")

    def test_get_query_entry(self):

        manager = MetadataPrimitivesQueryManager()

        entry = manager.get_query_entry("schema_list_tables")
        assert isinstance(entry, MetadataQuery)
        assert entry.id == "schema_list_tables"
        assert entry.category == "schema"

    def test_get_all_queries(self):

        manager = MetadataPrimitivesQueryManager()

        queries = manager.get_all_queries()
        assert isinstance(queries, dict)
        assert len(queries) > 0
        assert all(isinstance(v, str) for v in queries.values())

    def test_query_categories(self):

        manager = MetadataPrimitivesQueryManager()

        categories = manager.get_query_categories()
        assert "schema" in categories
        assert "column" in categories
        assert "stats" in categories
        assert "query" in categories

    def test_get_queries_by_category(self):

        manager = MetadataPrimitivesQueryManager()

        schema_queries = manager.get_queries_by_category("schema")
        assert len(schema_queries) > 0
        assert "schema_list_tables" in schema_queries

        column_queries = manager.get_queries_by_category("column")
        assert len(column_queries) > 0
        assert "column_list_all" in column_queries

        empty_queries = manager.get_queries_by_category("nonexistent")
        assert len(empty_queries) == 0

    def test_get_queries_for_dialect(self):

        manager = MetadataPrimitivesQueryManager()

        duckdb_queries = manager.get_queries_for_dialect("duckdb")
        assert len(duckdb_queries) > 0

        clickhouse_queries = manager.get_queries_for_dialect("clickhouse")
        assert len(clickhouse_queries) > 0
        assert "schema_list_views" not in clickhouse_queries


@pytest.mark.unit
class TestMetadataPrimitivesBenchmark:
    def test_benchmark_initialization(self):

        benchmark = MetadataPrimitivesBenchmark()
        assert benchmark._name == "Metadata Primitives Benchmark"
        assert benchmark._version == "1.0"
        assert benchmark.query_manager is not None

    def test_benchmark_with_scale_factor(self):

        benchmark = MetadataPrimitivesBenchmark(scale_factor=0.1)
        assert benchmark.scale_factor == 0.1

    def test_data_source_none(self):

        benchmark = MetadataPrimitivesBenchmark()
        assert benchmark.get_data_source_benchmark() is None

    def test_generate_data_empty(self):

        benchmark = MetadataPrimitivesBenchmark()
        result = benchmark.generate_data()
        assert result == {}

    def test_execute_query_reports_translation_failure(self, monkeypatch):
        from benchbox.utils.dialect_utils import SQLTranslationError

        benchmark = MetadataPrimitivesBenchmark()

        def fail(query_id, dialect):
            raise SQLTranslationError("cannot translate", None)

        monkeypatch.setattr(benchmark, "_resolve_query_sql", fail)
        result = benchmark.execute_query("schema_list_schemata", object(), dialect="postgres")

        assert result.success is False
        assert "cannot translate" in result.error

    def test_get_query(self):

        benchmark = MetadataPrimitivesBenchmark()

        query = benchmark.get_query("schema_list_tables")
        assert isinstance(query, str)
        assert "SELECT" in query.upper()

    def test_get_query_with_params_error(self):

        benchmark = MetadataPrimitivesBenchmark()

        with pytest.raises(ValueError, match="don't accept parameters"):
            benchmark.get_query("schema_list_tables", params={"foo": "bar"})

    def test_get_queries(self):

        benchmark = MetadataPrimitivesBenchmark()

        queries = benchmark.get_queries()
        assert isinstance(queries, dict)
        assert len(queries) > 0

    def test_get_queries_with_dialect(self):

        benchmark = MetadataPrimitivesBenchmark()

        clickhouse_queries = benchmark.get_queries(dialect="clickhouse")
        assert "schema_list_views" not in clickhouse_queries

    def test_get_queries_by_category(self):

        benchmark = MetadataPrimitivesBenchmark()

        schema_queries = benchmark.get_queries_by_category("schema")
        assert len(schema_queries) > 0

        column_queries = benchmark.get_queries_by_category("column")
        assert len(column_queries) > 0

    def test_get_query_categories(self):

        benchmark = MetadataPrimitivesBenchmark()

        categories = benchmark.get_query_categories()
        assert "schema" in categories
        assert "column" in categories
        assert "stats" in categories
        assert "query" in categories

    def test_invalid_query(self):

        benchmark = MetadataPrimitivesBenchmark()

        with pytest.raises(ValueError, match="Invalid query ID"):
            benchmark.get_query("invalid_query")


@pytest.mark.unit
class TestMetadataQueryResult:
    def test_result_creation(self):

        result = MetadataQueryResult(
            query_id="schema_list_tables",
            category="schema",
            execution_time_ms=10.5,
            row_count=100,
            success=True,
        )
        assert result.query_id == "schema_list_tables"
        assert result.category == "schema"
        assert result.execution_time_ms == 10.5
        assert result.row_count == 100
        assert result.success is True
        assert result.error is None

    def test_result_with_error(self):

        result = MetadataQueryResult(
            query_id="schema_list_tables",
            category="schema",
            execution_time_ms=5.0,
            row_count=0,
            success=False,
            error="Query failed",
        )
        assert result.success is False
        assert result.error == "Query failed"


@pytest.mark.unit
class TestMetadataBenchmarkResult:
    def test_result_creation(self):

        result = MetadataBenchmarkResult()
        assert result.total_queries == 0
        assert result.successful_queries == 0
        assert result.failed_queries == 0
        assert result.total_time_ms == 0.0
        assert result.results == []
        assert result.category_summary == {}

    def test_result_with_data(self):

        query_results = [
            MetadataQueryResult(
                query_id="q1",
                category="schema",
                execution_time_ms=10.0,
                row_count=5,
                success=True,
            ),
            MetadataQueryResult(
                query_id="q2",
                category="column",
                execution_time_ms=15.0,
                row_count=10,
                success=True,
            ),
        ]

        result = MetadataBenchmarkResult(
            total_queries=2,
            successful_queries=2,
            failed_queries=0,
            total_time_ms=25.0,
            results=query_results,
            category_summary={
                "schema": {"total_queries": 1, "successful": 1},
                "column": {"total_queries": 1, "successful": 1},
            },
        )

        assert result.total_queries == 2
        assert result.successful_queries == 2
        assert len(result.results) == 2
        assert "schema" in result.category_summary


@pytest.mark.unit
class TestQueryContentValidation:
    def test_schema_queries_content(self):

        manager = MetadataPrimitivesQueryManager()

        query = manager.get_query("schema_list_tables")
        assert "table_name" in query.lower()
        assert "table_type" in query.lower()

    def test_column_queries_content(self):

        manager = MetadataPrimitivesQueryManager()

        query = manager.get_query("column_list_all")
        assert "column_name" in query.lower()
        assert "data_type" in query.lower()

    def test_stats_queries_content(self):

        manager = MetadataPrimitivesQueryManager()

        query = manager.get_query("stats_column_count_summary")
        assert "count" in query.lower()

    def test_query_introspection_content(self):

        manager = MetadataPrimitivesQueryManager()

        query = manager.get_query("query_explain_simple")
        assert "explain" in query.lower() or "select" in query.lower()


@pytest.mark.unit
class TestDialectVariants:
    def test_clickhouse_variants_use_system_tables(self):

        manager = MetadataPrimitivesQueryManager()

        clickhouse_queries = manager.get_queries_for_dialect("clickhouse")

        has_system_tables = False
        for sql in clickhouse_queries.values():
            if "system." in sql:
                has_system_tables = True
                break
        assert has_system_tables

    def test_duckdb_variants(self):

        manager = MetadataPrimitivesQueryManager()

        duckdb_query = manager.get_query("schema_list_views", dialect="duckdb")
        assert "duckdb_views()" in duckdb_query

    def test_base_query_unchanged_without_variant(self):

        manager = MetadataPrimitivesQueryManager()

        base_query = manager.get_query("schema_list_schemata")
        duckdb_query = manager.get_query("schema_list_schemata", dialect="duckdb")

        assert base_query == duckdb_query
