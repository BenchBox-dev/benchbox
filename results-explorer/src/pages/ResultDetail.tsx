import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { TuningSettingsDisclosure } from "@/components/TuningSettingsDisclosure";
import type { RoutableProps } from "preact-router";
import type { DetailResult, QueryDisplayTiming, QueryTiming, SortState } from "@/types";
import type { ChartContext } from "@/lib/chartRegistry";
import { getDetailResult, getPrimaryMetricForBenchmark, resolveShortId } from "@/lib/duckdbQueries";
import { humanizeBenchmark, errMsg, fmtGeomean, fmtScoreCompact, fmtScoreExact } from "@/utils";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Breadcrumb } from "@/components/Breadcrumb";
import { TrustBadge, ValidationBadge } from "@/components/TrustBadge";
import { FundingChip } from "@/components/FundingChip";
import { ProvenanceLegend } from "@/components/ProvenanceLegend";
import { PassStrip, summarizeQueryPasses } from "@/components/PassStrip";
import { resultDetailHref, withinRunCompareHref } from "@/lib/resultLinks";
import { queryRunComparisonHref } from "@/lib/queryRunComparison";
import { encodeBasis, selectComparableBasisPair } from "@/lib/measurementBasis";
import { TableScrollHint } from "@/components/TableScrollHint";
import { TuningBadge } from "@/components/TuningBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { MethodologyDisclosure } from "@/components/MethodologyDisclosure";
import { RunReceipt, planDownloadUrl } from "@/components/RunReceipt";
import { CapturedPlanViewer } from "@/components/CapturedPlanViewer";
import { ChartPanel } from "@/components/ChartPanel";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import {
  canonicalPhase,
  formatEnumLabel,
  formatTrustLabel,
  formatValidationStatus,
  parseOverrideRules,
} from "@/lib/displayLabels";
import {
  primaryMetricHigherIsBetter,
  primaryMetricLabel,
  primaryMetricValue,
  type PrimaryMetric,
} from "@/lib/displayEligibility";
import { formatDurationSeconds, formatLatencyMs, formatScaleFactor } from "@/lib/metricFormatters";
import { visibleResultIdForRow } from "@/lib/resultLinks";
import { RunDateChip } from "@/components/RunAge";
import { PageHeader } from "@/components/PageHeader";
import { useLocalResultState } from "@/lib/localResultState";
import { LocalResultPicker } from "@/components/LocalResultPicker";

interface ResultDetailProps extends RoutableProps {
  resultId?: string;
  source?: "public" | "local";
}

const PASS_STRIP_DETAIL_LIMIT = 200;

type MedianSortKey = "query_id" | "display_ms" | "sample_count";
type RawSortKey = "query_id" | "duration_ms" | "status";
type SortAriaValue = "ascending" | "descending" | "none";
interface DetailState {
  detail: DetailResult;
  primaryMetric: PrimaryMetric;
}

export function ResultDetail({ resultId = "", source = "public" }: ResultDetailProps) {
  const timingsScrollerRef = useRef<HTMLDivElement>(null);
  const samplesScrollerRef = useRef<HTMLDivElement>(null);
  const [detailState, setDetailState] = useState<DetailState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailRetryToken, setDetailRetryToken] = useState(0);
  const [sort, setSort] = useState<SortState<MedianSortKey>>({
    key: "query_id",
    direction: "asc",
  });
  const [rawSort, setRawSort] = useState<SortState<RawSortKey>>({
    key: "query_id",
    direction: "asc",
  });
  const localResultState = useLocalResultState();
  const isLocal = source === "local";
  const detail = detailState?.detail ?? null;
  const primaryMetric = detailState?.primaryMetric ?? "display_geomean_ms";
  const documentTitle = detail
    ? `${humanizeBenchmark(detail.benchmark)} · ${detail.platform} · ${formatScaleFactor(detail.scale_factor)} · BenchBox Results`
    : "Result · BenchBox Results";
  useDocumentTitle(documentTitle);

  useEffect(() => {
    if (!resultId) {
      setError("No result ID provided.");
      return;
    }
    setDetailState(null);
    setError(null);
    let cancelled = false;
    if (isLocal) {
      const preview = localResultState.preview;
      if (preview?.detail.result_id !== resultId) {
        setError("This local preview is no longer available. Open the result file again to restore it.");
      } else {
        setDetailState({ detail: preview.detail, primaryMetric: preview.primaryMetric });
      }
      return () => {
        cancelled = true;
      };
    }
    resolveShortId(resultId)
      .then(async (resolvedId) => {
        if (cancelled) return null;
        if (resolvedId !== resultId && typeof window !== "undefined") {
          history.replaceState(null, "", `${resultDetailHref(resolvedId)}${window.location.search}${window.location.hash}`);
        }
        return getDetailResult(resolvedId);
      })
      .then(async (data) => {
        if (cancelled) return;
        if (data === null) {
          setError(`No result found for "${resultId}".`);
          return;
        }
        const metric = await getPrimaryMetricForBenchmark(data.benchmark, canonicalPhase(data.test_type));
        if (cancelled) return;
        setDetailState({ detail: data, primaryMetric: metric });
      })
      .catch((err: unknown) => { if (!cancelled) setError(errMsg(err)); });
    return () => {
      cancelled = true;
    };
  }, [isLocal, localResultState.preview, resultId, detailRetryToken]);

  const chartContext = useMemo<ChartContext | null>(
    () => (detail ? { kind: "detail" as const, detail, primaryMetric } : null),
    [detail, primaryMetric],
  );

  const sortedMedians = useMemo<QueryDisplayTiming[]>(
    () => {
      if (!detail) return [];
      return [...detail.display_timings].sort((a, b) => {
        const dir = sort.direction === "asc" ? 1 : -1;
        if (sort.key === "query_id") return dir * a.query_id.localeCompare(b.query_id);
        if (sort.key === "display_ms") {
          const av = a.display_ms ?? Infinity;
          const bv = b.display_ms ?? Infinity;
          return dir * (av - bv);
        }
        if (sort.key === "sample_count") return dir * (a.sample_count - b.sample_count);
        return 0;
      });
    },
    [detail, sort],
  );

  const sortedRawQueries = useMemo<QueryTiming[]>(
    () => {
      if (!detail) return [];
      return [...detail.queries].sort((a, b) => {
        const dir = rawSort.direction === "asc" ? 1 : -1;
        if (rawSort.key === "query_id") return dir * a.query_id.localeCompare(b.query_id);
        if (rawSort.key === "duration_ms") return dir * (a.duration_ms - b.duration_ms);
        if (rawSort.key === "status") return dir * a.status.localeCompare(b.status);
        return 0;
      });
    },
    [detail, rawSort],
  );

  if (error) {
    return (
      <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <Breadcrumb crumbs={[{ label: "Results", href: "/results/" }, { label: isLocal ? "Local preview" : "Result detail" }]} />
        <div class="mt-8">
          <ErrorMessage message={error} onRetry={() => setDetailRetryToken((t) => t + 1)} />
          <div class="mt-4 flex flex-wrap gap-2">
            {isLocal && <LocalResultPicker label="Open result file again" />}
            <a href="/results/query" class="btn btn-primary no-underline">Find runs</a>
            <a href="/results/benchmarks/" class="btn btn-secondary no-underline">Browse benchmarks</a>
          </div>
        </div>
      </div>
    );
  }
  if (!detail || !chartContext) return <LoadingSpinner message="Loading result..." />;

  const benchmarkLabel = humanizeBenchmark(detail.benchmark);
  const detailOverrideRules = parseOverrideRules(detail.override_rules);

  function toggleSort(key: MedianSortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, direction: prev.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    );
  }

  function sortArrow(key: MedianSortKey) {
    if (sort.key !== key) return " ↕";
    return sort.direction === "asc" ? " ↑" : " ↓";
  }

  function sortAriaValue(state: SortState<MedianSortKey>, key: MedianSortKey): SortAriaValue {
    if (state.key !== key) return "none";
    return state.direction === "asc" ? "ascending" : "descending";
  }

  function toggleRawSort(key: RawSortKey) {
    setRawSort((prev) =>
      prev.key === key
        ? { key, direction: prev.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    );
  }

  function rawSortArrow(key: RawSortKey) {
    if (rawSort.key !== key) return " ↕";
    return rawSort.direction === "asc" ? " ↑" : " ↓";
  }

  function rawSortAriaValue(key: RawSortKey): SortAriaValue {
    if (rawSort.key !== key) return "none";
    return rawSort.direction === "asc" ? "ascending" : "descending";
  }

  const passSummaries = summarizeQueryPasses(detail.queries);
  const passQueryIds = new Set(passSummaries.map((summary) => summary.queryId));
  const passesCoverAllTimings =
    passSummaries.length > 0 &&
    detail.display_timings.every((timing) => passQueryIds.has(timing.query_id));
  const plansUrl = isLocal ? null : planDownloadUrl(detail);
  const hasTimings = detail.display_timings.length > 0 || detail.queries.length > 0;
  const withinRunBases = selectComparableBasisPair(detail.queries, detail.display_timings);
  const primaryMetricRaw = primaryMetricValue(detail, primaryMetric);
  const hasPrimaryMetric = primaryMetricRaw !== null && primaryMetricRaw !== undefined;
  const primaryMetricDirection = primaryMetricHigherIsBetter(primaryMetric) ? "higher is better" : "lower is better";
  const primaryMetricName = primaryMetricLabel(primaryMetric);
  const primaryMetricExactTitle =
    primaryMetric !== "display_geomean_ms" && hasPrimaryMetric
      ? `Exact ${primaryMetricName.toLowerCase()}: ${fmtScoreExact(primaryMetricRaw)}`
      : undefined;

  return (
    <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      {isLocal && (
        <aside
          role="status"
          class="mb-6 rounded-lg border border-[var(--bb-data-border-strong)] bg-[var(--bb-tone-info-bg)] p-4 text-sm text-[var(--bb-tone-info-fg)]"
          data-testid="local-result-banner"
          aria-label="Local result preview"
        >
          <p class="font-semibold">Local preview</p>
          <p class="mt-1">
            Viewing <span class="font-medium">{localResultState.preview?.fileName}</span> in this browser tab. This result
            has not been uploaded, reviewed, or added to the public rankings.
          </p>
        </aside>
      )}

      <PageHeader
        crumbs={isLocal
          ? [{ label: "Results", href: "/results/" }, { label: "Local preview" }, { label: detail.platform }]
          : [
              { label: "Results", href: "/results/" },
              { label: benchmarkLabel, href: `/results/${detail.benchmark}/` },
              { label: detail.platform },
            ]}
        eyebrow="Run"
        title={`${benchmarkLabel} result: ${detail.platform}`}
        subtitle={
          <>
            Scale factor {detail.scale_factor}, {detail.test_type ? formatEnumLabel(detail.test_type) : "standard"} phase.
          </>
        }
        meta={
          <>
            <RunDateChip runDate={detail.run_date} />
            <span class="bb-meta-chip">
              {isLocal ? "Local preview ID" : "Public ID"} {visibleResultIdForRow(detail)}
            </span>
            <TrustBadge trustLabel={detail.trust_label} />
            <FundingChip funding={detail.funding} />
            {(!isPassingValidationStatus(detail.validation_status) || detailOverrideRules.length > 0) && (
              <ValidationBadge
                validationStatus={detail.validation_status}
                overrideRules={detailOverrideRules}
                showMissing
              />
            )}
            {detail.tuning_mode && (
              <TuningBadge
                tuningMode={detail.tuning_mode}
                tuningValidationStatus={detail.tuning_validation_status}
              />
            )}
            {detail.visibility === "public-curated" && (
              <StatusBadge role="visibility" tone="success">Published</StatusBadge>
            )}
          </>
        }
        actions={
          <div class="flex flex-wrap gap-2">
            {isLocal ? (
              <>
                <LocalResultPicker label="Open another result" />
                <a
                  href="/docs/contributing-results.html"
                  referrerPolicy="no-referrer"
                  class="btn btn-primary no-underline"
                >
                  Submit for public review
                </a>
              </>
            ) : (
              <>
                <a href={`/results/query?pick=${encodeURIComponent(detail.result_id)}`} class="btn btn-primary" data-testid="result-detail-compare-link">
                  Find a run to compare
                </a>
                <a href={detail.bundle_download_url} class="btn btn-secondary" download>
                  Download bundle
                </a>
                {detail.has_plans && plansUrl && (
                  <a href={plansUrl} class="btn btn-secondary" download>
                    Download plans
                  </a>
                )}
              </>
            )}
          </div>
        }
      />

      <section aria-label="Result summary" class="mb-8">
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-[repeat(auto-fit,minmax(12rem,1fr))]">
          {hasPrimaryMetric && (
            <ResultMetricCard
              label={`Primary metric · ${primaryMetricDirection}`}
              value={formatPrimaryMetric(detail, primaryMetric)}
              helper={primaryMetricName}
              valueTitle={primaryMetricExactTitle}
            />
          )}
          <ResultMetricCard
            label="Scale factor"
            value={formatScaleFactor(detail.scale_factor)}
            helper={`Phase: ${detail.test_type ? formatEnumLabel(detail.test_type) : "Standard"}`}
          />
          <ResultMetricCard
            label="Trust / validation"
            value={formatTrustLabel(detail.trust_label)}
            helper={detail.validation_status ? formatValidationStatus(detail.validation_status) : "validation not recorded"}
          />
          <ResultMetricCard
            label="Wall-clock total"
            value={formatDurationSeconds(detail.total_duration_s).valueText}
            helper="Run duration"
          />
          {detail.has_tuning && detail.bundle_download_url ? (
            <TuningSettingsDisclosure bundleUrl={detail.bundle_download_url} />
          ) : null}
        </div>
      </section>

      <div class="space-y-6">
          {hasTimings && (
            <ChartPanel
              context={chartContext}
              summaryLayout="long"
              excludeChartIds={["performance_bar", "power_bar", "sparkline_table", "rank_table", "query_heatmap"]}
            />
          )}

          {detail.has_plans && <CapturedPlanViewer plansUrl={plansUrl} />}

          <RunReceipt
            detail={detail}
            isRankingEligible={isLocal ? false : null}
            reproduceCommand={isLocal ? null : undefined}
            plansUrl={plansUrl}
          />

          {hasTimings && (
            <section class="card">
            <h2 class="mb-4 text-base font-semibold text-[var(--bb-data-fg-primary)]">
              Query timings ({detail.display_timings.length})
            </h2>
            {!passesCoverAllTimings && (
            <>
            <TableScrollHint scrollerRef={timingsScrollerRef} testId="detail-timings-scroll-hint" />
            <div ref={timingsScrollerRef} class="overflow-x-auto" data-testid="detail-timings-scroll-container">
              <table class="min-w-full w-max divide-y divide-[var(--bb-data-border)]">
                <thead class="bg-[var(--bb-surface-data-muted)]">
                  <tr>
                    <th class="p-0" scope="col" aria-sort={sortAriaValue(sort, "query_id")}>
                      <button
                        type="button"
                        class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                        onClick={() => toggleSort("query_id")}
                      >
                        Query{sortArrow("query_id")}
                      </button>
                    </th>
                    <th class="p-0" scope="col" aria-sort={sortAriaValue(sort, "display_ms")}>
                      <button
                        type="button"
                        class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                        onClick={() => toggleSort("display_ms")}
                      >
                        Median latency{sortArrow("display_ms")}
                      </button>
                    </th>
                    <th class="p-0" scope="col" aria-sort={sortAriaValue(sort, "sample_count")}>
                      <button
                        type="button"
                        class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                        onClick={() => toggleSort("sample_count")}
                      >
                        Samples{sortArrow("sample_count")}
                      </button>
                    </th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
                  {sortedMedians.map((q) => (
                    <MedianRow key={q.query_id} timing={q} resultId={detail.result_id} />
                  ))}
                </tbody>
              </table>
            </div>
            </>
            )}
            {detail.queries.length > 0 && (
              <>
              <PassStrip queries={detail.queries} limit={PASS_STRIP_DETAIL_LIMIT} embedded />
              {!isLocal && withinRunBases !== null && (
                <p class="mb-6 text-sm">
                  <a
                    class="link"
                    href={withinRunCompareHref(detail.result_id, withinRunBases.map(encodeBasis), 0)}
                    data-testid="within-run-compare-link"
                  >
                    Compare measurement bases within this run
                  </a>
                </p>
              )}

              <details class="mt-4">
                <summary class="cursor-pointer select-none text-sm text-[var(--bb-data-fg-muted)] hover:text-[var(--bb-data-fg-primary)]">
                  Individual samples ({detail.queries.length})
                </summary>
                <TableScrollHint
                  scrollerRef={samplesScrollerRef}
                  testId="detail-samples-scroll-hint"
                  wrapperClassName="mt-3 mb-2 flex justify-end"
                />
                <div ref={samplesScrollerRef} class="overflow-x-auto" data-testid="detail-samples-scroll-container">
                  <table class="min-w-full w-max divide-y divide-[var(--bb-data-border)]">
                    <thead class="bg-[var(--bb-surface-data-muted)]">
                      <tr>
                        <th class="p-0" scope="col" aria-sort={rawSortAriaValue("query_id")}>
                          <button
                            type="button"
                            class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                            onClick={() => toggleRawSort("query_id")}
                          >
                            Query{rawSortArrow("query_id")}
                          </button>
                        </th>
                        <th class="p-0" scope="col" aria-sort={rawSortAriaValue("duration_ms")}>
                          <button
                            type="button"
                            class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                            onClick={() => toggleRawSort("duration_ms")}
                          >
                            Duration{rawSortArrow("duration_ms")}
                          </button>
                        </th>
                        <th class="p-0" scope="col" aria-sort={rawSortAriaValue("status")}>
                          <button
                            type="button"
                            class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                            onClick={() => toggleRawSort("status")}
                          >
                            Status{rawSortArrow("status")}
                          </button>
                        </th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
                      {sortedRawQueries.map((q, i) => (
                        <QueryRow key={`${q.query_id}-${i}`} query={q} resultId={detail.result_id} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
              </>
            )}
            </section>
          )}

          <MethodologyDisclosure detail={detail} />

          <ProvenanceLegend />
      </div>
    </div>
  );
}

function ResultMetricCard({
  label,
  value,
  helper,
  valueTitle,
}: {
  label: string;
  value: string;
  helper: string;
  valueTitle?: string;
}) {
  return (
    <div class="rounded-lg panel-muted px-4 py-3">
      <div class="text-xs font-semibold uppercase tracking-wide text-[var(--bb-data-fg-subtle)]">{label}</div>
      <div
        class="mt-1 font-mono text-lg font-semibold text-[var(--bb-data-fg-primary)]"
        title={valueTitle}
        aria-label={valueTitle ? `${value}. ${valueTitle}` : undefined}
      >
        {value}
      </div>
      <div class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">{helper}</div>
    </div>
  );
}

function formatPrimaryMetric(detail: DetailResult, primaryMetric: PrimaryMetric) {
  if (primaryMetric === "display_geomean_ms") return fmtGeomean(detail.display_geomean_ms);
  return fmtScoreCompact(primaryMetricValue(detail, primaryMetric));
}

function isPassingValidationStatus(status: string | null | undefined): boolean {
  const normalized = status?.trim().toLowerCase();
  return normalized === "pass" || normalized === "passed";
}

function MedianRow({ timing, resultId }: { timing: QueryDisplayTiming; resultId: string }) {
  return (
    <tr class="hover:bg-[var(--bb-surface-data-muted)]">
      <td class="table-td font-mono font-medium">
        <a
          href={queryRunComparisonHref(timing.query_id, [resultId])}
          class="text-[var(--bb-accent-hover)] underline"
          aria-label={`Compare ${timing.query_id} executions across runs`}
        >
          {timing.query_id}
        </a>
      </td>
      <td class="table-td font-mono">
        {formatLatencyMs(timing.display_ms).valueText}
      </td>
      <td class="table-td font-mono">{timing.sample_count}</td>
    </tr>
  );
}

function QueryRow({ query, resultId }: { query: QueryTiming; resultId: string }) {
  const tone = query.status === "pass" ? "success" : query.status === "fail" ? "danger" : "neutral";

  return (
    <tr class="hover:bg-[var(--bb-surface-data-muted)]">
      <td class="table-td font-mono font-medium">
        <a
          href={queryRunComparisonHref(query.query_id, [resultId])}
          class="text-[var(--bb-accent-hover)] underline"
          aria-label={`Compare ${query.query_id} executions across runs`}
        >
          {query.query_id}
        </a>
      </td>
      <td class="table-td font-mono">{formatLatencyMs(query.duration_ms).valueText}</td>
      <td class="table-td">
        <StatusBadge role="validation" tone={tone}>{query.status}</StatusBadge>
      </td>
    </tr>
  );
}
