import { canonicalPhase } from "@/lib/displayLabels";
import type {
  BenchmarkSummary,
  DetailResult,
  MetaCohort,
  MetaCohortPlatform,
  PlatformRow,
  QueryDisplayTiming,
} from "@/types";

export type EligibilityRow = DetailResult | PlatformRow | MetaCohortPlatform;
export type PrimaryMetricRow = DetailResult | PlatformRow;
export type PrimaryMetric = "power_score" | "throughput_at_size" | "display_geomean_ms";
export type ChartDatasetEligibilityClass =
  | "display_safe"
  | "rank_safe"
  | "compare_safe"
  | "cost_safe"
  | "trend_safe"
  | "provenance_only";

export interface CompareEvidenceSummary {
  comparable: boolean;
  reason: string | null;
  totalQueries: number;
  commonValidQueries: number;
  requiredValidQueriesPerResult: number;
}

const EXCLUSION_LABELS: Record<string, string> = {
  compliance_not_rankable: "This result does not meet the requirements for ranking.",
  display_metric_unavailable: "Display timing is unavailable.",
  failed_queries: "One or more queries failed.",
  hidden_result: "Result is not in the public comparison set.",
  insufficient_common_valid_timing_coverage: "Selected runs do not share enough valid query timings.",
  insufficient_common_valid_timings: "Selected runs do not share at least two valid query timings.",
  insufficient_query_coverage: "Result does not have enough valid query coverage.",
  insufficient_valid_timings: "Result does not have enough valid query timings.",
  invalid_timing: "Timing is invalid.",
  missing_display_metric: "Display timing is unavailable.",
  missing_primary_metric: "Primary metric is unavailable.",
  missing_timing: "Timing is missing.",
  no_comparable_results: "No comparable results are available.",
  no_display_timing: "Display timing is unavailable.",
  no_rankable_results: "No results meet the requirements for ranking.",
  no_valid_display_timing: "No valid display timing is available.",
  non_positive_primary_metric: "Primary metric is not positive.",
  non_positive_timing: "Timing is not positive.",
  partial_query_coverage: "Result has partial query coverage.",
  stream_count_mismatch: "Recorded throughput stream count does not match the configured count.",
  trust_not_rankable: "Results from this source are not included in rankings.",
  tuning_not_applied: "Custom tuning was requested, but the run recorded no applied settings.",
  validation_not_clean: "Validation status excludes this result from ranking.",
  visibility_not_comparable: "Visibility policy excludes this result from comparison.",
  visibility_not_rankable: "This result is not published for ranking.",
  zero_timing: "A zero timing cannot be used in this ranking.",
  zero_timings_only: "Only exact zero timings are available.",
};

export function isValidTimingValue(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value) && value > 0;
}

export function validTimingValues(values: readonly (number | null | undefined)[]): number[] {
  return values.filter(isValidTimingValue);
}

export function displayTimingValue(timing: QueryDisplayTiming | null | undefined): number | null {
  if (!timing?.is_valid_display_timing) return null;
  return isValidTimingValue(timing.display_ms) ? timing.display_ms : null;
}

export function validDisplayTimingValues(timings: readonly QueryDisplayTiming[]): number[] {
  return timings.map(displayTimingValue).filter(isValidTimingValue);
}

export function timingValueForQuery(result: DetailResult, queryId: string): number | null {
  return displayTimingValue(result.display_timings.find((timing) => timing.query_id === queryId));
}

export function platformTimingValue(row: PlatformRow, queryId: string): number | null {
  const eligibility = row.timing_eligibility[queryId];
  if (!eligibility?.is_valid_display_timing) return null;
  return validPlatformTimingValue(row.timings[queryId] ?? null);
}

export function validPlatformTimingValue(value: number | null | undefined): number | null {
  return isValidTimingValue(value) ? value : null;
}

export function isTimingDisplayable(row: EligibilityRow): boolean {
  return row.display_exclusion_reason === null;
}

export function isComparable(row: EligibilityRow): boolean {
  return row.comparison_exclusion_reason === null;
}

export function isRankable(row: EligibilityRow): boolean {
  return row.ranking_exclusion_reason === null;
}

export function isNormalizedCostSafe(row: PlatformRow): boolean {
  return row.cost_status === "normalized" && Number.isFinite(row.normalized_cost_usd);
}

export function isChartDatasetEligible(row: PlatformRow, eligibilityClass: ChartDatasetEligibilityClass): boolean {
  switch (eligibilityClass) {
    case "display_safe":
      return isTimingDisplayable(row);
    case "rank_safe":
      return isRankable(row);
    case "compare_safe":
      return isComparable(row);
    case "cost_safe":
      return isNormalizedCostSafe(row);
    case "trend_safe":
    case "provenance_only":
      return true;
  }
}

export function filterSummaryForChartDataset(
  summary: BenchmarkSummary,
  eligibilityClass: ChartDatasetEligibilityClass,
): BenchmarkSummary {
  if (eligibilityClass === "provenance_only" || eligibilityClass === "trend_safe") return summary;
  if (eligibilityClass === "compare_safe" && summary.platforms.some((platform) => !isComparable(platform))) {
    return { ...summary, platforms: [] };
  }
  return {
    ...summary,
    platforms: summary.platforms.filter((platform) => isChartDatasetEligible(platform, eligibilityClass)),
  };
}

export function chartDatasetClassLabel(eligibilityClass: ChartDatasetEligibilityClass): string {
  switch (eligibilityClass) {
    case "display_safe":
      return "valid display timings";
    case "rank_safe":
      return "ranked runs";
    case "compare_safe":
      return "comparable selected runs";
    case "cost_safe":
      return "normalized cost rows";
    case "trend_safe":
      return "trend-safe rows";
    case "provenance_only":
      return "published runs";
  }
}

export function chartDatasetEmptyTitle(eligibilityClass: ChartDatasetEligibilityClass): string {
  switch (eligibilityClass) {
    case "display_safe":
      return "No valid display timings";
    case "rank_safe":
      return "No ranked runs";
    case "compare_safe":
      return "No comparable query timings";
    case "cost_safe":
      return "No normalized cost data";
    case "trend_safe":
      return "Not enough runs for a trend";
    case "provenance_only":
      return "No published runs";
  }
}

export function chartDatasetExclusionReason(
  row: PlatformRow,
  eligibilityClass: ChartDatasetEligibilityClass,
): string | null {
  if (isChartDatasetEligible(row, eligibilityClass)) return null;
  switch (eligibilityClass) {
    case "display_safe":
      return formatTimingExclusion(row.display_exclusion_reason, "Display timing is unavailable.");
    case "rank_safe":
      return formatTimingExclusion(row.ranking_exclusion_reason, "This result is not included in rankings.");
    case "compare_safe":
      return formatTimingExclusion(row.comparison_exclusion_reason, "Result is not comparable.");
    case "cost_safe":
      return normalizedCostExclusionReason(row);
    case "trend_safe":
    case "provenance_only":
      return null;
  }
}

export function summarizeChartDatasetExclusions(
  platforms: readonly PlatformRow[],
  eligibilityClass: ChartDatasetEligibilityClass,
): { reason: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const platform of platforms) {
    const reason = chartDatasetExclusionReason(platform, eligibilityClass);
    if (reason === null) continue;
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([reason, count]) => ({ reason, count }))
    .sort((a, b) => b.count - a.count || a.reason.localeCompare(b.reason));
}

export function normalizePrimaryMetric(metric: string | null | undefined): PrimaryMetric {
  if (metric === "power_score" || metric === "throughput_at_size") return metric;
  return "display_geomean_ms";
}

export function primaryMetricHigherIsBetter(metric: PrimaryMetric): boolean {
  return metric !== "display_geomean_ms";
}

export function primaryMetricLabel(metric: PrimaryMetric): string {
  switch (metric) {
    case "power_score":
      return "Power score";
    case "throughput_at_size":
      return "Throughput@Size";
    case "display_geomean_ms":
      return "Geomean query time";
  }
}

export function phaseScoreValue(row: {
  test_type?: string | null;
  phase?: string | null;
  power_score: number | null;
  throughput_at_size?: number | null;
}): number | null {
  return canonicalPhase(row.phase ?? row.test_type) === "throughput" ? (row.throughput_at_size ?? null) : row.power_score;
}

export function primaryMetricValue(row: PrimaryMetricRow, metric: PrimaryMetric): number | null {
  switch (metric) {
    case "power_score":
      return row.power_score;
    case "throughput_at_size":
      return row.throughput_at_size ?? null;
    case "display_geomean_ms":
      return row.display_geomean_ms;
  }
}

export function validPrimaryMetricValue(row: PrimaryMetricRow, metric: PrimaryMetric): number | null {
  const value = primaryMetricValue(row, metric);
  return isValidTimingValue(value) ? value : null;
}

export function formatTimingExclusion(
  input: EligibilityRow | QueryDisplayTiming | string | null | undefined,
  fallback = "This timing cannot be displayed.",
): string {
  const reason = exclusionReason(input);
  if (reason === null) return fallback;
  return EXCLUSION_LABELS[reason] ?? humanizeReason(reason);
}

export function formatCohortExclusion(cohort: BenchmarkSummary | MetaCohort): string | null {
  if ("cohort_ranking_exclusion_reason" in cohort && cohort.cohort_ranking_exclusion_reason !== null) {
    return formatTimingExclusion(cohort.cohort_ranking_exclusion_reason, "Ranking is not available.");
  }
  if ("platforms" in cohort && cohort.platforms && cohort.platforms.length > 0) {
    if (cohort.platforms.every((platform) => !isRankable(platform))) {
      return EXCLUSION_LABELS.no_rankable_results ?? "No results meet the requirements for ranking.";
    }
  }
  return null;
}

export function compareEvidenceSummary(results: readonly DetailResult[]): CompareEvidenceSummary {
  const queryIds = [...new Set(results.flatMap((result) => result.display_timings.map((timing) => timing.query_id)))];
  const requiredValidQueriesPerResult = Math.max(2, Math.ceil(queryIds.length * 0.5));
  const commonValidQueries = countCommonValidQueries(results, queryIds);
  const ineligible = results.find((result) => !isComparable(result));
  if (ineligible) {
    return {
      comparable: false,
      reason: formatTimingExclusion(ineligible.comparison_exclusion_reason, "Selected run is not comparable."),
      totalQueries: queryIds.length,
      commonValidQueries,
      requiredValidQueriesPerResult,
    };
  }

  if (commonValidQueries < 2) {
    return {
      comparable: false,
      reason: EXCLUSION_LABELS.insufficient_common_valid_timings ?? "Selected runs do not share enough valid timings.",
      totalQueries: queryIds.length,
      commonValidQueries,
      requiredValidQueriesPerResult,
    };
  }
  const underCovered = results.find(
    (result) => validDisplayTimingValues(result.display_timings).length < requiredValidQueriesPerResult,
  );
  if (underCovered) {
    return {
      comparable: false,
      reason:
        EXCLUSION_LABELS.insufficient_common_valid_timing_coverage ??
        "Selected runs do not share enough valid query timing coverage.",
      totalQueries: queryIds.length,
      commonValidQueries,
      requiredValidQueriesPerResult,
    };
  }
  return {
    comparable: true,
    reason: null,
    totalQueries: queryIds.length,
    commonValidQueries,
    requiredValidQueriesPerResult,
  };
}

function countCommonValidQueries(results: readonly DetailResult[], queryIds: readonly string[]): number {
  return queryIds.filter((queryId) =>
    results.every((result) => timingValueForQuery(result, queryId) !== null),
  ).length;
}

function exclusionReason(input: EligibilityRow | QueryDisplayTiming | string | null | undefined): string | null {
  if (typeof input === "string") return input;
  if (!input) return null;
  if ("timing_exclusion_reason" in input) return input.timing_exclusion_reason;
  return (
    input.display_exclusion_reason ??
    input.comparison_exclusion_reason ??
    input.ranking_exclusion_reason ??
    null
  );
}

function humanizeReason(reason: string): string {
  const text = reason.replace(/_/g, " ");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}

function normalizedCostExclusionReason(row: PlatformRow): string {
  if (row.cost_status === "not_applicable_local") return "Local rows do not have normalized cloud cost.";
  if (row.cost_status === "unavailable") return "Normalized cost was not recorded.";
  if (row.cost_status === null || row.cost_status === undefined) return "Cost status is not recorded.";
  return "No finite normalized cost value is available.";
}
