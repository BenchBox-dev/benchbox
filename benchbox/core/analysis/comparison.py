# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from benchbox.core.analysis.models import (
    ComparisonOutcome,
    ComparisonReport,
    CostPerformanceAnalysis,
    HeadToHeadComparison,
    PlatformRanking,
    QueryComparison,
    ValidationResult,
    WinLossRecord,
)
from benchbox.core.analysis.statistics import (
    apply_bonferroni_correction,
    calculate_geometric_mean,
    calculate_performance_metrics,
    create_outlier_info,
    detect_outliers_iqr,
    welchs_t_test,
)
from benchbox.core.cost.models import published_total_cost
from benchbox.core.results.comparison_modes import results_mode_differences
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.query_execution import (
    DURATION_CONSISTENCY_TOLERANCE_MS,
    query_execution_from_legacy_dict,
)

logger = logging.getLogger(__name__)

_DURATION_CONSISTENCY_TOLERANCE_MS = DURATION_CONSISTENCY_TOLERANCE_MS


@dataclass
class ComparisonConfig:
    significance_level: float = 0.05
    min_sample_size: int = 3
    outlier_method: str = "iqr"
    outlier_threshold: float = 1.5
    exclude_outliers: bool = False
    require_all_queries: bool = False
    performance_ratio_threshold: float = 1.05
    apply_bonferroni: bool = True


class PlatformComparison:
    def __init__(
        self,
        results: list[BenchmarkResults],
        config: Optional[ComparisonConfig] = None,
    ) -> None:
        if not results:
            raise ValueError("At least one benchmark result is required")

        self.results = results
        self.config = config or ComparisonConfig()
        self._validation_result: Optional[ValidationResult] = None
        self._comparison_report: Optional[ComparisonReport] = None

    @classmethod
    def from_files(
        cls,
        file_paths: list[Union[str, Path]],
        config: Optional[ComparisonConfig] = None,
    ) -> "PlatformComparison":
        results = []
        for path in file_paths:
            path = Path(path)
            if not path.exists():
                raise FileNotFoundError(f"Result file not found: {path}")

            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                result = _dict_to_benchmark_results(data)
                results.append(result)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in {path}: {e}") from e
            except Exception as e:
                raise ValueError(f"Failed to parse {path}: {e}") from e

        return cls(results, config)

    @classmethod
    def from_directory(
        cls,
        directory: Union[str, Path],
        pattern: str = "*.json",
        config: Optional[ComparisonConfig] = None,
    ) -> "PlatformComparison":
        directory = Path(directory)
        if not directory.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        file_paths = list(directory.glob(pattern))
        if not file_paths:
            raise ValueError(f"No files matching '{pattern}' found in {directory}")

        return cls.from_files(file_paths, config)

    @property
    def platforms(self) -> list[str]:
        return [r.platform for r in self.results]

    @property
    def benchmark_name(self) -> str:
        return self.results[0].benchmark_name if self.results else "unknown"

    @property
    def scale_factor(self) -> float:
        return self.results[0].scale_factor if self.results else 0.0

    def validate(self) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        if len(self.results) < 2:
            errors.append("At least two results are required for comparison")

        benchmark_names = {r.benchmark_name for r in self.results}
        if len(benchmark_names) > 1:
            errors.append(f"Results are from different benchmarks: {benchmark_names}")

        for difference in results_mode_differences(self.results):
            errors.append(f"Results use different measurement modes: {difference}")

        scale_factors = {r.scale_factor for r in self.results}
        if len(scale_factors) > 1:
            warnings.append(f"Results have different scale factors: {scale_factors}")

        common_queries = self._validate_query_overlap(errors, warnings)
        outliers_detected = self._detect_outliers(warnings)

        self._validation_result = ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            platforms_validated=self.platforms,
            common_queries=sorted(common_queries),
            outliers_detected=outliers_detected,
        )

        return self._validation_result

    def _validate_query_overlap(self, errors: list[str], warnings: list[str]) -> set[str]:
        query_sets = [set(_extract_query_ids(result)) for result in self.results]

        if not query_sets:
            return set()

        common_queries = set.intersection(*query_sets)
        all_queries = set.union(*query_sets)

        if not common_queries:
            errors.append("No common queries found across all platforms")
        elif len(common_queries) < len(all_queries):
            missing_pct = (1 - len(common_queries) / len(all_queries)) * 100
            warnings.append(
                f"Only {len(common_queries)}/{len(all_queries)} queries "
                f"are common across all platforms ({missing_pct:.1f}% missing)"
            )
            if self.config.require_all_queries:
                errors.append("Not all queries present in all results")

        return common_queries

    def _detect_outliers(self, warnings: list[str]) -> list[Any]:
        if self.config.outlier_method == "none":
            return []

        outliers_detected: list[Any] = []
        for result in self.results:
            times = _extract_query_times(result)
            if not times:
                continue
            outlier_indices = detect_outliers_iqr(
                list(times.values()),
                self.config.outlier_threshold,
            )
            for _idx, value, deviation in outlier_indices:
                outliers_detected.append(
                    create_outlier_info(
                        platform=result.platform,
                        query_id="overall",
                        value=value,
                        method=self.config.outlier_method,
                        threshold=self.config.outlier_threshold,
                        deviation=deviation,
                    )
                )

        if outliers_detected:
            warnings.append(f"Detected {len(outliers_detected)} outliers in results")

        return outliers_detected

    def compare(self) -> ComparisonReport:
        if self._validation_result is None:
            self.validate()

        if self._validation_result and not self._validation_result.is_valid:
            raise ValueError(f"Cannot compare invalid results: {self._validation_result.errors}")

        common_queries = (
            self._validation_result.common_queries if self._validation_result else _get_common_queries(self.results)
        )

        query_comparisons: dict[str, QueryComparison] = {}
        all_p_values: list[float] = []

        for query_id in common_queries:
            comparison = self._compare_query(query_id)
            query_comparisons[query_id] = comparison
            if comparison.statistical_test:
                all_p_values.append(comparison.statistical_test.p_value)

        if self.config.apply_bonferroni and all_p_values:
            corrected_p_values = apply_bonferroni_correction(all_p_values)
            for i, query_id in enumerate(common_queries):
                if query_comparisons[query_id].statistical_test:
                    test = query_comparisons[query_id].statistical_test
                    test.p_value = corrected_p_values[i]
                    test.notes = (test.notes or "") + " (Bonferroni corrected)"

        win_loss_matrix = self._calculate_win_loss_matrix(query_comparisons)

        rankings = self._calculate_rankings(query_comparisons, win_loss_matrix)

        head_to_head = self._generate_head_to_head(query_comparisons)

        cost_analysis = self._analyze_cost_performance()

        winner = rankings[0].platform if rankings else None

        insights = self._generate_insights(rankings, query_comparisons, head_to_head, cost_analysis)

        self._comparison_report = ComparisonReport(
            benchmark_name=self.benchmark_name,
            scale_factor=self.scale_factor,
            platforms=self.platforms,
            generated_at=datetime.now(),
            winner=winner,
            rankings=rankings,
            query_comparisons=query_comparisons,
            head_to_head=head_to_head,
            win_loss_matrix=win_loss_matrix,
            cost_analysis=cost_analysis,
            statistical_summary=self._create_statistical_summary(query_comparisons),
            insights=insights,
            warnings=self._validation_result.warnings if self._validation_result else [],
            metadata={
                "num_platforms": len(self.platforms),
                "num_queries_compared": len(common_queries),
                "config": {
                    "significance_level": self.config.significance_level,
                    "outlier_method": self.config.outlier_method,
                    "apply_bonferroni": self.config.apply_bonferroni,
                },
            },
        )

        return self._comparison_report

    def compare_overall_performance(self) -> ComparisonReport:
        return self.compare()

    def compare_by_query(self) -> dict[str, QueryComparison]:
        if self._comparison_report is None:
            self.compare()
        return self._comparison_report.query_comparisons if self._comparison_report else {}

    def compare_cost_performance(self) -> Optional[CostPerformanceAnalysis]:
        if self._comparison_report is None:
            self.compare()
        return self._comparison_report.cost_analysis if self._comparison_report else None

    def get_head_to_head(
        self,
        platform_a: str,
        platform_b: str,
    ) -> Optional[HeadToHeadComparison]:
        if self._comparison_report is None:
            self.compare()

        if not self._comparison_report:
            return None

        for h2h in self._comparison_report.head_to_head:
            if (h2h.platform_a == platform_a and h2h.platform_b == platform_b) or (
                h2h.platform_a == platform_b and h2h.platform_b == platform_a
            ):
                return h2h

        return None

    def _compare_query(self, query_id: str) -> QueryComparison:
        metrics: dict[str, Any] = {}
        times_by_platform: dict[str, list[float]] = {}

        for result in self.results:
            times = _get_query_times_for_query(result, query_id)
            if times:
                times_by_platform[result.platform] = times
                metrics[result.platform] = calculate_performance_metrics(times)

        winner = min(metrics.keys(), key=lambda p: metrics[p].mean) if metrics else ""

        winner_mean = metrics[winner].mean if winner and winner in metrics else 1.0
        performance_ratios = {p: m.mean / winner_mean if winner_mean > 0 else 1.0 for p, m in metrics.items()}

        statistical_test = None
        outcome = ComparisonOutcome.INCONCLUSIVE

        if len(times_by_platform) == 2:
            platforms = list(times_by_platform.keys())
            statistical_test = welchs_t_test(
                times_by_platform[platforms[0]],
                times_by_platform[platforms[1]],
            )

            if statistical_test.p_value < self.config.significance_level:
                ratio = performance_ratios[platforms[1]]
                if ratio > self.config.performance_ratio_threshold:
                    outcome = ComparisonOutcome.WIN
                elif ratio < 1 / self.config.performance_ratio_threshold:
                    outcome = ComparisonOutcome.LOSS
                else:
                    outcome = ComparisonOutcome.TIE
            else:
                outcome = ComparisonOutcome.TIE

        insights = []
        if winner and len(metrics) > 1:
            slowest = max(metrics.keys(), key=lambda p: metrics[p].mean)
            ratio = performance_ratios[slowest]
            if ratio > 1.1:
                insights.append(f"{winner} is {ratio:.1f}x faster than {slowest} on query {query_id}")

        return QueryComparison(
            query_id=query_id,
            platforms=list(metrics.keys()),
            metrics=metrics,
            winner=winner,
            performance_ratios=performance_ratios,
            statistical_test=statistical_test,
            outcome=outcome,
            insights=insights,
        )

    def _calculate_win_loss_matrix(
        self,
        query_comparisons: dict[str, QueryComparison],
    ) -> dict[str, WinLossRecord]:
        records: dict[str, WinLossRecord] = {p: WinLossRecord(platform=p) for p in self.platforms}

        for comparison in query_comparisons.values():
            for platform in comparison.platforms:
                records[platform].total += 1

                if platform == comparison.winner:
                    ratio = (
                        max(r for p, r in comparison.performance_ratios.items() if p != platform)
                        if len(comparison.performance_ratios) > 1
                        else 1.0
                    )

                    if ratio > self.config.performance_ratio_threshold:
                        records[platform].wins += 1
                    else:
                        records[platform].ties += 1
                else:
                    winner_ratio = comparison.performance_ratios.get(platform, 1.0)
                    if winner_ratio > self.config.performance_ratio_threshold:
                        records[platform].losses += 1
                    else:
                        records[platform].ties += 1

        for record in records.values():
            if record.total > 0:
                record.win_rate = record.wins / record.total * 100

        return records

    def _calculate_rankings(
        self,
        query_comparisons: dict[str, QueryComparison],
        win_loss_matrix: dict[str, WinLossRecord],
    ) -> list[PlatformRanking]:
        rankings = []

        for result in self.results:
            platform = result.platform
            times = _extract_query_times(result)
            query_times = list(times.values()) if times else []

            geo_mean = calculate_geometric_mean(query_times) if query_times else 0.0
            total_time = sum(query_times) if query_times else 0.0
            win_rate = win_loss_matrix[platform].win_rate if platform in win_loss_matrix else 0.0

            score = geo_mean * (1 - win_rate / 100 * 0.1) if geo_mean > 0 else float("inf")

            rankings.append(
                PlatformRanking(
                    platform=platform,
                    rank=0,
                    score=score,
                    geometric_mean_time=geo_mean,
                    total_time=total_time,
                    win_rate=win_rate,
                )
            )

        rankings.sort(key=lambda r: r.score)

        for i, ranking in enumerate(rankings):
            ranking.rank = i + 1

        return rankings

    def _generate_head_to_head(
        self,
        query_comparisons: dict[str, QueryComparison],
    ) -> list[HeadToHeadComparison]:
        comparisons = []
        platforms = self.platforms

        for i, platform_a in enumerate(platforms):
            for platform_b in platforms[i + 1 :]:
                wins_a, wins_b, ties, total_ratio = self._score_platform_pair(
                    query_comparisons,
                    platform_a,
                    platform_b,
                )

                geo_ratio = calculate_geometric_mean(total_ratio) if total_ratio else 1.0

                if wins_a > wins_b + ties:
                    winner = platform_a
                elif wins_b > wins_a + ties:
                    winner = platform_b
                else:
                    winner = None

                comparisons.append(
                    HeadToHeadComparison(
                        platform_a=platform_a,
                        platform_b=platform_b,
                        winner=winner,
                        performance_ratio=geo_ratio,
                        wins_a=wins_a,
                        wins_b=wins_b,
                        ties=ties,
                        insights=self._build_head_to_head_insights(
                            platform_a,
                            platform_b,
                            winner,
                            geo_ratio,
                            wins_a,
                            wins_b,
                            ties,
                        ),
                    )
                )

        return comparisons

    def _analyze_cost_performance(self) -> Optional[CostPerformanceAnalysis]:
        cost_data = {}
        perf_data = {}
        query_counts = {}

        for result in self.results:
            total_cost = published_total_cost(result.cost_summary)
            if total_cost is not None and total_cost > 0:
                cost_data[result.platform] = total_cost
                query_counts[result.platform] = result.total_queries

                total_time_sec = result.total_execution_time if result.total_execution_time else 1.0
                qps = result.total_queries / total_time_sec if total_time_sec > 0 else 0

                perf_data[result.platform] = qps / total_cost if total_cost > 0 else 0

        if not cost_data:
            return None

        cost_per_query = {
            platform: cost / query_counts[platform]
            for platform, cost in cost_data.items()
            if query_counts[platform] > 0
        }

        cost_rankings = sorted(cost_data.keys(), key=lambda p: cost_data[p])
        efficiency_rankings = sorted(perf_data.keys(), key=lambda p: perf_data[p], reverse=True)

        best_value = efficiency_rankings[0] if efficiency_rankings else cost_rankings[0]

        max_cost = max(cost_data.values()) if cost_data else 0
        potential_savings = {p: max_cost - c for p, c in cost_data.items()}

        return CostPerformanceAnalysis(
            platforms=list(cost_data.keys()),
            cost_per_query=cost_per_query,
            performance_per_dollar=perf_data,
            best_value=best_value,
            cost_rankings=cost_rankings,
            cost_efficiency_rankings=efficiency_rankings,
            potential_savings=potential_savings,
        )

    def _create_statistical_summary(
        self,
        query_comparisons: dict[str, QueryComparison],
    ) -> dict[str, Any]:
        significant_count = 0
        total_tests = 0
        avg_effect_size = []

        for qc in query_comparisons.values():
            if qc.statistical_test:
                total_tests += 1
                if qc.statistical_test.p_value < self.config.significance_level:
                    significant_count += 1
                if qc.statistical_test.effect_size is not None:
                    avg_effect_size.append(abs(qc.statistical_test.effect_size))

        return {
            "total_tests": total_tests,
            "significant_count": significant_count,
            "significant_percent": significant_count / total_tests * 100 if total_tests > 0 else 0,
            "average_effect_size": sum(avg_effect_size) / len(avg_effect_size) if avg_effect_size else 0,
            "bonferroni_applied": self.config.apply_bonferroni,
        }

    def _generate_insights(
        self,
        rankings: list[PlatformRanking],
        query_comparisons: dict[str, QueryComparison],
        head_to_head: list[HeadToHeadComparison],
        cost_analysis: Optional[CostPerformanceAnalysis],
    ) -> list[str]:
        del query_comparisons, head_to_head
        insights: list[str] = []
        insights.extend(self._insight_overall_winner(rankings))
        insights.extend(self._insight_win_rates(rankings))
        insights.extend(self._insight_consistency())
        insights.extend(self._insight_cost(cost_analysis))
        return insights

    def _score_platform_pair(
        self,
        query_comparisons: dict[str, QueryComparison],
        platform_a: str,
        platform_b: str,
    ) -> tuple[int, int, int, list[float]]:
        wins_a = 0
        wins_b = 0
        ties = 0
        total_ratio: list[float] = []
        threshold = self.config.performance_ratio_threshold

        for qc in query_comparisons.values():
            if platform_a not in qc.metrics or platform_b not in qc.metrics:
                continue
            ratio_a = qc.performance_ratios.get(platform_a, 1.0)
            ratio_b = qc.performance_ratios.get(platform_b, 1.0)
            if qc.metrics[platform_b].mean > 0:
                total_ratio.append(qc.metrics[platform_a].mean / qc.metrics[platform_b].mean)
            if ratio_a < ratio_b / threshold:
                wins_a += 1
            elif ratio_b < ratio_a / threshold:
                wins_b += 1
            else:
                ties += 1

        return wins_a, wins_b, ties, total_ratio

    @staticmethod
    def _build_head_to_head_insights(
        platform_a: str,
        platform_b: str,
        winner: str | None,
        geo_ratio: float,
        wins_a: int,
        wins_b: int,
        ties: int,
    ) -> list[str]:
        if not winner:
            return []

        loser = platform_b if winner == platform_a else platform_a
        wins = wins_a if winner == platform_a else wins_b
        total = wins_a + wins_b + ties
        insights = [f"{winner} wins {wins}/{total} queries against {loser}"]
        if geo_ratio == 1.0:
            return insights
        if geo_ratio > 1.0:
            insights.append(f"{platform_b} is {geo_ratio:.2f}x faster overall than {platform_a}")
        else:
            insights.append(f"{platform_a} is {1 / geo_ratio:.2f}x faster overall than {platform_b}")
        return insights

    @staticmethod
    def _insight_overall_winner(rankings: list[PlatformRanking]) -> list[str]:
        if not rankings or len(rankings) == 1:
            return []
        winner = rankings[0]
        runner_up = rankings[1]
        speedup = runner_up.geometric_mean_time / winner.geometric_mean_time
        if speedup > 1.1:
            return [f"{winner.platform} is the overall winner, {speedup:.2f}x faster than {runner_up.platform}"]
        return [f"{winner.platform} edges out {runner_up.platform} with similar performance"]

    @staticmethod
    def _insight_win_rates(rankings: list[PlatformRanking]) -> list[str]:
        return [
            f"{ranking.platform} wins {ranking.win_rate:.0f}% of queries"
            for ranking in rankings[:3]
            if ranking.win_rate >= 70
        ]

    def _insight_consistency(self) -> list[str]:
        insights: list[str] = []
        for result in self.results:
            times = _extract_query_times(result)
            if not times:
                continue
            cv = _calculate_cv(list(times.values()))
            if cv < 0.2:
                insights.append(f"{result.platform} shows very consistent performance (CV={cv:.2f})")
            elif cv > 0.5:
                insights.append(f"{result.platform} shows high variance in query times (CV={cv:.2f})")
        return insights

    @staticmethod
    def _insight_cost(cost_analysis: Optional[CostPerformanceAnalysis]) -> list[str]:
        if not cost_analysis:
            return []
        insights = [f"{cost_analysis.best_value} offers the best price/performance"]
        if len(cost_analysis.cost_rankings) <= 1:
            return insights
        cheapest = cost_analysis.cost_rankings[0]
        most_expensive = cost_analysis.cost_rankings[-1]
        savings = cost_analysis.potential_savings.get(cheapest, 0)
        if savings > 0:
            insights.append(f"Switching from {most_expensive} to {cheapest} could save ${savings:.2f}")
        return insights


def _dict_to_benchmark_results(data: dict[str, Any]) -> BenchmarkResults:
    from benchbox.core.results.loader import reconstruct_benchmark_results
    from benchbox.core.results.schema_policy import is_loader_supported_result_schema, result_schema_version_value

    version = result_schema_version_value(data)
    if not is_loader_supported_result_schema(data):
        raise ValueError(f"Unsupported schema version: {version}. Only schema v2 is supported for comparison.")

    return reconstruct_benchmark_results(data)


def _is_comparable(execution) -> bool:
    return execution.status in {"SUCCESS", "UNKNOWN"}


def _extract_query_ids(result: BenchmarkResults) -> list[str]:
    query_ids = set()

    for qr in result.query_results or []:
        execution = query_execution_from_legacy_dict(qr)
        if execution.query_id and _is_comparable(execution):
            query_ids.add(execution.query_id)

    for timing in result.per_query_timings or []:
        execution = query_execution_from_legacy_dict(timing)
        if execution.query_id and _is_comparable(execution):
            query_ids.add(execution.query_id)

    return sorted(query_ids)


def _normalize_execution_time_ms(timing: Mapping[str, Any]) -> float | None:
    return query_execution_from_legacy_dict(timing).execution_time_ms


def _extract_query_times(result: BenchmarkResults) -> dict[str, float]:
    samples: dict[str, list[float]] = {}

    for qr in result.query_results or []:
        execution = query_execution_from_legacy_dict(qr)
        query_id = execution.query_id
        time_ms = execution.execution_time_ms
        if query_id and _is_comparable(execution) and time_ms is not None:
            samples.setdefault(query_id, []).append(time_ms)

    for timing in result.per_query_timings or []:
        execution = query_execution_from_legacy_dict(timing)
        query_id = execution.query_id
        time_ms = execution.execution_time_ms
        if query_id and _is_comparable(execution) and time_ms is not None:
            samples.setdefault(query_id, []).append(time_ms)

    return {query_id: sum(query_samples) / len(query_samples) for query_id, query_samples in samples.items()}


def _get_query_times_for_query(
    result: BenchmarkResults,
    query_id: str,
) -> list[float]:
    times = []

    for qr in result.query_results or []:
        execution = query_execution_from_legacy_dict(qr)
        if execution.query_id == query_id and _is_comparable(execution):
            time_ms = execution.execution_time_ms
            if time_ms is not None:
                times.append(time_ms)

    for timing in result.per_query_timings or []:
        execution = query_execution_from_legacy_dict(timing)
        if execution.query_id == query_id and _is_comparable(execution):
            time_ms = execution.execution_time_ms
            if time_ms is not None:
                times.append(time_ms)

    return times


def _get_common_queries(results: list[BenchmarkResults]) -> list[str]:
    if not results:
        return []

    query_sets = [set(_extract_query_ids(r)) for r in results]
    common = set.intersection(*query_sets) if query_sets else set()
    return sorted(common)


def _calculate_cv(values: list[float]) -> float:
    if not values or len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    std_dev = variance**0.5
    return std_dev / mean
