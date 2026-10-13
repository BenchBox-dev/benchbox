import type { JSX } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import type { RoutableProps } from "preact-router";
import type { DetailResult } from "@/types";
import {
  getDetailResult,
  getExistingResultIds,
  getPrimaryMetricForBenchmark,
  resolveShortId,
  toShortIds,
} from "@/lib/duckdbQueries";
import { errMsg, fmtGeomean } from "@/utils";
import { canonicalBenchmarkSlug, canonicalPhase, formatBenchmarkLabel } from "@/lib/displayLabels";
import { compareCohortSignatureForRow } from "@/lib/compareCohort";
import { resultDetailHref, visibleResultIdForRow, MAX_COMPARE_SELECTIONS } from "@/lib/resultLinks";
import { formatRunIdentitiesForCohort, type RunIdentitySource } from "@/lib/runIdentity";
import { CompareSummarySkeleton } from "@/components/LoadingSpinner";
import { ErrorMessage } from "@/components/ErrorMessage";
import { TrustBadge } from "@/components/TrustBadge";
import { FundingChip } from "@/components/FundingChip";
import { TuningBadge } from "@/components/TuningBadge";
import {
  COMPARABILITY_RECEIPT_ID,
  COMPARABILITY_WARNING_TARGET_ID,
  ComparabilityReceipt,
  buildComparabilityFields,
  comparabilityWarningFields,
  orderWarningLabelsForSummary,
} from "@/components/ComparabilityReceipt";
import { CompareSummary } from "@/components/CompareSummary";
import {
  DEFAULT_QUERY_DIFF_LIMIT,
  QueryDiffTable,
  QUERY_DIFF_LIMITER_LABELS,
  type QueryDiffLimiter,
  selectQueryIdsForLimiter,
} from "@/components/QueryDiffTable";
import { modeLabel, testTypeLabel } from "@/components/MethodologyDisclosure";
import { vsSlowestRatio } from "@/lib/chartMath";
import {
  buildCompareDecisionSummary,
  COMPARE_TIE_THRESHOLD,
  type ComparePrimaryMetric,
} from "@/lib/compareSummary";
import { IdentityDiffStrip } from "@/components/IdentityDiffStrip";
import { MultiRunHeatmap } from "@/components/MultiRunHeatmap";
import { MultiRunStandings } from "@/components/MultiRunStandings";
import { MeasurementBasisBar } from "@/components/MeasurementBasisBar";
import { stringSerde, useUrlState, type UrlSerde } from "@/lib/useUrlState";
import { getResultBasisAvailability } from "@/lib/duckdbQueries";
import {
  BASIS_URL_KEY,
  DEFAULT_BASIS,
  basisSerde,
  formatBasisLabel,
  resolvedStatisticsCollapsed,
  isDefaultBasis,
  parseAvailablePassSelections,
  passSelectionsEqual,
  resolveResultsForBasis,
  type PassSelection,
} from "@/lib/measurementBasis";
import { formatDurationSeconds, formatScoreMetric, formatSpeedup } from "@/lib/metricFormatters";
import {
  isValidTimingValue,
  primaryMetricHigherIsBetter,
  primaryMetricLabel,
  primaryMetricValue,
  timingValueForQuery,
} from "@/lib/displayEligibility";
import { formatWarningClassSummary, formatWarningCount } from "@/lib/copyFormatters";
import { paletteColor } from "@/lib/chartTheme";
import { queryRunComparisonHref } from "@/lib/queryRunComparison";
import { ChartPanel } from "@/components/ChartPanel";
import { RunDateChip } from "@/components/RunAge";
import { PageHeader } from "@/components/PageHeader";
import { Leaderboard } from "@/pages/Leaderboard";
import { Select } from "@/components/Select";
import { StatusBadge } from "@/components/StatusBadge";
import { ProvenanceLegend } from "@/components/ProvenanceLegend";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import {
  planCompareIds,
  recoverCompareResults,
  shouldPreserveMultiSelectionUrl,
} from "@/lib/compareRecovery";

interface CompareState {
  results: DetailResult[];
  primaryMetric: ComparePrimaryMetric;
}

const EMPTY_RESULTS: DetailResult[] = [];

interface CompareProps extends RoutableProps {
  url?: string;
}

const QUERY_LIMITER_URL_KEY = "queries";
const BASELINE_URL_KEY = "baseline";
const queryLimiterSerde: UrlSerde<QueryDiffLimiter> = {
  encode: (value) => value,
  decode: (raw) => raw in QUERY_DIFF_LIMITER_LABELS ? raw as QueryDiffLimiter : null,
};

function currentCompareUrl(url: string | undefined): string {
  if (url) return url;
  if (typeof window === "undefined") return "/results/compare";
  return `${window.location.pathname}${window.location.search}`;
}

function searchParamsFromUrl(url: string): URLSearchParams {
  return new URL(url, "https://benchbox.dev").searchParams;
}

function formatIdList(ids: string[]): string {
  return ids.map((id) => `“${id}”`).join(", ");
}

function appendCompareNotice(current: string | null, next: string): string {
  return current ? `${current} ${next}` : next;
}

export type CompareLayout =
  | { readonly kind: "empty" }
  | { readonly kind: "within_run"; readonly resultId: string }
  | { readonly kind: "head_to_head"; readonly runIds: readonly [string, string] }
  | { readonly kind: "multi_run"; readonly runIds: readonly string[] };

export function isWithinTieBand(ratio: number | null): boolean {
  if (ratio === null || !Number.isFinite(ratio)) return false;
  return Math.abs(ratio - 1) < COMPARE_TIE_THRESHOLD;
}

export function shouldShowMultiRunStandings(resultIds: readonly string[], claimSuppressed: boolean): boolean {
  return !claimSuppressed && compareLayoutForSelection(resultIds).kind === "multi_run";
}

export function compareLayoutForSelection(resultIds: readonly string[]): CompareLayout {
  const distinct: string[] = [];
  for (const id of resultIds) {
    if (id && !distinct.includes(id)) distinct.push(id);
  }
  const [first, second] = distinct;
  if (first === undefined) return { kind: "empty" };
  if (second === undefined) return { kind: "within_run", resultId: first };
  if (distinct.length === 2) return { kind: "head_to_head", runIds: [first, second] };
  return { kind: "multi_run", runIds: distinct };
}

export function Compare({ url }: CompareProps) {
  const activeUrl = currentCompareUrl(url);
  const requestedIdsToken = searchParamsFromUrl(activeUrl).get("ids") ?? "";
  const [compareState, setCompareState] = useState<CompareState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [compareRetryToken, setCompareRetryToken] = useState(0);
  const [baselineResultId, setBaselineResultId] = useUrlState(BASELINE_URL_KEY, "", stringSerde);
  const [basis, setBasis] = useUrlState(BASIS_URL_KEY, DEFAULT_BASIS, basisSerde);
  const [queryLimiter, setQueryLimiter] = useUrlState<QueryDiffLimiter>(QUERY_LIMITER_URL_KEY, "all", queryLimiterSerde);
  const [showBuilder, setShowBuilder] = useState(false);
  const [compareNotice, setCompareNotice] = useState<string | null>(null);
  const [preserveRequestedIds, setPreserveRequestedIds] = useState(false);
  const results = compareState?.results ?? EMPTY_RESULTS;
  const [availabilityRows, setAvailabilityRows] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    const ids = results.map((r) => r.result_id);
    if (ids.length === 0) return;
    void Promise.all(ids.map((id) => getResultBasisAvailability(id).catch(() => null))).then((rows) => {
      if (cancelled) return;
      const next: Record<string, string> = {};
      rows.forEach((row, i) => {
        const id = ids[i];
        if (row && id) next[id] = row.available_bases;
      });
      setAvailabilityRows(next);
    });
    return () => {
      cancelled = true;
    };
  }, [results]);

  const availablePasses = useMemo<PassSelection[]>(() => {
    const perRun = results
      .map((r) => availabilityRows[r.result_id])
      .filter((raw): raw is string => typeof raw === "string" && raw.length > 0)
      .map((raw) => parseAvailablePassSelections(raw));
    if (perRun.length === 0) return [DEFAULT_BASIS.passes];
    const [first, ...rest] = perRun;
    return (first ?? []).filter((candidate) =>
      rest.every((other) => other.some((p) => passSelectionsEqual(p, candidate))),
    );
  }, [results, availabilityRows]);
  const primaryMetric = compareState?.primaryMetric ?? "display_geomean_ms";
  const baselineIndex = results.findIndex((result) => result.result_id === baselineResultId);
  const normalizedBaselineIndex = baselineIndex >= 0 ? baselineIndex : 0;
  const resolvedResults = useMemo(
    () => resolveResultsForBasis(results, basis),
    [results, basis],
  );
  const { queryIds: limitedQueryIds } = useMemo(
    () => selectQueryIdsForLimiter(
      resolvedResults,
      normalizedBaselineIndex,
      queryLimiter,
      DEFAULT_QUERY_DIFF_LIMIT,
    ),
    [resolvedResults, normalizedBaselineIndex, queryLimiter],
  );
  useDocumentTitle(
    results.length > 0 ? `Compare (${results.length}) · BenchBox Results` : "Compare · BenchBox Results",
  );
  useEffect(() => {
    let cancelled = false;

    setCompareState(null);
    setError(null);
    setLoading(true);
    setShowBuilder(false);
    setCompareNotice(null);
    setPreserveRequestedIds(false);

    const idsParam = requestedIdsToken;
    const rawIds = idsParam.split(",");
    const plan = planCompareIds(rawIds, rawIds.length);
    let initialNotice: string | null = null;
    if (plan.duplicates.length > 0) {
      initialNotice = `Ignored duplicate result ID${plan.duplicates.length === 1 ? "" : "s"}: ${formatIdList(plan.duplicates)}.`;
    }
    if (initialNotice) setCompareNotice(initialNotice);
    const ids = plan.retained;

    if (ids.length === 0) {
      setShowBuilder(true);
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    if (ids.length === 1) {
      resolveShortId(ids[0]!)
        .then(async (resolvedId) => {
          if (cancelled) return;
          const detail = await getDetailResult(resolvedId);
          if (cancelled) return;
          if (detail === null) {
            setError(
              `No result found for: ${resolvedId}. ` +
                "These results may have been removed from the published dataset.",
            );
            setLoading(false);
            return;
          }
          setCompareState({ results: [detail], primaryMetric: "display_geomean_ms" });
          setShowBuilder(true);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            setError(errMsg(err));
            setLoading(false);
          }
        });
      return () => {
        cancelled = true;
      };
    }

    recoverCompareResults(ids, MAX_COMPARE_SELECTIONS, {
      resolveId: resolveShortId,
      findExistingIds: getExistingResultIds,
      loadResult: getDetailResult,
      isCancelled: () => cancelled,
    })
      .then(async (recovery) => {
        if (cancelled || recovery === null) return;

        if (recovery.aliases.length > 0) {
          const aliasNotice = `Ignored duplicate result ID${recovery.aliases.length === 1 ? "" : "s"} after alias resolution: ${formatIdList(recovery.aliases)}.`;
          initialNotice = appendCompareNotice(initialNotice, aliasNotice);
          setCompareNotice(initialNotice);
        }

        if (recovery.overflow.length > 0) {
          const aliasResolutionNote = recovery.aliases.length > 0 ? " after alias resolution" : "";
          initialNotice = appendCompareNotice(
            initialNotice,
            `Ignored ${recovery.overflow.length} additional result ID${recovery.overflow.length === 1 ? "" : "s"}${aliasResolutionNote} (${formatIdList(recovery.overflow)}); comparisons are limited to ${MAX_COMPARE_SELECTIONS} unique results.`,
          );
          setCompareNotice(initialNotice);
        }
        if (recovery.unprocessed.length > 0) {
          initialNotice = appendCompareNotice(
            initialNotice,
            `Did not process ${recovery.unprocessed.length} additional result ID${recovery.unprocessed.length === 1 ? "" : "s"} to keep this page responsive; comparisons are limited to ${MAX_COMPARE_SELECTIONS} unique results.`,
          );
          setCompareNotice(initialNotice);
        }
        const details = recovery.recovered.map((entry) => entry.detail);
        if (recovery.failed.length > 0) setPreserveRequestedIds(true);
        if (details.length === 0) {
          if (recovery.failed.length > 0) {
            setError(errMsg(recovery.failed[0]!.error));
          } else {
            const missingIds = recovery.missing.map((entry) => entry.requestedId);
            setError(
              `No result found for: ${missingIds.join(", ")}. ` +
                "These results may have been removed from the published dataset.",
            );
          }
          setLoading(false);
          return;
        }
        if (recovery.missing.length > 0 || recovery.failed.length > 0) {
          const unavailable = [
            ...recovery.missing.map((entry) => entry.requestedId),
            ...recovery.failed.map((entry) => entry.requestedId),
          ];
          initialNotice = appendCompareNotice(
            initialNotice,
            `Ignored unavailable result ID${unavailable.length === 1 ? "" : "s"}: ${formatIdList(unavailable)}.`,
          );
          setCompareNotice(initialNotice);
        }

        const metric = await getPrimaryMetricForBenchmark(details[0]!.benchmark, canonicalPhase(details[0]!.test_type));
        if (cancelled) return;
        if (details.length === 1) {
          setShowBuilder(true);
        } else {
          setShowBuilder(false);
        }
        setCompareState({ results: details, primaryMetric: metric });
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(errMsg(err));
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [requestedIdsToken, compareRetryToken]);

  useEffect(() => {
    if (!baselineResultId || results.length === 0) return;
    if (results.some((result) => result.result_id === baselineResultId)) return;
    setBaselineResultId("");
  }, [baselineResultId, results, setBaselineResultId]);

  useEffect(() => {
    const ids = results.map((r) => r.result_id);
    if (ids.length === 0 || typeof window === "undefined") return;
    if (preserveRequestedIds) return;
    const currentParams = new URLSearchParams(window.location.search);
    const currentRawIds = currentParams.get("ids") ?? "";
    if (
      shouldPreserveMultiSelectionUrl(
        currentRawIds.split(","),
        ids.length,
        MAX_COMPARE_SELECTIONS,
      )
    ) {
      setCompareNotice((current) =>
        appendCompareNotice(current, "The original comparison URL is kept so refresh preserves this recovery state."),
      );
      return;
    }
    toShortIds(ids)
      .then((shortIds) => {
        if (shortIds.length !== ids.length || new Set(shortIds).size !== ids.length) {
          setCompareNotice((current) =>
            appendCompareNotice(
              current,
              "The comparison URL could not be canonicalized; retained result order is unchanged.",
            ),
          );
          return;
        }
        const params = new URLSearchParams(window.location.search);
        const currentIds = params.get("ids") ?? "";
        const canonicalIds = shortIds.join(",");
        if (currentIds === canonicalIds) return;
        params.set("ids", canonicalIds);
        const search = params.toString();
        history.replaceState(null, "", `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`);
      })
      .catch(() => {
        setCompareNotice((current) =>
          appendCompareNotice(
            current,
            "The comparison URL could not be canonicalized; sharing it will retry when the dataset is available.",
          ),
        );
      });
  }, [preserveRequestedIds, results]);

  if (loading) return <CompareSummarySkeleton message="Loading results for comparison..." />;
  if (error)
    return (
      <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <ErrorMessage title="Cannot compare" message={error} onRetry={() => setCompareRetryToken((t) => t + 1)} />
        <a href="/results/" class="mt-4 inline-block text-sm no-underline">
          ← Back to Results
        </a>
      </div>
    );

  if (showBuilder) {
    return <>
      {results.length === 1 && <div class="mx-auto max-w-7xl px-4 pt-6 sm:px-6 lg:px-8" role="status">
        <p>Selected run: {results[0]!.platform} · {visibleResultIdForRow(results[0]!)}</p>
        <a class="btn btn-primary mt-2" href={`/results/query?pick=${encodeURIComponent(results[0]!.result_id)}`}>Find runs to compare with this run</a>
      </div>}
      <Leaderboard notice={compareNotice} />
    </>;
  }

  if (results.length === 0) return null;

  const benchmark = results[0]?.benchmark ?? "";
  const scaleFactor = results[0]?.scale_factor ?? 0;
  const severeMismatchReason = severeCohortMismatchReason(results);
  const comparabilityFields = buildComparabilityFields(results);
  const comparabilityWarnings = comparabilityWarningFields(comparabilityFields);
  const comparabilityWarningCount = comparabilityWarnings.length;
  const comparabilityWarningLabels = orderWarningLabelsForSummary(comparabilityWarnings);
  const canonicalBenchmark = canonicalBenchmarkSlug(benchmark);
  const mixedBenchmark = new Set(results.map((result) => canonicalBenchmarkSlug(result.benchmark))).size > 1;
  const benchmarkLabel = mixedBenchmark ? "Mixed Benchmark" : formatBenchmarkLabel(canonicalBenchmark);
  const scaleFactorLabel = new Set(results.map((result) => result.scale_factor)).size > 1
    ? "Mixed scale factors"
    : `SF ${scaleFactor}`;
  const rowCount = results.length;

  const queryCoverage = useMemo(() => {
    const allQueryIds = new Set<string>();
    for (const result of resolvedResults) {
      for (const timing of result.display_timings) allQueryIds.add(timing.query_id);
    }
    let shared = 0;
    for (const queryId of allQueryIds) {
      if (resolvedResults.every((result) => timingValueForQuery(result, queryId) !== null)) shared += 1;
    }
    return { shared, total: allQueryIds.size };
  }, [resolvedResults]);

  const effectivePrimaryMetric: ComparePrimaryMetric =
    primaryMetric !== "display_geomean_ms" && !isDefaultBasis(basis)
      ? "display_geomean_ms"
      : (primaryMetric as ComparePrimaryMetric);

  const higherIsBetter = primaryMetricHigherIsBetter(effectivePrimaryMetric);

  const primaries: (number | null)[] = resolvedResults.map((r) => primaryMetricValue(r, effectivePrimaryMetric));
  const summaryRunLabels: Record<string, string> = (() => {
    const labels = formatRunIdentitiesForCohort(
      results.map((r) => ({
        result_id: r.result_id,
        platform: r.platform,
        platform_version: r.platform_version,
        driver_version: r.driver_version,
        run_date: r.run_date,
        scale_factor: r.scale_factor,
        trust_label: r.trust_label,
      })),
      "compact",
    );
    const out: Record<string, string> = {};
    results.forEach((r, i) => {
      out[r.result_id] = labels[i] ?? r.platform;
    });
    return out;
  })();
  const decisionSummary = buildCompareDecisionSummary(resolvedResults, effectivePrimaryMetric, {
    suppressWinnerClaims: severeMismatchReason !== null,
    suppressionReason: severeMismatchReason ?? undefined,
    runLabels: summaryRunLabels,
    comparisonBoundary: buildComparisonBoundary(comparabilityFields),
  });

  const validPrimaries = primaries.filter(isValidTimingValue);
  const fastestPrimary =
    validPrimaries.length > 0 ? (higherIsBetter ? Math.max(...validPrimaries) : Math.min(...validPrimaries)) : null;
  const slowestPrimary =
    validPrimaries.length > 0 ? (higherIsBetter ? Math.min(...validPrimaries) : Math.max(...validPrimaries)) : null;
  const selectionRatio =
    fastestPrimary !== null && slowestPrimary !== null
      ? higherIsBetter
        ? fastestPrimary / slowestPrimary
        : slowestPrimary / fastestPrimary
      : null;
  const selectionIsTie = isWithinTieBand(selectionRatio);

  const identitySources: RunIdentitySource[] = results.map((r) => ({
    result_id: r.result_id,
    platform: r.platform,
    platform_version: r.platform_version,
    driver_version: r.driver_version,
    run_date: r.run_date,
    scale_factor: r.scale_factor,
    trust_label: r.trust_label,
  }));
  const cohortIdentities = formatRunIdentitiesForCohort(identitySources, "table");
  const cohortIdentitiesCompact = formatRunIdentitiesForCohort(identitySources, "compact");

  const rowData = resolvedResults.map((r, idx) => ({
    resultId: r.result_id,
    publicId: visibleResultIdForRow(r),
    label: cohortIdentities[idx]!,
    trustLabel: r.trust_label,
    funding: r.funding,
    runDate: r.run_date,
    tuningMode: r.tuning_mode,
    tuningValidationStatus: r.tuning_validation_status,
    executionMode: r.execution_mode,
    testType: r.test_type,
    primaryScore: primaryMetricValue(r, effectivePrimaryMetric),
    displayGeomeanMs: r.display_geomean_ms,
    totalDurationS: r.total_duration_s,
    driverVersion: r.driver_version,
  }));
  const isMultiRun = compareLayoutForSelection(results.map((r) => r.result_id)).kind === "multi_run";

  return (
    <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        crumbs={
          mixedBenchmark
            ? [{ label: "Results", href: "/results/" }, { label: "Compare" }]
            : [
                { label: "Results", href: "/results/" },
                { label: benchmarkLabel, href: `/results/${benchmark}/` },
                { label: "Compare" },
              ]
        }
        eyebrow="Compare"
        title={`${benchmarkLabel} Comparison`}
        meta={
          <>
            <span class="bb-meta-chip">{rowCount} runs</span>
            <span class="bb-meta-chip">SF {scaleFactorLabel.replace(/^SF\s*/, "")}</span>
            {(() => {
              const tiers = [...new Set(rowData.map((r) => r.trustLabel))];
              return tiers.length > 1 ? (
                <span class="bb-meta-chip">Across trust tiers: {tiers.join(", ")}</span>
              ) : null;
            })()}
          </>
        }

      />

      {compareNotice && (
        <div
          class="mb-6 rounded-md border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-4 py-3 text-sm text-[var(--bb-data-fg-muted)]"
          role="status"
          data-testid="compare-url-notice"
        >
          {compareNotice}
        </div>
      )}

      <CompareGuardrailSummary
        warningCount={comparabilityWarningCount}
        warningLabels={comparabilityWarningLabels}
        claimSuppressed={decisionSummary.claimSuppressed}
        suppressionReason={decisionSummary.claimSuppressionReason}
        comparisonBoundary={decisionSummary.comparisonBoundary}
      />
      <CompareSummary summary={decisionSummary} />
      <IdentityDiffStrip
        results={results}
        baselineIndex={normalizedBaselineIndex}
        runLabels={cohortIdentitiesCompact}
      />
      {results.length > 1 && (
        <div
          class="panel mb-4 grid gap-x-6 gap-y-4 px-4 py-3 shadow-sm lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]"
          data-testid="compare-controls"
        >
          <MeasurementBasisBar
            layout="card"
            basis={basis}
            onBasisChange={setBasis}
            availablePasses={availablePasses}
            comparableQueryCount={queryCoverage.shared}
            totalQueryCount={queryCoverage.total}
            runCount={results.length}
            statisticCollapsed={resolvedStatisticsCollapsed(resolvedResults)}
          />

          <div class="min-w-0">
            <label class="text-sm font-medium text-[var(--bb-data-fg-primary)]" for="compare-baseline">
              Baseline
            </label>
            <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">
              Ratios and differences compare every other selected run with this run.
            </p>
            <div class="mt-2">
              <Select
                id="compare-baseline"
                ariaLabel="Baseline"
                value={results[normalizedBaselineIndex]?.result_id ?? ""}
                onChange={setBaselineResultId}
                options={results.map((result, index) => ({
                  value: result.result_id,
                  label: cohortIdentitiesCompact[index]!,
                }))}
                size="sm"
              />
            </div>
          </div>

          <div class="min-w-0">
            <label class="text-sm font-medium text-[var(--bb-data-fg-primary)]" for="query-limiter">
              Queries shown
            </label>
            <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">
              Applies to the chart and the table together.
            </p>
            <div class="mt-2">
              <Select
                id="query-limiter"
                ariaLabel="Queries shown"
                size="sm"
                value={queryLimiter}
                onChange={(value) => setQueryLimiter(value as QueryDiffLimiter)}
                options={(Object.keys(QUERY_DIFF_LIMITER_LABELS) as QueryDiffLimiter[]).map((key) => ({
                  value: key,
                  label: QUERY_DIFF_LIMITER_LABELS[key],
                }))}
              />
            </div>
          </div>
        </div>
      )}

      {rowCount > 0 && (
        <div class="mb-8">
          <ChartPanel
            context={{ kind: "compare", results: resolvedResults, primaryMetric: effectivePrimaryMetric }}
            summaryLayout="long"
            baselineIndex={normalizedBaselineIndex}
            onBaselineIndexChange={(index) => setBaselineResultId(results[index]?.result_id ?? "")}
            suppressWinnerClaims={decisionSummary.claimSuppressed}
            suppressionReason={decisionSummary.claimSuppressionReason ?? undefined}
            queryFilter={queryLimiter === "all" ? undefined : limitedQueryIds}
            queryHref={(queryId) => queryRunComparisonHref(queryId, results.map((result) => result.result_id), basis)}
            excludeChartIds={["performance_bar", "power_bar", "comparison_bar"]}
          />
        </div>
      )}

      <div class="mb-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {rowData.map((r, i) => {
          const color = paletteColor(i);
          const primary = primaries[i] ?? null;
          const speedup = higherIsBetter
            ? primary !== null && slowestPrimary !== null && slowestPrimary > 0
              ? primary / slowestPrimary
              : null
            : vsSlowestRatio(primary, slowestPrimary);
          const vsLabel = higherIsBetter ? "Compared with lowest selected score" : "Compared with slowest selected run";
          const showPrimaryClaims = !decisionSummary.claimSuppressed;
          const isComparisonBaseline =
            showPrimaryClaims && !selectionIsTie && primary !== null && primary === slowestPrimary;
          const isFastest =
            showPrimaryClaims && primary !== null && fastestPrimary !== null && primary === fastestPrimary;

          return (
            <div
              key={r.resultId}
              class="card relative overflow-hidden"
              style={{ borderTopColor: color, borderTopWidth: "3px" }}
            >
              <div class="mb-2 flex items-center justify-between">
                {isMultiRun ? (
                  <label class="flex items-center gap-1.5 cursor-pointer text-xs font-medium text-[var(--bb-data-fg-primary)]">
                    <input
                      type="radio"
                      name="compare-baseline-radio"
                      checked={i === normalizedBaselineIndex}
                      onChange={() => setBaselineResultId(r.resultId)}
                      aria-label={`Set ${r.label} as baseline`}
                      data-testid={`baseline-radio-${r.resultId}`}
                    />
                    <span class="font-semibold">{r.label}</span>
                  </label>
                ) : (
                  <span class="font-semibold text-[var(--bb-data-fg-primary)]">{r.label}</span>
                )}
                <div class="flex flex-wrap gap-1">
                  <TrustBadge trustLabel={r.trustLabel} compact />
                  <FundingChip funding={r.funding} compact />
                  {r.tuningMode && (
                    <TuningBadge
                      tuningMode={r.tuningMode}
                      tuningValidationStatus={r.tuningValidationStatus}
                    />
                  )}
                  {isFastest && <StatusBadge role="ranking" tone="success">fastest</StatusBadge>}
                </div>
              </div>
              <p class="mb-3 text-xs text-[var(--bb-data-fg-muted)]">
                <RunDateChip runDate={r.runDate} />
                {r.driverVersion && !r.label.includes(`v${r.driverVersion}`) && ` · v${r.driverVersion}`}
              </p>
              <p class="mb-3 font-mono text-xs text-[var(--bb-data-fg-muted)]">Public ID {r.publicId}</p>
              <dl class="space-y-1 text-sm">
                <div class="flex justify-between">
                  <dt class="text-[var(--bb-data-fg-muted)]">
                    {primaryMetricLabel(effectivePrimaryMetric)}
                  </dt>
                  <dd class="font-mono font-medium">
                    {effectivePrimaryMetric !== "display_geomean_ms"
                      ? r.primaryScore !== null
                        ? formatScoreMetric(effectivePrimaryMetric, r.primaryScore)
                        : "-"
                      : fmtGeomean(r.displayGeomeanMs)}
                  </dd>
                </div>
                {effectivePrimaryMetric !== "display_geomean_ms" && (
                  <div class="flex justify-between">
                    <dt class="text-xs text-[var(--bb-data-fg-muted)]">Geomean</dt>
                    <dd class="font-mono text-xs text-[var(--bb-data-fg-muted)]">{fmtGeomean(r.displayGeomeanMs)}</dd>
                  </div>
                )}
                {r.totalDurationS !== null && (
                  <div class="flex justify-between">
                    <dt class="text-[var(--bb-data-fg-muted)]">Wall-clock total</dt>
                    <dd class="font-mono text-[var(--bb-data-fg-primary)]">
                      {formatDurationSeconds(r.totalDurationS).valueText}
                    </dd>
                  </div>
                )}
                {showPrimaryClaims && speedup !== null && (
                  <div class="flex justify-between">
                    <dt class="text-[var(--bb-data-fg-muted)]">
                      {isComparisonBaseline ? "Relative position" : vsLabel}
                    </dt>
                    {isComparisonBaseline ? (
                      <dd class="text-[var(--bb-data-fg-muted)]">
                        {higherIsBetter ? "Lowest selected" : "Slowest selected"}
                      </dd>
                    ) : isWithinTieBand(speedup) ? (
                      <dd class="text-[var(--bb-data-fg-muted)]" data-testid="headline-tie">
                        Tied
                      </dd>
                    ) : (
                      <dd class="font-mono">{formatSpeedup(speedup).valueText}</dd>
                    )}
                  </div>
                )}
                {r.executionMode && (
                  <div class="flex justify-between">
                    <dt class="text-[var(--bb-data-fg-muted)]">Mode</dt>
                    <dd class="text-[var(--bb-data-fg-primary)]">{modeLabel(r.executionMode)}</dd>
                  </div>
                )}
                {r.testType && (
                  <div class="flex justify-between">
                    <dt class="text-[var(--bb-data-fg-muted)]">Test type</dt>
                    <dd class="text-[var(--bb-data-fg-primary)]">{testTypeLabel(r.testType)}</dd>
                  </div>
                )}
              </dl>
              <a
                href={resultDetailHref(r.resultId)}
                class="mt-3 block text-xs no-underline text-[var(--bb-data-fg-muted)] hover:text-[var(--bb-accent-hover)]"
              >
                View detail →
              </a>
            </div>
          );
        })}
      </div>

      <p class="mb-6 text-xs text-[var(--bb-data-fg-subtle)]">
        <strong>Geomean query time</strong> - geometric mean of per-query median execution times ({isDefaultBasis(basis) ? "measurement runs only" : `${formatBasisLabel(basis)} only`}). More
        comparable than wall-clock total when query counts differ. Lower is faster.
      </p>

      {compareLayoutForSelection(resolvedResults.map((r) => r.result_id)).kind === "multi_run" && (
        <>
          {shouldShowMultiRunStandings(
            resolvedResults.map((r) => r.result_id),
            decisionSummary.claimSuppressed,
          ) ? (
            <MultiRunStandings
              results={resolvedResults}
              baselineIndex={normalizedBaselineIndex}
              runLabels={cohortIdentitiesCompact}
            />
          ) : (
            <section class="card mb-8" aria-labelledby="standings-title">
              <h2 id="standings-title" class="text-base font-semibold text-[var(--bb-data-fg-primary)]">
                Standings
              </h2>
              <p class="mt-1 text-sm text-[var(--bb-data-fg-muted)]" role="status">
                Standings are unavailable because {decisionSummary.claimSuppressionReason ?? "the selected runs are not comparable"}.
              </p>
            </section>
          )}
          <MultiRunHeatmap
            results={resolvedResults}
            baselineIndex={normalizedBaselineIndex}
            runLabels={cohortIdentitiesCompact}
            limiter={queryLimiter}
            orderByDisagreement={queryLimiter === "movement"}
            queryFilter={queryLimiter === "all" ? undefined : limitedQueryIds}
            queryHref={(queryId) => queryRunComparisonHref(queryId, results.map((result) => result.result_id), basis)}
          />
        </>
      )}

      {!isMultiRun && (
        <QueryDiffTable
          limiter={queryLimiter}
          results={resolvedResults}
          baselineIndex={normalizedBaselineIndex}
          suppressionReason={decisionSummary.claimSuppressionReason}
          queryFilter={queryLimiter === "all" ? undefined : limitedQueryIds}
          queryHref={(queryId) => queryRunComparisonHref(queryId, results.map((result) => result.result_id), basis)}
        />
      )}
      <ComparabilityReceipt results={results} />
      <ProvenanceLegend />
</div>
  );
}

function CompareGuardrailSummary({
  warningCount,
  warningLabels,
  claimSuppressed,
  suppressionReason,
  comparisonBoundary,
}: {
  warningCount: number;
  warningLabels: string[];
  claimSuppressed: boolean;
  suppressionReason: string | null;
  comparisonBoundary: string | null;
}) {
  const warningText = warningCount > 0 ? formatWarningClassSummary(warningCount, warningLabels) : null;
  function focusWarningTarget(event: JSX.TargetedMouseEvent<HTMLAnchorElement>) {
    const target =
      document.getElementById(COMPARABILITY_WARNING_TARGET_ID) ?? document.getElementById(COMPARABILITY_RECEIPT_ID);
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView?.({ block: "start" });
    target.focus({ preventScroll: true });
    window.history.replaceState(null, "", `#${target.id}`);
  }
  return (
    <section aria-label="Compare guardrails" class="mb-4 panel-elevated p-4">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-base font-semibold text-[var(--bb-data-fg-primary)]">Before you compare</h2>
          <p class="mt-1 text-sm text-[var(--bb-data-fg-muted)]">
            {claimSuppressed
              ? `The summary does not rank these runs because ${suppressionReason ?? "they are not comparable"}.`
              : "These runs share the same benchmark, scale, and test phase. Review the differences below before drawing conclusions."}
          </p>
          {comparisonBoundary && (
            <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]" data-testid="comparison-boundary">
              {comparisonBoundary}
            </p>
          )}
          {warningText && (
            <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">
              {warningText}{" "}
              <a href={`#${COMPARABILITY_WARNING_TARGET_ID}`} onClick={focusWarningTarget}>
                Review differences
              </a>.
            </p>
          )}
        </div>
        {warningCount > 0 ? (
          <a
            href={`#${COMPARABILITY_WARNING_TARGET_ID}`}
            class="no-underline"
            data-testid="compare-warning-link"
            aria-label={`${formatWarningCount(warningCount)}; review comparison differences`}
            onClick={focusWarningTarget}
          >
            <StatusBadge role="comparison" tone="warning">
              {formatWarningCount(warningCount)}
            </StatusBadge>
          </a>
        ) : (
          <StatusBadge role="comparison" tone={claimSuppressed ? "warning" : "success"}>
            {claimSuppressed ? "No winner named" : "Same ranking"}
          </StatusBadge>
        )}
      </div>
    </section>
  );
}

const HARDWARE_BOUNDARY_FIELDS = ["Architecture", "CPU family", "CPU model", "CPU count", "Memory", "Locality"];

export function buildComparisonBoundary(fields: readonly { label: string; status: string }[]): string {
  const hardware = fields.filter((field) => HARDWARE_BOUNDARY_FIELDS.includes(field.label));
  const differing = hardware.filter((field) => field.status === "diff").map((field) => sentenceCaseField(field.label));
  const missing = hardware.filter((field) => field.status === "missing").map((field) => sentenceCaseField(field.label));
  const limits: string[] = [];
  if (differing.length > 0) {
    limits.push(`${formatPlainList(differing)} ${differing.length === 1 ? "differs" : "differ"}`);
  }
  if (missing.length > 0) {
    limits.push(
      `${formatPlainList(missing)} ${missing.length === 1 ? "is" : "are"} not recorded for every run`,
    );
  }
  if (limits.length > 0) {
    return `Hardware boundary: ${limits.join("; ")}. This compares recorded runs, not platforms in isolation.`;
  }
  return "Hardware boundary: the recorded architecture, CPU family, CPU model, CPU count, memory, and locality match. Other hardware details may differ.";
}

function sentenceCaseField(label: string): string {
  if (/^[A-Z]{2,}\b/.test(label)) return label;
  return `${label.charAt(0).toLowerCase()}${label.slice(1)}`;
}

function formatPlainList(values: readonly string[]): string {
  if (values.length < 2) return values[0] ?? "hardware fields";
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(", ")}, and ${values[values.length - 1]}`;
}

function severeCohortMismatchReason(results: DetailResult[]) {
  const reasons: string[] = [];
  if (new Set(results.map((result) => canonicalBenchmarkSlug(result.benchmark))).size > 1) {
    reasons.push("benchmarks differ");
  }
  if (new Set(results.map((result) => result.scale_factor)).size > 1) {
    reasons.push("scale factors differ");
  }
  if (new Set(results.map((result) => canonicalPhase(result.test_type))).size > 1) {
    reasons.push("phases differ");
  }
  if (new Set(results.map((result) => compareCohortSignatureForRow(result).streamCount)).size > 1) {
    reasons.push("stream counts differ");
  }
  return reasons.length > 0 ? reasons.join(" and ") : null;
}
