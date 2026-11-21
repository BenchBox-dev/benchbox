# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) DS (TPC-DS) - Copyright (c) Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import dataclasses
import functools
import os
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS, ParameterBinding, bind_parameters
from benchbox.utils.run_deadline import bind_run_deadline

SQL_POWER_DSQGEN_STREAM = 0

_QUERY_NUMBER = re.compile(r"^Q?(\d+)[A-Za-z]?$", re.IGNORECASE)

_cache: dict[tuple[int, float, int, int], ParameterBinding] = {}
_cache_lock = threading.Lock()


def power_parameter_seed(seed: int | None, stream_id: int) -> int:
    from benchbox.core.tpcds.power_test import power_parameter_seed as sql_power_parameter_seed

    return sql_power_parameter_seed(seed, stream_id)


def query_number(query_id: Any) -> int | None:
    match = _QUERY_NUMBER.match(str(query_id).strip())
    return int(match.group(1)) if match else None


def clear_binding_cache() -> None:
    with _cache_lock:
        _cache.clear()


def bind_power_stream(
    query_numbers: Iterable[int],
    *,
    scale_factor: float,
    seed: int | None,
    stream_id: int,
    dsqgen_stream: int = SQL_POWER_DSQGEN_STREAM,
    dsqgen: Any | None = None,
) -> dict[int, ParameterBinding]:
    rngseed = power_parameter_seed(seed, stream_id)
    scale = float(scale_factor)
    wanted = sorted({number for number in query_numbers if is_bound(number)})

    def key(number: int) -> tuple[int, float, int, int]:
        return (number, scale, rngseed, dsqgen_stream)

    with _cache_lock:
        missing = [number for number in wanted if key(number) not in _cache]
    if missing:
        if dsqgen is None:
            from benchbox.core.tpcds.c_tools import DSQGenBinary

            dsqgen = DSQGenBinary()

        def bind(number: int) -> ParameterBinding:
            return bind_parameters(number, scale_factor=scale, seed=rngseed, stream_id=dsqgen_stream, dsqgen=dsqgen)

        workers = max(1, min(8, os.cpu_count() or 1, len(missing)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fresh = dict(zip(missing, pool.map(bind_run_deadline(bind), missing)))
        with _cache_lock:
            _cache.update({key(number): binding for number, binding in fresh.items()})

    with _cache_lock:
        return {number: _cache[key(number)] for number in wanted}


def _bound(implementation: Callable[[Any], Any] | None, binding: ParameterBinding) -> Callable[[Any], Any] | None:
    if implementation is None:
        return None
    from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides

    number = binding.query_id
    parameters = dict(binding.parameters)

    @functools.wraps(implementation)
    def run(ctx: Any) -> Any:
        with parameter_overrides({number: dict(parameters)}):
            return implementation(ctx)

    run.parameter_binding = binding
    return run


def bind_queries(queries: Iterable[Any], bindings: Mapping[int, ParameterBinding]) -> list[Any]:
    bound: list[Any] = []
    for query in queries:
        number = query_number(query.query_id)
        binding = bindings.get(number) if number is not None else None
        if binding is None:
            bound.append(query)
            continue
        bound.append(
            dataclasses.replace(
                query,
                pandas_impl=_bound(query.pandas_impl, binding),
                expression_impl=_bound(query.expression_impl, binding),
            )
        )
    return bound


def query_binding(query: Any) -> ParameterBinding | None:
    for implementation in (query.expression_impl, query.pandas_impl):
        binding = getattr(implementation, "parameter_binding", None)
        if binding is not None:
            return binding
    return None


def run_seed(benchmark_config: Any) -> int | None:
    seed = (getattr(benchmark_config, "options", {}) or {}).get("seed")
    return None if seed is None else int(seed)


def bind_power_stream_queries(queries: list[Any], benchmark_config: Any, stream_id: int) -> list[Any]:
    from benchbox.core.dataframe.query_resolution import build_dataframe_query_filter_from_config

    query_filter = build_dataframe_query_filter_from_config(benchmark_config)
    numbers = set()
    for query in queries:
        number = query_number(query.query_id)
        if number is None:
            continue
        if query_filter is None or str(query.query_id).upper() in query_filter or f"Q{number}" in query_filter:
            numbers.add(number)
    bindings = bind_power_stream(
        numbers,
        scale_factor=getattr(benchmark_config, "scale_factor", 1.0),
        seed=run_seed(benchmark_config),
        stream_id=stream_id,
    )
    return bind_queries(queries, bindings)


def is_bound(number: int | None) -> bool:
    return number in ADAPTERS


def describe_query_parameters(seed: int | None, query_numbers: Iterable[int] | None = None) -> str:
    numbers = sorted(set(range(1, 100) if query_numbers is None else query_numbers))
    bound = [n for n in numbers if is_bound(n)]
    defaults = [n for n in numbers if not is_bound(n)]
    first = power_parameter_seed(seed, 0)

    def listed(values: list[int]) -> str:
        return " ".join(f"Q{n}" for n in values) or "none"

    return (
        f"dsqgen -LOG values (-RNGSEED {first} + stream_id, dsqgen stream {SQL_POWER_DSQGEN_STREAM}) "
        f"for {len(bound)} of {len(numbers)} queries: {listed(bound)}; "
        f"default_parameters.yaml for {len(defaults)}: {listed(defaults)}"
    )


def selected_query_numbers(benchmark_config: Any) -> list[int] | None:
    queries = getattr(benchmark_config, "queries", None)
    if not queries:
        return None
    numbers = {query_number(query_id) for query_id in queries}
    return sorted(n for n in numbers if n is not None)
