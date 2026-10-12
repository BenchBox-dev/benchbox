import type { DetailResult } from "@/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  DEFAULT_QUERY_DIFF_LIMIT,
  type QueryDiffLimiter,
  QUERY_DIFF_LIMITER_LABELS,
} from "@/components/QueryDiffTable";
import {
  DIVERGING_RATIO_CLAMP,
  divergingRatioPosition,
  queryDisagreementSpread,
} from "@/lib/chartMath";
import { formatTimingExclusion, timingValueForQuery } from "@/lib/displayEligibility";

export interface MultiRunHeatmapProps {
  results: DetailResult[];
  baselineIndex: number;
  runLabels: readonly string[];
  limit?: number;
  limiter?: QueryDiffLimiter;
  orderByDisagreement?: boolean;
  queryFilter?: readonly string[];
  queryHref?: (queryId: string) => string;
}

export interface HeatmapCell {
  ratio: number | null;
  position: number | null;
  timingMs: number | null;
  missingKind: "none" | "run_missing" | "baseline_missing";
  evidenceKind: "available" | "failed" | "excluded" | "unrecorded";
  unavailableReason: string | null;
  baselineEvidenceKind: "failed" | "excluded" | "unrecorded" | null;
  baselineUnavailableReason: string | null;
}

export interface HeatmapRow {
  queryId: string;
  cells: HeatmapCell[];
  disagreement: number | null;
}

function unavailableEvidence(
  result: DetailResult,
  queryId: string,
): Pick<HeatmapCell, "evidenceKind" | "unavailableReason"> {
  const timing = result.display_timings.find((row) => row.query_id === queryId);
  const executions = result.queries.filter((row) => row.query_id === queryId);
  const measurement = executions.filter((row) => row.run_type === "measurement");
  const basisExecutions = measurement.length > 0
    ? measurement
    : executions.filter((row) => row.run_type === null);
  const allBasisExecutionsFailed = basisExecutions.length > 0
    && basisExecutions.every((row) => row.status !== "pass");
  const reason = timing?.timing_exclusion_reason ?? null;
  const basisWasNotRecorded = reason === "pass_not_recorded"
    || reason === "no_warmup_recorded"
    || reason === "no_warm_passes_recorded"
    || (reason === "missing_timing" && basisExecutions.length === 0);
  if (basisWasNotRecorded || (!timing && basisExecutions.length === 0)) {
    return { evidenceKind: "unrecorded", unavailableReason: null };
  }
  return {
    evidenceKind: allBasisExecutionsFailed || reason?.includes("fail") || reason === "no_passing_executions"
      ? "failed"
      : "excluded",
    unavailableReason: reason,
  };
}

export function buildHeatmapRows(
  results: readonly DetailResult[],
  baselineIndex: number,
): HeatmapRow[] {
  const queryIds = new Set<string>();
  for (const r of results) for (const t of r.display_timings) queryIds.add(t.query_id);
  const baseline = results[baselineIndex];

  return [...queryIds]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
    .map((queryId) => {
      const baseMs = baseline ? timingValueForQuery(baseline, queryId) : null;
      const baselineEvidence = baseline && baseMs === null
        ? unavailableEvidence(baseline, queryId)
        : { evidenceKind: "available" as const, unavailableReason: null };
      const cells: HeatmapCell[] = results.map((r) => {
        const ms = timingValueForQuery(r, queryId);
        let missingKind: "none" | "run_missing" | "baseline_missing" = "none";
        if (ms === null || ms <= 0) {
          missingKind = "run_missing";
        } else if (baseMs === null || baseMs <= 0) {
          missingKind = "baseline_missing";
        }
        const ratio = ms !== null && baseMs !== null && baseMs > 0 ? ms / baseMs : null;
        const evidence = ms === null
          ? unavailableEvidence(r, queryId)
          : { evidenceKind: "available" as const, unavailableReason: null };
        return {
          ratio,
          position: divergingRatioPosition(ratio),
          timingMs: ms,
          missingKind,
          ...evidence,
          baselineEvidenceKind: missingKind === "baseline_missing"
            ? baselineEvidence.evidenceKind as "failed" | "excluded" | "unrecorded"
            : null,
          baselineUnavailableReason: missingKind === "baseline_missing"
            ? baselineEvidence.unavailableReason
            : null,
        };
      });
      return {
        queryId,
        cells,
        disagreement: queryDisagreementSpread(cells.map((c) => c.ratio)),
      };
    });
}

export function orderRowsByDisagreement(rows: readonly HeatmapRow[]): HeatmapRow[] {
  return [...rows].sort((a, b) => {
    if (a.disagreement === null && b.disagreement === null) return 0;
    if (a.disagreement === null) return 1;
    if (b.disagreement === null) return -1;
    return b.disagreement - a.disagreement;
  });
}

export function filterHeatmapRows(
  rows: readonly HeatmapRow[],
  limiter: QueryDiffLimiter = "all",
  baselineIndex: number = 0,
): HeatmapRow[] {
  if (limiter === "all") return [...rows];
  if (limiter === "movement") return orderRowsByDisagreement(rows);

  if (limiter === "speedups") {
    const speedupRows = rows.filter((r) =>
      r.cells.some((c, i) => i !== baselineIndex && c.ratio !== null && c.ratio < 1),
    );
    return speedupRows.sort((a, b) => {
      const minA = Math.min(...a.cells.map((c, i) => (i !== baselineIndex && c.ratio !== null ? c.ratio : 1)));
      const minB = Math.min(...b.cells.map((c, i) => (i !== baselineIndex && c.ratio !== null ? c.ratio : 1)));
      return minA - minB;
    });
  }

  if (limiter === "slowdowns") {
    const slowdownRows = rows.filter((r) =>
      r.cells.some((c, i) => i !== baselineIndex && c.ratio !== null && c.ratio > 1),
    );
    return slowdownRows.sort((a, b) => {
      const maxA = Math.max(...a.cells.map((c, i) => (i !== baselineIndex && c.ratio !== null ? c.ratio : 1)));
      const maxB = Math.max(...b.cells.map((c, i) => (i !== baselineIndex && c.ratio !== null ? c.ratio : 1)));
      return maxB - maxA;
    });
  }

  return [...rows];
}

export function heatmapCellStyle(position: number | null): string {
  if (position === null) return "";
  const hue = position < 0 ? 150 : 15;
  const lightness = 95 - Math.abs(position) * 35;
  const darkLightness = 22 + Math.abs(position) * 18;
  return `--cell-hue:${hue}; --cell-lightness:${lightness.toFixed(1)}%; --cell-dark-lightness:${darkLightness.toFixed(1)}%;`;
}

function cellText(cell: HeatmapCell): string {
  if (cell.ratio === null) return "—";
  return `${cell.ratio.toFixed(2)}x`;
}

function unavailableCellDescription(cell: HeatmapCell): string | undefined {
  if (cell.missingKind === "baseline_missing") {
    const baselineState = cell.baselineEvidenceKind === "unrecorded"
      ? "was not recorded"
      : cell.baselineEvidenceKind === "failed"
        ? "failed"
        : "was excluded";
    const reason = cell.baselineUnavailableReason
      ? ` — ${formatTimingExclusion(cell.baselineUnavailableReason)}`
      : "";
    return `Baseline ${baselineState} for this query; recorded candidate timing: ${cell.timingMs?.toFixed(1)} ms${reason}`;
  }
  if (cell.missingKind !== "run_missing") return undefined;
  return cell.evidenceKind === "unrecorded"
    ? "This run did not record this query under the current basis"
    : formatTimingExclusion(cell.unavailableReason);
}

export function MultiRunHeatmap({
  results,
  baselineIndex,
  runLabels,
  limit = DEFAULT_QUERY_DIFF_LIMIT,
  limiter,
  orderByDisagreement = false,
  queryFilter,
  queryHref,
}: MultiRunHeatmapProps) {
  if (results.length < 3) return null;
  const effectiveLimiter: QueryDiffLimiter = limiter ?? (orderByDisagreement ? "movement" : "all");
  const all = buildHeatmapRows(results, baselineIndex);
  const rowsByQueryId = new Map(all.map((row) => [row.queryId, row]));
  const ordered = queryFilter
    ? (queryFilter.map((id) => rowsByQueryId.get(id)).filter((r): r is HeatmapRow => r !== undefined))
    : filterHeatmapRows(all, effectiveLimiter, baselineIndex);
  const shown = queryFilter || effectiveLimiter === "all" ? ordered : ordered.slice(0, limit);
  const unavailableCounts = {
    failed: shown.reduce((n, row) => n + row.cells.filter((c) => c.evidenceKind === "failed").length, 0),
    excluded: shown.reduce((n, row) => n + row.cells.filter((c) => c.evidenceKind === "excluded").length, 0),
    unrecorded: shown.reduce((n, row) => n + row.cells.filter((c) => c.evidenceKind === "unrecorded").length, 0),
  };

  return (
    <section class="card mb-8" aria-labelledby="multi-run-heatmap-title">
      <div class="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="multi-run-heatmap-title" class="text-base font-semibold text-[var(--bb-data-fg-primary)]">
            Query by run
          </h2>
          <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]" data-testid="heatmap-caption">
            {shown.length === 0 && effectiveLimiter !== "all"
              ? `No queries match ${QUERY_DIFF_LIMITER_LABELS[effectiveLimiter].toLowerCase()} — showing 0 of ${all.length}.`
              : `Ratio against ${runLabels[baselineIndex] ?? "the baseline"} — below 1.00x is faster. Showing ${shown.length} of ${all.length} ${all.length === 1 ? "query" : "queries"}.`}
          </p>
        </div>
        <StatusBadge role="comparison" tone="neutral">
          {`Scale saturates at ${DIVERGING_RATIO_CLAMP}x`}
        </StatusBadge>
      </div>

      {(unavailableCounts.failed > 0 || unavailableCounts.excluded > 0 || unavailableCounts.unrecorded > 0) && (
        <div class="mb-2 space-y-1 text-xs text-[var(--bb-data-fg-subtle)]" data-testid="heatmap-unrecorded-note">
          {unavailableCounts.failed > 0 && (
            <p>
              {`${unavailableCounts.failed} ${unavailableCounts.failed === 1 ? "cell has" : "cells have"} failed execution evidence and cannot form a ratio.`}
            </p>
          )}
          {unavailableCounts.excluded > 0 && (
            <p>
              {`${unavailableCounts.excluded} ${unavailableCounts.excluded === 1 ? "cell has" : "cells have"} recorded timing evidence that is excluded from comparison.`}
            </p>
          )}
          {unavailableCounts.unrecorded > 0 && (
            <p>
              {`${unavailableCounts.unrecorded} ${unavailableCounts.unrecorded === 1 ? "cell is" : "cells are"} unrecorded under the current basis.`}
            </p>
          )}
        </div>
      )}

      <div class="overflow-x-auto">
        <table class="min-w-full w-max divide-y divide-[var(--bb-data-border)] text-sm">
          <thead class="bg-[var(--bb-surface-data-muted)]">
            <tr>
              <th scope="col" class="table-th">Query</th>
              {results.map((r, i) => (
                <th key={r.result_id} scope="col" class="table-th">
                  {runLabels[i] ?? r.platform}
                  {i === baselineIndex ? " (baseline)" : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
            {shown.map((row) => (
              <tr key={row.queryId}>
                <th scope="row" class="table-td font-mono font-medium text-left">
                  {queryHref ? (
                    <a href={queryHref(row.queryId)} class="text-[var(--bb-accent-hover)] underline" aria-label={`Compare ${row.queryId} iterations across selected runs`}>
                      {row.queryId}
                    </a>
                  ) : row.queryId}
                </th>
                {row.cells.map((cell, i) => (
                  <td
                    key={i}
                    class={`table-td font-mono text-xs${cell.position === null ? "" : " multi-run-heatmap-cell"}`}
                    style={heatmapCellStyle(cell.position)}
                    title={unavailableCellDescription(cell)}
                    aria-label={cell.ratio !== null ? `${cell.ratio.toFixed(2)} times baseline` : unavailableCellDescription(cell)}
                    data-testid={`cell-${row.queryId}-${i}`}
                  >
                    {cell.missingKind === "baseline_missing" && cell.timingMs !== null ? (
                      <span>
                        {cell.timingMs.toFixed(1)} ms{" "}
                        <span class="text-[10px] text-[var(--bb-data-fg-subtle)]">(no base)</span>
                      </span>
                    ) : (
                      cellText(cell)
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
