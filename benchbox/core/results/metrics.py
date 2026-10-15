from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

NAMED_METRICS = ("geometric_mean", "p50", "p95", "p99", "total_time", "mean")

NON_POWER_TEST_TYPES = frozenset({"throughput", "maintenance"})
UNOFFICIAL_COMPLIANCE_CLASSES = frozenset({"unofficial_subscale", "unofficial_nonstandard"})
TPC_QUERIES_PER_STREAM = {"tpch": 22, "tpcds": 99}
_TPC_BENCHMARK_LABELS = {"tpch": "TPC-H", "tpcds": "TPC-DS"}


def percentile_ms(times_ms: Sequence[float], p: float) -> float:
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"percentile must be a fraction in [0, 1], got {p!r} (use 0.95 for p95, not 95)")

    sorted_times = sorted(times_ms)
    n = len(sorted_times)
    if n == 0:
        return 0.0
    rank = min(max(1, math.ceil(n * p)), n)
    return sorted_times[rank - 1]


def geometric_mean_ms(times_ms: Sequence[float]) -> float:
    positive = [t for t in times_ms if t > 0]
    if not positive:
        return 0.0
    return statistics.geometric_mean(positive)


def sample_stdev_ms(times_ms: Sequence[float]) -> float:
    times_list = list(times_ms)
    if len(times_list) < 2:
        return 0.0
    return statistics.stdev(times_list)


def calculate_named_metric(times_ms: Sequence[float], metric: str) -> float:
    times_list = list(times_ms)
    if not times_list:
        return 0.0

    if metric == "geometric_mean":
        return geometric_mean_ms(times_list)
    if metric == "p50":
        return percentile_ms(times_list, 0.50)
    if metric == "p95":
        return percentile_ms(times_list, 0.95)
    if metric == "p99":
        return percentile_ms(times_list, 0.99)
    if metric == "total_time":
        return sum(times_list)
    return statistics.mean(times_list)


class TPCMetricsCalculator:
    @staticmethod
    def calculate_power_at_size(
        query_times_seconds: Sequence[float],
        scale_factor: float,
    ) -> float:
        if not query_times_seconds:
            return 0.0

        valid_times = [t for t in query_times_seconds if t > 0]
        if not valid_times:
            return 0.0

        geometric_mean = statistics.geometric_mean(valid_times)
        if geometric_mean <= 0:
            return 0.0

        return (scale_factor * 3600) / geometric_mean

    @staticmethod
    def calculate_throughput_at_size(
        total_queries: int,
        total_time_seconds: float,
        scale_factor: float,
        num_streams: int = 1,
    ) -> float:
        if total_time_seconds <= 0 or num_streams <= 0:
            return 0.0

        return (total_queries * scale_factor * 3600) / total_time_seconds

    @staticmethod
    def calculate_qphh_at_size(power_at_size: float, throughput_at_size: float) -> float:
        if power_at_size <= 0 or throughput_at_size <= 0:
            return 0.0
        return round(math.sqrt(power_at_size * throughput_at_size), 1)

    @staticmethod
    def calculate_geometric_mean(times: Sequence[float]) -> float:
        return geometric_mean_ms(times)

    @staticmethod
    def resolve_scale_factor(
        power_data: dict,
        throughput_data: dict,
        scale_factor: float | None = None,
    ) -> float:
        if scale_factor is not None:
            return scale_factor
        sf_power = power_data.get("environment", {}).get("scale_factor") or power_data.get("benchmark", {}).get(
            "scale_factor"
        )
        sf_throughput = throughput_data.get("environment", {}).get("scale_factor") or throughput_data.get(
            "benchmark", {}
        ).get("scale_factor")
        if sf_power and sf_throughput:
            if sf_power != sf_throughput:
                raise ValueError(f"Scale factor mismatch: power={sf_power}, throughput={sf_throughput}")
            return sf_power
        raise ValueError("Could not auto-detect scale factor. Please specify --scale-factor")

    @staticmethod
    def metrics_refusal_reason(data: dict, label: str) -> str | None:
        summary = data.get("summary", {})
        tpc_metrics = summary.get("tpc_metrics") or {}
        if tpc_metrics.get("suppressed"):
            return f"{label} results have suppressed TPC metrics ({tpc_metrics.get('reason', 'suppressed')})"
        compliance_class = (data.get("benchmark") or {}).get("compliance_class")
        if compliance_class in UNOFFICIAL_COMPLIANCE_CLASSES:
            return f"{label} results are {compliance_class} and carry no official TPC metrics"
        failed = (summary.get("queries") or {}).get("failed") or 0
        if failed:
            return f"{label} results contain {failed} failed queries"
        throughput_phase = (data.get("phases") or {}).get("throughput_test") or {}
        if throughput_phase.get("status") == "FAILED":
            return f"{label} results have a failed throughput phase"
        return None

    @staticmethod
    def _power_refresh_evidence(power_phase: dict | None) -> tuple[bool, int | None]:
        if not isinstance(power_phase, dict):
            return False, None
        first_set = power_phase.get("refresh_first_set")
        functions = power_phase.get("refresh_functions")
        if isinstance(first_set, bool) or not isinstance(first_set, int) or first_set < 1:
            return False, None
        if not isinstance(functions, list) or len(functions) != 2:
            return False, first_set
        for expected, function in zip(("RF1", "RF2"), functions, strict=True):
            update_set = function.get("update_set") if isinstance(function, dict) else None
            duration_ms = function.get("duration_ms") if isinstance(function, dict) else None
            if (
                not isinstance(function, dict)
                or function.get("function") != expected
                or isinstance(update_set, bool)
                or update_set != first_set
                or function.get("status") != "SUCCESS"
                or isinstance(duration_ms, bool)
                or not isinstance(duration_ms, (int, float))
                or not math.isfinite(duration_ms)
                or duration_ms <= 0
            ):
                return False, first_set
        return True, first_set

    @staticmethod
    def _throughput_refresh_evidence(
        throughput_phase: dict | None,
        expected_streams: int | None = None,
    ) -> tuple[bool, int | None, int]:
        if not isinstance(throughput_phase, dict):
            return False, None, 0
        refresh_stream = throughput_phase.get("refresh_stream")
        if not isinstance(refresh_stream, dict):
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
            functions = pair.get("functions") if isinstance(pair, dict) else None
            pair_set = pair.get("update_set") if isinstance(pair, dict) else None
            if (
                not isinstance(pair, dict)
                or isinstance(pair_set, bool)
                or pair_set != set_id
                or pair.get("status") != "SUCCESS"
                or not isinstance(functions, list)
                or len(functions) != 2
            ):
                valid = False
                continue
            for expected, function in zip(("RF1", "RF2"), functions, strict=True):
                update_set = function.get("update_set") if isinstance(function, dict) else None
                duration_ms = function.get("duration_ms") if isinstance(function, dict) else None
                if (
                    not isinstance(function, dict)
                    or function.get("function") != expected
                    or isinstance(update_set, bool)
                    or update_set != set_id
                    or function.get("status") != "SUCCESS"
                    or isinstance(duration_ms, bool)
                    or not isinstance(duration_ms, (int, float))
                    or not math.isfinite(duration_ms)
                    or duration_ms <= 0
                ):
                    valid = False
        return valid, first_set, stream_count

    @staticmethod
    def refresh_included(power_phase: dict | None, throughput_phase: dict | None) -> bool:
        power_present = isinstance(power_phase, dict)
        throughput_present = isinstance(throughput_phase, dict)
        if power_present and throughput_present:
            power_valid, _power_set = TPCMetricsCalculator._power_refresh_evidence(power_phase)
            throughput_valid, _first_set, _streams = TPCMetricsCalculator._throughput_refresh_evidence(throughput_phase)
            return power_valid and throughput_valid
        if power_present:
            return TPCMetricsCalculator._power_refresh_evidence(power_phase)[0]
        if throughput_present:
            return TPCMetricsCalculator._throughput_refresh_evidence(throughput_phase)[0]
        return False

    @staticmethod
    def qphh_unavailable_reasons(power_data: dict, throughput_data: dict) -> list[str]:
        reasons = []
        for label, data in (("Power", power_data), ("Throughput", throughput_data)):
            benchmark = data.get("benchmark") or {}
            compliance_class = getattr(benchmark.get("compliance_class"), "value", benchmark.get("compliance_class"))
            if compliance_class != "official":
                reasons.append(f"{label} results are not official compliance ({compliance_class or 'unspecified'})")
            if benchmark.get("id") != "tpch":
                reasons.append(f"{label} results are not TPC-H")
            failed = (data.get("summary") or {}).get("queries", {}).get("failed", 0)
            if failed:
                reasons.append(f"{label} results contain {failed} failed queries")
            if (data.get("run") or {}).get("query_subset"):
                reasons.append(f"{label} results use a query subset")

        power_phase = (power_data.get("phases") or {}).get("power_test")
        if not isinstance(power_phase, dict) or power_phase.get("status") != "COMPLETED":
            reasons.append("Power phase did not complete")
        elif power_phase.get("query_count") != 22:
            reasons.append("Power phase must complete all 22 TPC-H queries")

        power_refresh_valid, power_first_set = TPCMetricsCalculator._power_refresh_evidence(power_phase)
        if not power_refresh_valid:
            reasons.append("Power phase must contain successful RF1 and RF2 evidence for one update set")

        throughput_phase = (throughput_data.get("phases") or {}).get("throughput_test")
        if not isinstance(throughput_phase, dict) or throughput_phase.get("status") != "COMPLETED":
            reasons.append("Throughput phase did not complete")
        stream_count = throughput_phase.get("num_streams") if isinstance(throughput_phase, dict) else None
        if isinstance(stream_count, bool) or not isinstance(stream_count, int):
            stream_count = (
                len(throughput_phase.get("stream_results") or []) if isinstance(throughput_phase, dict) else 0
            )
        if stream_count < 2:
            reasons.append("QphH requires at least two throughput streams")

        throughput_refresh_valid, throughput_first_set, actual_stream_count = (
            TPCMetricsCalculator._throughput_refresh_evidence(throughput_phase, stream_count)
        )
        if not throughput_refresh_valid:
            reasons.append("Throughput phase must complete one successful RF1/RF2 pair per query stream")
        if power_first_set is not None and throughput_first_set != power_first_set + 1:
            reasons.append("Throughput refresh sets must start at the set immediately after the Power set")
        if actual_stream_count != stream_count:
            reasons.append("Throughput refresh pair count must match the requested stream count")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def derive_tpc_metrics(
        power_data: dict,
        throughput_data: dict,
        scale_factor: float,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        for label, data in (("Power", power_data), ("Throughput", throughput_data)):
            reason = TPCMetricsCalculator.metrics_refusal_reason(data, label)
            if reason:
                raise ValueError(reason)

        power_metrics = power_data.get("summary", {}).get("tpc_metrics", {})
        throughput_metrics = throughput_data.get("summary", {}).get("tpc_metrics", {})
        power_at_size = power_metrics.get("power_at_size")
        throughput_at_size = throughput_metrics.get("throughput_at_size")

        power_times = TPCMetricsCalculator._final_power_iteration_times(power_data)
        power_phase = (power_data.get("phases") or {}).get("power_test")
        power_refresh_valid, _power_first_set = TPCMetricsCalculator._power_refresh_evidence(power_phase)
        refresh_times = (
            [function["duration_ms"] / 1000.0 for function in power_phase["refresh_functions"]]
            if power_refresh_valid
            else []
        )
        power_time = (
            sum([refresh_times[0], *power_times, refresh_times[1]])
            if power_times and refresh_times
            else (sum(power_times) if power_times else None)
        )
        if power_at_size is None and power_times:
            power_times_for_metric = (
                [refresh_times[0], *power_times, refresh_times[1]] if refresh_times else power_times
            )
            power_at_size = TPCMetricsCalculator.calculate_power_at_size(power_times_for_metric, scale_factor)

        phase = (throughput_data.get("phases") or {}).get("throughput_test") or {}
        duration_ms = phase.get("duration_ms")
        throughput_time = duration_ms / 1000.0 if duration_ms else None
        if throughput_at_size is None and throughput_time and phase.get("status") == "COMPLETED":
            num_streams = phase.get("num_streams") or len(phase.get("stream_results") or [])
            num_streams = num_streams or throughput_data.get("run", {}).get("streams") or 1
            per_stream = TPC_QUERIES_PER_STREAM.get(throughput_data.get("benchmark", {}).get("id"))
            if per_stream:
                throughput_at_size = TPCMetricsCalculator.calculate_throughput_at_size(
                    per_stream * num_streams,
                    throughput_time,
                    scale_factor,
                    num_streams,
                )
        return power_at_size, throughput_at_size, power_time, throughput_time

    @staticmethod
    def _final_power_iteration_times(power_data: dict) -> list[float]:
        rows = [
            q
            for q in power_data.get("queries", [])
            if q.get("run_type") == "measurement"
            and q.get("status") == "SUCCESS"
            and q.get("test_type") not in NON_POWER_TEST_TYPES
            and q.get("ms")
        ]
        if not rows:
            return []
        final_iteration = max(q.get("iter") or 0 for q in rows)
        return [q["ms"] / 1000.0 for q in rows if (q.get("iter") or 0) == final_iteration]

    @staticmethod
    def compute_qphh_result(
        power_data: dict,
        throughput_data: dict,
        scale_factor: float | None = None,
    ) -> dict:
        sf = TPCMetricsCalculator.resolve_scale_factor(power_data, throughput_data, scale_factor)
        power_benchmark = power_data.get("benchmark") or {}
        throughput_benchmark = throughput_data.get("benchmark") or {}
        benchmark_id = throughput_benchmark.get("id") or power_benchmark.get("id")
        refresh_included = False
        qphh_at_size = None
        if benchmark_id == "tpch" or power_benchmark.get("id") == "tpch":
            reasons = TPCMetricsCalculator.qphh_unavailable_reasons(power_data, throughput_data)
            if reasons:
                raise ValueError("QphH@Size unavailable: " + "; ".join(reasons))
            power_at_size, throughput_at_size, power_time, throughput_time = TPCMetricsCalculator.derive_tpc_metrics(
                power_data, throughput_data, sf
            )
            if power_at_size is None or throughput_at_size is None:
                raise ValueError("QphH@Size unavailable: could not derive Power@Size or Throughput@Size")
            refresh_included = True
            qphh_at_size = TPCMetricsCalculator.calculate_qphh_at_size(power_at_size, throughput_at_size)
        else:
            power_at_size, throughput_at_size, power_time, throughput_time = TPCMetricsCalculator.derive_tpc_metrics(
                power_data, throughput_data, sf
            )
            if power_at_size is None or throughput_at_size is None:
                raise ValueError("Could not derive Power@Size or Throughput@Size from result data")

        throughput_phase = (throughput_data.get("phases") or {}).get("throughput_test") or {}
        num_streams = (
            throughput_phase.get("num_streams")
            or len(throughput_phase.get("stream_results") or [])
            or throughput_data.get("run", {}).get("streams")
            or throughput_data.get("environment", {}).get("num_streams", 1)
        )
        benchmark = throughput_benchmark
        return {
            "benchmark": benchmark.get("name") or _TPC_BENCHMARK_LABELS.get(str(benchmark.get("id") or ""), "TPC"),
            "scale_factor": sf,
            "num_streams": num_streams,
            "power_test_time": power_time,
            "throughput_test_time": throughput_time,
            "power_at_size": power_at_size,
            "throughput_at_size": throughput_at_size,
            "qphh_at_size": qphh_at_size,
            "refresh_included": refresh_included,
        }


class TimingStatsCalculator:
    @staticmethod
    def calculate(times_ms: Sequence[float]) -> dict[str, float]:
        if not times_ms:
            return {}

        times_list = list(times_ms)

        return {
            "total_ms": sum(times_list),
            "avg_ms": statistics.mean(times_list),
            "min_ms": min(times_list),
            "max_ms": max(times_list),
            "geometric_mean_ms": geometric_mean_ms(times_list),
            "stdev_ms": sample_stdev_ms(times_list),
            "p50_ms": percentile_ms(times_list, 0.50),
            "p90_ms": percentile_ms(times_list, 0.90),
            "p95_ms": percentile_ms(times_list, 0.95),
            "p99_ms": percentile_ms(times_list, 0.99),
        }

    @staticmethod
    def calculate_seconds(times_seconds: Sequence[float]) -> dict[str, float]:
        if not times_seconds:
            return {}

        times_ms = [t * 1000 for t in times_seconds]
        stats_ms = TimingStatsCalculator.calculate(times_ms)

        return {key.replace("_ms", "_s"): value / 1000 for key, value in stats_ms.items()}
