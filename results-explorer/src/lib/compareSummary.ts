import type { DetailResult } from "@/types";
import { formatSpeedup } from "@/lib/metricFormatters";
import { describeValidationStatus } from "@/lib/displayLabels";
import {
  compareEvidenceSummary,
  isComparable,
  isValidTimingValue,
  primaryMetricHigherIsBetter,
  primaryMetricLabel as labelForPrimaryMetric,
  primaryMetricValue,
  timingValueForQuery,
  validDisplayTimingValues,
  type PrimaryMetric,
} from "@/lib/displayEligibility";

export type ComparePrimaryMetric = PrimaryMetric;

export const COMPARE_TIE_THRESHOLD = 0.005;

export interface CompareResultMetric {
  resultId: string;
  platform: string;
  value: number | null;
}

export interface WinnerQueryRecord {
  totalQueries: number;
  comparableQueries: number;
  wins: number;
  losses: number;
  ties: number;
  missing: number;
}

export interface ComparePercentiles {
  resultId: string;
  platform: string;
  p50: number | null;
  p90: number | null;
  p99: number | null;
}

export interface CompareCostSummary {
  normalizedResultCount: number;
  winnerCostUsd: number | null;
  winnerCostPerformanceRatioVsWorst: number | null;
  winnerIsBestCostPerformance: boolean;
}

export interface CompareValidationCaveat {
  resultId: string;
  platform: string;
  status: string;
  label: string;
}

export interface CompareDecisionSummary {
  primaryMetric: ComparePrimaryMetric;
  primaryMetricLabel: string;
  higherIsBetter: boolean;
  claimSuppressed: boolean;
  claimSuppressionReason: string | null;
  winner: CompareResultMetric | null;
  winnerLabel: string | null;
  comparison: CompareResultMetric | null;
  comparisonRatio: number | null;
  comparisonLabel: string;
  isTie: boolean;
  headline: string;
  queryRecord: WinnerQueryRecord;
  percentiles: ComparePercentiles[];
  cost: CompareCostSummary | null;
  nonCleanValidation: CompareValidationCaveat[];
  validationCaveat: string | null;
  comparisonBoundary: string | null;
}

interface CompareDecisionSummaryOptions {
  suppressWinnerClaims?: boolean;
  suppressionReason?: string;
  runLabels?: Record<string, string>;
  comparisonBoundary?: string;
}

export function buildCompareDecisionSummary(
  results: DetailResult[],
  primaryMetric: ComparePrimaryMetric,
  options: CompareDecisionSummaryOptions = {},
): CompareDecisionSummary {
  const higherIsBetter = primaryMetricHigherIsBetter(primaryMetric);
  const primaryMetricLabel = labelForPrimaryMetric(primaryMetric);
  const evidence = compareEvidenceSummary(results);
  const suppressWinnerClaims = options.suppressWinnerClaims === true || !evidence.comparable;
  const suppressionReason = options.suppressionReason ?? evidence.reason ?? null;
  const metrics = results.map((result) => ({
    resultId: result.result_id,
    platform: result.platform,
    value: primaryMetricValue(result, primaryMetric),
    comparable: isComparable(result),
  }));
  const sortedMetrics = metrics
    .filter(
      (metric): metric is CompareResultMetric & { value: number; comparable: boolean } =>
        metric.comparable && isValidTimingValue(metric.value),
    )
    .sort((a, b) => (higherIsBetter ? b.value - a.value : a.value - b.value));

  const metricWinner = sortedMetrics[0] ?? null;
  const winner = suppressWinnerClaims ? null : metricWinner;
  const comparison = sortedMetrics.length > 1 ? sortedMetrics[sortedMetrics.length - 1]! : null;
  const comparisonRatio = winner && comparison ? metricRatio(winner.value, comparison.value, higherIsBetter) : null;
  const comparisonLabel = higherIsBetter
    ? "better than the lowest selected run"
    : "faster than the slowest selected run";
  const queryRecord = buildWinnerQueryRecord(results, winner?.resultId ?? null);
  const percentiles = results.map((result) => buildPercentiles(result));
  const cost = buildCostSummary(results, winner, primaryMetric);
  const isTie = comparisonRatio !== null && Math.abs(comparisonRatio - 1) < COMPARE_TIE_THRESHOLD;
  const winnerLabel = winner ? options.runLabels?.[winner.resultId] ?? winner.platform : null;
  const nonCleanValidation = buildNonCleanValidation(results);
  const validationCaveat = buildValidationCaveat(nonCleanValidation);

  return {
    primaryMetric,
    primaryMetricLabel,
    higherIsBetter,
    claimSuppressed: suppressWinnerClaims,
    claimSuppressionReason: suppressionReason,
    winner,
    winnerLabel,
    comparison,
    comparisonRatio,
    comparisonLabel,
    isTie,
    headline: buildHeadline(winner, comparisonRatio, primaryMetric, {
      ...options,
      suppressWinnerClaims,
      suppressionReason: suppressionReason ?? undefined,
    }, validationCaveat),
    queryRecord,
    percentiles,
    cost,
    nonCleanValidation,
    validationCaveat,
    comparisonBoundary: options.comparisonBoundary ?? null,
  };
}

function buildNonCleanValidation(results: DetailResult[]): CompareValidationCaveat[] {
  const caveats: CompareValidationCaveat[] = [];
  for (const result of results) {
    const raw = result.validation_status;
    if (raw === null || raw === undefined || raw === "") continue;
    const info = describeValidationStatus(raw);
    if (info.isClean) continue;
    caveats.push({ resultId: result.result_id, platform: result.platform, status: raw, label: info.label });
  }
  return caveats;
}

function buildValidationCaveat(nonCleanValidation: CompareValidationCaveat[]): string | null {
  if (nonCleanValidation.length === 0) return null;
  const parts = nonCleanValidation.map((entry) => `${entry.platform}: ${entry.label}`);
  return `Validation caution: ${parts.join("; ")}. This comparison rests on at least one unvalidated or non-clean result - treat the leading-run claim as provisional.`;
}

function metricRatio(winnerValue: number, comparisonValue: number, higherIsBetter: boolean): number | null {
  if (!isValidTimingValue(winnerValue) || !isValidTimingValue(comparisonValue)) return null;
  return higherIsBetter ? winnerValue / comparisonValue : comparisonValue / winnerValue;
}

function buildHeadline(
  winner: CompareDecisionSummary["winner"],
  comparisonRatio: number | null,
  primaryMetric: ComparePrimaryMetric,
  options: CompareDecisionSummaryOptions,
  validationCaveat: string | null,
): string {
  const boundarySuffix = options.comparisonBoundary ? ` ${options.comparisonBoundary}` : "";
  if (options.suppressWinnerClaims) {
    const reason = options.suppressionReason ?? "selected runs are not from the same comparable ranking";
    if (isQueryEvidenceSuppressionReason(reason)) {
      return `Insufficient comparable query evidence: ${reason}. Winner language is suppressed; raw query evidence remains available.${boundarySuffix}`;
    }
    return `Not directly comparable: ${reason}. Winner language is suppressed; raw query evidence remains available.${boundarySuffix}`;
  }
  if (!winner) return `The selected runs do not have enough primary-metric data for a comparison.${boundarySuffix}`;
  const winnerLabel = options.runLabels?.[winner.resultId] ?? winner.platform;
  const caveatSuffix = `${validationCaveat ? ` ${validationCaveat}` : ""}${boundarySuffix}`;
  if (comparisonRatio === null) return `${winnerLabel} leads on the selected primary metric.${caveatSuffix}`;
  if (Math.abs(comparisonRatio - 1) < COMPARE_TIE_THRESHOLD) {
    return `The selected runs are within the tie threshold (${formatRatio(comparisonRatio)}) on the primary metric.${caveatSuffix}`;
  }
  if (primaryMetric === "power_score") {
    return `In these selected runs, ${winnerLabel}'s power score was ${formatRatio(comparisonRatio)} better than the lowest selected run.${caveatSuffix}`;
  }
  if (primaryMetric === "throughput_at_size") {
    return `In these selected runs, ${winnerLabel}'s Throughput@Size was ${formatRatio(comparisonRatio)} better than the lowest selected run.${caveatSuffix}`;
  }
  return `In these selected runs, ${winnerLabel}'s geomean query time was ${formatRatio(comparisonRatio)} faster than the slowest selected run.${caveatSuffix}`;
}

function isQueryEvidenceSuppressionReason(reason: string): boolean {
  const normalized = reason.toLowerCase();
  return normalized.includes("query") || normalized.includes("timing") || normalized.includes("coverage");
}

function buildWinnerQueryRecord(results: DetailResult[], winnerResultId: string | null): WinnerQueryRecord {
  const queryIds = [...new Set(results.flatMap((result) => result.display_timings.map((timing) => timing.query_id)))];
  if (!winnerResultId) {
    return {
      totalQueries: queryIds.length,
      comparableQueries: 0,
      wins: 0,
      losses: 0,
      ties: 0,
      missing: queryIds.length,
    };
  }

  let comparableQueries = 0;
  let wins = 0;
  let losses = 0;
  let ties = 0;
  let missing = 0;

  for (const queryId of queryIds) {
    const entries = results
      .map((result) => ({
        resultId: result.result_id,
        value: timingValueForQuery(result, queryId),
      }))
      .filter((entry): entry is { resultId: string; value: number } => isValidTimingValue(entry.value));
    const winnerEntry = entries.find((entry) => entry.resultId === winnerResultId);
    const competitorCount = entries.filter((entry) => entry.resultId !== winnerResultId).length;
    if (!winnerEntry || competitorCount === 0) {
      missing += 1;
      continue;
    }
    comparableQueries += 1;
    const fastest = Math.min(...entries.map((entry) => entry.value));
    const fastestCount = entries.filter((entry) => entry.value === fastest).length;
    if (winnerEntry.value === fastest && fastestCount > 1) {
      ties += 1;
    } else if (winnerEntry.value === fastest) {
      wins += 1;
    } else {
      losses += 1;
    }
  }

  return {
    totalQueries: queryIds.length,
    comparableQueries,
    wins,
    losses,
    ties,
    missing,
  };
}

function buildPercentiles(result: DetailResult): ComparePercentiles {
  const values = validDisplayTimingValues(result.display_timings).sort((a, b) => a - b);
  return {
    resultId: result.result_id,
    platform: result.platform,
    p50: percentile(values, 50),
    p90: percentile(values, 90),
    p99: percentile(values, 99),
  };
}

function percentile(sortedValues: number[], pct: number): number | null {
  if (sortedValues.length === 0) return null;
  if (sortedValues.length === 1) return sortedValues[0]!;
  const position = (pct / 100) * (sortedValues.length - 1);
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  const lowerValue = sortedValues[lower]!;
  const upperValue = sortedValues[upper]!;
  return lowerValue + (upperValue - lowerValue) * (position - lower);
}

function buildCostSummary(
  results: DetailResult[],
  winner: (CompareResultMetric & { value: number }) | null,
  primaryMetric: ComparePrimaryMetric,
): CompareCostSummary | null {
  const normalized = results
    .map((result) => ({
      resultId: result.result_id,
      comparable: isComparable(result),
      cost: result.normalized_cost_usd ?? null,
      costStatus: result.cost_status ?? null,
      primaryValue: primaryMetricValue(result, primaryMetric),
    }))
    .filter(
      (entry): entry is { resultId: string; comparable: boolean; cost: number; costStatus: string; primaryValue: number } =>
        entry.comparable &&
        entry.costStatus === "normalized" &&
        entry.cost !== null &&
        entry.cost > 0 &&
        isValidTimingValue(entry.primaryValue),
    );

  if (normalized.length === 0) return null;

  const scored = normalized.map((entry) => ({
    resultId: entry.resultId,
    cost: entry.cost,
    costPerformance:
      primaryMetricHigherIsBetter(primaryMetric)
        ? entry.primaryValue / entry.cost
        : 1 / (entry.primaryValue * entry.cost),
  }));
  const winnerCost = winner ? scored.find((entry) => entry.resultId === winner.resultId) : undefined;
  const sorted = [...scored].sort((a, b) => b.costPerformance - a.costPerformance);
  const worst = sorted[sorted.length - 1];
  const ratio =
    winnerCost && worst && isValidTimingValue(worst.costPerformance) ? winnerCost.costPerformance / worst.costPerformance : null;

  return {
    normalizedResultCount: normalized.length,
    winnerCostUsd: winnerCost?.cost ?? null,
    winnerCostPerformanceRatioVsWorst: ratio,
    winnerIsBestCostPerformance: Boolean(winnerCost && sorted[0]?.resultId === winnerCost.resultId),
  };
}

export function formatRatio(value: number): string {
  return formatSpeedup(value).valueText;
}
