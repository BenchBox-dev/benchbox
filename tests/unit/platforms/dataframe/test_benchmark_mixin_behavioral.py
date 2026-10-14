# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory
from benchbox.core.schemas import BenchmarkConfig
from benchbox.platforms.dataframe.benchmark_mixin import (
    BenchmarkExecutionMixin,
    DataFramePhases,
    DataFrameRunOptions,
    DataLoadingError,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.usefixtures("sufficient_dataframe_memory"),
]


class StubAdapter(BenchmarkExecutionMixin):
    platform_name = "Polars"
    family = "expression"

    def __init__(self) -> None:
        self.executed_queries: list[str] = []
        self.loaded_tables: dict[str, list] = {}

    def create_context(self) -> SimpleNamespace:
        return SimpleNamespace()

    def load_table(self, ctx, table_name, file_paths, column_names=None, delimiter=None, benchmark=None, **kwargs):
        self.loaded_tables[table_name] = file_paths
        return 10

    def execute_query(self, ctx, query, query_id=None):
        qid = query_id or query.query_id
        self.executed_queries.append(qid)
        return {
            "query_id": qid,
            "status": "SUCCESS",
            "execution_time_seconds": 0.005,
            "rows_returned": 10,
        }

    def get_platform_info(self):
        return {"platform": self.platform_name, "family": self.family}


class FailingQueryAdapter(StubAdapter):
    def execute_query(self, ctx, query, query_id=None):
        qid = query_id or query.query_id
        self.executed_queries.append(qid)
        return {
            "query_id": qid,
            "status": "FAILED",
            "execution_time_seconds": 0.001,
            "error": "simulated query failure",
        }


class ExceptionQueryAdapter(StubAdapter):
    def execute_query(self, ctx, query, query_id=None):
        raise RuntimeError("unexpected crash during query")


class FailingLoadAdapter(StubAdapter):
    def load_table(self, ctx, table_name, file_paths, column_names=None, delimiter=None, benchmark=None, **kwargs):
        raise RuntimeError("disk read error")


def _make_benchmark(tables, queries=None, skip_queries=None):

    class _Benchmark:
        def __init__(self):
            self.name = "test_bench"
            self.display_name = "Test Benchmark"
            self.scale_factor = 1.0
            self.tables = tables

        def get_dataframe_queries(self):
            if queries is not None:
                return queries
            return [
                DataFrameQuery(
                    query_id="Q1",
                    query_name="Select All",
                    description="Simple select",
                    expression_impl=lambda _ctx: None,
                ),
                DataFrameQuery(
                    query_id="Q2",
                    query_name="Aggregate",
                    description="Simple agg",
                    expression_impl=lambda _ctx: None,
                ),
            ]

        def get_dataframe_skip_queries(self):
            return skip_queries or []

    return _Benchmark()


def _make_config(**overrides) -> BenchmarkConfig:
    defaults = {
        "name": "test_bench",
        "display_name": "Test Benchmark",
        "scale_factor": 1.0,
        "options": {
            "power_warmup_iterations": 0,
            "power_iterations": 1,
        },
    }
    defaults.update(overrides)
    return BenchmarkConfig(**defaults)


class TestQueryExecutionDispatch:
    def test_all_queries_executed(self, tmp_path):
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl})
        config = _make_config()

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        executed_ids = [q["query_id"] for q in result.query_results if q.get("run_type") == "measurement"]
        assert "Q1" in executed_ids
        assert "Q2" in executed_ids

    def test_mocked_execution_is_isolated_from_ambient_memory(self, monkeypatch, tmp_path):
        monkeypatch.setattr("benchbox.core.dataframe.capabilities.get_available_memory_gb", lambda: 0.01)
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")

        result = adapter.run_benchmark(
            _make_benchmark({"data": tbl}),
            benchmark_config=_make_config(),
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        assert result.validation_status != "FAILED"
        assert adapter.executed_queries == ["Q1", "Q2"]

    def test_query_filter_limits_execution(self, tmp_path):
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl})
        config = _make_config()
        config.queries = ["Q1"]

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        measurement_ids = [q["query_id"] for q in result.query_results if q.get("run_type") == "measurement"]
        assert "Q1" in measurement_ids
        assert "Q2" not in measurement_ids

    def test_skipped_queries_produce_skipped_results(self, tmp_path):
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl}, skip_queries=["Q2"])
        config = _make_config()

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        skipped = [q for q in result.query_results if q.get("status") == "SKIPPED"]
        assert len(skipped) == 1
        assert skipped[0]["query_id"] == "Q2"


class TestResultFormatting:
    def test_successful_result_has_required_keys(self, tmp_path):
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl})
        config = _make_config()

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        measurement = [q for q in result.query_results if q.get("run_type") == "measurement"]
        assert len(measurement) >= 1

        first_query = measurement[0]
        assert "query_id" in first_query
        assert "status" in first_query
        assert "execution_time_seconds" in first_query
        assert first_query["execution_time_seconds"] >= 0.0

    def test_timing_uses_canonical_key(self, tmp_path):
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl})
        config = _make_config()

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        for query_result in result.query_results:
            if query_result.get("run_type") == "measurement":
                assert "execution_time_seconds" in query_result
                assert query_result["execution_time_seconds"] == query_result["execution_time"]
                assert query_result["execution_time_ms"] == int(query_result["execution_time_seconds"] * 1000)

    def test_failed_query_includes_error(self, tmp_path):
        adapter = FailingQueryAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl})
        config = _make_config()

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        measurement = [q for q in result.query_results if q.get("run_type") == "measurement"]
        assert len(measurement) >= 1
        assert measurement[0]["status"] == "FAILED"
        assert "simulated query failure" in measurement[0].get("error_message", "")

    def test_iteration_and_stream_id_populated(self, tmp_path):
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl})
        config = _make_config()

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        measurement = [q for q in result.query_results if q.get("run_type") == "measurement"]
        for meas in measurement:
            assert "iteration" in meas
            assert "stream_id" in meas
            assert meas["iteration"] >= 1


class TestWarmupBehavior:
    def test_warmup_iterations_emitted(self, tmp_path):
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl})
        config = _make_config(options={"power_warmup_iterations": 1, "power_iterations": 1})

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        warmup = [q for q in result.query_results if q.get("run_type") == "warmup"]
        measurement = [q for q in result.query_results if q.get("run_type") == "measurement"]

        assert len(warmup) >= 1
        assert len(measurement) >= 1
        for warmup_result in warmup:
            assert warmup_result["iteration"] == 0

    def test_no_warmup_when_zero(self, tmp_path):
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl})
        config = _make_config(options={"power_warmup_iterations": 0, "power_iterations": 1})

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        warmup = [q for q in result.query_results if q.get("run_type") == "warmup"]
        assert len(warmup) == 0


class TestErrorReporting:
    def test_load_failure_reports_data_loading_phase(self, tmp_path):
        adapter = FailingLoadAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl})
        config = _make_config()

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=True, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        assert result.validation_status == "FAILED"
        assert result.validation_details is not None
        assert result.validation_details.get("phase") == "data_loading"
        assert len(adapter.executed_queries) == 0

    def test_missing_data_files_fail_fast(self, tmp_path):
        adapter = StubAdapter()
        benchmark = _make_benchmark({"customer": tmp_path / "nonexistent.tbl"})
        config = _make_config()

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=True, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        assert result.validation_status == "FAILED"
        assert result.validation_details.get("phase") == "data_loading"
        assert len(adapter.executed_queries) == 0

    def test_exception_during_query_is_caught(self, tmp_path):
        adapter = ExceptionQueryAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")
        benchmark = _make_benchmark({"data": tbl})
        config = _make_config()

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        measurement = [q for q in result.query_results if q.get("run_type") == "measurement"]
        assert len(measurement) >= 1
        assert measurement[0]["status"] == "FAILED"
        assert "unexpected crash" in measurement[0].get("error_message", "")


class TestStaticHelpers:
    def test_build_query_filter_with_q_prefix(self):
        config = _make_config()
        config.queries = ["Q1", "Q5"]

        result = BenchmarkExecutionMixin._build_query_filter(config)

        assert result is not None
        assert "Q1" in result
        assert "1" in result
        assert "Q5" in result
        assert "5" in result

    def test_build_query_filter_without_prefix(self):
        config = _make_config()
        config.queries = ["3"]

        result = BenchmarkExecutionMixin._build_query_filter(config)

        assert result is not None
        assert "3" in result
        assert "Q3" in result

    def test_build_query_filter_none_returns_none(self):
        config = _make_config()
        config.queries = None

        result = BenchmarkExecutionMixin._build_query_filter(config)

        assert result is None

    def test_build_query_filter_empty_returns_none(self):
        config = _make_config()
        config.queries = []

        result = BenchmarkExecutionMixin._build_query_filter(config)

        assert result is None

    def test_query_category_for_numbered_queries(self):
        assert BenchmarkExecutionMixin._query_category("Q1") == "q"
        assert BenchmarkExecutionMixin._query_category("Q22") == "q"

    def test_query_category_for_underscore_ids(self):
        assert BenchmarkExecutionMixin._query_category("filter_selective") == "filter"
        assert BenchmarkExecutionMixin._query_category("optimizer_exists_to_semijoin") == "optimizer"

    def test_query_category_for_other(self):
        assert BenchmarkExecutionMixin._query_category("foobar") == "other"

    def test_normalize_table_paths_string_to_path(self):
        from pathlib import Path

        result = BenchmarkExecutionMixin._normalize_table_paths({"orders": "/tmp/orders.tbl"})

        assert isinstance(result["orders"], Path)
        assert result["orders"] == Path("/tmp/orders.tbl")

    def test_normalize_table_paths_list(self):
        from pathlib import Path

        result = BenchmarkExecutionMixin._normalize_table_paths(
            {"lineitem": ["/tmp/lineitem.tbl.1", "/tmp/lineitem.tbl.2"]}
        )

        assert isinstance(result["lineitem"], list)
        assert all(isinstance(p, Path) for p in result["lineitem"])
        assert len(result["lineitem"]) == 2

    def test_find_missing_paths_all_exist(self, tmp_path):
        data_file = tmp_path / "orders.tbl"
        data_file.write_text("1|A|\n")

        result = BenchmarkExecutionMixin._find_missing_paths({"orders": data_file})

        assert result == {}

    def test_find_missing_paths_some_missing(self, tmp_path):
        from pathlib import Path

        result = BenchmarkExecutionMixin._find_missing_paths({"orders": Path("/nonexistent/orders.tbl")})

        assert "orders" in result
        assert len(result["orders"]) == 1


class TestDataclassDefaults:
    def test_phases_defaults(self):
        phases = DataFramePhases()

        assert phases.load is True
        assert phases.execute is True

    def test_phases_custom(self):
        phases = DataFramePhases(load=False, execute=True)

        assert phases.load is False
        assert phases.execute is True

    def test_run_options_defaults(self):
        options = DataFrameRunOptions()

        assert options.ignore_memory_warnings is False
        assert options.force_regenerate is False
        assert options.prefer_parquet is True
        assert options.cache_dir is None
        assert options.verbose is False
        assert options.very_verbose is False

    def test_run_options_custom(self):
        from pathlib import Path

        options = DataFrameRunOptions(
            ignore_memory_warnings=True,
            prefer_parquet=False,
            cache_dir=Path("/tmp/cache"),
            verbose=True,
        )

        assert options.ignore_memory_warnings is True
        assert options.prefer_parquet is False
        assert options.cache_dir == Path("/tmp/cache")
        assert options.verbose is True


class TestDataLoadingError:
    def test_basic_message(self):
        err = DataLoadingError("table load failed")

        assert str(err) == "table load failed"
        assert err.table_stats == {}
        assert err.per_table_stats == {}

    def test_with_table_stats(self):
        err = DataLoadingError("load failed", table_stats={"orders": 100})

        assert err.table_stats == {"orders": 100}

    def test_is_runtime_error(self):
        err = DataLoadingError("test")

        assert isinstance(err, RuntimeError)


class TestMultipleIterations:
    def test_multiple_iterations_produce_multiple_results(self, tmp_path):
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")

        single_query = [
            DataFrameQuery(
                query_id="Q1",
                query_name="Single",
                description="Single query",
                expression_impl=lambda _ctx: None,
            )
        ]
        benchmark = _make_benchmark({"data": tbl}, queries=single_query)
        config = _make_config(options={"power_warmup_iterations": 0, "power_iterations": 2})

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        measurement = [q for q in result.query_results if q.get("run_type") == "measurement"]
        assert len(measurement) == 2

        iterations = [m["iteration"] for m in measurement]
        assert 1 in iterations
        assert 2 in iterations

    def test_stream_ids_increment(self, tmp_path):
        adapter = StubAdapter()
        tbl = tmp_path / "data.tbl"
        tbl.write_text("1|A|\n")

        single_query = [
            DataFrameQuery(
                query_id="Q1",
                query_name="Single",
                description="Single query",
                expression_impl=lambda _ctx: None,
            )
        ]
        benchmark = _make_benchmark({"data": tbl}, queries=single_query)
        config = _make_config(options={"power_warmup_iterations": 1, "power_iterations": 2})

        result = adapter.run_benchmark(
            benchmark,
            benchmark_config=config,
            phases=DataFramePhases(load=False, execute=True),
            options=DataFrameRunOptions(prefer_parquet=False),
        )

        warmup = [q for q in result.query_results if q.get("run_type") == "warmup"]
        measurement = [q for q in result.query_results if q.get("run_type") == "measurement"]

        assert all(w["stream_id"] == 0 for w in warmup)
        meas_stream_ids = sorted(set(m["stream_id"] for m in measurement))
        assert meas_stream_ids == [1, 2]


class _RecordingBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return _record


class TuningSpyAdapter(StubAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.writer_calls: list[Any] = []

    def _write_applied_tuning_ledger(self, builder: Any) -> None:
        self.writer_calls.append(builder)


class TestAppliedTuningLedgerAttach:
    def test_attach_without_tuning_mixin_leaves_builder_untouched(self):
        adapter = StubAdapter()
        assert not hasattr(adapter, "_write_applied_tuning_ledger")
        builder = _RecordingBuilder()

        adapter._attach_applied_tuning_ledger(builder)

        assert builder.calls == []

    def test_attach_dispatches_configured_writer_once(self):
        adapter = TuningSpyAdapter()
        builder = _RecordingBuilder()

        adapter._attach_applied_tuning_ledger(builder)

        assert adapter.writer_calls == [builder]
