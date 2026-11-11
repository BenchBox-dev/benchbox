from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.tpch import dataframe_queries as dq
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.power_test import TPCHPowerTest

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpch, pytest.mark.qgen]

SCALE_FACTOR = 0.01
SEEDS = [None, 17039360, 4242]
WARMUPS = 1
ITERATIONS = 2
STREAMS = [0]


@pytest.fixture(scope="module")
def tpch_benchmark() -> TPCHBenchmark:
    try:
        return TPCHBenchmark(scale_factor=SCALE_FACTOR)
    except RuntimeError as exc:
        pytest.skip(f"qgen binary not available: {exc}")


def _sql_power_stream(benchmark: TPCHBenchmark, seed: int | None, stream_id: int) -> dict[int, str]:
    executed: list[str] = []
    connection = Mock()

    def _execute(sql: str) -> Mock:
        executed.append(sql)
        cursor = Mock(spec=["fetchall"])
        cursor.fetchall = Mock(return_value=[(1,)])
        return cursor

    connection.execute = Mock(side_effect=_execute)
    power_test = TPCHPowerTest(
        benchmark=benchmark,
        connection=connection,
        scale_factor=SCALE_FACTOR,
        seed=seed,
        stream_id=stream_id,
        dialect="duckdb",
        validation=False,
        warm_up=False,
    )
    result = power_test.run()
    order = [int(qr["query_id"]) for qr in result.query_results]
    assert len(order) == 22
    return dict(zip(order, executed, strict=True))


def _dataframe_bindings(seed: int | None) -> dict[int, list[tuple[int, dict[str, Any]]]]:
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    adapter = PolarsDataFrameAdapter()
    recorded: list[tuple[int, dict[str, Any]]] = []

    def _record(ctx: Any, query: Any, **_kwargs: Any) -> dict[str, Any]:
        number = int(str(query.query_id).lstrip("Qq"))
        recorded.append((number, dq.get_tpch_parameters(number)))
        return {"query_id": query.query_id, "status": "SUCCESS", "execution_time_seconds": 0.0, "rows_returned": 1}

    options: dict[str, Any] = {"power_warmup_iterations": WARMUPS, "power_iterations": ITERATIONS}
    if seed is not None:
        options["seed"] = seed
    config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=SCALE_FACTOR, options=options)

    with patch.object(adapter, "execute_query", _record):
        results = adapter._execute_queries_phase(
            ctx=None, benchmark_config=config, benchmark_instance=None, monitor=None
        )

    by_stream: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for result, binding in zip(results, recorded, strict=True):
        by_stream.setdefault(int(result["stream_id"]), []).append(binding)
    return by_stream


def _quoted(value: str, sql: str) -> bool:
    return re.search(r"'[^']*" + re.escape(value) + r"[^']*'", sql) is not None


def _assert_bound_in_sql(query: int, params: dict[str, Any], sql: str) -> None:
    where = f"Q{query}"
    for key, value in params.items():
        if key in {"discount", "delta"}:
            continue
        if key == "cutoff_date":
            delta = (date(1998, 12, 1) - value).days
            assert f"INTERVAL '{delta}' DAY" in sql, (where, key, value)
        elif key in {"discount_low", "discount_high"}:
            mid = round(params["discount_low"] + 0.01, 2)
            assert f"BETWEEN {mid:.2f} - 0.01 AND {mid:.2f} + 0.01" in sql, (where, key, value)
            assert round(params["discount_high"] - 0.01, 2) == mid, (where, key, value)
        elif key == "fraction":
            match = re.search(r"\* ([0-9.]+) FROM", sql)
            assert match is not None and float(match.group(1)) == pytest.approx(value, rel=1e-9), (where, key, value)
        elif key == "end_date" and query not in (7, 8):
            continue
        elif isinstance(value, date):
            assert f"CAST('{value.isoformat()}' AS DATE)" in sql, (where, key, value)
        elif isinstance(value, bool):
            raise AssertionError(f"{where}: unexpected boolean parameter {key}")
        elif isinstance(value, int):
            assert re.search(rf"(?<![\w.]){value}(?![\w.])", sql), (where, key, value)
        elif isinstance(value, str):
            assert _quoted(value, sql), (where, key, value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, int):
                    assert re.search(rf"(?<![\w.]){item}(?![\w.])", sql), (where, key, item)
                else:
                    assert f"'{item}'" in sql, (where, key, item)
        else:
            raise AssertionError(f"{where}: unhandled parameter type for {key}: {type(value).__name__}")


@pytest.mark.parametrize("seed", SEEDS, ids=lambda s: "no-seed" if s is None else f"seed-{s}")
def test_sql_and_dataframe_bind_identical_parameters(tpch_benchmark: TPCHBenchmark, seed: int | None) -> None:
    dataframe = _dataframe_bindings(seed)
    assert sorted(dataframe) == STREAMS

    for stream_id in STREAMS:
        sql = _sql_power_stream(tpch_benchmark, seed, stream_id)
        runs = [dataframe[stream_id][start : start + 22] for start in range(0, len(dataframe[stream_id]), 22)]
        assert len(runs) == WARMUPS + ITERATIONS

        for bindings in runs:
            assert [number for number, _ in bindings] == list(sql)
            for number, params in bindings:
                assert set(dq.TPCH_DEFAULT_PARAMS[number]) <= set(params), f"Q{number} lost a parameter"
                _assert_bound_in_sql(number, params, sql[number])

            defaults_used = sum(
                all(
                    params.get(key) == value
                    for key, value in dq.TPCH_DEFAULT_PARAMS[number].items()
                    if key != "fraction"
                )
                for number, params in bindings
            )
            if seed is None:
                assert defaults_used == 22
            else:
                assert defaults_used <= 2, f"stream {stream_id} bound qgen -d values for {defaults_used} queries"


def test_seeded_overrides_do_not_leak_past_the_run() -> None:
    previous = {3: {"segment": "SENTINEL"}}
    dq.set_parameter_overrides(previous)
    try:
        _dataframe_bindings(17039360)
        assert dq.get_tpch_parameters(3)["segment"] == "SENTINEL"
    finally:
        dq.set_parameter_overrides(None)
    assert dq.get_tpch_parameters(3)["segment"] == "BUILDING"


def test_seeded_overrides_restored_when_a_query_raises() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with dq.seeded_parameter_overrides(4242, SCALE_FACTOR, 0):
            assert dq.get_tpch_parameters(3) != dq.TPCH_DEFAULT_PARAMS[3]
            raise RuntimeError("boom")
    assert dq.get_tpch_parameters(3) == dq.TPCH_DEFAULT_PARAMS[3]


def test_dataframe_result_records_bound_parameter_set() -> None:
    from types import SimpleNamespace

    from benchbox.platforms.dataframe.benchmark_mixin import _describe_query_parameters

    assert (
        _describe_query_parameters(SimpleNamespace(name="tpch", options={"seed": None}))
        == "qgen -d (TPC-H default substitution parameters)"
    )
    assert (
        _describe_query_parameters(SimpleNamespace(name="TPC-H", options={"seed": 17039360}))
        == "qgen -r (17039360 + 1000 * stream_id)"
    )
    assert _describe_query_parameters(SimpleNamespace(name="tpcdi", options={"seed": 17039360})) is None


def test_overlapping_runs_keep_seed_and_scale_bindings_isolated() -> None:
    entered = Barrier(3)
    observed = Barrier(3)

    def extracted(seed, scale_factor):
        return {3: {"segment": str(seed)}}

    def run(seed, scale_factor):
        with dq.seeded_parameter_overrides(seed, scale_factor, 0):
            entered.wait(timeout=10)
            segment = dq.get_tpch_parameters(3)["segment"]
            fraction = dq.get_tpch_parameters(11)["fraction"]
            observed.wait(timeout=10)
            assert dq.get_tpch_parameters(3)["segment"] == segment
            assert dq.get_tpch_parameters(11)["fraction"] == fraction
            return segment, fraction

    with (
        patch("benchbox.core.tpch.parameter_extractor.get_tpch_extracted_parameters", side_effect=extracted),
        ThreadPoolExecutor(max_workers=3) as executor,
    ):
        runs = [(101, 0.1), (202, 0.01), (None, 1.0)]
        futures = [executor.submit(run, seed, scale) for seed, scale in runs]
        assert [future.result(timeout=15) for future in futures] == [
            ("101", 0.001),
            ("202", 0.01),
            ("BUILDING", 0.0001),
        ]


def test_nested_unseeded_run_restores_outer_seed_and_scale_after_failure() -> None:
    with patch(
        "benchbox.core.tpch.parameter_extractor.get_tpch_extracted_parameters",
        return_value={3: {"segment": "OUTER"}},
    ):
        with dq.seeded_parameter_overrides(101, 0.1, 0):
            assert dq.get_tpch_parameters(3)["segment"] == "OUTER"
            assert dq.get_tpch_parameters(11)["fraction"] == 0.001
            with pytest.raises(RuntimeError, match="inner failure"):
                with dq.seeded_parameter_overrides(None, 0.01, 0):
                    assert dq.get_tpch_parameters(3)["segment"] == "BUILDING"
                    assert dq.get_tpch_parameters(11)["fraction"] == 0.01
                    raise RuntimeError("inner failure")
            assert dq.get_tpch_parameters(3)["segment"] == "OUTER"
            assert dq.get_tpch_parameters(11)["fraction"] == 0.001
    assert dq.get_tpch_parameters(3)["segment"] == "BUILDING"
    assert dq.get_tpch_parameters(11)["fraction"] == 0.0001


def test_failed_parameter_extraction_preserves_outer_bindings() -> None:
    with dq.seeded_parameter_overrides(None, 0.1, 0):
        with patch(
            "benchbox.core.tpch.parameter_extractor.get_tpch_extracted_parameters",
            side_effect=RuntimeError("extraction"),
        ):
            with pytest.raises(RuntimeError, match="extraction"), dq.seeded_parameter_overrides(101, 0.01, 0):
                pytest.fail("failed extraction entered the run")
        assert dq.get_tpch_parameters(3)["segment"] == "BUILDING"
        assert dq.get_tpch_parameters(11)["fraction"] == 0.001


@pytest.mark.parametrize("seed", SEEDS, ids=lambda s: "no-seed" if s is None else f"seed-{s}")
def test_dataframe_power_iterations_all_run_stream_zero(seed: int | None) -> None:
    from benchbox.core.tpch.streams import TPCHStreams
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    adapter = PolarsDataFrameAdapter()

    def _record(ctx: Any, query: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"query_id": query.query_id, "status": "SUCCESS", "execution_time_seconds": 0.0, "rows_returned": 1}

    options: dict[str, Any] = {"power_warmup_iterations": WARMUPS, "power_iterations": ITERATIONS}
    if seed is not None:
        options["seed"] = seed
    config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=SCALE_FACTOR, options=options)

    with patch.object(adapter, "execute_query", _record):
        results = adapter._execute_queries_phase(
            ctx=None, benchmark_config=config, benchmark_instance=None, monitor=None
        )

    assert {result["stream_id"] for result in results} == {0}
    assert {(result["run_type"], result["iteration"]) for result in results} == {
        ("warmup", 0),
        *(("measurement", iteration) for iteration in range(1, ITERATIONS + 1)),
    }
    for iteration in range(1, ITERATIONS + 1):
        order = [int(str(r["query_id"]).lstrip("Qq")) for r in results if r["iteration"] == iteration]
        assert order == TPCHStreams.PERMUTATION_MATRIX[0]


