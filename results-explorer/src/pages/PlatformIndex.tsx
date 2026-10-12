import type { ComponentChildren } from "preact";
import type { DetailResult } from "@/types";
import { PlatformBasisControl } from "@/components/PlatformBasisControl";
import { platformRowsForBasis } from "@/lib/platformMeasurementBasis";
import { BASIS_URL_KEY, DEFAULT_BASIS, basisSerde, encodeBasis, formatBasisLabel, isDefaultBasis, parseAvailableBases } from "@/lib/measurementBasis";
import { useUrlState } from "@/lib/useUrlState";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { RoutableProps } from "preact-router";
import { route } from "preact-router";
import type { PlatformIndexRowRow } from "@/lib/duckdbQueries";
import { getCohortBasisDetails, getResultsBasisAvailability, getPlatformIndexRows } from "@/lib/duckdbQueries";
import { useFacetState, type DateWindowFacet, type ExplorerFacetKey, type FacetState } from "@/lib/facetModel";
import { hasActiveFacets, matchesFacetRow, singleFacetValue } from "@/lib/facetMatching";
import { singleValueFilterReason } from "@/lib/cohortFilterReason";
import { CohortFilterPanel, type CohortFilterFieldSpec } from "@/components/CohortFilterPanel";
import { ResultsCardToolbar, GroupBySelect, ResultsBasisStatement } from "@/components/ResultsCard";
import { AnalysisCard, AnalysisCardGrid } from "@/components/AnalysisCardGrid";
import { paletteColor, timeSeriesColor } from "@/lib/chartTheme";
import {
  canonicalBenchmarkSlug,
  canonicalPhase,
  formatArchitecture,
  formatBenchmarkLabel,
  formatCpuFamily,
  formatMemoryGb,
  formatPhaseWithStreams,
  formatTrustLabel,
  formatValidationStatus,
  isValidationNotClean,
  parseOverrideRules,
} from "@/lib/displayLabels";
import {
  normalizePrimaryMetric,
  phaseScoreValue,
  primaryMetricHigherIsBetter,
  type PrimaryMetric,
} from "@/lib/displayEligibility";
import {
  compareCohortLockReason,
  compareCohortSignatureForRow,
  compareCohortSummary,
  compareSelectionLabel,
} from "@/lib/compareCohort";
import {
  buildCompareUrl,
  compareIdForRow,
  resultIdentityAriaLabel,
  resultReceiptHref,
  visibleResultIdForRow,
  MAX_COMPARE_SELECTIONS,
} from "@/lib/resultLinks";
import {
  describeCompareExclusionReason,
  summarizeCompareExclusionReasons,
  type CompareExclusionReasonCopy,
} from "@/lib/compareExclusionReasons";
import { formatCount, formatSelectedCount } from "@/lib/copyFormatters";
import { humanizeBenchmark, fmtScore, fmtGeomean, errMsg } from "@/utils";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Breadcrumb } from "@/components/Breadcrumb";
import { TrustBadge, ValidationBadge } from "@/components/TrustBadge";
import { FundingChip } from "@/components/FundingChip";
import { TuningBadge, tuningLabel } from "@/components/TuningBadge";
import { TimeSeries } from "@/components/TimeSeries";
import { ProvenanceLegend } from "@/components/ProvenanceLegend";
import { TableScrollHint } from "@/components/TableScrollHint";
import { DataTable } from "@/components/DataTable";
import { CompareTray } from "@/components/CompareTray";
import { TrayAnnouncer } from "@/components/TrayAnnouncer";
import { RunDateChip } from "@/components/RunAge";
import { splitVersion } from "@/lib/versionLabel";
import { PageHeader } from "@/components/PageHeader";
import type { SortState } from "@/types";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { formatRunIdentitiesForCohort } from "@/lib/runIdentity";
import {
  groupCohortRows,
  limitCohortGroups,
  type CohortGroupBy,
} from "@/lib/queryFilters";

interface PlatformIndexProps extends RoutableProps {
  platform?: string;
}

type PlatformSortKey = "benchmark" | "scale_factor" | "run_date" | "power_score" | "geomean_ms" | "arch" | "cpu_family" | "memory_gb";
type TrendMetric = PrimaryMetric;
const TABLE_RENDER_LIMIT = 200;
const TABLE_RENDER_INCREMENT = 200;
const MIN_TREND_OBSERVATIONS = 3;
const PLATFORM_TABLE_COLUMNS = [
  "compare",
  "version",
  "benchmark",
  "scale",
  "phase",
  "metric_contract",
  "date",
  "power_score",
  "geomean",
  "queries",
  "source",
  "arch",
  "cpu_family",
  "memory_gb",
  "receipt",
] as const;
type PlatformTableColumn = (typeof PLATFORM_TABLE_COLUMNS)[number];
const PLATFORM_ROUTE_ALIASES: Readonly<Record<string, string>> = {
  clickhouse_local: "clickhouse-local",
};
const PLATFORM_RESULT_FACET_KEYS: ExplorerFacetKey[] = [
  "benchmark",
  "scale_factor",
  "phase",
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

interface TrendCohort {
  key: string;
  label: string;
  primaryMetric: TrendMetric;
  metricDescription: string;
  observationCount: number;
  entries: PlatformIndexRowRow[];
}

function normalizePlatformKey(value: string): string {
  return value.trim().toLowerCase();
}

function canonicalPlatformRouteKey(value: string): string {
  const normalized = normalizePlatformKey(value);
  return PLATFORM_ROUTE_ALIASES[normalized] ?? normalized;
}

function platformRowsForRequest(rows: PlatformIndexRowRow[], platform: string): PlatformIndexRowRow[] {
  const requested = canonicalPlatformRouteKey(platform);
  const variantMatches = rows.filter(
    (row) => normalizePlatformKey(row.variant_id || row.platform_id) === requested,
  );
  if (variantMatches.length > 0) return variantMatches;
  const idMatches = rows.filter((row) => normalizePlatformKey(row.platform_id) === requested);
  if (idMatches.length > 0) return idMatches;
  return rows.filter((row) => normalizePlatformKey(row.platform) === requested);
}

function trendMetricDescription(metric: TrendMetric): string {
  if (metric === "power_score") return "Power score (higher is better)";
  if (metric === "throughput_at_size") return "Throughput@Size (higher is better)";
  return "Geomean latency (lower is better)";
}

function versionText(entry: { driver_version: string | null; platform_version?: string | null }): string | null {
  const parts = splitVersion(entry.driver_version ?? entry.platform_version ?? null);
  return parts ? (parts.suffix ? `${parts.core}…` : parts.core) : null;
}

function buildVersionCellLabels(rows: readonly PlatformIndexRowRow[]): string[] {
  const base = rows.map((row) => versionText(row) ?? row.short_id);
  const displayedKey = (row: PlatformIndexRowRow, index: number) =>
    [base[index], row.benchmark, row.scale_factor, row.phase, row.run_date].join("\u0000");
  const counts = new Map<string, number>();
  rows.forEach((row, index) => {
    const key = displayedKey(row, index);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  return rows.map((row, index) =>
    (counts.get(displayedKey(row, index)) ?? 0) > 1 ? `${base[index]} · ${row.short_id}` : base[index]!,
  );
}

function primaryMetricContract(metric: string): string {
  return trendMetricDescription(normalizeTrendMetric(metric));
}

function primaryMetricShort(metric: string): string {
  const normalized = normalizeTrendMetric(metric);
  if (normalized === "power_score") return "Power score ↑";
  if (normalized === "throughput_at_size") return "Throughput@Size ↑";
  return "Geomean ↓";
}

function platformTableColumnIndex(column: PlatformTableColumn, showMetricContract: boolean): number {
  const baseIndex = PLATFORM_TABLE_COLUMNS.indexOf(column) + 1;
  const metricContractIndex = PLATFORM_TABLE_COLUMNS.indexOf("metric_contract") + 1;
  return !showMetricContract && baseIndex > metricContractIndex ? baseIndex - 1 : baseIndex;
}

function selectedCohortDifferences(rows: PlatformIndexRowRow[]): string[] {
  if (rows.length < 2) return [];
  const first = rows[0]!;
  const differs = (field: keyof PlatformIndexRowRow) => rows.some((row) => row[field] !== first[field]);
  const differences: string[] = [];
  if (differs("benchmark")) differences.push("benchmark");
  if (differs("scale_factor")) differences.push("scale factor");
  if (differs("phase")) differences.push("phase");
  if (differs("primary_metric")) differences.push("primary metric");
  return differences;
}

function platformCompareGuidanceMessage(
  selectedRows: PlatformIndexRowRow[],
  selectedCount: number,
  platformName: string,
): string {
  const selectedStatus = formatSelectedCount(selectedCount, "result", MAX_COMPARE_SELECTIONS);
  if (selectedCount === 0) {
    return `${selectedStatus}. Select two or more ${platformName} results from the same benchmark, scale, phase, and primary metric for a decision-grade comparison.`;
  }
  if (selectedCount === 1) {
    const row = selectedRows[0];
    const ranking = row ? compareCohortSummary(compareCohortSignatureForRow(row)) : "this ranking";
    return `${selectedStatus} in ${ranking}. Select one more result from the same comparable ranking.`;
  }
  const differences = selectedCohortDifferences(selectedRows);
  if (differences.length > 0) {
    return `${selectedStatus}, but they differ by ${differences.join(", ")}. Compare will keep the receipts visible and may suppress winner claims for mixed rankings.`;
  }
  const first = selectedRows[0];
  const ranking = first ? compareCohortSummary(compareCohortSignatureForRow(first)) : "one ranking";
  return `${selectedStatus} in ${ranking}. The sticky tray opens Compare with matching ranking context.`;
}

export function PlatformIndex({ platform = "" }: PlatformIndexProps) {
  const resultsScrollerRef = useRef<HTMLDivElement>(null);
  const [rows, setRows] = useState<PlatformIndexRowRow[] | null>(null);
  const [basis, setBasis] = useUrlState(BASIS_URL_KEY, DEFAULT_BASIS, basisSerde);
  const basisCache = useRef(new Map<string, DetailResult>());
  const [basisDetails, setBasisDetails] = useState<Map<string, DetailResult>>(new Map());
  const [basisOptions, setBasisOptions] = useState([DEFAULT_BASIS, { ...DEFAULT_BASIS, statistic: "min" as const }]);
  const [basisLoading, setBasisLoading] = useState(false);
  const [basisError, setBasisError] = useState<string | null>(null);
  const requestedRows = useMemo(() => platformRowsForRequest(rows ?? [], platform), [rows, platform]);
  useEffect(() => {
    let cancelled = false;
    const defaults = [DEFAULT_BASIS, { ...DEFAULT_BASIS, statistic: "min" as const }];
    setBasisOptions(defaults);
    if (requestedRows.length === 0) return;
    void getResultsBasisAvailability(requestedRows.map((row) => row.result_id)).catch(() => []).then((available) => {
      if (cancelled) return;
      const options = new Map(defaults.map((option) => [encodeBasis(option), option]));
      for (const row of available) for (const option of parseAvailableBases(row?.available_bases)) options.set(encodeBasis(option), option);
      options.set(encodeBasis(basis), basis);
      setBasisOptions([...options.values()]);
    });
    return () => { cancelled = true; };
  }, [requestedRows]);
  useEffect(() => {
    let cancelled = false;
    setBasisError(null);
    if (isDefaultBasis(basis)) {
      setBasisLoading(false);
      setSelected((current) => new Set([...current].filter((id) => requestedRows.some((row) => row.result_id === id && !row.comparison_exclusion_reason))));
      return;
    }
    setBasisLoading(true);
    const uncachedIds = requestedRows
      .map((row) => row.result_id)
      .filter((id) => !basisCache.current.has(id));

    const loadBasisData = async () => {
      if (uncachedIds.length > 0) {
        const fetched = await getCohortBasisDetails(uncachedIds);
        for (const [id, detail] of fetched) {
          basisCache.current.set(id, detail);
        }
      }
      const byId = new Map<string, DetailResult>();
      for (const row of requestedRows) {
        const detail = basisCache.current.get(row.result_id);
        if (detail) byId.set(row.result_id, detail);
      }
      return byId;
    };

    void loadBasisData().then((byId) => {
      if (cancelled) return;
      setBasisDetails(byId);
      const eligibleIds = new Set(platformRowsForBasis(requestedRows, byId, basis).filter((row) => !row.comparison_exclusion_reason).map((row) => row.result_id));
      setSelected((current) => new Set([...current].filter((id) => eligibleIds.has(id))));
      setBasisLoading(false);
    }).catch(() => {
      if (cancelled) return;
      setBasisDetails(new Map());
      setSelected(new Set());
      setBasisError("Could not load measurement passes. Choose the published basis or retry.");
      setBasisLoading(false);
    });
    return () => { cancelled = true; };
  }, [requestedRows, basis]);
  const [error, setError] = useState<string | null>(null);
  const [rowsRetryToken, setRowsRetryToken] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [visibleLimit, setVisibleLimit] = useState(TABLE_RENDER_LIMIT);
  const [groupBy, setGroupBy] = useState<CohortGroupBy>("none");
  const [openAnalysisCardIds, setOpenAnalysisCardIds] = useState<Set<string>>(() => new Set());
  const { facets, setFacet } = useFacetState();
  const tuningFilter = singleFacetValue(facets.tuning_mode, "all") ?? "all";
  const setTuningFilter = (value: string) => setFacet("tuning_mode", value === "all" ? [] : [value]);
  const benchmarkFilter = singleFacetValue(facets.benchmark, "all") ?? "all";
  const scaleFilter = singleFacetValue(facets.scale_factor, "all") ?? "all";
  const phaseFilter = singleFacetValue(facets.phase, "all") ?? "all";
  const validationFilter = singleFacetValue(facets.validation_status, "all") ?? "all";
  const archFilter = singleFacetValue(facets.arch, "all") ?? "all";
  const cpuFamilyFilter = singleFacetValue(facets.cpu_family, "all") ?? "all";
  const memoryFilter = singleFacetValue(facets.memory_gb, "all") ?? "all";
  const platformVersionFilter = facets.platform_version.length === 0
    ? "all"
    : facets.platform_version.length === 1
      ? facets.platform_version[0]!
      : "__multiple__";
  const trustFilterValue =
    facets.trust_tier.length === 0 ? "all" : facets.trust_tier.length === 1 ? facets.trust_tier[0]! : "__multiple__";
  const dateWindowFilter: DateWindowFacet = facets.date_window;
  const setSingleArrayFacet = (
    key: "benchmark" | "scale_factor" | "phase" | "trust_tier" | "validation_status" | "platform_version" | "arch" | "cpu_family" | "memory_gb",
    value: string,
  ) => setFacet(key, value === "all" ? [] : [value]);
  const hasPlatformFilters = hasActiveFacets(facets, PLATFORM_RESULT_FACET_KEYS);
  const resetPlatformFilters = () => {
    for (const key of PLATFORM_RESULT_FACET_KEYS) {
      if (key === "date_window") setFacet(key, "all");
      else setFacet(key, [] as never);
    }
  };
  const [sort, setSort] = useState<SortState<PlatformSortKey>>({
    key: "run_date",
    direction: "desc",
  });
  const platformDisplayName = rows ? platformRowsForRequest(rows, platform)[0]?.platform ?? platform : platform;
  useDocumentTitle(`${platformDisplayName} · BenchBox Results`);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getPlatformIndexRows()
      .then(async (r) => {
        if (cancelled) return;
        if (r.length > 0) {
          setRows(r);
          return;
        }
        const retried = await getPlatformIndexRows();
        if (!cancelled) setRows(retried);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errMsg(err));
      });
    return () => {
      cancelled = true;
    };
  }, [rowsRetryToken]);

  useEffect(() => {
    if (!rows) return;
    const requested = normalizePlatformKey(platform);
    const canonical = canonicalPlatformRouteKey(platform);
    if (requested === canonical) return;
    if (rows.some((row) => normalizePlatformKey(row.platform_id) === canonical)) {
      route(`/results/p/${canonical}/${window.location.search}`, true);
    }
  }, [platform, rows]);

  useEffect(() => {
    setVisibleLimit(TABLE_RENDER_LIMIT);
  }, [
    platform,
    facets.benchmark,
    facets.scale_factor,
    facets.phase,
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

  if (error) return <ErrorMessage message={error} onRetry={() => setRowsRetryToken((t) => t + 1)} />;
  if (!rows) return <LoadingSpinner message="Loading results..." />;
  if (rows.length === 0) {
    return (
      <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <Breadcrumb crumbs={[{ label: "Results", href: "/results/" }]} />
        <p class="mt-6 text-[var(--bb-data-fg-muted)]">
          No published platform results are available in this snapshot.
        </p>
      </div>
    );
  }

  const allPlatformResults = platformRowsForBasis(requestedRows, basisDetails, basis);
  const routeMetricContracts = new Set(
    allPlatformResults.map((row) => primaryMetricContract(row.primary_metric)),
  );
  const hoistedMetricContract =
    routeMetricContracts.size === 1 ? routeMetricContracts.values().next().value ?? null : null;
  const showMetricContract = hoistedMetricContract === null;
  const platformColumnCount = PLATFORM_TABLE_COLUMNS.length - (showMetricContract ? 0 : 1);
  const canonicalPlatformId = normalizePlatformKey(allPlatformResults[0]?.platform_id ?? canonicalPlatformRouteKey(platform));

  const platformOptions = (() => {
    const byId = new Map<string, { platform_id: string; platform: string }>();
    for (const row of rows) {
      const platform_id = normalizePlatformKey(row.platform_id);
      if (!byId.has(platform_id)) byId.set(platform_id, { platform_id, platform: row.platform });
    }
    return [...byId.values()].sort((a, b) => a.platform.localeCompare(b.platform));
  })();

  const tuningModes = [
    ...new Set(allPlatformResults.map((r) => r.tuning_mode).filter((m): m is string => m !== null)),
  ].sort();

  const benchmarkOptions = [...new Set(allPlatformResults.map((r) => r.benchmark))].sort();
  const scaleOptions = [
    ...new Set(allPlatformResults.map((r) => r.scale_factor)),
  ].sort((a, b) => a - b);
  const phaseOptions = [...new Set(allPlatformResults.map((r) => r.phase))].sort();
  const trustOptions = [
    ...new Set(
      allPlatformResults
        .map((r) => r.trust_label)
        .filter((label): label is string => label !== null && label !== undefined),
    ),
  ].sort();
  const validationOptions = [
    ...new Set(
      allPlatformResults
        .map((r) => r.validation_status)
        .filter((status): status is string => status !== null && status !== undefined),
    ),
  ].sort();
  const platformVersionOptions = [
    ...new Set(
      allPlatformResults
        .map((r) => r.platform_version)
        .filter((version): version is string => version !== null && version !== undefined),
    ),
  ].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const archOptions = [
    ...new Set(allPlatformResults.map((r) => r.arch).filter((a): a is string => a !== null && a !== undefined)),
  ].sort();
  const cpuFamilyOptions = [
    ...new Set(allPlatformResults.map((r) => r.cpu_family).filter((c): c is string => c !== null && c !== undefined)),
  ].sort();
  const memoryOptions = [
    ...new Set(
      allPlatformResults.map((r) => r.memory_gb).filter((m): m is number => m !== null && m !== undefined),
    ),
  ].sort((a, b) => a - b);

  const platformResultsRaw = allPlatformResults.filter((row) =>
    matchesFacetRow(row, facets, { keys: PLATFORM_RESULT_FACET_KEYS }),
  );
  const allTrendCohorts = buildTrendCohorts(platformResultsRaw);
  const trendCohorts = allTrendCohorts.filter((cohort) => cohort.observationCount >= MIN_TREND_OBSERVATIONS);
  const sparseTrendCohorts = allTrendCohorts.filter(
    (cohort) => cohort.observationCount > 0 && cohort.observationCount < MIN_TREND_OBSERVATIONS,
  );
  const coverageStats = buildCoverageStats(platformResultsRaw);
  const rowsByResultId = new Map(allPlatformResults.map((row) => [row.result_id, row]));
  const selectedRows = [...selected]
    .map((resultId) => rowsByResultId.get(resultId))
    .filter((row): row is PlatformIndexRowRow => row !== undefined);
  const selectedCompareIds = [...selected].map((resultId) => {
    const row = rowsByResultId.get(resultId);
    return row ? compareIdForRow(row) : resultId;
  });
  const compareUrl = !basisLoading && selected.size >= 2 ? `${buildCompareUrl(selectedCompareIds)}${isDefaultBasis(basis) ? "" : `&basis=${encodeBasis(basis)}`}` : null;
  const compareGuidance = platformCompareGuidanceMessage(selectedRows, selected.size, platformDisplayName);

  const platformResults = [...platformResultsRaw].sort((a, b) => {
    const dir = sort.direction === "asc" ? 1 : -1;
    if (sort.key === "benchmark") return dir * a.benchmark.localeCompare(b.benchmark);
    if (sort.key === "run_date") {
      if (a.run_date === b.run_date) return a.result_id.localeCompare(b.result_id);
      return dir * (a.run_date < b.run_date ? -1 : 1);
    }
    if (sort.key === "arch" || sort.key === "cpu_family") {
      const av = a[sort.key] ?? "";
      const bv = b[sort.key] ?? "";
      if (!av && !bv) return 0;
      if (!av) return 1;
      if (!bv) return -1;
      const comparison = dir * av.localeCompare(bv);
      return comparison !== 0 ? comparison : a.result_id.localeCompare(b.result_id);
    }
    if (sort.key === "memory_gb") {
      const av = a.memory_gb ?? null;
      const bv = b.memory_gb ?? null;
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      const comparison = dir * (av - bv);
      return comparison !== 0 ? comparison : a.result_id.localeCompare(b.result_id);
    }
    const av = sort.key === "power_score" ? phaseScoreValue(a) : a[sort.key];
    const bv = sort.key === "power_score" ? phaseScoreValue(b) : b[sort.key];
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    const comparison = dir * (av - bv);
    return comparison !== 0 ? comparison : a.result_id.localeCompare(b.result_id);
  });
  const visiblePlatformResults = platformResults.slice(0, visibleLimit);
  const groupedPlatformResults = limitCohortGroups(
    groupCohortRows<PlatformIndexRowRow>(
      platformResults,
      groupBy,
      (row) => row.platform_version ?? null,
    ),
    platformResults,
    visibleLimit,
  );
  const runIdentityLabels = formatRunIdentitiesForCohort(platformResults, "table");
  const versionCellLabels = buildVersionCellLabels(platformResults);

  function toggleSort(key: PlatformSortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, direction: prev.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    );
  }

  function sortArrow(key: PlatformSortKey) {
    if (sort.key !== key) return " ↕";
    return sort.direction === "asc" ? " ↑" : " ↓";
  }

  function ariaSort(key: PlatformSortKey): "ascending" | "descending" | "none" {
    if (sort.key !== key) return "none";
    return sort.direction === "asc" ? "ascending" : "descending";
  }

  function ariaSortAnnouncement(key: PlatformSortKey) {
    if (sort.key !== key) return null;
    return (
      <span class="sr-only">
        {sort.direction === "asc" ? "sorted ascending" : "sorted descending"}
      </span>
    );
  }

  function toggleSelect(resultId: string) {
    const row = rowsByResultId.get(resultId);
    if (!row || (!selected.has(resultId) && comparisonExclusionReason(row))) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(resultId)) {
        next.delete(resultId);
      } else if (next.size < MAX_COMPARE_SELECTIONS) {
        next.add(resultId);
      }
      return next;
    });
  }

  const cohortLockSignature = (() => {
    const firstSelectedId = [...selected][0];
    if (firstSelectedId === undefined) return null;
    const firstRow = allPlatformResults.find((row) => row.result_id === firstSelectedId);
    return firstRow ? compareCohortSignatureForRow(firstRow) : null;
  })();
  function cohortLockReason(row: PlatformIndexRowRow): string | undefined {
    if (selected.has(row.result_id)) return undefined;
    return compareCohortLockReason(row, cohortLockSignature);
  }

  function comparisonExclusionReason(row: PlatformIndexRowRow): string | undefined {
    const reason = row.comparison_exclusion_reason;
    if (selected.has(row.result_id) || typeof reason !== "string" || reason.length === 0) return undefined;
    return reason;
  }
  const zeroSelectable = platformResults.length > 0 && platformResults.every((row) => comparisonExclusionReason(row));
  const filtersCausedZeroSelectable = zeroSelectable && hasPlatformFilters && allPlatformResults.some((row) => !comparisonExclusionReason(row));
  const zeroSelectableReasons = summarizeCompareExclusionReasons(
    platformResults.map((row) => comparisonExclusionReason(row)),
  );

  const platformCohortFilterFields: CohortFilterFieldSpec[] = [
    {
      id: "platform-filter-benchmark",
      testId: "platform-filter-benchmark",
      label: "Benchmark",
      value: benchmarkFilter,
      options: [{ value: "all", label: "All benchmarks" }, ...benchmarkOptions.map((value) => ({ value, label: formatBenchmarkLabel(value) }))],
      onChange: (value) => setSingleArrayFacet("benchmark", value),
      disabledReason: singleValueFilterReason(benchmarkOptions.length, benchmarkFilter !== "all"),
    },
    {
      id: "platform-filter-scale",
      testId: "platform-filter-scale",
      label: "Scale",
      value: scaleFilter,
      options: [{ value: "all", label: "All scales" }, ...scaleOptions.map((value) => ({ value: String(value), label: `SF ${value}` }))],
      onChange: (value) => setSingleArrayFacet("scale_factor", value),
      disabledReason: singleValueFilterReason(scaleOptions.length, scaleFilter !== "all"),
    },
    {
      id: "platform-filter-phase",
      testId: "platform-filter-phase",
      label: "Phase",
      value: phaseFilter,
      options: [{ value: "all", label: "All phases" }, ...phaseOptions.map((value) => ({ value, label: value.charAt(0).toUpperCase() + value.slice(1) }))],
      onChange: (value) => setSingleArrayFacet("phase", value),
      disabledReason: singleValueFilterReason(phaseOptions.length, phaseFilter !== "all"),
    },
    {
      id: "tuning-filter",
      label: "Tuning",
      value: tuningFilter,
      options: [{ value: "all", label: "All" }, ...tuningModes.map((m) => ({ value: m, label: tuningLabel(m) }))],
      onChange: (value) => setTuningFilter(value),
      disabledReason: singleValueFilterReason(tuningModes.length, tuningFilter !== "all"),
    },
    {
      id: "platform-filter-version",
      testId: "platform-filter-version",
      label: "Platform version",
      value: platformVersionFilter,
      options: [{ value: "all", label: "All versions" }, ...platformVersionOptions.map((version) => ({ value: version, label: version }))],
      onChange: (value) => setSingleArrayFacet("platform_version", value),
      disabledReason: singleValueFilterReason(platformVersionOptions.length, platformVersionFilter !== "all"),
      multiValueOption:
        platformVersionFilter === "__multiple__"
          ? { value: "__multiple__", label: `${facets.platform_version.length} versions selected` }
          : undefined,
    },
    {
      id: "platform-filter-trust",
      testId: "platform-filter-trust",
      label: "Trust tier",
      value: trustFilterValue,
      options: [{ value: "all", label: "All trust tiers" }, ...trustOptions.map((tier) => ({ value: tier, label: formatTrustLabel(tier) }))],
      onChange: (value) => setSingleArrayFacet("trust_tier", value),
      disabledReason: singleValueFilterReason(trustOptions.length, trustFilterValue !== "all"),
      multiValueOption:
        trustFilterValue === "__multiple__"
          ? { value: "__multiple__", label: `${facets.trust_tier.length} tiers selected` }
          : undefined,
    },
    {
      id: "platform-filter-validation",
      testId: "platform-filter-validation",
      label: "Validation",
      value: validationFilter,
      options: [{ value: "all", label: "All validation" }, ...validationOptions.map((status) => ({ value: status, label: formatValidationStatus(status) }))],
      onChange: (value) => setSingleArrayFacet("validation_status", value),
      disabledReason: singleValueFilterReason(validationOptions.length, validationFilter !== "all"),
    },
    {
      id: "platform-filter-arch",
      testId: "platform-filter-arch",
      label: "Architecture",
      value: archFilter,
      options: [{ value: "all", label: "All architectures" }, ...archOptions.map((option) => ({ value: option, label: formatArchitecture(option) }))],
      onChange: (value) => setSingleArrayFacet("arch", value),
      disabledReason: singleValueFilterReason(archOptions.length, archFilter !== "all"),
    },
    {
      id: "platform-filter-cpu-family",
      testId: "platform-filter-cpu-family",
      label: "CPU family",
      value: cpuFamilyFilter,
      options: [{ value: "all", label: "All CPU families" }, ...cpuFamilyOptions.map((option) => ({ value: option, label: formatCpuFamily(option) }))],
      onChange: (value) => setSingleArrayFacet("cpu_family", value),
      disabledReason: singleValueFilterReason(cpuFamilyOptions.length, cpuFamilyFilter !== "all"),
    },
    {
      id: "platform-filter-memory",
      testId: "platform-filter-memory",
      label: "Memory",
      value: memoryFilter,
      options: [{ value: "all", label: "All memory sizes" }, ...memoryOptions.map((option) => ({ value: String(option), label: formatMemoryGb(option) }))],
      onChange: (value) => setSingleArrayFacet("memory_gb", value),
      disabledReason: singleValueFilterReason(memoryOptions.length, memoryFilter !== "all"),
    },
    {
      id: "platform-filter-date-window",
      testId: "platform-filter-date-window",
      label: "Run date",
      value: dateWindowFilter,
      options: [
        { value: "all", label: "All time" },
        { value: "30d", label: "Last 30 days" },
        { value: "90d", label: "Last 90 days" },
        { value: "365d", label: "Last 365 days" },
      ],
      onChange: (value) => setFacet("date_window", value as DateWindowFacet),
    },
  ];

  return (
    <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        crumbs={[{ label: "Results", href: "/results/" }, { label: platformDisplayName }]}
        eyebrow="Platform"
        title={`${platformDisplayName} Results`}
        meta={
          allPlatformResults.length > 0 ? (
            <>
              <span class="bb-meta-chip" data-testid="platform-run-count">
                {platformResults.length === allPlatformResults.length
                  ? `${allPlatformResults.length} published runs`
                  : `${platformResults.length} of ${allPlatformResults.length} published runs`}
              </span>
              <span class="bb-meta-chip">
                {new Set(allPlatformResults.map((row) => row.benchmark)).size} benchmarks
              </span>
            </>
          ) : undefined
        }
        actions={
          <>
          {platformOptions.length > 1 && (
            <div class="flex items-center gap-2">
              <label class="text-sm font-medium text-[var(--bb-data-fg-primary)]" for="platform-switcher">
                Platform:
              </label>
              <select
                id="platform-switcher"
                data-testid="platform-switcher"
                class="rounded-md border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-3 py-1.5 text-sm shadow-sm"
                value={canonicalPlatformId}
                onChange={(event) => {
                  const next = (event.target as HTMLSelectElement).value;
                  if (next === canonicalPlatformId) return;
                  route(`/results/p/${next}/`);
                }}
              >
                {platformOptions.map((option) => (
                  <option key={option.platform_id} value={option.platform_id}>
                    {option.platform}
                  </option>
                ))}
                {!platformOptions.some((option) => option.platform_id === canonicalPlatformId) && (
                  <option value={canonicalPlatformId}>{platformDisplayName}</option>
                )}
              </select>
            </div>
          )}
          </>
        }
      />

      <CohortFilterPanel
        testId="platform-detail-filters"
        fields={platformCohortFilterFields}
        showClear={hasPlatformFilters}
        clearTestId="platform-filter-reset"
        onClear={resetPlatformFilters}
      />

      <section
        class="mb-4 rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] px-4 py-3 shadow-sm"
        data-testid="platform-compare-guidance"
        aria-label="Platform compare guidance"
      >
        <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 class="text-sm font-semibold text-[var(--bb-data-fg-primary)]">Compare selected results</h2>
            <p
              class="mt-1 text-sm text-[var(--bb-data-fg-muted)]"
              {...(selected.size < 2 ? { "aria-live": "polite" as const, "aria-atomic": "true" as const } : {})}
            >
              {compareGuidance}
            </p>
          </div>
          {compareUrl ? (
            <a
              href={compareUrl}
              class="btn btn-primary shrink-0 text-sm no-underline"
              data-testid="platform-compare-cta"
            >
              Compare {selected.size} selected
            </a>
          ) : (
            <p class="shrink-0 text-sm text-[var(--bb-data-fg-subtle)]" data-testid="platform-compare-cta-pending">
              {selected.size === 1 ? "Select 1 more result" : "Select 2 results to compare"}
            </p>
          )}
        </div>
      </section>

      {zeroSelectable && (
        <section
          class="mb-4 rounded-lg border border-[var(--bb-tone-warning-border)] bg-[var(--bb-tone-warning-bg)] px-4 py-3 text-sm text-[var(--bb-tone-warning-fg)]"
          data-testid="platform-zero-selectable"
          aria-label="No selectable platform compare rows"
        >
          <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 class="font-semibold">No selectable compare rows</h2>
              <p class="mt-1">
                {zeroSelectableReasons.length > 0
                  ? `${zeroSelectableReasons[0]!.count} ${zeroSelectableReasons[0]!.copy.shortText.toLowerCase()} row${
                      zeroSelectableReasons[0]!.count === 1 ? "" : "s"
                    }. ${zeroSelectableReasons[0]!.copy.recoveryHint}`
                  : "The current filters do not expose a comparable run. Clear filters or choose another ranking."}
              </p>
            </div>
            {filtersCausedZeroSelectable && (
              <button type="button" class="btn btn-secondary shrink-0 text-sm" onClick={resetPlatformFilters}>
                Clear filters
              </button>
            )}
          </div>
        </section>
      )}

      <section id="platform-section-results" class="scroll-mt-24 overflow-x-hidden" aria-labelledby="platform-heading-results">
        <h2 id="platform-heading-results" class="mb-3 text-lg font-semibold text-[var(--bb-data-fg-primary)]">
          Results
        </h2>
        {platformResults.length === 0 ? (
        <p class="text-[var(--bb-data-fg-muted)]">
          {allPlatformResults.length > 0 && hasActivePlatformResultFacets(facets)
            ? `No results match the selected filters for platform: ${platformDisplayName}.`
            : `No results found for platform: ${platformDisplayName}.`}
        </p>
      ) : (
        <div class="overflow-hidden rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] shadow-sm">
          <ResultsCardToolbar
            left={
              <PlatformBasisControl basis={basis} options={basisOptions} onChange={setBasis} loading={basisLoading} error={basisError} />
            }
            right={<GroupBySelect id="platform-group-by" testId="platform-group-by" value={groupBy} onChange={setGroupBy} />}
          />
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] px-4 py-3 text-sm text-[var(--bb-data-fg-muted)]">
            <div>
              {visiblePlatformResults.length === platformResults.length ? null : (
                <span>
                  Showing {visiblePlatformResults.length.toLocaleString()} of{" "}
                  {platformResults.length.toLocaleString()} published runs
                </span>
              )}
            </div>
          </div>
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-2 text-xs text-[var(--bb-data-fg-muted)]">
            <span>
              Rows are labelled by comparable ranking: benchmark, scale, phase, and primary metric. A
              non-clean validation label means that result was excluded from ranking on validation grounds.
            </span>
            <TableScrollHint
              scrollerRef={resultsScrollerRef}
              testId="platform-table-scroll-hint"
              label="Scroll for dates, timings, and source labels →"
              wrapperClassName={null}
            />
          </div>
          <ResultsBasisStatement>
            {isDefaultBasis(basis) ? "Geomean query time uses the median of each query’s published measurement passes, then the geometric mean across queries. Warmup passes are excluded." : `Geomean query time uses ${formatBasisLabel(basis)} across each run’s available queries. Published power scores are hidden for this basis.`}
          </ResultsBasisStatement>
          <div ref={resultsScrollerRef} class="overflow-x-auto" data-testid="platform-results-scroll-container">
          <DataTable
            ariaLabel={`${platformDisplayName} results`}
            ariaColCount={platformColumnCount}
            caption={
              hoistedMetricContract ? (
                <span data-testid="platform-hoisted-metric-contract">
                  Results are ranked by: {hoistedMetricContract}
                </span>
              ) : (
                "Platform results"
              )
            }
            class="min-w-max divide-y divide-[var(--bb-data-border)]"
          >
            <thead class="bg-[var(--bb-surface-data-muted)]">
              <tr>
                <th class="table-th w-8" aria-colindex={platformTableColumnIndex("compare", showMetricContract)}>
                  <span class="sr-only">Compare</span>
                </th>
                <th class="table-th" aria-colindex={platformTableColumnIndex("version", showMetricContract)}>Version</th>
                <th class="p-0" scope="col" aria-sort={ariaSort("benchmark")} aria-colindex={platformTableColumnIndex("benchmark", showMetricContract)}>
                  <button
                    type="button"
                    class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                    onClick={() => toggleSort("benchmark")}
                  >
                    Benchmark{sortArrow("benchmark")}
                    {ariaSortAnnouncement("benchmark")}
                  </button>
                </th>
                <th class="p-0" scope="col" aria-sort={ariaSort("scale_factor")} aria-colindex={platformTableColumnIndex("scale", showMetricContract)}>
                  <button
                    type="button"
                    class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                    onClick={() => toggleSort("scale_factor")}
                  >
                    Scale{sortArrow("scale_factor")}
                    {ariaSortAnnouncement("scale_factor")}
                  </button>
                </th>
                <th class="table-th" aria-colindex={platformTableColumnIndex("phase", showMetricContract)}>Phase</th>
                {showMetricContract && (
                  <th
                    class="table-th"
                    aria-colindex={platformTableColumnIndex("metric_contract", true)}
                    title="The metric each run is ranked on, and the direction that counts as better."
                  >
                    Ranked on
                  </th>
                )}
                <th class="p-0" scope="col" aria-sort={ariaSort("run_date")} aria-colindex={platformTableColumnIndex("date", showMetricContract)}>
                  <button
                    type="button"
                    class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                    onClick={() => toggleSort("run_date")}
                  >
                    Date{sortArrow("run_date")}
                    {ariaSortAnnouncement("run_date")}
                  </button>
                </th>
                <th class="p-0" scope="col" aria-sort={ariaSort("power_score")} aria-colindex={platformTableColumnIndex("power_score", showMetricContract)}>
                  <button
                    type="button"
                    class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                    onClick={() => toggleSort("power_score")}
                  >
                    Power score{sortArrow("power_score")}
                    {ariaSortAnnouncement("power_score")}
                  </button>
                </th>
                <th
                  class="p-0"
                  scope="col"
                  aria-sort={ariaSort("geomean_ms")}
                  aria-colindex={platformTableColumnIndex("geomean", showMetricContract)}
                  title="Geometric mean of per-query median execution times (measurement runs only). Lower is faster."
                >
                  <button
                    type="button"
                    class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                    onClick={() => toggleSort("geomean_ms")}
                  >
                    Geomean{sortArrow("geomean_ms")}
                    {ariaSortAnnouncement("geomean_ms")}
                  </button>
                </th>
                <th class="table-th" aria-colindex={platformTableColumnIndex("queries", showMetricContract)}>Queries</th>
                <th class="table-th" aria-colindex={platformTableColumnIndex("source", showMetricContract)}>Labels</th>
                <th
                  class="p-0"
                  scope="col"
                  aria-sort={ariaSort("arch")}
                  aria-colindex={platformTableColumnIndex("arch", showMetricContract)}
                >
                  <button
                    type="button"
                    class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                    onClick={() => toggleSort("arch")}
                  >
                    Arch{sortArrow("arch")}
                    {ariaSortAnnouncement("arch")}
                  </button>
                </th>
                <th
                  class="p-0"
                  scope="col"
                  aria-sort={ariaSort("cpu_family")}
                  aria-colindex={platformTableColumnIndex("cpu_family", showMetricContract)}
                >
                  <button
                    type="button"
                    class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                    onClick={() => toggleSort("cpu_family")}
                  >
                    CPU family{sortArrow("cpu_family")}
                    {ariaSortAnnouncement("cpu_family")}
                  </button>
                </th>
                <th
                  class="p-0"
                  scope="col"
                  aria-sort={ariaSort("memory_gb")}
                  aria-colindex={platformTableColumnIndex("memory_gb", showMetricContract)}
                >
                  <button
                    type="button"
                    class="table-th block w-full text-left cursor-pointer select-none bg-transparent border-0"
                    onClick={() => toggleSort("memory_gb")}
                  >
                    Memory{sortArrow("memory_gb")}
                    {ariaSortAnnouncement("memory_gb")}
                  </button>
                </th>
                <th class="table-th text-right" aria-colindex={platformTableColumnIndex("receipt", showMetricContract)}>
                  Receipt
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
              {groupBy === "none"
                ? visiblePlatformResults.map((r, index) => (
                    <PlatformRow
                      key={r.result_id}
                      entry={r}
                      runIdentityLabel={runIdentityLabels[index] ?? r.platform}
                      versionLabel={versionCellLabels[index] ?? r.short_id}
                      checked={selected.has(r.result_id)}
                      onToggle={() => toggleSelect(r.result_id)}
                      showMetricContract={showMetricContract}
                      disabledReason={
                        comparisonExclusionReason(r) ??
                        cohortLockReason(r) ??
                        (!selected.has(r.result_id) && selected.size >= MAX_COMPARE_SELECTIONS
                          ? `Up to ${MAX_COMPARE_SELECTIONS} runs can be compared.`
                          : undefined)
                      }
                    />
                  ))
                : groupedPlatformResults.map((group) => (
                    <>
                      <tr
                        key={`group-${group.key}`}
                        class="bg-[var(--bb-surface-data-muted)] font-semibold text-xs text-[var(--bb-data-fg-primary)]"
                      >
                        <td colspan={platformColumnCount} class="px-4 py-2">
                          {group.label} ({group.totalRows} {group.totalRows === 1 ? "result" : "results"})
                        </td>
                      </tr>
                      {group.rows.map((r) => {
                        const index = platformResults.findIndex((row) => row.result_id === r.result_id);
                        return (
                          <PlatformRow
                            key={r.result_id}
                            entry={r}
                            runIdentityLabel={runIdentityLabels[index] ?? r.platform}
                            versionLabel={versionCellLabels[index] ?? r.short_id}
                            checked={selected.has(r.result_id)}
                            onToggle={() => toggleSelect(r.result_id)}
                            showMetricContract={showMetricContract}
                            disabledReason={
                              comparisonExclusionReason(r) ??
                              cohortLockReason(r) ??
                              (!selected.has(r.result_id) && selected.size >= MAX_COMPARE_SELECTIONS
                                ? `Up to ${MAX_COMPARE_SELECTIONS} runs can be compared.`
                                : undefined)
                            }
                          />
                        );
                      })}
                    </>
                  ))}
            </tbody>
          </DataTable>
          </div>
          {visiblePlatformResults.length < platformResults.length && (
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
        )}
      </section>

      <TrayAnnouncer count={selected.size} />
      {compareUrl && (
        <CompareTray
          summary={
            <>
              <strong>{formatCount(selected.size, "result")}</strong> selected for compare
            </>
          }
          items={selectedRows.map((row) => ({
            id: row.result_id,
            platform: row.platform,
            benchmarkLabel: humanizeBenchmark(row.benchmark),
            scaleFactor: row.scale_factor,
            phase: row.phase,
            runDate: row.run_date,
            trustLabel: row.trust_label,
            funding: row.funding,
            visibleResultId: visibleResultIdForRow(row),
          }))}
          compareHref={compareUrl}
          compareLabel={`Compare ${selected.size} selected`}
          onClear={() => setSelected(new Set())}
        />
      )}

      <section id="platform-section-analysis" class="mt-8 scroll-mt-24" aria-labelledby="platform-heading-analysis">
        <h2 id="platform-heading-analysis" class="mb-3 text-lg font-semibold text-[var(--bb-data-fg-primary)]">
          Analysis
        </h2>
        <AnalysisCardGrid
          headingId="platform-more-views-title"
          headingLevel="h3"
          title="More views"
          description="Per-query charts (query matrix, ranks, percentiles) need a single ranking and live on each benchmark's page."
        >
          <AnalysisCard
            id="trends"
            title={`Performance trends (${trendCohorts.length})`}
            isOpen={openAnalysisCardIds.has("trends")}
            onToggle={(open) =>
              setOpenAnalysisCardIds((current) => {
                const next = new Set(current);
                if (open) next.add("trends");
                else next.delete("trends");
                return next;
              })
            }
            renderThumbnail={() => <TrendsThumbnail cohorts={trendCohorts} />}
            renderFull={() => (
              <div>
                <p class="mb-4 text-sm text-[var(--bb-data-fg-muted)]">
                  Each trend keeps the benchmark, scale, phase, and measurement fixed. A chart needs at least{" "}
                  {MIN_TREND_OBSERVATIONS} runs.
                </p>
                {trendCohorts.length === 0 && sparseTrendCohorts.length === 0 ? (
                  <p class="text-sm text-[var(--bb-data-fg-subtle)] italic">
                    No trendable metric values are available for the selected filters.
                  </p>
                ) : (
                  <div class="space-y-6">
                    {trendCohorts.map((cohort) => (
                      <section key={cohort.key} data-testid={`trend-cohort-${cohort.key}`} class="space-y-2">
                        <h3 class="text-sm font-medium text-[var(--bb-data-fg-primary)]">{cohort.label}</h3>
                        <p class="text-xs text-[var(--bb-data-fg-muted)]">
                          {cohort.observationCount} observations · {cohort.metricDescription}
                        </p>
                        <TimeSeries entries={cohort.entries} primaryMetric={cohort.primaryMetric} />
                      </section>
                    ))}
                    {sparseTrendCohorts.length > 0 && (
                      <details class="rounded-lg border border-dashed border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-3 py-3">
                        <summary class="cursor-pointer text-sm font-medium text-[var(--bb-data-fg-primary)]">
                          {sparseTrendCohorts.length} {sparseTrendCohorts.length === 1 ? "ranking has" : "rankings have"} too few runs for a trend
                        </summary>
                        <ul class="mt-3 space-y-2 text-sm text-[var(--bb-data-fg-muted)]">
                          {sparseTrendCohorts.map((cohort) => (
                            <li key={cohort.key} data-testid={`trend-sparse-${cohort.key}`}>
                              <span class="font-medium text-[var(--bb-data-fg-primary)]">{cohort.label}</span>: {cohort.observationCount} published {cohort.observationCount === 1 ? "run" : "runs"} · {cohort.metricDescription}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                )}
              </div>
            )}
          />
          <AnalysisCard
            id="coverage"
            title={`Coverage (${coverageStats.length} ${coverageStats.length === 1 ? "benchmark" : "benchmarks"})`}
            isOpen={openAnalysisCardIds.has("coverage")}
            onToggle={(open) =>
              setOpenAnalysisCardIds((current) => {
                const next = new Set(current);
                if (open) next.add("coverage");
                else next.delete("coverage");
                return next;
              })
            }
            renderThumbnail={() => <CoverageThumbnail stats={coverageStats} />}
            renderFull={() => <CoverageTable stats={coverageStats} />}
          />
        </AnalysisCardGrid>
      </section>
      <ProvenanceLegend />
</div>
  );
}

function hasActivePlatformResultFacets(facets: FacetState): boolean {
  return hasActiveFacets(facets, PLATFORM_RESULT_FACET_KEYS);
}

function buildTrendCohorts(rows: PlatformIndexRowRow[]): TrendCohort[] {
  const groups = new Map<string, TrendCohort>();
  for (const row of rows) {
    const primaryMetric = normalizeTrendMetric(row.primary_metric);
    const key = `${row.benchmark}-sf${row.scale_factor}-${row.phase}${row.stream_count == null ? "" : `-${row.stream_count}streams`}-${primaryMetric}`;
    let cohort = groups.get(key);
    if (!cohort) {
      const metricDescription = trendMetricDescription(primaryMetric);
      cohort = {
        key,
        label: `${humanizeBenchmark(row.benchmark)} · SF ${row.scale_factor} · ${formatPhaseWithStreams(row.phase, row.stream_count)} · ${metricDescription}`,
        primaryMetric,
        metricDescription,
        observationCount: 0,
        entries: [],
      };
      groups.set(key, cohort);
    }
    cohort.entries.push(row);
  }

  return [...groups.values()]
    .map((cohort) => {
      const entries = [...cohort.entries].sort((a, b) => a.run_date.localeCompare(b.run_date));
      return {
        ...cohort,
        entries,
        observationCount: entries.filter((entry) => trendValue(entry, cohort.primaryMetric) !== null).length,
      };
    })
    .filter((cohort) => cohort.observationCount > 0)
    .sort((a, b) => a.label.localeCompare(b.label));
}

function normalizeTrendMetric(metric: string): TrendMetric {
  return normalizePrimaryMetric(metric);
}

function trendValue(row: PlatformIndexRowRow, metric: TrendMetric): number | null {
  if (metric === "power_score") return row.power_score;
  if (metric === "throughput_at_size") return row.throughput_at_size ?? null;
  return row.display_geomean_ms;
}

function AnalysisMiniFrame({ children }: { children: ComponentChildren }) {
  return (
    <div class="summary-chart-thumbnail mt-4 flex items-center justify-center rounded-md bg-[var(--bb-surface-data-muted)] p-2">
      {children}
    </div>
  );
}

function AnalysisMiniUnavailable({ label }: { label: string }) {
  return (
    <AnalysisMiniFrame>
      <p class="px-3 text-center text-xs text-[var(--bb-data-fg-subtle)]">{label}</p>
    </AnalysisMiniFrame>
  );
}

function TrendsThumbnail({ cohorts }: { cohorts: TrendCohort[] }) {
  const items = cohorts.slice(0, 4);
  if (items.length === 0) {
    return <AnalysisMiniUnavailable label="No trendable metric values are available for the selected filters." />;
  }
  const width = 80;
  const height = 28;
  return (
    <div class="summary-chart-thumbnail mt-4 grid grid-cols-2 gap-2 rounded-md bg-[var(--bb-surface-data-muted)] p-2">
      {items.map((cohort, index) => {
        const values = cohort.entries
          .map((entry) => trendValue(entry, cohort.primaryMetric))
          .filter((value): value is number => value !== null);
        const label = cohort.label;
        if (values.length === 0) {
          return (
            <div key={cohort.key} class="flex flex-col items-center gap-1">
              <span class="text-[9px] text-[var(--bb-data-fg-subtle)]">No data</span>
            </div>
          );
        }
        const min = Math.min(...values);
        const max = Math.max(...values);
        const span = max - min || 1;
        const points = values
          .map((value, pointIndex) => {
            const x = values.length > 1 ? (pointIndex / (values.length - 1)) * width : width / 2;
            const normalized = (value - min) / span;
            const y = primaryMetricHigherIsBetter(cohort.primaryMetric) ? height - normalized * height : normalized * height;
            return `${x.toFixed(1)},${y.toFixed(1)}`;
          })
          .join(" ");
        return (
          <div key={cohort.key} class="flex flex-col items-center gap-1">
            <svg
              viewBox={`0 0 ${width} ${height}`}
              class="h-7 w-full"
              preserveAspectRatio="none"
              role="img"
              aria-label={`${label} trend thumbnail`}
            >
              <polyline points={points} fill="none" stroke={timeSeriesColor(index)} stroke-width="1.5" />
            </svg>
            <span class="max-w-full truncate text-[9px] text-[var(--bb-data-fg-subtle)]" title={label}>
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

interface CoverageStat {
  benchmark: string;
  scales: number[];
  runs: number;
  latestRunDate: string;
}

function buildCoverageStats(rows: PlatformIndexRowRow[]): CoverageStat[] {
  const byBenchmark = new Map<string, { scales: Set<number>; runs: number; latestRunDate: string }>();
  for (const row of rows) {
    let entry = byBenchmark.get(row.benchmark);
    if (!entry) {
      entry = { scales: new Set(), runs: 0, latestRunDate: row.run_date };
      byBenchmark.set(row.benchmark, entry);
    }
    entry.scales.add(row.scale_factor);
    entry.runs += 1;
    if (row.run_date > entry.latestRunDate) entry.latestRunDate = row.run_date;
  }
  return [...byBenchmark.entries()]
    .map(([benchmark, entry]) => ({
      benchmark,
      scales: [...entry.scales].sort((a, b) => a - b),
      runs: entry.runs,
      latestRunDate: entry.latestRunDate,
    }))
    .sort((a, b) => b.runs - a.runs || a.benchmark.localeCompare(b.benchmark));
}

interface CoverageBarItem {
  label: string;
  runs: number;
}

function coverageBarItems(stats: CoverageStat[]): CoverageBarItem[] {
  const top = stats.slice(0, 5).map((stat) => ({ label: humanizeBenchmark(stat.benchmark), runs: stat.runs }));
  const rest = stats.slice(5);
  if (rest.length === 0) return top;
  return [...top, { label: `${rest.length} others`, runs: rest.reduce((total, stat) => total + stat.runs, 0) }];
}

function CoverageThumbnail({ stats }: { stats: CoverageStat[] }) {
  if (stats.length === 0) {
    return <AnalysisMiniUnavailable label="No benchmark coverage is available for the selected filters." />;
  }
  const bars = coverageBarItems(stats);
  const max = Math.max(...bars.map((bar) => bar.runs), 1);
  return (
    <div class="summary-chart-thumbnail mt-4 w-full space-y-1.5 rounded-md bg-[var(--bb-surface-data-muted)] p-2">
      {bars.map((bar, index) => (
        <div key={bar.label} class="flex items-center gap-1.5">
          <span class="w-16 shrink-0 truncate text-[9px] text-[var(--bb-data-fg-subtle)]" title={bar.label}>
            {bar.label}
          </span>
          <span class="relative h-2 flex-1 rounded-sm bg-[var(--bb-data-border)]">
            <span
              class="absolute inset-y-0 left-0 rounded-sm"
              style={{ width: `${Math.max(4, (bar.runs / max) * 100)}%`, backgroundColor: paletteColor(index) }}
            />
          </span>
          <span class="w-6 shrink-0 text-right font-mono text-[9px] text-[var(--bb-data-fg-subtle)]">{bar.runs}</span>
        </div>
      ))}
    </div>
  );
}

function CoverageTable({ stats }: { stats: CoverageStat[] }) {
  if (stats.length === 0) {
    return (
      <p class="text-sm text-[var(--bb-data-fg-subtle)] italic">
        No benchmark coverage is available for the selected filters.
      </p>
    );
  }
  return (
    <DataTable
      ariaLabel="Benchmark coverage"
      caption="Runs per benchmark for this platform, given the active filters."
      class="min-w-[28rem] w-full divide-y divide-[var(--bb-data-border)]"
    >
      <thead class="bg-[var(--bb-surface-data-muted)]">
        <tr>
          <th class="table-th" scope="col">Benchmark</th>
          <th class="table-th" scope="col">Scales</th>
          <th class="table-th" scope="col">Runs</th>
          <th class="table-th" scope="col">Latest run</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
        {stats.map((stat) => (
          <tr key={stat.benchmark}>
            <th class="table-td text-left font-medium" scope="row">
              <a href={`/results/${canonicalBenchmarkSlug(stat.benchmark)}/`} class="no-underline hover:underline">
                {humanizeBenchmark(stat.benchmark)}
              </a>
            </th>
            <td class="table-td text-[var(--bb-data-fg-muted)]">{stat.scales.map((sf) => `SF ${sf}`).join(", ")}</td>
            <td class="table-td font-mono">{stat.runs}</td>
            <td class="table-td text-[var(--bb-data-fg-muted)]">
              <RunDateChip runDate={stat.latestRunDate} />
            </td>
          </tr>
        ))}
      </tbody>
    </DataTable>
  );
}

interface PlatformRowProps {
  entry: PlatformIndexRowRow;
  runIdentityLabel: string;
  versionLabel: string;
  checked: boolean;
  onToggle: () => void;
  showMetricContract: boolean;
  disabledReason?: string;
}

function PlatformRow({ entry, runIdentityLabel, versionLabel, checked, onToggle, showMetricContract, disabledReason }: PlatformRowProps) {
  const disabledCopy = describeCompareExclusionReason(disabledReason);
  const entryOverrideRules = parseOverrideRules(entry.override_rules);
  const reasonId = disabledCopy ? `platform-compare-reason-${entry.result_id}` : undefined;
  return (
    <tr class="hover:bg-[var(--bb-surface-data-muted)]" data-testid={entry.result_id}>
      <td class="table-td" aria-colindex={platformTableColumnIndex("compare", showMetricContract)}>
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          disabled={Boolean(disabledReason)}
          title={disabledCopy?.detailText ?? disabledReason}
          class="h-4 w-4 rounded border-[var(--bb-data-border-strong)] disabled:cursor-not-allowed disabled:opacity-50"
          aria-describedby={reasonId}
          aria-label={compareSelectionLabel({
            platform: entry.platform,
            benchmark: entry.benchmark,
            scaleFactor: entry.scale_factor,
            phase: entry.phase ?? null,
            runDate: entry.run_date,
            resultId: entry.result_id,
          })}
          data-testid={`platform-compare-checkbox-${entry.result_id}`}
        />
      </td>
      <td class="table-td" aria-colindex={platformTableColumnIndex("version", showMetricContract)}>
        <a
          href={resultReceiptHref(entry)}
          aria-label={resultIdentityAriaLabel(entry, "receipt")}
          title={`${runIdentityLabel} · ${splitVersion(entry.driver_version ?? entry.platform_version)?.full ?? "Version not recorded"}`}
          class="font-medium no-underline hover:underline"
          data-testid="run-identity-label"
        >
          {versionLabel}
        </a>
        {(isValidationNotClean(entry.validation_status) || entryOverrideRules.length > 0) && (
          <div class="mt-0.5" data-testid={`platform-validation-flag-${entry.result_id}`}>
            <ValidationBadge
              validationStatus={entry.validation_status}
              overrideRules={entryOverrideRules}
              showMissing
            />
          </div>
        )}
        <PlatformCompareReasonStatus id={reasonId} copy={disabledCopy} />
      </td>
      <td class="table-td font-medium" aria-colindex={platformTableColumnIndex("benchmark", showMetricContract)}>{humanizeBenchmark(entry.benchmark)}</td>
      <td class="table-td" aria-colindex={platformTableColumnIndex("scale", showMetricContract)}>SF {entry.scale_factor}</td>
      <td class="table-td text-[var(--bb-data-fg-muted)]" aria-colindex={platformTableColumnIndex("phase", showMetricContract)}>{formatPhaseWithStreams(entry.phase, entry.stream_count)}</td>
      {showMetricContract && (
        <td
          class="table-td whitespace-nowrap text-xs text-[var(--bb-data-fg-muted)]"
          aria-colindex={platformTableColumnIndex("metric_contract", true)}
          title={primaryMetricContract(entry.primary_metric)}
        >
          {primaryMetricShort(entry.primary_metric)}
        </td>
      )}
      <td class="table-td text-[var(--bb-data-fg-muted)]" aria-colindex={platformTableColumnIndex("date", showMetricContract)}><RunDateChip runDate={entry.run_date} /></td>
      <td
        class="table-td font-mono"
        aria-colindex={platformTableColumnIndex("power_score", showMetricContract)}
        title={canonicalPhase(entry.phase) === "throughput" ? "Throughput@Size" : undefined}
      >
        {fmtScore(phaseScoreValue(entry))}
      </td>
      <td class="table-td font-mono" aria-colindex={platformTableColumnIndex("geomean", showMetricContract)}>{fmtGeomean(entry.geomean_ms)}</td>
      <td class="table-td text-[var(--bb-data-fg-muted)]" aria-colindex={platformTableColumnIndex("queries", showMetricContract)}>{entry.query_count}</td>
      <td class="table-td" aria-colindex={platformTableColumnIndex("source", showMetricContract)}>
        <div class="flex flex-wrap gap-1">
          <TrustBadge trustLabel={entry.trust_label} compact />
          <FundingChip funding={entry.funding} compact />
          <ValidationBadge
            validationStatus={entry.validation_status}
            overrideRules={entryOverrideRules}
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
      <td class="table-td text-[var(--bb-data-fg-muted)]" aria-colindex={platformTableColumnIndex("arch", showMetricContract)}>
        {entry.arch ? formatArchitecture(entry.arch) : "—"}
      </td>
      <td class="table-td text-[var(--bb-data-fg-muted)]" aria-colindex={platformTableColumnIndex("cpu_family", showMetricContract)}>
        {entry.cpu_family ? formatCpuFamily(entry.cpu_family) : "—"}
      </td>
      <td class="table-td text-[var(--bb-data-fg-muted)]" aria-colindex={platformTableColumnIndex("memory_gb", showMetricContract)}>
        {entry.memory_gb != null ? formatMemoryGb(entry.memory_gb) : "—"}
      </td>
      <td class="table-td text-right" aria-colindex={platformTableColumnIndex("receipt", showMetricContract)}>
        <a
          href={resultReceiptHref(entry)}
          aria-label={resultIdentityAriaLabel(entry, "receipt")}
          class="text-xs font-medium no-underline"
        >
          Receipt →
        </a>
      </td>
    </tr>
  );
}

function PlatformCompareReasonStatus({
  id,
  copy,
}: {
  id?: string;
  copy: CompareExclusionReasonCopy | null;
}) {
  if (copy === null) return null;
  return (
    <div id={id} class="mt-1 text-xs text-[var(--bb-data-fg-muted)]" data-testid="platform-disabled-reason">
      <span class="font-medium text-[var(--bb-tone-warning-fg)]">Why unavailable: {copy.shortText}</span>
      <span class="block">{copy.recoveryHint}</span>
    </div>
  );
}
