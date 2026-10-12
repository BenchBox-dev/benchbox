import type { QueryExecutionRow } from "@/lib/duckdbQueries";
import { planCompareIds } from "@/lib/compareRecovery";
import {
  DEFAULT_BASIS,
  decodeBasis,
  encodeBasis,
  isDefaultBasis,
  type MeasurementBasis,
} from "@/lib/measurementBasis";
import { MAX_COMPARE_SELECTIONS } from "@/lib/resultLinks";

export const MAX_QUERY_COMPARISON_RUNS = MAX_COMPARE_SELECTIONS;

export interface QueryRunSample {
  durationMs: number;
  iteration: number | null;
  stream: number | null;
  legacy: boolean;
}

export interface QueryRunExecutionSummary {
  samples: QueryRunSample[];
  medianMs: number | null;
  minMs: number | null;
  maxMs: number | null;
  selectedValueMs: number | null;
  sampleCount: number;
  iterationCount: number;
  measurementRowCount: number;
  legacyRowCount: number;
  measurementFailureCount: number;
  invalidMeasurementTimingCount: number;
  warmupPassCount: number;
  warmupFailureCount: number;
  ignoredLegacyCount: number;
  otherRunTypeCount: number;
  legacyFallback: boolean;
  queryRecordCount: number;
}

export interface QueryRunSelection {
  queryId: string;
  requestedIds: string[];
  duplicateIds: string[];
  overflowIds: string[];
  basis: MeasurementBasis;
  basisNotice: string | null;
}

export function parseQueryRunSelection(url: string, queryId: string): QueryRunSelection {
  const params = new URL(url, "https://benchbox.dev").searchParams;
  const rawIds = params.get("ids") ?? "";
  const plan = planCompareIds(rawIds.split(","), MAX_QUERY_COMPARISON_RUNS);
  const rawBasis = params.get("basis");
  const decodedBasis = rawBasis === null ? DEFAULT_BASIS : decodeBasis(rawBasis);
  const basisUsesWarmup = decodedBasis?.passes.kind === "warmup";
  const basis = decodedBasis && !basisUsesWarmup ? decodedBasis : DEFAULT_BASIS;

  return {
    queryId: queryId.trim(),
    requestedIds: plan.retained,
    duplicateIds: plan.duplicates,
    overflowIds: plan.overflow,
    basis,
    basisNotice:
      rawBasis !== null && decodedBasis === null
        ? "The requested measurement basis was not recognized; showing all measurement iterations."
        : basisUsesWarmup
          ? "Warmup executions are listed separately, so this view uses measurement iterations."
          : null,
  };
}

export function queryRunComparisonHref(
  queryId: string,
  resultIds: readonly string[],
  basis: MeasurementBasis = DEFAULT_BASIS,
): string {
  const params = new URLSearchParams();
  params.set("ids", resultIds.join(","));
  if (!isDefaultBasis(basis)) params.set("basis", encodeBasis(basis));
  return `/results/compare/query/${encodeURIComponent(queryId)}?${params.toString()}`;
}

function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[middle] ?? null;
  const low = sorted[middle - 1];
  const high = sorted[middle];
  return low === undefined || high === undefined ? null : (low + high) / 2;
}

export function summarizeQueryRunExecutions(
  rows: readonly QueryExecutionRow[],
  queryId: string,
  basis: MeasurementBasis = DEFAULT_BASIS,
): QueryRunExecutionSummary {
  const queryRows = rows.filter((row) => row.query_id === queryId);
  const measurementRows = queryRows.filter((row) => row.run_type === "measurement");
  const legacyRows = queryRows.filter((row) => row.run_type === null);
  const warmupRows = queryRows.filter((row) => row.run_type === "warmup");
  const legacyFallback = measurementRows.length === 0 && legacyRows.length > 0;
  const selectedRows = measurementRows.length > 0 ? measurementRows : legacyRows;
  const selectedIteration = basis.passes.kind === "warm_pass" ? basis.passes.pass : null;
  const measurementFailureCount = selectedRows.filter((row) => row.status !== "pass").length;
  const passingRows = selectedRows.filter((row) => row.status === "pass");
  const samples = passingRows.flatMap((row): QueryRunSample[] => {
    if (!Number.isFinite(row.duration_ms) || row.duration_ms <= 0) return [];
    return [{
      durationMs: row.duration_ms,
      iteration: row.iter !== null && Number.isInteger(row.iter) && row.iter > 0 ? row.iter : null,
      stream: row.stream,
      legacy: legacyFallback,
    }];
  });
  const values = samples.map((sample) => sample.durationMs);
  const medianMs = median(values);
  const minMs = values.length > 0 ? Math.min(...values) : null;
  const maxMs = values.length > 0 ? Math.max(...values) : null;
  const basisSamples = selectedIteration === null
    ? samples
    : samples.filter((sample) => sample.iteration === selectedIteration);
  const basisValues = basisSamples.map((sample) => sample.durationMs);
  const selectedValueMs = basis.statistic === "min"
    ? (basisValues.length > 0 ? Math.min(...basisValues) : null)
    : median(basisValues);
  const iterationCount = new Set(samples.flatMap((sample) => sample.iteration === null ? [] : [sample.iteration])).size;

  return {
    samples,
    medianMs,
    minMs,
    maxMs,
    selectedValueMs,
    sampleCount: samples.length,
    iterationCount,
    measurementRowCount: measurementRows.length,
    legacyRowCount: legacyRows.length,
    measurementFailureCount,
    invalidMeasurementTimingCount: passingRows.length - samples.length,
    warmupPassCount: warmupRows.filter((row) => row.status === "pass").length,
    warmupFailureCount: warmupRows.filter((row) => row.status !== "pass").length,
    ignoredLegacyCount: measurementRows.length > 0 ? legacyRows.length : 0,
    otherRunTypeCount: queryRows.filter(
      (row) => row.run_type !== null && row.run_type !== "measurement" && row.run_type !== "warmup",
    ).length,
    legacyFallback,
    queryRecordCount: queryRows.length,
  };
}
