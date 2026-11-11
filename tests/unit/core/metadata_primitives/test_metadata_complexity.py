# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.metadata_primitives import (
    COMPLEXITY_PRESETS,
    ComplexityBenchmarkResult,
    ConstraintDensity,
    GeneratedMetadata,
    MetadataComplexityConfig,
    MetadataGenerator,
    MetadataPrimitivesBenchmark,
    PermissionDensity,
    RoleHierarchyDepth,
    TypeComplexity,
    get_complexity_preset,
)
from benchbox.core.metadata_primitives.ddl import (
    TYPE_MAPPINGS,
    ColumnDefinition,
    TableDefinition,
    ViewDefinition,
    generate_create_table_sql,
    generate_create_view_sql,
    generate_drop_table_sql,
    generate_drop_view_sql,
    generate_simple_table_columns,
    generate_wide_table_columns,
    get_type_mapping,
    map_type,
    supports_complex_types,
    supports_foreign_keys,
    supports_views,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.unit
class TestMetadataComplexityConfig:
    def test_default_config(self):

        config = MetadataComplexityConfig()
        assert config.width_factor == 50
        assert config.view_depth == 1
        assert config.type_complexity == TypeComplexity.SCALAR
        assert config.catalog_size == 10
        assert config.constraint_density == ConstraintDensity.NONE
        assert config.schema_count == 1
        assert config.prefix == "benchbox_"

    def test_custom_config(self):

        config = MetadataComplexityConfig(
            width_factor=200,
            view_depth=3,
            type_complexity=TypeComplexity.NESTED,
            catalog_size=50,
            constraint_density=ConstraintDensity.DENSE,
        )
        assert config.width_factor == 200
        assert config.view_depth == 3
        assert config.type_complexity == TypeComplexity.NESTED
        assert config.catalog_size == 50
        assert config.constraint_density == ConstraintDensity.DENSE

    def test_width_factor_validation_min(self):

        with pytest.raises(ValueError, match="width_factor must be >= 1"):
            MetadataComplexityConfig(width_factor=0)

    def test_width_factor_validation_max(self):

        with pytest.raises(ValueError, match="width_factor must be <= 10000"):
            MetadataComplexityConfig(width_factor=20000)

    def test_view_depth_validation_min(self):

        with pytest.raises(ValueError, match="view_depth must be >= 0"):
            MetadataComplexityConfig(view_depth=-1)

    def test_view_depth_validation_max(self):

        with pytest.raises(ValueError, match="view_depth must be <= 10"):
            MetadataComplexityConfig(view_depth=15)

    def test_catalog_size_validation_min(self):

        with pytest.raises(ValueError, match="catalog_size must be >= 1"):
            MetadataComplexityConfig(catalog_size=0)

    def test_catalog_size_validation_max(self):

        with pytest.raises(ValueError, match="catalog_size must be <= 5000"):
            MetadataComplexityConfig(catalog_size=10000)

    def test_schema_count_validation(self):

        with pytest.raises(ValueError, match="schema_count must be >= 1"):
            MetadataComplexityConfig(schema_count=0)

    def test_type_complexity_from_string(self):

        config = MetadataComplexityConfig(type_complexity="nested")
        assert config.type_complexity == TypeComplexity.NESTED

    def test_constraint_density_from_string(self):

        config = MetadataComplexityConfig(constraint_density="sparse")
        assert config.constraint_density == ConstraintDensity.SPARSE

    def test_to_dict(self):

        config = MetadataComplexityConfig(
            width_factor=100,
            view_depth=2,
            type_complexity=TypeComplexity.BASIC,
        )
        d = config.to_dict()

        assert d["width_factor"] == 100
        assert d["view_depth"] == 2
        assert d["type_complexity"] == "basic"
        assert d["prefix"] == "benchbox_"

    def test_from_dict(self):

        d = {
            "width_factor": 150,
            "view_depth": 3,
            "type_complexity": "nested",
            "catalog_size": 25,
        }
        config = MetadataComplexityConfig.from_dict(d)

        assert config.width_factor == 150
        assert config.view_depth == 3
        assert config.type_complexity == TypeComplexity.NESTED
        assert config.catalog_size == 25

    def test_from_dict_defaults(self):

        config = MetadataComplexityConfig.from_dict({})
        assert config.width_factor == 50
        assert config.view_depth == 1


@pytest.mark.unit
class TestComplexityPresets:
    def test_all_presets_exist(self):

        expected = [
            "minimal",
            "baseline",
            "wide_tables",
            "deep_views",
            "complex_types",
            "large_catalog",
            "full",
            "stress",
        ]
        for preset_name in expected:
            assert preset_name in COMPLEXITY_PRESETS

    def test_get_preset_valid(self):

        config = get_complexity_preset("wide_tables")
        assert isinstance(config, MetadataComplexityConfig)
        assert config.width_factor == 500

    def test_get_preset_invalid(self):

        with pytest.raises(ValueError, match="Unknown complexity preset"):
            get_complexity_preset("nonexistent")

    def test_minimal_preset(self):

        config = get_complexity_preset("minimal")
        assert config.width_factor == 20
        assert config.catalog_size == 5

    def test_stress_preset(self):

        config = get_complexity_preset("stress")
        assert config.width_factor == 1000
        assert config.view_depth == 5
        assert config.type_complexity == TypeComplexity.NESTED
        assert config.catalog_size == 200
        assert config.constraint_density == ConstraintDensity.DENSE


@pytest.mark.unit
class TestGeneratedMetadata:
    def test_default_values(self):

        generated = GeneratedMetadata()
        assert generated.tables == []
        assert generated.views == []
        assert generated.schemas == []
        assert generated.prefix == "benchbox_"
        assert generated.config is None

    def test_total_objects(self):

        generated = GeneratedMetadata(
            tables=["t1", "t2", "t3"],
            views=["v1", "v2"],
            schemas=["s1"],
        )
        assert generated.total_objects == 6

    def test_summary(self):

        generated = GeneratedMetadata(
            tables=["t1", "t2"],
            views=["v1"],
            prefix="test_",
        )
        summary = generated.summary()

        assert summary["tables"] == 2
        assert summary["views"] == 1
        assert summary["schemas"] == 0
        assert summary["total"] == 3
        assert summary["prefix"] == "test_"


@pytest.mark.unit
class TestTypeComplexity:
    def test_enum_values(self):

        assert TypeComplexity.SCALAR.value == "scalar"
        assert TypeComplexity.BASIC.value == "basic"
        assert TypeComplexity.NESTED.value == "nested"


@pytest.mark.unit
class TestConstraintDensity:
    def test_enum_values(self):

        assert ConstraintDensity.NONE.value == "none"
        assert ConstraintDensity.SPARSE.value == "sparse"
        assert ConstraintDensity.DENSE.value == "dense"


@pytest.mark.unit
class TestTypeMappings:
    def test_all_dialects_have_mappings(self):

        expected_dialects = ["duckdb", "snowflake", "bigquery", "clickhouse", "databricks", "postgres"]
        for dialect in expected_dialects:
            assert dialect in TYPE_MAPPINGS

    def test_get_type_mapping_known_dialect(self):

        mapping = get_type_mapping("duckdb")
        assert "integer" in mapping
        assert "varchar" in mapping
        assert mapping["integer"] == "INTEGER"

    def test_get_type_mapping_unknown_dialect(self):

        mapping = get_type_mapping("unknown_dialect")
        assert mapping == TYPE_MAPPINGS["duckdb"]

    def test_get_type_mapping_case_insensitive(self):

        mapping = get_type_mapping("DuckDB")
        assert mapping == TYPE_MAPPINGS["duckdb"]

    def test_map_type_basic(self):

        assert map_type("integer", "duckdb") == "INTEGER"
        assert map_type("varchar", "snowflake") == "VARCHAR(255)"

    def test_map_type_complex(self):

        assert map_type("array_int", "duckdb") == "INTEGER[]"
        assert map_type("array_int", "clickhouse") == "Array(Int32)"
        assert map_type("struct_simple", "bigquery") == "STRUCT<key STRING, value STRING>"

    def test_map_type_unknown(self):

        result = map_type("unknown_type", "duckdb")
        assert result == "VARCHAR(255)"


@pytest.mark.unit
class TestColumnDefinition:
    def test_default_values(self):

        col = ColumnDefinition(name="test", data_type="INTEGER")
        assert col.name == "test"
        assert col.data_type == "INTEGER"
        assert col.nullable is True
        assert col.primary_key is False

    def test_primary_key_column(self):

        col = ColumnDefinition(name="id", data_type="BIGINT", nullable=False, primary_key=True)
        assert col.primary_key is True
        assert col.nullable is False


@pytest.mark.unit
class TestTableDefinition:
    def test_basic_table(self):

        table = TableDefinition(
            name="test_table",
            columns=[
                ColumnDefinition("id", "BIGINT", nullable=False, primary_key=True),
                ColumnDefinition("name", "VARCHAR(255)"),
            ],
        )
        assert table.name == "test_table"
        assert len(table.columns) == 2
        assert table.schema_name is None


@pytest.mark.unit
class TestGenerateWideTableColumns:
    def test_generate_columns_count(self):

        columns = generate_wide_table_columns(100, "duckdb")
        assert len(columns) == 100

    def test_generate_columns_has_pk(self):

        columns = generate_wide_table_columns(50, "duckdb")
        pk_columns = [c for c in columns if c.primary_key]
        assert len(pk_columns) == 1
        assert pk_columns[0].name == "id"

    def test_generate_columns_type_distribution(self):

        columns = generate_wide_table_columns(100, "duckdb")
        types = {c.data_type for c in columns}
        assert len(types) >= 3

    def test_generate_columns_with_complex_types(self):

        columns = generate_wide_table_columns(50, "duckdb", TypeComplexity.BASIC)
        type_names = [c.data_type for c in columns]
        assert any("[]" in t for t in type_names)

    def test_generate_columns_with_nested_types(self):

        columns = generate_wide_table_columns(50, "duckdb", TypeComplexity.NESTED)
        type_names = [c.data_type for c in columns]
        assert any("STRUCT" in t for t in type_names)


@pytest.mark.unit
class TestGenerateSimpleTableColumns:
    def test_generate_minimum_columns(self):

        columns = generate_simple_table_columns(5, "duckdb")
        assert len(columns) == 5
        assert columns[0].name == "id"
        assert columns[1].name == "name"

    def test_generate_extra_columns(self):

        columns = generate_simple_table_columns(10, "duckdb")
        assert len(columns) == 10
        extra_names = [c.name for c in columns[5:]]
        assert all(name.startswith("field_") for name in extra_names)


@pytest.mark.unit
class TestGenerateCreateTableSQL:
    def test_basic_table(self):

        columns = [
            ColumnDefinition("id", "BIGINT", nullable=False, primary_key=True),
            ColumnDefinition("name", "VARCHAR(255)"),
        ]
        table = TableDefinition(name="test_table", columns=columns)
        sql = generate_create_table_sql(table, "duckdb")

        assert "CREATE TABLE IF NOT EXISTS test_table" in sql
        assert "id BIGINT NOT NULL" in sql
        assert "name VARCHAR(255)" in sql
        assert "PRIMARY KEY (id)" in sql

    def test_table_without_if_not_exists(self):

        columns = [ColumnDefinition("id", "BIGINT")]
        table = TableDefinition(name="test", columns=columns)
        sql = generate_create_table_sql(table, "duckdb", if_not_exists=False)

        assert "IF NOT EXISTS" not in sql
        assert "CREATE TABLE test" in sql

    def test_table_with_schema(self):

        columns = [ColumnDefinition("id", "BIGINT")]
        table = TableDefinition(name="test", columns=columns, schema_name="myschema")
        sql = generate_create_table_sql(table, "duckdb")

        assert "myschema.test" in sql

    def test_clickhouse_engine(self):

        columns = [
            ColumnDefinition("id", "Int64", nullable=False, primary_key=True),
        ]
        table = TableDefinition(name="test", columns=columns)
        sql = generate_create_table_sql(table, "clickhouse")

        assert "ENGINE = MergeTree()" in sql
        assert "ORDER BY (id)" in sql


@pytest.mark.unit
class TestGenerateCreateViewSQL:
    def test_basic_view(self):

        view = ViewDefinition(name="test_view", source_sql="SELECT * FROM base_table")
        sql = generate_create_view_sql(view, "duckdb")

        assert "CREATE OR REPLACE VIEW test_view" in sql
        assert "SELECT * FROM base_table" in sql

    def test_view_without_replace(self):

        view = ViewDefinition(name="test_view", source_sql="SELECT 1")
        sql = generate_create_view_sql(view, "duckdb", or_replace=False)

        assert "IF NOT EXISTS" in sql
        assert "OR REPLACE" not in sql


@pytest.mark.unit
class TestDropSQL:
    def test_drop_table(self):

        sql = generate_drop_table_sql("test_table", "duckdb")
        assert sql == "DROP TABLE IF EXISTS test_table;"

    def test_drop_table_without_if_exists(self):

        sql = generate_drop_table_sql("test_table", "duckdb", if_exists=False)
        assert sql == "DROP TABLE test_table;"

    def test_drop_table_with_schema(self):

        sql = generate_drop_table_sql("test_table", "duckdb", schema_name="myschema")
        assert "myschema.test_table" in sql

    def test_drop_view(self):

        sql = generate_drop_view_sql("test_view", "duckdb")
        assert sql == "DROP VIEW IF EXISTS test_view;"


@pytest.mark.unit
class TestDialectSupport:
    def test_supports_complex_types(self):

        assert supports_complex_types("duckdb") is True
        assert supports_complex_types("snowflake") is True
        assert supports_complex_types("bigquery") is True
        assert supports_complex_types("clickhouse") is True
        assert supports_complex_types("databricks") is True
        assert supports_complex_types("mysql") is False

    def test_supports_views(self):

        assert supports_views("duckdb") is True
        assert supports_views("snowflake") is True
        assert supports_views("clickhouse") is False

    def test_supports_foreign_keys(self):

        assert supports_foreign_keys("duckdb") is True
        assert supports_foreign_keys("snowflake") is True
        assert supports_foreign_keys("bigquery") is False
        assert supports_foreign_keys("clickhouse") is False


@pytest.mark.unit
class TestMetadataGenerator:
    def test_generator_instantiation(self):

        generator = MetadataGenerator()
        assert isinstance(generator, MetadataGenerator)
        assert hasattr(generator, "setup")


@pytest.mark.unit
class TestBenchmarkComplexityMethods:
    def test_get_complexity_categories(self):

        benchmark = MetadataPrimitivesBenchmark()
        categories = benchmark.get_complexity_categories()

        expected = ["wide_table", "view_hierarchy", "complex_type", "large_catalog", "constraint", "acl"]
        assert categories == expected

    def test_get_complexity_categories_for_wide_tables(self):

        benchmark = MetadataPrimitivesBenchmark()
        config = get_complexity_preset("wide_tables")
        categories = benchmark._get_complexity_categories(config)

        assert "wide_table" in categories
        assert "large_catalog" in categories

    def test_get_complexity_categories_for_full(self):

        benchmark = MetadataPrimitivesBenchmark()
        config = get_complexity_preset("full")
        categories = benchmark._get_complexity_categories(config)

        assert "wide_table" in categories
        assert "view_hierarchy" in categories
        assert "large_catalog" in categories
        assert "complex_type" in categories
        assert "constraint" in categories


@pytest.mark.unit
class TestComplexityBenchmarkResult:
    def test_result_creation(self):

        config = MetadataComplexityConfig()
        generated = GeneratedMetadata(tables=["t1"], views=["v1"])

        result = ComplexityBenchmarkResult(
            complexity_config=config,
            generated_metadata=generated,
            setup_time_ms=100.5,
            teardown_time_ms=50.2,
        )

        assert result.complexity_config == config
        assert result.generated_metadata == generated
        assert result.setup_time_ms == 100.5
        assert result.teardown_time_ms == 50.2
        assert result.benchmark_result is None


@pytest.mark.unit
class TestComplexityQueryCategories:
    def test_wide_table_category_exists(self):

        benchmark = MetadataPrimitivesBenchmark()
        queries = benchmark.get_queries_by_category("wide_table")
        assert len(queries) > 0

    def test_view_hierarchy_category_exists(self):

        benchmark = MetadataPrimitivesBenchmark()
        queries = benchmark.get_queries_by_category("view_hierarchy")
        assert len(queries) > 0

    def test_complex_type_category_exists(self):

        benchmark = MetadataPrimitivesBenchmark()
        queries = benchmark.get_queries_by_category("complex_type")
        assert len(queries) > 0

    def test_large_catalog_category_exists(self):

        benchmark = MetadataPrimitivesBenchmark()
        queries = benchmark.get_queries_by_category("large_catalog")
        assert len(queries) > 0

    def test_constraint_category_exists(self):

        benchmark = MetadataPrimitivesBenchmark()
        queries = benchmark.get_queries_by_category("constraint")
        assert len(queries) > 0

    def test_all_categories_available(self):

        benchmark = MetadataPrimitivesBenchmark()
        categories = benchmark.get_query_categories()

        assert "schema" in categories
        assert "column" in categories
        assert "stats" in categories
        assert "query" in categories

        assert "wide_table" in categories
        assert "view_hierarchy" in categories
        assert "complex_type" in categories
        assert "large_catalog" in categories
        assert "constraint" in categories

    def test_acl_category_exists(self):

        benchmark = MetadataPrimitivesBenchmark()
        queries = benchmark.get_queries_by_category("acl")
        assert len(queries) > 0
        query_ids = list(queries.keys())
        assert any("privilege" in q for q in query_ids)


@pytest.mark.unit
class TestAclComplexityConfig:
    def test_default_acl_config(self):

        config = MetadataComplexityConfig()
        assert config.acl_role_count == 0
        assert config.acl_permission_density == PermissionDensity.NONE
        assert config.acl_hierarchy_depth == RoleHierarchyDepth.FLAT
        assert config.acl_column_grants is False
        assert config.acl_grant_with_grant_option is False

    def test_acl_config_with_roles(self):

        config = MetadataComplexityConfig(
            acl_role_count=10,
            acl_permission_density=PermissionDensity.MODERATE,
            acl_hierarchy_depth=RoleHierarchyDepth.SHALLOW,
        )
        assert config.acl_role_count == 10
        assert config.acl_permission_density == PermissionDensity.MODERATE
        assert config.acl_hierarchy_depth == RoleHierarchyDepth.SHALLOW

    def test_acl_config_validation(self):

        with pytest.raises(ValueError):
            MetadataComplexityConfig(acl_role_count=-1)

        with pytest.raises(ValueError):
            MetadataComplexityConfig(acl_role_count=501)

    def test_acl_config_serialization(self):

        config = MetadataComplexityConfig(
            acl_role_count=20,
            acl_permission_density=PermissionDensity.DENSE,
            acl_hierarchy_depth=RoleHierarchyDepth.DEEP,
            acl_column_grants=True,
            acl_grant_with_grant_option=True,
        )
        data = config.to_dict()

        assert data["acl_role_count"] == 20
        assert data["acl_permission_density"] == "dense"
        assert data["acl_hierarchy_depth"] == "deep"
        assert data["acl_column_grants"] is True
        assert data["acl_grant_with_grant_option"] is True

    def test_acl_config_deserialization(self):

        data = {
            "acl_role_count": 15,
            "acl_permission_density": "sparse",
            "acl_hierarchy_depth": "moderate",
        }
        config = MetadataComplexityConfig.from_dict(data)

        assert config.acl_role_count == 15
        assert config.acl_permission_density == PermissionDensity.SPARSE
        assert config.acl_hierarchy_depth == RoleHierarchyDepth.MODERATE


@pytest.mark.unit
class TestAclPresets:
    def test_acl_sparse_preset(self):

        config = get_complexity_preset("acl_sparse")
        assert config.acl_role_count == 5
        assert config.acl_permission_density == PermissionDensity.SPARSE
        assert config.acl_hierarchy_depth == RoleHierarchyDepth.FLAT

    def test_acl_moderate_preset(self):

        config = get_complexity_preset("acl_moderate")
        assert config.acl_role_count == 20
        assert config.acl_permission_density == PermissionDensity.MODERATE
        assert config.acl_hierarchy_depth == RoleHierarchyDepth.SHALLOW

    def test_acl_dense_preset(self):

        config = get_complexity_preset("acl_dense")
        assert config.acl_role_count == 50
        assert config.acl_permission_density == PermissionDensity.DENSE
        assert config.acl_grant_with_grant_option is True

    def test_acl_hierarchy_preset(self):

        config = get_complexity_preset("acl_hierarchy")
        assert config.acl_role_count == 30
        assert config.acl_hierarchy_depth == RoleHierarchyDepth.DEEP

    def test_acl_full_preset(self):

        config = get_complexity_preset("acl_full")
        assert config.acl_role_count == 30
        assert config.acl_column_grants is True
        assert config.acl_grant_with_grant_option is True


@pytest.mark.unit
class TestGeneratedMetadataWithAcl:
    def test_generated_metadata_with_roles(self):

        metadata = GeneratedMetadata(
            tables=["t1", "t2"],
            roles=["r1", "r2", "r3"],
        )
        assert metadata.total_objects == 5

    def test_generated_metadata_with_grants(self):

        from benchbox.core.metadata_primitives import AclGrant

        grants = [
            AclGrant("r1", "table", "t1", ["SELECT"]),
            AclGrant("r2", "table", "t1", ["SELECT", "INSERT"]),
        ]
        metadata = GeneratedMetadata(
            tables=["t1"],
            grants=grants,
        )
        assert metadata.total_grants == 2
        assert metadata.summary()["grants"] == 2

    def test_generated_metadata_summary_with_acl(self):

        from benchbox.core.metadata_primitives import AclGrant

        metadata = GeneratedMetadata(
            tables=["t1"],
            views=["v1"],
            roles=["r1"],
            grants=[AclGrant("r1", "table", "t1", ["SELECT"])],
        )
        summary = metadata.summary()

        assert summary["tables"] == 1
        assert summary["views"] == 1
        assert summary["roles"] == 1
        assert summary["grants"] == 1
        assert summary["total"] == 3


