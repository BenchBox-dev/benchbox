from __future__ import annotations

import re
from typing import Any
from unittest.mock import patch

import pytest
from test_dataframe_seed_parity import _assert_bound_in_sql

from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.tpch import dataframe_queries as dq
from benchbox.core.tpch.benchmark import power_stream_seed

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpch, pytest.mark.qgen]

SCALE_FACTOR = 0.01
SEEDS = [4242, 17039360]
SKEW_QUERIES = ["Q1", "Q6", "Q11"]
HAVOC_QUERIES = ["Q1v1", "Q6v1", "Q11v1"]


def _benchmark(benchmark_id: str) -> Any:
    try:
        if benchmark_id == "tpch_skew":
            from benchbox.core.tpch_skew.benchmark import TPCHSkewBenchmark

            return TPCHSkewBenchmark(scale_factor=SCALE_FACTOR)
        from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

        return TPCHavocBenchmark(scale_factor=SCALE_FACTOR)
    except (RuntimeError, TypeError, ValueError) as exc:
        pytest.skip(f"benchmark unavailable: {exc}")


def _variant_dataframe_bindings(benchmark_id: str, seed: int | None) -> dict[int, list[tuple[int, dict[str, Any]]]]:
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    adapter = PolarsDataFrameAdapter()
    recorded: list[tuple[int, dict[str, Any]]] = []

    def _record(ctx: Any, query: Any, **_kwargs: Any) -> dict[str, Any]:
        match = re.match(r"Q(\d+)", str(query.query_id).upper())
        assert match is not None, f"unexpected variant query id {query.query_id}"
        recorded.append((int(match.group(1)), dq.get_tpch_parameters(int(match.group(1)))))
        return {"query_id": query.query_id, "status": "SUCCESS", "execution_time_seconds": 0.0}

    options: dict[str, Any] = {"power_warmup_iterations": 1, "power_iterations": 1}
    if seed is not None:
        options["seed"] = seed
    config = BenchmarkConfig(
        name=benchmark_id,
        display_name=benchmark_id,
        scale_factor=SCALE_FACTOR,
        queries=SKEW_QUERIES if benchmark_id == "tpch_skew" else HAVOC_QUERIES,
        options=options,
    )

    with patch.object(adapter, "execute_query", _record):
        results = adapter._execute_queries_phase(
            ctx=None, benchmark_config=config, benchmark_instance=None, monitor=None
        )

    by_stream: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for result, binding in zip(results, recorded, strict=True):
        by_stream.setdefault(int(result["stream_id"]), []).append(binding)
    return by_stream


@pytest.mark.parametrize("benchmark_id", ["tpch_skew", "tpchavoc"])
@pytest.mark.parametrize("seed", SEEDS)
def test_variant_dataframe_binds_same_qgen_parameters_as_sql(benchmark_id: str, seed: int) -> None:
    benchmark = _benchmark(benchmark_id)
    dataframe = _variant_dataframe_bindings(benchmark_id, seed)
    assert sorted(dataframe) == [0]

    for stream_id, bindings in sorted(dataframe.items()):
        stream_seed = power_stream_seed(seed, stream_id)
        assert stream_seed is not None
        for number, params in bindings:
            sql = benchmark.get_query(number, seed=stream_seed, scale_factor=SCALE_FACTOR, dialect="duckdb")
            assert set(dq.TPCH_DEFAULT_PARAMS[number]) <= set(params), f"Q{number} lost a parameter"
            _assert_bound_in_sql(number, params, sql)

        defaults_used = sum(
            all(params.get(key) == value for key, value in dq.TPCH_DEFAULT_PARAMS[number].items() if key != "fraction")
            for number, params in bindings
        )
        assert defaults_used <= 1, f"stream {stream_id} bound qgen -d values for {defaults_used} queries"


@pytest.mark.parametrize("benchmark_id", ["tpch_skew", "tpchavoc"])
def test_variant_dataframe_unseeded_binds_defaults(benchmark_id: str) -> None:
    dataframe = _variant_dataframe_bindings(benchmark_id, None)
    assert sorted(dataframe) == [0]

    for _stream_id, bindings in sorted(dataframe.items()):
        for number, params in bindings:
            expected = dict(dq.TPCH_DEFAULT_PARAMS[number])
            if number == 11:
                expected["fraction"] = float(f"{expected['fraction'] / SCALE_FACTOR:.10f}")
            assert params == expected, f"Q{number} did not bind defaults"


def test_variant_describe_query_parameters() -> None:
    from types import SimpleNamespace

    from benchbox.platforms.dataframe.benchmark_mixin import _describe_query_parameters

    assert (
        _describe_query_parameters(SimpleNamespace(name="tpch_skew", options={"seed": None}))
        == "qgen -d (TPC-H default substitution parameters)"
    )
    assert (
        _describe_query_parameters(SimpleNamespace(name="tpchavoc", options={"seed": 4242}))
        == "qgen -r (4242 + 1000 * stream_id)"
    )


