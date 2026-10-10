#!/usr/bin/env python3

from __future__ import annotations

import collections
import datetime as _dt
import json
import math
import pathlib
import re
import sys
from typing import NamedTuple

COMPANION_SUFFIXES = (".manifest.json", ".plans.json", ".tuning.json", ".applied.json", ".override.json")
LEGACY_MANIFEST_NAME = "submission-manifest.json"

MINIMUM_PLATFORMS_PER_COHORT = 3
NON_CLEAN_VALIDATION_STATUSES = frozenset(
    {"failed", "interrupted", "partial", "error", "not_run", "not_validated", "uncertain", "unknown"}
)
NON_CLEAN_TRANSLATION_STATUSES = frozenset({"fallback", "failed"})
UNOFFICIAL_COMPLIANCE_CLASSES = frozenset({"unofficial_nonstandard", "unofficial_subscale"})
PHASE_ALIASES = {"standard": "power"}
EXECUTION_RUN_TYPES = frozenset({"measurement", "warmup"})
PASS_STATUSES = frozenset({"SUCCESS", "PASS", "pass", "success"})
KNOWN_LOGICAL_QUERY_COUNTS = {
    "tpch": 22,
    "tpch_skew": 22,
    "tpchavoc": 22,
    "tpcds": 99,
    "ssb": 13,
    "star_schema": 13,
    "clickbench": 43,
}
CANONICAL_TUNING_MODES = frozenset({"tuned", "tuned-fallback", "notuning", "auto", "custom"})
APPLIED_TUNING_STATUSES = frozenset({"applied_unverified", "applied_verified"})
POWER_SCORE_BENCHMARKS = frozenset({"tpch", "tpcds"})
THROUGHPUT_SCORE_BENCHMARKS = frozenset({"tpch", "tpcds"})
THROUGHPUT_PHASE = "throughput"
UTC = _dt.timezone.utc
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
TIMESTAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$")

CohortKey = tuple[str, str]


class CorpusReadError(Exception):
    pass


class RecencyStats(NamedTuple):
    oldest: _dt.date
    newest: _dt.date
    oldest_age_days: int
    newest_age_days: int
    bundle_count: int


def discover_bundles(bundles_dir: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in bundles_dir.rglob("*.json")
        if path.name != LEGACY_MANIFEST_NAME and not path.name.endswith(COMPANION_SUFFIXES)
    )


def _load_bundle(bundle: pathlib.Path) -> dict:
    try:
        with open(bundle, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        raise CorpusReadError(f"ERROR reading {bundle}: {exc}") from exc


def _phase_executed(phase: object) -> bool:
    if not isinstance(phase, dict) or not phase:
        return False
    return str(phase.get("status") or "").upper() != "NOT_RUN"


def bundle_phase(payload: dict) -> str:
    benchmark = payload.get("benchmark")
    declared = benchmark.get("test_type") if isinstance(benchmark, dict) else None
    if declared:
        normalized = str(declared).strip().lower()
        return PHASE_ALIASES.get(normalized, normalized) or "unknown"
    phases = payload.get("phases")
    if not isinstance(phases, dict):
        return "unknown"
    if _phase_executed(phases.get("power_test")):
        return "power"
    if _phase_executed(phases.get("throughput_test")):
        return "throughput"
    return "unknown"


def _stream_count(payload: dict) -> int | None:
    phases = payload.get("phases")
    throughput = phases.get("throughput_test") if isinstance(phases, dict) else None
    streams = throughput.get("stream_results") if isinstance(throughput, dict) else None
    return len(streams) if isinstance(streams, list) and streams else None


def _int_or_none(value: object) -> int | None:
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


def _query_row_failed(query: object) -> bool:
    if not isinstance(query, dict):
        return False
    if str(query.get("run_type") or "measurement").strip().lower() != "measurement":
        return False
    status = query.get("status")
    return status is not None and str(status).upper() not in {"SUCCESS", "SKIPPED"}


def failed_query_count(payload: dict) -> int:
    summary = payload.get("summary")
    if isinstance(summary, dict):
        queries = summary.get("queries")
        if isinstance(queries, dict):
            failed = _int_or_none(queries.get("failed"))
            if failed is not None and failed > 0:
                return failed
            total = _int_or_none(queries.get("total"))
            passed = _int_or_none(queries.get("passed"))
            skipped = _int_or_none(queries.get("skipped")) or 0
            if total is not None and passed is not None and total > passed + skipped:
                return total - passed - skipped
    rows = payload.get("queries")
    if isinstance(rows, list):
        return sum(1 for row in rows if _query_row_failed(row))
    return 0


def _status_text(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("status")
    if value is None:
        return None
    return str(value).strip().lower() or None


def _query_validation_failed(payload: dict) -> bool:
    rows = payload.get("queries")
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(row, dict)
        and isinstance(evidence := row.get("row_count_validation"), dict)
        and str(evidence.get("status") or "").strip().upper() == "FAILED"
        for row in rows
    )


def _translation_status(payload: dict) -> str | None:
    execution = payload.get("execution")
    return _status_text(execution.get("translation")) if isinstance(execution, dict) else None


def bundle_validation_status(payload: dict) -> str | None:
    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, dict):
        return None
    recorded = summary.get("validation") if isinstance(summary, dict) else None
    failed = failed_query_count(payload)
    if not isinstance(recorded, (str, dict)):
        return "partial" if failed else None
    status = _status_text(recorded)
    if status in {None, "passed"}:
        if failed:
            return "partial"
        if not _query_validation_failed(payload) and _translation_status(payload) in NON_CLEAN_TRANSLATION_STATUSES:
            return "uncertain"
    return status


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _query_timings(payload: dict) -> list[tuple[str, float, bool, str | None]]:
    rows = payload.get("queries")
    timings = []
    for row in rows if isinstance(rows, list) else []:
        data = _mapping(row)
        run_type = data.get("run_type")
        run_type = None if run_type is None else str(run_type)
        if run_type is not None and run_type not in EXECUTION_RUN_TYPES:
            continue
        raw = data.get("ms")
        if raw is None:
            raw = data.get("execution_time_ms") or 0.0
        try:
            duration = float(raw)
        except (TypeError, ValueError):
            duration = 0.0
        status = "fail" if "status" in data and data["status"] is None else data.get("status", "pass")
        query_id = str(data.get("id") or data.get("query_id", ""))
        timings.append((query_id, duration, status in PASS_STATUSES, run_type))
    return timings


def _display_timings(payload: dict) -> list[tuple[str, float | None, int]]:
    grouped: dict[str, list[tuple[float, bool, str | None]]] = {}
    for query_id, duration, passed, run_type in _query_timings(payload):
        grouped.setdefault(query_id, []).append((duration, passed, run_type))
    result = []
    for query_id, rows in grouped.items():
        passing = [(duration, run_type) for duration, passed, run_type in rows if passed]
        measurement = [duration for duration, run_type in passing if run_type == "measurement"]
        legacy = [duration for duration, run_type in passing if run_type is None]
        candidates = sorted(measurement or legacy)
        if not candidates:
            result.append((query_id, None, 0))
            continue
        middle = len(candidates) // 2
        if len(candidates) % 2:
            median = candidates[middle]
        else:
            median = (candidates[middle - 1] + candidates[middle]) / 2.0
        result.append((query_id, median, len(candidates)))
    return result


def _summary_query_total(payload: dict) -> int:
    try:
        return int(_mapping(_mapping(payload.get("summary")).get("queries")).get("total") or 0)
    except (TypeError, ValueError):
        return 0


def _logical_query_count(payload: dict, display: list[tuple[str, float | None, int]]) -> int:
    best = None
    rows = payload.get("queries")
    for row in rows if isinstance(rows, list) else []:
        skip = _mapping(_mapping(row).get("dataframe_skip_summary"))
        executed = _int_or_none(skip.get("executed_total"))
        skipped = _int_or_none(skip.get("skipped_total"))
        if executed is None or skipped is None:
            continue
        total = executed + skipped
        if total > 0 and (best is None or total > best):
            best = total
    if best is not None:
        return best
    raw = _summary_query_total(payload)
    observed = len({query_id for query_id, _, _ in display if query_id})
    if observed <= 0:
        return raw
    if raw <= observed:
        return raw or observed
    known = KNOWN_LOGICAL_QUERY_COUNTS.get(str(_mapping(payload.get("benchmark")).get("id", "unknown")))
    if known and observed <= known and raw % known == 0:
        return known
    if raw % observed == 0 and any(samples > 1 for _, _, samples in display):
        return observed
    return raw


def _valid_timing(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value)) and float(value) > 0


def _timing_exclusion(display: list[tuple[str, float | None, int]], logical: int) -> str | None:
    valid = zero = missing = 0
    for _, value, _ in display:
        if _valid_timing(value):
            valid += 1
        elif value is not None and math.isfinite(float(value)) and float(value) == 0:
            zero += 1
        else:
            missing += 1
    missing += max(logical - len({query_id for query_id, _, _ in display}), 0)
    if valid <= 0:
        if logical <= 0:
            return "no_queries"
        if zero > 0 and missing == 0:
            return "zero_timings_only"
        if missing > 0 and zero == 0:
            return "missing_timings"
        return "no_valid_display_timing"
    if valid < 2:
        return "insufficient_valid_queries"
    if logical > 0 and valid * 2 < logical:
        return "insufficient_query_coverage"
    return None


def _tuning_mode(payload: dict) -> str | None:
    for section in ("config", "execution"):
        mode = _mapping(payload.get(section)).get("tuning_mode")
        if mode and str(mode) in CANONICAL_TUNING_MODES:
            return str(mode)
    return None


def _tuning_applied(payload: dict) -> bool:
    if _tuning_mode(payload) != "custom":
        return True
    status = _mapping(_mapping(payload.get("platform")).get("tuning")).get("validation_status")
    return bool(status) and str(status) in APPLIED_TUNING_STATUSES


def _tpc_metric(payload: dict, name: str) -> float | None:
    metrics = _mapping(_mapping(payload.get("summary")).get("tpc_metrics"))
    value = metrics.get(name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _power_score(payload: dict) -> float | None:
    return _tpc_metric(payload, "power_at_size")


def _throughput_phase_clean(payload: dict) -> bool:
    block = _mapping(_mapping(payload.get("phases")).get("throughput_test"))
    if str(block.get("status") or "").upper() != "COMPLETED":
        return False
    streams = block.get("stream_results")
    if not isinstance(streams, list) or not streams:
        return False
    if not all(isinstance(stream, dict) and stream.get("success") is True for stream in streams):
        return False
    return not block.get("errors") and not block.get("outstanding_work")


def _throughput_score(payload: dict) -> float | None:
    if not _throughput_phase_clean(payload):
        return None
    return _tpc_metric(payload, "throughput_at_size")


def _primary_metric_value(payload: dict, display: list[tuple[str, float | None, int]]) -> float | None:
    benchmark_id = str(_mapping(payload.get("benchmark")).get("id", "unknown"))
    if bundle_phase(payload) == THROUGHPUT_PHASE and benchmark_id in THROUGHPUT_SCORE_BENCHMARKS:
        return _throughput_score(payload)
    if benchmark_id in POWER_SCORE_BENCHMARKS:
        return _power_score(payload)
    values = [value for _, value, _ in display if value is not None and value > 0]
    return math.exp(sum(math.log(item) for item in values) / len(values)) if values else None


def _primary_metric_reason(payload: dict, display: list[tuple[str, float | None, int]]) -> str | None:
    value = _primary_metric_value(payload, display)
    if value is None:
        return "missing_primary_metric"
    if not math.isfinite(float(value)) or float(value) <= 0:
        return "non_positive_primary_metric"
    return None


def exclusion_reason(payload: dict) -> str | None:
    benchmark = _mapping(payload.get("benchmark"))
    compliance = benchmark.get("compliance_class")
    if compliance is not None and str(compliance) in UNOFFICIAL_COMPLIANCE_CLASSES:
        return "unofficial_compliance"
    if failed_query_count(payload):
        return "failed_queries"
    if bundle_validation_status(payload) in NON_CLEAN_VALIDATION_STATUSES:
        return "validation_not_clean"
    if not _tuning_applied(payload):
        return "tuning_not_applied"
    display = _display_timings(payload)
    timing = _timing_exclusion(display, _logical_query_count(payload, display))
    if timing is not None:
        return timing
    return _primary_metric_reason(payload, display)


def bundle_rankable(payload: dict) -> bool:
    return exclusion_reason(payload) is None


def cohort_phase_suffix(payload: dict) -> str:
    phase = bundle_phase(payload)
    if phase == "power":
        return ""
    streams = _stream_count(payload) if phase == "throughput" else None
    return f"#{phase}" if streams is None else f"#{phase}#{streams}streams"


def _cohort_key(payload: dict) -> CohortKey:
    try:
        benchmark_id = payload["benchmark"]["id"]
        scale_factor = str(payload["benchmark"].get("scale_factor", ""))
    except Exception as exc:
        raise CorpusReadError(f"ERROR missing cohort fields: {exc}") from exc
    return (benchmark_id, f"{scale_factor}{cohort_phase_suffix(payload)}")


def _comparison_identity(bundle: pathlib.Path, payload: dict) -> str:
    try:
        platform_section = payload["platform"]
        platform = platform_section["name"]
        version = None
        if bundle.parent.name == "duckdb-version-matrix":
            version = platform_section.get("version") or platform_section.get("client_version")
            execution = payload.get("execution", {})
            if isinstance(execution, dict):
                for key in (
                    "driver_version_resolved",
                    "driver_version_requested",
                    "driver_resolved_version",
                    "driver_requested_version",
                ):
                    candidate = execution.get(key)
                    if candidate and str(candidate) != "unknown":
                        version = candidate
                        break
    except Exception as exc:
        raise CorpusReadError(f"ERROR reading platform identity from {bundle}: {exc}") from exc
    identity = str(platform)
    if version and str(version) != "unknown":
        identity = f"{identity} v{str(version)[:120]}"
    return identity


def parse_run_date(payload: dict, *, bundle: pathlib.Path | None = None) -> _dt.date:
    label = f" in {bundle}" if bundle is not None else ""
    try:
        timestamp = payload["run"]["timestamp"]
    except Exception as exc:
        raise CorpusReadError(f"ERROR missing run.timestamp{label}: {exc}") from exc
    if not isinstance(timestamp, str):
        raise CorpusReadError(f"ERROR unparseable run.timestamp{label}: {timestamp!r}")

    if DATE_RE.fullmatch(timestamp):
        try:
            return _dt.date.fromisoformat(timestamp)
        except ValueError as exc:
            raise CorpusReadError(f"ERROR unparseable run.timestamp{label}: {timestamp!r}") from exc

    if not TIMESTAMP_RE.fullmatch(timestamp):
        raise CorpusReadError(f"ERROR unparseable run.timestamp{label}: {timestamp!r}")
    try:
        parsed = _dt.datetime.fromisoformat(f"{timestamp[:-1]}+00:00" if timestamp.endswith("Z") else timestamp)
    except ValueError as exc:
        raise CorpusReadError(f"ERROR unparseable run.timestamp{label}: {timestamp!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date()


def utc_today() -> _dt.date:
    return _dt.datetime.now(UTC).date()


def age_days(run_date: _dt.date, *, as_of: _dt.date | None = None) -> int:
    as_of = as_of or utc_today()
    return (as_of - run_date).days


def _stats_from_dates(dates: list[_dt.date], *, as_of: _dt.date) -> RecencyStats:
    oldest = min(dates)
    newest = max(dates)
    return RecencyStats(
        oldest=oldest,
        newest=newest,
        oldest_age_days=age_days(oldest, as_of=as_of),
        newest_age_days=age_days(newest, as_of=as_of),
        bundle_count=len(dates),
    )


def cohort_platforms(bundles: list[pathlib.Path]) -> dict[CohortKey, set[str]]:
    cohorts: collections.defaultdict[CohortKey, set[str]] = collections.defaultdict(set)
    for bundle in bundles:
        payload = _load_bundle(bundle)
        identities = cohorts[_cohort_key(payload)]
        if bundle_rankable(payload):
            identities.add(_comparison_identity(bundle, payload))
    return dict(cohorts)


def cohort_recency(
    bundles: list[pathlib.Path],
    *,
    as_of: _dt.date | None = None,
) -> tuple[RecencyStats | None, dict[CohortKey, RecencyStats], list[str]]:
    as_of = as_of or utc_today()
    by_cohort: collections.defaultdict[CohortKey, list[_dt.date]] = collections.defaultdict(list)
    all_dates: list[_dt.date] = []
    warnings: list[str] = []
    for bundle in bundles:
        payload = _load_bundle(bundle)
        try:
            run_date = parse_run_date(payload, bundle=bundle)
        except CorpusReadError as exc:
            warnings.append(str(exc).replace("ERROR", "WARN", 1))
            continue
        key = _cohort_key(payload)
        by_cohort[key].append(run_date)
        all_dates.append(run_date)
    per_cohort = {key: _stats_from_dates(dates, as_of=as_of) for key, dates in by_cohort.items()}
    overall = _stats_from_dates(all_dates, as_of=as_of) if all_dates else None
    return overall, per_cohort, warnings


def format_recency_report(
    overall: RecencyStats | None,
    per_cohort: dict[CohortKey, RecencyStats],
    *,
    as_of: _dt.date,
) -> str:
    lines = [
        "Recency (from run.timestamp; informational only — age does not fail "
        "the depth gate and does not affect ranking eligibility):",
        f"  as_of={as_of.isoformat()}",
    ]
    if overall is None:
        lines.append("  Overall: no parseable run timestamps")
        return "\n".join(lines)
    lines.append(
        "  Overall: "
        f"oldest={overall.oldest.isoformat()} ({overall.oldest_age_days} days), "
        f"newest={overall.newest.isoformat()} ({overall.newest_age_days} days), "
        f"{overall.bundle_count} bundles"
    )
    lines.append("  Cohorts:")
    for key, stats in sorted(per_cohort.items()):
        lines.append(
            f"    {key[0]} SF={key[1]}: "
            f"oldest={stats.oldest.isoformat()} ({stats.oldest_age_days} days), "
            f"newest={stats.newest.isoformat()} ({stats.newest_age_days} days), "
            f"{stats.bundle_count} bundles"
        )
    return "\n".join(lines)


def shallow_cohorts(cohorts: dict[CohortKey, set[str]]) -> dict[CohortKey, set[str]]:
    return {key: platforms for key, platforms in cohorts.items() if 0 < len(platforms) < MINIMUM_PLATFORMS_PER_COHORT}


def unranked_cohorts(cohorts: dict[CohortKey, set[str]]) -> list[CohortKey]:
    return sorted(key for key, platforms in cohorts.items() if not platforms)


def main(bundles_dir: pathlib.Path | None = None, *, as_of: _dt.date | None = None) -> int:
    bundles_dir = bundles_dir or pathlib.Path(__file__).parent / "bundles"
    as_of = as_of or utc_today()
    bundles = discover_bundles(bundles_dir)
    print(f"Found {len(bundles)} bundles")

    try:
        cohorts = cohort_platforms(bundles)
    except CorpusReadError as exc:
        print(exc)
        return 1

    print("\nCohorts:")
    for key, platforms in sorted(cohorts.items()):
        if not platforms:
            status = "UNRANKED (no rankable bundles; no ranking published)"
        elif len(platforms) >= MINIMUM_PLATFORMS_PER_COHORT:
            status = "OK"
        else:
            status = "WARN (<3 identities)"
        print(f"  {key[0]} SF={key[1]}: {len(platforms)} identities ({sorted(platforms)}) [{status}]")

    try:
        overall, per_cohort, recency_warnings = cohort_recency(bundles, as_of=as_of)
    except CorpusReadError as exc:
        print(f"WARN recency skipped: {exc}")
        overall, per_cohort, recency_warnings = None, {}, []
    for warning in recency_warnings:
        print(warning)

    print()
    print(format_recency_report(overall, per_cohort, as_of=as_of))

    low = shallow_cohorts(cohorts)
    if low:
        print(f"\nWARN: {len(low)} cohort(s) have <3 comparison identities: { {k: len(v) for k, v in low.items()} }")
        return 1

    unranked = unranked_cohorts(cohorts)
    if unranked:
        print(f"\nUNRANKED: {len(unranked)} cohort(s) have no rankable bundle and publish no ranking: {unranked}")
    ranked = len(cohorts) - len(unranked)
    print(f"\nAll {ranked} ranked cohort(s) meet the >={MINIMUM_PLATFORMS_PER_COHORT}-identity depth criterion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
