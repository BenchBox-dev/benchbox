export type PassSelection =
  | { readonly kind: "all_warm" }
  | { readonly kind: "warmup" }
  | { readonly kind: "warm_pass"; readonly pass: number };

export type BasisStatistic = "median" | "min";

export interface MeasurementBasis {
  readonly passes: PassSelection;
  readonly statistic: BasisStatistic;
}

export const ALL_WARM: PassSelection = { kind: "all_warm" };
export const WARMUP: PassSelection = { kind: "warmup" };
export const warmPass = (pass: number): PassSelection => ({ kind: "warm_pass", pass });

export const DEFAULT_BASIS: MeasurementBasis = { passes: ALL_WARM, statistic: "median" };

export function isDefaultBasis(basis: MeasurementBasis): boolean {
  return basis.passes.kind === "all_warm" && basis.statistic === "median";
}

export function isCollapsedStatistic(basis: MeasurementBasis): boolean {
  return basis.passes.kind !== "all_warm";
}

export function passSelectionsEqual(a: PassSelection, b: PassSelection): boolean {
  if (a.kind !== b.kind) return false;
  if (a.kind === "warm_pass" && b.kind === "warm_pass") return a.pass === b.pass;
  return true;
}

export function basesEqual(a: MeasurementBasis, b: MeasurementBasis): boolean {
  return a.statistic === b.statistic && passSelectionsEqual(a.passes, b.passes);
}

export interface CrossRunSeries {
  readonly resultId: string;
  readonly label?: string;
}

export interface WithinRunSeries {
  readonly basis: MeasurementBasis;
  readonly label?: string;
}

type TwoOrMore<T> = readonly [T, T, ...T[]];

export const MAX_COMPARISON_SERIES = 10;

export interface CrossRunComparison {
  readonly kind: "cross_run";
  readonly runs: TwoOrMore<CrossRunSeries>;
  readonly basis: MeasurementBasis;
}

export interface WithinRunComparison {
  readonly kind: "within_run";
  readonly resultId: string;
  readonly series: TwoOrMore<WithinRunSeries>;
}

export type BasisComparison = CrossRunComparison | WithinRunComparison;

export type ComparisonError =
  | { readonly kind: "too_few_series"; readonly count: number }
  | { readonly kind: "too_many_series"; readonly count: number; readonly max: number }
  | { readonly kind: "duplicate_basis" };

export type ComparisonResult<T> =
  | { readonly ok: true; readonly comparison: T }
  | { readonly ok: false; readonly error: ComparisonError };

function cardinalityError(count: number): ComparisonError | null {
  if (count < 2) return { kind: "too_few_series", count };
  if (count > MAX_COMPARISON_SERIES) {
    return { kind: "too_many_series", count, max: MAX_COMPARISON_SERIES };
  }
  return null;
}

export function crossRunComparison(
  runs: readonly CrossRunSeries[],
  basis: MeasurementBasis,
): ComparisonResult<CrossRunComparison> {
  const error = cardinalityError(runs.length);
  if (error) return { ok: false, error };
  const [first, second, ...rest] = runs;
  if (!first || !second) return { ok: false, error: { kind: "too_few_series", count: runs.length } };
  return { ok: true, comparison: { kind: "cross_run", runs: [first, second, ...rest], basis } };
}

export function withinRunComparison(
  resultId: string,
  series: readonly WithinRunSeries[],
): ComparisonResult<WithinRunComparison> {
  const error = cardinalityError(series.length);
  if (error) return { ok: false, error };
  for (let i = 0; i < series.length; i += 1) {
    for (let j = i + 1; j < series.length; j += 1) {
      const a = series[i];
      const b = series[j];
      if (a && b && basesEqual(a.basis, b.basis)) {
        return { ok: false, error: { kind: "duplicate_basis" } };
      }
    }
  }
  const [first, second, ...rest] = series;
  if (!first || !second) {
    return { ok: false, error: { kind: "too_few_series", count: series.length } };
  }
  return {
    ok: true,
    comparison: { kind: "within_run", resultId, series: [first, second, ...rest] },
  };
}

export function basesInComparison(comparison: BasisComparison): MeasurementBasis[] {
  if (comparison.kind === "cross_run") return [comparison.basis];
  const out: MeasurementBasis[] = [];
  for (const s of comparison.series) {
    if (!out.some((b) => basesEqual(b, s.basis))) out.push(s.basis);
  }
  return out;
}

import { geomeanMs } from "@/lib/chartMath";
import type { UrlSerde } from "@/lib/useUrlState";
import type { DetailResult, QueryDisplayTiming } from "@/types";

export const BASIS_URL_KEY = "basis";
export const BASES_URL_KEY = "bases";

const STATISTICS: readonly BasisStatistic[] = ["median", "min"];
const WARM_PASS_TOKEN = /^warm_pass_(\d+)$/;

function passToken(passes: PassSelection): string {
  switch (passes.kind) {
    case "all_warm":
      return "all_warm";
    case "warmup":
      return "warmup";
    case "warm_pass":
      return `warm_pass_${passes.pass}`;
  }
}

export function encodeBasis(basis: MeasurementBasis): string {
  if (isDefaultBasis(basis)) return "default";
  const token = passToken(basis.passes);
  return basis.statistic === "median" ? token : `${token}:${basis.statistic}`;
}

export function decodeBasis(raw: string): MeasurementBasis | null {
  const [passTokenRaw, statisticToken, ...rest] = raw.split(":");
  if (rest.length > 0 || passTokenRaw === undefined) return null;
  const passToken = passTokenRaw;

  if (statisticToken !== undefined && !STATISTICS.includes(statisticToken as BasisStatistic)) {
    return null;
  }
  const statistic = (statisticToken ?? "median") as BasisStatistic;

  if (passToken === "default") {
    return statisticToken === undefined ? DEFAULT_BASIS : null;
  }
  if (passToken === "all_warm") return { passes: ALL_WARM, statistic };
  if (passToken === "warmup") return { passes: WARMUP, statistic };
  const match = WARM_PASS_TOKEN.exec(passToken);
  if (match?.[1] !== undefined) {
    const pass = Number(match[1]);
    if (!Number.isInteger(pass) || pass < 1) return null;
    return { passes: warmPass(pass), statistic };
  }
  return null;
}

export const basisSerde: UrlSerde<MeasurementBasis> = {
  encode: encodeBasis,
  decode: decodeBasis,
};

export const basesSerde: UrlSerde<MeasurementBasis[]> = {
  encode: (bases) => bases.map(encodeBasis).join(","),
  decode: (raw) => {
    if (raw === "") return [];
    const decoded = raw.split(",").map(decodeBasis);
    if (decoded.some((b) => b === null)) return null;
    return decoded as MeasurementBasis[];
  },
};

export interface BasisExecution {
  readonly query_id: string;
  readonly duration_ms: number;
  readonly status: string;
  readonly run_type: string | null;
  readonly iter: number | null;
}

export interface BasisDisplayTiming {
  readonly query_id: string;
  readonly display_ms: number | null;
  readonly is_valid_display_timing?: boolean;
}

export type BasisUnavailableReason =
  | "no_warmup_recorded"
  | "no_warm_passes_recorded"
  | "pass_not_recorded"
  | "no_passing_executions"
  | "zero_timing"
  | "display_value_excluded";

const UNAVAILABLE_LABELS: Record<BasisUnavailableReason, string> = {
  no_warmup_recorded: "This run did not record a warmup pass.",
  no_warm_passes_recorded: "This run did not record any warm passes.",
  pass_not_recorded: "This run did not record that warm pass.",
  no_passing_executions: "No execution of this query passed under that basis.",
  zero_timing: "Recorded execution timing was zero.",
  display_value_excluded: "The published value for this query is excluded from display evidence.",
};

export function basisUnavailableLabel(reason: BasisUnavailableReason): string {
  return UNAVAILABLE_LABELS[reason];
}

export type BasisValue =
  | { readonly kind: "value"; readonly ms: number; readonly sampleCount: number; readonly collapsed: boolean }
  | { readonly kind: "unavailable"; readonly reason: BasisUnavailableReason };

function isPassing(row: BasisExecution): boolean {
  return row.status === "pass";
}

function warmExecutionCandidates(rows: readonly BasisExecution[]): BasisExecution[] {
  const measurement = rows.filter((r) => r.run_type === "measurement");
  if (measurement.length > 0) return measurement;
  return rows.filter((r) => r.run_type === null);
}

function warmExecutions(rows: readonly BasisExecution[]): BasisExecution[] {
  return warmExecutionCandidates(rows).filter(isPassing);
}

export function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[mid] ?? null;
  const lower = sorted[mid - 1];
  const upper = sorted[mid];
  if (lower === undefined || upper === undefined) return null;
  return (lower + upper) / 2;
}

function applyStatistic(values: readonly number[], statistic: BasisStatistic): number | null {
  if (values.length === 0) return null;
  return statistic === "min" ? Math.min(...values) : median(values);
}

function selectExecutions(
  rows: readonly BasisExecution[],
  passes: PassSelection,
): { rows: BasisExecution[]; missingReason: BasisUnavailableReason | null } {
  switch (passes.kind) {
    case "all_warm": {
      const recorded = warmExecutionCandidates(rows);
      if (recorded.length === 0) return { rows: [], missingReason: "no_warm_passes_recorded" };
      const passing = recorded.filter(isPassing);
      return { rows: passing, missingReason: passing.length === 0 ? "no_passing_executions" : null };
    }
    case "warmup": {
      const recorded = rows.filter((r) => r.run_type === "warmup");
      if (recorded.length === 0) return { rows: [], missingReason: "no_warmup_recorded" };
      const passing = recorded.filter(isPassing);
      return { rows: passing, missingReason: passing.length === 0 ? "no_passing_executions" : null };
    }
    case "warm_pass": {
      const recorded = warmExecutionCandidates(rows);
      if (recorded.length === 0) return { rows: [], missingReason: "no_warm_passes_recorded" };
      const named = recorded.filter((r) => r.iter === passes.pass);
      if (named.length === 0) return { rows: [], missingReason: "pass_not_recorded" };
      const passing = named.filter(isPassing);
      return { rows: passing, missingReason: passing.length === 0 ? "no_passing_executions" : null };
    }
  }
}

export function resolveQueryValue(
  basis: MeasurementBasis,
  rows: readonly BasisExecution[],
  displayMs: number | null = null,
): BasisValue {
  if (isDefaultBasis(basis)) {
    const warm = warmExecutions(rows);
    if (displayMs !== null && Number.isFinite(displayMs) && displayMs > 0) {
      return { kind: "value", ms: displayMs, sampleCount: warm.length, collapsed: warm.length <= 1 };
    }
    const { missingReason } = selectExecutions(rows, ALL_WARM);
    return { kind: "unavailable", reason: missingReason ?? "display_value_excluded" };
  }

  const { rows: selected, missingReason } = selectExecutions(rows, basis.passes);
  if (missingReason !== null) return { kind: "unavailable", reason: missingReason };

  const nonZero = selected.map((r) => r.duration_ms).filter((ms) => Number.isFinite(ms) && ms > 0);
  const value = applyStatistic(nonZero, basis.statistic);
  if (value === null) {
    const hasZero = selected.some((r) => r.duration_ms === 0);
    return { kind: "unavailable", reason: hasZero ? "zero_timing" : "no_passing_executions" };
  }
  return { kind: "value", ms: value, sampleCount: nonZero.length, collapsed: nonZero.length <= 1 };
}

export interface BasisAvailability {
  readonly available: boolean;
  readonly reason: BasisUnavailableReason | null;
  readonly unansweredQueries: readonly string[];
}

function groupByQuery(rows: readonly BasisExecution[]): Map<string, BasisExecution[]> {
  const byQuery = new Map<string, BasisExecution[]>();
  for (const row of rows) {
    const bucket = byQuery.get(row.query_id);
    if (bucket) bucket.push(row);
    else byQuery.set(row.query_id, [row]);
  }
  return byQuery;
}

export function basisAvailability(
  basis: MeasurementBasis,
  rows: readonly BasisExecution[],
  displayTimings: readonly BasisDisplayTiming[] = [],
): BasisAvailability {
  const displayByQuery = new Map(displayTimings.map((t) => [t.query_id, t]));
  const byQuery = groupByQuery(rows);
  const unanswered: string[] = [];
  const reasons = new Set<BasisUnavailableReason>();
  let answered = 0;

  for (const [queryId, queryRows] of byQuery) {
    const timing = displayByQuery.get(queryId);
    const displayMs =
      timing && timing.is_valid_display_timing !== false ? (timing.display_ms ?? null) : null;
    const value = resolveQueryValue(basis, queryRows, displayMs);
    if (value.kind === "value") answered += 1;
    else {
      unanswered.push(queryId);
      reasons.add(value.reason);
    }
  }

  if (answered === 0) {
    const only = reasons.size === 1 ? [...reasons][0] : undefined;
    return {
      available: false,
      reason: only ?? "no_passing_executions",
      unansweredQueries: unanswered.sort(),
    };
  }
  return { available: true, reason: null, unansweredQueries: unanswered.sort() };
}

function basisHasSamples(
  executions: readonly BasisExecution[],
  passes: PassSelection,
  minimum: number,
): boolean {
  const basis: MeasurementBasis = { passes, statistic: "min" };
  for (const queryRows of groupByQuery(executions).values()) {
    const value = resolveQueryValue(basis, queryRows, null);
    if (value.kind === "value" && value.sampleCount >= minimum) return true;
  }
  return false;
}

export function availableBasesForExecutions(
  executions: readonly BasisExecution[],
  displayTimings?: readonly BasisDisplayTiming[],
): MeasurementBasis[] {
  const list: MeasurementBasis[] = [];
  const defaultAvailable = displayTimings === undefined || displayTimings.some(
    (timing) => timing.is_valid_display_timing !== false
      && timing.display_ms !== null
      && Number.isFinite(timing.display_ms)
      && timing.display_ms > 0,
  );
  if (defaultAvailable) list.push(DEFAULT_BASIS);
  if (basisHasSamples(executions, ALL_WARM, 2)) {
    list.push({ passes: ALL_WARM, statistic: "min" });
  }

  if (basisHasSamples(executions, WARMUP, 1)) {
    list.push({ passes: WARMUP, statistic: "median" });
    if (basisHasSamples(executions, WARMUP, 2)) {
      list.push({ passes: WARMUP, statistic: "min" });
    }
  }

  const iterations = new Set<number>();
  for (const row of executions) {
    if (row.status === "pass" && typeof row.iter === "number" && row.iter > 0) iterations.add(row.iter);
  }
  for (const iteration of [...iterations].sort((a, b) => a - b)) {
    const passes = warmPass(iteration);
    if (!basisHasSamples(executions, passes, 1)) continue;
    list.push({ passes, statistic: "median" });
    if (basisHasSamples(executions, passes, 2)) list.push({ passes, statistic: "min" });
  }

  return list.filter((basis, index) => !list.slice(0, index).some((earlier) => basesEqual(earlier, basis)));
}

function answeredQueriesForBasis(
  basis: MeasurementBasis,
  executions: readonly BasisExecution[],
  displayTimings: readonly BasisDisplayTiming[],
): Set<string> {
  const executionsByQuery = groupByQuery(executions);
  const displayByQuery = new Map(displayTimings.map((timing) => [timing.query_id, timing]));
  const queryIds = new Set([...executionsByQuery.keys(), ...displayByQuery.keys()]);
  const answered = new Set<string>();
  for (const queryId of queryIds) {
    const timing = displayByQuery.get(queryId);
    const displayMs = timing && timing.is_valid_display_timing !== false ? timing.display_ms : null;
    const value = resolveQueryValue(basis, executionsByQuery.get(queryId) ?? [], displayMs ?? null);
    if (value.kind === "value") answered.add(queryId);
  }
  return answered;
}

export function selectComparableBasisPair(
  executions: readonly BasisExecution[],
  displayTimings: readonly BasisDisplayTiming[],
  preferred: readonly MeasurementBasis[] = [],
): [MeasurementBasis, MeasurementBasis] | null {
  const available = availableBasesForExecutions(executions, displayTimings);
  const ordered = [
    ...preferred.filter((basis) => available.some((candidate) => basesEqual(candidate, basis))),
    ...available,
  ].filter((basis, index, list) => !list.slice(0, index).some((earlier) => basesEqual(earlier, basis)));
  const answered = new Map(
    ordered.map((basis) => [encodeBasis(basis), answeredQueriesForBasis(basis, executions, displayTimings)]),
  );

  for (let left = 0; left < ordered.length; left++) {
    for (let right = left + 1; right < ordered.length; right++) {
      const leftBasis = ordered[left]!;
      const rightBasis = ordered[right]!;
      const leftQueries = answered.get(encodeBasis(leftBasis))!;
      const rightQueries = answered.get(encodeBasis(rightBasis))!;
      if ([...leftQueries].some((queryId) => rightQueries.has(queryId))) {
        return [leftBasis, rightBasis];
      }
    }
  }
  return null;
}

export interface BasisSeriesInput {
  readonly key: string;
  readonly executions: readonly BasisExecution[];
  readonly displayTimings?: readonly BasisDisplayTiming[];
}

export interface BasisSeriesGeomean {
  readonly key: string;
  readonly geomeanMs: number | null;
  readonly values: readonly number[];
}

export interface SharedQueryGeomeans {
  readonly series: readonly BasisSeriesGeomean[];
  readonly sharedQueryIds: readonly string[];
  readonly excludedQueryIds: readonly string[];
}

export function sharedQueryGeomeans(
  basis: MeasurementBasis,
  series: readonly BasisSeriesInput[],
): SharedQueryGeomeans {
  const resolved = series.map((s) => {
    const displayByQuery = new Map((s.displayTimings ?? []).map((t) => [t.query_id, t]));
    const execsByQuery = groupByQuery(s.executions ?? []);
    const queryIds = new Set<string>([...execsByQuery.keys(), ...displayByQuery.keys()]);
    const values = new Map<string, number>();
    for (const queryId of queryIds) {
      const rows = execsByQuery.get(queryId) ?? [];
      const timing = displayByQuery.get(queryId);
      const displayMs =
        timing && timing.is_valid_display_timing !== false ? (timing.display_ms ?? null) : null;
      const value = resolveQueryValue(basis, rows, displayMs);
      if (value.kind === "value" && Number.isFinite(value.ms) && value.ms > 0) {
        values.set(queryId, value.ms);
      }
    }
    return { key: s.key, values };
  });

  const allQueryIds = new Set<string>();
  for (const s of series) {
    for (const row of s.executions ?? []) allQueryIds.add(row.query_id);
    for (const t of s.displayTimings ?? []) allQueryIds.add(t.query_id);
  }

  const shared: string[] = [];
  const excluded: string[] = [];
  for (const queryId of [...allQueryIds].sort()) {
    if (resolved.length > 0 && resolved.every((s) => s.values.has(queryId))) shared.push(queryId);
    else excluded.push(queryId);
  }

  return {
    series: resolved.map((s) => {
      const values = shared.map((q) => s.values.get(q)!);
      return { key: s.key, geomeanMs: geomeanOf(values), values };
    }),
    sharedQueryIds: shared,
    excludedQueryIds: excluded,
  };
}

export function resolveResultsForBasis(
  results: readonly DetailResult[],
  basis: MeasurementBasis,
): DetailResult[] {
  if (results.length === 0) return [];

  const seriesInputs: BasisSeriesInput[] = results.map((r) => ({
    key: r.result_id,
    executions: r.queries ?? [],
    displayTimings: r.display_timings ?? [],
  }));
  const geomeanData = sharedQueryGeomeans(basis, seriesInputs);
  const geomeanByResultId = new Map(geomeanData.series.map((s) => [s.key, s.geomeanMs]));

  return results.map((r) => {
    const displayByQuery = new Map((r.display_timings ?? []).map((t) => [t.query_id, t]));
    const execsByQuery = groupByQuery(r.queries ?? []);

    const allQueryIds = new Set<string>();
    for (const t of r.display_timings ?? []) allQueryIds.add(t.query_id);
    for (const q of r.queries ?? []) allQueryIds.add(q.query_id);

    const newDisplayTimings: QueryDisplayTiming[] = [...allQueryIds]
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
      .map((queryId) => {
        const rows = execsByQuery.get(queryId) ?? [];
        const existing = displayByQuery.get(queryId);
        const displayMs =
          existing && existing.is_valid_display_timing !== false ? (existing.display_ms ?? null) : null;
        const val = resolveQueryValue(basis, rows, displayMs);
        const ms = val.kind === "value" && Number.isFinite(val.ms) && val.ms > 0 ? val.ms : null;
        const timingExclusionReason =
          isDefaultBasis(basis) && existing?.timing_exclusion_reason
            ? existing.timing_exclusion_reason
            : val.kind === "unavailable"
              ? val.reason
              : null;
        const sampleCount = isDefaultBasis(basis)
          ? (existing?.sample_count ?? 0)
          : val.kind === "value"
            ? val.sampleCount
            : 0;
        return {
          query_id: queryId,
          display_ms: ms,
          sample_count: sampleCount,
          is_valid_display_timing: ms !== null,
          timing_exclusion_reason: timingExclusionReason,
        };
      });

    const newGeomean = geomeanByResultId.get(r.result_id) ?? null;
    const isDefault = isDefaultBasis(basis);

    const logicalCount =
      r.logical_query_count !== undefined && r.logical_query_count > 0
        ? r.logical_query_count
        : newDisplayTimings.length;
    const zeroTimingCount = isDefault
      ? (r.zero_timing_count ?? 0)
      : newDisplayTimings.filter((t) => t.timing_exclusion_reason === "zero_timing" || t.display_ms === 0).length;
    const validQueryCount = isDefault
      ? r.valid_query_count
      : newDisplayTimings.filter((t) => t.is_valid_display_timing).length;
    const missingQueryCount = isDefault
      ? r.missing_query_count
      : Math.max(logicalCount - validQueryCount - zeroTimingCount, 0);
    const hasDisplayTiming = isDefault ? r.has_display_timing : validQueryCount > 0;

    let displayExclusionReason = r.display_exclusion_reason;
    let comparisonExclusionReason = r.comparison_exclusion_reason;
    let rankingExclusionReason = r.ranking_exclusion_reason;

    if (!isDefault) {
      if (validQueryCount > 0) {
        displayExclusionReason = null;
      } else if (logicalCount <= 0) {
        displayExclusionReason = "no_queries";
      } else if (zeroTimingCount > 0 && missingQueryCount === 0) {
        displayExclusionReason = "zero_timings_only";
      } else if (missingQueryCount > 0 && zeroTimingCount === 0) {
        displayExclusionReason = "missing_timings";
      } else {
        displayExclusionReason = "no_valid_display_timing";
      }

      const independentCompareExclusions = new Set([
        "visibility_not_comparable",
        "benchmarks_differ",
        "scales_differ",
        "phases_differ",
        "hidden_result",
      ]);
      if (r.comparison_exclusion_reason && independentCompareExclusions.has(r.comparison_exclusion_reason)) {
        comparisonExclusionReason = r.comparison_exclusion_reason;
      } else if (displayExclusionReason !== null) {
        comparisonExclusionReason = displayExclusionReason;
      } else if (validQueryCount < 2) {
        comparisonExclusionReason = "insufficient_valid_queries";
      } else if (logicalCount > 0 && validQueryCount * 2 < logicalCount) {
        comparisonExclusionReason = "insufficient_query_coverage";
      } else {
        comparisonExclusionReason = null;
      }

      const independentRankingExclusions = new Set([
        "visibility_not_rankable",
        "trust_not_rankable",
        "unofficial_compliance",
        "compliance_not_rankable",
        "failed_queries",
        "validation_not_clean",
        "hidden_result",
      ]);
      if (r.ranking_exclusion_reason && independentRankingExclusions.has(r.ranking_exclusion_reason)) {
        rankingExclusionReason = r.ranking_exclusion_reason;
      } else if (comparisonExclusionReason !== null) {
        rankingExclusionReason = comparisonExclusionReason;
      } else if (newGeomean === null) {
        rankingExclusionReason = "missing_primary_metric";
      } else if (!Number.isFinite(newGeomean) || newGeomean <= 0) {
        rankingExclusionReason = "non_positive_primary_metric";
      } else {
        rankingExclusionReason = null;
      }
    }

    const validQueryIds = newDisplayTimings
      .filter((timing) => timing.is_valid_display_timing)
      .map((timing) => timing.query_id);
    const usesSharedQuerySet =
      validQueryIds.length === geomeanData.sharedQueryIds.length &&
      validQueryIds.every((queryId) => geomeanData.sharedQueryIds.includes(queryId));
    const preservedGeomean =
      isDefault && usesSharedQuerySet && r.display_geomean_ms !== null ? r.display_geomean_ms : newGeomean;

    return {
      ...r,
      display_geomean_ms: preservedGeomean,
      display_timings: newDisplayTimings,
      power_score: isDefault ? r.power_score : null,
      throughput_at_size: isDefault ? (r.throughput_at_size ?? null) : null,
      normalized_cost_usd: isDefault ? r.normalized_cost_usd : null,
      cost_status: isDefault ? r.cost_status : "unavailable",
      valid_query_count: validQueryCount,
      missing_query_count: missingQueryCount,
      zero_timing_count: zeroTimingCount,
      has_display_timing: hasDisplayTiming,
      display_exclusion_reason: displayExclusionReason,
      comparison_exclusion_reason: comparisonExclusionReason,
      ranking_exclusion_reason: rankingExclusionReason,
    };
  });
}

export function resolvedStatisticsCollapsed(results: readonly DetailResult[]): boolean {
  return !results.some((result) =>
    result.display_timings.some((timing) => timing.sample_count > 1),
  );
}

function geomeanOf(values: readonly number[]): number | null {
  return geomeanMs([...values]);
}

export function parseAvailablePassSelections(raw: string | null | undefined): PassSelection[] {
  const out: PassSelection[] = [];
  for (const basis of parseAvailableBases(raw)) {
    if (!out.some((p) => passSelectionsEqual(p, basis.passes))) out.push(basis.passes);
  }
  return out;
}

export function parseAvailableBases(raw: string | null | undefined): MeasurementBasis[] {
  if (!raw) return [];
  const out: MeasurementBasis[] = [];
  for (const token of raw.split(",")) {
    const trimmed = token.trim();
    if (trimmed === "") continue;
    const basis = decodeBasis(trimmed);
    if (basis && !out.some((b) => basesEqual(b, basis))) out.push(basis);
  }
  return out;
}

export function parseVaryingPassQueries(raw: string | null | undefined): Map<string, number> {
  if (!raw) return new Map();
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return new Map();
    const out = new Map<string, number>();
    for (const [queryId, count] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof count === "number" && Number.isFinite(count)) out.set(queryId, count);
    }
    return out;
  } catch {
    return new Map();
  }
}

export function formatBasisLabel(basis: MeasurementBasis): string {
  switch (basis.passes.kind) {
    case "all_warm":
      return basis.statistic === "median" ? "published median" : "fastest warm pass";
    case "warmup":
      return basis.statistic === "median" ? "warmup pass" : "warmup pass (min)";
    case "warm_pass":
      return basis.statistic === "median"
        ? `warm pass ${basis.passes.pass}`
        : `warm pass ${basis.passes.pass} (min)`;
  }
}
