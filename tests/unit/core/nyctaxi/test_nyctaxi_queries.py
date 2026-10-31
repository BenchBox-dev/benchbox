# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import datetime

import pytest

from benchbox.core.nyctaxi.queries import QUERIES, NYCTaxiQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueriesDefinition:
    def test_queries_not_empty(self):
        assert len(QUERIES) > 0, "QUERIES should not be empty"

    def test_all_queries_have_required_fields(self):
        required_fields = {"id", "name", "description", "category", "sql"}
        for query_id, query_def in QUERIES.items():
            missing = required_fields - set(query_def.keys())
            assert not missing, f"Query {query_id} missing fields: {missing}"

    def test_all_queries_have_unique_ids(self):
        ids = [qdef["id"] for qdef in QUERIES.values()]
        assert len(ids) == len(set(ids)), "Duplicate query IDs found"

    def test_all_queries_have_valid_categories(self):
        valid_categories = {
            "temporal",
            "geographic",
            "financial",
            "characteristics",
            "rates",
            "vendor",
            "complex",
            "point",
            "baseline",
        }
        for query_id, query_def in QUERIES.items():
            assert query_def["category"] in valid_categories, (
                f"Query {query_id} has invalid category: {query_def['category']}"
            )


class TestQueryCategories:
    def test_temporal_queries(self):
        temporal = [q for q, d in QUERIES.items() if d["category"] == "temporal"]
        assert len(temporal) >= 3

    def test_geographic_queries(self):
        geographic = [q for q, d in QUERIES.items() if d["category"] == "geographic"]
        assert len(geographic) >= 4

    def test_financial_queries(self):
        financial = [q for q, d in QUERIES.items() if d["category"] == "financial"]
        assert len(financial) >= 3

    def test_complex_queries(self):
        complex_queries = [q for q, d in QUERIES.items() if d["category"] == "complex"]
        assert len(complex_queries) >= 3


class TestNYCTaxiQueryManager:
    @pytest.fixture
    def manager(self, seed, start_date, end_date):
        return NYCTaxiQueryManager(
            start_date=start_date,
            end_date=end_date,
            seed=seed,
        )

    def test_init_default_dates(self, seed):
        manager = NYCTaxiQueryManager(seed=seed)
        assert manager.start_date == datetime(2019, 1, 1)
        assert manager.end_date == datetime(2019, 12, 31)

    def test_init_custom_dates(self, start_date, end_date, seed):
        manager = NYCTaxiQueryManager(
            start_date=start_date,
            end_date=end_date,
            seed=seed,
        )
        assert manager.start_date == start_date
        assert manager.end_date == end_date

    def test_get_query_returns_string(self, manager):
        query = manager.get_query("trips-per-hour")
        assert isinstance(query, str)
        assert "SELECT" in query
        assert "FROM trips" in query

    def test_get_query_fills_parameters(self, manager):
        query = manager.get_query("trips-per-hour")
        assert "{" not in query
        assert "}" not in query
        assert "2019" in query

    def test_get_query_with_param_override(self, manager):
        query = manager.get_query("zone-detail", params={"zone_id": 161})
        assert "161" in query

    def test_get_query_unknown_raises(self, manager):
        with pytest.raises(ValueError, match="Unknown query"):
            manager.get_query("nonexistent_query")

    def test_get_query_error_shows_available(self, manager):
        with pytest.raises(ValueError) as exc_info:
            manager.get_query("bad_query")
        assert "trips-per-hour" in str(exc_info.value)

    def test_get_queries_returns_all(self, manager):
        queries = manager.get_queries()
        assert len(queries) == len(QUERIES)
        assert all(isinstance(q, str) for q in queries.values())

    def test_get_query_info(self, manager):
        info = manager.get_query_info("trips-per-hour")
        assert info["id"] == "1"
        assert info["category"] == "temporal"
        assert "description" in info

    def test_get_query_info_unknown_raises(self, manager):
        with pytest.raises(ValueError, match="Unknown query"):
            manager.get_query_info("unknown")

    def test_get_queries_by_category(self, manager):
        temporal = manager.get_queries_by_category("temporal")
        assert "trips-per-hour" in temporal
        for qid in temporal:
            assert QUERIES[qid]["category"] == "temporal"

    def test_get_queries_by_category_empty(self, manager):
        result = manager.get_queries_by_category("nonexistent")
        assert result == []

    def test_get_categories(self):
        manager = NYCTaxiQueryManager()
        categories = manager.get_categories()
        assert isinstance(categories, list)
        assert len(categories) > 0
        assert "temporal" in categories
        assert "geographic" in categories

    def test_get_query_count(self):
        manager = NYCTaxiQueryManager()
        count = manager.get_query_count()
        assert count == len(QUERIES)


class TestQueryParameterGeneration:
    @pytest.fixture
    def manager(self, seed, start_date, end_date):
        return NYCTaxiQueryManager(start_date=start_date, end_date=end_date, seed=seed)

    def test_date_params_within_dataset(self, manager):
        for _ in range(10):
            query = manager.get_query("trips-per-hour")
            assert "2019" in query

    def test_zone_id_from_popular_zones(self, manager):
        query = manager.get_query("zone-detail")
        assert "pickup_location_id =" in query

    def test_reproducible_with_seed(self, start_date, end_date):
        manager1 = NYCTaxiQueryManager(start_date=start_date, end_date=end_date, seed=42)
        manager2 = NYCTaxiQueryManager(start_date=start_date, end_date=end_date, seed=42)

        query1 = manager1.get_query("trips-per-hour")
        query2 = manager2.get_query("trips-per-hour")
        assert query1 == query2

    def test_repeated_renders_on_one_instance_are_identical(self, start_date, end_date):
        manager = NYCTaxiQueryManager(start_date=start_date, end_date=end_date, seed=42)
        first = manager.get_queries()
        manager.get_query("zone-detail")
        assert manager.get_queries() == first

    def test_unseeded_instance_renders_are_stable(self, start_date, end_date):
        manager = NYCTaxiQueryManager(start_date=start_date, end_date=end_date)
        assert manager.get_queries() == manager.get_queries()

    def test_render_is_independent_of_call_order(self, start_date, end_date):
        forward = NYCTaxiQueryManager(start_date=start_date, end_date=end_date, seed=7)
        backward = NYCTaxiQueryManager(start_date=start_date, end_date=end_date, seed=7)
        ids = list(forward.get_queries())
        expected = {qid: forward.get_query(qid) for qid in ids}
        assert {qid: backward.get_query(qid) for qid in reversed(ids)} == expected


class TestQuerySQLValidity:
    @pytest.fixture
    def manager(self, seed, start_date, end_date):
        return NYCTaxiQueryManager(start_date=start_date, end_date=end_date, seed=seed)

    def test_all_queries_have_select(self, manager):
        for query_id in QUERIES:
            query = manager.get_query(query_id)
            assert "SELECT" in query.upper()

    def test_all_queries_have_from(self, manager):
        for query_id in QUERIES:
            query = manager.get_query(query_id)
            assert "FROM" in query.upper()

    def test_temporal_queries_have_group_by(self, manager):
        temporal_queries = [q for q, d in QUERIES.items() if d["category"] == "temporal"]
        for query_id in temporal_queries:
            query = manager.get_query(query_id)
            assert "GROUP BY" in query.upper()

    def test_geographic_queries_use_joins(self, manager):
        geographic_queries = [q for q, d in QUERIES.items() if d["category"] == "geographic"]
        for query_id in geographic_queries:
            query = manager.get_query(query_id)
            assert "JOIN" in query.upper() or "taxi_zones" in query.lower()


class TestCloudTranslation:
    @pytest.fixture
    def queries_by_dialect(self):
        import tempfile

        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

        with tempfile.TemporaryDirectory() as tmp:
            bm = NYCTaxiBenchmark(scale_factor=0.1, output_dir=tmp)
            return {
                d: bm.get_queries(dialect=d) for d in ("bigquery", "snowflake", "databricks", "sqlite", "singlestore")
            }

    def test_no_dow_or_epoch_residuals(self, queries_by_dialect):
        for dialect, queries in queries_by_dialect.items():
            for qid, sql in queries.items():
                assert "DOW" not in sql, (dialect, qid)
                assert "EPOCH" not in sql, (dialect, qid)

    def test_bigquery_dayofweek_preserves_sunday_zero(self, queries_by_dialect):
        sql = queries_by_dialect["bigquery"]["trips-by-day-of-week"]
        assert "EXTRACT(DAYOFWEEK" in sql
        assert "- 1" in sql

    def test_bigquery_epoch_uses_unix_seconds(self, queries_by_dialect):
        sql = queries_by_dialect["bigquery"]["trip-duration-analysis"]
        assert "UNIX_SECONDS(TIMESTAMP(" in sql

    def test_snowflake_epoch_uses_datediff(self, queries_by_dialect):
        sql = queries_by_dialect["snowflake"]["trip-duration-analysis"]
        assert "DATEDIFF(second," in sql

    def test_databricks_dow_uses_dayofweek(self, queries_by_dialect):
        sql = queries_by_dialect["databricks"]["trips-by-day-of-week"]
        assert "DAYOFWEEK(" in sql
        assert "- 1" in sql

    def test_singlestore_dow_uses_dayofweek(self, queries_by_dialect):
        for qid in ("trips-by-day-of-week", "weekday-weekend-comparison"):
            sql = queries_by_dialect["singlestore"][qid]
            assert "DAYOFWEEK(" in sql.upper(), qid
            assert "- 1" in sql, qid

    def test_sqlite_dow_uses_strftime(self, queries_by_dialect):
        sql = queries_by_dialect["sqlite"]["trips-by-day-of-week"]
        assert "STRFTIME('%w'," in sql
        assert "EXTRACT(DOW" not in sql.upper()

    def test_sqlite_epoch_uses_strftime_seconds(self, queries_by_dialect):
        sql = queries_by_dialect["sqlite"]["trip-duration-analysis"]
        assert "STRFTIME('%s'," in sql
        assert "EXTRACT(EPOCH" not in sql.upper()

    def test_sqlite_translated_queries_execute(self, queries_by_dialect):
        import sqlite3

        from benchbox.core.nyctaxi.schema import get_create_tables_sql

        conn = sqlite3.connect(":memory:")
        conn.executescript(get_create_tables_sql(dialect="sqlite"))
        conn.execute(
            "INSERT INTO trips (pickup_datetime, dropoff_datetime, trip_distance, total_amount) "
            "VALUES ('2019-01-05 08:15:00', '2019-01-05 08:45:30', 2.5, 12.0)"
        )
        for qid in (
            "trips-by-day-of-week",
            "trip-duration-analysis",
            "weekday-weekend-comparison",
            "rush-hour-analysis",
        ):
            conn.execute(queries_by_dialect["sqlite"][qid]).fetchall()
