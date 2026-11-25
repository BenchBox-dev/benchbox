from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).with_name("corpus_rule_data.json")

PHASE_POWER = "power"
PHASE_THROUGHPUT = "throughput"
PHASE_REFRESH = "refresh"
PHASE_OTHER_TIMED = "other_timed"
TIMED_PHASES = frozenset({PHASE_POWER, PHASE_THROUGHPUT, PHASE_REFRESH, PHASE_OTHER_TIMED})

CLASS_FULL_SPEC = "full_spec"
CLASS_PARTIAL = "partial"
CLASS_POWER_ONLY = "power_only"
CLASS_THROUGHPUT_ONLY = "throughput_only"
CLASS_SUBSCALE = "subscale"
CLASS_NONSTANDARD_SCALE = "nonstandard_scale"
CLASS_NONE = "none"
CLASS_UNDETERMINED = "undetermined"

LANE_COMBINED = "combined"
LANE_POWER_ONLY = "power_only"
LANE_THROUGHPUT = "throughput"
LANE_SUBSCALE = "subscale"
LANE_OWN = "own"

SCORE_QPH_AND_POWER = "qph_and_power_at_size"
SCORE_FROM_TIMINGS = "from_timings"
SCORE_POWER_QUERY_TIMINGS_ONLY = "power_at_size_from_query_timings_only"
SCORE_POWER_FROM_TIMINGS = "power_at_size_from_timings"
SCORE_THROUGHPUT_FROM_TIMINGS = "throughput_from_timings"
SCORE_TIMINGS_ONLY = "timings_only"
SCORE_OWN = "own"

HELD_NO_TIMED_PHASE = "no_timed_phase"
HELD_GATEWAY_ROUTED = "gateway_routed"
HELD_KNOWN_DEFECT = "known_defect"
HELD_FAILED_VALIDATION = "failed_validation"

BAND_STANDARD = "standard"
BAND_SUBSCALE = "subscale"
BAND_NONSTANDARD = "nonstandard"

KIND_TPC = "tpc"
KIND_TPCDI = "tpcdi"
KIND_OTHER = "other"

MISSING_PHASES = "phases"
MISSING_BENCHMARK = "benchmark"
MISSING_SCALE_FACTOR = "scale_factor"
MISSING_SCALE_REGISTRY = "scale_registry_entry"
MISSING_STREAMS = "streams"
MISSING_TPCDI_RULE = "tpcdi_admission_rule"

DELEGATE_TPCDI = "tpcdi"

NON_CLEAN_VALIDATION_STATUSES = frozenset(
    {"failed", "interrupted", "timed_out", "partial", "error", "not_run", "not_validated", "uncertain", "unknown"}
)

NON_CLEAN_TRANSLATION_STATUSES = frozenset({"fallback", "failed"})

BENCHMARK_LABELS = {"tpch": "TPC-H", "tpcds": "TPC-DS"}
CLASS_WORDS = {
    CLASS_FULL_SPEC: "Full specification run: power, throughput and refresh",
    CLASS_PARTIAL: "Partial run: not every required phase ran",
    CLASS_POWER_ONLY: "Power test only",
    CLASS_THROUGHPUT_ONLY: "Throughput test only",
    CLASS_SUBSCALE: "Scale below the TPC minimum",
}
NONSTANDARD_SCALE_LABEL = "Non-standard scale: compared only at the same scale"
ROW_COUNTS_NOT_CHECKED_LABEL = "Row counts not checked"
SINGLE_ITERATION_LABEL = "Single iteration"
MIN_MEASURED_ITERATIONS = 3
HELD_LABELS = {
    HELD_GATEWAY_ROUTED: "Not ranked: routed through a gateway",
    HELD_KNOWN_DEFECT: "Not ranked: known defect",
    HELD_FAILED_VALIDATION: "Not ranked: validation did not pass",
}


@dataclass(frozen=True)
class Facts:
    benchmark: str | None
    scale_factor: float | None
    phases: frozenset[str] | None
    streams: int | None = None
    gateway_routed: bool = False
    known_defect: bool = False
    validation_failed: bool = False
    row_counts_checked: bool = True
    public_validation_refused: bool = False
    measured_iterations: int | None = None


@dataclass(frozen=True)
class Outcome:
    class_name: str
    public_admission: bool
    maintainer_admission: bool
    lanes: tuple[str, ...]
    score_source: str | None
    labels: tuple[str, ...]
    held_reason: str | None = None
    missing_facts: tuple[str, ...] = ()
    row: str | None = None

    @property
    def ranked(self) -> bool:
        return bool(self.lanes) and self.held_reason is None and self.class_name != CLASS_UNDETERMINED


@dataclass(frozen=True)
class Context:
    kind: str | None
    band: str | None
    streams_ok: bool | None
    power: bool
    throughput: bool
    refresh: bool
    timed: bool
    registry_known: bool


@dataclass(frozen=True)
class Row:
    name: str
    matches: Callable[[Context], bool]
    class_name: str
    public_admission: bool
    maintainer_admission: bool
    lanes: tuple[str, ...]
    score_source: str | None
    delegate: str | None = None


def _standard_tpc(context: Context) -> bool:
    return context.kind == KIND_TPC and context.band == BAND_STANDARD


RULE_TABLE: tuple[Row, ...] = (
    Row("no_timed_phase", lambda c: not c.timed, CLASS_NONE, False, False, (), None),
    Row("tpcdi", lambda c: c.kind == KIND_TPCDI, CLASS_NONE, False, False, (), None, DELEGATE_TPCDI),
    Row(
        "tpc_subscale",
        lambda c: c.kind == KIND_TPC and c.band == BAND_SUBSCALE,
        CLASS_SUBSCALE,
        False,
        True,
        (LANE_SUBSCALE,),
        SCORE_POWER_FROM_TIMINGS,
    ),
    Row(
        "nonstandard_scale",
        lambda c: c.band == BAND_NONSTANDARD,
        CLASS_NONSTANDARD_SCALE,
        True,
        True,
        (),
        SCORE_TIMINGS_ONLY,
    ),
    Row(
        "tpc_full_spec",
        lambda c: _standard_tpc(c) and c.power and c.throughput and c.refresh and c.streams_ok is True,
        CLASS_FULL_SPEC,
        True,
        True,
        (LANE_COMBINED,),
        SCORE_QPH_AND_POWER,
    ),
    Row(
        "tpc_power_throughput_partial",
        lambda c: _standard_tpc(c) and c.power and c.throughput and (not c.refresh or c.streams_ok is False),
        CLASS_PARTIAL,
        True,
        True,
        (LANE_POWER_ONLY, LANE_THROUGHPUT),
        SCORE_FROM_TIMINGS,
    ),
    Row(
        "tpc_power_refresh_partial",
        lambda c: _standard_tpc(c) and c.power and c.refresh and not c.throughput,
        CLASS_PARTIAL,
        True,
        True,
        (LANE_POWER_ONLY,),
        SCORE_POWER_QUERY_TIMINGS_ONLY,
    ),
    Row(
        "tpc_power_only",
        lambda c: _standard_tpc(c) and c.power and not c.throughput and not c.refresh,
        CLASS_POWER_ONLY,
        True,
        True,
        (LANE_POWER_ONLY,),
        SCORE_POWER_FROM_TIMINGS,
    ),
    Row(
        "tpc_throughput_only",
        lambda c: _standard_tpc(c) and c.throughput and not c.power,
        CLASS_THROUGHPUT_ONLY,
        True,
        True,
        (LANE_THROUGHPUT,),
        SCORE_THROUGHPUT_FROM_TIMINGS,
    ),
    Row(
        "tpc_no_ranked_phase",
        lambda c: _standard_tpc(c) and not c.power and not c.throughput,
        CLASS_PARTIAL,
        True,
        True,
        (),
        None,
    ),
    Row(
        "other_standard_scale",
        lambda c: c.kind == KIND_OTHER and c.band == BAND_STANDARD,
        CLASS_NONE,
        True,
        True,
        (LANE_OWN,),
        SCORE_OWN,
    ),
)


@cache
def load_data(path: Path = DATA_PATH) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["schema_version"] != 1 or not isinstance(data["scales"], dict) or not data["scales"]:
            raise ValueError("invalid corpus rule data")
        return data
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(
            "corpus rule data benchbox/core/results/corpus_rule_data.json is missing or unreadable"
        ) from error


def min_throughput_streams(data: Mapping[str, Any] | None = None) -> int:
    data = data if data is not None else load_data()
    return int(data["min_throughput_streams"])


def row_count_reference_exists(benchmark_id: Any, scale_factor: Any, data: Mapping[str, Any] | None = None) -> bool:
    data = data if data is not None else load_data()
    benchmark = normalize_benchmark_id(benchmark_id)
    scale = _scale_value(scale_factor)
    if benchmark is None or scale is None or not math.isfinite(scale):
        return False
    scales = data["row_count_reference_scales"].get(benchmark, ())
    return any(abs(scale - recorded) < 1e-9 for recorded in scales)


def normalize_benchmark_id(benchmark_id: Any) -> str | None:
    if not isinstance(benchmark_id, str) or not benchmark_id.strip():
        return None
    lowered = benchmark_id.strip().lower()
    squashed = "".join(character for character in lowered if character.isalnum())
    return squashed if squashed in ("tpch", "tpcds", "tpcdi") else lowered


def _scale_value(scale_factor: Any) -> float | None:
    if isinstance(scale_factor, bool) or not isinstance(scale_factor, int | float):
        return None
    return float(scale_factor)


def is_standard_scale(benchmark_id: Any, scale_factor: float, data: Mapping[str, Any] | None = None) -> bool | None:
    data = data if data is not None else load_data()
    benchmark = normalize_benchmark_id(benchmark_id)
    entry = data["scales"].get(benchmark)
    if entry is None:
        return None
    return math.isfinite(scale_factor) and any(abs(scale_factor - option) < 1e-9 for option in entry["standard"])


def _scale_band(benchmark: str, scale: float, data: Mapping[str, Any]) -> str | None:
    if benchmark in data["tpc_benchmarks"] and 0 < scale < 1.0:
        return BAND_SUBSCALE
    standard = is_standard_scale(benchmark, scale, data)
    if standard is None:
        return None
    return BAND_STANDARD if standard else BAND_NONSTANDARD


def _streams_ok(benchmark: str, scale: float, streams: int | None, data: Mapping[str, Any]) -> bool | None:
    minima = data["stream_minimum"].get(benchmark, {})
    minimum = next((value for key, value in minima.items() if abs(float(key) - scale) < 1e-9), None)
    if streams is None or minimum is None:
        return None
    return streams >= minimum


def build_context(facts: Facts, data: Mapping[str, Any] | None = None) -> Context:
    data = data if data is not None else load_data()
    phases = facts.phases or frozenset()
    benchmark = normalize_benchmark_id(facts.benchmark)
    kind: str | None = None
    band: str | None = None
    streams_ok: bool | None = None
    registry_known = False
    if benchmark is not None:
        if benchmark in data["tpc_benchmarks"]:
            kind = KIND_TPC
        elif benchmark == "tpcdi":
            kind = KIND_TPCDI
        else:
            kind = KIND_OTHER
        registry_known = benchmark in data["scales"]
        scale = _scale_value(facts.scale_factor)
        if scale is not None:
            band = _scale_band(benchmark, scale, data)
            streams_ok = _streams_ok(benchmark, scale, facts.streams, data)
    return Context(
        kind=kind,
        band=band,
        streams_ok=streams_ok,
        power=PHASE_POWER in phases,
        throughput=PHASE_THROUGHPUT in phases,
        refresh=PHASE_REFRESH in phases,
        timed=bool(phases & TIMED_PHASES),
        registry_known=registry_known,
    )


def missing_facts(facts: Facts, context: Context) -> tuple[str, ...]:
    if facts.phases is None:
        return (MISSING_PHASES,)
    if normalize_benchmark_id(facts.benchmark) is None:
        return (MISSING_BENCHMARK,)
    if _scale_value(facts.scale_factor) is None:
        return (MISSING_SCALE_FACTOR,)
    if not context.registry_known or context.band is None:
        return (MISSING_SCALE_REGISTRY,)
    if (
        _standard_tpc(context)
        and context.power
        and context.throughput
        and context.refresh
        and context.streams_ok is None
    ):
        return (MISSING_STREAMS,)
    return ()


def _undetermined(missing: tuple[str, ...], row: str | None = None) -> Outcome:
    return Outcome(CLASS_UNDETERMINED, False, False, (), None, (), None, missing, row)


def tpcdi_default_admission(facts: Facts) -> Outcome:
    return Outcome(CLASS_UNDETERMINED, True, False, (), None, (), None, (MISSING_TPCDI_RULE,), "tpcdi")


def _labels(row: Row, benchmark: str | None, data: Mapping[str, Any]) -> tuple[str, ...]:
    labels: list[str] = []
    base = benchmark if benchmark in BENCHMARK_LABELS else data["tpc_derived_benchmarks"].get(benchmark)
    if row.lanes and base in BENCHMARK_LABELS:
        labels.append(f"Derived from {BENCHMARK_LABELS[base]}, not an official TPC result")
        if row.class_name in CLASS_WORDS:
            labels.append(CLASS_WORDS[row.class_name])
    if row.class_name == CLASS_NONSTANDARD_SCALE:
        labels.append(NONSTANDARD_SCALE_LABEL)
    return tuple(labels)


def _held_reason(facts: Facts) -> str | None:
    if facts.gateway_routed:
        return HELD_GATEWAY_ROUTED
    if facts.known_defect:
        return HELD_KNOWN_DEFECT
    if facts.validation_failed:
        return HELD_FAILED_VALIDATION
    return None


def _hold(outcome: Outcome, reason: str, *, validation_failed: bool = False) -> Outcome:
    keeps_admission = reason == HELD_KNOWN_DEFECT and not validation_failed and outcome.class_name != CLASS_UNDETERMINED
    return replace(
        outcome,
        public_admission=keeps_admission and outcome.public_admission,
        maintainer_admission=keeps_admission and outcome.maintainer_admission,
        lanes=(),
        score_source=None,
        labels=(*outcome.labels, HELD_LABELS[reason]),
        held_reason=reason,
    )


def derive_outcome(
    facts: Facts,
    *,
    table: Sequence[Row] = RULE_TABLE,
    delegates: Mapping[str, Callable[[Facts], Outcome]] | None = None,
    data: Mapping[str, Any] | None = None,
) -> Outcome:
    data = data if data is not None else load_data()
    if facts.phases is None:
        outcome = _undetermined((MISSING_PHASES,))
        held = _held_reason(facts)
        return _hold(outcome, held, validation_failed=facts.validation_failed) if held else outcome
    context = build_context(facts, data)
    row = next((candidate for candidate in table if candidate.matches(context)), None)
    held = _held_reason(facts)
    if row is None:
        outcome = _undetermined(missing_facts(facts, context))
        return _hold(outcome, held, validation_failed=facts.validation_failed) if held else outcome
    if row.name == "no_timed_phase":
        return Outcome(row.class_name, False, False, (), None, (), HELD_NO_TIMED_PHASE, (), row.name)
    if missing_facts(facts, context):
        outcome = _undetermined(missing_facts(facts, context), row.name)
        return _hold(outcome, held, validation_failed=facts.validation_failed) if held else outcome
    if row.delegate is not None:
        delegate = (delegates or {}).get(row.delegate)
        outcome = delegate(facts) if delegate is not None else tpcdi_default_admission(facts)
    else:
        benchmark = normalize_benchmark_id(facts.benchmark)
        outcome = Outcome(
            row.class_name,
            row.public_admission,
            row.maintainer_admission,
            row.lanes,
            row.score_source,
            _labels(row, benchmark, data),
            None,
            (),
            row.name,
        )
    if facts.public_validation_refused:
        outcome = replace(outcome, public_admission=False)
    if held is not None:
        return _hold(outcome, held, validation_failed=facts.validation_failed)
    if outcome.class_name == CLASS_UNDETERMINED:
        return outcome
    return replace(outcome, labels=(*outcome.labels, *_evidence_labels(facts)))


def _evidence_labels(facts: Facts) -> tuple[str, ...]:
    labels: list[str] = []
    if not facts.row_counts_checked:
        labels.append(ROW_COUNTS_NOT_CHECKED_LABEL)
    if facts.measured_iterations is not None and facts.measured_iterations < MIN_MEASURED_ITERATIONS:
        labels.append(SINGLE_ITERATION_LABEL)
    return tuple(labels)


def _executed(block: Any) -> bool:
    return isinstance(block, Mapping) and bool(block) and str(block.get("status") or "").upper() != "NOT_RUN"


def _status_completed(block: Any) -> bool:
    return isinstance(block, Mapping) and str(block.get("status") or "").upper() == "COMPLETED"


def _all_succeed(items: Any, expected: int | None = None) -> bool:
    return (
        isinstance(items, list)
        and bool(items)
        and (expected is None or len(items) == expected)
        and all(isinstance(item, Mapping) and str(item.get("status") or "").upper() == "SUCCESS" for item in items)
    )


def _tpch_refresh_clean(phases: Mapping[str, Any]) -> bool:
    power = phases.get("power_test")
    functions = power.get("refresh_functions") if isinstance(power, Mapping) else None
    if not _all_succeed(functions, 2):
        return False
    throughput = phases.get("throughput_test")
    if not _executed(throughput):
        return True
    refresh_stream = throughput.get("refresh_stream") if isinstance(throughput, Mapping) else None
    return (
        isinstance(refresh_stream, Mapping)
        and str(refresh_stream.get("status") or "") == "COMPLETED"
        and _all_succeed(refresh_stream.get("pairs"))
    )


def _tpcds_maintenance_clean(phases: Mapping[str, Any]) -> bool:
    for name in ("maintenance_test", "maintenance_test_2"):
        block = phases.get(name)
        if not _status_completed(block) or not _all_succeed(block.get("operations")):
            return False
    return True


def _refresh_evidence(data: Mapping[str, Any], phases: Mapping[str, Any]) -> bool:
    benchmark = normalize_benchmark_id(_benchmark_id(data))
    if benchmark == "tpch":
        return _tpch_refresh_clean(phases)
    if benchmark == "tpcds":
        return _tpcds_maintenance_clean(phases)
    return _tpch_refresh_clean(phases) or _tpcds_maintenance_clean(phases)


def _failed(block: Any, success: str) -> bool:
    status = str(block.get("status") or "").upper() if isinstance(block, Mapping) else ""
    return status not in ("", "NOT_RUN", success)


def _any_failed(items: Any) -> bool:
    return isinstance(items, list) and any(_failed(item, "SUCCESS") for item in items)


def _phase_block(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    phases = data.get("phases")
    block = phases.get(name) if isinstance(phases, Mapping) else None
    return block if isinstance(block, Mapping) else {}


def power_refresh_failed(data: Mapping[str, Any]) -> bool:
    return _any_failed(_phase_block(data, "power_test").get("refresh_functions"))


def throughput_refresh_failed(data: Mapping[str, Any]) -> bool:
    stream = _phase_block(data, "throughput_test").get("refresh_stream")
    return _failed(stream, "COMPLETED") or (isinstance(stream, Mapping) and _any_failed(stream.get("pairs")))


def _maintenance_failed(data: Mapping[str, Any]) -> bool:
    for name in ("maintenance_test", "maintenance_test_2"):
        block = _phase_block(data, name)
        if _failed(block, "COMPLETED") or _any_failed(block.get("operations")):
            return True
    return False


def _refresh_failed(data: Mapping[str, Any]) -> bool:
    benchmark = normalize_benchmark_id(_benchmark_id(data))
    tpch = power_refresh_failed(data) or throughput_refresh_failed(data)
    if benchmark == "tpch":
        return tpch
    if benchmark == "tpcds":
        return _maintenance_failed(data)
    return tpch or _maintenance_failed(data)


def _benchmark_id(data: Mapping[str, Any]) -> Any:
    benchmark = data.get("benchmark")
    return benchmark.get("id") if isinstance(benchmark, Mapping) else None


def _measured_iterations(data: Mapping[str, Any]) -> frozenset[int]:
    queries = data.get("queries")
    if not isinstance(queries, list):
        return frozenset()
    iterations: set[int] = set()
    for query in queries:
        if not isinstance(query, Mapping) or (query.get("run_type") or "measurement") != "measurement":
            continue
        iteration = query.get("iter", query.get("iteration", 1))
        duration = query.get("ms", query.get("execution_time_ms"))
        status = query.get("status")
        if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration <= 0:
            continue
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int | float)
            or not math.isfinite(duration)
            or duration <= 0
        ):
            continue
        if status is not None and str(status).upper() not in {"SUCCESS", "PASS"}:
            continue
        iterations.add(iteration)
    return frozenset(iterations)


def legacy_query_timing_defaults(data: Mapping[str, Any]) -> bool:
    version = data.get("result_schema_version", data.get("version"))
    return version in ("2.0", "2.1", "2.2", "2.3")


def phases_ran(data: Mapping[str, Any]) -> frozenset[str] | None:
    phases = data.get("phases")
    if "phases" not in data:
        if legacy_query_timing_defaults(data) and _measured_iterations(data):
            return frozenset({PHASE_OTHER_TIMED})
        return None
    if not isinstance(phases, Mapping):
        return None
    ran: set[str] = set()
    if _executed(phases.get("power_test")):
        ran.add(PHASE_POWER)
    if _executed(phases.get("throughput_test")):
        ran.add(PHASE_THROUGHPUT)
    if _refresh_evidence(data, phases) or _refresh_failed(data):
        ran.add(PHASE_REFRESH)
    if not ran & {PHASE_POWER, PHASE_THROUGHPUT} and _measured_iterations(data):
        ran.add(PHASE_OTHER_TIMED)
    return frozenset(ran)


def _succeeded_streams(block: Any) -> int | None:
    streams = block.get("stream_results") if isinstance(block, Mapping) else None
    if not isinstance(streams, list) or not streams:
        return None
    return sum(1 for stream in streams if isinstance(stream, Mapping) and stream.get("success") is True)


def throughput_stream_count(data: Mapping[str, Any]) -> int | None:
    phases = data.get("phases")
    return _succeeded_streams(phases.get("throughput_test")) if isinstance(phases, Mapping) else None


def executed_stream_count(data: Mapping[str, Any]) -> int | None:
    first = throughput_stream_count(data)
    if first is None or normalize_benchmark_id(_benchmark_id(data)) != "tpcds":
        return first
    return min(first, _succeeded_streams(data["phases"].get("throughput_test_2")) or 0)


def _gateway_routed(data: Mapping[str, Any]) -> bool:
    platform = data.get("platform")
    gateway = platform.get("gateway") if isinstance(platform, Mapping) else None
    return isinstance(gateway, Mapping) and bool(gateway.get("routed"))


def row_counts_checked(data: Mapping[str, Any]) -> bool:
    queries = data.get("queries")
    statuses: list[str] = []
    for query in queries if isinstance(queries, list) else ():
        if (
            not isinstance(query, Mapping)
            or str(query.get("run_type") or "measurement").strip().lower() != "measurement"
        ):
            continue
        evidence = query.get("row_count_validation")
        status = evidence.get("status") if isinstance(evidence, Mapping) else None
        statuses.append(str(status or "").strip().upper())
    return "PASSED" in statuses and "UNVALIDATED" not in statuses


def _status_text(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("status")
    if value is None:
        return None
    return str(value).strip().lower() or None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _query_row_failed(query: Any) -> bool:
    if not isinstance(query, Mapping):
        return False
    if str(query.get("run_type") or "measurement").strip().lower() != "measurement":
        return False
    status = query.get("status")
    return status is not None and str(status).upper() not in {"SUCCESS", "SKIPPED"}


def _recorded_failed_queries(data: Mapping[str, Any]) -> int:
    summary = data.get("summary")
    queries = summary.get("queries") if isinstance(summary, Mapping) else None
    if isinstance(queries, Mapping):
        failed = _int_or_none(queries.get("failed"))
        if failed is not None and failed > 0:
            return failed
        total = _int_or_none(queries.get("total"))
        passed = _int_or_none(queries.get("passed"))
        skipped = _int_or_none(queries.get("skipped")) or 0
        if total is not None and passed is not None and total > passed + skipped:
            return total - passed - skipped
    rows = data.get("queries")
    return sum(1 for row in rows if _query_row_failed(row)) if isinstance(rows, list) else 0


def _row_count_validation_failed(data: Mapping[str, Any]) -> bool:
    rows = data.get("queries")
    return isinstance(rows, list) and any(
        isinstance(row, Mapping)
        and isinstance(evidence := row.get("row_count_validation"), Mapping)
        and str(evidence.get("status") or "").strip().upper() == "FAILED"
        for row in rows
    )


def _recorded_validation_status(data: Mapping[str, Any]) -> str | None:
    summary = data.get("summary")
    return _status_text(summary.get("validation")) if isinstance(summary, Mapping) else None


def _translation_non_clean(data: Mapping[str, Any]) -> bool:
    execution = data.get("execution")
    status = _status_text(execution.get("translation")) if isinstance(execution, Mapping) else None
    return status in NON_CLEAN_TRANSLATION_STATUSES


def facts_from_result(
    data: Mapping[str, Any],
    *,
    known_defects: Sequence[str] = (),
    validation_status: str | None = None,
    failed_queries: int | None = None,
) -> Facts:
    benchmark = data.get("benchmark")
    benchmark = benchmark if isinstance(benchmark, Mapping) else {}
    benchmark_id = benchmark.get("id")
    status = _status_text(validation_status) if validation_status is not None else _recorded_validation_status(data)
    failed = failed_queries if failed_queries is not None else _recorded_failed_queries(data)
    return Facts(
        benchmark=benchmark_id if isinstance(benchmark_id, str) else None,
        scale_factor=_scale_value(benchmark.get("scale_factor")),
        phases=phases_ran(data),
        streams=executed_stream_count(data),
        gateway_routed=_gateway_routed(data),
        known_defect=any(isinstance(item, str) and item for item in known_defects),
        validation_failed=(
            status in NON_CLEAN_VALIDATION_STATUSES
            or failed > 0
            or _row_count_validation_failed(data)
            or _translation_non_clean(data)
        ),
        row_counts_checked=row_counts_checked(data),
        public_validation_refused=status == "no_reference"
        and row_count_reference_exists(benchmark_id, benchmark.get("scale_factor")),
        measured_iterations=len(_measured_iterations(data)) or None,
    )


def sidecar_known_defects(result_path: Path) -> list[str]:
    try:
        payload = json.loads(result_path.with_name(f"{result_path.stem}.manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    recorded = payload.get("known_defects") if isinstance(payload, dict) else None
    return [item for item in recorded if isinstance(item, str) and item] if isinstance(recorded, list) else []


def facts_from_path(result_path: Path) -> Facts:
    data = json.loads(result_path.read_text(encoding="utf-8"))
    return facts_from_result(data if isinstance(data, dict) else {}, known_defects=sidecar_known_defects(result_path))


def composite_scale_refusal(
    benchmark_id: str | None, scale_factor: float | None, *, gateway_routed: bool = False
) -> str | None:
    if gateway_routed:
        return "routed through a gateway"
    outcome = derive_outcome(Facts(benchmark_id, scale_factor, frozenset({PHASE_POWER})))
    if outcome.class_name == CLASS_SUBSCALE:
        return "below the standard scale"
    if outcome.class_name == CLASS_NONSTANDARD_SCALE:
        return "at a non-standard scale"
    return None


def public_refusal(benchmark_id: str | None, scale_factor: float | None, *, gateway_routed: bool = False) -> str | None:
    if gateway_routed:
        return HELD_GATEWAY_ROUTED
    outcome = derive_outcome(Facts(benchmark_id, scale_factor, frozenset({PHASE_POWER})))
    if outcome.class_name == CLASS_SUBSCALE and not outcome.public_admission:
        return CLASS_SUBSCALE
    return None


def public_refusal_for_facts(facts: Facts) -> str | None:
    if facts.phases is None:
        return CLASS_UNDETERMINED
    if facts.validation_failed or facts.public_validation_refused:
        return HELD_FAILED_VALIDATION
    outcome = derive_outcome(facts)
    held = _held_reason(facts)
    if held == HELD_GATEWAY_ROUTED:
        return held
    if outcome.class_name == CLASS_SUBSCALE and not outcome.public_admission:
        return CLASS_SUBSCALE
    if (
        normalize_benchmark_id(facts.benchmark) == "tpcdi"
        and outcome.held_reason == HELD_NO_TIMED_PHASE
        and held is None
    ):
        outcome = tpcdi_default_admission(facts)
    if outcome.held_reason not in (None, HELD_KNOWN_DEFECT) and not outcome.public_admission:
        return outcome.held_reason
    if not outcome.public_admission:
        return outcome.class_name
    return None
