from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpch.power_test import TPCHPowerTest, TPCHPowerTestConfig, TPCHPowerTestResult

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


def _make_power_test(
    *,
    benchmark: Mock | None = None,
    seed: int | None = None,
    stream_id: int = 0,
    scale_factor: float = 0.01,
) -> TPCHPowerTest:

    bench = benchmark or Mock()
    bench.get_query = Mock(return_value="SELECT 1")

    connection = Mock()
    mock_cursor = Mock()
    mock_cursor.fetchall = Mock(return_value=[(1,)])
    connection.execute = Mock(return_value=mock_cursor)

    return TPCHPowerTest(
        benchmark=bench,
        connection=connection,
        scale_factor=scale_factor,
        seed=seed,
        stream_id=stream_id,
        validation=True,
        warm_up=False,
    )


class TestPowerTestConfig:
    def test_defaults(self) -> None:
        config = TPCHPowerTestConfig()
        assert config.scale_factor == 1.0
        assert config.seed is None
        assert config.stream_id == 0
        assert config.timeout is None
        assert config.warm_up is True
        assert config.validation is True
        assert config.verbose is False


class TestPowerTestResult:
    def test_to_dict_serialisation(self) -> None:
        config = TPCHPowerTestConfig(
            scale_factor=0.1, seed=17, stream_id=2, timeout=30.0, warm_up=False, validation=False
        )
        result = TPCHPowerTestResult(
            config=config,
            start_time="2025-01-01T00:00:00",
            end_time="2025-01-01T00:01:00",
            total_time=60.5,
            power_at_size=123.45,
            queries_executed=22,
            queries_successful=22,
            query_results=[{"query_id": 1, "success": True, "execution_time": 0.5}],
            success=True,
            errors=[],
        )

        payload = result.to_dict()
        assert payload["power_at_size"] == 123.45
        assert payload["queries_successful"] == 22
        assert payload["config"]["scale_factor"] == 0.1
        assert json.loads(json.dumps(payload))["errors"] == []


class TestPowerTestExecution:
    def test_run_produces_successful_result(self) -> None:
        power_test = _make_power_test()
        benchmark = power_test.benchmark
        connection = power_test.connection

        result = power_test.run()

        assert isinstance(result, TPCHPowerTestResult)
        assert result.success is True
        assert result.queries_executed == 22
        assert result.queries_successful == 22
        assert len(result.query_results) == 22
        assert benchmark.get_query.call_count == 22
        assert connection.execute.call_count == 22
        assert power_test.validate_results(result) is True
        assert result.power_at_size > 0.0

    def test_run_captures_query_failures(self) -> None:
        power_test = _make_power_test()

        def fail_once_on_execute(*_args, **_kwargs):
            fail_once_on_execute.invocations += 1
            if fail_once_on_execute.invocations == 5 and not fail_once_on_execute.failed:
                fail_once_on_execute.failed = True
                raise RuntimeError("boom")
            mock_cursor = Mock()
            mock_cursor.fetchall = Mock(return_value=[(1,)])
            return mock_cursor

        fail_once_on_execute.invocations = 0
        fail_once_on_execute.failed = False

        with patch.object(power_test, "_preflight_validate_generation", return_value=None):
            power_test.connection.execute.side_effect = fail_once_on_execute
            result = power_test.run()

        assert result.success is False
        assert result.queries_successful == 21
        assert result.queries_executed == 22
        assert len(result.errors) == 1
        assert "failed" in result.errors[0].lower()
        assert power_test.validate_results(result) is False

    def test_preflight_validation_raises_on_generation_failure(self) -> None:
        power_test = _make_power_test()

        def raise_on_second(query_id, **_kwargs):
            if query_id == 2:
                raise RuntimeError("cannot build query")
            return "SELECT 1"

        power_test.benchmark.get_query.side_effect = raise_on_second

        with pytest.raises(RuntimeError, match="preflight failed"):
            power_test._preflight_validate_generation([1, 2, 3])

    def test_get_all_queries_returns_stream_specific_sql(self) -> None:
        power_test = _make_power_test()

        def render_query(query_id, **_kwargs):
            return f"SELECT {query_id}"

        power_test.benchmark.get_query.side_effect = render_query
        queries = power_test.get_all_queries()

        assert len(queries) == 22
        assert all(key.startswith("Position_") for key in queries)

        sample_key, sample_sql = next(iter(queries.items()))
        parts = sample_key.split("_")
        assert parts[0] == "Position"
        assert parts[2] == "Query"
        assert sample_sql == f"SELECT {parts[3]}"


class TestPowerTestReferenceSeedContext:
    def test_reference_seed_context_true_for_qgen_defaults(self) -> None:
        power_test = _make_power_test(scale_factor=1.0, seed=None)

        calls: list[bool] = []
        with patch(
            "benchbox.core.tpch.power_test.set_reference_seed_context",
            side_effect=lambda v: calls.append(v),
        ):
            power_test.run()

        assert len(calls) == 22
        assert all(v is True for v in calls)

    def test_reference_seed_context_false_for_former_reference_seed(self) -> None:
        power_test = _make_power_test(scale_factor=1.0, seed=17039360)

        calls: list[bool] = []
        with patch(
            "benchbox.core.tpch.power_test.set_reference_seed_context",
            side_effect=lambda v: calls.append(v),
        ):
            power_test.run()

        assert len(calls) == 22
        assert all(v is False for v in calls)

    def test_reference_seed_context_false_for_custom_seed(self) -> None:
        power_test = _make_power_test(scale_factor=1.0, seed=12345)

        calls: list[bool] = []
        with patch(
            "benchbox.core.tpch.power_test.set_reference_seed_context",
            side_effect=lambda v: calls.append(v),
        ):
            power_test.run()

        assert len(calls) == 22
        assert all(v is False for v in calls)

    def test_reference_seed_context_cleared_after_every_query(self) -> None:
        power_test = _make_power_test(scale_factor=1.0, seed=12345)

        with patch("benchbox.core.tpch.power_test.clear_reference_seed_context") as mock_clear:
            power_test.run()

        assert mock_clear.call_count == 22

    def test_boundary_query_not_failed_with_custom_seed_at_sf1(self) -> None:
        from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
        from benchbox.platforms.duckdb import DuckDBAdapter

        bench = Mock()
        bench.get_query = Mock(side_effect=lambda query_id, **_kwargs: f"SELECT {query_id}")

        raw_connection = Mock()

        def raw_execute(query_text):
            raw_result = Mock()
            in_spec_counts = {11: 999, 16: 18_000, 18: 20, 20: 186}
            query_id = int(query_text.rsplit(" ", 1)[-1])
            raw_result.fetchall = Mock(return_value=[(1,)] * in_spec_counts[query_id])
            return raw_result

        raw_connection.execute = Mock(side_effect=raw_execute)

        adapter = DuckDBAdapter(fetch_mode="rows")
        connection = PlatformAdapterConnection(raw_connection, adapter)
        connection.benchmark_type = "tpch"
        connection.scale_factor = 1.0

        power_test = TPCHPowerTest(
            benchmark=bench,
            connection=connection,
            scale_factor=1.0,
            seed=12345,
            stream_id=0,
            validation=True,
            warm_up=False,
            query_subset=["11", "16", "18", "20"],
        )

        result = power_test.run()

        assert result.success is True
        assert result.queries_successful == 4
        assert all(qr["success"] for qr in result.query_results)
        for qr in result.query_results:
            assert qr.get("error") is None

    def test_boundary_query_rejects_out_of_range_count_at_sf1(self) -> None:
        from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
        from benchbox.platforms.duckdb import DuckDBAdapter

        bench = Mock()
        bench.get_query = Mock(return_value="SELECT 11")

        raw_connection = Mock()

        def raw_execute(query_text):
            raw_result = Mock()
            raw_result.fetchall = Mock(return_value=[(1,)] * 10_000)
            return raw_result

        raw_connection.execute = Mock(side_effect=raw_execute)

        adapter = DuckDBAdapter(fetch_mode="rows")
        connection = PlatformAdapterConnection(raw_connection, adapter)
        connection.benchmark_type = "tpch"
        connection.scale_factor = 1.0

        power_test = TPCHPowerTest(
            benchmark=bench,
            connection=connection,
            scale_factor=1.0,
            seed=None,
            stream_id=0,
            validation=True,
            validation_mode="exact",
            warm_up=False,
            query_subset=["11"],
        )

        result = power_test.run()

        assert result.success is False
        assert result.queries_successful == 0
        assert not result.query_results[0]["success"]


class TestPowerTestValidationAfterRefresh:
    @staticmethod
    def _connection(rows_for_query):
        connection = Mock()
        state = {"query_id": None}

        def set_query_context(query_id, stream_id=None):
            state["query_id"] = query_id

        def execute(_query):
            from benchbox.core.validation.query_validation import QueryValidator

            rows = rows_for_query()
            validation = QueryValidator().validate_query_result("tpch", state["query_id"], rows, scale_factor=0.01)
            status = "PASSED" if validation.is_valid and validation.expected_row_count is not None else "SKIPPED"
            if not validation.is_valid:
                return Mock(
                    platform_result={"status": "FAILED", "error": validation.error_message},
                )
            evidence = {"status": status, "expected": validation.expected_row_count, "actual": rows}
            return Mock(platform_result={"status": "SUCCESS", "rows_returned": rows, "row_count_validation": evidence})

        connection.set_query_context = set_query_context
        connection.execute = execute
        return connection

    def test_refresh_enabled_run_skips_validation_for_refreshed_data(self) -> None:
        state = {"refreshed": False}
        connection = self._connection(lambda: 34 if state["refreshed"] else 33)
        bench = Mock()
        bench.get_query = Mock(return_value="SELECT 1")
        power_test = TPCHPowerTest(
            benchmark=bench,
            connection=connection,
            scale_factor=0.01,
            validation=True,
            warm_up=False,
            query_subset=["Q13"],
            refresh_update_set=Mock(set_id=1),
            refresh_connection_factory=lambda: object(),
        )

        def refresh(function, _connection):
            state["refreshed"] = state["refreshed"] or function == "RF1"
            return {"function": function, "status": "SUCCESS", "error": None, "duration_ms": 1, "update_set": 1}

        power_test._execute_refresh_function = refresh
        power_test._preflight_validate_generation = lambda _permutation: None

        result = power_test.run()

        assert result.success is True
        assert result.validation["data_state"] == "refreshed"
        assert result.validation["skipped_queries"] == [13]
        assert result.validation["skip_reason"] == "data refreshed"
        assert json.dumps(result.to_dict())

    def test_run_without_refresh_validates_against_base_data(self) -> None:
        connection = self._connection(lambda: 33)
        bench = Mock()
        bench.get_query = Mock(return_value="SELECT 1")
        power_test = TPCHPowerTest(
            benchmark=bench,
            connection=connection,
            scale_factor=0.01,
            validation=True,
            warm_up=False,
            query_subset=["Q13"],
        )
        power_test._preflight_validate_generation = lambda _permutation: None

        result = power_test.run()

        assert result.success is True
        assert result.validation["data_state"] == "base"
        assert result.validation["validated_queries"] == [13]
        assert result.validation["skipped_queries"] == []
