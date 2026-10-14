import { formatRatio } from "@/lib/metricFormatters";
import { useRef, useState } from "preact/hooks";
import type { QueryTiming } from "@/types";
import { TableScrollHint } from "@/components/TableScrollHint";
import { median } from "@/lib/measurementBasis";
import { fmtMs } from "@/utils";

export interface PassStripProps {
  queries: QueryTiming[];
  limit?: number;
  embedded?: boolean;
}

export interface QueryPassSummary {
  queryId: string;
  executions: QueryTiming[];
  warmValues: number[];
  warmupMs: number | null;
  warmupTotalMs: number | null;
  warmMedian: number | null;
  warmMin: number | null;
  spreadMs: number | null;
  warmupRatio: number | null;
}

function passing(rows: QueryTiming[]): QueryTiming[] {
  return rows.filter((row) => row.status === "pass");
}

export function summarizeQueryPasses(queries: QueryTiming[]): QueryPassSummary[] {
  const byQuery = new Map<string, QueryTiming[]>();
  for (const row of queries) {
    const bucket = byQuery.get(row.query_id);
    if (bucket) bucket.push(row);
    else byQuery.set(row.query_id, [row]);
  }

  return [...byQuery.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([queryId, executions]) => {
      const warm = passing(executions).filter((r) => r.run_type === "measurement" || r.run_type === null);
      const warmupValues = passing(executions)
        .filter((r) => r.run_type === "warmup")
        .map((r) => r.duration_ms)
        .filter((ms) => Number.isFinite(ms) && ms > 0);
      const warmValues = warm.map((r) => r.duration_ms).filter((ms) => Number.isFinite(ms) && ms > 0);
      const warmMedian = median(warmValues);
      const warmMin = warmValues.length > 0 ? Math.min(...warmValues) : null;
      const spreadMs =
        warmValues.length > 1 ? Math.max(...warmValues) - Math.min(...warmValues) : null;
      const warmupMs = median(warmupValues);
      const warmupTotalMs = warmupValues.length > 0 ? warmupValues.reduce((total, ms) => total + ms, 0) : null;
      return {
        queryId,
        executions,
        warmValues,
        warmupMs,
        warmupTotalMs,
        warmMedian,
        warmMin,
        spreadMs,
        warmupRatio:
          warmupMs !== null && warmMedian !== null && warmMedian > 0 ? warmupMs / warmMedian : null,
      };
    });
}

export interface RunPassTotals {
  queryCount: number;
  passCount: number;
  warmMedianMs: number | null;
  warmMinMs: number | null;
  spreadMs: number | null;
  warmupMs: number | null;
  warmupQueryCount: number;
  warmupRatio: number | null;
}

export function summarizeRunPasses(summaries: readonly QueryPassSummary[]): RunPassTotals {
  const sum = (pick: (s: QueryPassSummary) => number | null): number | null => {
    const values = summaries.map(pick).filter((value): value is number => value !== null);
    return values.length > 0 ? values.reduce((total, value) => total + value, 0) : null;
  };
  const withWarmup = summaries.filter((s) => s.warmupMs !== null);
  const comparable = withWarmup.filter((s) => s.warmMedian !== null);
  const warmupMs = sum((s) => s.warmupTotalMs);
  const comparableWarmupMs = comparable.reduce((total, s) => total + (s.warmupMs ?? 0), 0);
  const comparableWarmMedianMs = comparable.reduce((total, s) => total + (s.warmMedian ?? 0), 0);

  return {
    queryCount: summaries.length,
    passCount: summaries.reduce((total, s) => total + s.warmValues.length, 0),
    warmMedianMs: sum((s) => s.warmMedian),
    warmMinMs: sum((s) => s.warmMin),
    spreadMs: sum((s) => s.spreadMs),
    warmupMs,
    warmupQueryCount: withWarmup.length,
    warmupRatio: comparableWarmMedianMs > 0 ? comparableWarmupMs / comparableWarmMedianMs : null,
  };
}

export function hasNoRecordedWarmup(summaries: readonly QueryPassSummary[]): boolean {
  return summaries.every((s) => s.warmupMs === null);
}

function ratioText(ratio: number | null): string {
  return formatRatio(ratio);
}

export function PassStrip({ queries, limit = 25, embedded = false }: PassStripProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [visibleLimit, setVisibleLimit] = useState(limit);
  const summaries = summarizeQueryPasses(queries);
  if (summaries.length === 0) return null;
  const shown = summaries.slice(0, visibleLimit);
  const noWarmup = hasNoRecordedWarmup(summaries);
  const totals = summarizeRunPasses(summaries);
  const totalsScope =
    shown.length === summaries.length
      ? `Totals across all ${summaries.length} ${summaries.length === 1 ? "query" : "queries"}.`
      : `Totals across all ${summaries.length} queries, including the ${summaries.length - shown.length} not listed above.`;

  const Container = embedded ? "div" : "section";
  const containerClass = embedded ? "mb-6" : "card mb-8";
  const containerProps = embedded ? {} : { "aria-labelledby": "pass-view-title" };
  const introClass = embedded ? "text-xs text-[var(--bb-data-fg-muted)]" : "mt-1 text-xs text-[var(--bb-data-fg-muted)]";

  const heading = embedded ? null : (
    <h2 id="pass-view-title" class="text-base font-semibold text-[var(--bb-data-fg-primary)]">
      Passes within this run
    </h2>
  );

  return (
    <Container class={containerClass} {...containerProps}>
      <div class="mb-3">
        {heading}
        <p class={introClass}>
          {`Warm median is the median of this run's passing measurement passes, per query — the same reduction the published figure uses. Warmup median includes all passing warmup streams and iterations and is excluded from the warm median. Showing ${shown.length} of ${summaries.length} ${summaries.length === 1 ? "query" : "queries"}.`}
        </p>
        {noWarmup ? (
          <p class="mt-1 text-xs text-[var(--bb-data-fg-subtle)]" data-testid="no-warmup-note">
            This run has no usable passing warmup duration, so no warmup ratio is shown. It is absent, not zero.
          </p>
        ) : null}
      </div>

      <TableScrollHint scrollerRef={scrollerRef} testId="detail-passes-scroll-hint" />
      <div ref={scrollerRef} class="overflow-x-auto" data-testid="detail-passes-scroll-container">
        <table class="min-w-full w-max divide-y divide-[var(--bb-data-border)] text-sm">
          <thead class="bg-[var(--bb-surface-data-muted)]">
            <tr>
              <th class="table-th">Query</th>
              <th class="table-th">Passes</th>
              <th class="table-th">Warm median</th>
              <th class="table-th">Warm min</th>
              <th class="table-th">Spread</th>
              <th class="table-th">Warmup median</th>
              <th class="table-th">Warmup vs warm</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
            {shown.map((s) => (
              <tr key={s.queryId} class="hover:bg-[var(--bb-surface-data-muted)]">
                <td class="table-td font-mono font-medium">{s.queryId}</td>
                <td class="table-td font-mono text-xs">{s.warmValues.length}</td>
                <td class="table-td font-mono">{s.warmMedian !== null ? fmtMs(s.warmMedian) : "—"}</td>
                <td class="table-td font-mono">{s.warmMin !== null ? fmtMs(s.warmMin) : "—"}</td>
                <td class="table-td font-mono">{s.spreadMs !== null ? fmtMs(s.spreadMs) : "—"}</td>
                <td class="table-td font-mono">{s.warmupMs !== null ? fmtMs(s.warmupMs) : "—"}</td>
                <td class="table-td font-mono" data-testid={`warmup-ratio-${s.queryId}`}>
                  {ratioText(s.warmupRatio)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot class="border-t-2 border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data-muted)]">
            <tr data-testid="pass-strip-totals">
              <th scope="row" class="table-td text-left font-semibold" title={totalsScope}>
                Overall
              </th>
              <td class="table-td font-mono text-xs">{totals.passCount}</td>
              <td class="table-td font-mono font-semibold">
                {totals.warmMedianMs !== null ? fmtMs(totals.warmMedianMs) : "—"}
              </td>
              <td class="table-td font-mono">{totals.warmMinMs !== null ? fmtMs(totals.warmMinMs) : "—"}</td>
              <td class="table-td font-mono">{totals.spreadMs !== null ? fmtMs(totals.spreadMs) : "—"}</td>
              <td
                class="table-td font-mono"
                title={
                  `Total recorded warmup time across all passing warmup executions for ${totals.warmupQueryCount} of ${totals.queryCount} queries.`
                }
              >
                {totals.warmupMs !== null ? fmtMs(totals.warmupMs) : "—"}
              </td>
              <td class="table-td font-mono" data-testid="warmup-ratio-overall">
                {ratioText(totals.warmupRatio)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      {shown.length < summaries.length && (
        <button type="button" class="btn btn-secondary mt-3" onClick={() => setVisibleLimit((count) => count + limit)}>
          Show more query summaries
        </button>
      )}
      <p class="mt-2 text-xs text-[var(--bb-data-fg-subtle)]">
        {totalsScope} Warmup overall is the total of all passing warmup executions.
        The overall ratio compares summed warmup medians with summed warm medians for queries with both.
      </p>
    </Container>
  );
}
