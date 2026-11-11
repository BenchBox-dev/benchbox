# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.tsbs_devops.schema import (
    TABLE_ORDER,
    TSBS_DEVOPS_SCHEMA,
    get_create_tables_sql,
    get_metric_tables,
    get_table_columns,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSchemaDefinition:
    def test_schema_has_required_tables(self):
        required_tables = {"tags", "cpu", "mem", "disk", "net"}
        assert set(TSBS_DEVOPS_SCHEMA.keys()) == required_tables

    def test_table_order_matches_schema(self):
        assert set(TABLE_ORDER) == set(TSBS_DEVOPS_SCHEMA.keys())

    def test_table_order_has_tags_first(self):
        assert TABLE_ORDER[0] == "tags"

    def test_tags_table_schema(self):
        columns = TSBS_DEVOPS_SCHEMA["tags"]["columns"]
        expected_columns = {
            "hostname",
            "region",
            "datacenter",
            "rack",
            "os",
            "arch",
            "team",
            "service",
            "service_version",
            "service_environment",
        }
        assert set(columns.keys()) == expected_columns

    def test_cpu_table_schema(self):
        columns = TSBS_DEVOPS_SCHEMA["cpu"]["columns"]
        assert "time" in columns
        assert "hostname" in columns
        cpu_metrics = {"usage_user", "usage_system", "usage_idle", "usage_iowait"}
        assert cpu_metrics.issubset(set(columns.keys()))

    def test_mem_table_schema(self):
        columns = TSBS_DEVOPS_SCHEMA["mem"]["columns"]
        assert "time" in columns
        assert "hostname" in columns
        mem_metrics = {"total", "available", "used", "free", "cached", "buffered", "used_percent"}
        assert mem_metrics.issubset(set(columns.keys()))

    def test_disk_table_schema(self):
        columns = TSBS_DEVOPS_SCHEMA["disk"]["columns"]
        assert "time" in columns
        assert "hostname" in columns
        assert "device" in columns
        disk_metrics = {"reads_completed", "writes_completed", "read_time_ms", "write_time_ms"}
        assert disk_metrics.issubset(set(columns.keys()))

    def test_net_table_schema(self):
        columns = TSBS_DEVOPS_SCHEMA["net"]["columns"]
        assert "time" in columns
        assert "hostname" in columns
        assert "interface" in columns
        net_metrics = {"bytes_recv", "bytes_sent", "packets_recv", "packets_sent"}
        assert net_metrics.issubset(set(columns.keys()))

    def test_metric_tables_have_primary_key(self):
        for table_name in get_metric_tables():
            table_def = TSBS_DEVOPS_SCHEMA[table_name]
            assert "primary_key" in table_def
            assert len(table_def["primary_key"]) > 0

    def test_metric_tables_have_partition_by(self):
        for table_name in get_metric_tables():
            table_def = TSBS_DEVOPS_SCHEMA[table_name]
            assert table_def.get("partition_by") == "time"

    def test_column_types_are_valid(self):
        valid_types = {
            "TIMESTAMP",
            "VARCHAR(255)",
            "VARCHAR(64)",
            "VARCHAR(32)",
            "DOUBLE",
            "BIGINT",
            "INTEGER",
        }
        for table_name, table_def in TSBS_DEVOPS_SCHEMA.items():
            for col_name, col_spec in table_def["columns"].items():
                assert col_spec["type"] in valid_types, f"{table_name}.{col_name} has invalid type"


class TestGetMetricTables:
    def test_returns_metric_tables_only(self):
        metric_tables = get_metric_tables()
        assert "tags" not in metric_tables
        assert set(metric_tables) == {"cpu", "mem", "disk", "net"}

    def test_returns_list(self):
        result = get_metric_tables()
        assert isinstance(result, list)


class TestGetTableColumns:
    def test_returns_column_names(self):
        columns = get_table_columns("cpu")
        assert "time" in columns
        assert "hostname" in columns
        assert "usage_user" in columns

    def test_raises_for_unknown_table(self):
        with pytest.raises(ValueError, match="Unknown table"):
            get_table_columns("unknown_table")

    def test_all_tables_have_columns(self):
        for table_name in TABLE_ORDER:
            columns = get_table_columns(table_name)
            assert len(columns) > 0


class TestGetCreateTablesSql:
    def test_generates_standard_sql(self):
        sql = get_create_tables_sql(dialect="standard")
        assert isinstance(sql, str)
        assert len(sql) > 0
        for table_name in TABLE_ORDER:
            assert f"CREATE TABLE {table_name}" in sql

    def test_includes_primary_key_constraints(self):
        sql = get_create_tables_sql(dialect="standard", include_constraints=True)
        assert "PRIMARY KEY" in sql

    def test_excludes_constraints_when_disabled(self):
        sql = get_create_tables_sql(dialect="standard", include_constraints=False)
        assert "PRIMARY KEY" not in sql

    def test_generates_duckdb_sql(self):
        sql = get_create_tables_sql(dialect="duckdb")
        assert "CREATE TABLE" in sql
        assert "VARCHAR" in sql

    def test_generates_clickhouse_sql(self):
        sql = get_create_tables_sql(dialect="clickhouse")
        assert "ENGINE = MergeTree()" in sql
        assert "ORDER BY" in sql
        assert "String" in sql

    def test_generates_timescale_sql(self):
        sql = get_create_tables_sql(dialect="timescale")
        assert "create_hypertable" in sql
        assert "TIMESTAMPTZ" in sql

    def test_clickhouse_with_partitioning(self):
        sql = get_create_tables_sql(dialect="clickhouse", time_partitioning=True)
        assert "PARTITION BY" in sql

    def test_sql_order_is_correct(self):
        sql = get_create_tables_sql(dialect="standard")
        tags_pos = sql.find("CREATE TABLE tags")
        cpu_pos = sql.find("CREATE TABLE cpu")
        assert tags_pos < cpu_pos, "tags should be created before cpu"


class TestTypeMapping:
    def test_clickhouse_type_mapping(self):
        sql = get_create_tables_sql(dialect="clickhouse")
        assert "DateTime64(3)" in sql
        assert "Int64" in sql
        assert "Float64" in sql
        assert "String" in sql

    def test_duckdb_type_mapping(self):
        sql = get_create_tables_sql(dialect="duckdb")
        assert "TIMESTAMP" in sql
        assert "BIGINT" in sql
        assert "DOUBLE" in sql
        assert "VARCHAR" in sql

    def test_timescale_type_mapping(self):
        sql = get_create_tables_sql(dialect="timescale")
        assert "TIMESTAMPTZ" in sql
        assert "BIGINT" in sql
        assert "DOUBLE PRECISION" in sql
        assert "TEXT" in sql


