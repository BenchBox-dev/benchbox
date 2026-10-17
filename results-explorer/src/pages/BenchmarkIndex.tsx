import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { RoutableProps } from "preact-router";
import { route } from "preact-router";
import type { BenchmarkSummary, PlatformRow, SortDirection, SortState } from "@/types";
import type { ResultRow } from "@/lib/duckdbQueries";
import { getBenchmarkSummaryFromDuckDB, listBenchmarksWithPublicResults, listResults } from "@/lib/duckdbQueries";
import { BENCHMARK_LABELS, humanizeBenchmark, isKnownBenchmark, fmtScore, fmtGeomean, errMsg, complianceLabel } from "@/utils";
import { facetsToWhereClause, useFacetState, type DateWindowFacet, type ExplorerFacetKey, type FacetState } from "@/lib/facetModel";
import { hasActiveFacets, matchesFacetRow, singleFacetValue } from "@/lib/facetMatching";
import { singleValueFilterReason } from "@/lib/cohortFilterReason";
import { CohortFilterPanel, type CohortFilterFieldSpec } from "@/components/CohortFilterPanel";
import { ResultsCardToolbar, GroupBySelect, ResultsBasisStatement } from "@/components/ResultsCard";
import {
  buildCompareUrl,
  compareIdForRow,
  resultIdentityAriaLabel,
  resultReceiptHref,
  visibleResultIdForRow,
  MAX_COMPARE_SELECTIONS,
} from "@/lib/resultLinks";
import {
  formatCohortExclusion,
  formatTimingExclusion,
  isComparable,
  isTimingDisplayable,
  phaseScoreValue,
} from "@/lib/displayEligibility";
import { describeCompareExclusionReason, summarizeCompareExclusionReasons } from "@/lib/compareExclusionReasons";
import { compareSelectionLabel } from "@/lib/compareCohort";
import { BenchmarkMatrixSkeleton } from "@/components/LoadingSpinner";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Breadcrumb } from "@/components/Breadcrumb";
import { TrustBadge, ValidationBadge } from "@/components/TrustBadge";
import { FundingChip } from "@/components/FundingChip";
import { TuningBadge, tuningLabel } from "@/components/TuningBadge";
import { ChartPanel } from "@/components/ChartPanel";
import { ProvenanceLegend } from "@/components/ProvenanceLegend";
import { PageHeader } from "@/components/PageHeader";
import { CompareCta, isCompareAllAvailable } from "@/components/CompareCta";
import { CompareTray } from "@/components/CompareTray";
import { TrayAnnouncer } from "@/components/TrayAnnouncer";
import { RunDateChip } from "@/components/RunAge";
import { SubscaleCohortNotice } from "@/components/SubscaleNotice";
import { DERIVED_POWER_NOTE, isDerivedPowerScore } from "@/lib/subscale";
import { NotFound } from "@/pages/NotFound";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import {
  canonicalBenchmarkSlug,
  canonicalPhase,
  formatArchitecture,
  formatCpuFamily,
  formatMemoryGb,
  formatPhaseWithStreams,
  formatTrustLabel,
  formatValidationStatus,
  parseOverrideRules,
} from "@/lib/displayLabels";
import { formatRunIdentitiesForCohort } from "@/lib/runIdentity";
import { formatScaleFactor } from "@/lib/metricFormatters";
import { clientHardwareTitle, isManagedCloudRun } from "@/lib/clientHardware";
import { platformVersionDisplay } from "@/lib/versionLabel";
import { RunIdentityLabel } from "@/components/DataTable";
import { formatSelectedCount } from "@/lib/copyFormatters";
import { groupCohortRows, limitCohortGroups, type CohortGroupBy } from "@/lib/queryFilters";

const BENCHMARK_SELECTION_LIMIT_REASON_ID = "benchmark-selection-limit";

const CHART_CARD_ANCHORS: Readonly<Record<string, string>> = {
  query_heatmap: "benchmark-section-matrix",
  percentile_ladder: "benchmark-section-percentiles",
  cdf_chart: "benchmark-section-cdf",
  query_histogram: "benchmark-section-histogram",
  stacked_phase: "benchmark-section-phases",
  time_series: "benchmark-section-trend",
  rank_table: "benchmark-section-ranks",
  cost_scatter: "benchmark-section-cost",
};

const ANALYSIS_SECTION_SUFFIXES: readonly string[] = Object.values(CHART_CARD_ANCHORS).map((anchor) =>
  anchor.replace("benchmark-section-", "")
);

const ANALYSIS_CHART_BY_SECTION: Readonly<Record<string, string>> = Object.fromEntries(
  Object.entries(CHART_CARD_ANCHORS).map(([chartId, anchorId]) => [
    anchorId.replace("benchmark-section-", ""),
    chartId,
  ]),
);

const DATE_WINDOW_OPTIONS: { value: DateWindowFacet; label: string }[] = [
  { value: "all", label: "All time" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
  { value: "365d", label: "Last 365 days" },
];

interface BenchmarkIndexProps extends RoutableProps {
  benchmark?: string;
}

type ViewMode = "list" | (string & {});
type BenchmarkListSortKey = "platform" | "scale_factor" | "arch" | "cpu_family" | "memory_gb" | "run_date" | "power_score" | "display_geomean_ms" | "query_count";
const TABLE_RENDER_LIMIT = 200;
const TABLE_RENDER_INCREMENT = 200;
const BENCHMARK_RESULT_FACET_KEYS: ExplorerFacetKey[] = [
  "platform",
  "execution_mode",
  "validation_status",
  "deployment_class",
  "cloud_provider",
  "cloud_region",
  "instance_or_warehouse",
  "storage_format",
  "cost_status",
  "date_window",
  "platform_version",
  "arch",
  "cpu_family",
  "memory_gb",
];
const BENCHMARK_ROW_FACET_KEYS: ExplorerFacetKey[] = [
  "platform",
  "execution_mode",
  "tuning_mode",
  "trust_tier",
  "validation_status",
  "deployment_class",
  "cloud_provider",
  "cloud_region",
  "instance_or_warehouse",
  "storage_format",
  "cost_status",
  "date_window",
  "platform_version",
  "arch",
  "cpu_family",
  "memory_gb",
];

function requestedAnalysisChart(): string | null {
  const hash = window.location.hash;
  if (!hash.startsWith("#benchmark-section-")) return null;
  return ANALYSIS_CHART_BY_SECTION[hash.replace("#benchmark-section-", "")] ?? null;
}

function requestedBenchmarkSection(): ViewMode | null {
  const hash = window.location.hash.replace("#benchmark-section-", "");
  if (window.location.hash.startsWith("#benchmark-section-") && (hash === "list" || ANALYSIS_SECTION_SUFFIXES.includes(hash))) {
    return hash as ViewMode;
  }
  const legacyView = new URLSearchParams(window.location.search).get("view");
  return legacyView === "ranks" || legacyView === "list" ? legacyView : null;
}

function benchmarkContextNote(benchmark: string): string | null {
  if (benchmark === "star_schema" || benchmark === "ssb") {
    return "Star Schema Benchmark (SSB): raw star_schema evidence is retained, while cohort and ranking identity uses canonical ssb.";
  }
  return null;
}

function uniqueBenchmarkOptions(availableBenchmarks: ReadonlySet<string> | null): { value: string; label: string }[] {
  if (availableBenchmarks === null) return [];
  const seen = new Set<string>();
  const options: { value: string; label: string }[] = [];
  for (const [value, label] of Object.entries(BENCHMARK_LABELS)) {
    if (!availableBenchmarks.has(value)) continue;
    if (seen.has(label)) continue;
    seen.add(label);
    options.push({ value, label });
  }
  return options.sort((a, b) => a.label.localeCompare(b.label));
}

function benchmarkCompareGuidanceMessage(
  selectedCount: number,
  title: string,
  scaleFactor: string,
  phase: string,
): string {
  const ranking = `${title} ${formatScaleFactor(scaleFactor)} ${phase}`;
  const selectedStatus = formatSelectedCount(selectedCount, "result", MAX_COMPARE_SELECTIONS);
  if (selectedCount === 0) {
    return `${selectedStatus}. Select two or more platforms from the same ${ranking} ranking to compare. Benchmark, scale, phase, metric, and unit stay fixed on this page.`;
  }
  if (selectedCount === 1) {
    return `${selectedStatus} in ${ranking}. Select one more result from this ranking to enable Compare.`;
  }
  return `${selectedStatus} in ${ranking}. The sticky tray opens Compare with the same benchmark, scale, and phase contract.`;
}

function isResultTimingDisplayable(row: ResultRow): boolean {
  return row.display_exclusion_reason === null;
}

export function BenchmarkIndex({ benchmark = "" }: BenchmarkIndexProps) {
  const canonicalBenchmark = canonicalBenchmarkSlug(benchmark);
  const title = humanizeBenchmark(canonicalBenchmark);
  const [results, setResults] = useState<ResultRow[] | null>(null);
  const [platformVersionDomainResults, setPlatformVersionDomainResults] = useState<ResultRow[] | null>(null);
  const [summary, setSummary] = useState<BenchmarkSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [availableBenchmarks, setAvailableBenchmarks] = useState<ReadonlySet<string> | null>(null);

  useEffect(() => {
    let cancelled = false;
    listBenchmarksWithPublicResults()
      .then((slugs) => {
        if (!cancelled) setAvailableBenchmarks(new Set(slugs));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        console.warn("listBenchmarksWithPublicResults failed; switcher will show only the current benchmark", err);
        setAvailableBenchmarks(new Set(canonicalBenchmark ? [canonicalBenchmark] : []));
      });
    return () => {
      cancelled = true;
    };
  }, [benchmark]);

  const { facets, setFacet, resetFacets } = useFacetState();
  const requestedSf = singleFacetValue(facets.scale_factor);
  const phaseFilter = singleFacetValue(facets.phase, "power") ?? "power";
  const tuningFilter = singleFacetValue(facets.tuning_mode, "all") ?? "all";
  const platformVersionFilter = facets.platform_version.length === 0
    ? "all"
    : facets.platform_version.length === 1
      ? facets.platform_version[0]!
      : "__multiple__";
  const benchmarkResultWhere = useMemo(
    () =>
      facetsToWhereClause({
        ...facets,
        benchmark: canonicalBenchmark ? [canonicalBenchmark] : [],
        scale_factor: [],
        phase: [],
        stream_count: [],
        tuning_mode: [],
        trust_tier: [],
      }),
    [
      benchmark,
      canonicalBenchmark,
      facets.cloud_provider,
      facets.cloud_region,
      facets.cost_status,
      facets.date_window,
      facets.deployment_class,
      facets.execution_mode,
      facets.instance_or_warehouse,
      facets.platform,
      facets.platform_version,
      facets.arch,
      facets.cpu_family,
      facets.memory_gb,
      facets.storage_format,
      facets.validation_status,
    ],
  );
  const benchmarkVersionDomainWhere = useMemo(
    () => facetsToWhereClause({
      ...facets,
      benchmark: canonicalBenchmark ? [canonicalBenchmark] : [],
      scale_factor: [],
      phase: [],
      stream_count: [],
      tuning_mode: [],
      trust_tier: [],
      platform_version: [],
    }),
    [
      benchmark,
      canonicalBenchmark,
      facets.cloud_provider,
      facets.cloud_region,
      facets.cost_status,
      facets.date_window,
      facets.deployment_class,
      facets.execution_mode,
      facets.instance_or_warehouse,
      facets.platform,
      facets.arch,
      facets.cpu_family,
      facets.memory_gb,
      facets.storage_format,
      facets.validation_status,
    ],
  );

  const setScaleFilter = (value: string | null) => setFacet("scale_factor", value ? [value] : []);
  const setPhaseFilter = (value: string) => setFacet("phase", value === "power" ? [] : [value]);
  const setStreamFilter = (value: number | null) => setFacet("stream_count", value === null ? [] : [String(value)]);
  const setTuningFilter = (value: string) => setFacet("tuning_mode", value === "all" ? [] : [value]);
  const setTrustFilter = (value: Set<string> | null) => setFacet("trust_tier", value ? [...value].sort() : []);

  const [sectionNavigation, setSectionNavigation] = useState(0);
  const completedSectionScroll = useRef<string | null>(null);
  const [settledSummaryKey, setSettledSummaryKey] = useState<string | null>(null);
  const requestedSection = requestedBenchmarkSection();
  useEffect(() => {
    const onNavigation = () => setSectionNavigation((value) => value + 1);
    window.addEventListener("hashchange", onNavigation);
    window.addEventListener("popstate", onNavigation);
    return () => {
      window.removeEventListener("hashchange", onNavigation);
      window.removeEventListener("popstate", onNavigation);
    };
  }, []);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const [resultsRetryToken, setResultsRetryToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    listResults(benchmarkResultWhere, { includeHardware: true })
      .then((r) => {
        if (!cancelled) setResults(r);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errMsg(err));
      });
    return () => {
      cancelled = true;
    };
  }, [benchmarkResultWhere, resultsRetryToken]);

  useEffect(() => {
    if (facets.platform_version.length === 0) {
      setPlatformVersionDomainResults(null);
      return;
    }
    let cancelled = false;
    listResults(benchmarkVersionDomainWhere)
      .then((rows) => {
        if (!cancelled) setPlatformVersionDomainResults(rows);
      })
      .catch(() => {
        if (!cancelled) setPlatformVersionDomainResults(null);
      });
    return () => {
      cancelled = true;
    };
  }, [benchmarkVersionDomainWhere, facets.platform_version]);

  const benchmarkResults = results?.filter((r) => canonicalBenchmarkSlug(r.benchmark) === canonicalBenchmark) ?? [];
  const benchmarkNotFound = results !== null && benchmarkResults.length === 0 && !isKnownBenchmark(canonicalBenchmark);
  useDocumentTitle(benchmarkNotFound ? "Not found · BenchBox Results" : `${title} · BenchBox Results`);

  const scaleFactors = [
    ...new Set(benchmarkResults.map((r) => String(r.scale_factor))),
  ].sort((a, b) => Number(a) - Number(b));

  const scaleFilter: string | null =
    requestedSf !== null && scaleFactors.includes(requestedSf) ? requestedSf : null;

  const effectiveSf = scaleFilter ?? scaleFactors[0] ?? "0.01";

  useEffect(() => {
    if (!results || scaleFactors.length === 0 || requestedSf === null || requestedSf === effectiveSf) return;
    setScaleFilter(effectiveSf);
  }, [effectiveSf, requestedSf, results, scaleFactors.length]);

  const phases = [
    ...new Set(
      benchmarkResults
        .filter((r) => String(r.scale_factor) === effectiveSf)
        .map((r) => canonicalPhase(r.test_type))
        .filter(Boolean),
    ),
  ].sort();

  const effectivePhase = phases.includes(phaseFilter) ? phaseFilter : (phases[0] ?? phaseFilter);
  const streamCountsInPhase = new Map<number, number>();
  const rankableStreamCounts = new Map<number, number>();
  for (const result of benchmarkResults) {
    if (String(result.scale_factor) !== effectiveSf || canonicalPhase(result.test_type) !== effectivePhase) continue;
    if (result.stream_count === null || result.stream_count === undefined) continue;
    streamCountsInPhase.set(result.stream_count, (streamCountsInPhase.get(result.stream_count) ?? 0) + 1);
    if (result.ranking_exclusion_reason === null) {
      rankableStreamCounts.set(result.stream_count, (rankableStreamCounts.get(result.stream_count) ?? 0) + 1);
    }
  }
  const streamCounts = [...streamCountsInPhase.keys()].sort((a, b) => a - b);
  const defaultStreamCounts = rankableStreamCounts.size > 0 ? rankableStreamCounts : streamCountsInPhase;
  const requestedStreams = singleFacetValue(facets.stream_count);
  const requestedStreamCount = requestedStreams === null ? null : Number(requestedStreams);
  const effectiveStreams: number | null =
    streamCounts.length === 0
      ? null
      : requestedStreamCount !== null && streamCounts.includes(requestedStreamCount)
        ? requestedStreamCount
        : [...defaultStreamCounts.keys()]
            .sort((a, b) => a - b)
            .reduce((best, count) => ((defaultStreamCounts.get(count) ?? 0) > (defaultStreamCounts.get(best) ?? 0) ? count : best));
  const summaryKey = JSON.stringify([benchmark, effectiveSf, effectivePhase, effectiveStreams]);

  useEffect(() => {
    if (!results || phases.length === 0 || phaseFilter === effectivePhase) return;
    setPhaseFilter(effectivePhase);
  }, [effectivePhase, phaseFilter, phases.length, results, setPhaseFilter]);

  useEffect(() => {
    if (!results || requestedStreams === null || requestedStreamCount === effectiveStreams) return;
    setStreamFilter(effectiveStreams);
  }, [effectiveStreams, requestedStreamCount, requestedStreams, results, setStreamFilter]);

  useEffect(() => {
    if (!results || phases.length === 0) return;
    let cancelled = false;
    setSelectedIds(new Set());
    setSummary(null);
    setSummaryError(null);
    setSummaryLoading(true);
    setSettledSummaryKey(null);
    getBenchmarkSummaryFromDuckDB(benchmark, Number(effectiveSf), effectivePhase, effectiveStreams)
      .then((s) => {
        if (!cancelled) {
          setSummary(s);
          setSummaryLoading(false);
          setSettledSummaryKey(summaryKey);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setSummaryError(errMsg(err));
          setSummaryLoading(false);
          setSettledSummaryKey(summaryKey);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [results, benchmark, effectiveSf, effectivePhase, effectiveStreams]);

  useEffect(() => {
    if (!requestedSection || requestedSection !== "list" || summaryLoading || settledSummaryKey !== summaryKey) return;
    const navigationKey = `${benchmark}:${requestedSection}:${sectionNavigation}`;
    if (completedSectionScroll.current === navigationKey) return;
    const target = document.getElementById(`benchmark-section-${requestedSection}`);
    if (!target) return;
    target.scrollIntoView?.({ block: "start" });
    completedSectionScroll.current = navigationKey;
  }, [benchmark, requestedSection, sectionNavigation, settledSummaryKey, summaryKey, summaryLoading]);

  if (error) return <ErrorMessage message={error} onRetry={() => setResultsRetryToken((t) => t + 1)} />;
  if (!results) {
    return (
      <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <BenchmarkMatrixSkeleton message="Loading results..." />
      </div>
    );
  }

  if (benchmarkResults.length === 0) {
    if (benchmarkNotFound) {
      return (
        <NotFound
          message={`Benchmark "${benchmark}" is not part of the published corpus.`}
        />
      );
    }
    if (hasActiveFacets(facets, BENCHMARK_RESULT_FACET_KEYS)) {
      return (
        <div class="mx-auto max-w-7xl px-4 py-24 text-center sm:px-6 lg:px-8">
          <Breadcrumb crumbs={[{ label: "Results", href: "/results/" }, { label: humanizeBenchmark(benchmark) }]} />
          <h1 class="mt-6 text-3xl font-bold text-[var(--bb-data-fg-primary)]">{humanizeBenchmark(benchmark)}</h1>
          <p class="mt-4 text-lg text-[var(--bb-data-fg-muted)]">
            No published results match the selected filters for {humanizeBenchmark(benchmark)}.
          </p>
          <a href={`/results/${benchmark}/`} class="mt-6 inline-block btn btn-primary no-underline">
            Clear filters
          </a>
        </div>
      );
    }
    return (
      <div class="mx-auto max-w-7xl px-4 py-24 text-center sm:px-6 lg:px-8">
        <Breadcrumb crumbs={[{ label: "Results", href: "/results/" }, { label: humanizeBenchmark(benchmark) }]} />
        <h1 class="mt-6 text-3xl font-bold text-[var(--bb-data-fg-primary)]">{humanizeBenchmark(benchmark)}</h1>
        <p class="mt-4 text-lg text-[var(--bb-data-fg-muted)]">
          No published results yet for {humanizeBenchmark(benchmark)}.
        </p>
        <p class="mt-2 text-sm text-[var(--bb-data-fg-muted)]">
          The benchmark is supported by BenchBox but no runs have been ingested into the public corpus.
        </p>
        <a href="/results/" class="mt-6 inline-block btn btn-primary no-underline">
          Back to Results
        </a>
      </div>
    );
  }

  const resultMetadataById = new Map(benchmarkResults.map((result) => [result.result_id, result]));
  const summaryWithResultMetadata: BenchmarkSummary | null = summary
    ? {
        ...summary,
        platforms: summary.platforms.map((platform) => {
          const metadata = resultMetadataById.get(platform.result_id);
          return {
            ...platform,
            platform_version: platform.platform_version ?? metadata?.platform_version ?? null,
            validation_status: platform.validation_status ?? metadata?.validation_status ?? null,
            override_rules: platform.override_rules ?? metadata?.override_rules ?? null,
            arch: metadata?.arch ?? null,
            cpu_family: metadata?.cpu_family ?? null,
            memory_gb: metadata?.memory_gb ?? null,
          };
        }),
      }
    : null;

  const tuningModes = summaryWithResultMetadata
    ? [...new Set(summaryWithResultMetadata.platforms.map((p) => p.tuning_mode).filter((m): m is string => m !== null))].sort()
    : [];
  const trustLabels = summaryWithResultMetadata
    ? [...new Set(summaryWithResultMetadata.platforms.map((p) => p.trust_label))].sort()
    : [];
  const platformOptions = summaryWithResultMetadata
    ? [...new Set(summaryWithResultMetadata.platforms.map((p) => p.platform))].sort((a, b) => a.localeCompare(b))
    : [];
  const validationOptions = summaryWithResultMetadata
    ? [
        ...new Set(
          summaryWithResultMetadata.platforms
            .map((p) => p.validation_status)
            .filter((status): status is string => status !== null && status !== ""),
        ),
      ].sort()
    : [];
  const archOptions = summaryWithResultMetadata
    ? [
        ...new Set(
          summaryWithResultMetadata.platforms
            .map((p) => p.arch)
            .filter((a): a is string => a !== null && a !== undefined),
        ),
      ].sort()
    : [];
  const cpuFamilyOptions = summaryWithResultMetadata
    ? [
        ...new Set(
          summaryWithResultMetadata.platforms
            .map((p) => p.cpu_family)
            .filter((c): c is string => c !== null && c !== undefined),
        ),
      ].sort()
    : [];
  const memoryOptions = summaryWithResultMetadata
    ? [
        ...new Set(
          summaryWithResultMetadata.platforms
            .map((p) => p.memory_gb)
            .filter((m): m is number => m !== null && m !== undefined),
        ),
      ].sort((a, b) => a - b)
    : [];
  const platformVersions = [
    ...new Set(
      (platformVersionDomainResults ?? benchmarkResults)
        .map((result) => result.platform_version)
        .filter((version): version is string => version !== null),
    ),
  ].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

  const filteredSummary: BenchmarkSummary | null = summaryWithResultMetadata
    ? {
        ...summaryWithResultMetadata,
        platforms: summaryWithResultMetadata.platforms.filter((p) => {
          return matchesFacetRow(p, facets, { keys: BENCHMARK_ROW_FACET_KEYS });
        }),
      }
    : null;
  const analysisSummary: BenchmarkSummary | null = filteredSummary
    ? {
        ...filteredSummary,
        platforms: filteredSummary.platforms.filter(isTimingDisplayable),
      }
    : null;
  const excludedRows = filteredSummary?.platforms.filter((row) => !isTimingDisplayable(row)) ?? [];
  const rankGateReason = filteredSummary ? formatCohortExclusion(filteredSummary) : null;
  const compareEligibilityByResultId = new Map(
    (filteredSummary?.platforms ?? []).map((row) => [row.result_id, row] as const),
  );
  const historicalEntries = benchmarkResults.filter((result) => {
    if (String(result.scale_factor) !== effectiveSf) return false;
    if (canonicalPhase(result.test_type) !== effectivePhase) return false;
    if (effectiveStreams !== null && (result.stream_count ?? null) !== effectiveStreams) return false;
    if (!isResultTimingDisplayable(result)) return false;
    return matchesFacetRow(result, facets, { keys: BENCHMARK_ROW_FACET_KEYS });
  });

  const selectedCompareRowsById = new Map(
    (summaryWithResultMetadata?.platforms ?? [])
      .filter(isTimingDisplayable)
      .filter(isComparable)
      .map((row) => [compareIdForRow(row), row]),
  );
  const selectedCompareRows = [...selectedIds]
    .map((id) => selectedCompareRowsById.get(id))
    .filter((row): row is PlatformRow => row !== undefined);

  const selectedCompareIds = selectedCompareRows.map((row) => compareIdForRow(row));
  const benchmarkOptions = uniqueBenchmarkOptions(availableBenchmarks);
  const hasCurrentBenchmarkOption = benchmarkOptions.some(
    (option) => canonicalBenchmarkSlug(option.value) === canonicalBenchmark,
  );
  const contextNote = benchmarkContextNote(benchmark);
  const selectedComparableCount = selectedCompareRows.length;
  const matrixCompareRows = analysisSummary?.platforms ?? [];
  const filteredComparableRows = matrixCompareRows.filter(isComparable);
  const filteredCompareIds = filteredComparableRows.map((row) => compareIdForRow(row));
  const compareGuidance =
    selectedComparableCount < 2 && isCompareAllAvailable(filteredCompareIds)
      ? `All ${filteredCompareIds.length} comparable runs in this view can be compared together.`
      : benchmarkCompareGuidanceMessage(selectedComparableCount, title, effectiveSf, effectivePhase);
  const selectionLimitCopy =
    selectedIds.size >= MAX_COMPARE_SELECTIONS
      ? describeCompareExclusionReason(`Up to ${MAX_COMPARE_SELECTIONS} runs can be compared.`)
      : null;
  const zeroSelectableCompareRows =
    matrixCompareRows.length > 0 && matrixCompareRows.every((row) => !isComparable(row));
  const zeroSelectableReasons = summarizeCompareExclusionReasons(
    matrixCompareRows.map((row) => row.comparison_exclusion_reason),
  );

  const updateSelectedIds = (next: Set<string>) => {
    if (next.size <= MAX_COMPARE_SELECTIONS) setSelectedIds(next);
  };

  const trustFilterValue =
    facets.trust_tier.length === 0 ? "all" : facets.trust_tier.length === 1 ? facets.trust_tier[0]! : "__multiple__";

  const benchmarkCohortFilterFields: CohortFilterFieldSpec[] = [
    {
      id: "scale-filter",
      label: "Scale",
      value: effectiveSf,
      options:
        scaleFactors.length > 0
          ? scaleFactors.map((sf) => ({ value: sf, label: formatScaleFactor(sf) }))
          : [{ value: effectiveSf, label: formatScaleFactor(effectiveSf) }],
      onChange: (value) => setScaleFilter(value),
      disabledReason: singleValueFilterReason(scaleFactors.length, false),
    },
    {
      id: "phase-filter",
      label: "Phase",
      value: effectivePhase,
      options:
        phases.length > 0
          ? phases.map((ph) => ({ value: ph, label: ph.charAt(0).toUpperCase() + ph.slice(1) }))
          : [{ value: effectivePhase, label: effectivePhase }],
      onChange: (value) => setPhaseFilter(value),
      disabledReason: singleValueFilterReason(phases.length, false),
    },
    ...(streamCounts.length > 0
      ? [
          {
            id: "stream-filter",
            testId: "benchmark-stream-filter",
            label: "Streams",
            value: String(effectiveStreams),
            options: streamCounts.map((count) => ({
              value: String(count),
              label: `${count} ${count === 1 ? "stream" : "streams"}`,
            })),
            onChange: (value: string) => setStreamFilter(Number(value)),
            disabledReason: singleValueFilterReason(streamCounts.length, false),
          },
        ]
      : []),
    {
      id: "tuning-filter",
      label: "Tuning",
      value: tuningFilter,
      options: [{ value: "all", label: "All" }, ...tuningModes.map((m) => ({ value: m, label: tuningLabel(m) }))],
      onChange: (value) => setTuningFilter(value),
      disabledReason: singleValueFilterReason(tuningModes.length, tuningFilter !== "all"),
    },
    {
      id: "benchmark-version-filter",
      testId: "benchmark-version-filter",
      label: "Platform version",
      value: platformVersionFilter,
      options: [{ value: "all", label: "All versions" }, ...platformVersions.map((version) => ({ value: version, label: version }))],
      onChange: (value) => setFacet("platform_version", value === "all" ? [] : [value]),
      disabledReason: singleValueFilterReason(platformVersions.length, platformVersionFilter !== "all"),
      multiValueOption:
        platformVersionFilter === "__multiple__"
          ? { value: "__multiple__", label: `${facets.platform_version.length} versions selected` }
          : undefined,
    },
    {
      id: "benchmark-trust-filter",
      testId: "benchmark-trust-filter",
      label: "Trust tier",
      value: trustFilterValue,
      options: [{ value: "all", label: "All trust tiers" }, ...trustLabels.map((tier) => ({ value: tier, label: formatTrustLabel(tier) }))],
      onChange: (value) => setTrustFilter(value === "all" ? null : new Set([value])),
      disabledReason: singleValueFilterReason(trustLabels.length, trustFilterValue !== "all"),
      multiValueOption:
        trustFilterValue === "__multiple__"
          ? { value: "__multiple__", label: `${facets.trust_tier.length} tiers selected` }
          : undefined,
    },
    {
      id: "benchmark-platform-filter",
      testId: "benchmark-platform-filter",
      label: "Platform",
      value: facets.platform.length === 1 ? facets.platform[0]! : "all",
      options: [{ value: "all", label: "All platforms" }, ...platformOptions.map((option) => ({ value: option, label: option }))],
      onChange: (value) => setFacet("platform", value === "all" ? [] : [value]),
      disabledReason: singleValueFilterReason(platformOptions.length, facets.platform.length > 0),
    },
    {
      id: "benchmark-validation-filter",
      testId: "benchmark-validation-filter",
      label: "Validation",
      value: facets.validation_status.length === 1 ? facets.validation_status[0]! : "all",
      options: [{ value: "all", label: "All validation" }, ...validationOptions.map((status) => ({ value: status, label: formatValidationStatus(status) }))],
      onChange: (value) => setFacet("validation_status", value === "all" ? [] : [value]),
      disabledReason: singleValueFilterReason(validationOptions.length, facets.validation_status.length > 0),
    },
    {
      id: "benchmark-arch-filter",
      testId: "benchmark-arch-filter",
      label: "Architecture",
      value: facets.arch.length === 1 ? facets.arch[0]! : "all",
      options: [{ value: "all", label: "All architectures" }, ...archOptions.map((option) => ({ value: option, label: formatArchitecture(option) }))],
      onChange: (value) => setFacet("arch", value === "all" ? [] : [value]),
      disabledReason: singleValueFilterReason(archOptions.length, facets.arch.length > 0),
    },
    {
      id: "benchmark-cpu-family-filter",
      testId: "benchmark-cpu-family-filter",
      label: "CPU family",
      value: facets.cpu_family.length === 1 ? facets.cpu_family[0]! : "all",
      options: [{ value: "all", label: "All CPU families" }, ...cpuFamilyOptions.map((option) => ({ value: option, label: formatCpuFamily(option) }))],
      onChange: (value) => setFacet("cpu_family", value === "all" ? [] : [value]),
      disabledReason: singleValueFilterReason(cpuFamilyOptions.length, facets.cpu_family.length > 0),
    },
    {
      id: "benchmark-memory-filter",
      testId: "benchmark-memory-filter",
      label: "Memory",
      value: facets.memory_gb.length === 1 ? facets.memory_gb[0]! : "all",
      options: [{ value: "all", label: "All memory sizes" }, ...memoryOptions.map((option) => ({ value: String(option), label: formatMemoryGb(option) }))],
      onChange: (value) => setFacet("memory_gb", value === "all" ? [] : [value]),
      disabledReason: singleValueFilterReason(memoryOptions.length, facets.memory_gb.length > 0),
    },
    {
      id: "benchmark-date-window-filter",
      testId: "benchmark-date-window-filter",
      label: "Run date",
      value: facets.date_window,
      options: DATE_WINDOW_OPTIONS,
      onChange: (value) => setFacet("date_window", value as DateWindowFacet),
    },
  ];

  return (
    <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        crumbs={[
          { label: "Results", href: "/results/" },
          { label: title },
        ]}
        eyebrow="Benchmark"
        title={`${title} Results`}
        meta={
          filteredSummary && filteredSummary.platforms.length > 0 ? (
            <>
              <span class="bb-meta-chip" data-testid="cohort-counts">
                {filteredSummary.platforms.length} published runs
              </span>
              <span class="bb-meta-chip">{filteredSummary.query_ids.length} queries</span>
              <span class="bb-meta-chip">SF {filteredSummary.scale_factor}</span>
              <span class="bb-meta-chip">{formatPhaseWithStreams(effectivePhase, effectiveStreams)}</span>
            </>
          ) : undefined
        }
        actions={
          <>
          <div class="flex items-center gap-2">
            <label class="text-sm font-medium text-[var(--bb-data-fg-primary)]" for="benchmark-switcher">
              Benchmark:
            </label>
            <select
              id="benchmark-switcher"
              data-testid="benchmark-switcher"
              class="rounded-md border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-3 py-1.5 text-sm shadow-sm"
              value={canonicalBenchmark}
              onChange={(event) => {
                const next = (event.target as HTMLSelectElement).value;
                if (next === benchmark) return;
                const section = requestedBenchmarkSection();
                resetFacets();
                route(`/results/${next}/${section ? `#benchmark-section-${section}` : ""}`);
              }}
            >
              {benchmarkOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
              {!hasCurrentBenchmarkOption && (
                <option value={benchmark}>{title}</option>
              )}
            </select>
          </div>
          </>
        }
      />

      {filteredSummary && (
        <SubscaleCohortNotice benchmark={filteredSummary.benchmark} scaleFactor={filteredSummary.scale_factor} />
      )}

      <CohortFilterPanel
        testId="benchmark-filters"
        fields={benchmarkCohortFilterFields}
        showClear={hasActiveFacets(facets, BENCHMARK_ROW_FACET_KEYS)}
        clearTestId="benchmark-clear-filters"
        onClear={() => {
          for (const key of BENCHMARK_ROW_FACET_KEYS) {
            if (key === "date_window") continue;
            setFacet(key, [] as never);
          }
          setFacet("date_window", "all");
        }}
      />

      {contextNote && (
        <div
          class="mb-4 rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-3 text-sm text-[var(--bb-data-fg-muted)]"
          data-testid="benchmark-context-note"
        >
          {contextNote}
        </div>
      )}


      <section
        class="mb-4 rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] px-4 py-3 shadow-sm"
        data-testid="benchmark-compare-guidance"
        aria-label="Benchmark compare guidance"
      >
        <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 class="text-sm font-semibold text-[var(--bb-data-fg-primary)]">Compare selected results</h2>
            <p
              class="mt-1 text-sm text-[var(--bb-data-fg-muted)]"
              {...(selectedComparableCount < 2 ? { "aria-live": "polite" as const, "aria-atomic": "true" as const } : {})}
            >
              {compareGuidance}
            </p>
            {selectionLimitCopy && (
              <p
                id={BENCHMARK_SELECTION_LIMIT_REASON_ID}
                class="mt-1 text-sm text-[var(--bb-tone-warning-fg)]"
                data-testid="benchmark-selection-limit"
              >
                <span class="font-medium">{selectionLimitCopy.shortText}.</span> {selectionLimitCopy.recoveryHint}
              </p>
            )}
          </div>
          <CompareCta
            selectedIds={selectedCompareIds}
            allIds={filteredCompareIds}
            buildHref={buildCompareUrl}
            testId="benchmark-compare-cta"
          />
        </div>
      </section>

      {zeroSelectableCompareRows && (
        <section
          class="mb-4 rounded-lg border border-[var(--bb-tone-warning-border)] bg-[var(--bb-tone-warning-bg)] px-4 py-3 text-sm text-[var(--bb-tone-warning-fg)]"
          data-testid="benchmark-zero-selectable"
          aria-label="No selectable benchmark compare rows"
        >
          <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 class="font-semibold">No selectable compare rows</h2>
              <p class="mt-1">
                {zeroSelectableReasons.length > 0
                  ? `${zeroSelectableReasons[0]!.count} ${zeroSelectableReasons[0]!.copy.shortText.toLowerCase()} row${
                      zeroSelectableReasons[0]!.count === 1 ? "" : "s"
                    }. ${zeroSelectableReasons[0]!.copy.recoveryHint}`
                  : "This ranking does not expose a comparable run. Choose another ranking in the Compare builder."}
              </p>
            </div>
            <a href="/results/compare" class="btn btn-secondary shrink-0 text-sm no-underline">
              Choose another ranking
            </a>
          </div>
        </section>
      )}

      <section id="benchmark-section-list" class="scroll-mt-24 overflow-x-hidden" aria-labelledby="benchmark-heading-list">
        <h2 id="benchmark-heading-list" class="mb-3 text-lg font-semibold text-[var(--bb-data-fg-primary)]">
          Results
        </h2>
        {summaryError && (
          <div class="mb-3 rounded-lg tone-warning border border-[var(--bb-data-border)] px-4 py-3 text-sm">
            Could not load compare eligibility for this cohort: {summaryError}
          </div>
        )}
        <ListTable
          benchmark={benchmark}
          results={results}
          scaleFactor={effectiveSf}
          phase={effectivePhase}
          streamCount={effectiveStreams}
          facets={facets}
          compareEligibilityByResultId={compareEligibilityByResultId}
          compareEligibilityState={
            summaryLoading || settledSummaryKey !== summaryKey
              ? "loading"
              : summaryError
                ? "error"
                : "ready"
          }
          selectedIds={selectedIds}
          onSelectionChange={updateSelectedIds}
          selectionAtCap={selectedIds.size >= MAX_COMPARE_SELECTIONS}
          selectionLimitReasonId={selectionLimitCopy ? BENCHMARK_SELECTION_LIMIT_REASON_ID : undefined}
        />
      </section>

      <ExcludedRunsDisclosure rows={excludedRows} />

      {filteredSummary && filteredSummary.platforms.length > 0 && (
        <section id="benchmark-section-analysis" class="mt-8 scroll-mt-24" aria-labelledby="benchmark-heading-analysis">
          <h2 id="benchmark-heading-analysis" class="mb-3 text-lg font-semibold text-[var(--bb-data-fg-primary)]">
            Analysis
          </h2>
          <ChartPanel
            context={{
              kind: "summary",
              summary: filteredSummary,
              historical: historicalEntries,
            }}
            summaryLayout="long"
            cardAnchors={CHART_CARD_ANCHORS}
            forceOpenChartId={
              requestedAnalysisChart() ?? (requestedSection === "ranks" ? "rank_table" : undefined)
            }
            rankGateReason={rankGateReason}
            rankGateContext={{ benchmark: title, scaleFactor: effectiveSf, phase: effectivePhase }}
          />
        </section>
      )}

      <TrayAnnouncer count={selectedComparableCount} />
      {selectedCompareRows.length >= 2 && (
        <CompareTray
          summary={
            <>
              <strong>{selectedComparableCount}</strong> platforms selected for compare
            </>
          }
          items={selectedCompareRows.map((row) => ({
            id: compareIdForRow(row),
            platform: row.platform,
            benchmarkLabel: humanizeBenchmark(summaryWithResultMetadata?.benchmark ?? benchmark),
            scaleFactor: summaryWithResultMetadata?.scale_factor ?? effectiveSf,
            phase: summaryWithResultMetadata?.phase ?? effectivePhase,
            runDate: row.run_date,
            trustLabel: row.trust_label,
            funding: row.funding,
            visibleResultId: visibleResultIdForRow(row),
          }))}
          allIds={filteredCompareIds}
          buildHref={buildCompareUrl}
          onClear={() => setSelectedIds(new Set())}
        />
      )}
      <div id="provenance-legend" class="scroll-mt-24">
        <ProvenanceLegend />
      </div>
</div>
  );
}

function ExcludedRunsDisclosure({ rows }: { rows: PlatformRow[] }) {
  if (rows.length === 0) return null;
  return (
    <details
      class="mt-4 rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] px-4 py-3 text-sm shadow-sm"
      data-testid="excluded-runs"
    >
      <summary class="cursor-pointer font-medium text-[var(--bb-data-fg-primary)]">
        Excluded runs ({rows.length})
      </summary>
      <div class="mt-3 overflow-x-auto">
        <table class="min-w-full divide-y divide-[var(--bb-data-border)]">
          <thead class="bg-[var(--bb-surface-data-muted)]">
            <tr>
              <th class="table-th text-left">Platform</th>
              <th class="table-th text-left">Reason</th>
              <th class="table-th text-left">Receipt</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--bb-data-border)]">
            {rows.map((row) => (
              <tr key={row.result_id} data-testid={`excluded-run-${row.result_id}`}>
                <td class="table-td font-medium text-[var(--bb-data-fg-primary)]">{row.platform}</td>
                <td class="table-td text-[var(--bb-data-fg-muted)]">
                  {formatTimingExclusion(row.display_exclusion_reason, "Display timing is unavailable.")}
                </td>
                <td class="table-td">
                  <a
                    href={resultReceiptHref(row)}
                    aria-label={resultIdentityAriaLabel(row, "receipt")}
                    class="text-xs font-medium no-underline"
                  >
                    Receipt →
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function ListTable({
  benchmark,
  results,
  scaleFactor,
  phase,
  streamCount,
  facets,
  compareEligibilityByResultId,
  compareEligibilityState,
  selectedIds,
  onSelectionChange,
  selectionAtCap,
  selectionLimitReasonId,
}: {
  benchmark: string;
  results: ResultRow[];
  scaleFactor: string;
  phase?: string;
  streamCount?: number | null;
  facets: FacetState;
  compareEligibilityByResultId: Map<string, PlatformRow>;
  compareEligibilityState: "loading" | "ready" | "error";
  selectedIds: Set<string>;
  onSelectionChange: (next: Set<string>) => void;
  selectionAtCap: boolean;
  selectionLimitReasonId?: string;
}) {
  const [sort, setSort] = useState<SortState<BenchmarkListSortKey>>({
    key: "display_geomean_ms",
    direction: "asc",
  });
  const [visibleLimit, setVisibleLimit] = useState(TABLE_RENDER_LIMIT);
  const benchmarkResults = results.filter((r) => canonicalBenchmarkSlug(r.benchmark) === canonicalBenchmarkSlug(benchmark));

  const byCohort = benchmarkResults.filter((r) => {
    if (String(r.scale_factor) !== scaleFactor) return false;
    if (phase && canonicalPhase(r.test_type) !== phase) return false;
    if (streamCount !== undefined && streamCount !== null && (r.stream_count ?? null) !== streamCount) return false;
    return true;
  });

  const filtered = byCohort
    .filter((row) => matchesFacetRow(row, facets, { keys: BENCHMARK_ROW_FACET_KEYS }))
    .sort((a, b) => compareListRows(a, b, sort));
  const [groupBy, setGroupBy] = useState<CohortGroupBy>("none");
  const visibleRows = filtered.slice(0, visibleLimit);
  const groupedRows = useMemo(() => {
    return limitCohortGroups(
      groupCohortRows(
        filtered,
        groupBy,
        (row) => row.platform_version ?? null,
      ),
      filtered,
      visibleLimit,
    );
  }, [filtered, groupBy, visibleLimit]);
  const runIdentityLabels = formatRunIdentitiesForCohort(filtered, "table");

  const skipVisibleLimitResetOnMount = useRef(true);
  useEffect(() => {
    if (skipVisibleLimitResetOnMount.current) {
      skipVisibleLimitResetOnMount.current = false;
      return;
    }
    setVisibleLimit(TABLE_RENDER_LIMIT);
  }, [
    benchmark,
    scaleFactor,
    phase,
    streamCount,
    facets.platform,
    facets.execution_mode,
    facets.tuning_mode,
    facets.trust_tier,
    facets.validation_status,
    facets.deployment_class,
    facets.cloud_provider,
    facets.cloud_region,
    facets.instance_or_warehouse,
    facets.storage_format,
    facets.cost_status,
    facets.date_window,
    facets.platform_version,
    facets.arch,
    facets.cpu_family,
    facets.memory_gb,
    sort.key,
    sort.direction,
  ]);

  const compareEligibilityReady = compareEligibilityState === "ready";
  const compareEligibilityStatusId = "benchmark-compare-eligibility-status";
  const compareEligibilityStatus =
    compareEligibilityState === "loading"
      ? "Compare eligibility is loading."
      : compareEligibilityState === "error"
        ? "Compare eligibility is unavailable for this cohort."
        : null;

  function toggleSort(key: BenchmarkListSortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, direction: prev.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    );
  }

  function ariaSort(key: BenchmarkListSortKey): "ascending" | "descending" | "none" {
    if (sort.key !== key) return "none";
    return sort.direction === "asc" ? "ascending" : "descending";
  }

  function sortArrow(key: BenchmarkListSortKey) {
    if (sort.key !== key) return " ↕";
    return sort.direction === "asc" ? " ↑" : " ↓";
  }

  function sortAnnouncement(key: BenchmarkListSortKey) {
    if (sort.key !== key) return null;
    return (
      <span class="sr-only">
        {sort.direction === "asc" ? "sorted ascending" : "sorted descending"}
      </span>
    );
  }

  if (filtered.length === 0) {
    return (
      <p class="text-[var(--bb-data-fg-muted)]">
        No results found for SF {scaleFactor}.
      </p>
    );
  }

  return (
    <div
      class="overflow-hidden rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] shadow-sm"
      style="contain: paint"
      aria-busy={!compareEligibilityReady}
    >
      <ResultsCardToolbar
        left={
          <span>
            Showing {visibleRows.length.toLocaleString()} of {filtered.length.toLocaleString()} results for SF {scaleFactor}
          </span>
        }
        right={
          <GroupBySelect
            id="benchmark-list-group-by"
            testId="benchmark-list-group-by"
            value={groupBy}
            onChange={setGroupBy}
          />
        }
      />
      {compareEligibilityStatus && (
        <p
          id={compareEligibilityStatusId}
          class="border-b border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-2 text-xs text-[var(--bb-data-fg-muted)]"
          role="status"
        >
          {compareEligibilityStatus}
        </p>
      )}
      <ResultsBasisStatement>
        Geomean query time uses the median of each query's published measurement passes, then the
        geometric mean across queries. Warmup passes are excluded. Dates, counts, and power scores use
        the definitions shown in their columns and receipts.
      </ResultsBasisStatement>
      <div class="overflow-x-auto" data-testid="benchmark-list-scroll-container">
      <table
        class="min-w-full divide-y divide-[var(--bb-data-border)]"
        aria-label={`${canonicalBenchmarkSlug(benchmark)} ${formatScaleFactor(scaleFactor)}${phase ? ` ${phase}` : ""} results`}
      >
        <thead class="bg-[var(--bb-surface-data-muted)]">
          <tr>
            <th class="py-3 pl-4 pr-1 text-left" aria-label="Select for comparison" scope="col" />
            <ListSortHeader
              label="Platform"
              sortKey="platform"
              ariaSort={ariaSort}
              sortArrow={sortArrow}
              sortAnnouncement={sortAnnouncement}
              onSort={toggleSort}
            />
            <ListSortHeader
              label="Scale"
              sortKey="scale_factor"
              ariaSort={ariaSort}
              sortArrow={sortArrow}
              sortAnnouncement={sortAnnouncement}
              onSort={toggleSort}
            />
            <ListSortHeader
              label="Date"
              sortKey="run_date"
              ariaSort={ariaSort}
              sortArrow={sortArrow}
              sortAnnouncement={sortAnnouncement}
              onSort={toggleSort}
            />
            <ListSortHeader
              label={phase === "throughput" ? "Throughput@Size" : "Power score"}
              sortKey="power_score"
              ariaSort={ariaSort}
              sortArrow={sortArrow}
              sortAnnouncement={sortAnnouncement}
              onSort={toggleSort}
            />
            <ListSortHeader
              label="Geomean"
              sortKey="display_geomean_ms"
              ariaSort={ariaSort}
              sortArrow={sortArrow}
              sortAnnouncement={sortAnnouncement}
              onSort={toggleSort}
            />
            <ListSortHeader
              label="Queries"
              sortKey="query_count"
              ariaSort={ariaSort}
              sortArrow={sortArrow}
              sortAnnouncement={sortAnnouncement}
              onSort={toggleSort}
            />
            <th class="table-th text-left whitespace-nowrap">Badges</th>
            <ListSortHeader
              label="Arch"
              sortKey="arch"
              ariaSort={ariaSort}
              sortArrow={sortArrow}
              sortAnnouncement={sortAnnouncement}
              onSort={toggleSort}
            />
            <ListSortHeader
              label="CPU family"
              sortKey="cpu_family"
              ariaSort={ariaSort}
              sortArrow={sortArrow}
              sortAnnouncement={sortAnnouncement}
              onSort={toggleSort}
            />
            <ListSortHeader
              label="Memory"
              sortKey="memory_gb"
              ariaSort={ariaSort}
              sortArrow={sortArrow}
              sortAnnouncement={sortAnnouncement}
              onSort={toggleSort}
            />
            <th class="table-th whitespace-nowrap text-right">Receipt</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
          {groupBy === "none"
            ? visibleRows.map((r, index) => (
                <BenchmarkRow
                  key={r.result_id}
                  entry={r}
                  runIdentityLabel={runIdentityLabels[index] ?? r.platform}
                  compareRow={compareEligibilityByResultId.get(r.result_id)}
                  compareEligibilityReady={compareEligibilityReady}
                  compareEligibilityStatusId={compareEligibilityStatusId}
                  selectedIds={selectedIds}
                  onSelectionChange={onSelectionChange}
                  selectionAtCap={selectionAtCap}
                  selectionLimitReasonId={selectionLimitReasonId}
                />
              ))
            : groupedRows.map((group) => (
                <>
                  <tr
                    key={`group-${group.key}`}
                    class="bg-[var(--bb-surface-data-muted)] font-semibold text-xs text-[var(--bb-data-fg-primary)]"
                  >
                    <td colspan={12} class="px-4 py-2">
                      {group.label} ({group.totalRows} {group.totalRows === 1 ? "result" : "results"})
                    </td>
                  </tr>
                  {group.rows.map((r) => {
                    const index = filtered.findIndex((row) => row.result_id === r.result_id);
                    return (
                      <BenchmarkRow
                        key={r.result_id}
                        entry={r}
                        runIdentityLabel={runIdentityLabels[index] ?? r.platform}
                        compareRow={compareEligibilityByResultId.get(r.result_id)}
                        compareEligibilityReady={compareEligibilityReady}
                        compareEligibilityStatusId={compareEligibilityStatusId}
                        selectedIds={selectedIds}
                        onSelectionChange={onSelectionChange}
                        selectionAtCap={selectionAtCap}
                        selectionLimitReasonId={selectionLimitReasonId}
                      />
                    );
                  })}
                </>
              ))}
        </tbody>
      </table>
      </div>
      {visibleRows.length < filtered.length && (
        <div class="border-t border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-3 text-center">
          <button
            type="button"
            class="btn btn-secondary"
            onClick={() => setVisibleLimit((limit) => limit + TABLE_RENDER_INCREMENT)}
          >
            Show more results
          </button>
        </div>
      )}
    </div>
  );
}

function listRowDisabledReasonCode(compareRow: PlatformRow | undefined): string | null {
  if (!compareRow) return "not_in_cohort_summary";
  if (!isTimingDisplayable(compareRow)) return compareRow.display_exclusion_reason ?? "display_timing_unavailable";
  if (!isComparable(compareRow)) return compareRow.comparison_exclusion_reason ?? "not_comparable";
  return null;
}

function distinctQueryCount(row: { query_count: number; logical_query_count?: number }): number {
  return row.logical_query_count && row.logical_query_count > 0 ? row.logical_query_count : row.query_count;
}

function queryExecutionTitle(row: { query_count: number; logical_query_count?: number }): string | undefined {
  return row.query_count !== distinctQueryCount(row) ? `${row.query_count} query executions` : undefined;
}

function platformVersionTitle(row: { platform_version?: string | null; driver_version?: string | null }): string {
  const { text, driverTitle } = platformVersionDisplay(row);
  return [text ? `Version ${text}` : null, driverTitle].filter((part): part is string => part !== null).join(" · ");
}

function ListCompareDisabledReason({ id, copy }: { id?: string; copy: ReturnType<typeof describeCompareExclusionReason> }) {
  if (copy === null) return null;
  return (
    <div id={id} class="mt-1 text-xs text-[var(--bb-data-fg-muted)]" data-testid="list-row-disabled-reason">
      <span class="font-medium text-[var(--bb-tone-warning-fg)]">Why unavailable: {copy.shortText}</span>
      <span class="block">{copy.recoveryHint}</span>
    </div>
  );
}

function BenchmarkRow({
  entry,
  runIdentityLabel,
  compareRow,
  compareEligibilityReady,
  compareEligibilityStatusId,
  selectedIds,
  onSelectionChange,
  selectionAtCap,
  selectionLimitReasonId,
}: {
  entry: ResultRow;
  runIdentityLabel: string;
  compareRow: PlatformRow | undefined;
  compareEligibilityReady: boolean;
  compareEligibilityStatusId: string;
  selectedIds: Set<string>;
  onSelectionChange: (next: Set<string>) => void;
  selectionAtCap: boolean;
  selectionLimitReasonId?: string;
}) {
  const compareId = compareRow ? compareIdForRow(compareRow) : null;
  const isSelected = compareId !== null && selectedIds.has(compareId);
  const managedCloud = isManagedCloudRun(entry);
  const clientTitle = clientHardwareTitle(entry);
  const disabledReasonCode = compareEligibilityReady ? listRowDisabledReasonCode(compareRow) : null;
  const disabledCopy = describeCompareExclusionReason(disabledReasonCode);
  const capDisabled = selectionAtCap && !isSelected;
  const selectionDisabled = !compareEligibilityReady || disabledCopy !== null || capDisabled;
  const reasonId = disabledCopy
    ? `benchmark-list-compare-reason-${entry.result_id}`
    : capDisabled
      ? selectionLimitReasonId
    : compareEligibilityReady
      ? undefined
      : compareEligibilityStatusId;

  function toggle() {
    if (!compareId || selectionDisabled) return;
    const next = new Set(selectedIds);
    if (next.has(compareId)) next.delete(compareId);
    else next.add(compareId);
    onSelectionChange(next);
  }

  return (
    <tr class="hover:bg-[var(--bb-surface-data-muted)]" data-testid={`list-${entry.result_id}`}>
      <td class="py-3 pl-4 pr-1">
        <input
          type="checkbox"
          checked={isSelected}
          disabled={selectionDisabled}
          onChange={toggle}
          aria-describedby={reasonId}
          title={disabledCopy?.detailText ?? undefined}
          class="h-4 w-4 rounded border-[var(--bb-data-border-strong)] disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={compareSelectionLabel({
            platform: entry.platform,
            benchmark: entry.benchmark,
            scaleFactor: entry.scale_factor,
            phase: canonicalPhase(entry.test_type),
            runDate: entry.run_date,
            resultId: entry.result_id,
          })}
          data-testid={`benchmark-list-compare-checkbox-${entry.result_id}`}
        />
      </td>
      <td class="table-td min-w-[11rem]">
        <RunIdentityLabel
          label={runIdentityLabel}
          href={`/results/p/${entry.platform_id}/`}
          class="block max-w-[20rem] truncate no-underline"
          title={platformVersionTitle(entry)}
        />
        {entry.compliance_class && entry.compliance_class !== "official" && (
          <span class="ml-2 text-xs text-[var(--bb-data-fg-subtle)]" data-testid={`benchmark-list-compliance-${entry.result_id}`}>
            {complianceLabel(entry.compliance_class)}
          </span>
        )}
        <ListCompareDisabledReason id={reasonId} copy={disabledCopy} />
      </td>
      <td class="table-td">SF {entry.scale_factor}</td>
      <td class="table-td text-[var(--bb-data-fg-muted)]"><RunDateChip runDate={entry.run_date} /></td>
      <td class="table-td font-mono">
        {fmtScore(phaseScoreValue(entry))}
        {isDerivedPowerScore(entry.power_score_source) && (
          <span
            class="ml-1 font-sans text-xs text-[var(--bb-data-fg-subtle)]"
            title={DERIVED_POWER_NOTE}
            data-testid={`benchmark-list-derived-power-${entry.result_id}`}
          >
            derived
          </span>
        )}
      </td>
      <td class="table-td font-mono">{fmtGeomean(entry.display_geomean_ms ?? entry.geomean_ms)}</td>
      <td class="table-td text-[var(--bb-data-fg-muted)]" title={queryExecutionTitle(entry)}>{distinctQueryCount(entry)}</td>
      <td class="table-td min-w-[12rem]">
        <div class="flex flex-wrap items-center gap-1">
          <TrustBadge trustLabel={entry.trust_label} compact />
          <FundingChip funding={entry.funding} compact />
          <ValidationBadge
            validationStatus={entry.validation_status}
            overrideRules={parseOverrideRules(entry.override_rules)}
            showMissing
          />
          {entry.tuning_mode && (
            <TuningBadge
              tuningMode={entry.tuning_mode}
              tuningValidationStatus={entry.tuning_validation_status}
            />
          )}
        </div>
      </td>
      <td class="table-td whitespace-nowrap text-[var(--bb-data-fg-muted)]" title={managedCloud ? clientTitle : undefined}>
        {!managedCloud && entry.arch ? formatArchitecture(entry.arch) : "—"}
      </td>
      <td class="table-td whitespace-nowrap text-[var(--bb-data-fg-muted)]" title={managedCloud ? clientTitle : undefined}>
        {!managedCloud && entry.cpu_family ? formatCpuFamily(entry.cpu_family) : "—"}
      </td>
      <td class="table-td whitespace-nowrap text-[var(--bb-data-fg-muted)]" title={managedCloud ? clientTitle : undefined}>
        {!managedCloud && entry.memory_gb != null ? formatMemoryGb(entry.memory_gb) : "—"}
      </td>
      <td class="table-td text-right">
        <a
          href={resultReceiptHref(entry)}
          aria-label={resultIdentityAriaLabel(entry, "receipt")}
          class="whitespace-nowrap text-xs font-medium no-underline"
        >
          Receipt →
        </a>
      </td>
    </tr>
  );
}

function ListSortHeader({
  label,
  sortKey,
  ariaSort,
  sortArrow,
  sortAnnouncement,
  onSort,
}: {
  label: string;
  sortKey: BenchmarkListSortKey;
  ariaSort: (key: BenchmarkListSortKey) => "ascending" | "descending" | "none";
  sortArrow: (key: BenchmarkListSortKey) => string;
  sortAnnouncement: (key: BenchmarkListSortKey) => ComponentChildren;
  onSort: (key: BenchmarkListSortKey) => void;
}) {
  return (
    <th class="p-0 whitespace-nowrap" scope="col" aria-sort={ariaSort(sortKey)}>
      <button
        type="button"
        class="table-th relative block w-full cursor-pointer select-none whitespace-nowrap border-0 bg-transparent text-left"
        onClick={() => onSort(sortKey)}
      >
        {label}{sortArrow(sortKey)}
        {sortAnnouncement(sortKey)}
      </button>
    </th>
  );
}

function compareListRows(a: ResultRow, b: ResultRow, sort: SortState<BenchmarkListSortKey>): number {
  if (sort.key === "platform") {
    return sort.direction === "asc"
      ? a.platform.localeCompare(b.platform)
      : b.platform.localeCompare(a.platform);
  }
  if (sort.key === "arch" || sort.key === "cpu_family") {
    const aVal = a[sort.key] ?? "";
    const bVal = b[sort.key] ?? "";
    if (!aVal && !bVal) return 0;
    if (!aVal) return 1;
    if (!bVal) return -1;
    return sort.direction === "asc" ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
  }
  if (sort.key === "run_date") {
    if (a.run_date === b.run_date) return 0;
    const order = a.run_date < b.run_date ? -1 : 1;
    return sort.direction === "asc" ? order : -order;
  }
  if (sort.key === "display_geomean_ms") {
    return compareNullableNumber(
      a.display_geomean_ms ?? a.geomean_ms,
      b.display_geomean_ms ?? b.geomean_ms,
      sort.direction,
    );
  }
  if (sort.key === "memory_gb") {
    return compareNullableNumber(a.memory_gb ?? null, b.memory_gb ?? null, sort.direction);
  }
  if (sort.key === "power_score") {
    return compareNullableNumber(phaseScoreValue(a), phaseScoreValue(b), sort.direction);
  }
  return compareNullableNumber(a[sort.key], b[sort.key], sort.direction);
}

function compareNullableNumber(a: number | null, b: number | null, direction: SortDirection): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return direction === "asc" ? a - b : b - a;
}
