from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_FLOOR, Decimal
from typing import Any

POWER_QUERY_COUNTS = {"tpch": 22, "tpcds": 99}
TPCDS_POWER_STATEMENTS = 103
QPHDS_MIN_STREAMS = 4
LOAD_PHASES = ("schema_creation", "data_loading", "statistics", "post_load_maintenance")
SCORE_DERIVED_FROM_TIMINGS = "derived_from_timings"

_NON_POWER_TEST_TYPES = frozenset({"throughput", "maintenance"})
_PASSING_STATUSES = frozenset({"SUCCESS", "PASS"})


def tpc_benchmark_id(data: Mapping[str, Any]) -> str | None:
    benchmark = data.get("benchmark")
    raw = benchmark.get("id") if isinstance(benchmark, Mapping) else None
    squashed = "".join(character for character in str(raw or "").lower() if character.isalnum())
    return squashed if squashed in POWER_QUERY_COUNTS else None


def _scale_factor(data: Mapping[str, Any]) -> float | None:
    benchmark = data.get("benchmark")
    value = benchmark.get("scale_factor") if isinstance(benchmark, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    scale = float(value)
    return scale if math.isfinite(scale) and scale > 0 else None


def _iteration(query: Mapping[str, Any]) -> int:
    value = query.get("iter", query.get("iteration"))
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _duration_ms(query: Mapping[str, Any]) -> float | None:
    value = query.get("ms", query.get("execution_time_ms"))
    if value is None or isinstance(value, bool):
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if math.isfinite(duration) else None


def _queries(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    queries = data.get("queries")
    return [query for query in queries if isinstance(query, Mapping)] if isinstance(queries, list) else []


def power_at_size(data: Mapping[str, Any]) -> float | None:
    benchmark_id = tpc_benchmark_id(data)
    scale = _scale_factor(data)
    if benchmark_id is None or scale is None:
        return None
    measurement = [
        query
        for query in _queries(data)
        if query.get("run_type") == "measurement"
        and _iteration(query) > 0
        and str(query.get("test_type") or "power").lower() not in _NON_POWER_TEST_TYPES
    ]
    if not measurement:
        return None
    final_iteration = max(_iteration(query) for query in measurement)
    final = [query for query in measurement if _iteration(query) == final_iteration]
    if any(str(query.get("status") or "").upper() not in _PASSING_STATUSES for query in final):
        return None
    if len({str(query.get("id", query.get("query_id"))) for query in final}) < POWER_QUERY_COUNTS[benchmark_id]:
        return None
    durations = [_duration_ms(query) for query in final]
    if any(ms is None or ms <= 0 for ms in durations):
        return None
    seconds = [ms / 1000.0 for ms in durations if ms is not None]
    geometric_mean = math.exp(sum(math.log(value) for value in seconds) / len(seconds))
    return (scale * 3600) / geometric_mean


def throughput_at_size(data: Mapping[str, Any]) -> float | None:
    scale = _scale_factor(data)
    phases = data.get("phases")
    block = phases.get("throughput_test") if isinstance(phases, Mapping) else None
    duration_ms = block.get("duration_ms") if isinstance(block, Mapping) else None
    if scale is None or isinstance(duration_ms, bool) or not isinstance(duration_ms, int | float) or duration_ms <= 0:
        return None
    streams = block.get("stream_results")
    if isinstance(streams, list) and not all(isinstance(s, Mapping) and s.get("success") is True for s in streams):
        return None
    executed = sum(
        1
        for query in _queries(data)
        if query.get("test_type") == "throughput" and query.get("run_type") == "measurement"
    )
    return executed * scale * 3600 / (duration_ms / 1000) if executed else None


def qphh_at_size(power: float, throughput: float) -> float:
    if power <= 0 or throughput <= 0:
        return 0.0
    return round(math.sqrt(power * throughput), 1)


def qphds_terms(
    num_streams: int,
    power_seconds: float,
    throughput_seconds: Sequence[float],
    maintenance_seconds: Sequence[float],
    load_seconds: float,
) -> dict[str, float]:
    return {
        "q": float(POWER_QUERY_COUNTS["tpcds"] * num_streams),
        "t_pt_hours": power_seconds * num_streams / 3600.0,
        "t_tt_hours": sum(throughput_seconds) / 3600.0,
        "t_dm_hours": sum(maintenance_seconds) / 3600.0,
        "t_ld_hours": 0.01 * num_streams * load_seconds / 3600.0,
    }


def floor_qphds(scale_factor: float, q: float, hours_product: float) -> float:
    value = (
        Decimal(repr(float(scale_factor))) * Decimal(repr(float(q))) / Decimal(repr(float(hours_product))).sqrt().sqrt()
    )
    return float(value.to_integral_value(rounding=ROUND_FLOOR))


def qphds_at_size(
    scale_factor: float,
    num_streams: int,
    power_seconds: float,
    throughput_seconds: Sequence[float],
    maintenance_seconds: Sequence[float],
    load_seconds: float,
) -> float:
    terms = qphds_terms(num_streams, power_seconds, throughput_seconds, maintenance_seconds, load_seconds)
    product = terms["t_pt_hours"] * terms["t_tt_hours"] * terms["t_dm_hours"] * terms["t_ld_hours"]
    return floor_qphds(scale_factor, terms["q"], product)


def _positive_ms(block: Any) -> float | None:
    value = block.get("duration_ms") if isinstance(block, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _completed(block: Any) -> bool:
    return isinstance(block, Mapping) and str(block.get("status") or "").upper() == "COMPLETED"


def _load_seconds(phases: Mapping[str, Any]) -> float | None:
    if (
        not isinstance(phases.get("data_loading"), Mapping)
        or str(phases["data_loading"].get("status")).upper() == "NOT_RUN"
    ):
        return None
    total_ms = 0.0
    for name in LOAD_PHASES:
        block = phases.get(name)
        if not isinstance(block, Mapping) or str(block.get("status") or "").upper() == "NOT_RUN":
            continue
        if str(block.get("status") or "").upper() not in _PASSING_STATUSES | {"COMPLETED"}:
            return None
        duration = block.get("duration_ms")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int | float)
            or not math.isfinite(duration)
            or duration < 0
        ):
            return None
        total_ms += float(duration)
    settle = phases.get("merge_settle")
    if isinstance(settle, Mapping):
        waited = settle.get("waited_seconds")
        if settle.get("settled") is not True or isinstance(waited, bool) or not isinstance(waited, int | float):
            return None
        total_ms += float(waited) * 1000.0
    return total_ms / 1000.0 if total_ms > 0 else None


def _qphds(data: Mapping[str, Any], scale: float) -> float | None:
    phases = data.get("phases")
    if not isinstance(phases, Mapping):
        return None
    power = phases.get("power_test")
    if not _completed(power) or power.get("query_count") != TPCDS_POWER_STATEMENTS:
        return None
    first = phases.get("throughput_test")
    streams = first.get("num_streams") if isinstance(first, Mapping) else None
    if isinstance(streams, bool) or not isinstance(streams, int) or streams < QPHDS_MIN_STREAMS or streams % 2:
        return None
    blocks = [
        phases.get(name) for name in ("throughput_test", "throughput_test_2", "maintenance_test", "maintenance_test_2")
    ]
    if not all(_completed(block) for block in blocks):
        return None
    if any(block.get("num_streams") != streams for block in blocks[:2]):
        return None
    power_ms = _positive_ms(power)
    durations = [_positive_ms(block) for block in blocks]
    load_seconds = _load_seconds(phases)
    if power_ms is None or load_seconds is None or any(duration is None for duration in durations):
        return None
    seconds = [duration / 1000.0 for duration in durations if duration is not None]
    return qphds_at_size(scale, streams, power_ms / 1000.0, seconds[:2], seconds[2:], load_seconds)


def combined_score(data: Mapping[str, Any]) -> float | None:
    benchmark_id = tpc_benchmark_id(data)
    scale = _scale_factor(data)
    if benchmark_id is None or scale is None:
        return None
    if benchmark_id == "tpcds":
        return _qphds(data, scale)
    phases = data.get("phases")
    if not isinstance(phases, Mapping) or not refresh_included(phases.get("power_test"), phases.get("throughput_test")):
        return None
    power = power_at_size(data)
    throughput = throughput_at_size(data)
    if power is None or throughput is None:
        return None
    return qphh_at_size(power, throughput)


def power_refresh_evidence(power_phase: Mapping[str, Any] | None) -> tuple[bool, int | None]:
    if not isinstance(power_phase, Mapping):
        return False, None
    first_set = power_phase.get("refresh_first_set")
    functions = power_phase.get("refresh_functions")
    if isinstance(first_set, bool) or not isinstance(first_set, int) or first_set < 1:
        return False, None
    if not isinstance(functions, list) or len(functions) != 2:
        return False, first_set
    for expected, function in zip(("RF1", "RF2"), functions, strict=True):
        update_set = function.get("update_set") if isinstance(function, Mapping) else None
        duration_ms = function.get("duration_ms") if isinstance(function, Mapping) else None
        if (
            not isinstance(function, Mapping)
            or function.get("function") != expected
            or isinstance(update_set, bool)
            or update_set != first_set
            or function.get("status") != "SUCCESS"
            or isinstance(duration_ms, bool)
            or not isinstance(duration_ms, (int, float))
            or not math.isfinite(duration_ms)
            or duration_ms < 0
        ):
            return False, first_set
    return True, first_set


def throughput_refresh_evidence(
    throughput_phase: Mapping[str, Any] | None,
    expected_streams: int | None = None,
) -> tuple[bool, int | None, int]:
    if not isinstance(throughput_phase, Mapping):
        return False, None, 0
    refresh_stream = throughput_phase.get("refresh_stream")
    if not isinstance(refresh_stream, Mapping):
        return False, None, 0
    first_set = refresh_stream.get("first_set")
    if isinstance(first_set, bool) or not isinstance(first_set, int) or first_set < 1:
        return False, None, 0
    pairs = refresh_stream.get("pairs")
    if not isinstance(pairs, list):
        return False, first_set, 0
    stream_count = throughput_phase.get("num_streams")
    if isinstance(stream_count, bool) or not isinstance(stream_count, int):
        stream_count = (
            expected_streams
            if isinstance(expected_streams, int) and not isinstance(expected_streams, bool)
            else len(throughput_phase.get("stream_results") or [])
        )
    pair_count = refresh_stream.get("pair_count")
    start_time = refresh_stream.get("start_time")
    end_time = refresh_stream.get("end_time")
    valid = (
        refresh_stream.get("status") == "COMPLETED"
        and isinstance(start_time, str)
        and bool(start_time)
        and isinstance(end_time, str)
        and bool(end_time)
        and stream_count > 0
        and len(pairs) == stream_count
        and isinstance(pair_count, int)
        and not isinstance(pair_count, bool)
        and pair_count == len(pairs)
    )
    for offset, pair in enumerate(pairs):
        set_id = first_set + offset
        functions = pair.get("functions") if isinstance(pair, Mapping) else None
        pair_set = pair.get("update_set") if isinstance(pair, Mapping) else None
        if (
            not isinstance(pair, Mapping)
            or isinstance(pair_set, bool)
            or pair_set != set_id
            or pair.get("status") != "SUCCESS"
            or not isinstance(functions, list)
            or len(functions) != 2
        ):
            valid = False
            continue
        for expected, function in zip(("RF1", "RF2"), functions, strict=True):
            update_set = function.get("update_set") if isinstance(function, Mapping) else None
            duration_ms = function.get("duration_ms") if isinstance(function, Mapping) else None
            if (
                not isinstance(function, Mapping)
                or function.get("function") != expected
                or isinstance(update_set, bool)
                or update_set != set_id
                or function.get("status") != "SUCCESS"
                or isinstance(duration_ms, bool)
                or not isinstance(duration_ms, (int, float))
                or not math.isfinite(duration_ms)
                or duration_ms < 0
            ):
                valid = False
    return valid, first_set, stream_count


def refresh_included(power_phase: Mapping[str, Any] | None, throughput_phase: Mapping[str, Any] | None) -> bool:
    power_present = isinstance(power_phase, Mapping)
    throughput_present = isinstance(throughput_phase, Mapping)
    if power_present and throughput_present:
        power_valid, _power_set = power_refresh_evidence(power_phase)
        throughput_valid, _first_set, _streams = throughput_refresh_evidence(throughput_phase)
        return power_valid and throughput_valid
    if power_present:
        return power_refresh_evidence(power_phase)[0]
    if throughput_present:
        return throughput_refresh_evidence(throughput_phase)[0]
    return False
