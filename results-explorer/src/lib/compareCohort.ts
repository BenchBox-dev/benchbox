import { formatScaleFactor } from "@/lib/metricFormatters";
import { canonicalBenchmarkSlug, canonicalPhase, formatBenchmarkLabel } from "@/lib/displayLabels";
import { formatRunDateWithAge } from "@/lib/runAge";
import {
  basesEqual,
  formatBasisLabel,
  parseAvailablePassSelections,
  passSelectionsEqual,
  type MeasurementBasis,
} from "@/lib/measurementBasis";

function compareSelectionShortId(id: string): string {
  if (id.length === 0) return "";
  const trailing = id.split("-").pop()!;
  return trailing.length > 0 ? trailing : id;
}

export interface CompareCohortSignature {
  benchmark: string;
  scaleFactor: string;
  phase: string;
  streamCount: string;
  primaryMetric: string | null;
  basis: MeasurementBasis | null;
}

export type CompareCohortField =
  | "benchmark"
  | "scale"
  | "phase"
  | "streams"
  | "primary metric"
  | "measurement basis";

export interface CompareCohortRow {
  benchmark?: unknown;
  scale_factor?: unknown;
  phase?: unknown;
  test_type?: unknown;
  stream_count?: unknown;
  primary_metric?: unknown;
  available_bases?: unknown;
}

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

export function compareCohortSignatureForRow(
  row: CompareCohortRow,
  basis: MeasurementBasis | null = null,
): CompareCohortSignature {
  return {
    benchmark: canonicalBenchmarkSlug(asText(row.benchmark)),
    scaleFactor: asText(row.scale_factor),
    phase: canonicalPhase(asText(row.phase ?? row.test_type)),
    streamCount: asText(row.stream_count),
    primaryMetric: asText(row.primary_metric) || null,
    basis,
  };
}

export function rowAnswersBasis(row: CompareCohortRow, basis: MeasurementBasis | null): boolean {
  if (basis === null) return true;
  if (row.available_bases === undefined || row.available_bases === null) return true;
  const available = parseAvailablePassSelections(asText(row.available_bases));
  if (available.length === 0) return true;
  return available.some((candidate) => passSelectionsEqual(candidate, basis.passes));
}

export function compareCohortMismatches(
  row: CompareCohortRow,
  signature: CompareCohortSignature,
): CompareCohortField[] {
  const candidate = compareCohortSignatureForRow(row);
  const mismatches: CompareCohortField[] = [];
  if (candidate.benchmark !== signature.benchmark) mismatches.push("benchmark");
  if (candidate.scaleFactor !== signature.scaleFactor) mismatches.push("scale");
  if (candidate.phase !== signature.phase) mismatches.push("phase");
  if (candidate.streamCount !== signature.streamCount) mismatches.push("streams");
  if (
    signature.primaryMetric !== null &&
    candidate.primaryMetric !== null &&
    candidate.primaryMetric !== signature.primaryMetric
  ) {
    mismatches.push("primary metric");
  }
  if (!rowAnswersBasis(row, signature.basis)) mismatches.push("measurement basis");
  return mismatches;
}

export function compareCohortSummary(signature: CompareCohortSignature): string {
  const parts = [formatBenchmarkLabel(signature.benchmark)];
  if (signature.scaleFactor !== "") parts.push(formatScaleFactor(signature.scaleFactor));
  if (signature.phase !== "") parts.push(signature.phase);
  if (signature.streamCount !== "") parts.push(`${signature.streamCount} streams`);
  if (signature.basis !== null) parts.push(`at ${formatBasisLabel(signature.basis)}`);
  return parts.join(" ");
}

export function lockCrossRunBasis(
  bases: readonly MeasurementBasis[],
): { ok: true; basis: MeasurementBasis | null } | { ok: false; reason: string } {
  const distinct: MeasurementBasis[] = [];
  for (const basis of bases) {
    if (!distinct.some((b) => basesEqual(b, basis))) distinct.push(basis);
  }
  if (distinct.length === 0) return { ok: true, basis: null };
  if (distinct.length === 1) return { ok: true, basis: distinct[0]! };
  return {
    ok: false,
    reason:
      "A comparison across runs reads every run through one measurement basis. " +
      `This selection carries ${distinct.length}: ` +
      `${distinct.map(formatBasisLabel).join(", ")}. ` +
      "Comparing one run's basis against another's measures the basis, not the platform.",
  };
}

export function compareCohortLockReason(
  row: CompareCohortRow,
  signature: CompareCohortSignature | null,
): string | undefined {
  if (signature === null) return undefined;
  const mismatches = compareCohortMismatches(row, signature);
  if (mismatches.length === 0) return undefined;
  return (
    `Locked: first selection is ${compareCohortSummary(signature)}. ` +
    `This row differs by ${mismatches.join(", ")}.`
  );
}

export function compareCohortPartition<T extends CompareCohortRow>(
  rows: readonly T[],
  signature: CompareCohortSignature | null,
): { compatible: T[]; incompatible: T[] } {
  if (signature === null) return { compatible: [...rows], incompatible: [] };
  const compatible: T[] = [];
  const incompatible: T[] = [];
  for (const row of rows) {
    if (compareCohortMismatches(row, signature).length === 0) compatible.push(row);
    else incompatible.push(row);
  }
  return { compatible, incompatible };
}

export interface CompareSelectionLabelInput {
  platform?: string | null;
  benchmark?: string | null;
  scaleFactor?: string | number | null;
  phase?: string | null;
  runDate?: string | null;
  resultId?: string | null;
}

export function hiddenIncompatibleSuffix(count: number): string {
  if (count <= 0) return "";
  return ` ${count} incompatible row${count === 1 ? "" : "s"} hidden.`;
}

export function compareSelectionLabel(input: CompareSelectionLabelInput): string {
  const parts: string[] = [];
  const platform = (input.platform ?? "").toString().trim();
  const benchmark = (input.benchmark ?? "").toString().trim();
  const scale =
    input.scaleFactor === null || input.scaleFactor === undefined ? "" : String(input.scaleFactor).trim();
  const phase = (input.phase ?? "").toString().trim();
  const rawRunDate = (input.runDate ?? "").toString();
  const runDate = rawRunDate === "" ? "" : formatRunDateWithAge(rawRunDate);
  const resultId = (input.resultId ?? "").toString().trim();

  if (platform !== "") parts.push(platform);
  if (benchmark !== "") parts.push(formatBenchmarkLabel(benchmark));
  if (scale !== "") parts.push(formatScaleFactor(scale));
  if (phase !== "") parts.push(phase);
  if (runDate !== "") parts.push(runDate);
  if (resultId !== "") parts.push(`(${compareSelectionShortId(resultId)})`);

  if (parts.length === 0) return "Select run for comparison";
  return `Select ${parts.join(" ")} for comparison`;
}
