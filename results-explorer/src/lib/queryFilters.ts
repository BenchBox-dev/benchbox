import {
  NULL_PHYSICAL_RENDERING_SENTINELS,
  NULL_TUNING_MODE_SENTINELS,
  UNKNOWN_FACET_VALUE,
  addNullableSentinelClause,
  dateWindowCutoffIso,
  type DateWindowFacet,
} from "@/lib/facetModel";

export interface QueryFilterState {
  benchmarks: string[];
  platforms: string[];
  scaleFactors: string[];
  tuningModes: string[];
  trustTiers: string[];
  validationStatuses: string[];
  costStatuses: string[];
  costModelVersions: string[];
  deploymentClasses?: string[];
  cloudProviders: string[];
  cloudRegions: string[];
  instanceOrWarehouses?: string[];
  instanceTypes: string[];
  warehouseSizes: string[];
  storageFormats: string[];
  physicalRenderingIds: string[];
  hasCost: "all" | "yes" | "no";
  dateWindow: DateWindowFacet;
  platformVersions?: string[];
  archs?: string[];
  cpuFamilies?: string[];
}

export interface QuerySort {
  column: string;
  direction: "asc" | "desc";
}

export interface BuiltQuery {
  sql: string;
  params: unknown[];
}

const ALLOWED_COLUMNS = new Set([
  "result_id",
  "benchmark",
  "scale_factor",
  "platform",
  "platform_id",
  "driver_version",
  "run_date",
  "power_score",
  "throughput_at_size",
  "stream_count",
  "total_duration_s",
  "geomean_ms",
  "display_geomean_ms",
  "query_count",
  "has_display_timing",
  "valid_query_count",
  "missing_query_count",
  "zero_timing_count",
  "display_exclusion_reason",
  "comparison_exclusion_reason",
  "ranking_exclusion_reason",
  "trust_label",
  "visibility",
  "platform_version",
  "execution_mode",
  "tuning_mode",
  "tuning_hash",
  "test_type",
  "phase",
  "primary_metric",
  "validation_status",
  "cost_usd",
  "normalized_cost_usd",
  "cost_model_version",
  "cost_model_source",
  "cost_scope",
  "cost_status",
  "billing_unit",
  "pricing_region",
  "deployment_class",
  "cloud_provider",
  "cloud_region",
  "instance_or_warehouse",
  "instance_type",
  "warehouse_size",
  "node_count",
  "cluster_size",
  "storage_format",
  "physical_rendering_id",
  "storage_tier",
  "compliance_class",
  "power_score_source",
  "is_ranking_eligible",
  "has_plans",
  "plans_published",
  "has_tuning",
  "bundle_download_url",
  "arch",
  "cpu_family",
]);

function expandListFilter(
  column: string,
  values: string[],
  clauses: string[],
  params: unknown[],
) {
  if (values.length === 0) return;
  clauses.push(`${column} IN (${values.map(() => "?").join(", ")})`);
  params.push(...values);
}

export function buildWhereClause(filters: QueryFilterState): BuiltQuery {
  const clauses: string[] = [];
  const params: unknown[] = [];

  expandListFilter("benchmark", filters.benchmarks, clauses, params);
  expandListFilter("platform", filters.platforms, clauses, params);
  if (filters.scaleFactors.length > 0) {
    clauses.push(`scale_factor IN (${filters.scaleFactors.map(() => "?").join(", ")})`);
    params.push(...filters.scaleFactors.map((value) => Number(value)));
  }
  addNullableSentinelClause("tuning_mode", filters.tuningModes, NULL_TUNING_MODE_SENTINELS, clauses, params);
  expandListFilter("trust_label", filters.trustTiers, clauses, params);
  expandListFilter("validation_status", filters.validationStatuses, clauses, params);
  expandListFilter("cost_status", filters.costStatuses, clauses, params);
  expandListFilter("cost_model_version", filters.costModelVersions, clauses, params);
  expandListFilter("deployment_class", filters.deploymentClasses ?? [], clauses, params);
  expandListFilter("cloud_provider", filters.cloudProviders, clauses, params);
  expandListFilter("cloud_region", filters.cloudRegions, clauses, params);
  expandListFilter(
    "instance_or_warehouse",
    uniqueStrings([
      ...(filters.instanceOrWarehouses ?? []),
      ...filters.instanceTypes,
      ...filters.warehouseSizes,
    ]),
    clauses,
    params,
  );
  expandListFilter("storage_format", filters.storageFormats, clauses, params);
  addNullableSentinelClause(
    "physical_rendering_id",
    filters.physicalRenderingIds,
    NULL_PHYSICAL_RENDERING_SENTINELS,
    clauses,
    params,
    true,
  );

  if (filters.platformVersions && filters.platformVersions.length > 0) {
    expandListFilter("platform_version", filters.platformVersions, clauses, params);
  }
  if (filters.archs && filters.archs.length > 0) {
    clauses.push(
      "result_id IN (SELECT result_id FROM bench.result_environment WHERE arch IN (" +
        filters.archs.map(() => "?").join(", ") +
        "))",
    );
    params.push(...filters.archs);
  }
  if (filters.cpuFamilies && filters.cpuFamilies.length > 0) {
    clauses.push(
      "result_id IN (SELECT result_id FROM bench.result_environment WHERE cpu_family IN (" +
        filters.cpuFamilies.map(() => "?").join(", ") +
        "))",
    );
    params.push(...filters.cpuFamilies);
  }

  if (filters.hasCost === "yes") clauses.push("cost_usd IS NOT NULL");
  if (filters.hasCost === "no") clauses.push("cost_usd IS NULL");

  const cutoff = dateWindowCutoffIso(filters.dateWindow);
  if (cutoff) {
    clauses.push("run_date >= ?");
    params.push(cutoff);
  }

  return {
    sql: clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "",
    params,
  };
}

export const DEFAULT_ROW_LIMIT = 10000;
export const UNLIMITED_ROW_LIMIT = Number.MAX_SAFE_INTEGER;

export function buildSelectQuery(
  filters: QueryFilterState,
  columns: string[],
  sort: QuerySort,
  limit: number = DEFAULT_ROW_LIMIT,
): BuiltQuery {
  const safeColumns = columns.filter((column) => ALLOWED_COLUMNS.has(column));
  const projection = safeColumns.length > 0 ? safeColumns.join(", ") : "result_id";
  const safeSortColumn = ALLOWED_COLUMNS.has(sort.column) ? sort.column : "run_date";
  const safeDirection = sort.direction === "asc" ? "ASC" : "DESC";
  const where = buildWhereClause(filters);
  const safeLimit = Number.isFinite(limit) && limit > 0 ? Math.floor(limit) : DEFAULT_ROW_LIMIT;
  return {
    sql: `SELECT ${projection} FROM bench.results ${where.sql} ORDER BY ${safeSortColumn} ${safeDirection} LIMIT ${safeLimit}`,
    params: where.params,
  };
}

export function buildFacetCountQuery(
  column: string,
  filters: QueryFilterState,
  options: { exclude?: keyof QueryFilterState; derived?: "has_cost" | "date_window" } = {},
): BuiltQuery {
  const effective: QueryFilterState = {
    ...filters,
    benchmarks: options.exclude === "benchmarks" ? [] : filters.benchmarks,
    platforms: options.exclude === "platforms" ? [] : filters.platforms,
    scaleFactors: options.exclude === "scaleFactors" ? [] : filters.scaleFactors,
    tuningModes: options.exclude === "tuningModes" ? [] : filters.tuningModes,
    trustTiers: options.exclude === "trustTiers" ? [] : filters.trustTiers,
    validationStatuses:
      options.exclude === "validationStatuses" ? [] : filters.validationStatuses,
    costStatuses: options.exclude === "costStatuses" ? [] : filters.costStatuses,
    costModelVersions:
      options.exclude === "costModelVersions" ? [] : filters.costModelVersions,
    deploymentClasses:
      options.exclude === "deploymentClasses" ? [] : filters.deploymentClasses,
    cloudProviders: options.exclude === "cloudProviders" ? [] : filters.cloudProviders,
    cloudRegions: options.exclude === "cloudRegions" ? [] : filters.cloudRegions,
    instanceOrWarehouses:
      options.exclude === "instanceOrWarehouses" ? [] : filters.instanceOrWarehouses,
    instanceTypes: options.exclude === "instanceTypes" ? [] : filters.instanceTypes,
    warehouseSizes: options.exclude === "warehouseSizes" ? [] : filters.warehouseSizes,
    storageFormats: options.exclude === "storageFormats" ? [] : filters.storageFormats,
    physicalRenderingIds:
      options.exclude === "physicalRenderingIds" ? [] : filters.physicalRenderingIds,
    hasCost: options.exclude === "hasCost" ? "all" : filters.hasCost,
    dateWindow: options.exclude === "dateWindow" ? "all" : filters.dateWindow,
    platformVersions:
      options.exclude === "platformVersions" ? [] : (filters.platformVersions ?? []),
    archs: options.exclude === "archs" ? [] : (filters.archs ?? []),
    cpuFamilies: options.exclude === "cpuFamilies" ? [] : (filters.cpuFamilies ?? []),
  };
  const where = buildWhereClause(effective);

  if (options.derived === "has_cost") {
    return {
      sql: `
        SELECT CASE WHEN cost_usd IS NULL THEN 'no' ELSE 'yes' END AS value, COUNT(*) AS count
        FROM bench.results
        ${where.sql}
        GROUP BY 1
        ORDER BY 1
      `,
      params: where.params,
    };
  }

  if (options.derived === "date_window") {
    const cutoff30 = dateWindowCutoffIso("30d")!;
    const cutoff90 = dateWindowCutoffIso("90d")!;
    const cutoff365 = dateWindowCutoffIso("365d")!;
    const baseParams = where.params;
    const cutoffClause = where.sql ? `${where.sql} AND run_date >= ?` : "WHERE run_date >= ?";
    return {
      sql: `
        SELECT '30d'  AS value, COUNT(*) AS count FROM bench.results ${cutoffClause}
        UNION ALL
        SELECT '90d',  COUNT(*) FROM bench.results ${cutoffClause}
        UNION ALL
        SELECT '365d', COUNT(*) FROM bench.results ${cutoffClause}
        ORDER BY value
      `,
      params: [...baseParams, cutoff30, ...baseParams, cutoff90, ...baseParams, cutoff365],
    };
  }

  if (!ALLOWED_COLUMNS.has(column)) {
    throw new Error(`Unsupported facet column: ${column}`);
  }

  return {
    sql: `
      SELECT CASE WHEN ${column} IS NULL THEN '${UNKNOWN_FACET_VALUE}' ELSE CAST(${column} AS VARCHAR) END AS value, COUNT(*) AS count
      FROM bench.results
      ${where.sql}
      GROUP BY 1
      ORDER BY count DESC, value ASC
    `,
    params: where.params,
  };
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values)];
}

export function buildFacetSql(
  filters: QueryFilterState,
  columns: string[],
  sort: QuerySort = { column: "run_date", direction: "desc" },
  limit: number = DEFAULT_ROW_LIMIT,
): string {
  const query = buildSelectQuery(filters, columns, sort, limit);
  let paramIndex = 0;
  return query.sql.replace(/\?/g, () => renderSqlLiteral(query.params[paramIndex++]));
}

function renderSqlLiteral(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "NULL";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  const escaped = String(value).split("\\").join("\\\\").split("'").join("''");
  return `'${escaped}'`;
}

export type CohortGroupBy = "none" | "engine_version";

export const COHORT_GROUP_BY_LABELS: Record<CohortGroupBy, string> = {
  none: "No grouping",
  engine_version: "Platform version",
};

export interface CohortGroup<T> {
  key: string;
  label: string;
  rows: T[];
}

export interface LimitedCohortGroup<T> extends CohortGroup<T> {
  totalRows: number;
}

export const UNGROUPED_LABEL = "Not recorded";

export function groupCohortRows<T>(
  rows: readonly T[],
  groupBy: CohortGroupBy,
  readValue: (row: T) => string | null | undefined,
): CohortGroup<T>[] {
  if (groupBy === "none") {
    return [{ key: "all", label: "All runs", rows: [...rows] }];
  }
  const groups = new Map<string, T[]>();
  for (const row of rows) {
    const raw = readValue(row);
    const key = raw === null || raw === undefined || raw === "" ? UNGROUPED_LABEL : raw;
    const bucket = groups.get(key);
    if (bucket) bucket.push(row);
    else groups.set(key, [row]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => {
      if (a === UNGROUPED_LABEL) return 1;
      if (b === UNGROUPED_LABEL) return -1;
      return a.localeCompare(b, undefined, { numeric: true });
    })
    .map(([key, groupRows]) => ({ key, label: key, rows: groupRows }));
}

export function cohortGroupTotal<T>(groups: readonly CohortGroup<T>[]): number {
  return groups.reduce((sum, group) => sum + group.rows.length, 0);
}

export function limitCohortGroups<T>(
  groups: readonly CohortGroup<T>[],
  rankedRows: readonly T[],
  limit: number,
  keyOf: (row: T) => string = (row: any) =>
    String((row as any).result_id ?? (row as any).id ?? (row as any).key ?? JSON.stringify(row)),
  fingerprintOf: (row: T) => string = (row) => JSON.stringify(row),
): LimitedCohortGroup<T>[] {
  const window = rankedRows.slice(0, Math.max(0, Math.floor(limit)));
  const groupedReferences = new Set(groups.flatMap((group) => group.rows));
  const admittedReferences = new Set(window.filter((row) => groupedReferences.has(row)));
  const admittedCloneCounts = new Map<string, Map<string, number>>();
  for (const row of window) {
    if (admittedReferences.has(row)) continue;
    const key = keyOf(row);
    const fingerprint = fingerprintOf(row);
    const byFingerprint = admittedCloneCounts.get(key) ?? new Map<string, number>();
    byFingerprint.set(fingerprint, (byFingerprint.get(fingerprint) ?? 0) + 1);
    admittedCloneCounts.set(key, byFingerprint);
  }
  return groups.flatMap((group) => {
    const rows = group.rows.filter((row) => {
      if (admittedReferences.has(row)) return true;
      const key = keyOf(row);
      const fingerprint = fingerprintOf(row);
      const byFingerprint = admittedCloneCounts.get(key);
      const remaining = byFingerprint?.get(fingerprint) ?? 0;
      if (!byFingerprint || remaining === 0) return false;
      byFingerprint.set(fingerprint, remaining - 1);
      return true;
    });
    return rows.length > 0 ? [{ ...group, rows, totalRows: group.rows.length }] : [];
  });
}
