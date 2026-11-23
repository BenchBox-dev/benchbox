from __future__ import annotations

import functools
import importlib.util
import math
import sys
from collections.abc import Mapping, Sequence
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

POWER_QUERY_COUNTS = {"tpch": 22, "tpcds": 99}
TPCDS_POWER_STATEMENTS = 103
QPHDS_MIN_STREAMS = 4
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
    score_source = _corpus_rule().derive_outcome(_corpus_rule().facts_from_result(data)).score_source
    if benchmark_id == "tpch" and score_source != _corpus_rule().SCORE_POWER_QUERY_TIMINGS_ONLY:
        phases = data.get("phases")
        power_phase = phases.get("power_test") if isinstance(phases, Mapping) else None
        refresh_configured = isinstance(power_phase, Mapping) and power_phase.get("refresh_first_set") is not None
        if refresh_configured and not power_refresh_evidence(power_phase)[0]:
            return None
        if power_refresh_evidence(power_phase)[0]:
            refresh = [max(0.001, function["duration_ms"] / 1000.0) for function in power_phase["refresh_functions"]]
            seconds = [refresh[0], *seconds, refresh[1]]
    geometric_mean = math.exp(math.fsum(math.log(value) for value in seconds) / len(seconds))
    return (scale * 3600) / geometric_mean


def throughput_at_size(data: Mapping[str, Any]) -> float | None:
    scale = _scale_factor(data)
    phases = data.get("phases")
    block = phases.get("throughput_test") if isinstance(phases, Mapping) else None
    duration_ms = block.get("duration_ms") if isinstance(block, Mapping) else None
    if scale is None or isinstance(duration_ms, bool) or not isinstance(duration_ms, int | float) or duration_ms <= 0:
        return None
    stream_results = block.get("stream_results")
    if isinstance(stream_results, list) and not all(
        isinstance(stream, Mapping) and stream.get("success") is True for stream in stream_results
    ):
        return None
    benchmark_id = tpc_benchmark_id(data)
    if benchmark_id is None:
        executed = sum(
            1
            for query in _queries(data)
            if query.get("test_type") == "throughput" and query.get("run_type") == "measurement"
        )
        return executed * scale * 3600 / (duration_ms / 1000) if executed else None
    streams = _corpus_rule().executed_stream_count(data) or 0
    if streams <= 0:
        return None
    return POWER_QUERY_COUNTS[benchmark_id] * streams * scale * 3600 / (duration_ms / 1000)


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
        "t_tt_hours": math.fsum(throughput_seconds) / 3600.0,
        "t_dm_hours": math.fsum(maintenance_seconds) / 3600.0,
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


LOAD_SUCCESS_STATUSES = frozenset({"SUCCESS", "COMPLETED"})
TPCDS_QUERIES_PER_STREAM = POWER_QUERY_COUNTS["tpcds"]


def _positive_finite(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def qphds_input_problems(
    scale_factor: float,
    num_streams: int,
    power_seconds: float | None,
    throughput_seconds: Sequence[float | None],
    maintenance_seconds: Sequence[float | None],
    load_seconds: float | None,
) -> list[str]:
    problems: list[str] = []
    if not _positive_finite(scale_factor):
        problems.append("scale factor is not a positive number")
    if (
        isinstance(num_streams, bool)
        or not isinstance(num_streams, int)
        or num_streams < QPHDS_MIN_STREAMS
        or num_streams % 2
    ):
        problems.append(f"stream count must be an even number of at least {QPHDS_MIN_STREAMS}; got {num_streams}")
    for label, value in (("Power Test", power_seconds), ("database load", load_seconds)):
        if not _positive_finite(value):
            problems.append(f"{label} elapsed time is missing or not positive")
    for label, values in (("Throughput Test", throughput_seconds), ("Data Maintenance Test", maintenance_seconds)):
        if len(values) != 2:
            problems.append(f"two {label} elapsed times are required; got {len(values)}")
            continue
        for index, value in enumerate(values, start=1):
            if not _positive_finite(value):
                problems.append(f"{label} {index} elapsed time is missing or not positive")
    return problems


def qphds_power_problems(present: bool, success: object, statements: object) -> list[str]:
    if not present:
        return ["Power Test did not run"]
    problems: list[str] = []
    if success is not True:
        problems.append("Power Test did not complete successfully")
    if statements != TPCDS_POWER_STATEMENTS:
        problems.append(
            f"Power Test ran {statements} statements instead of {TPCDS_POWER_STATEMENTS} "
            f"(all {TPCDS_QUERIES_PER_STREAM} queries)"
        )
    return problems


def qphds_refresh_runs(stream_count: int) -> int:
    return max(stream_count // 2, 1)


def qphds_maintenance_problems(
    present: bool, success: object, refresh_sets: set[int], label: str, refresh_runs: int
) -> list[str]:
    if not present:
        return [f"{label} did not run"]
    problems: list[str] = []
    if success is not True:
        problems.append(f"{label} did not complete successfully")
    if len(refresh_sets) != refresh_runs:
        problems.append(f"{label} ran {len(refresh_sets)} refresh runs instead of {refresh_runs}")
    return problems


def _contiguous(values: set[int]) -> bool:
    return bool(values) and max(values) - min(values) + 1 == len(values)


def qphds_refresh_window_problems(first: set[int], second: set[int]) -> list[str]:
    if first and second and not (_contiguous(first) and _contiguous(second) and min(second) == max(first) + 1):
        return ["Data Maintenance Test 2 did not use the refresh sets that follow Data Maintenance Test 1"]
    return []


def refresh_sets_of(operations: object) -> set[int]:
    if not isinstance(operations, list | tuple):
        return set()
    sets: set[int] = set()
    for operation in operations:
        value = operation.get("refresh_set") if isinstance(operation, Mapping) else operation
        if isinstance(value, int) and not isinstance(value, bool):
            sets.add(value)
    return sets


def qphds_load_evidence(
    timed: Sequence[tuple[str, str | None, Any]],
    *,
    data_loading_captured: bool,
    reused: bool | None,
    settle: tuple[bool, Any] | None,
) -> tuple[float | None, list[str]]:
    if not data_loading_captured:
        return None, ["database load time was not captured"]
    problems: list[str] = []
    total_ms = 0.0
    if reused is None:
        problems.append("database load reuse was not recorded")
    elif reused:
        problems.append("database load was reused, so its time was not measured in this run")
    for label, status, duration_ms in timed:
        if status not in LOAD_SUCCESS_STATUSES:
            problems.append(f"database load {label} did not complete successfully ({status})")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int | float):
            problems.append(f"database load {label} duration was not captured")
        else:
            total_ms += duration_ms
    if settle is not None:
        settled, waited_seconds = settle
        if not settled:
            problems.append("database load merge settle did not complete")
        if isinstance(waited_seconds, bool) or not isinstance(waited_seconds, int | float):
            problems.append("database load merge settle duration was not captured")
        else:
            total_ms += waited_seconds * 1000.0
    return total_ms / 1000.0, problems


_STORED_LOAD_LABELS = (
    ("schema_creation", "table creation"),
    ("data_loading", "data loading"),
    ("statistics", "statistics gathering"),
    ("post_load_maintenance", "post-load maintenance"),
)


def _stored_load_evidence(phases: Mapping[str, Any]) -> tuple[float | None, list[str]]:
    loading = phases.get("data_loading")
    if not isinstance(loading, Mapping) or str(loading.get("status") or "").upper() == "NOT_RUN":
        return qphds_load_evidence([], data_loading_captured=False, reused=None, settle=None)
    timed = []
    for name, label in _STORED_LOAD_LABELS:
        block = phases.get(name)
        if isinstance(block, Mapping) and str(block.get("status") or "").upper() != "NOT_RUN":
            timed.append((label, str(block.get("status") or "").upper(), block.get("duration_ms")))
    settle = phases.get("merge_settle")
    reused = loading.get("database_reused")
    return qphds_load_evidence(
        timed,
        data_loading_captured=True,
        reused=reused if isinstance(reused, bool) else None,
        settle=(settle.get("settled") is True, settle.get("waited_seconds")) if isinstance(settle, Mapping) else None,
    )


def _qphds(data: Mapping[str, Any], scale: float) -> float | None:
    phases = data.get("phases")
    if not isinstance(phases, Mapping):
        return None
    power = phases.get("power_test")
    stream_count = _corpus_rule().executed_stream_count(data) or 0
    names = ("throughput_test", "throughput_test_2", "maintenance_test", "maintenance_test_2")
    blocks = [phases.get(name) for name in names]
    problems = qphds_power_problems(
        isinstance(power, Mapping),
        power.get("status") == "COMPLETED" if isinstance(power, Mapping) else None,
        power.get("query_count") if isinstance(power, Mapping) else None,
    )
    problems += [
        f"{name} did not complete" for name, block in zip(names[:2], blocks[:2], strict=True) if not _completed(block)
    ]
    problems += [
        f"{name} streams differ"
        for name, block in zip(names[:2], blocks[:2], strict=True)
        if isinstance(block, Mapping) and _corpus_rule()._succeeded_streams(block) != stream_count
    ]
    refresh_runs = qphds_refresh_runs(stream_count)
    sets = []
    for index, block in enumerate(blocks[2:], start=1):
        present = isinstance(block, Mapping)
        sets.append(refresh_sets_of(block.get("operations")) if present else set())
        problems += qphds_maintenance_problems(
            present, _completed(block) if present else None, sets[-1], f"Data Maintenance Test {index}", refresh_runs
        )
    problems += qphds_refresh_window_problems(sets[0], sets[1])
    load_seconds, load_problems = _stored_load_evidence(phases)
    problems += load_problems
    power_ms = _positive_ms(power)
    durations = [_positive_ms(block) for block in blocks]
    seconds = [None if duration is None else duration / 1000.0 for duration in durations]
    problems += qphds_input_problems(
        scale, stream_count, None if power_ms is None else power_ms / 1000.0, seconds[:2], seconds[2:], load_seconds
    )
    if problems or power_ms is None or load_seconds is None or any(value is None for value in seconds):
        return None
    return qphds_at_size(scale, stream_count, power_ms / 1000.0, seconds[:2], seconds[2:], load_seconds)


@functools.cache
def _corpus_rule() -> Any:
    try:
        from benchbox.core.results import corpus_rule

        return corpus_rule
    except ImportError:
        path = Path(__file__).with_name("corpus_rule.py")
        spec = importlib.util.spec_from_file_location("_benchbox_corpus_rule", path)
        if spec is None or spec.loader is None:
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


def _gateway_routed(data: Mapping[str, Any]) -> bool:
    platform = data.get("platform")
    gateway = platform.get("gateway") if isinstance(platform, Mapping) else None
    return isinstance(gateway, Mapping) and bool(gateway.get("routed"))


def combined_score(data: Mapping[str, Any]) -> float | None:
    benchmark_id = tpc_benchmark_id(data)
    scale = _scale_factor(data)
    if benchmark_id is None or scale is None:
        return None
    if _corpus_rule().composite_scale_refusal(benchmark_id, scale, gateway_routed=_gateway_routed(data)):
        return None
    facts = _corpus_rule().facts_from_result(data)
    if _corpus_rule().build_context(facts).streams_ok is not True:
        return None
    if benchmark_id == "tpcds":
        return _qphds(data, scale)
    phases = data.get("phases")
    if not isinstance(phases, Mapping) or qphh_structure_problems(
        phases.get("power_test"), phases.get("throughput_test"), scale_factor=scale
    ):
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
    stream_count = expected_streams
    if isinstance(stream_count, bool) or not isinstance(stream_count, int):
        stream_count = _corpus_rule()._succeeded_streams(throughput_phase) or 0
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


def qphh_structure_problems(power_phase: Any, throughput_phase: Any, *, scale_factor: float | None = None) -> list[str]:
    reasons: list[str] = []
    if not isinstance(power_phase, Mapping) or power_phase.get("status") != "COMPLETED":
        reasons.append("Power phase did not complete")
    elif power_phase.get("query_count") != POWER_QUERY_COUNTS["tpch"]:
        reasons.append("Power phase must complete all 22 TPC-H queries")

    power_refresh_valid, power_first_set = power_refresh_evidence(power_phase)
    if not power_refresh_valid:
        reasons.append("Power phase must contain successful RF1 and RF2 evidence for one update set")

    if not isinstance(throughput_phase, Mapping) or throughput_phase.get("status") != "COMPLETED":
        reasons.append("Throughput phase did not complete")
    streams = throughput_phase.get("stream_results") if isinstance(throughput_phase, Mapping) else None
    stream_count = (
        sum(1 for stream in streams if isinstance(stream, Mapping) and stream.get("success") is True)
        if isinstance(streams, list)
        else 0
    )
    if scale_factor is None:
        minimum_ok = stream_count >= _corpus_rule().min_throughput_streams()
    else:
        minimum_ok = _corpus_rule()._streams_ok("tpch", scale_factor, stream_count, _corpus_rule().load_data()) is True
    if not minimum_ok:
        reasons.append("QphH requires the scale's minimum number of successful throughput streams")

    throughput_refresh_valid, throughput_first_set, actual_stream_count = throughput_refresh_evidence(
        throughput_phase, stream_count
    )
    if not throughput_refresh_valid:
        reasons.append("Throughput phase must complete one successful RF1/RF2 pair per query stream")
    if power_first_set is not None and throughput_first_set != power_first_set + 1:
        reasons.append("Throughput refresh sets must start at the set immediately after the Power set")
    if actual_stream_count != stream_count:
        reasons.append("Throughput refresh pair count must match the requested stream count")
    return reasons
