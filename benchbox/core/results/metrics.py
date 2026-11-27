from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING

from benchbox.core.results import corpus_rule, corpus_score

if TYPE_CHECKING:
    from collections.abc import Sequence

NAMED_METRICS = ("geometric_mean", "p50", "p95", "p99", "total_time", "mean")

NON_POWER_TEST_TYPES = frozenset({"throughput", "maintenance"})
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

        return corpus_score.throughput_from_queries(total_queries, total_time_seconds, scale_factor)

    @staticmethod
    def calculate_qphh_at_size(power_at_size: float, throughput_at_size: float) -> float:
        return corpus_score.qphh_at_size(power_at_size, throughput_at_size)

    @staticmethod
    def qphds_input_problems(
        scale_factor: float,
        num_streams: int,
        power_seconds: float | None,
        throughput_seconds: Sequence[float | None],
        maintenance_seconds: Sequence[float | None],
        load_seconds: float | None,
    ) -> list[str]:
        return corpus_score.qphds_input_problems(
            scale_factor, num_streams, power_seconds, throughput_seconds, maintenance_seconds, load_seconds
        )

    @staticmethod
    def calculate_qphds_terms(
        scale_factor: float,
        num_streams: int,
        power_seconds: float,
        throughput_seconds: Sequence[float],
        maintenance_seconds: Sequence[float],
        load_seconds: float,
    ) -> dict[str, float]:
        problems = TPCMetricsCalculator.qphds_input_problems(
            scale_factor, num_streams, power_seconds, throughput_seconds, maintenance_seconds, load_seconds
        )
        if problems:
            raise ValueError("; ".join(problems))
        return corpus_score.qphds_terms(
            num_streams, power_seconds, throughput_seconds, maintenance_seconds, load_seconds
        )

    @staticmethod
    def calculate_qphds_at_size(
        scale_factor: float,
        num_streams: int,
        power_seconds: float,
        throughput_seconds: Sequence[float],
        maintenance_seconds: Sequence[float],
        load_seconds: float,
    ) -> float:
        problems = TPCMetricsCalculator.qphds_input_problems(
            scale_factor, num_streams, power_seconds, throughput_seconds, maintenance_seconds, load_seconds
        )
        if problems:
            raise ValueError("; ".join(problems))
        return corpus_score.qphds_at_size(
            scale_factor, num_streams, power_seconds, throughput_seconds, maintenance_seconds, load_seconds
        )

    @staticmethod
    def floor_qphds(scale_factor: float, q: float, hours_product: float) -> float:
        return corpus_score.floor_qphds(scale_factor, q, hours_product)

    @staticmethod
    def calculate_geometric_mean(times: Sequence[float]) -> float:
        return geometric_mean_ms(times)

    @staticmethod
    def resolve_scale_factor(
        power_data: dict,
        throughput_data: dict,
        scale_factor: float | None = None,
    ) -> float:
        recorded = {
            label: TPCMetricsCalculator._recorded_scale_factor(data)
            for label, data in (("power", power_data), ("throughput", throughput_data))
        }
        if scale_factor is not None:
            for label, value in recorded.items():
                if value and value != scale_factor:
                    raise ValueError(
                        f"Scale factor {scale_factor} differs from the {label} results' recorded scale factor "
                        f"{value}; omit --scale-factor to use the recorded value"
                    )
            return scale_factor
        sf_power, sf_throughput = recorded["power"], recorded["throughput"]
        if sf_power and sf_throughput:
            if sf_power != sf_throughput:
                raise ValueError(f"Scale factor mismatch: power={sf_power}, throughput={sf_throughput}")
            return sf_power
        raise ValueError("Could not auto-detect scale factor. Please specify --scale-factor")

    @staticmethod
    def _recorded_scale_factor(data: dict) -> float | None:
        return (data.get("benchmark") or {}).get("scale_factor") or (data.get("environment") or {}).get("scale_factor")

    @staticmethod
    def _with_scale(data: dict, scale_factor: float | None) -> dict:
        benchmark = data.get("benchmark") or {}
        resolved = TPCMetricsCalculator._recorded_scale_factor(data) or scale_factor
        if benchmark.get("scale_factor") == resolved:
            return data
        return {**data, "benchmark": {**benchmark, "scale_factor": resolved}}

    @staticmethod
    def _composite_scale_refusal(data: dict) -> str | None:
        facts = corpus_rule.facts_from_result(data)
        return corpus_rule.composite_scale_refusal(
            facts.benchmark, facts.scale_factor, gateway_routed=facts.gateway_routed
        )

    @staticmethod
    def metrics_refusal_reason(data: dict, label: str) -> str | None:
        summary = data.get("summary", {})
        refusal = TPCMetricsCalculator._composite_scale_refusal(data)
        if refusal:
            return f"{label} results are {refusal} and carry no official TPC metrics"
        failed = (summary.get("queries") or {}).get("failed") or 0
        if failed:
            return f"{label} results contain {failed} failed queries"
        throughput_phase = (data.get("phases") or {}).get("throughput_test") or {}
        if throughput_phase.get("status") == "FAILED":
            return f"{label} results have a failed throughput phase"
        return None

    @staticmethod
    def _power_refresh_evidence(power_phase: dict | None) -> tuple[bool, int | None]:
        return corpus_score.power_refresh_evidence(power_phase)

    @staticmethod
    def _throughput_refresh_evidence(
        throughput_phase: dict | None,
        expected_streams: int | None = None,
    ) -> tuple[bool, int | None, int]:
        return corpus_score.throughput_refresh_evidence(throughput_phase, expected_streams)

    @staticmethod
    def refresh_included(power_phase: dict | None, throughput_phase: dict | None) -> bool:
        return corpus_score.refresh_included(power_phase, throughput_phase)

    @staticmethod
    def qphh_unavailable_reasons(
        power_data: dict, throughput_data: dict, scale_factor: float | None = None
    ) -> list[str]:
        reasons = []
        for label, raw in (("Power", power_data), ("Throughput", throughput_data)):
            data = TPCMetricsCalculator._with_scale(raw, scale_factor)
            benchmark = data.get("benchmark") or {}
            refusal = TPCMetricsCalculator._composite_scale_refusal(data)
            if refusal:
                reasons.append(f"{label} results are not official compliance ({refusal})")
            if benchmark.get("id") != "tpch":
                reasons.append(f"{label} results are not TPC-H")
            failed = (data.get("summary") or {}).get("queries", {}).get("failed", 0)
            if failed:
                reasons.append(f"{label} results contain {failed} failed queries")
            if (data.get("run") or {}).get("query_subset"):
                reasons.append(f"{label} results use a query subset")

        reasons.extend(
            corpus_score.qphh_structure_problems(
                (power_data.get("phases") or {}).get("power_test"),
                (throughput_data.get("phases") or {}).get("throughput_test"),
                scale_factor=scale_factor
                or (throughput_data.get("benchmark") or {}).get("scale_factor")
                or (throughput_data.get("environment") or {}).get("scale_factor"),
            )
        )
        return list(dict.fromkeys(reasons))

    @staticmethod
    def derive_tpc_metrics(
        power_data: dict,
        throughput_data: dict,
        scale_factor: float,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        scale_factor = TPCMetricsCalculator.resolve_scale_factor(power_data, throughput_data, scale_factor)
        power_data = TPCMetricsCalculator._with_scale(power_data, scale_factor)
        throughput_data = TPCMetricsCalculator._with_scale(throughput_data, scale_factor)
        for label, data in (("Power", power_data), ("Throughput", throughput_data)):
            reason = TPCMetricsCalculator.metrics_refusal_reason(data, label)
            if reason:
                raise ValueError(reason)

        power_input = {
            **power_data,
            "phases": {
                **(power_data.get("phases") or {}),
                "throughput_test": (throughput_data.get("phases") or {}).get("throughput_test"),
            },
        }
        throughput_input = throughput_data
        power_at_size = corpus_score.power_at_size(power_input)
        throughput_at_size = corpus_score.throughput_at_size(throughput_input)

        power_times = TPCMetricsCalculator._final_power_iteration_times(power_data)
        power_phase = (power_data.get("phases") or {}).get("power_test")
        power_refresh_valid, _power_first_set = TPCMetricsCalculator._power_refresh_evidence(power_phase)
        refresh_times = (
            [max(0.001, function["duration_ms"] / 1000.0) for function in power_phase["refresh_functions"]]
            if power_refresh_valid
            else []
        )
        power_time = (
            sum([refresh_times[0], *power_times, refresh_times[1]])
            if power_times and refresh_times
            else (sum(power_times) if power_times else None)
        )

        phase = (throughput_data.get("phases") or {}).get("throughput_test") or {}
        duration_ms = phase.get("duration_ms")
        throughput_time = duration_ms / 1000.0 if duration_ms else None
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
        power_at_size, throughput_at_size, power_time, throughput_time = TPCMetricsCalculator.derive_tpc_metrics(
            power_data, throughput_data, sf
        )
        if power_at_size is None or throughput_at_size is None:
            raise ValueError("Could not derive Power@Size or Throughput@Size from result data")
        tpch_extras: dict = {}
        if benchmark_id == "tpch" or power_benchmark.get("id") == "tpch":
            reasons = TPCMetricsCalculator.qphh_unavailable_reasons(power_data, throughput_data, sf)
            tpch_extras["refresh_included"] = TPCMetricsCalculator.refresh_included(
                (power_data.get("phases") or {}).get("power_test"),
                (throughput_data.get("phases") or {}).get("throughput_test"),
            )
            if reasons:
                tpch_extras["qphh_unavailable"] = reasons
            else:
                tpch_extras["qphh_at_size"] = TPCMetricsCalculator.calculate_qphh_at_size(
                    power_at_size, throughput_at_size
                )

        throughput_phase = (throughput_data.get("phases") or {}).get("throughput_test") or {}
        streams = throughput_phase.get("stream_results") or []
        num_streams = sum(1 for stream in streams if isinstance(stream, dict) and stream.get("success") is True)
        benchmark = throughput_benchmark
        return {
            "benchmark": benchmark.get("name") or _TPC_BENCHMARK_LABELS.get(str(benchmark.get("id") or ""), "TPC"),
            "scale_factor": sf,
            "num_streams": num_streams,
            "power_test_time": power_time,
            "throughput_test_time": throughput_time,
            "power_at_size": power_at_size,
            "throughput_at_size": throughput_at_size,
            **tpch_extras,
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
