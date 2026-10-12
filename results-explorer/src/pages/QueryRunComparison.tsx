import { useEffect, useMemo, useState } from "preact/hooks";
import type { RoutableProps } from "preact-router";
import { ErrorMessage } from "@/components/ErrorMessage";
import { PageHeader } from "@/components/PageHeader";
import { CompareSummarySkeleton } from "@/components/LoadingSpinner";
import {
  getExistingResultIds,
  getQueryExecutions,
  getResultDetailMetrics,
  resolveShortId,
  type QueryExecutionRow,
  type ResultDetailMetricsRow,
} from "@/lib/duckdbQueries";
import { recoverCompareResults } from "@/lib/compareRecovery";
import { formatLatencyMs } from "@/lib/metricFormatters";
import { formatRunIdentitiesForCohort } from "@/lib/runIdentity";
import { resultDetailHref, visibleResultIdForRow } from "@/lib/resultLinks";
import { paletteColor } from "@/lib/chartTheme";
import type { MeasurementBasis } from "@/lib/measurementBasis";
import {
  MAX_QUERY_COMPARISON_RUNS,
  parseQueryRunSelection,
  summarizeQueryRunExecutions,
  type QueryRunExecutionSummary,
} from "@/lib/queryRunComparison";
import { errMsg } from "@/utils";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

interface QueryRunComparisonProps extends RoutableProps {
  queryId?: string;
  url?: string;
}

interface LoadedRun {
  requestedId: string;
  resolvedId: string;
  detail: ResultDetailMetricsRow;
  summary: QueryRunExecutionSummary;
  identityLabel: string;
}

interface UnavailableRun {
  requestedId: string;
  kind: "missing" | "failed";
  message: string;
}

type ComparisonRun = LoadedRun | UnavailableRun;

interface ComparisonData {
  runs: ComparisonRun[];
  notices: string[];
}

function currentUrl(url?: string): string {
  if (url) return url;
  if (typeof window === "undefined") return "/results/compare";
  return `${window.location.pathname}${window.location.search}`;
}

function isLoadedRun(run: ComparisonRun): run is LoadedRun {
  return "detail" in run;
}

function latency(value: number | null): string {
  return value === null ? "—" : formatLatencyMs(value, { subMillisecond: "compact" }).valueText;
}

function runBasisLabel(basis: MeasurementBasis): string {
  const passLabel = basis.passes.kind === "warm_pass"
    ? `measurement iteration ${basis.passes.pass}`
    : "all successful measurement iterations";
  const statisticLabel = basis.statistic === "min" ? "minimum" : "median";
  return `Selected basis value: ${statisticLabel} of ${passLabel}`;
}

function observedRange(summary: QueryRunExecutionSummary): string {
  if (summary.minMs === null || summary.maxMs === null) return "—";
  return `${latency(summary.minMs)} – ${latency(summary.maxMs)}`;
}

function sampleDescription(summary: QueryRunExecutionSummary): string {
  if (summary.sampleCount === 0) {
    return summary.queryRecordCount === 0
      ? "No execution record for this query"
      : "No successful measurement execution";
  }
  const executions = `${summary.sampleCount} successful execution${summary.sampleCount === 1 ? "" : "s"}`;
  const iterations = `${summary.iterationCount} recorded iteration${summary.iterationCount === 1 ? "" : "s"}`;
  const unlabelled = summary.samples.filter((sample) => sample.iteration === null).length;
  return `${executions}; ${iterations}${unlabelled > 0 ? `; ${unlabelled} without an iteration number` : ""}`;
}

export function QueryRunComparison({ queryId = "", url }: QueryRunComparisonProps) {
  const activeUrl = currentUrl(url);
  const selection = useMemo(() => parseQueryRunSelection(activeUrl, queryId), [activeUrl, queryId]);
  const [data, setData] = useState<ComparisonData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryToken, setRetryToken] = useState(0);
  const [loading, setLoading] = useState(true);
  const queryTitle = selection.queryId || "Query";
  useDocumentTitle(`${queryTitle} across runs · BenchBox Results`);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    setLoading(true);

    if (!selection.queryId) {
      setError("No query ID was provided.");
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    if (selection.requestedIds.length === 0) {
      setData({ runs: [], notices: [] });
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    const notices: string[] = [];
    if (selection.duplicateIds.length > 0) {
      notices.push(`Ignored ${selection.duplicateIds.length} repeated run ID${selection.duplicateIds.length === 1 ? "" : "s"}.`);
    }
    if (selection.overflowIds.length > 0) {
      notices.push(`Only the first ${MAX_QUERY_COMPARISON_RUNS} distinct run IDs are included.`);
    }
    if (selection.basisNotice) notices.push(selection.basisNotice);

    recoverCompareResults(selection.requestedIds, MAX_QUERY_COMPARISON_RUNS, {
      resolveId: resolveShortId,
      findExistingIds: getExistingResultIds,
      loadResult: getResultDetailMetrics,
      isCancelled: () => cancelled,
    })
      .then(async (recovery) => {
        if (cancelled || recovery === null) return;
        if (recovery.aliases.length > 0) {
          notices.push(
            `Ignored ${recovery.aliases.length} run ID${recovery.aliases.length === 1 ? "" : "s"} that resolve to a run already selected.`,
          );
        }
        if (recovery.overflow.length > 0) {
          notices.push(`Only ${MAX_QUERY_COMPARISON_RUNS} unique published runs can be shown.`);
        }
        if (recovery.unprocessed.length > 0) {
          notices.push(`${recovery.unprocessed.length} additional run ID${recovery.unprocessed.length === 1 ? " was" : "s were"} not checked.`);
        }

        const resolvedIds = recovery.recovered.map((entry) => entry.resolvedId);
        const queryRows = resolvedIds.length > 0 ? await getQueryExecutions(resolvedIds, selection.queryId) : [];
        if (cancelled) return;
        const rowsByResult = new Map<string, QueryExecutionRow[]>();
        for (const row of queryRows) {
          const rows = rowsByResult.get(row.result_id);
          if (rows) rows.push(row);
          else rowsByResult.set(row.result_id, [row]);
        }

        const order = new Map(selection.requestedIds.map((id, index) => [id, index]));
        const availableRuns: ComparisonRun[] = [
          ...recovery.recovered.map((entry): LoadedRun => ({
            requestedId: entry.requestedId,
            resolvedId: entry.resolvedId,
            detail: entry.detail,
            summary: summarizeQueryRunExecutions(
              rowsByResult.get(entry.resolvedId) ?? [],
              selection.queryId,
              selection.basis,
            ),
            identityLabel: "",
          })),
          ...recovery.missing.map((entry): UnavailableRun => ({
            requestedId: entry.requestedId,
            kind: "missing",
            message: "This run is no longer published.",
          })),
          ...recovery.failed.map((entry): UnavailableRun => ({
            requestedId: entry.requestedId,
            kind: "failed",
            message: errMsg(entry.error),
          })),
        ].sort((a, b) => (order.get(a.requestedId) ?? Infinity) - (order.get(b.requestedId) ?? Infinity));

        const loadedRuns = availableRuns.filter(isLoadedRun);
        const identityLabels = formatRunIdentitiesForCohort(
          loadedRuns.map((run) => run.detail),
          "table",
        );
        let identityIndex = 0;
        const runs = availableRuns.map((run) => {
          if (!isLoadedRun(run)) return run;
          const identityLabel = identityLabels[identityIndex] ?? run.detail.platform;
          identityIndex += 1;
          return { ...run, identityLabel };
        });
        if (!cancelled) {
          setData({ runs, notices });
          setLoading(false);
        }
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(errMsg(reason));
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selection, retryToken]);

  if (loading) return <CompareSummarySkeleton message={`Loading ${queryTitle} executions across selected runs...`} />;
  if (error) {
    return (
      <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <ErrorMessage title="Cannot compare query executions" message={error} onRetry={() => setRetryToken((value) => value + 1)} />
        <a href="/results/compare" class="mt-4 inline-block text-sm no-underline">← Back to run comparison</a>
      </div>
    );
  }
  if (!data) return null;

  const loadedRuns = data.runs.filter(isLoadedRun);
  const chartRuns = data.runs.map((run, index) => ({
    label: `Run ${index + 1}`,
    summary: isLoadedRun(run) ? run.summary : null,
    color: paletteColor(index),
  }));
  const chartHasSamples = chartRuns.some((run) => (run.summary?.sampleCount ?? 0) > 0);

  return (
    <main class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        crumbs={[{ label: "Results", href: "/results/" }, { label: "Compare runs", href: "/results/compare" }, { label: queryTitle }]}
        eyebrow="Single-query comparison"
        title={queryTitle}
        subtitle={`Successful measurement executions across up to ${MAX_QUERY_COMPARISON_RUNS} selected runs. ${runBasisLabel(selection.basis)}. The chart and observed range include all successful measurement iterations.`}
        meta={<span>{data.runs.length} selected run{data.runs.length === 1 ? "" : "s"}</span>}
      />

      {data.notices.length > 0 && (
        <aside role="status" class="mb-5 rounded-lg border border-[var(--bb-data-border-strong)] bg-[var(--bb-tone-info-bg)] p-4 text-sm text-[var(--bb-tone-info-fg)]">
          {data.notices.map((notice) => <p key={notice}>{notice}</p>)}
        </aside>
      )}

      {selection.requestedIds.length === 0 ? (
        <section class="rounded-xl border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] p-6">
          <h2 class="text-lg font-semibold text-[var(--bb-data-fg-primary)]">Choose runs to compare</h2>
          <p class="mt-2 text-sm text-[var(--bb-data-fg-muted)]">
            Select runs on the comparison page, then open this query view with their IDs in the URL.
          </p>
          <a href="/results/compare" class="btn btn-primary mt-4 inline-block no-underline">Open run comparison</a>
        </section>
      ) : (
        <>
          <section aria-labelledby="query-execution-chart-heading" class="rounded-xl border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] p-4 sm:p-6">
            <div class="mb-3">
              <h2 id="query-execution-chart-heading" class="text-lg font-semibold text-[var(--bb-data-fg-primary)]">
                Recorded execution times
              </h2>
              <p class="mt-1 text-sm text-[var(--bb-data-fg-muted)]">
                Each point is one successful execution. The median and observed min–max range describe these records; they are not confidence intervals or estimates of future performance. Stream executions remain separate points.
              </p>
            </div>
            {chartHasSamples ? (
              <QueryExecutionChart queryId={selection.queryId} runs={chartRuns} />
            ) : (
              <p class="rounded-lg bg-[var(--bb-surface-data-muted)] p-4 text-sm text-[var(--bb-data-fg-muted)]">
                No selected run has a successful measurement execution for {selection.queryId} under this basis.
              </p>
            )}
          </section>

          <section aria-labelledby="query-execution-table-heading" class="mt-6">
            <h2 id="query-execution-table-heading" class="mb-3 text-lg font-semibold text-[var(--bb-data-fg-primary)]">
              Run details and excluded records
            </h2>
            <div class="overflow-x-auto rounded-xl border border-[var(--bb-data-border)]">
              <table class="min-w-full border-collapse bg-[var(--bb-surface-data)] text-left text-sm">
                <caption class="sr-only">{selection.queryId} execution statistics and statuses for selected runs</caption>
                <thead class="bg-[var(--bb-surface-data-muted)] text-xs uppercase tracking-wide text-[var(--bb-data-fg-muted)]">
                  <tr>
                    <th scope="col" class="px-4 py-3">Run</th>
                    <th scope="col" class="px-4 py-3">Median</th>
                    <th scope="col" class="px-4 py-3">Observed range</th>
                    <th scope="col" class="px-4 py-3">Sample n</th>
                    <th scope="col" class="px-4 py-3">Measurement failures</th>
                    <th scope="col" class="px-4 py-3">Warmup pass / fail</th>
                    <th scope="col" class="px-4 py-3">Other records</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)] text-[var(--bb-data-fg-primary)]">
                  {data.runs.map((run, index) => (
                    <QueryRunTableRow key={`${run.requestedId}-${index}`} run={run} index={index} basis={selection.basis} />
                  ))}
                </tbody>
              </table>
            </div>
            {loadedRuns.some((run) => run.summary.legacyFallback || run.summary.ignoredLegacyCount > 0) && (
              <p class="mt-3 text-xs text-[var(--bb-data-fg-muted)]">
                Legacy rows without a run type are used only when the query has no measurement-tagged rows in that run. When both exist, the legacy rows are excluded.
              </p>
            )}
          </section>
        </>
      )}
    </main>
  );
}

interface QueryRunTableRowProps {
  run: ComparisonRun;
  index: number;
  basis: MeasurementBasis;
}

function QueryRunTableRow({ run, index, basis }: QueryRunTableRowProps) {
  if (!isLoadedRun(run)) {
    return (
      <tr>
        <th scope="row" class="whitespace-nowrap px-4 py-3 font-medium">
          <span class="mr-2 inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: paletteColor(index) }} aria-hidden="true" />
          Run {index + 1} · {run.requestedId}
        </th>
        <td colSpan={6} class="px-4 py-3 text-[var(--bb-data-fg-muted)]">
          <span class="font-medium">{run.kind === "missing" ? "Unavailable" : "Could not load"}:</span> {run.message}
        </td>
      </tr>
    );
  }

  const summary = run.summary;
  const otherRecords = [
    summary.measurementRowCount === 0 && summary.legacyRowCount === 0 ? "No measurement execution recorded" : null,
    summary.invalidMeasurementTimingCount > 0 ? `${summary.invalidMeasurementTimingCount} invalid timing` : null,
    summary.ignoredLegacyCount > 0 ? `${summary.ignoredLegacyCount} legacy row${summary.ignoredLegacyCount === 1 ? "" : "s"} ignored` : null,
    summary.otherRunTypeCount > 0 ? `${summary.otherRunTypeCount} unsupported run type` : null,
    summary.samples.some((sample) => sample.legacy) ? "legacy run type fallback" : null,
  ].filter((value): value is string => value !== null);

  return (
    <tr>
      <th scope="row" class="min-w-64 px-4 py-3 align-top font-medium">
        <span class="mr-2 inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: paletteColor(index) }} aria-hidden="true" />
        <span class="mr-1 text-xs text-[var(--bb-data-fg-muted)]">Run {index + 1}</span>
          <a class="block text-[var(--bb-accent-hover)] underline decoration-dotted underline-offset-2" href={resultDetailHref(run.detail.result_id)}>
          {run.identityLabel} · {visibleResultIdForRow(run.detail)}
        </a>
        <span class="mt-1 block text-xs font-normal text-[var(--bb-data-fg-muted)]" title={run.detail.result_id}>
          {run.detail.run_date}
        </span>
      </th>
      <td class="whitespace-nowrap px-4 py-3 align-top" title={sampleDescription(summary)}>
        {latency(summary.medianMs)}
        {summary.sampleCount > 0 && summary.sampleCount < 2 && <span class="ml-1 text-xs text-[var(--bb-data-fg-muted)]">(n=1)</span>}
        {(basis.passes.kind !== "all_warm" || basis.statistic !== "median") && (
          <span class="block text-xs text-[var(--bb-data-fg-muted)]">Selected basis value: {latency(summary.selectedValueMs)}</span>
        )}
      </td>
      <td class="whitespace-nowrap px-4 py-3 align-top">{observedRange(summary)}</td>
      <td class="whitespace-nowrap px-4 py-3 align-top">
        <span>{summary.sampleCount}</span>
        <span class="block text-xs text-[var(--bb-data-fg-muted)]">{sampleDescription(summary)}</span>
      </td>
      <td class="whitespace-nowrap px-4 py-3 align-top">
        {summary.measurementFailureCount}
        {summary.invalidMeasurementTimingCount > 0 && (
          <span class="block text-xs text-[var(--bb-data-fg-muted)]">{summary.invalidMeasurementTimingCount} invalid time{summary.invalidMeasurementTimingCount === 1 ? "" : "s"}</span>
        )}
      </td>
      <td class="whitespace-nowrap px-4 py-3 align-top">{summary.warmupPassCount} / {summary.warmupFailureCount}</td>
      <td class="min-w-48 px-4 py-3 align-top text-xs text-[var(--bb-data-fg-muted)]">
        {otherRecords.length > 0 ? otherRecords.join("; ") : "—"}
      </td>
    </tr>
  );
}

interface ChartRun {
  label: string;
  summary: QueryRunExecutionSummary | null;
  color: string;
}

function QueryExecutionChart({ queryId, runs }: { queryId: string; runs: ChartRun[] }) {
  const sampleValues = runs.flatMap((run) => run.summary?.samples.map((sample) => sample.durationMs) ?? []);
  if (sampleValues.length === 0) return null;

  const width = Math.max(680, runs.length * 112);
  const height = 340;
  const padding = { top: 24, right: 18, bottom: 52, left: 72 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const minimum = Math.min(...sampleValues);
  const maximum = Math.max(...sampleValues);
  const span = maximum - minimum;
  const axisMinimum = Math.max(0, minimum - (span > 0 ? span * 0.12 : Math.max(minimum * 0.12, 1)));
  const axisMaximum = maximum + (span > 0 ? span * 0.12 : Math.max(maximum * 0.12, 1));
  const groupWidth = plotWidth / Math.max(runs.length, 1);
  const toY = (value: number) => padding.top + plotHeight - ((value - axisMinimum) / (axisMaximum - axisMinimum)) * plotHeight;
  const tickCount = 4;

  return (
    <div class="overflow-x-auto" data-testid="query-run-comparison-chart-scroll">
      <svg
        class="bb-chart-svg"
        width="100%"
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ minWidth: `${width}px` }}
        role="img"
        aria-labelledby="query-run-comparison-chart-title query-run-comparison-chart-description"
        data-testid="query-run-comparison-chart"
      >
        <title id="query-run-comparison-chart-title">{queryId} successful measurement execution times</title>
        <desc id="query-run-comparison-chart-description">
          Each run shows all valid successful executions as points, a vertical observed range, and a diamond at the median. Run numbers match the table below.
        </desc>
        {Array.from({ length: tickCount + 1 }, (_, index) => {
          const fraction = index / tickCount;
          const value = axisMinimum + (axisMaximum - axisMinimum) * fraction;
          const y = toY(value);
          return (
            <g key={index} aria-hidden="true">
              <line x1={padding.left} y1={y} x2={width - padding.right} y2={y} stroke="var(--bb-chart-grid)" stroke-width="1" />
              <text x={padding.left - 8} y={y + 4} text-anchor="end" font-size="11" fill="var(--bb-chart-axis)">
                {latency(value)}
              </text>
            </g>
          );
        })}
        {runs.map((run, index) => {
          const centerX = padding.left + groupWidth * (index + 0.5);
          const samples = run.summary?.samples ?? [];
          const maxJitter = Math.min(34, groupWidth * 0.32);
          const jitterStep = samples.length > 1 ? (maxJitter * 2) / (samples.length - 1) : 0;
          return (
            <g key={run.label}>
              {samples.length > 1 && run.summary?.minMs !== null && run.summary?.minMs !== undefined && run.summary.maxMs !== null && (
                <g aria-hidden="true">
                  <line x1={centerX} y1={toY(run.summary.minMs)} x2={centerX} y2={toY(run.summary.maxMs)} stroke={run.color} stroke-width="2" opacity="0.55" />
                  <line x1={centerX - 5} y1={toY(run.summary.minMs)} x2={centerX + 5} y2={toY(run.summary.minMs)} stroke={run.color} stroke-width="2" />
                  <line x1={centerX - 5} y1={toY(run.summary.maxMs)} x2={centerX + 5} y2={toY(run.summary.maxMs)} stroke={run.color} stroke-width="2" />
                </g>
              )}
              {samples.map((sample, sampleIndex) => {
                const x = samples.length > 1 ? centerX - maxJitter + jitterStep * sampleIndex : centerX;
                const iteration = sample.iteration === null ? "iteration not recorded" : `iteration ${sample.iteration}`;
                const stream = sample.stream === null ? "" : `, stream ${sample.stream}`;
                const legacy = sample.legacy ? ", legacy run type fallback" : "";
                return (
                  <circle key={`${sample.iteration}-${sample.stream}-${sampleIndex}`} cx={x} cy={toY(sample.durationMs)} r="5" fill={run.color} stroke="var(--bb-surface-data)" stroke-width="1.5">
                    <title>{`${run.label}, ${iteration}${stream}${legacy}: ${latency(sample.durationMs)}`}</title>
                  </circle>
                );
              })}
              {run.summary?.medianMs !== null && run.summary?.medianMs !== undefined && (
                <path
                  d={`M ${centerX} ${toY(run.summary.medianMs) - 7} L ${centerX + 7} ${toY(run.summary.medianMs)} L ${centerX} ${toY(run.summary.medianMs) + 7} L ${centerX - 7} ${toY(run.summary.medianMs)} Z`}
                  fill="var(--bb-data-fg-primary)"
                  stroke="var(--bb-surface-data)"
                  stroke-width="1"
                >
                  <title>{`${run.label} median: ${latency(run.summary.medianMs)}`}</title>
                </path>
              )}
              {samples.length === 0 && (
                <text x={centerX} y={padding.top + plotHeight / 2} text-anchor="middle" font-size="11" fill="var(--bb-chart-label-muted)">
                  No data
                </text>
              )}
              <text x={centerX} y={height - 18} text-anchor="middle" font-size="11" fill="var(--bb-chart-axis)">{run.label}</text>
            </g>
          );
        })}
        <text
          x={18}
          y={padding.top + plotHeight / 2}
          text-anchor="middle"
          font-size="11"
          fill="var(--bb-chart-axis)"
          transform={`rotate(-90, 18, ${padding.top + plotHeight / 2})`}
        >
          Duration
        </text>
        <text x={padding.left + plotWidth / 2} y={height - 2} text-anchor="middle" font-size="10" fill="var(--bb-chart-label-muted)">
          Selected runs in the same order as the table
        </text>
      </svg>
    </div>
  );
}
