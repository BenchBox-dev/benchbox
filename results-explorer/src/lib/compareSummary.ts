import type { DetailResult } from "@/types";
import { formatSpeedup } from "@/lib/metricFormatters";
import { describeValidationStatus } from "@/lib/displayLabels";
import {
  compareEvidenceSummary,
  isComparable,
  isRankable,
  formatTimingExclusion,
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
  rankingBlocked: boolean;
  descriptiveGeomeanSpread: number | null;
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

interface HeadlineOptions extends CompareDecisionSummaryOptions {
  rankingBlocked?: boolean;
}

export function sharedQueryIdsFor(results: readonly DetailResult[]): string[] {
  const all = new Set<string>();
  for (const result of results) for (const timing of result.display_timings) all.add(timing.query_id);
  return [...all]
    .filter((queryId) => results.every((result) => timingValueForQuery(result, queryId) !== null))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
}

function fastestRunIndexesForQuery(results: readonly DetailResult[], queryId: string): number[] {
  const values = results.map((result) => timingValueForQuery(result, queryId) ?? Infinity);
  const fastest = Math.min(...values);
  return values.flatMap((value, index) => (value === fastest ? [index] : []));
}

export function countUniqueFastestQueries(results: readonly DetailResult[], sharedQueryIds: readonly string[]): number[] {
  const counts = new Array<number>(results.length).fill(0);
  for (const queryId of sharedQueryIds) {
    const fastest = fastestRunIndexesForQuery(results, queryId);
    if (fastest.length === 1) counts[fastest[0]!]! += 1;
  }
  return counts;
}

function rankingBlockReason(results: readonly DetailResult[]): string {
  const blocked = results.find((result) => isComparable(result) && !isRankable(result));
  const detail = blocked ? formatTimingExclusion(blocked.ranking_exclusion_reason, "") : "";
  return detail
    ? `no selected run is rank-safe; ${detail.charAt(0).toLowerCase()}${detail.slice(1)}`
    : "no selected run is rank-safe";
}

function geomeanSpread(results: readonly DetailResult[]): number | null {
  const values = results
    .filter(isComparable)
    .map((result) => result.display_geomean_ms)
    .filter(isValidTimingValue);
  if (values.length < 2) return null;
  return Math.max(...values) / Math.min(...values);
}

export function buildCompareDecisionSummary(
  results: DetailResult[],
  primaryMetric: ComparePrimaryMetric,
  options: CompareDecisionSummaryOptions = {},
): CompareDecisionSummary {
  const higherIsBetter = primaryMetricHigherIsBetter(primaryMetric);
  const primaryMetricLabel = labelForPrimaryMetric(primaryMetric);
  const evidence = compareEvidenceSummary(results);
  const metrics = results.map((result) => ({
    resultId: result.result_id,
    platform: result.platform,
    value: primaryMetricValue(result, primaryMetric),
    comparable: isComparable(result),
    rankable: isRankable(result),
  }));
  const comparableMetrics = metrics.filter(
    (metric): metric is CompareResultMetric & { value: number; comparable: boolean; rankable: boolean } =>
      metric.comparable && isValidTimingValue(metric.value),
  );
  const sortedMetrics = comparableMetrics
    .filter((metric) => metric.rankable)
    .sort((a, b) => (higherIsBetter ? b.value - a.value : a.value - b.value));
  const blockedByRanking =
    options.suppressWinnerClaims !== true && evidence.comparable && comparableMetrics.length > 0 && sortedMetrics.length === 0;
  const suppressWinnerClaims = options.suppressWinnerClaims === true || !evidence.comparable || blockedByRanking;
  const suppressionReason =
    options.suppressionReason ?? evidence.reason ?? (blockedByRanking ? rankingBlockReason(results) : null);

  const metricWinner = sortedMetrics[0] ?? null;
  const winner = suppressWinnerClaims ? null : metricWinner;
  const comparison = sortedMetrics.length > 1 ? sortedMetrics[sortedMetrics.length - 1]! : null;
  const comparisonRatio = winner && comparison ? metricRatio(winner.value, comparison.value, higherIsBetter) : null;
  const descriptiveGeomeanSpread = blockedByRanking ? geomeanSpread(results) : null;
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
    rankingBlocked: blockedByRanking,
    descriptiveGeomeanSpread,
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
      rankingBlocked: blockedByRanking,
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
  options: HeadlineOptions,
  validationCaveat: string | null,
): string {
  const caveatSuffix = validationCaveat ? ` ${validationCaveat}` : "";
  const boundarySuffix = options.comparisonBoundary ? ` ${options.comparisonBoundary}` : "";
  return `${buildHeadlineCore(winner, comparisonRatio, primaryMetric, options)}${caveatSuffix}${boundarySuffix}`;
}

function buildHeadlineCore(
  winner: CompareDecisionSummary["winner"],
  comparisonRatio: number | null,
  primaryMetric: ComparePrimaryMetric,
  options: HeadlineOptions,
): string {
  if (options.rankingBlocked) {
    return `No ranked winner: ${options.suppressionReason}. Only descriptive geomean comparisons are shown.`;
  }
  if (options.suppressWinnerClaims) {
    const reason = options.suppressionReason ?? "selected runs are not from the same comparable ranking";
    if (isQueryEvidenceSuppressionReason(reason)) {
      return `Insufficient comparable query evidence: ${reason}. Winner language is suppressed; raw query evidence remains available.`;
    }
    return `Not directly comparable: ${reason}. Winner language is suppressed; raw query evidence remains available.`;
  }
  if (!winner) return "The selected runs do not have enough primary-metric data for a comparison.";
  const winnerLabel = options.runLabels?.[winner.resultId] ?? winner.platform;
  if (comparisonRatio === null) return `${winnerLabel} leads on the selected primary metric.`;
  if (Math.abs(comparisonRatio - 1) < COMPARE_TIE_THRESHOLD) {
    return `The selected runs are within the tie threshold (${formatRatio(comparisonRatio)}) on the primary metric.`;
  }
  if (primaryMetric === "power_score") {
    return `In these selected runs, ${winnerLabel}'s power score was ${formatRatio(comparisonRatio)} better than the lowest selected run.`;
  }
  if (primaryMetric === "throughput_at_size") {
    return `In these selected runs, ${winnerLabel}'s Throughput@Size was ${formatRatio(comparisonRatio)} better than the lowest selected run.`;
  }
  return `In these selected runs, ${winnerLabel}'s geomean query time was ${formatRatio(comparisonRatio)} faster than the slowest selected run.`;
}

function isQueryEvidenceSuppressionReason(reason: string): boolean {
  const normalized = reason.toLowerCase();
  return normalized.includes("query") || normalized.includes("timing") || normalized.includes("coverage");
}

function buildWinnerQueryRecord(results: DetailResult[], winnerResultId: string | null): WinnerQueryRecord {
  const allQueryIds = new Set<string>();
  for (const result of results) for (const timing of result.display_timings) allQueryIds.add(timing.query_id);
  const totalQueries = allQueryIds.size;
  const sharedQueryIds = sharedQueryIdsFor(results);
  const winnerIndex = winnerResultId === null ? -1 : results.findIndex((result) => result.result_id === winnerResultId);
  if (winnerIndex < 0) {
    return { totalQueries, comparableQueries: 0, wins: 0, losses: 0, ties: 0, missing: totalQueries };
  }

  let wins = 0;
  let losses = 0;
  let ties = 0;
  for (const queryId of sharedQueryIds) {
    const fastest = fastestRunIndexesForQuery(results, queryId);
    if (!fastest.includes(winnerIndex)) losses += 1;
    else if (fastest.length > 1) ties += 1;
    else wins += 1;
  }

  return {
    totalQueries,
    comparableQueries: sharedQueryIds.length,
    wins,
    losses,
    ties,
    missing: totalQueries - sharedQueryIds.length,
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
