# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.dataframe.query import QueryCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_QUERY_IDS = [f"Q{i}" for i in range(1, 19)]


class TestTSBSDevOpsQueryRegistry:
    def test_registry_imports_successfully(self):
        from benchbox.core.tsbs_devops.dataframe_queries import TSBS_DEVOPS_DATAFRAME_QUERIES

        assert len(TSBS_DEVOPS_DATAFRAME_QUERIES) > 0
        assert hasattr(TSBS_DEVOPS_DATAFRAME_QUERIES, "get_query_ids")

    def test_registry_has_18_queries(self):
        from benchbox.core.tsbs_devops.dataframe_queries import TSBS_DEVOPS_DATAFRAME_QUERIES

        queries = TSBS_DEVOPS_DATAFRAME_QUERIES.get_all_queries()
        assert len(queries) == 18

    def test_registry_benchmark_name(self):
        from benchbox.core.tsbs_devops.dataframe_queries import TSBS_DEVOPS_DATAFRAME_QUERIES

        assert TSBS_DEVOPS_DATAFRAME_QUERIES.benchmark == "tsbs_devops"

    def test_get_query_by_id(self):
        from benchbox.core.tsbs_devops.dataframe_queries import get_tsbs_devops_query

        query = get_tsbs_devops_query("Q1")
        assert query is not None
        assert query.query_id == "Q1"

    def test_get_nonexistent_query(self):
        from benchbox.core.tsbs_devops.dataframe_queries import get_tsbs_devops_query

        assert get_tsbs_devops_query("Q99") is None

    def test_list_queries(self):
        from benchbox.core.tsbs_devops.dataframe_queries import list_tsbs_devops_queries

        assert len(list_tsbs_devops_queries()) == 18

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_all_queries_registered(self, query_id):
        from benchbox.core.tsbs_devops.dataframe_queries import get_tsbs_devops_query

        assert get_tsbs_devops_query(query_id) is not None, f"Query {query_id} should be registered"

    def test_queries_have_both_implementations(self):
        from benchbox.core.tsbs_devops.dataframe_queries import TSBS_DEVOPS_DATAFRAME_QUERIES

        for query in TSBS_DEVOPS_DATAFRAME_QUERIES.get_all_queries():
            assert query.expression_impl is not None, f"{query.query_id} missing expression impl"
            assert query.pandas_impl is not None, f"{query.query_id} missing pandas impl"

    def test_all_queries_callable(self):
        from benchbox.core.tsbs_devops.dataframe_queries import TSBS_DEVOPS_DATAFRAME_QUERIES

        for query in TSBS_DEVOPS_DATAFRAME_QUERIES.get_all_queries():
            assert callable(query.expression_impl), f"{query.query_id} expression_impl not callable"
            assert callable(query.pandas_impl), f"{query.query_id} pandas_impl not callable"

    def test_query_descriptions_not_empty(self):
        from benchbox.core.tsbs_devops.dataframe_queries import TSBS_DEVOPS_DATAFRAME_QUERIES

        for query in TSBS_DEVOPS_DATAFRAME_QUERIES.get_all_queries():
            assert query.description, f"{query.query_id} missing description"
            assert len(query.description) > 10, f"{query.query_id} description too short"

    def test_query_names_not_empty(self):
        from benchbox.core.tsbs_devops.dataframe_queries import TSBS_DEVOPS_DATAFRAME_QUERIES

        for query in TSBS_DEVOPS_DATAFRAME_QUERIES.get_all_queries():
            assert query.query_name, f"{query.query_id} missing query_name"


class TestTSBSDevOpsQueryCategories:
    def test_single_host_queries_have_scan(self):
        from benchbox.core.tsbs_devops.dataframe_queries import get_tsbs_devops_query

        for qid in ["Q1", "Q2"]:
            query = get_tsbs_devops_query(qid)
            assert QueryCategory.SCAN in query.categories, f"{qid} should have SCAN"

    def test_aggregation_queries_have_group_by(self):
        from benchbox.core.tsbs_devops.dataframe_queries import get_tsbs_devops_query

        for qid in ["Q3", "Q4", "Q5", "Q6"]:
            query = get_tsbs_devops_query(qid)
            assert QueryCategory.GROUP_BY in query.categories, f"{qid} should have GROUP_BY"

    def test_join_queries_have_join(self):
        from benchbox.core.tsbs_devops.dataframe_queries import get_tsbs_devops_query

        for qid in ["Q15", "Q16", "Q17", "Q18"]:
            query = get_tsbs_devops_query(qid)
            assert QueryCategory.JOIN in query.categories, f"{qid} should have JOIN"

    def test_lastpoint_has_subquery(self):
        from benchbox.core.tsbs_devops.dataframe_queries import get_tsbs_devops_query

        query = get_tsbs_devops_query("Q16")
        assert QueryCategory.SUBQUERY in query.categories, "Q16 should have SUBQUERY"

    def test_time_bucket_queries_have_analytical(self):
        from benchbox.core.tsbs_devops.dataframe_queries import get_tsbs_devops_query

        for qid in ["Q5", "Q6"]:
            query = get_tsbs_devops_query(qid)
            assert QueryCategory.ANALYTICAL in query.categories, f"{qid} should have ANALYTICAL"


class TestTSBSDevOpsParameters:
    def test_all_queries_have_parameters(self):
        from benchbox.core.tsbs_devops.dataframe_queries.parameters import TSBS_DEVOPS_DEFAULT_PARAMS

        assert len(TSBS_DEVOPS_DEFAULT_PARAMS) == 18

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_parameter_entries_exist(self, query_id):
        from benchbox.core.tsbs_devops.dataframe_queries.parameters import TSBS_DEVOPS_DEFAULT_PARAMS

        assert query_id in TSBS_DEVOPS_DEFAULT_PARAMS, f"Missing parameters for {query_id}"

    def test_all_have_start_time(self):
        from benchbox.core.tsbs_devops.dataframe_queries.parameters import TSBS_DEVOPS_DEFAULT_PARAMS

        for qid, params in TSBS_DEVOPS_DEFAULT_PARAMS.items():
            assert "start_time" in params, f"{qid} missing start_time"

    def test_q17_has_region(self):
        from benchbox.core.tsbs_devops.dataframe_queries.parameters import get_parameters

        result = get_parameters("Q17")
        assert result.get("region") == "us-east-1"

    def test_q1_has_hostname(self):
        from benchbox.core.tsbs_devops.dataframe_queries.parameters import get_parameters

        result = get_parameters("Q1")
        assert result.get("hostname") == "host_0"

    def test_get_parameters_returns_container(self):
        from benchbox.core.tsbs_devops.dataframe_queries.parameters import TSBSDevOpsParameters, get_parameters

        result = get_parameters("Q1")
        assert isinstance(result, TSBSDevOpsParameters)
        assert result.query_id == "Q1"

    def test_query_time_bounds_are_native_temporal_values(self):
        from benchbox.core.tsbs_devops.dataframe_queries.parameters import get_parameters

        params = get_parameters("Q1")

        assert params.get("start_time") == datetime(2024, 1, 1)
        assert params.get("end_time") == datetime(2024, 1, 1, 12)

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_time_parameters_are_native_datetimes(self, query_id):
        from benchbox.core.tsbs_devops.dataframe_queries.parameters import get_parameters

        params = get_parameters(query_id)

        assert isinstance(params.get("start_time"), datetime)
        assert isinstance(params.get("end_time"), datetime)

    def test_production_time_bounds_filter_polars_timestamp_column(self):
        pl = pytest.importorskip("polars")

        from benchbox.core.tsbs_devops.dataframe_queries.queries import _expr_window
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        ctx.register_table(
            "cpu",
            pl.DataFrame(
                {
                    "time": [datetime(2024, 1, 1), datetime(2025, 1, 1)],
                    "hostname": ["host_0", "host_0"],
                }
            ),
        )

        result, _, _ = _expr_window(ctx, "Q1", "cpu", host=True)

        assert result.collect().height == 1

    def test_production_time_bounds_filter_polars_raw_csv_timestamp_column(self, tmp_path):
        pl = pytest.importorskip("polars")

        from benchbox.core.tsbs_devops.dataframe_queries.queries import _expr_window
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        class Benchmark:
            def get_schema(self):
                return {
                    "cpu": {
                        "columns": {
                            "time": {"type": "TIMESTAMP"},
                            "hostname": {"type": "VARCHAR(255)"},
                        }
                    }
                }

        csv_path = tmp_path / "cpu.csv"
        csv_path.write_text("time,hostname\n2024-01-01 00:00:00,host_0\n2025-01-01 00:00:00,host_0\n")
        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()
        adapter.load_table(
            ctx,
            "cpu",
            [csv_path],
            column_names=["time", "hostname"],
            benchmark=Benchmark(),
        )

        result, _, _ = _expr_window(ctx, "Q1", "cpu", host=True)

        assert ctx.get_table("cpu").native.collect_schema()["time"] == pl.Datetime("us")
        assert result.collect().height == 1


class TestTSBSDevOpsBenchmarkRegistry:
    def test_tsbs_devops_supports_dataframe(self):
        from benchbox.core.benchmark_registry import get_benchmark_metadata

        meta = get_benchmark_metadata("tsbs_devops")
        assert meta is not None
        assert meta.get("supports_dataframe") is True


