# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.nyctaxi.schema import (
    NYC_TAXI_SCHEMA,
    TABLE_ORDER,
    get_create_tables_sql,
    get_table_columns,
    get_trips_columns,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSchemaDefinition:
    def test_schema_has_required_tables(self):
        required_tables = {"trips", "taxi_zones", "green_trips", "hvfhv_trips"}
        assert required_tables.issubset(set(NYC_TAXI_SCHEMA.keys()))

    def test_table_order_matches_schema(self):
        assert set(TABLE_ORDER) == set(NYC_TAXI_SCHEMA.keys())

    def test_table_order_has_zones_first(self):
        assert TABLE_ORDER[0] == "taxi_zones"

    def test_trips_table_schema(self):
        columns = NYC_TAXI_SCHEMA["trips"]["columns"]
        required_columns = {
            "trip_id",
            "vendor_id",
            "pickup_datetime",
            "dropoff_datetime",
            "passenger_count",
            "trip_distance",
            "pickup_location_id",
            "dropoff_location_id",
            "fare_amount",
            "tip_amount",
            "total_amount",
        }
        assert required_columns.issubset(set(columns.keys()))

    def test_taxi_zones_table_schema(self):
        columns = NYC_TAXI_SCHEMA["taxi_zones"]["columns"]
        expected_columns = {"location_id", "borough", "zone", "service_zone"}
        assert set(columns.keys()) == expected_columns

    def test_trips_table_has_primary_key(self):
        assert "primary_key" in NYC_TAXI_SCHEMA["trips"]
        assert NYC_TAXI_SCHEMA["trips"]["primary_key"] == ["trip_id"]

    def test_taxi_zones_has_primary_key(self):
        assert "primary_key" in NYC_TAXI_SCHEMA["taxi_zones"]
        assert NYC_TAXI_SCHEMA["taxi_zones"]["primary_key"] == ["location_id"]


class TestGetTableColumns:
    def test_returns_column_names(self):
        columns = get_table_columns("trips")
        assert "trip_id" in columns
        assert "pickup_datetime" in columns
        assert "total_amount" in columns

    def test_raises_for_unknown_table(self):
        with pytest.raises(ValueError, match="Unknown table"):
            get_table_columns("unknown_table")

    def test_all_tables_have_columns(self):
        for table_name in TABLE_ORDER:
            columns = get_table_columns(table_name)
            assert len(columns) > 0


class TestGetTripsColumns:
    def test_excludes_trip_id(self):
        columns = get_trips_columns()
        assert "trip_id" not in columns

    def test_includes_data_columns(self):
        columns = get_trips_columns()
        assert "vendor_id" in columns
        assert "pickup_datetime" in columns
        assert "total_amount" in columns


class TestGetCreateTablesSql:
    def test_generates_standard_sql(self):
        sql = get_create_tables_sql(dialect="standard")
        assert isinstance(sql, str)
        assert len(sql) > 0
        assert "CREATE TABLE taxi_zones" in sql
        assert "CREATE TABLE trips" in sql

    def test_includes_primary_key_constraints(self):
        sql = get_create_tables_sql(dialect="standard", include_constraints=True)
        assert "PRIMARY KEY" in sql

    def test_excludes_constraints_when_disabled(self):
        sql = get_create_tables_sql(dialect="standard", include_constraints=False)
        assert "PRIMARY KEY" not in sql

    def test_generates_duckdb_sql(self):
        sql = get_create_tables_sql(dialect="duckdb")
        assert "CREATE TABLE" in sql

    def test_generates_clickhouse_sql(self):
        sql = get_create_tables_sql(dialect="clickhouse")
        assert "ENGINE = MergeTree()" in sql
        assert "ORDER BY" in sql

    def test_generates_postgres_sql(self):
        sql = get_create_tables_sql(dialect="postgres")
        assert "CREATE TABLE" in sql
        assert "TIMESTAMPTZ" in sql

    def test_clickhouse_with_partitioning(self):
        sql = get_create_tables_sql(dialect="clickhouse", time_partitioning=True)
        assert "PARTITION BY" in sql

    def test_sql_order_is_correct(self):
        sql = get_create_tables_sql(dialect="standard")
        zones_pos = sql.find("CREATE TABLE taxi_zones")
        trips_pos = sql.find("CREATE TABLE trips")
        assert zones_pos < trips_pos


class TestTypeMapping:
    def test_clickhouse_type_mapping(self):
        sql = get_create_tables_sql(dialect="clickhouse")
        assert "DateTime64(3)" in sql
        assert "Int64" in sql or "Int32" in sql
        assert "Float64" in sql

    def test_duckdb_type_mapping(self):
        sql = get_create_tables_sql(dialect="duckdb")
        assert "TIMESTAMP" in sql
        assert "BIGINT" in sql
        assert "DOUBLE" in sql

    def test_postgres_type_mapping(self):
        sql = get_create_tables_sql(dialect="postgres")
        assert "TIMESTAMPTZ" in sql
        assert "DOUBLE PRECISION" in sql


