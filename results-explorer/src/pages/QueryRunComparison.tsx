import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { RoutableProps } from "preact-router";
import { CapturedPlanViewer } from "@/components/CapturedPlanViewer";
import { ErrorMessage } from "@/components/ErrorMessage";
import { PageHeader } from "@/components/PageHeader";
import { CompareSummarySkeleton } from "@/components/LoadingSpinner";
import { planDownloadUrl } from "@/components/RunReceipt";
import { TableScrollHint } from "@/components/TableScrollHint";
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
import { formatRunIdentitiesForCohort, preserveUniqueAfterTruncation } from "@/lib/runIdentity";
import { resultDetailHref, visibleResultIdForRow } from "@/lib/resultLinks";
import { assignRunColorSlots, paletteColor } from "@/lib/chartTheme";
import { runMarkerPath } from "@/lib/chartMarkers";
import type { MeasurementBasis } from "@/lib/measurementBasis";
import {
  addRunsHref,
  benchmarkDisplayLabel,
  buildValueAxis,
  MAX_QUERY_COMPARISON_RUNS,
  parseQueryRunSelection,
  queryDisplayTitle,
  runScopeDifferences,
  summarizeQueryRunExecutions,
  valueAxisFraction,
  type QueryRunExecutionSummary,
  type ValueAxis,
} from "@/lib/queryRunComparison";
import { useIsNarrowViewport } from "@/lib/useIsNarrowViewport";
import { errMsg } from "@/utils";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { axisLabelAnchor } from "@/lib/chartFrame";

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

const NARROW_VIEWPORT_QUERY = "(max-width: 639px)";
const SAMPLE_RADIUS = 5;
const SELECTED_DIAMOND_RADIUS = 9;

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
  if (summary.displayMinMs === null || summary.displayMaxMs === null) return "—";
  return `${latency(summary.displayMinMs)} – ${latency(summary.displayMaxMs)}`;
}

function basisPrimaryLabel(basis: MeasurementBasis): string {
  if (basis.passes.kind === "warm_pass") return `Iteration ${basis.passes.pass} value`;
  if (basis.statistic === "min") return "Selected minimum";
  return "Median";
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

function otherRecordsFor(summary: QueryRunExecutionSummary): string[] {
  return [
    summary.measurementRowCount === 0 && summary.legacyRowCount === 0 ? "No measurement execution recorded" : null,
    summary.invalidMeasurementTimingCount > 0 ? `${summary.invalidMeasurementTimingCount} invalid timing` : null,
    summary.ignoredLegacyCount > 0 ? `${summary.ignoredLegacyCount} legacy row${summary.ignoredLegacyCount === 1 ? "" : "s"} ignored` : null,
    summary.otherRunTypeCount > 0 ? `${summary.otherRunTypeCount} unsupported run type` : null,
    summary.samples.some((sample) => sample.legacy) ? "legacy run type fallback" : null,
  ].filter((value): value is string => value !== null);
}

export function QueryRunComparison({ queryId = "", url }: QueryRunComparisonProps) {
  const activeUrl = currentUrl(url);
  const selection = useMemo(() => parseQueryRunSelection(activeUrl, queryId), [activeUrl, queryId]);
  const [data, setData] = useState<ComparisonData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryToken, setRetryToken] = useState(0);
  const [loading, setLoading] = useState(true);
  const queryTitle = selection.queryId || "Query";
  const loadedDetails = data?.runs.filter(isLoadedRun).map((run) => run.detail) ?? [];
  const benchmarkLabel = benchmarkDisplayLabel(loadedDetails.map((detail) => detail.benchmark));
  const pageTitle = selection.queryId === "" ? queryTitle : queryDisplayTitle(selection.queryId, benchmarkLabel);
  useDocumentTitle(`${pageTitle} across runs · BenchBox Results`);

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
        if (cancelled) return;
        if (recovery === null) {
          setError("The selected runs could not be loaded. Retry to try again.");
          setLoading(false);
          return;
        }
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
  const scopeDifferences = runScopeDifferences(loadedRuns.map((run) => run.detail));
  const runSlots = runColorSlotsFor(data.runs);
  const chartRuns: ChartRun[] = data.runs.map((run, index) => ({
    label: isLoadedRun(run) ? run.identityLabel : run.requestedId,
    summary: isLoadedRun(run) ? run.summary : null,
    color: paletteColor(runSlots[index] ?? index),
    shapeIndex: runSlots[index] ?? index,
  }));
  const chartHasSamples = chartRuns.some((run) => (run.summary?.sampleCount ?? 0) > 0);
  const showOtherRecords = loadedRuns.some((run) => otherRecordsFor(run.summary).length > 0);
  const plannedRuns = loadedRuns.flatMap((run) => {
    const plansUrl = run.detail.has_plans ? planDownloadUrl(run.detail) : null;
    return plansUrl === null ? [] : [{ run, plansUrl }];
  });

  return (
    <main class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        crumbs={[{ label: "Results", href: "/results/" }, { label: "Compare", href: "/results/compare" }, { label: pageTitle }]}
        eyebrow="Single-query comparison"
        title={pageTitle}
        subtitle={`Successful measurement executions across up to ${MAX_QUERY_COMPARISON_RUNS} selected runs. ${runBasisLabel(selection.basis)}. Points show every successful measurement iteration; the highlighted value and observed range follow the selected basis.`}
        meta={<span>{data.runs.length} selected run{data.runs.length === 1 ? "" : "s"}</span>}
        actions={
          selection.requestedIds.length > 0 ? (
            <a href={addRunsHref(selection.requestedIds)} class="text-sm font-medium text-[var(--bb-accent-hover)] underline underline-offset-2">
              Add or change runs
            </a>
          ) : null
        }
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
                Each marker is one successful execution, drawn in its run's colour and shape. The hollow diamond and observed min–max range follow the selected basis value; they are not confidence intervals or estimates of future performance. Stream executions remain separate markers.
              </p>
            </div>
            {scopeDifferences.length > 0 && (
              <aside role="status" class="mb-4 rounded-lg bg-[var(--bb-tone-warning-bg)] p-3 text-sm text-[var(--bb-tone-warning-fg)]" data-testid="query-run-scope-warning">
                The selected runs differ in {scopeDifferences.join("; ")}. Compare their timings with that difference in mind.
              </aside>
            )}
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
            <QueryRunTable runs={data.runs} basis={selection.basis} queryId={selection.queryId} showOtherRecords={showOtherRecords} />
            <p class="mt-3 text-xs text-[var(--bb-data-fg-muted)]">
              Hover a value or count for how it was derived.
              {loadedRuns.some((run) => run.summary.legacyFallback || run.summary.ignoredLegacyCount > 0) && (
                <> Legacy rows without a run type are used only when the query has no measurement-tagged rows in that run. When both exist, the legacy rows are excluded.</>
              )}
            </p>
          </section>

          {plannedRuns.length > 0 && (
            <section aria-labelledby="query-plans-heading" class="mt-6">
              <h2 id="query-plans-heading" class="mb-3 text-lg font-semibold text-[var(--bb-data-fg-primary)]">
                Query plans
              </h2>
              <div class="grid gap-4">
                {plannedRuns.map(({ run, plansUrl }) => (
                  <div key={run.resolvedId}>
                    <p class="mb-2 text-sm font-medium text-[var(--bb-data-fg-primary)]">{run.identityLabel}</p>
                    <CapturedPlanViewer plansUrl={plansUrl} queryId={selection.queryId} />
                  </div>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </main>
  );
}

function runColorSlotsFor(runs: readonly ComparisonRun[]): number[] {
  const slots = assignRunColorSlots(
    runs.map((run, index) => ({
      result_id: String(index),
      platform_id: isLoadedRun(run) ? run.detail.platform_id : null,
      platform: isLoadedRun(run) ? run.detail.platform : run.requestedId,
    })),
  );
  return runs.map((_, index) => slots.get(String(index)) ?? index);
}

function RunMarker({ slot }: { slot: number }) {
  return (
    <svg class="mr-2 inline-block h-3 w-3 align-[-2px]" viewBox="0 0 12 12" aria-hidden="true">
      <path d={runMarkerPath(slot, 6, 6, 5)} fill={paletteColor(slot)} />
    </svg>
  );
}

interface QueryRunTableProps {
  runs: ComparisonRun[];
  basis: MeasurementBasis;
  queryId: string;
  showOtherRecords: boolean;
}

function QueryRunTable({ runs, basis, queryId, showOtherRecords }: QueryRunTableProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const columnCount = showOtherRecords ? 7 : 6;
  const runSlots = runColorSlotsFor(runs);
  return (
    <>
      <TableScrollHint scrollerRef={scrollerRef} testId="query-run-table-scroll-hint" />
      <div ref={scrollerRef} class="overflow-x-auto rounded-xl border border-[var(--bb-data-border)]">
        <table class="min-w-full border-collapse bg-[var(--bb-surface-data)] text-left text-sm">
          <caption class="sr-only">{queryId} execution statistics and statuses for selected runs</caption>
          <thead class="bg-[var(--bb-surface-data-muted)] text-xs uppercase tracking-wide text-[var(--bb-data-fg-muted)]">
            <tr>
              <th scope="col" class="sticky left-0 z-[1] bg-[var(--bb-surface-data-muted)] px-3 py-2 whitespace-nowrap">Run</th>
              <th scope="col" class="px-3 py-2 whitespace-nowrap">{basisPrimaryLabel(basis)}</th>
              <th scope="col" class="px-3 py-2 whitespace-nowrap">Observed range</th>
              <th scope="col" class="px-3 py-2 whitespace-nowrap">Sample n</th>
              <th scope="col" class="px-3 py-2 whitespace-nowrap">Measurement failures</th>
              <th scope="col" class="px-3 py-2 whitespace-nowrap">Warmup pass / fail</th>
              {showOtherRecords && <th scope="col" class="px-3 py-2 whitespace-nowrap">Other records</th>}
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)] text-[var(--bb-data-fg-primary)]">
            {runs.map((run, index) => (
              <QueryRunTableRow
                key={`${run.requestedId}-${index}`}
                run={run}
                slot={runSlots[index] ?? index}
                showOtherRecords={showOtherRecords}
                columnCount={columnCount}
              />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

interface QueryRunTableRowProps {
  run: ComparisonRun;
  slot: number;
  showOtherRecords: boolean;
  columnCount: number;
}

function QueryRunTableRow({ run, slot, showOtherRecords, columnCount }: QueryRunTableRowProps) {
  if (!isLoadedRun(run)) {
    return (
      <tr>
        <th scope="row" class="sticky left-0 whitespace-nowrap bg-[var(--bb-surface-data)] px-3 py-2 font-medium">
          <RunMarker slot={slot} />
          {run.requestedId}
        </th>
        <td colSpan={columnCount - 1} class="px-3 py-2 text-[var(--bb-data-fg-muted)]">
          <span class="font-medium">{run.kind === "missing" ? "Unavailable" : "Could not load"}:</span> {run.message}
        </td>
      </tr>
    );
  }

  const summary = run.summary;
  const otherRecords = otherRecordsFor(summary);

  return (
    <tr>
      <th scope="row" class="sticky left-0 whitespace-nowrap bg-[var(--bb-surface-data)] px-3 py-2 text-left font-medium">
        <RunMarker slot={slot} />
        <a
          class="text-[var(--bb-accent-hover)] underline decoration-dotted underline-offset-2"
          href={resultDetailHref(run.detail.result_id)}
          title={`${run.detail.result_id} · ${run.detail.run_date}`}
        >
          {run.identityLabel} · {visibleResultIdForRow(run.detail)}
        </a>
      </th>
      <td class="whitespace-nowrap px-3 py-2">
        {latency(summary.displayValueMs)}
        {summary.displaySamples.length === 1 && <span class="ml-1 text-xs text-[var(--bb-data-fg-muted)]">(n=1)</span>}
        {summary.basisActive && (
          <span class="ml-2 text-xs text-[var(--bb-data-fg-muted)]">All-iteration median: {latency(summary.medianMs)}</span>
        )}
      </td>
      <td class="whitespace-nowrap px-3 py-2">{observedRange(summary)}</td>
      <td class="whitespace-nowrap px-3 py-2" title={sampleDescription(summary)}>{summary.sampleCount}</td>
      <td class="whitespace-nowrap px-3 py-2">{summary.measurementFailureCount}</td>
      <td class="whitespace-nowrap px-3 py-2">{summary.warmupPassCount} / {summary.warmupFailureCount}</td>
      {showOtherRecords && (
        <td class="min-w-48 px-3 py-2 text-xs text-[var(--bb-data-fg-muted)]">
          {otherRecords.length > 0 ? otherRecords.join("; ") : "—"}
        </td>
      )}
    </tr>
  );
}

interface ChartRun {
  label: string;
  summary: QueryRunExecutionSummary | null;
  color: string;
  shapeIndex: number;
}

function selectedDiamondPath(cx: number, cy: number, radius: number): string {
  return `M ${cx} ${cy - radius} L ${cx + radius} ${cy} L ${cx} ${cy + radius} L ${cx - radius} ${cy} Z`;
}

function sampleValues(runs: readonly ChartRun[]): number[] {
  return runs.flatMap((run) => run.summary?.samples.map((sample) => sample.durationMs) ?? []);
}

function sampleTitle(run: ChartRun, sample: QueryRunExecutionSummary["samples"][number]): string {
  const iteration = sample.iteration === null ? "iteration not recorded" : `iteration ${sample.iteration}`;
  const stream = sample.stream === null ? "" : `, stream ${sample.stream}`;
  const legacy = sample.legacy ? ", legacy run type fallback" : "";
  return `${run.label}, ${iteration}${stream}${legacy}: ${latency(sample.durationMs)}`;
}

function QueryExecutionChart({ queryId, runs }: { queryId: string; runs: ChartRun[] }) {
  const isNarrow = useIsNarrowViewport(NARROW_VIEWPORT_QUERY);
  const values = sampleValues(runs);
  if (values.length === 0) return null;
  return isNarrow ? (
    <RowChart queryId={queryId} runs={runs} values={values} />
  ) : (
    <ColumnChart queryId={queryId} runs={runs} values={values} />
  );
}

function axisScaleCaption(axis: ValueAxis): string {
  return axis.scale === "log" ? "Duration (log scale)" : "Duration";
}

function ColumnChart({ queryId, runs, values }: { queryId: string; runs: ChartRun[]; values: number[] }) {
  const width = Math.max(680, runs.length * 112);
  const height = 340;
  const padding = { top: 24, right: 18, bottom: 64, left: 72 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const axis = buildValueAxis(values, plotHeight);
  const groupWidth = plotWidth / Math.max(runs.length, 1);
  const yFor = (value: number) => padding.top + plotHeight * (1 - valueAxisFraction(axis, value));
  const labels = preserveUniqueAfterTruncation(runs.map((run) => run.label), 18);

  return (
    <div class="overflow-x-auto" data-testid="query-run-comparison-chart-scroll">
      <svg
        class="bb-chart-svg"
        width="100%"
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ minWidth: `${width}px` }}
        role="img"
        data-layout="columns"
        aria-labelledby="query-run-comparison-chart-title query-run-comparison-chart-description"
        data-testid="query-run-comparison-chart"
      >
        <title id="query-run-comparison-chart-title">{queryId} successful measurement execution times</title>
        <desc id="query-run-comparison-chart-description">
          Each run shows all valid successful executions as markers, a vertical observed range for the selected basis, and a hollow diamond at the selected basis value. Runs are in the same order as the table below.
        </desc>
        {axis.ticks.map((tick) => {
          const y = yFor(tick.value);
          return (
            <g key={tick.value} aria-hidden="true">
              <line x1={padding.left} y1={y} x2={width - padding.right} y2={y} stroke="var(--bb-chart-grid)" stroke-width="1" />
              <text x={padding.left - 8} y={y + 4} text-anchor="end" font-size="11" fill="var(--bb-chart-axis)">
                {tick.label}
              </text>
            </g>
          );
        })}
        {runs.map((run, index) => {
          const centerX = padding.left + groupWidth * (index + 0.5);
          const samples = run.summary?.samples ?? [];
          const maxJitter = Math.min(34, groupWidth * 0.32);
          const jitterStep = samples.length > 1 ? (maxJitter * 2) / (samples.length - 1) : 0;
          const summary = run.summary;
          const hasRange = samples.length > 1 && summary?.displayMinMs != null && summary.displayMaxMs != null;
          return (
            <g key={`${run.label}-${index}`}>
              {hasRange && (
                <g aria-hidden="true">
                  <line x1={centerX} y1={yFor(summary.displayMinMs!)} x2={centerX} y2={yFor(summary.displayMaxMs!)} stroke={run.color} stroke-width="2" opacity="0.55" />
                  <line x1={centerX - 5} y1={yFor(summary.displayMinMs!)} x2={centerX + 5} y2={yFor(summary.displayMinMs!)} stroke={run.color} stroke-width="2" />
                  <line x1={centerX - 5} y1={yFor(summary.displayMaxMs!)} x2={centerX + 5} y2={yFor(summary.displayMaxMs!)} stroke={run.color} stroke-width="2" />
                </g>
              )}
              {samples.map((sample, sampleIndex) => {
                const x = samples.length > 1 ? centerX - maxJitter + jitterStep * sampleIndex : centerX;
                return (
                  <path
                    key={`${sample.iteration}-${sample.stream}-${sampleIndex}`}
                    data-sample="true"
                    d={runMarkerPath(run.shapeIndex, x, yFor(sample.durationMs), SAMPLE_RADIUS)}
                    fill={run.color}
                    stroke="var(--bb-surface-data)"
                    stroke-width="1"
                  >
                    <title>{sampleTitle(run, sample)}</title>
                  </path>
                );
              })}
              {summary?.displayValueMs != null && (
                <path
                  d={selectedDiamondPath(centerX, yFor(summary.displayValueMs), SELECTED_DIAMOND_RADIUS)}
                  fill="none"
                  stroke="var(--bb-data-fg-primary)"
                  stroke-width="2"
                >
                  <title>{`${run.label} selected basis value: ${latency(summary.displayValueMs)}`}</title>
                </path>
              )}
              {samples.length === 0 && (
                <text x={centerX} y={padding.top + plotHeight / 2} text-anchor="middle" font-size="11" fill="var(--bb-chart-label-muted)">
                  No data
                </text>
              )}
              <text x={centerX} y={height - 40} text-anchor="middle" font-size="11" fill="var(--bb-chart-axis)">
                {labels[index]}
                <title>{run.label}</title>
              </text>
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
          {axisScaleCaption(axis)}
        </text>
        <text x={padding.left + plotWidth / 2} y={height - 6} text-anchor="middle" font-size="10" fill="var(--bb-chart-label-muted)">
          Selected runs in the same order as the table
        </text>
      </svg>
    </div>
  );
}

function RowChart({ queryId, runs, values }: { queryId: string; runs: ChartRun[]; values: number[] }) {
  const width = 360;
  const rowHeight = 46;
  const padding = { top: 10, right: 16, bottom: 46, left: 126 };
  const plotWidth = width - padding.left - padding.right;
  const height = padding.top + runs.length * rowHeight + padding.bottom;
  const plotBottom = padding.top + runs.length * rowHeight;
  const axis = buildValueAxis(values, plotWidth);
  const xFor = (value: number) => padding.left + plotWidth * valueAxisFraction(axis, value);
  const labels = preserveUniqueAfterTruncation(runs.map((run) => run.label), 18);

  return (
    <div data-testid="query-run-comparison-chart-scroll">
      <svg
        class="bb-chart-svg"
        width="100%"
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        data-layout="rows"
        aria-labelledby="query-run-comparison-chart-title query-run-comparison-chart-description"
        data-testid="query-run-comparison-chart"
      >
        <title id="query-run-comparison-chart-title">{queryId} successful measurement execution times</title>
        <desc id="query-run-comparison-chart-description">
          Each run is a row showing all valid successful executions as markers, an observed range for the selected basis, and a hollow diamond at the selected basis value. Runs are in the same order as the table below.
        </desc>
        {axis.ticks.map((tick) => {
          const x = xFor(tick.value);
          return (
            <g key={tick.value} aria-hidden="true">
              <line x1={x} y1={padding.top} x2={x} y2={plotBottom} stroke="var(--bb-chart-grid)" stroke-width="1" />
              <text x={x} y={plotBottom + 16} text-anchor={axisLabelAnchor(x, width)} font-size="11" fill="var(--bb-chart-axis)">
                {tick.label}
              </text>
            </g>
          );
        })}
        {runs.map((run, index) => {
          const rowY = padding.top + rowHeight * (index + 0.5);
          const samples = run.summary?.samples ?? [];
          const maxJitter = Math.min(12, rowHeight * 0.3);
          const jitterStep = samples.length > 1 ? (maxJitter * 2) / (samples.length - 1) : 0;
          const summary = run.summary;
          const hasRange = samples.length > 1 && summary?.displayMinMs != null && summary.displayMaxMs != null;
          return (
            <g key={`${run.label}-${index}`}>
              <text x={padding.left - 10} y={rowY + 4} text-anchor="end" font-size="11" fill="var(--bb-data-fg-primary)">
                {labels[index]}
                <title>{run.label}</title>
              </text>
              {hasRange && (
                <g aria-hidden="true">
                  <line x1={xFor(summary.displayMinMs!)} y1={rowY} x2={xFor(summary.displayMaxMs!)} y2={rowY} stroke={run.color} stroke-width="2" opacity="0.55" />
                  <line x1={xFor(summary.displayMinMs!)} y1={rowY - 5} x2={xFor(summary.displayMinMs!)} y2={rowY + 5} stroke={run.color} stroke-width="2" />
                  <line x1={xFor(summary.displayMaxMs!)} y1={rowY - 5} x2={xFor(summary.displayMaxMs!)} y2={rowY + 5} stroke={run.color} stroke-width="2" />
                </g>
              )}
              {samples.map((sample, sampleIndex) => {
                const y = samples.length > 1 ? rowY - maxJitter + jitterStep * sampleIndex : rowY;
                return (
                  <path
                    key={`${sample.iteration}-${sample.stream}-${sampleIndex}`}
                    data-sample="true"
                    d={runMarkerPath(run.shapeIndex, xFor(sample.durationMs), y, SAMPLE_RADIUS)}
                    fill={run.color}
                    stroke="var(--bb-surface-data)"
                    stroke-width="1"
                  >
                    <title>{sampleTitle(run, sample)}</title>
                  </path>
                );
              })}
              {summary?.displayValueMs != null && (
                <path
                  d={selectedDiamondPath(xFor(summary.displayValueMs), rowY, SELECTED_DIAMOND_RADIUS - 1)}
                  fill="none"
                  stroke="var(--bb-data-fg-primary)"
                  stroke-width="2"
                >
                  <title>{`${run.label} selected basis value: ${latency(summary.displayValueMs)}`}</title>
                </path>
              )}
              {samples.length === 0 && (
                <text x={padding.left + plotWidth / 2} y={rowY + 4} text-anchor="middle" font-size="11" fill="var(--bb-chart-label-muted)">
                  No data
                </text>
              )}
            </g>
          );
        })}
        <text x={padding.left + plotWidth / 2} y={height - 6} text-anchor="middle" font-size="11" fill="var(--bb-chart-axis)">
          {axisScaleCaption(axis)}
        </text>
      </svg>
    </div>
  );
}
