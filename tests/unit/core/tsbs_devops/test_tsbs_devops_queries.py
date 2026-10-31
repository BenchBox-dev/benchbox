# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import datetime
from random import Random

import pytest

from benchbox.core.tsbs_devops.queries import QUERIES, TSBSDevOpsQueryManager

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
            "single-host",
            "aggregation",
            "groupby",
            "threshold",
            "memory",
            "disk",
            "network",
            "combined",
            "lastpoint",
            "tags",
        }
        for query_id, query_def in QUERIES.items():
            assert query_def["category"] in valid_categories, (
                f"Query {query_id} has invalid category: {query_def['category']}"
            )

    def test_sql_contains_placeholder_parameters(self):
        for query_id, query_def in QUERIES.items():
            sql = query_def["sql"]
            if query_id != "lastpoint":
                assert "{start_time}" in sql or "{end_time}" in sql, f"Query {query_id} missing time parameters"


class TestQueryCategories:
    def test_single_host_queries(self):
        single_host = [q for q, d in QUERIES.items() if d["category"] == "single-host"]
        assert len(single_host) >= 2

    def test_aggregation_queries(self):
        aggregation = [q for q, d in QUERIES.items() if d["category"] == "aggregation"]
        assert len(aggregation) >= 2

    def test_threshold_queries(self):
        threshold = [q for q, d in QUERIES.items() if d["category"] == "threshold"]
        assert len(threshold) >= 3

    def test_groupby_queries(self):
        groupby = [q for q, d in QUERIES.items() if d["category"] == "groupby"]
        assert len(groupby) >= 2


class TestTSBSDevOpsQueryManager:
    @pytest.fixture
    def manager(self, seed, start_time):
        return TSBSDevOpsQueryManager(
            num_hosts=100,
            start_time=start_time,
            seed=seed,
        )

    def test_init_creates_hostnames(self, manager):
        assert len(manager.hostnames) == 100
        assert all(h.startswith("host_") for h in manager.hostnames)

    def test_init_with_custom_num_hosts(self, start_time, seed):
        manager = TSBSDevOpsQueryManager(num_hosts=50, start_time=start_time, seed=seed)
        assert len(manager.hostnames) == 50

    def test_init_default_start_time(self, seed):
        manager = TSBSDevOpsQueryManager(seed=seed)
        assert manager.start_time == datetime(2024, 1, 1)

    def test_get_query_returns_string(self, manager):
        query = manager.get_query("single-host-12-hr")
        assert isinstance(query, str)
        assert "SELECT" in query
        assert "FROM cpu" in query

    def test_get_query_fills_parameters(self, manager):
        query = manager.get_query("single-host-12-hr")
        assert "{" not in query
        assert "}" not in query
        assert "2024-01-01" in query

    def test_lastpoint_uses_joined_latest_rows(self, manager):
        query = manager.get_query("lastpoint")
        assert "JOIN (" in query
        assert "MAX(time) AS max_time" in query
        assert "ORDER BY c.hostname" in query
        assert "(hostname, time) IN" not in query

    @pytest.mark.parametrize("seed", [None, 0, 42])
    def test_rendering_is_independent_of_call_order(self, seed):
        manager = TSBSDevOpsQueryManager(seed=seed, duration_days=2)
        expected = manager.get_queries()
        query_ids = list(QUERIES)
        Random(17).shuffle(query_ids)

        for query_id in query_ids:
            assert manager.get_query(query_id) == expected[query_id]
            assert manager.get_query(query_id) == expected[query_id]
        assert manager.get_queries() == expected

    @pytest.mark.parametrize("seed", [0, 42])
    def test_seeded_instances_render_identically_in_different_orders(self, seed):
        expected = TSBSDevOpsQueryManager(seed=seed, duration_days=2).get_queries()
        manager = TSBSDevOpsQueryManager(seed=seed, duration_days=2)

        for query_id in reversed(QUERIES):
            assert manager.get_query(query_id) == expected[query_id]

    def test_overrides_do_not_change_subsequent_rendering(self, manager):
        query_id = "single-host-1-hr"
        expected = manager.get_query(query_id)
        overrides = {"hostname": "custom_host", "start_time": "2024-01-01 00:00:00"}
        expected_overrides = overrides.copy()

        overridden = manager.get_query(query_id, params=overrides)

        assert "custom_host" in overridden
        assert "2024-01-01 00:00:00" in overridden
        assert overrides == expected_overrides
        assert manager.get_query(query_id) == expected

    def test_get_query_with_param_override(self, manager):
        query = manager.get_query("single-host-12-hr", params={"hostname": "custom_host"})
        assert "custom_host" in query

    def test_get_query_unknown_raises(self, manager):
        with pytest.raises(ValueError, match="Unknown query"):
            manager.get_query("nonexistent_query")

    def test_get_query_error_shows_available(self, manager):
        with pytest.raises(ValueError) as exc_info:
            manager.get_query("bad_query")
        assert "single-host-12-hr" in str(exc_info.value)

    def test_get_queries_returns_all(self, manager):
        queries = manager.get_queries()
        assert len(queries) == len(QUERIES)
        assert all(isinstance(q, str) for q in queries.values())

    def test_get_query_info(self, manager):
        info = manager.get_query_info("cpu-max-all-1-hr")
        assert info["id"] == "3"
        assert info["category"] == "aggregation"
        assert "Maximum CPU" in info["description"]

    def test_get_query_info_unknown_raises(self, manager):
        with pytest.raises(ValueError, match="Unknown query"):
            manager.get_query_info("unknown")

    def test_get_queries_by_category(self, manager):
        single_host = manager.get_queries_by_category("single-host")
        assert "single-host-12-hr" in single_host
        assert "single-host-1-hr" in single_host
        for qid in single_host:
            assert QUERIES[qid]["category"] == "single-host"

    def test_get_queries_by_category_empty(self, manager):
        result = manager.get_queries_by_category("nonexistent")
        assert result == []

    def test_get_categories(self):
        categories = TSBSDevOpsQueryManager.get_categories()
        assert isinstance(categories, list)
        assert len(categories) > 0
        assert "single-host" in categories
        assert "aggregation" in categories
        assert "threshold" in categories

    def test_get_query_count(self):
        count = TSBSDevOpsQueryManager.get_query_count()
        assert count == len(QUERIES)


class TestQueryParameterGeneration:
    @pytest.fixture
    def manager(self, seed, start_time):
        return TSBSDevOpsQueryManager(num_hosts=100, start_time=start_time, seed=seed)

    def test_time_params_within_dataset(self, manager):
        for _ in range(10):
            query = manager.get_query("single-host-1-hr")
            assert "2024-01-01" in query

    def test_hostname_from_hostlist(self, manager):
        query = manager.get_query("single-host-12-hr")
        assert "host_" in query

    def test_region_param_for_tags_queries(self, manager):
        query = manager.get_query("by-region")
        valid_regions = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]
        assert any(r in query for r in valid_regions)

    def test_reproducible_with_seed(self, start_time):
        manager1 = TSBSDevOpsQueryManager(num_hosts=100, start_time=start_time, seed=42)
        manager2 = TSBSDevOpsQueryManager(num_hosts=100, start_time=start_time, seed=42)

        query1 = manager1.get_query("single-host-12-hr")
        query2 = manager2.get_query("single-host-12-hr")
        assert query1 == query2

    @pytest.mark.parametrize("seed", [None, 0, 42])
    def test_repeated_and_reordered_retrieval(self, start_time, seed):
        manager = TSBSDevOpsQueryManager(num_hosts=100, start_time=start_time, seed=seed)
        expected = manager.get_queries()

        for query_id in reversed(QUERIES):
            assert manager.get_query(query_id) == expected[query_id]
            assert manager.get_query(query_id) == expected[query_id]

        assert manager.get_queries() == expected
        if seed is not None:
            other = TSBSDevOpsQueryManager(num_hosts=100, start_time=start_time, seed=seed)
            assert {query_id: other.get_query(query_id) for query_id in reversed(QUERIES)} == expected

    @pytest.mark.parametrize("query_id", ["single-host-12-hr", "by-region"])
    def test_overrides_are_stable_and_do_not_change_defaults(self, manager, query_id):
        expected = manager.get_queries()
        overrides = {
            "hostname": "custom_host",
            "region": "custom_region",
            "start_time": "2024-01-01 02:00:00",
            "end_time": "2024-01-01 03:00:00",
        }
        original = overrides.copy()
        query = manager.get_query(query_id, params=overrides)

        assert query == QUERIES[query_id]["sql"].strip().format(**overrides)
        manager.get_query("cpu-max-all-1-hr")
        assert manager.get_query(query_id, params=overrides) == query
        assert overrides == original
        assert manager.get_queries() == expected

    def test_partial_override_preserves_other_defaults(self, manager):
        expected = manager.get_query("single-host-12-hr")
        hostname = expected.split("hostname = '", 1)[1].split("'", 1)[0]
        assert manager.get_query("single-host-12-hr", params={"hostname": "custom_host"}) == expected.replace(
            f"hostname = '{hostname}'", "hostname = 'custom_host'"
        )

    def test_different_seed_different_params(self, start_time):
        manager1 = TSBSDevOpsQueryManager(num_hosts=100, start_time=start_time, seed=42)
        manager2 = TSBSDevOpsQueryManager(num_hosts=100, start_time=start_time, seed=123)

        query1 = manager1.get_query("single-host-12-hr")
        query2 = manager2.get_query("single-host-12-hr")
        assert "SELECT" in query1
        assert "SELECT" in query2


class TestQuerySQLValidity:
    @pytest.fixture
    def manager(self, seed, start_time):
        return TSBSDevOpsQueryManager(num_hosts=100, start_time=start_time, seed=seed)

    def test_all_queries_have_select(self, manager):
        for query_id in QUERIES:
            query = manager.get_query(query_id)
            assert "SELECT" in query.upper()

    def test_all_queries_have_from(self, manager):
        for query_id in QUERIES:
            query = manager.get_query(query_id)
            assert "FROM" in query.upper()

    def test_time_filtered_queries_have_where(self, manager):
        for query_id in QUERIES:
            if "time" in QUERIES[query_id]["sql"]:
                query = manager.get_query(query_id)
                assert "WHERE" in query.upper()

    def test_aggregation_queries_have_group_by(self, manager):
        aggregation_queries = [q for q, d in QUERIES.items() if d["category"] == "aggregation"]
        for query_id in aggregation_queries:
            query = manager.get_query(query_id)
            assert "GROUP BY" in query.upper()

    def test_join_queries_have_join(self, manager):
        join_categories = {"combined", "tags"}
        for query_id, query_def in QUERIES.items():
            if query_def["category"] in join_categories:
                query = manager.get_query(query_id)
                assert "JOIN" in query.upper()


class TestResourceUtilizationJoinBounds:
    @pytest.fixture
    def sqlite_connection(self):
        import sqlite3

        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE cpu (time TEXT, hostname TEXT, usage_user REAL, usage_system REAL)")
        connection.execute("CREATE TABLE mem (time TEXT, hostname TEXT, used_percent REAL)")
        connection.execute("CREATE INDEX idx_mem_host_time ON mem (hostname, time)")
        connection.execute("CREATE INDEX idx_cpu_host_time ON cpu (hostname, time)")
        for minute in range(180):
            stamp = f"2024-01-01 {minute // 60:02d}:{minute % 60:02d}:00"
            connection.execute("INSERT INTO cpu VALUES (?, 'host_0', 10.0, 5.0)", (stamp,))
            connection.execute("INSERT INTO mem VALUES (?, 'host_0', 40.0)", (stamp,))
        return connection

    @pytest.fixture
    def rendered_sqlite(self):
        from benchbox.core.tsbs_devops.benchmark import TSBSDevOpsBenchmark

        benchmark = TSBSDevOpsBenchmark(scale_factor=0.01)
        return benchmark.get_query(
            "resource-utilization",
            params={"start_time": "2024-01-01 01:00:00", "end_time": "2024-01-01 02:00:00"},
            dialect="sqlite",
        )

    def test_memory_side_is_bounded_to_the_query_window(self):
        manager = TSBSDevOpsQueryManager(num_hosts=1, start_time=datetime(2024, 1, 1), duration_days=1, seed=1)
        sql = manager.get_query(
            "resource-utilization", {"start_time": "2024-01-01 01:00:00", "end_time": "2024-01-01 02:00:00"}
        )
        assert "m.time >= '2024-01-01 01:00:00'" in sql
        assert "m.time < '2024-01-01 02:00:00'" in sql

    def test_memory_bounds_cover_whole_minutes_of_a_partial_minute_window(self):
        manager = TSBSDevOpsQueryManager(num_hosts=1, start_time=datetime(2024, 1, 1), duration_days=1, seed=1)
        sql = manager.get_query(
            "resource-utilization", {"start_time": "2024-01-01 01:00:30", "end_time": "2024-01-01 02:00:30"}
        )
        assert "m.time >= '2024-01-01 01:00:00'" in sql
        assert "m.time < '2024-01-01 02:01:00'" in sql

    def test_partial_minute_window_keeps_same_minute_pairs(self, sqlite_connection):
        from benchbox.core.tsbs_devops.benchmark import TSBSDevOpsBenchmark

        sqlite_connection.execute("DELETE FROM cpu")
        sqlite_connection.execute("DELETE FROM mem")
        sqlite_connection.execute("INSERT INTO cpu VALUES ('2024-01-01 01:00:45', 'host_0', 10.0, 5.0)")
        sqlite_connection.execute("INSERT INTO mem VALUES ('2024-01-01 01:00:10', 'host_0', 70.0)")
        sql = TSBSDevOpsBenchmark(scale_factor=0.01).get_query(
            "resource-utilization",
            params={"start_time": "2024-01-01 01:00:30", "end_time": "2024-01-01 02:00:30"},
            dialect="sqlite",
        )
        assert sqlite_connection.execute(sql).fetchall() == [("host_0", 15.0, 70.0)]

    def test_sqlite_plan_searches_memory_by_host_and_time_range(self, sqlite_connection, rendered_sqlite):
        plan = " ".join(row[3] for row in sqlite_connection.execute("EXPLAIN QUERY PLAN " + rendered_sqlite))
        assert "SEARCH m USING INDEX idx_mem_host_time (hostname=? AND time>? AND time<?)" in plan

    def test_window_bound_keeps_the_joined_rows(self, sqlite_connection, rendered_sqlite):
        rows = sqlite_connection.execute(rendered_sqlite).fetchall()
        assert rows == [("host_0", 15.0, 40.0)]
