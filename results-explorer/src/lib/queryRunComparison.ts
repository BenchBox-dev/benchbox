import type { QueryExecutionRow } from "@/lib/duckdbQueries";
import { compareCohortSignatureForRow, type CompareCohortRow } from "@/lib/compareCohort";
import { planCompareIds } from "@/lib/compareRecovery";
import { canonicalBenchmarkSlug, formatBenchmarkLabel } from "@/lib/displayLabels";
import { formatTickMs, logTicks } from "@/lib/chartTicks";
import {
  DEFAULT_BASIS,
  decodeBasis,
  encodeBasis,
  isDefaultBasis,
  type MeasurementBasis,
} from "@/lib/measurementBasis";
import { MAX_COMPARE_SELECTIONS } from "@/lib/resultLinks";

export const MAX_QUERY_COMPARISON_RUNS = MAX_COMPARE_SELECTIONS;

export const LOG_AXIS_SPREAD_RATIO = 10;

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
  basisActive: boolean;
  displaySamples: QueryRunSample[];
  displayValueMs: number | null;
  displayMinMs: number | null;
  displayMaxMs: number | null;
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

  const basisActive = !isDefaultBasis(basis);
  const displaySamples = basisActive ? basisSamples : samples;
  const displayValues = displaySamples.map((sample) => sample.durationMs);
  const displayValueMs = basisActive ? selectedValueMs : medianMs;
  const displayMinMs = displayValues.length > 0 ? Math.min(...displayValues) : null;
  const displayMaxMs = displayValues.length > 0 ? Math.max(...displayValues) : null;

  return {
    samples,
    medianMs,
    minMs,
    maxMs,
    selectedValueMs,
    basisActive,
    displaySamples,
    displayValueMs,
    displayMinMs,
    displayMaxMs,
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

export interface ValueAxisTick {
  value: number;
  label: string;
}

export interface ValueAxis {
  scale: "log" | "linear";
  min: number;
  max: number;
  ticks: ValueAxisTick[];
}

const NICE_MANTISSAS = [1, 2, 5] as const;
const MAX_LINEAR_INTERVALS = 6;
const LINEAR_HEADROOM = 1.05;

function withoutRepeatedLabels(values: readonly number[]): ValueAxisTick[] {
  const ticks: ValueAxisTick[] = [];
  for (const value of values) {
    const label = formatTickMs(value);
    if (ticks[ticks.length - 1]?.label === label) continue;
    ticks.push({ value, label });
  }
  return ticks;
}

function linearStep(span: number): number {
  const base = Math.floor(Math.log10(span));
  const candidates = [-1, 0, 1, 2].flatMap((offset) =>
    NICE_MANTISSAS.map((mantissa) => Number((mantissa * 10 ** (base + offset)).toPrecision(12))),
  );
  return candidates.find((step) => span / step <= MAX_LINEAR_INTERVALS) ?? span;
}

export function buildValueAxis(values: readonly number[], plotLengthPx: number): ValueAxis {
  const positive = values.filter((value) => Number.isFinite(value) && value > 0);
  if (positive.length === 0) return { scale: "linear", min: 0, max: 1, ticks: [] };
  const low = Math.min(...positive);
  const high = Math.max(...positive);

  if (high / low > LOG_AXIS_SPREAD_RATIO) {
    const min = low / 1.15;
    const max = high * 1.15;
    return { scale: "log", min, max, ticks: withoutRepeatedLabels(logTicks(min, max, plotLengthPx)) };
  }

  const headroomMax = high * LINEAR_HEADROOM;
  const step = linearStep(headroomMax);
  const max = Math.ceil(headroomMax / step) * step;
  const ticks: number[] = [];
  for (let index = 0; index * step <= max * (1 + 1e-9); index += 1) {
    ticks.push(Number((index * step).toPrecision(12)));
  }
  return { scale: "linear", min: 0, max, ticks: withoutRepeatedLabels(ticks) };
}

export function valueAxisFraction(axis: ValueAxis, value: number): number {
  if (axis.scale === "log") {
    return (Math.log10(value) - Math.log10(axis.min)) / (Math.log10(axis.max) - Math.log10(axis.min));
  }
  return (value - axis.min) / (axis.max - axis.min);
}

export function runScopeDifferences(rows: readonly CompareCohortRow[]): string[] {
  const signatures = rows.map((row) => compareCohortSignatureForRow(row));
  const dimensions: Array<[string, string[]]> = [
    ["benchmark", signatures.map((signature) => (signature.benchmark === "" ? "" : formatBenchmarkLabel(signature.benchmark)))],
    ["scale factor", signatures.map((signature) => (signature.scaleFactor === "" ? "" : `SF ${signature.scaleFactor}`))],
    ["phase", signatures.map((signature) => signature.phase)],
    ["stream count", signatures.map((signature) => (signature.streamCount === "" ? "" : `${signature.streamCount} streams`))],
  ];
  return dimensions.flatMap(([name, values]) => {
    const distinct = [...new Set(values)];
    if (distinct.length < 2) return [];
    return [`${name} (${distinct.map((value) => value || "not recorded").join(", ")})`];
  });
}

export function benchmarkDisplayLabel(benchmarks: readonly (string | null | undefined)[]): string | null {
  const slugs = [...new Set(benchmarks.map((benchmark) => canonicalBenchmarkSlug(benchmark ?? "")).filter((slug) => slug !== ""))];
  return slugs.length === 1 ? formatBenchmarkLabel(slugs[0]!) : null;
}

export function queryDisplayTitle(queryId: string, benchmarkLabel: string | null): string {
  const id = queryId.trim();
  if (/^\d+$/.test(id)) return benchmarkLabel === null ? `Q${id}` : `${benchmarkLabel} Q${id}`;
  return benchmarkLabel === null ? id : `${benchmarkLabel} · ${id}`;
}

export function addRunsHref(resultIds: readonly string[]): string {
  const params = new URLSearchParams();
  if (resultIds.length > 0) params.set("pick", resultIds[0]!);
  params.set("ids", resultIds.join(","));
  return `/results/query?${params.toString()}`;
}
