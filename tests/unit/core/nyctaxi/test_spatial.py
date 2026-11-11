# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.nyctaxi.spatial import (
    CLICKHOUSE_SPATIAL_QUERIES,
    DUCKDB_SPATIAL_QUERIES,
    POSTGIS_SPATIAL_QUERIES,
    SPATIAL_SCHEMA_EXTENSION,
    TAXI_ZONE_CENTROIDS,
    check_spatial_support,
    get_all_spatial_queries,
    get_spatial_create_table_sql,
    get_spatial_queries,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTaxiZoneCentroids:
    def test_centroids_not_empty(self):

        assert len(TAXI_ZONE_CENTROIDS) > 0

    def test_centroids_have_valid_coordinates(self):

        for location_id, (lon, lat) in TAXI_ZONE_CENTROIDS.items():
            assert isinstance(location_id, int)
            assert -74.5 <= lon <= -73.5, f"Invalid longitude for zone {location_id}: {lon}"
            assert 40.4 <= lat <= 41.0, f"Invalid latitude for zone {location_id}: {lat}"

    def test_key_zones_present(self):

        key_zones = [
            1,
            132,
            138,
            161,
            230,
        ]
        for zone_id in key_zones:
            assert zone_id in TAXI_ZONE_CENTROIDS, f"Missing centroid for zone {zone_id}"

    def test_airport_zones_distinct(self):

        airports = [1, 132, 138]
        centroids = [TAXI_ZONE_CENTROIDS[z] for z in airports if z in TAXI_ZONE_CENTROIDS]

        assert len(centroids) == len(airports)

        unique_centroids = set(centroids)
        assert len(unique_centroids) == len(centroids)


class TestSpatialSchemaExtension:
    def test_schema_has_spatial_table(self):

        assert "taxi_zones_spatial" in SPATIAL_SCHEMA_EXTENSION

    def test_spatial_table_has_required_columns(self):
        table_def = SPATIAL_SCHEMA_EXTENSION["taxi_zones_spatial"]
        columns = table_def["columns"]

        required = ["location_id", "borough", "zone", "service_zone", "centroid_lon", "centroid_lat"]
        for col in required:
            assert col in columns, f"Missing column: {col}"

    def test_spatial_table_has_primary_key(self):

        table_def = SPATIAL_SCHEMA_EXTENSION["taxi_zones_spatial"]
        assert "primary_key" in table_def
        assert "location_id" in table_def["primary_key"]


class TestDuckDBSpatialQueries:
    def test_has_queries(self):

        assert len(DUCKDB_SPATIAL_QUERIES) >= 10

    def test_all_queries_have_required_fields(self):
        required_fields = ["id", "name", "description", "category", "platform", "sql"]

        for query_name, query_def in DUCKDB_SPATIAL_QUERIES.items():
            for field in required_fields:
                assert field in query_def, f"Query {query_name} missing field: {field}"

    def test_all_queries_target_duckdb(self):

        for query_name, query_def in DUCKDB_SPATIAL_QUERIES.items():
            assert query_def["platform"] == "duckdb", f"Query {query_name} has wrong platform"

    def test_queries_use_st_functions(self):

        spatial_functions = ["ST_Distance", "ST_Point", "ST_Centroid", "ST_Collect"]

        found_spatial = False
        for query_def in DUCKDB_SPATIAL_QUERIES.values():
            sql = query_def["sql"]
            if any(func in sql for func in spatial_functions):
                found_spatial = True
                break

        assert found_spatial, "No spatial functions found in DuckDB queries"

    def test_queries_have_unique_ids(self):

        ids = [q["id"] for q in DUCKDB_SPATIAL_QUERIES.values()]
        assert len(ids) == len(set(ids)), "Duplicate query IDs found"

    def test_distance_query_exists(self):

        assert "spatial-distance-top-routes" in DUCKDB_SPATIAL_QUERIES

    def test_radius_search_query_exists(self):

        assert "spatial-radius-search" in DUCKDB_SPATIAL_QUERIES


class TestPostGISSpatialQueries:
    def test_has_queries(self):

        assert len(POSTGIS_SPATIAL_QUERIES) >= 3

    def test_all_queries_have_required_fields(self):
        required_fields = ["id", "name", "description", "category", "platform", "sql"]

        for query_name, query_def in POSTGIS_SPATIAL_QUERIES.items():
            for field in required_fields:
                assert field in query_def, f"Query {query_name} missing field: {field}"

    def test_all_queries_target_postgres(self):

        for query_name, query_def in POSTGIS_SPATIAL_QUERIES.items():
            assert query_def["platform"] == "postgres", f"Query {query_name} has wrong platform"

    def test_queries_use_postgis_functions(self):

        postgis_functions = ["ST_SetSRID", "ST_MakePoint", "::geography", "ST_DWithin"]

        found_postgis = False
        for query_def in POSTGIS_SPATIAL_QUERIES.values():
            sql = query_def["sql"]
            if any(func in sql for func in postgis_functions):
                found_postgis = True
                break

        assert found_postgis, "No PostGIS-specific functions found"

    def test_queries_specify_srid(self):
        for query_name, query_def in POSTGIS_SPATIAL_QUERIES.items():
            sql = query_def["sql"]
            if "ST_SetSRID" in sql:
                assert "4326" in sql, f"Query {query_name} should use SRID 4326"


class TestClickHouseSpatialQueries:
    def test_has_queries(self):

        assert len(CLICKHOUSE_SPATIAL_QUERIES) >= 3

    def test_all_queries_have_required_fields(self):
        required_fields = ["id", "name", "description", "category", "platform", "sql"]

        for query_name, query_def in CLICKHOUSE_SPATIAL_QUERIES.items():
            for field in required_fields:
                assert field in query_def, f"Query {query_name} missing field: {field}"

    def test_all_queries_target_clickhouse(self):

        for query_name, query_def in CLICKHOUSE_SPATIAL_QUERIES.items():
            assert query_def["platform"] == "clickhouse", f"Query {query_name} has wrong platform"

    def test_queries_use_clickhouse_geo_functions(self):

        ch_functions = ["geoDistance", "geohashEncode", "geoToH3", "greatCircleDistance"]

        found_ch_geo = False
        for query_def in CLICKHOUSE_SPATIAL_QUERIES.values():
            sql = query_def["sql"]
            if any(func in sql for func in ch_functions):
                found_ch_geo = True
                break

        assert found_ch_geo, "No ClickHouse geo functions found"

    def test_h3_query_exists(self):
        assert "spatial-h3-aggregation" in CLICKHOUSE_SPATIAL_QUERIES


class TestGetSpatialQueries:
    def test_returns_duckdb_queries(self):

        queries = get_spatial_queries("duckdb")
        assert len(queries) > 0
        assert queries == DUCKDB_SPATIAL_QUERIES

    def test_returns_postgres_queries(self):

        for platform in ["postgres", "postgresql", "postgis"]:
            queries = get_spatial_queries(platform)
            assert len(queries) > 0
            assert queries == POSTGIS_SPATIAL_QUERIES

    def test_returns_clickhouse_queries(self):

        queries = get_spatial_queries("clickhouse")
        assert len(queries) > 0
        assert queries == CLICKHOUSE_SPATIAL_QUERIES

    def test_returns_empty_for_unsupported(self):

        queries = get_spatial_queries("sqlite")
        assert queries == {}

    def test_case_insensitive(self):

        assert get_spatial_queries("DuckDB") == get_spatial_queries("duckdb")
        assert get_spatial_queries("CLICKHOUSE") == get_spatial_queries("clickhouse")


class TestGetAllSpatialQueries:
    def test_returns_all_platforms(self):

        all_queries = get_all_spatial_queries()

        assert "duckdb" in all_queries
        assert "postgres" in all_queries
        assert "clickhouse" in all_queries

    def test_platform_queries_not_empty(self):

        all_queries = get_all_spatial_queries()

        for platform, queries in all_queries.items():
            assert len(queries) > 0, f"Platform {platform} has no queries"


class TestGetSpatialCreateTableSql:
    def test_duckdb_sql_valid(self):

        sql = get_spatial_create_table_sql("duckdb")

        assert "CREATE TABLE taxi_zones_spatial" in sql
        assert "location_id INTEGER" in sql
        assert "centroid_lon DOUBLE" in sql
        assert "centroid_lat DOUBLE" in sql

    def test_postgres_sql_valid(self):

        sql = get_spatial_create_table_sql("postgres")

        assert "CREATE TABLE taxi_zones_spatial" in sql
        assert "GEOMETRY(POINT, 4326)" in sql
        assert "CREATE INDEX" in sql
        assert "GIST" in sql

    def test_clickhouse_sql_valid(self):

        sql = get_spatial_create_table_sql("clickhouse")

        assert "CREATE TABLE taxi_zones_spatial" in sql
        assert "MergeTree()" in sql
        assert "Float64" in sql

    def test_standard_sql_fallback(self):

        sql = get_spatial_create_table_sql("unknown")

        assert "CREATE TABLE taxi_zones_spatial" in sql
        assert "DOUBLE" in sql


class TestCheckSpatialSupport:
    def test_duckdb_support(self):

        support = check_spatial_support("duckdb")

        assert support["basic_spatial"] is True
        assert support["st_distance"] is True
        assert support["st_point"] is True
        assert support["geohash"] is False

    def test_postgres_support(self):

        support = check_spatial_support("postgres")

        assert support["basic_spatial"] is True
        assert support["st_distance"] is True
        assert support["geography"] is True
        assert support["st_convexhull"] is True

    def test_clickhouse_support(self):

        support = check_spatial_support("clickhouse")

        assert support["basic_spatial"] is True
        assert support["geo_distance"] is True
        assert support["geohash"] is True
        assert support["h3"] is True
        assert support["st_distance"] is False

    def test_unsupported_platform(self):

        support = check_spatial_support("sqlite")

        assert support["basic_spatial"] is False

    def test_case_insensitive(self):

        assert check_spatial_support("DuckDB") == check_spatial_support("duckdb")


class TestQueryCategories:
    def test_all_queries_have_spatial_category(self):

        for platform_queries in [DUCKDB_SPATIAL_QUERIES, POSTGIS_SPATIAL_QUERIES, CLICKHOUSE_SPATIAL_QUERIES]:
            for query_name, query_def in platform_queries.items():
                assert query_def["category"] == "spatial", f"Query {query_name} should have spatial category"

    def test_queries_have_params(self):

        for platform_queries in [DUCKDB_SPATIAL_QUERIES, POSTGIS_SPATIAL_QUERIES, CLICKHOUSE_SPATIAL_QUERIES]:
            for query_name, query_def in platform_queries.items():
                assert "params" in query_def, f"Query {query_name} missing params"


class TestQuerySQLValidity:
    def test_queries_have_from_clause(self):

        for platform_queries in [DUCKDB_SPATIAL_QUERIES, POSTGIS_SPATIAL_QUERIES, CLICKHOUSE_SPATIAL_QUERIES]:
            for query_name, query_def in platform_queries.items():
                sql = query_def["sql"].upper()
                assert "FROM" in sql, f"Query {query_name} missing FROM clause"

    def test_queries_reference_trips_table(self):
        for platform_queries in [DUCKDB_SPATIAL_QUERIES, POSTGIS_SPATIAL_QUERIES, CLICKHOUSE_SPATIAL_QUERIES]:
            for query_name, query_def in platform_queries.items():
                sql = query_def["sql"].lower()
                assert "trips" in sql, f"Query {query_name} should reference trips table"

    def test_queries_reference_spatial_zones(self):
        for platform_queries in [DUCKDB_SPATIAL_QUERIES, POSTGIS_SPATIAL_QUERIES, CLICKHOUSE_SPATIAL_QUERIES]:
            for query_name, query_def in platform_queries.items():
                sql = query_def["sql"].lower()
                assert "taxi_zones_spatial" in sql, f"Query {query_name} should reference taxi_zones_spatial"

    def test_queries_have_date_placeholders(self):

        for platform_queries in [DUCKDB_SPATIAL_QUERIES, POSTGIS_SPATIAL_QUERIES, CLICKHOUSE_SPATIAL_QUERIES]:
            for query_name, query_def in platform_queries.items():
                if query_def.get("params", {}).get("duration_days"):
                    sql = query_def["sql"]
                    assert "{start_date}" in sql or "{end_date}" in sql, (
                        f"Query {query_name} should have date placeholders"
                    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


