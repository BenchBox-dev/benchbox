import { useRef } from "preact/hooks";
import type { DetailResult } from "@/types";
import { StatusBadge } from "@/components/StatusBadge";
import { TableScrollHint } from "@/components/TableScrollHint";
import { buildComparabilityFields, type ComparabilityField } from "@/components/ComparabilityReceipt";
import { formatCpuIdentityProvenance } from "@/lib/hardwareProvenance";
import { formatArchitecture, formatCpuFamily, formatMemoryGb } from "@/lib/displayLabels";

export interface IdentityDiffStripProps {
  results: DetailResult[];
  baselineIndex?: number;
  runLabels?: readonly string[];
}

export const STRIP_AXES = [
  "Platform version",
  "Driver version",
  "Architecture",
  "CPU family",
  "CPU model",
  "CPU evidence",
  "Memory",
] as const;

export const AXIS_DISPLAY_LABEL: Record<(typeof STRIP_AXES)[number], string> = {
  "Platform version": "Platform version",
  "Driver version": "Driver",
  Architecture: "Architecture",
  "CPU family": "CPU family",
  "CPU model": "CPU model",
  "CPU evidence": "CPU evidence",
  Memory: "Memory",
};

export function axisValueForRun(axis: (typeof STRIP_AXES)[number], result: DetailResult): string {
  switch (axis) {
    case "Platform version":
      return result.platform_version && result.platform_version !== "" ? result.platform_version : "Not recorded";
    case "Driver version":
      return result.driver_version && result.driver_version !== "" ? result.driver_version : "Not recorded";
    case "Architecture":
      return result.environment?.arch && result.environment.arch !== "" ? formatArchitecture(result.environment.arch) : "Not recorded";
    case "CPU family":
      return result.environment?.cpu_family && result.environment.cpu_family !== "" ? formatCpuFamily(result.environment.cpu_family) : "Not recorded";
    case "CPU model":
      return result.environment?.cpu_model && result.environment.cpu_model !== "" ? result.environment.cpu_model : "Not recorded";
    case "CPU evidence":
      return formatCpuIdentityProvenance(result.environment?.cpu_identity_provenance);
    case "Memory":
      return result.environment?.memory_gb !== undefined ? formatMemoryGb(result.environment.memory_gb) : "Not recorded";
  }
}

export function identityStripFields(results: DetailResult[]): ComparabilityField[] {
  if (results.length === 0) return [];
  const byLabel = new Map(buildComparabilityFields(results).map((field) => [field.label, field]));
  return STRIP_AXES.map((label) => byLabel.get(label)).filter(
    (field): field is ComparabilityField => field !== undefined,
  );
}

export function identityStripDiffCount(results: DetailResult[]): number {
  return identityStripFields(results).filter((field) => field.status === "diff").length;
}

type MatrixStatus = "baseline" | "missing" | "indeterminate" | "diff" | "match";

interface MatrixCell {
  value: string;
  status: MatrixStatus;
}

function matrixCell(axis: (typeof STRIP_AXES)[number], run: DetailResult, index: number, baseIndex: number, baselineRun: DetailResult | undefined): MatrixCell {
  const value = axisValueForRun(axis, run);
  if (index === baseIndex) return { value, status: "baseline" };
  const baselineValue = baselineRun ? axisValueForRun(axis, baselineRun) : "Not recorded";
  if (value === "Not recorded") return { value, status: "missing" };
  if (baselineValue === "Not recorded") return { value, status: "indeterminate" };
  return { value, status: value !== baselineValue ? "diff" : "match" };
}

function matrixBadge(status: MatrixStatus, includeSame: boolean): { text: string; tone: "warning" | "success" | "neutral" } | null {
  if (status === "indeterminate") return { text: "No baseline", tone: "neutral" };
  if (status === "match" && includeSame) return { text: "Same", tone: "success" };
  return null;
}

function DifferenceMarker({ axis, runNames }: { axis: string; runNames: readonly string[] }) {
  if (runNames.length === 0) return null;
  return (
    <span class="inline-flex flex-wrap items-center gap-1" data-testid={`differs-marker-${axis}`}>
      <StatusBadge role="comparison" tone="warning">
        Differs
      </StatusBadge>
      <span class="sr-only">{`in ${runNames.join(", ")}`}</span>
    </span>
  );
}

const TRANSPOSE_ABOVE_RUNS = 4;

function toneFor(status: ComparabilityField["status"]): "success" | "warning" | "neutral" {
  if (status === "diff") return "warning";
  if (status === "missing") return "neutral";
  return "success";
}

function statusWord(status: ComparabilityField["status"]): string {
  if (status === "diff") return "Differs";
  if (status === "missing") return "Not recorded";
  return "Same";
}

export function IdentityDiffStrip({ results, baselineIndex = 0, runLabels }: IdentityDiffStripProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  if (results.length < 2) return null;
  const fields = identityStripFields(results);
  if (fields.length === 0) return null;
  const diffCount = fields.filter((f) => f.status === "diff").length;

  if (results.length > 2) {
    const baseIndex = results[baselineIndex] ? baselineIndex : 0;
    const baselineRun = results[baseIndex];
    const transposed = results.length > TRANSPOSE_ABOVE_RUNS;
    const summaryText = diffCount === 0
      ? `No differences were recorded for these ${fields.length} fields. Other recorded or unrecorded factors may differ.`
      : `${diffCount} of ${fields.length} ${diffCount === 1 ? "axis varies" : "axes vary"} across the whole selection.`;
    const runName = (index: number, run: DetailResult) =>
      `${runLabels?.[index] ?? run.platform}${index === baseIndex ? " (baseline)" : ""}`;
    const differingRunNames = (axis: (typeof STRIP_AXES)[number]) =>
      results.flatMap((run, index) =>
        matrixCell(axis, run, index, baseIndex, baselineRun).status === "diff" ? [runLabels?.[index] ?? run.platform] : [],
      );

    return (
      <section class="panel mb-4 px-3 py-2 shadow-sm" aria-label="Platform and hardware identity">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-sm font-medium text-[var(--bb-data-fg-primary)]">Platform and hardware</h2>
          <p class="text-xs text-[var(--bb-data-fg-muted)]">{summaryText}</p>
        </div>
        {transposed ? (
          <>
            <div ref={scrollerRef} class="mt-2 overflow-x-auto" data-testid="identity-matrix-scroller">
              <table class="w-max min-w-full divide-y divide-[var(--bb-data-border)] text-sm">
                <thead class="bg-[var(--bb-surface-data-muted)]">
                  <tr>
                    <th scope="col" class="table-th sticky left-0 z-10 whitespace-nowrap bg-[var(--bb-surface-data-muted)]">
                      Run
                    </th>
                    {fields.map((field) => {
                      const axis = field.label as (typeof STRIP_AXES)[number];
                      return (
                        <th key={axis} scope="col" class="table-th whitespace-nowrap">
                          <span class="inline-flex flex-wrap items-center gap-1">
                            {AXIS_DISPLAY_LABEL[axis] ?? field.label}
                            <DifferenceMarker axis={axis} runNames={differingRunNames(axis)} />
                          </span>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
                  {results.map((run, index) => (
                    <tr key={run.result_id}>
                      <th
                        scope="row"
                        class="table-td sticky left-0 z-10 whitespace-nowrap bg-[var(--bb-surface-data)] text-left font-medium"
                      >
                        {runName(index, run)}
                      </th>
                      {fields.map((field) => {
                        const axis = field.label as (typeof STRIP_AXES)[number];
                        const cell = matrixCell(axis, run, index, baseIndex, baselineRun);
                        const badge = matrixBadge(cell.status, false);
                        return (
                          <td
                            key={axis}
                            class={`table-td align-top text-xs ${cell.status === "diff" ? "bg-[var(--bb-surface-data-muted)]" : ""}`}
                            data-testid={`matrix-run-${index}-${axis}`}
                            data-differs={cell.status === "diff" ? "true" : undefined}
                          >
                            <span class="block break-words font-mono text-[var(--bb-data-fg-primary)]">{cell.value}</span>
                            {badge && (
                              <StatusBadge role="comparison" tone={badge.tone} class="mt-1">
                                {badge.text}
                              </StatusBadge>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <TableScrollHint scrollerRef={scrollerRef} testId="identity-matrix-scroll-hint" />
          </>
        ) : (
          <div ref={scrollerRef} class="mt-2 overflow-x-auto" data-testid="identity-matrix-scroller">
            <table role="table" class="min-w-full w-max divide-y divide-[var(--bb-data-border)] text-sm">
              <thead class="bg-[var(--bb-surface-data-muted)]">
                <tr>
                  <th class="table-th">Axis</th>
                  {results.map((r, i) => (
                    <th key={r.result_id} class="table-th whitespace-nowrap">
                      {runName(i, r)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
                {fields.map((field) => {
                  const axis = field.label as (typeof STRIP_AXES)[number];
                  return (
                    <tr key={field.label}>
                      <td class="table-td font-medium text-[var(--bb-data-fg-primary)]">
                        <span class="inline-flex flex-wrap items-center gap-1">
                          {AXIS_DISPLAY_LABEL[axis] ?? field.label}
                          <DifferenceMarker axis={axis} runNames={differingRunNames(axis)} />
                        </span>
                      </td>
                      {results.map((r, i) => {
                        const cell = matrixCell(axis, r, i, baseIndex, baselineRun);
                        const badge = matrixBadge(cell.status, true);
                        return (
                          <td
                            key={r.result_id}
                            class={`table-td text-xs ${cell.status === "diff" ? "bg-[var(--bb-surface-data-muted)]" : ""}`}
                            data-testid={`matrix-cell-${axis}-${i}`}
                            data-differs={cell.status === "diff" ? "true" : undefined}
                          >
                            <div class="flex items-center justify-between gap-2">
                              <span class="break-words font-mono text-[var(--bb-data-fg-primary)]">{cell.value}</span>
                              {badge && (
                                <StatusBadge role="comparison" tone={badge.tone}>
                                  {badge.text}
                                </StatusBadge>
                              )}
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    );
  }

  return (
    <section class="panel mb-4 px-3 py-2 shadow-sm" aria-label="Platform and hardware identity">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-sm font-medium text-[var(--bb-data-fg-primary)]">Platform and hardware</h2>
        <p class="text-xs text-[var(--bb-data-fg-muted)]">
          {diffCount === 0
            ? `No differences were recorded for these ${fields.length} fields. Other recorded or unrecorded factors may differ.`
            : `${diffCount} of ${fields.length} ${diffCount === 1 ? "axis differs" : "axes differ"} between these runs.`}
        </p>
      </div>
      <ul class="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {fields.map((field) => (
          <li key={field.label} class="flex items-start justify-between gap-2 rounded-md border border-[var(--bb-data-border-strong)] px-2 py-1.5">
            <div class="min-w-0">
              <p class="text-xs font-medium text-[var(--bb-data-fg-primary)]">
                {AXIS_DISPLAY_LABEL[field.label as (typeof STRIP_AXES)[number]] ?? field.label}
              </p>
              <p class="text-xs text-[var(--bb-data-fg-muted)] break-words">{field.summary}</p>
            </div>
            <StatusBadge role="comparison" tone={toneFor(field.status)}>
              {statusWord(field.status)}
            </StatusBadge>
          </li>
        ))}
      </ul>
    </section>
  );
}
