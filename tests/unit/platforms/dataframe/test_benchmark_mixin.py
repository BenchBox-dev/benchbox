from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.dataframe.query import DataFrameQuery, QueryRegistry
from benchbox.core.results.schema import build_result_payload
from benchbox.core.schemas import BenchmarkConfig
from benchbox.platforms.dataframe.benchmark_mixin import (
    BenchmarkExecutionMixin,
    DataFramePhases,
    DataFrameRunOptions,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.usefixtures("sufficient_dataframe_memory"),
]


class DummyAdapter(BenchmarkExecutionMixin):
    platform_name = "Polars"
    family = "expression"

    def __init__(self) -> None:
        self.execute_called = False
        self.executed_query_ids: list[str] = []
        self.loaded_paths: dict[str, list] = {}

    def create_context(self):
        return SimpleNamespace()

    def load_table(self, ctx, table_name, file_paths, column_names=None, delimiter=None, benchmark=None, **kwargs):
        self.loaded_paths[table_name] = file_paths
        return 1

    def execute_query(self, ctx, query, query_id=None):
        self.execute_called = True
        self.executed_query_ids.append(str(query.query_id))
        return {
            "query_id": query.query_id,
            "status": "SUCCESS",
            "execution_time_seconds": 0.01,
            "rows_returned": 1,
        }

    def get_platform_info(self):
        return {"platform": "Polars"}


class DummyPandasProfiledAdapter(DummyAdapter):
    family = "pandas"

    def __init__(self, *, query_plan=None) -> None:
        super().__init__()
        self._query_plan = query_plan
        self.profiled_called = False

    def execute_query_profiled(self, ctx, query, query_id=None):
        _ = (ctx, query, query_id)
        self.profiled_called = True
        result = {
            "query_id": query.query_id,
            "status": "SUCCESS",
            "execution_time_seconds": 0.01,
            "rows_returned": 1,
        }
        profile = SimpleNamespace(query_plan=self._query_plan, plan_capture_time_ms=0.0)
        return result, profile


class FailingLoadAdapter(DummyAdapter):
    def load_table(self, ctx, table_name, file_paths, column_names=None, delimiter=None, benchmark=None, **kwargs):
        raise RuntimeError("load failed")


class DummyDataFusionAdapter(DummyAdapter):
    platform_name = "DataFusion"
    family = "expression"


class TpchRowCountAdapter(DummyAdapter):
    def __init__(self, rows_returned: int = 4) -> None:
        super().__init__()
        self.rows_returned = rows_returned

    def execute_query(self, ctx, query, query_id=None):
        result = super().execute_query(ctx, query, query_id)
        result["rows_returned"] = self.rows_returned
        return result


class DummyBenchmarkWithPlatformSkips:
    def get_platform_skip_queries(self, platform_name: str) -> list[str]:
        if platform_name.lower() == "datafusion":
            return ["list_filter", "list_transform"]
        if platform_name.lower() == "lakesail":
            return ["empty_build_join"]
        return []


class DummyBenchmark:
    def __init__(self, tables):
        self.name = "dummy"
        self.display_name = "Dummy"
        self.scale_factor = 1.0
        self.tables = tables

    def get_dataframe_queries(self):
        return [
            DataFrameQuery(
                query_id="Q1",
                query_name="Test",
                description="Test query",
                expression_impl=lambda _ctx: None,
            )
        ]


class DummyRegistryBenchmark:
    def __init__(self, tables):
        self.name = "read_primitives"
        self.display_name = "Read Primitives"
        self.scale_factor = 1.0
        self.tables = tables

    def get_dataframe_queries(self):
        registry = QueryRegistry("read_primitives")
        registry.register(
            DataFrameQuery(
                query_id="aggregation_simple",
                query_name="Aggregation",
                description="Aggregation query",
                expression_impl=lambda _ctx: None,
            )
        )
        registry.register(
            DataFrameQuery(
                query_id="filter_selective",
                query_name="Filter",
                description="Filter query",
                expression_impl=lambda _ctx: None,
            )
        )
        registry.register(
            DataFrameQuery(
                query_id="optimizer_exists_to_semijoin",
                query_name="Optimizer Exists",
                description="Should be skipped in DataFrame mode",
                expression_impl=lambda _ctx: None,
            )
        )
        return registry

    def get_dataframe_skip_queries(self):
        return ["optimizer_exists_to_semijoin"]


def test_fail_fast_on_missing_data(tmp_path):
    adapter = DummyAdapter()
    benchmark = DummyBenchmark({"customer": tmp_path / "missing.tbl"})
    config = BenchmarkConfig(name="dummy", display_name="Dummy", scale_factor=1.0)

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=True, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "FAILED"
    assert result.validation_details is not None
    assert result.validation_details.get("phase") == "data_loading"
    assert adapter.execute_called is False


def test_fail_fast_on_load_error(tmp_path):
    adapter = FailingLoadAdapter()
    tbl_path = tmp_path / "customer.tbl"
    tbl_path.write_text("1|Alice|\n")
    benchmark = DummyBenchmark({"customer": tbl_path})
    config = BenchmarkConfig(name="dummy", display_name="Dummy", scale_factor=1.0)

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=True, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "FAILED"
    assert result.validation_details is not None
    assert result.validation_details.get("phase") == "data_loading"
    assert adapter.execute_called is False


def test_registry_queries_and_skip_summary_are_supported(tmp_path):
    adapter = DummyAdapter()
    tbl_path = tmp_path / "lineitem.tbl"
    tbl_path.write_text("1|A|\n")
    benchmark = DummyRegistryBenchmark({"lineitem": tbl_path})
    config = BenchmarkConfig(name="read_primitives", display_name="Read Primitives", scale_factor=1.0)

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    query_ids = [q["query_id"] for q in result.query_results]
    assert "QOPTIMIZER_EXISTS_TO_SEMIJOIN" in query_ids
    assert any(qid.endswith("DF_SKIP_SUMMARY") for qid in query_ids)
    assert "optimizer_exists_to_semijoin" not in [q.lower() for q in adapter.executed_query_ids]

    summary = next(q for q in result.query_results if q["query_id"].endswith("DF_SKIP_SUMMARY"))
    skip_summary = summary["dataframe_skip_summary"]
    assert "row_count_validation" not in summary
    assert skip_summary["executed_total"] == 2
    assert skip_summary["skipped_total"] == 1
    assert skip_summary["skipped_by_category"]["optimizer"] == 1


def test_skip_summary_respects_query_filter(tmp_path):
    adapter = DummyAdapter()
    tbl_path = tmp_path / "lineitem.tbl"
    tbl_path.write_text("1|A|\n")
    benchmark = DummyRegistryBenchmark({"lineitem": tbl_path})
    config = BenchmarkConfig(name="read_primitives", display_name="Read Primitives", scale_factor=1.0)
    config.queries = ["aggregation_simple"]

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    query_ids = [q["query_id"] for q in result.query_results]
    assert "QOPTIMIZER_EXISTS_TO_SEMIJOIN" not in query_ids
    assert not any(qid.endswith("DF_SKIP_SUMMARY") for qid in query_ids)


def test_sharded_tables_use_prepared_paths_when_prefer_parquet(tmp_path):
    adapter = DummyAdapter()
    shard1 = tmp_path / "lineitem.tbl.1"
    shard2 = tmp_path / "lineitem.tbl.2"
    shard1.write_text("1|A|\n")
    shard2.write_text("2|B|\n")
    prepared = tmp_path / "lineitem.parquet"
    prepared.write_text("placeholder")

    benchmark = DummyBenchmark({"lineitem": [shard1, shard2]})
    config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0)

    with patch("benchbox.platforms.dataframe.benchmark_mixin.DataFrameDataLoader.prepare_benchmark_data") as prep:
        prep.return_value = {"lineitem": prepared}
        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=True, execute=False),
            options=DataFrameRunOptions(prefer_parquet=True),
        )

    assert result.validation_status == "NOT_RUN"
    prep.assert_called_once()
    assert adapter.loaded_paths["lineitem"] == [prepared]


def test_prefer_parquet_fails_fast_when_preparation_raises(tmp_path):
    adapter = DummyAdapter()
    source = tmp_path / "lineitem.tbl"
    source.write_text("1|A|\n")
    benchmark = DummyBenchmark({"lineitem": source})
    config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0)

    with patch("benchbox.platforms.dataframe.benchmark_mixin.DataFrameDataLoader.prepare_benchmark_data") as prep:
        prep.side_effect = RuntimeError("conversion failed")
        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=True, execute=True),
            options=DataFrameRunOptions(prefer_parquet=True),
        )

    assert result.validation_status == "FAILED"
    assert result.validation_details is not None
    assert result.validation_details.get("phase") == "data_loading"
    assert "Data preparation failed in prefer_parquet mode" in result.validation_details.get("error", "")
    assert adapter.execute_called is False


def test_magicmock_benchmark_does_not_false_positive_skip_loader(tmp_path):
    adapter = DummyAdapter()
    tbl_path = tmp_path / "customer.tbl"
    tbl_path.write_text("1|Alice|\n")

    benchmark = MagicMock()
    benchmark.name = "dummy"
    benchmark.display_name = "Dummy"
    benchmark.scale_factor = 1.0
    benchmark.tables = {"customer": tbl_path}

    config = BenchmarkConfig(name="dummy", display_name="Dummy", scale_factor=1.0)

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=True, execute=False),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "NOT_RUN"
    assert adapter.loaded_paths["customer"] == [tbl_path]


def test_magicmock_benchmark_does_not_false_positive_execute_workload():
    adapter = DummyAdapter()
    benchmark = MagicMock()
    benchmark.name = "dummy"
    benchmark.display_name = "Dummy"
    benchmark.scale_factor = 1.0
    benchmark.get_dataframe_queries = MagicMock(
        return_value=[
            DataFrameQuery(
                query_id="Q1",
                query_name="Test",
                description="Test query",
                expression_impl=lambda _ctx: None,
            )
        ]
    )
    config = BenchmarkConfig(
        name="dummy",
        display_name="Dummy",
        scale_factor=1.0,
        options={"power_warmup_iterations": 0, "power_iterations": 1},
    )

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "NOT_RUN"
    assert benchmark.execute_dataframe_workload.called is False
    assert adapter.executed_query_ids == ["Q1"]


def test_tpch_reference_run_records_query_and_run_validation_evidence() -> None:
    adapter = TpchRowCountAdapter()
    benchmark = DummyBenchmark({})
    config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=1.0,
        queries=["1"],
        options={"power_warmup_iterations": 0, "power_iterations": 1},
    )

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "PASSED"
    assert result.validation_details == {
        "provider": "tpc_expected_row_counts",
        "benchmark": "tpch",
        "checked": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
    }
    assert result.execution_metadata["phase_status"]["validation"] == {"status": "PASSED"}
    assert build_result_payload(result)["phases"]["validation"] == {"status": "PASSED"}
    assert result.query_results[0]["row_count_validation"] == {
        "expected": 4,
        "actual": 4,
        "status": "PASSED",
    }


class EmptyTableAdapter(DummyAdapter):
    def load_table(self, ctx, table_name, file_paths, column_names=None, delimiter=None, benchmark=None, **kwargs):
        super().load_table(ctx, table_name, file_paths, column_names, delimiter, benchmark, **kwargs)
        return 0


def _no_oracle_config() -> BenchmarkConfig:
    return BenchmarkConfig(
        name="dummy",
        display_name="Dummy",
        scale_factor=1.0,
        options={"power_warmup_iterations": 0, "power_iterations": 1},
    )


def test_benchmark_without_oracle_passes_on_loaded_table_validation(tmp_path) -> None:
    adapter = DummyAdapter()
    source = tmp_path / "customer.tbl"
    source.write_text("1|A|\n")

    result = adapter.run_benchmark(
        DummyBenchmark({"customer": source}),
        benchmark_config=_no_oracle_config(),
        phases=DataFramePhases(load=True, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "PASSED"
    assert result.validation_details["load"]["provider"] == "loaded_table_checks"
    assert build_result_payload(result)["phases"]["validation"] == {"status": "PASSED"}


def test_benchmark_without_oracle_fails_when_a_loaded_table_is_empty(tmp_path) -> None:
    adapter = EmptyTableAdapter()
    source = tmp_path / "customer.tbl"
    source.write_text("1|A|\n")

    result = adapter.run_benchmark(
        DummyBenchmark({"customer": source}),
        benchmark_config=_no_oracle_config(),
        phases=DataFramePhases(load=True, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "FAILED"
    assert result.validation_details["load"]["empty_tables"] == ["customer"]


class FailingQueryAdapter(DummyAdapter):
    def execute_query(self, ctx, query, query_id=None):
        result = super().execute_query(ctx, query, query_id)
        result["status"] = "FAILED"
        result["error"] = "boom"
        return result


def test_failed_queries_without_oracle_record_partial_validation_phase() -> None:
    result = FailingQueryAdapter().run_benchmark(
        DummyBenchmark({}),
        benchmark_config=_no_oracle_config(),
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "PARTIAL"
    assert build_result_payload(result)["phases"]["validation"] == {"status": "PARTIAL"}


def test_disabled_validation_mode_skips_loaded_table_validation(tmp_path) -> None:
    adapter = DummyAdapter()
    source = tmp_path / "customer.tbl"
    source.write_text("1|A|\n")
    config = _no_oracle_config()
    config.options["validation_mode"] = "disabled"

    result = adapter.run_benchmark(
        DummyBenchmark({"customer": source}),
        benchmark_config=config,
        phases=DataFramePhases(load=True, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "NOT_RUN"


def test_tpch_reference_mismatch_fails_the_query_and_run() -> None:
    adapter = TpchRowCountAdapter(rows_returned=3)
    benchmark = DummyBenchmark({})
    config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=1.0,
        queries=["1"],
        options={"power_warmup_iterations": 0, "power_iterations": 1},
    )

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "FAILED"
    assert result.failed_queries == 1
    assert result.query_results[0]["row_count_validation"]["status"] == "FAILED"


def test_capture_plans_uses_profiled_execution_without_capture_plan_kw(tmp_path):
    adapter = DummyPandasProfiledAdapter(query_plan={"kind": "logical"})
    tbl_path = tmp_path / "customer.tbl"
    tbl_path.write_text("1|Alice|\n")
    benchmark = DummyBenchmark({"customer": tbl_path})
    config = BenchmarkConfig(
        name="dummy",
        display_name="Dummy",
        scale_factor=1.0,
        options={"power_warmup_iterations": 0, "power_iterations": 1},
        capture_plans=True,
    )

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "NOT_RUN"
    assert adapter.profiled_called is True
    assert result.query_plans_captured == 1
    assert result.plan_capture_failures == 0


def test_capture_plans_counts_missing_plan_as_failure(tmp_path):
    adapter = DummyPandasProfiledAdapter(query_plan=None)
    tbl_path = tmp_path / "customer.tbl"
    tbl_path.write_text("1|Alice|\n")
    benchmark = DummyBenchmark({"customer": tbl_path})
    config = BenchmarkConfig(
        name="dummy",
        display_name="Dummy",
        scale_factor=1.0,
        options={"power_warmup_iterations": 0, "power_iterations": 1},
        capture_plans=True,
    )

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.validation_status == "NOT_RUN"
    assert result.query_plans_captured == 0
    assert result.plan_capture_failures == 1
    assert result.plan_capture_errors
    assert result.plan_capture_errors[0]["query_id"] == "Q1"


class _RecordingCaptureFailureAdapter(DummyPandasProfiledAdapter):
    def execute_query_profiled(self, ctx, query, query_id=None):
        result, profile = super().execute_query_profiled(ctx, query, query_id)
        result["plan_capture_error"] = "TypeError: unsupported operand"
        self.plan_capture_errors.append({"query_id": "Q1", "error": "TypeError: unsupported operand"})
        return result, profile


def test_capture_plans_preserves_real_capture_error_cause(tmp_path):
    adapter = _RecordingCaptureFailureAdapter(query_plan=None)
    tbl_path = tmp_path / "customer.tbl"
    tbl_path.write_text("1|Alice|\n")
    benchmark = DummyBenchmark({"customer": tbl_path})
    config = BenchmarkConfig(
        name="dummy",
        display_name="Dummy",
        scale_factor=1.0,
        options={"power_warmup_iterations": 0, "power_iterations": 1},
        capture_plans=True,
    )

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )

    assert result.plan_capture_failures == 1
    assert result.plan_capture_errors[0]["error"] == "TypeError: unsupported operand"
    qr = next(q for q in result.query_results if q["query_id"] == "Q1")
    assert qr["plan_capture_error"] == "TypeError: unsupported operand"


def test_run_benchmark_resets_plan_capture_errors_between_runs(tmp_path):
    adapter = _RecordingCaptureFailureAdapter(query_plan=None)
    tbl_path = tmp_path / "customer.tbl"
    tbl_path.write_text("1|Alice|\n")
    benchmark = DummyBenchmark({"customer": tbl_path})
    config = BenchmarkConfig(
        name="dummy",
        display_name="Dummy",
        scale_factor=1.0,
        options={"power_warmup_iterations": 0, "power_iterations": 1},
        capture_plans=True,
    )
    run_kwargs = {
        "benchmark_config": config,
        "phases": DataFramePhases(load=False, execute=True),
        "options": DataFrameRunOptions(prefer_parquet=False),
    }

    first = adapter.run_benchmark(benchmark, **run_kwargs)
    second = adapter.run_benchmark(benchmark, **run_kwargs)

    assert len(first.plan_capture_errors) == 1
    assert len(second.plan_capture_errors) == 1


def test_collect_skip_query_ids_routes_through_get_platform_skip_queries():
    adapter = DummyDataFusionAdapter()
    benchmark = DummyBenchmarkWithPlatformSkips()

    skip_ids = adapter._collect_skip_query_ids(benchmark)

    assert "LIST_FILTER" in skip_ids
    assert "LIST_TRANSFORM" in skip_ids


def test_collect_skip_query_ids_platform_dispatch_is_parameterized():
    datafusion_adapter = DummyDataFusionAdapter()
    polars_adapter = DummyAdapter()
    benchmark = DummyBenchmarkWithPlatformSkips()

    datafusion_skips = datafusion_adapter._collect_skip_query_ids(benchmark)
    polars_skips = polars_adapter._collect_skip_query_ids(benchmark)

    assert "LIST_FILTER" in datafusion_skips
    assert "LIST_FILTER" not in polars_skips


def test_collect_skip_query_ids_no_platform_skips_method():
    adapter = DummyDataFusionAdapter()
    benchmark = DummyBenchmark(tables={})

    skip_ids = adapter._collect_skip_query_ids(benchmark)

    assert "LIST_FILTER" not in skip_ids
    assert "LIST_TRANSFORM" not in skip_ids


class _StubTPCDSQueryManager:
    def get_query(self, query_id, seed=None, variant=None):
        _ = (query_id, seed, variant)
        return "SELECT 1"


def test_tpcds_dataframe_requires_variant_parity_in_mixin(monkeypatch):
    from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES

    original_get = TPCDS_DATAFRAME_QUERIES.get
    monkeypatch.setattr(
        TPCDS_DATAFRAME_QUERIES,
        "get",
        lambda query_id: None if str(query_id).lower() == "q14b" else original_get(query_id),
    )
    adapter = DummyAdapter()
    config = BenchmarkConfig(
        name="tpcds",
        display_name="TPC-DS",
        scale_factor=1.0,
        options={"tpcds_dataframe_variant_fallback": False},
    )

    benchmark = SimpleNamespace(query_manager=_StubTPCDSQueryManager())

    with pytest.raises(RuntimeError, match="missing variant DataFrame implementations"):
        adapter._get_queries_for_benchmark(config, benchmark, stream_id=0)


class TestStructureDataFramePlan:
    def test_datafusion_text_plan_promoted_to_dag_preserving_raw_output(self):
        from benchbox.core.dataframe.profiling import QueryPlan
        from benchbox.core.results.query_plan_models import QueryPlanDAG

        adapter = DummyAdapter()
        plan_text = (
            "Projection: lineitem.l_returnflag\n"
            '  Filter: lineitem.l_shipdate <= Date32("1998-09-02")\n'
            "    TableScan: lineitem"
        )
        captured = QueryPlan(platform="datafusion", plan_type="logical", plan_text=plan_text)

        structured = adapter._structure_dataframe_plan(captured, "q1")

        assert isinstance(structured, QueryPlanDAG)
        assert structured.plan_fingerprint
        assert structured.raw_explain_output == plan_text

    def test_platform_without_parser_falls_back_to_text_plan(self):
        from benchbox.core.dataframe.profiling import QueryPlan

        adapter = DummyAdapter()
        captured = QueryPlan(platform="polars", plan_type="logical", plan_text="FILTER\n  SCAN lineitem")

        structured = adapter._structure_dataframe_plan(captured, "q1")

        assert structured is captured

    def test_parser_failure_falls_back_to_text_plan(self, monkeypatch):
        from benchbox.core.dataframe.profiling import QueryPlan

        adapter = DummyAdapter()

        class _BoomParser:
            def parse_explain_output(self, _qid, _txt):
                raise ValueError("unparseable")

        monkeypatch.setattr(
            "benchbox.core.query_plans.parsers.registry.get_parser_for_platform",
            lambda _platform: _BoomParser(),
        )
        captured = QueryPlan(platform="datafusion", plan_type="logical", plan_text="garbage")

        structured = adapter._structure_dataframe_plan(captured, "q1")

        assert structured is captured

    def test_parser_returning_none_falls_back_to_text_plan(self, monkeypatch):
        from benchbox.core.dataframe.profiling import QueryPlan

        adapter = DummyAdapter()

        class _NoneParser:
            def parse_explain_output(self, _qid, _txt):
                return None

        monkeypatch.setattr(
            "benchbox.core.query_plans.parsers.registry.get_parser_for_platform",
            lambda _platform: _NoneParser(),
        )
        captured = QueryPlan(platform="datafusion", plan_type="logical", plan_text="x")

        structured = adapter._structure_dataframe_plan(captured, "q1")

        assert structured is captured


class TestHandleNoQueries:
    def test_empty_resolution_raises_configuration_error(self):
        from benchbox.core.exceptions import ConfigurationError

        adapter = DummyAdapter()

        with pytest.raises(ConfigurationError, match="no DataFrame query source"):
            adapter._handle_no_queries(
                [],
                set(),
                benchmark_id="tpcds_obt",
                query_filter=None,
            )


class TestResolveColumnNamesAndDelimiter:
    def _config(self):
        return BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01)

    def test_mock_benchmark_delimiter_is_ignored(self):
        adapter = DummyAdapter()
        _, delimiter = adapter._resolve_column_names_and_delimiter(self._config(), MagicMock())
        assert delimiter is None

    def test_string_benchmark_delimiter_is_kept(self):
        adapter = DummyAdapter()
        benchmark = SimpleNamespace(csv_delimiter=";", tables={})
        _, delimiter = adapter._resolve_column_names_and_delimiter(self._config(), benchmark)
        assert delimiter == ";"
