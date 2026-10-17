import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { RoutableProps } from "preact-router";
import { DUCKDB_USER_QUERY_TIMEOUT_MS, queryRows } from "@/db";
import { CompareCta, isCompareAllAvailable } from "@/components/CompareCta";
import { EmptyState } from "@/components/EmptyState";
import { ErrorMessage } from "@/components/ErrorMessage";
import { FacetDrawer, FacetRail, type ActiveFacetChip, type FacetGroup } from "@/components/FacetRail";
import { TableScrollHint } from "@/components/TableScrollHint";
import { QueryRowsSkeleton } from "@/components/LoadingSpinner";
import { arraySerde, stringSerde, useUrlState } from "@/lib/useUrlState";
import {
  buildFacetCountQuery,
  buildFacetSql,
  buildSelectQuery,
  DEFAULT_ROW_LIMIT,
  UNLIMITED_ROW_LIMIT,
  type BuiltQuery,
  type QueryFilterState,
  type QuerySort,
} from "@/lib/queryFilters";
import { getTableSchema, type SchemaColumn } from "@/lib/duckdbSchema";
import { useFacetField } from "@/lib/facetModel";
import { toDateWindowFacet, toggleFacetValue } from "@/lib/facetMatching";
import {
  compareCohortLockReason,
  compareCohortPartition,
  compareCohortSignatureForRow,
  compareSelectionLabel,
  hiddenIncompatibleSuffix,
} from "@/lib/compareCohort";
import { buildCompareUrl, compareIdForRow, MAX_COMPARE_SELECTIONS, visibleResultIdForRow } from "@/lib/resultLinks";
import { STARTER_QUERY_CATEGORIES, starterQueriesByCategory, type StarterQueryCategory } from "@/lib/starterQueries";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { PageHeader } from "@/components/PageHeader";
import {
  buildQueryResultExportQuery,
  buildQueryResultPageQueries,
  getDetailResult,
  memoizedSnapshotQueryRows,
  QUERY_RESULT_PAGE_SIZE,
  resolveShortId,
} from "@/lib/duckdbQueries";
import { formatQueryCell, formatQueryColumnLabel, formatQueryFacetValue } from "@/lib/queryLabels";
import { RunDateChip } from "@/components/RunAge";
import { formatPlainNumber, MISSING_VALUE_GLYPH } from "@/lib/metricFormatters";
import {
  formatBenchmarkLabel,
  formatCostStatus,
  formatFunding,
  formatTrustLabel,
  formatValidationStatus,
  formatVisibility,
} from "@/lib/displayLabels";
import {
  EXPLORER_PERFORMANCE_MARKS,
  EXPLORER_PERFORMANCE_MEASURES,
  markExplorerPerformance,
  measureExplorerPerformance,
} from "@/lib/performanceMarks";
import {
  describeCompareExclusionReason,
  summarizeCompareExclusionReasons,
  type CompareExclusionReasonCopy,
} from "@/lib/compareExclusionReasons";
import { formatSelectedCount } from "@/lib/copyFormatters";
import { exportColumnsWithCompliance, withComplianceCaption } from "@/lib/subscale";

const EMPTY_STRING_ARRAY: string[] = [];

interface FacetBucket {
  value: string;
  count: number;
  [key: string]: unknown;
}

type ResultRow = Record<string, unknown>;

const DEFAULT_COLUMNS = [
  "benchmark",
  "platform",
  "scale_factor",
  "run_date",
  "power_score",
  "geomean_ms",
  "trust_label",
];
const SQL_TABLE_RENDER_LIMIT = 200;
const SQL_TABLE_RENDER_INCREMENT = 200;
const COMPARE_METADATA_COLUMNS = [
  "result_id",
  "benchmark",
  "scale_factor",
  "test_type",
  "phase",
  "stream_count",
  "primary_metric",
  "comparison_exclusion_reason",
];

interface QueryProps extends RoutableProps {
  url?: string;
}

export function Query({ url }: QueryProps) {
  useDocumentTitle("Find runs · BenchBox Results");
  const pinnedCompareId = useMemo(() => {
    const activeUrl = url ?? (typeof window === "undefined" ? "/results/query" : `${window.location.pathname}${window.location.search}`);
    return new URL(activeUrl, "https://benchbox.dev").searchParams.get("pick");
  }, [url]);
  const preselectedCompareIds = useMemo(() => {
    const activeUrl = url ?? (typeof window === "undefined" ? "/results/query" : `${window.location.pathname}${window.location.search}`);
    const raw = new URL(activeUrl, "https://benchbox.dev").searchParams.get("ids") ?? "";
    return [...new Set(raw.split(",").map((id) => id.trim()).filter((id) => id !== ""))].slice(0, MAX_COMPARE_SELECTIONS);
  }, [url]);
  const preselectedCompareKey = preselectedCompareIds.join(",");
  const resultsScrollerRef = useRef<HTMLDivElement>(null);
  const sqlScrollerRef = useRef<HTMLDivElement>(null);
  const [benchmarks, setBenchmarks] = useFacetField("benchmark");
  const [platforms, setPlatforms] = useFacetField("platform");
  const [scaleFactors, setScaleFactors] = useFacetField("scale_factor");
  const [tuningModes, setTuningModes] = useFacetField("tuning_mode");
  const [trustTiers, setTrustTiers] = useFacetField("trust_tier");
  const [validationStatuses, setValidationStatuses] = useFacetField("validation_status");
  const [costStatuses, setCostStatuses] = useFacetField("cost_status");
  const [costModelVersions, setCostModelVersions] = useUrlState<string[]>(
    "cost_model",
    EMPTY_STRING_ARRAY,
    arraySerde,
  );
  const [deploymentClasses, setDeploymentClasses] = useFacetField("deployment_class");
  const [cloudProviders, setCloudProviders] = useFacetField("cloud_provider");
  const [cloudRegions, setCloudRegions] = useFacetField("cloud_region");
  const [instanceOrWarehouses, setInstanceOrWarehouses] = useFacetField("instance_or_warehouse");
  const [storageFormats, setStorageFormats] = useFacetField("storage_format");
  const [physicalRenderingIds, setPhysicalRenderingIds] = useUrlState<string[]>(
    "physical_rendering_id",
    EMPTY_STRING_ARRAY,
    arraySerde,
  );
  const [rowLimitRaw, setRowLimitRaw] = useUrlState<string>("limit", "default", stringSerde);
  const [searchText, setSearchText] = useUrlState<string>("q", "", stringSerde);
  const [pageRaw, setPageRaw] = useUrlState<string>("page", "1", stringSerde);
  const [hasCost, setHasCost] = useUrlState<string>("has_cost", "all", stringSerde);
  const [dateWindow, setDateWindowFacet] = useFacetField("date_window");
  const setDateWindow = (value: string) => setDateWindowFacet(toDateWindowFacet(value));
  const [schema, setSchema] = useState<SchemaColumn[]>([]);
  const [visibleColumns, setVisibleColumns] = useState<string[]>([]);
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [resultTotal, setResultTotal] = useState(0);
  const [facetCounts, setFacetCounts] = useState<Record<string, FacetBucket[]>>({});
  const [sort, setSort] = useState<QuerySort>({ column: "run_date", direction: "desc" });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resultsRetryToken, setResultsRetryToken] = useState(0);
  const [sqlText, setSqlText] = useState("SELECT * FROM bench.results ORDER BY run_date DESC");
  const [sqlRows, setSqlRows] = useState<ResultRow[]>([]);
  const [sqlError, setSqlError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const [visibleSqlLimit, setVisibleSqlLimit] = useState(SQL_TABLE_RENDER_LIMIT);
  const [compareSelectedRows, setCompareSelectedRows] = useState<Map<string, ResultRow>>(new Map());
  const [compareHandoffError, setCompareHandoffError] = useState<string | null>(null);
  const [pinnedCompareResultId, setPinnedCompareResultId] = useState<string | null>(null);
  const compareSelectedIds = useMemo(() => new Set(compareSelectedRows.keys()), [compareSelectedRows]);
  const clearPinnedCompareParam = () => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (!params.has("pick")) return;
    params.delete("pick");
    const search = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`);
    setPinnedCompareResultId(null);
  };
  const toggleCompareSelection = (row: ResultRow) => {
    const resultId = String(row.result_id);
    if (compareSelectedRows.has(resultId) && resultId === pinnedCompareResultId) clearPinnedCompareParam();
    setCompareSelectedRows((prev) => {
      const next = new Map(prev);
      if (next.has(resultId)) next.delete(resultId);
      else if (next.size < MAX_COMPARE_SELECTIONS && comparisonExclusionReason(row) === undefined) next.set(resultId, row);
      return next;
    });
  };
  const removeCompareSelection = (resultId: string) => {
    if (resultId === pinnedCompareResultId) clearPinnedCompareParam();
    setCompareSelectedRows((prev) => {
      const next = new Map(prev);
      next.delete(resultId);
      return next;
    });
  };
  const clearCompareSelection = () => {
    clearPinnedCompareParam();
    setCompareSelectedRows(new Map());
  };
  const [compareCompatibleOnly, setCompareCompatibleOnly] = useState(true);

  useEffect(() => {
    if (!pinnedCompareId) return;
    let cancelled = false;
    setCompareHandoffError(null);
    resolveShortId(pinnedCompareId)
      .then((resolvedId) => getDetailResult(resolvedId))
      .then((detail) => {
        if (cancelled) return;
        if (detail === null) {
          setCompareHandoffError(`The selected run “${pinnedCompareId}” is no longer published.`);
          return;
        }
        const exclusionReason = comparisonExclusionReason(detail as unknown as ResultRow);
        if (exclusionReason !== undefined) {
          const copy = describeCompareExclusionReason(exclusionReason);
          setCompareHandoffError(copy?.detailText ?? "The selected run is not eligible for comparison.");
          return;
        }
        setPinnedCompareResultId(detail.result_id);
        setCompareSelectedRows((prev) => {
          const next = new Map<string, ResultRow>([[detail.result_id, detail as unknown as ResultRow]]);
          for (const [id, row] of prev) {
            if (id !== detail.result_id) next.set(id, row);
          }
          return next;
        });
      })
      .catch(() => {
        if (!cancelled) {
          setCompareHandoffError("The selected run could not be loaded. Try finding it again.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [pinnedCompareId]);

  useEffect(() => {
    if (preselectedCompareIds.length === 0) return;
    let cancelled = false;
    Promise.all(
      preselectedCompareIds.map((id) =>
        resolveShortId(id)
          .then((resolvedId) => getDetailResult(resolvedId))
          .catch(() => null),
      ),
    ).then((details) => {
      if (cancelled) return;
      const eligible = details.filter(
        (detail) => detail !== null && comparisonExclusionReason(detail as unknown as ResultRow) === undefined,
      );
      if (eligible.length === 0) return;
      setCompareSelectedRows((prev) => {
        const next = new Map(prev);
        for (const detail of eligible) {
          if (next.size >= MAX_COMPARE_SELECTIONS) break;
          next.set(detail!.result_id, detail as unknown as ResultRow);
        }
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [preselectedCompareKey]);

  const rowLimitMode = rowLimitRaw === "all" ? "all" : "default";
  const rowLimit = rowLimitMode === "all" ? UNLIMITED_ROW_LIMIT : DEFAULT_ROW_LIMIT;
  const parsedPage = Number.parseInt(pageRaw, 10);
  const currentPage = Number.isFinite(parsedPage) && parsedPage > 0 ? parsedPage : 1;
  const pageOffset = (currentPage - 1) * QUERY_RESULT_PAGE_SIZE;

  const filters: QueryFilterState = useMemo(
    () => ({
      benchmarks,
      platforms,
      scaleFactors,
      tuningModes,
      trustTiers,
      validationStatuses,
      costStatuses,
      costModelVersions,
      deploymentClasses,
      cloudProviders,
      cloudRegions,
      instanceOrWarehouses,
      instanceTypes: EMPTY_STRING_ARRAY,
      warehouseSizes: EMPTY_STRING_ARRAY,
      storageFormats,
      physicalRenderingIds,
      hasCost: hasCost === "yes" || hasCost === "no" ? hasCost : "all",
      dateWindow,
    }),
    [
      benchmarks,
      cloudProviders,
      cloudRegions,
      costModelVersions,
      costStatuses,
      dateWindow,
      deploymentClasses,
      hasCost,
      instanceOrWarehouses,
      physicalRenderingIds,
      platforms,
      scaleFactors,
      storageFormats,
      trustTiers,
      tuningModes,
      validationStatuses,
    ],
  );
  const compareMetadataColumns = useMemo(
    () => COMPARE_METADATA_COLUMNS.filter((column) => schema.some((item) => item.name === column)),
    [schema],
  );
  const queryColumns = useMemo(() => {
    const searchColumns = ["result_id", "platform", "platform_version"].filter((column) =>
      schema.some((item) => item.name === column),
    );
    return [...new Set([...compareMetadataColumns, ...searchColumns, ...visibleColumns])];
  }, [compareMetadataColumns, schema, visibleColumns]);
  const activeFilters = useMemo(() => applySchemaFilterSupport(filters, schema), [filters, schema]);
  const facetQueries = useMemo(
    () => (schema.length === 0 ? null : buildQueryFacetCountQueries(activeFilters, schema)),
    [activeFilters, schema],
  );
  const baseSelectQuery = useMemo(
    () =>
      visibleColumns.length === 0
        ? null
        : buildSelectQuery(activeFilters, queryColumns, sort, rowLimit),
    [activeFilters, queryColumns, rowLimit, sort, visibleColumns.length],
  );
  const pageQueries = useMemo(
    () =>
      baseSelectQuery === null
        ? null
        : buildQueryResultPageQueries(baseSelectQuery, searchText, pageOffset),
    [baseSelectQuery, pageOffset, searchText],
  );
  const compareCohortSignature = useMemo(() => {
    const firstId = [...compareSelectedIds][0];
    if (firstId === undefined) return null;
    const firstRow = compareSelectedRows.get(firstId);
    return firstRow ? compareCohortSignatureForRow(firstRow) : null;
  }, [compareSelectedIds, compareSelectedRows]);
  const compareRowPartition = useMemo(
    () => compareCohortPartition(rows, compareCohortSignature),
    [rows, compareCohortSignature],
  );
  const compareIncompatibleHiddenCount =
    compareCohortSignature !== null && compareCompatibleOnly ? compareRowPartition.incompatible.length : 0;
  const reorderedRows = useMemo(() => {
    if (compareCohortSignature === null) return rows;
    if (compareCompatibleOnly) return compareRowPartition.compatible;
    return [...compareRowPartition.compatible, ...compareRowPartition.incompatible];
  }, [compareCohortSignature, compareCompatibleOnly, compareRowPartition, rows]);
  const visibleRows = reorderedRows;
  const visibleSqlRows = sqlRows.slice(0, visibleSqlLimit);

  useEffect(() => {
    setVisibleSqlLimit(SQL_TABLE_RENDER_LIMIT);
  }, [sqlRows]);

  useEffect(() => {
    if (rowLimitRaw === "default" || rowLimitRaw === "all") return;
    setRowLimitRaw("default");
  }, [rowLimitRaw, setRowLimitRaw]);

  useEffect(() => {
    if (String(currentPage) === pageRaw) return;
    setPageRaw(String(currentPage));
  }, [currentPage, pageRaw]);

  useEffect(() => {
    if (currentPage === 1) return;
    setPageRaw("1");
  }, [activeFilters, queryColumns, rowLimit, searchText, sort]);

  useEffect(() => {
    let cancelled = false;
    getTableSchema("results")
      .then((columns) => {
        if (cancelled) return;
        setSchema(columns);
        setVisibleColumns((current) =>
          current.length > 0
            ? current
            : DEFAULT_COLUMNS.filter((column) => columns.some((item) => item.name === column)),
        );
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load schema");
      });
    return () => {
      cancelled = true;
    };
  }, [resultsRetryToken]);

  useEffect(() => {
    if (facetQueries === null) return;
    let cancelled = false;
    setError(null);

    Promise.all(
      Object.entries(facetQueries).map(async ([key, query]) => [
        key,
        await memoizedSnapshotQueryRows<FacetBucket>(`query-facet:${key}`, query),
      ]),
    )
      .then((facetEntries) => {
        if (cancelled) return;
        setFacetCounts(Object.fromEntries(facetEntries));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "DuckDB query failed");
      });
    return () => {
      cancelled = true;
    };
  }, [facetQueries, resultsRetryToken]);

  useEffect(() => {
    if (pageQueries === null) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      queryRows<ResultRow>(pageQueries.rows.sql, pageQueries.rows.params),
      queryRows<{ count: number }>(pageQueries.count.sql, pageQueries.count.params),
    ])
      .then(([nextRows, countRows]) => {
        if (cancelled) return;
        const count = Number(countRows[0]?.count);
        const nextTotal = Number.isFinite(count) ? count : nextRows.length;
        const lastPage = Math.max(1, Math.ceil(nextTotal / QUERY_RESULT_PAGE_SIZE));
        if (currentPage > lastPage) {
          setPageRaw(String(lastPage));
          return;
        }
        setRows(nextRows);
        setResultTotal(nextTotal);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "DuckDB query failed");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [currentPage, pageQueries, setPageRaw, resultsRetryToken]);

  useEffect(() => {
    if (loading || rows.length === 0 || visibleColumns.length === 0) return;
    markExplorerPerformance(EXPLORER_PERFORMANCE_MARKS.QUERY_WORKBENCH_RENDERED, {
      once: true,
      detail: { rowCount: rows.length, columnCount: visibleColumns.length },
    });
    measureExplorerPerformance(
      EXPLORER_PERFORMANCE_MEASURES.QUERY_WORKBENCH_RENDER_AFTER_DB,
      EXPLORER_PERFORMANCE_MARKS.DB_INIT_READY,
      EXPLORER_PERFORMANCE_MARKS.QUERY_WORKBENCH_RENDERED,
      { once: true },
    );
  }, [loading, rows.length, visibleColumns.length]);

  const sqlColumns = useMemo(() => [...new Set(sqlRows.flatMap((row) => Object.keys(row)))], [sqlRows]);
  const pageCount = Math.max(1, Math.ceil(resultTotal / QUERY_RESULT_PAGE_SIZE));
  const pageStart = resultTotal === 0 ? 0 : pageOffset + 1;
  const pageEnd = Math.min(pageOffset + rows.length, resultTotal);

  if (error) {
    return (
      <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <ErrorMessage title="Could not load results" message={error} onRetry={() => setResultsRetryToken((t) => t + 1)} />
        <div class="mt-4 flex flex-wrap gap-2">
          <a href="/results/query" class="btn btn-primary no-underline">Try again</a>
          <a href="/results/" class="btn btn-secondary no-underline">Browse leaderboards</a>
        </div>
      </div>
    );
  }
  if (schema.length === 0 && loading) {
    return (
      <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <QueryRowsSkeleton message="Loading published runs..." />
      </div>
    );
  }

  const columnNames = schema.map((column) => column.name);
  const facetGroups: FacetGroup[] = [
    makeFacetGroup("benchmark", "Benchmark", facetCounts.benchmark ?? [], benchmarks, {
      formatLabel: formatBenchmarkLabel,
      searchable: true,
    }),
    makeFacetGroup("platform", "Platform", facetCounts.platform ?? [], platforms, {
      searchable: true,
    }),
    makeFacetGroup("scale_factor", "Scale", facetCounts.scale_factor ?? [], scaleFactors),
    makeFacetGroup("tuning_mode", "Tuning", facetCounts.tuning_mode ?? [], tuningModes, {
      defaultCollapsed: true,
    }),
    makeFacetGroup("trust_tier", "Trust", facetCounts.trust_label ?? [], trustTiers, {
      formatLabel: formatTrustLabel,
      defaultCollapsed: true,
    }),
    makeFacetGroup("validation_status", "Validation", facetCounts.validation_status ?? [], validationStatuses, {
      formatLabel: formatValidationStatus,
      defaultCollapsed: true,
    }),
    makeFacetGroup("cost_status", "Cost status", facetCounts.cost_status ?? [], costStatuses, {
      formatLabel: formatCostStatus,
      defaultCollapsed: true,
    }),
    makeFacetGroup("cost_model", "Cost model", facetCounts.cost_model_version ?? [], costModelVersions, {
      defaultCollapsed: true,
    }),
    makeFacetGroup("deployment_class", "Deployment", facetCounts.deployment_class ?? [], deploymentClasses, {
      defaultCollapsed: true,
    }),
    makeFacetGroup("cloud_provider", "Cloud provider", facetCounts.cloud_provider ?? [], cloudProviders, {
      defaultCollapsed: true,
    }),
    makeFacetGroup("cloud_region", "Cloud region", facetCounts.cloud_region ?? [], cloudRegions, {
      searchable: true,
      defaultCollapsed: true,
    }),
    makeFacetGroup(
      "instance_or_warehouse",
      "Instance / warehouse",
      facetCounts.instance_or_warehouse ?? [],
      instanceOrWarehouses,
      { searchable: true, defaultCollapsed: true },
    ),
    makeFacetGroup("storage_format", "Storage", facetCounts.storage_format ?? [], storageFormats, {
      defaultCollapsed: true,
    }),
    makeFacetGroup(
      "physical_rendering_id",
      "Physical rendering",
      facetCounts.physical_rendering_id ?? [],
      physicalRenderingIds,
      { defaultCollapsed: true },
    ),
    makeFacetGroup(
      "has_cost",
      "Has cost",
      [{ value: "all", count: resultTotal }, ...(facetCounts.has_cost ?? [])],
      [hasCost],
      { formatLabel: formatHasCostLabel, defaultCollapsed: true },
    ),
    makeFacetGroup(
      "date_window",
      "Date window",
      [{ value: "all", count: resultTotal }, ...(facetCounts.date_window ?? [])],
      [dateWindow],
      { formatLabel: formatDateWindowLabel, defaultCollapsed: true },
    ),
  ].filter((group) => group.options.length > 0);
  const activeFilterChips: ActiveFacetChip[] = [
    ...makeActiveChips("benchmark", "Benchmark", benchmarks, setBenchmarks, formatBenchmarkLabel),
    ...makeActiveChips("platform", "Platform", platforms, setPlatforms),
    ...makeActiveChips("scale_factor", "Scale", scaleFactors, setScaleFactors),
    ...makeActiveChips("tuning_mode", "Tuning", tuningModes, setTuningModes),
    ...makeActiveChips("trust_tier", "Trust", trustTiers, setTrustTiers, formatTrustLabel),
    ...makeActiveChips(
      "validation_status",
      "Validation",
      validationStatuses,
      setValidationStatuses,
      formatValidationStatus,
    ),
    ...makeActiveChips("cost_status", "Cost status", costStatuses, setCostStatuses, formatCostStatus),
    ...makeActiveChips("cost_model", "Cost model", costModelVersions, setCostModelVersions),
    ...makeActiveChips("deployment_class", "Deployment", deploymentClasses, setDeploymentClasses),
    ...makeActiveChips("cloud_provider", "Cloud provider", cloudProviders, setCloudProviders),
    ...makeActiveChips("cloud_region", "Cloud region", cloudRegions, setCloudRegions),
    ...makeActiveChips("instance_or_warehouse", "Instance / warehouse", instanceOrWarehouses, setInstanceOrWarehouses),
    ...makeActiveChips("storage_format", "Storage", storageFormats, setStorageFormats),
    ...makeActiveChips(
      "physical_rendering_id",
      "Physical rendering",
      physicalRenderingIds,
      setPhysicalRenderingIds,
    ),
    ...(hasCost === "all"
      ? []
      : [{
          key: "has_cost",
          label: `Has cost: ${formatHasCostLabel(hasCost)}`,
          onClear: () => setHasCost("all"),
        }]),
    ...(dateWindow === "all"
      ? []
      : [{
          key: "date_window",
          label: `Date: ${formatDateWindowLabel(dateWindow)}`,
          onClear: () => setDateWindow("all"),
        }]),
  ];

  function toggleMulti(value: string, selected: string[], setSelected: (next: string[]) => void) {
    setSelected(toggleFacetValue(selected, value));
  }

  function toggleColumn(column: string) {
    setVisibleColumns((current) => {
      if (current.includes(column)) {
        return current.filter((candidate) => candidate !== column);
      }
      return [...current, column];
    });
  }

  function resetVisibleColumnsToDefault() {
    setVisibleColumns(DEFAULT_COLUMNS.filter((column) => columnNames.includes(column)));
  }
  function selectAllVisibleColumns() {
    setVisibleColumns([...columnNames]);
  }
  function clearOptionalVisibleColumns() {
    if (columnNames.length === 0) return;
    const required = columnNames.includes("result_id") ? ["result_id"] : columnNames.slice(0, 1);
    setVisibleColumns(required);
  }

  function toggleSort(column: string) {
    setSort((current) =>
      current.column === column
        ? { column, direction: current.direction === "asc" ? "desc" : "asc" }
        : { column, direction: "asc" },
    );
  }

  function toggleQueryFacet(groupKey: string, value: string) {
    switch (groupKey) {
      case "benchmark":
        toggleMulti(value, benchmarks, setBenchmarks);
        break;
      case "platform":
        toggleMulti(value, platforms, setPlatforms);
        break;
      case "scale_factor":
        toggleMulti(value, scaleFactors, setScaleFactors);
        break;
      case "tuning_mode":
        toggleMulti(value, tuningModes, setTuningModes);
        break;
      case "trust_tier":
        toggleMulti(value, trustTiers, setTrustTiers);
        break;
      case "validation_status":
        toggleMulti(value, validationStatuses, setValidationStatuses);
        break;
      case "cost_status":
        toggleMulti(value, costStatuses, setCostStatuses);
        break;
      case "cost_model":
        toggleMulti(value, costModelVersions, setCostModelVersions);
        break;
      case "deployment_class":
        toggleMulti(value, deploymentClasses, setDeploymentClasses);
        break;
      case "cloud_provider":
        toggleMulti(value, cloudProviders, setCloudProviders);
        break;
      case "cloud_region":
        toggleMulti(value, cloudRegions, setCloudRegions);
        break;
      case "instance_or_warehouse":
        toggleMulti(value, instanceOrWarehouses, setInstanceOrWarehouses);
        break;
      case "storage_format":
        toggleMulti(value, storageFormats, setStorageFormats);
        break;
      case "physical_rendering_id":
        toggleMulti(value, physicalRenderingIds, setPhysicalRenderingIds);
        break;
      case "has_cost":
        setHasCost(value);
        break;
      case "date_window":
        setDateWindow(value);
        break;
    }
  }

  function resetQueryFilters() {
    setBenchmarks([]);
    setPlatforms([]);
    setScaleFactors([]);
    setTuningModes([]);
    setTrustTiers([]);
    setValidationStatuses([]);
    setCostStatuses([]);
    setCostModelVersions([]);
    setDeploymentClasses([]);
    setCloudProviders([]);
    setCloudRegions([]);
    setInstanceOrWarehouses([]);
    setStorageFormats([]);
    setPhysicalRenderingIds([]);
    setHasCost("all");
    setDateWindow("all");
    setSearchText("");
  }

  function buildSqlFromFilters() {
    setSqlText(buildFacetSql(activeFilters, visibleColumns, sort, rowLimit));
  }

  async function downloadJson() {
    setDownloadError(null);
    try {
      const exportColumns = exportColumnsWithCompliance(visibleColumns);
      const query = buildQueryResultExportQuery(
        buildSelectQuery(activeFilters, exportColumns, sort, rowLimit),
        searchText,
      );
      const exportRows = await queryRows<ResultRow>(query.sql, query.params, DUCKDB_USER_QUERY_TIMEOUT_MS);
      const exportName = `benchbox-query-export-${Date.now()}.json`;
      const blob = new Blob(
        [
          JSON.stringify(
            exportRows.map((row) => withComplianceCaption(projectVisibleRow(row, exportColumns))),
            null,
            2,
          ),
        ],
        { type: "application/json;charset=utf-8" },
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = exportName;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setDownloadError(err instanceof Error ? err.message : "JSON export failed");
    }
  }

  function loadStarterQuery(sql: string) {
    setSqlText(sql);
    setSqlError(null);
    setSqlRows([]);
  }

  async function downloadCsv() {
    const exportName = `benchbox-query-export-${Date.now()}.csv`;
    const exportColumns = exportColumnsWithCompliance(visibleColumns);
    const selectQuery = buildQueryResultExportQuery(
      buildSelectQuery(activeFilters, exportColumns, sort, rowLimit),
      searchText,
    );

    setDownloadError(null);
    try {
      const exportRows = await queryRows<ResultRow>(selectQuery.sql, selectQuery.params, DUCKDB_USER_QUERY_TIMEOUT_MS);
      const blob = new Blob(
        [serializeCsv(exportRows.map(withComplianceCaption), [...exportColumns, "compliance_note"])],
        { type: "text/csv;charset=utf-8" },
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = exportName;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setDownloadError(err instanceof Error ? err.message : "CSV export failed");
    }
  }

  async function runSql() {
    setSqlError(null);
    try {
      const nextRows = await queryRows<ResultRow>(sqlText, [], DUCKDB_USER_QUERY_TIMEOUT_MS);
      setSqlRows(nextRows);
    } catch (err: unknown) {
      setSqlError(err instanceof Error ? err.message : "SQL query failed");
      setSqlRows([]);
    }
  }

  function comparisonExclusionReason(row: ResultRow): string | undefined {
    const reason = row.comparison_exclusion_reason;
    if (typeof reason !== "string" || reason.length === 0) return undefined;
    return reason;
  }

  const queryNarrowingLabels = [
    ...activeFilterChips.map((chip) => chip.label),
    ...(searchText === "" ? [] : [`Search: ${searchText}`]),
  ];

  return (
    <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        crumbs={[{ label: "Results", href: "/results/" }, { label: "Find runs" }]}
        eyebrow="Find runs"
        title="Find benchmark runs"
        subtitle="Filter published runs, open their evidence, or select up to ten runs to compare. Your filters stay in the page link."
      />

      <div class="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <section class="flex min-w-0 flex-col gap-4 lg:col-start-2">
          <div
            data-testid="query-result-summary"
            class="order-1 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] p-4 shadow-sm"
          >
            <div class="text-sm text-[var(--bb-data-fg-muted)]">
              Showing {pageStart.toLocaleString()}–{pageEnd.toLocaleString()} of {resultTotal.toLocaleString()} matching {resultTotal === 1 ? "run" : "runs"}
              {rowLimitMode === "default" && resultTotal >= DEFAULT_ROW_LIMIT && (
                <span class="ml-2 text-xs text-[var(--bb-tone-warning-fg)]">
                  The query reached the {DEFAULT_ROW_LIMIT.toLocaleString()}-result cap; add filters to narrow the set.
                </span>
              )}
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <div class="flex items-center gap-2 text-sm text-[var(--bb-data-fg-muted)]">
                <span class="font-medium">Results to load:</span>
                <div class="flex overflow-hidden rounded-md border border-[var(--bb-data-border-strong)]" role="group" aria-label="Result row limit">
                  {(["default", "all"] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      class={`px-3 py-1.5 text-sm ${
                        rowLimitMode === mode
                          ? "bg-[var(--bb-tone-info-bg)] text-[var(--bb-tone-info-fg)]"
                          : "bg-[var(--bb-surface-data)] text-[var(--bb-data-fg-muted)] hover:bg-[var(--bb-surface-data-muted)]"
                      }`}
                      aria-pressed={rowLimitMode === mode}
                      onClick={() => setRowLimitRaw(mode)}
                    >
                      {mode === "default" ? `Up to ${DEFAULT_ROW_LIMIT.toLocaleString()}` : "All"}
                    </button>
                  ))}
                </div>
              </div>
              <button class="btn btn-secondary" onClick={downloadCsv}>
                Download CSV (filtered rows)
              </button>
              <button class="btn btn-secondary" onClick={downloadJson}>
                Download JSON (filtered rows)
              </button>
            </div>
            <p class="w-full text-xs text-[var(--bb-data-fg-muted)]">
              Downloads include every matching run within the selected limit and use the columns shown below. Moving between pages does not change the download.
            </p>
            <label class="w-full text-sm text-[var(--bb-data-fg-muted)]">
              <span class="font-medium">Search results</span>
              <input
                type="search"
                value={searchText}
                onInput={(event) => setSearchText((event.target as HTMLInputElement).value)}
                placeholder="Platform, version, or public ID"
                class="mt-1 w-full rounded-md border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-3 py-2 text-[var(--bb-data-fg-primary)]"
                data-testid="query-result-search"
              />
            </label>
            {downloadError && <div class="w-full text-sm text-[var(--bb-tone-danger-fg)]">{downloadError}</div>}
          </div>

          <div data-testid="query-mobile-filter-drawer" class="order-2 lg:hidden">
            <FacetDrawer
              groups={facetGroups}
              resultCount={resultTotal}
              activeChips={activeFilterChips}
              onToggle={toggleQueryFacet}
              onReset={resetQueryFilters}
              open={mobileFiltersOpen}
              onOpenChange={setMobileFiltersOpen}
            />
          </div>

          <div data-testid="query-results-panel" class="order-3">
            {loading ? (
              <QueryRowsSkeleton
                message="Loading matching results..."
                columns={visibleColumns.length || DEFAULT_COLUMNS.length}
              />
            ) : resultTotal === 0 ? (
              <div data-testid="query-empty-state">
                <EmptyState
                  title={
                    queryNarrowingLabels.length > 0
                      ? "No results match these filters or search"
                      : "No published results"
                  }
                  description={
                    queryNarrowingLabels.length > 0
                      ? `Narrowed by ${queryNarrowingLabels.join(", ")}. Clear the filters or search text to widen the query.`
                      : "There are no published runs to show."
                  }
                  action={
                    queryNarrowingLabels.length > 0 ? (
                      <button type="button" class="btn btn-secondary" onClick={resetQueryFilters}>
                        Clear all filters
                      </button>
                    ) : undefined
                  }
                />
              </div>
            ) : (
              <div class="overflow-hidden rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] shadow-sm">
                <div class="flex flex-wrap items-center justify-end gap-2 border-b border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] px-4 py-3 text-sm text-[var(--bb-data-fg-muted)]">
                  <TableScrollHint
                    scrollerRef={resultsScrollerRef}
                    testId="query-results-scroll-hint"
                    wrapperClassName={null}
                  />
                </div>
                {(() => {
                  const lockReason = (row: ResultRow): string | undefined => {
                    const id = String(row.result_id);
                    if (compareSelectedIds.has(id)) return undefined;
                    return compareCohortLockReason(row, compareCohortSignature);
                  };
                  const zeroSelectable =
                    compareSelectedIds.size === 0 &&
                    visibleRows.length > 0 &&
                    visibleRows.every((row) => comparisonExclusionReason(row) !== undefined);
                  const zeroSelectableReasons = summarizeCompareExclusionReasons(
                    visibleRows.map((row) => comparisonExclusionReason(row)),
                  );
                  const selectedCompareIds = [...compareSelectedRows.values()].map((row) =>
                    compareIdForRow(row as Parameters<typeof compareIdForRow>[0]),
                  );
                  const selectedStatus = formatSelectedCount(
                    compareSelectedIds.size,
                    "result",
                    MAX_COMPARE_SELECTIONS,
                  );
                  const wholeFilteredSetVisible = rows.length === resultTotal;
                  const visibleComparableRows = wholeFilteredSetVisible
                    ? rows.filter((row) => comparisonExclusionReason(row) === undefined)
                    : [];
                  const visibleCohortKeys = new Set(
                    visibleComparableRows.map((row) => JSON.stringify(compareCohortSignatureForRow(row))),
                  );
                  const lockedComparableRows = compareCohortSignature !== null && wholeFilteredSetVisible
                    ? compareRowPartition.compatible.filter((row) => comparisonExclusionReason(row) === undefined)
                    : [];
                  const allRunsCandidates = visibleCohortKeys.size === 1
                    ? visibleComparableRows
                    : lockedComparableRows;
                  const allRunsCompareIds = allRunsCandidates.map((row) =>
                    compareIdForRow(row as Parameters<typeof compareIdForRow>[0]),
                  );
                  return (
                    <>
                      <div
                        class="border-b border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] text-sm text-[var(--bb-data-fg-muted)]"
                        data-testid="query-compare-tray"
                      >
                        <div class="flex flex-wrap items-center justify-between gap-2 px-4 py-2">
                          <span>
                            {compareSelectedIds.size === 0
                              ? isCompareAllAvailable(allRunsCompareIds)
                                ? `All ${allRunsCompareIds.length} comparable runs in these results can be compared together. Select rows to compare a subset.`
                                : `${selectedStatus}. Select two or more rows to compare. The first pick locks the ranking.`
                              : compareSelectedIds.size === 1
                                ? `${selectedStatus} — pick a compatible second row to enable Compare.${hiddenIncompatibleSuffix(compareIncompatibleHiddenCount)}`
                                : `${selectedStatus}.`}
                          </span>
                          <span class="flex items-center gap-2">
                            {compareCohortSignature !== null && (
                              <label
                                class="flex items-center gap-1 text-xs"
                                data-testid="query-compare-compatible-only-label"
                              >
                                <input
                                  type="checkbox"
                                  checked={compareCompatibleOnly}
                                  onChange={(e) =>
                                    setCompareCompatibleOnly((e.target as HTMLInputElement).checked)
                                  }
                                  class="h-4 w-4 rounded border-[var(--bb-data-border-strong)]"
                                  data-testid="query-compare-compatible-only"
                                />
                                Compatible only
                              </label>
                            )}
                            {compareSelectedIds.size > 0 && (
                              <button
                                type="button"
                                class="rounded-md border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-3 py-1 text-xs font-medium hover:bg-[var(--bb-surface-data-muted)]"
                                onClick={clearCompareSelection}
                                data-testid="query-compare-clear"
                              >
                                Clear selection
                              </button>
                            )}
                            <CompareCta
                              selectedIds={selectedCompareIds}
                              allIds={allRunsCompareIds}
                              buildHref={buildCompareUrl}
                              testId="query-compare-launch"
                            />
                          </span>
                        </div>
                        {compareHandoffError && (
                          <p class="border-t border-[var(--bb-tone-warning-border)] px-4 py-2 text-[var(--bb-tone-warning-fg)]" role="alert">
                            {compareHandoffError}
                          </p>
                        )}
                        {compareSelectedRows.size > 0 && (
                          <ul
                            class="flex flex-wrap gap-2 border-t border-[var(--bb-data-border)] px-4 py-2"
                            aria-label="Selected query compare results"
                          >
                            {[...compareSelectedRows.values()].map((row) => {
                              const resultId = String(row.result_id);
                              const platform = String(row.platform ?? "Unknown platform");
                              const publicId = visibleResultIdForRow({ result_id: resultId });
                              return (
                                <li
                                  key={resultId}
                                  class="flex max-w-full items-center gap-2 rounded-md border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] px-2 py-1 text-xs"
                                  data-testid={`query-compare-selected-${resultId}`}
                                >
                                  <span class="font-medium text-[var(--bb-data-fg-primary)]">{platform}</span>
                                  <span class="font-mono">Public ID {publicId}</span>
                                  <button
                                    type="button"
                                    class="rounded px-1 hover:bg-[var(--bb-surface-data-muted)] hover:text-[var(--bb-data-fg-primary)]"
                                    aria-label={`Remove ${platform}, Public ID ${publicId}, from comparison`}
                                    onClick={() => removeCompareSelection(resultId)}
                                  >
                                    Remove
                                  </button>
                                </li>
                              );
                            })}
                          </ul>
                        )}
                      </div>
                      {zeroSelectable && (
                        <section
                          class="border-b border-[var(--bb-tone-warning-border)] bg-[var(--bb-tone-warning-bg)] px-4 py-3 text-sm text-[var(--bb-tone-warning-fg)]"
                          data-testid="query-zero-selectable"
                          aria-label="No selectable query compare rows"
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
                            <p class="max-w-sm text-xs">
                              Review each row's reason. A run can be unavailable because of its evidence, even when filters are cleared.
                            </p>
                          </div>
                        </section>
                      )}
                      <div
                        ref={resultsScrollerRef}
                        class="overflow-x-auto"
                        data-testid="query-results-scroll-container"
                      >
                        <table class="min-w-full w-max divide-y divide-[var(--bb-data-border)]">
                          <thead class="bg-[var(--bb-surface-data-muted)]">
                            <tr>
                              <th
                                scope="col"
                                class="table-th sticky left-0 z-10 w-12 min-w-12 bg-[var(--bb-surface-data-muted)] text-left"
                              >
                                <span class="sr-only">Compare</span>
                              </th>
                              {visibleColumns.map((column) => {
                                const isActive = sort.column === column;
                                const arrow = isActive ? (sort.direction === "asc" ? " ↑" : " ↓") : "";
                                const headerLabel = column === "power_score" ? "Power score" : formatQueryColumnLabel(column);
                                const ariaSort = isActive
                                  ? sort.direction === "asc"
                                    ? "ascending"
                                    : "descending"
                                  : "none";
                                return (
                                  <th key={column} scope="col" aria-sort={ariaSort} class={`p-0 ${queryColumnResponsiveClass(column)}`}>
                                    <button
                                      type="button"
                                      class="table-th block w-full cursor-pointer select-none border-0 bg-transparent text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--bb-accent)]"
                                      onClick={() => toggleSort(column)}
                                    >
                                      <span title={column === "power_score" ? "Higher is better" : undefined}>{headerLabel}{arrow}</span>
                                    </button>
                                  </th>
                                );
                              })}
                              <th class="table-th">
                                <span class="sr-only">Open</span>
                              </th>
                            </tr>
                          </thead>
                          <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
                            {visibleRows.map((row) => {
                              const id = String(row.result_id);
                              const isSelected = compareSelectedIds.has(id);
                              const reason =
                                (!isSelected ? comparisonExclusionReason(row) : undefined) ??
                                lockReason(row) ??
                                (!isSelected && compareSelectedIds.size >= MAX_COMPARE_SELECTIONS
                                  ? `Up to ${MAX_COMPARE_SELECTIONS} runs can be compared.`
                                  : undefined);
                              const disabledCopy = describeCompareExclusionReason(reason);
                              const reasonId = disabledCopy ? `query-compare-reason-${id}` : undefined;
                              return (
                                <tr
                                  key={id}
                                  class="hover:bg-[var(--bb-surface-data-muted)]"
                                  data-testid={`query-result-row-${id}`}
                                >
                                  <td class="table-td sticky left-0 z-10 max-w-[16rem] bg-[var(--bb-surface-data)]">
                                    <input
                                      type="checkbox"
                                      checked={isSelected}
                                      disabled={Boolean(reason)}
                                      title={disabledCopy?.detailText ?? reason}
                                      aria-describedby={reasonId}
                                      onChange={() => toggleCompareSelection(row)}
                                      class="h-4 w-4 rounded border-[var(--bb-data-border-strong)] disabled:cursor-not-allowed disabled:opacity-50"
                                      aria-label={compareSelectionLabel({
                                        platform: row.platform as string | undefined,
                                        benchmark: row.benchmark as string | undefined,
                                        scaleFactor: row.scale_factor as string | number | undefined,
                                        phase: (row.phase ?? row.test_type) as string | undefined,
                                        runDate: row.run_date as string | undefined,
                                        resultId: id,
                                      })}
                                      data-testid={`query-compare-checkbox-${id}`}
                                    />
                                    <QueryCompareReasonStatus id={reasonId} copy={disabledCopy} />
                                  </td>
                                  {visibleColumns.map((column) => (
                                    <td key={column} class={`table-td ${queryColumnResponsiveClass(column)}`}>
                                      {column === "run_date" ? (
                                        <RunDateChip runDate={typeof row.run_date === "string" ? row.run_date : null} />
                                      ) : (
                                        formatQueryRowCell(column, row[column])
                                      )}
                                    </td>
                                  ))}
                                  <td class="table-td text-right">
                                    <a href={`/results/r/${row.result_id}`} class="text-xs font-medium no-underline">
                                      View →
                                    </a>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </>
                  );
                })()}
                {pageCount > 1 && (
                  <nav
                    aria-label="Query result pages"
                    class="flex items-center justify-between border-t border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-3"
                    data-testid="query-pagination"
                  >
                    <button
                      type="button"
                      class="btn btn-secondary"
                      disabled={currentPage === 1}
                      onClick={() => setPageRaw(String(currentPage - 1))}
                    >
                      Previous page
                    </button>
                    <span class="text-sm text-[var(--bb-data-fg-muted)]">
                      Page {currentPage.toLocaleString()} of {pageCount.toLocaleString()}
                    </span>
                    <button
                      type="button"
                      class="btn btn-secondary"
                      disabled={currentPage >= pageCount}
                      onClick={() => setPageRaw(String(currentPage + 1))}
                    >
                      Next page
                    </button>
                  </nav>
                )}
              </div>
            )}
          </div>

          <details class="order-4 rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] p-4 shadow-sm">
            <summary class="cursor-pointer text-sm font-medium text-[var(--bb-data-fg-primary)]">Advanced SQL</summary>
            <div class="mt-4 space-y-4">
              <p class="text-sm text-[var(--bb-data-fg-muted)]">Write a read-only SQL query against the published results. Start from the current filters or choose an example.</p>
              <button class="btn btn-secondary" onClick={buildSqlFromFilters}>
                Build SQL from filters
              </button>
              <StarterQueries onSelect={loadStarterQuery} />
              <textarea
                class="min-h-40 w-full rounded-lg border border-[var(--bb-data-border-strong)] p-3 font-mono text-sm"
                value={sqlText}
                onInput={(event) => setSqlText((event.target as HTMLTextAreaElement).value)}
              />
              <div class="flex items-center gap-2">
                <button class="btn btn-secondary" onClick={runSql}>
                  Run SQL
                </button>
                {sqlError && (
                  <span role="alert" class="text-sm text-[var(--bb-tone-danger-fg)]">
                    {sqlError}
                  </span>
                )}
              </div>
              {sqlRows.length > 0 && (
                <div class="overflow-hidden rounded-lg border border-[var(--bb-data-border)]">
                  <div class="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] px-4 py-3 text-sm text-[var(--bb-data-fg-muted)]">
                    <span>Showing {visibleSqlRows.length.toLocaleString()} of {sqlRows.length.toLocaleString()} SQL rows</span>
                    <TableScrollHint
                      scrollerRef={sqlScrollerRef}
                      testId="query-sql-scroll-hint"
                      wrapperClassName={null}
                    />
                  </div>
                  <div ref={sqlScrollerRef} class="overflow-x-auto">
                    <table class="min-w-full w-max divide-y divide-[var(--bb-data-border)]">
                      <thead class="bg-[var(--bb-surface-data-muted)]">
                        <tr>
                          {sqlColumns.map((column) => (
                            <th key={column} class="table-th">
                              {column}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
                        {visibleSqlRows.map((row, index) => (
                          <tr key={index}>
                            {sqlColumns.map((column) => (
                              <td key={column} class="table-td">
                                {formatCell(row[column])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {visibleSqlRows.length < sqlRows.length && (
                    <div class="border-t border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-3 text-center">
                      <button
                        type="button"
                        class="btn btn-secondary"
                        onClick={() => setVisibleSqlLimit((limit) => limit + SQL_TABLE_RENDER_INCREMENT)}
                      >
                        Show more SQL rows
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </details>

          <details
            data-testid="query-visible-columns"
            class="order-5 rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] p-4 shadow-sm"
          >
            <summary class="cursor-pointer text-sm font-medium text-[var(--bb-data-fg-primary)]">
              Configure visible columns
            </summary>
            <div class="mt-3 space-y-3">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <p class="text-xs text-[var(--bb-data-fg-muted)]">
                  Choose the details shown in the results table and downloads. Technical field names remain available for SQL users.
                </p>
                <div class="flex flex-wrap gap-2" role="group" aria-label="Bulk visible-column actions">
                  <button
                    type="button"
                    class="btn btn-secondary text-xs"
                    onClick={resetVisibleColumnsToDefault}
                  >
                    Reset to default
                  </button>
                  <button
                    type="button"
                    class="btn btn-secondary text-xs"
                    onClick={selectAllVisibleColumns}
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    class="btn btn-secondary text-xs"
                    onClick={clearOptionalVisibleColumns}
                  >
                    Clear optional
                  </button>
                </div>
              </div>
              <div class="flex flex-wrap gap-2">
                {columnNames.map((column) => (
                  <label
                    key={column}
                    class="inline-flex items-center gap-2 rounded-full bg-[var(--bb-surface-app)] px-3 py-1 text-xs text-[var(--bb-data-fg-muted)]"
                    title={column}
                  >
                    <input
                      type="checkbox"
                      aria-label={formatQueryColumnLabel(column)}
                      checked={visibleColumns.includes(column)}
                      onChange={() => toggleColumn(column)}
                    />
                    <span>{formatQueryColumnLabel(column)}</span>
                    <code class="rounded bg-[var(--bb-surface-app)] px-1 font-mono text-xs text-[var(--bb-data-fg-subtle)]">{column}</code>
                  </label>
                ))}
              </div>
            </div>
          </details>
        </section>

        <aside
          data-testid="query-desktop-filters"
          class="hidden space-y-4 lg:sticky lg:top-4 lg:col-start-1 lg:row-start-1 lg:block lg:self-start"
        >
          <FacetRail
            groups={facetGroups}
            resultCount={resultTotal}
            activeChips={activeFilterChips}
            onToggle={toggleQueryFacet}
            onReset={resetQueryFilters}
          />
        </aside>
      </div>
    </div>
  );
}

interface MakeFacetGroupOptions {
  formatLabel?: (value: string) => string;
  searchable?: boolean;
  defaultCollapsed?: boolean;
}

function makeFacetGroup(
  key: string,
  label: string,
  buckets: FacetBucket[],
  selected: string[],
  options: MakeFacetGroupOptions = {},
): FacetGroup {
  const formatLabel =
    options.formatLabel ?? ((value: string) => formatQueryFacetValue(key, value));
  return {
    key,
    label,
    selected,
    searchable: options.searchable,
    defaultCollapsed: options.defaultCollapsed,
    options: buckets.map((bucket) => ({
      value: bucket.value,
      label: formatLabel(bucket.value),
      count: bucket.count,
    })),
  };
}

function makeActiveChips(
  keyPrefix: string,
  label: string,
  selected: string[],
  setSelected: (next: string[]) => void,
  formatLabel: (value: string) => string = (value) => formatQueryFacetValue(keyPrefix, value),
): ActiveFacetChip[] {
  return selected.map((value) => ({
    key: `${keyPrefix}:${value}`,
    label: `${label}: ${formatLabel(value)}`,
    onClear: () => setSelected(selected.filter((candidate) => candidate !== value)),
  }));
}

function formatHasCostLabel(value: string): string {
  if (value === "all") return "All";
  return value === "yes" ? "Has cost" : "No cost";
}

function formatDateWindowLabel(value: string): string {
  if (value === "all") return "All";
  return `Last ${value}`;
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return MISSING_VALUE_GLYPH;
  if (typeof value === "number") return formatPlainNumber(value).valueText;
  return String(value);
}

function projectVisibleRow(row: ResultRow, visibleColumns: string[]): ResultRow {
  return Object.fromEntries(visibleColumns.map((column) => [column, row[column]]));
}

function serializeCsv(rows: ResultRow[], columns: string[]): string {
  const lines = [
    columns.map(serializeCsvCell).join(","),
    ...rows.map((row) => columns.map((column) => serializeCsvCell(row[column])).join(",")),
  ];
  return `${lines.join("\r\n")}\r\n`;
}

function serializeCsvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = value instanceof Date ? value.toISOString() : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function formatQueryRowCell(column: string, value: unknown): string {
  if (typeof value === "string" && value !== "") {
    if (column === "trust_label") return formatTrustLabel(value);
    if (column === "funding") return formatFunding(value);
    if (column === "validation_status") return formatValidationStatus(value);
    if (column === "visibility") return formatVisibility(value);
    if (column === "cost_status") return formatCostStatus(value);
    if (column === "benchmark") return formatBenchmarkLabel(value);
  }
  return formatQueryCell(column, value);
}

function queryColumnResponsiveClass(column: string): string {
  return column === "trust_label" || column === "visibility" || column === "validation_status"
    ? "hidden md:table-cell"
    : "";
}

function QueryCompareReasonStatus({
  id,
  copy,
}: {
  id?: string;
  copy: CompareExclusionReasonCopy | null;
}) {
  if (copy === null) return null;
  return (
    <div id={id} class="text-xs text-[var(--bb-data-fg-muted)]" data-testid="query-disabled-reason">
      <span class="font-medium text-[var(--bb-tone-warning-fg)]">Why unavailable: {copy.shortText}</span>
      <span class="block">{copy.recoveryHint}</span>
    </div>
  );
}

function buildQueryFacetCountQueries(
  activeFilters: QueryFilterState,
  schema: SchemaColumn[],
): Record<string, BuiltQuery> {
  const schemaColumns = new Set(schema.map((column) => column.name));
  const facetQueries: Record<string, BuiltQuery> = {
    benchmark: buildFacetCountQuery("benchmark", activeFilters, { exclude: "benchmarks" }),
    platform: buildFacetCountQuery("platform", activeFilters, { exclude: "platforms" }),
    scale_factor: buildFacetCountQuery("scale_factor", activeFilters, { exclude: "scaleFactors" }),
    tuning_mode: buildFacetCountQuery("tuning_mode", activeFilters, { exclude: "tuningModes" }),
    trust_label: buildFacetCountQuery("trust_label", activeFilters, { exclude: "trustTiers" }),
    validation_status: buildFacetCountQuery("validation_status", activeFilters, { exclude: "validationStatuses" }),
    has_cost: buildFacetCountQuery("cost_usd", activeFilters, { exclude: "hasCost", derived: "has_cost" }),
    date_window: buildFacetCountQuery("run_date", activeFilters, { exclude: "dateWindow", derived: "date_window" }),
  };
  if (schemaColumns.has("cost_status")) {
    facetQueries.cost_status = buildFacetCountQuery("cost_status", activeFilters, { exclude: "costStatuses" });
  }
  if (schemaColumns.has("cost_model_version")) {
    facetQueries.cost_model_version = buildFacetCountQuery("cost_model_version", activeFilters, {
      exclude: "costModelVersions",
    });
  }
  if (schemaColumns.has("deployment_class")) {
    facetQueries.deployment_class = buildFacetCountQuery("deployment_class", activeFilters, {
      exclude: "deploymentClasses",
    });
  }
  if (schemaColumns.has("cloud_provider")) {
    facetQueries.cloud_provider = buildFacetCountQuery("cloud_provider", activeFilters, {
      exclude: "cloudProviders",
    });
  }
  if (schemaColumns.has("cloud_region")) {
    facetQueries.cloud_region = buildFacetCountQuery("cloud_region", activeFilters, { exclude: "cloudRegions" });
  }
  if (schemaColumns.has("instance_or_warehouse")) {
    facetQueries.instance_or_warehouse = buildFacetCountQuery("instance_or_warehouse", activeFilters, {
      exclude: "instanceOrWarehouses",
    });
  }
  if (schemaColumns.has("storage_format")) {
    facetQueries.storage_format = buildFacetCountQuery("storage_format", activeFilters, {
      exclude: "storageFormats",
    });
  }
  if (schemaColumns.has("physical_rendering_id")) {
    facetQueries.physical_rendering_id = buildFacetCountQuery("physical_rendering_id", activeFilters, {
      exclude: "physicalRenderingIds",
    });
  }
  return facetQueries;
}

function applySchemaFilterSupport(filters: QueryFilterState, schema: SchemaColumn[]): QueryFilterState {
  const columns = new Set(schema.map((column) => column.name));
  return {
    ...filters,
    costStatuses: columns.has("cost_status") ? filters.costStatuses : [],
    costModelVersions: columns.has("cost_model_version") ? filters.costModelVersions : [],
    deploymentClasses: columns.has("deployment_class") ? filters.deploymentClasses : [],
    cloudProviders: columns.has("cloud_provider") ? filters.cloudProviders : [],
    cloudRegions: columns.has("cloud_region") ? filters.cloudRegions : [],
    instanceOrWarehouses: columns.has("instance_or_warehouse") ? filters.instanceOrWarehouses : [],
    instanceTypes: [],
    warehouseSizes: [],
    storageFormats: columns.has("storage_format") ? filters.storageFormats : [],
    physicalRenderingIds: columns.has("physical_rendering_id") ? filters.physicalRenderingIds : [],
  };
}

function StarterQueries({ onSelect }: { onSelect: (sql: string) => void }) {
  const grouped = starterQueriesByCategory();
  const categoryOrder: StarterQueryCategory[] = [
    "results",
    "cost_and_deployment",
    "per_query_timings",
    "cohort_comparisons",
    "trust_and_tuning",
    "detail_drilldown",
  ];
  return (
    <section aria-label="Starter queries" class="rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] p-3">
      <h3 class="mb-2 text-sm font-semibold text-[var(--bb-data-fg-primary)]">Starter queries</h3>
      <p class="mb-3 text-xs text-[var(--bb-data-fg-muted)]">Load a read-only template into the editor below and customise it.</p>
      <div class="space-y-3">
        {categoryOrder.map((category) => {
          const items = grouped[category];
          if (items.length === 0) return null;
          return (
            <div key={category}>
              <div class="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--bb-data-fg-muted)]">
                {STARTER_QUERY_CATEGORIES[category]}
              </div>
              <div class="flex flex-wrap gap-2">
                {items.map((query) => (
                  <button
                    key={query.id}
                    type="button"
                    class="rounded-full border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-3 py-1 text-xs font-medium text-[var(--bb-data-fg-primary)] hover:bg-[var(--bb-tone-info-bg)] hover:text-[var(--bb-accent-hover)]"
                    title={query.description}
                    onClick={() => onSelect(query.sql)}
                  >
                    {query.label}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
