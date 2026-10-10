import { queryRows } from "@/db";
import type { BuiltQuery } from "@/lib/queryFilters";
import { canonicalPhaseSql, type FacetWhereClause } from "@/lib/facetModel";
import { canonicalBenchmarkSlug } from "@/lib/displayLabels";
import { normalizePrimaryMetric, type PrimaryMetric } from "@/lib/displayEligibility";
import type {
  BenchmarkSummary,
  CostDeploymentFields,
  DetailResult,
  Environment,
  MetaCohort,
  MetaLeaderboard,
  MetaPlatform,
  MetaRank,
  PercentileStats,
  PlatformRow,
  QueryDisplayTiming,
  QueryTiming,
} from "@/types";

export const QUERY_RESULT_PAGE_SIZE = 24;

export interface QueryResultPageQueries {
  rows: BuiltQuery;
  count: BuiltQuery;
}

const RESULT_SEARCH_SQL = ["platform", "platform_version", "result_id"]
  .map((column) => `CONTAINS(LOWER(COALESCE(CAST(${column} AS VARCHAR), '')), LOWER(?))`)
  .join(" OR ");

export function buildQueryResultPageQueries(
  baseQuery: BuiltQuery,
  searchText: string,
  offset: number,
  pageSize: number = QUERY_RESULT_PAGE_SIZE,
): QueryResultPageQueries {
  const parsed = parseResultSelect(baseQuery);
  const search = normalizeResultSearch(searchText);
  const whereSql = search === ""
    ? parsed.whereSql
    : `${parsed.whereSql}${parsed.whereSql === "" ? " WHERE " : " AND "}(${RESULT_SEARCH_SQL})`;
  const searchParams = search === "" ? [] : [search, search, search];
  const safeOffset = Math.max(0, Math.floor(offset));
  const safePageSize = Math.max(1, Math.floor(pageSize));
  const remaining = Math.max(0, parsed.limit - safeOffset);
  const pageLimit = Math.min(safePageSize, remaining);
  const sharedParams = [...baseQuery.params, ...searchParams];

  return {
    rows: {
      sql: `${parsed.selectSql}${whereSql} ORDER BY ${parsed.sortColumn} ${parsed.sortDirection} LIMIT ${pageLimit} OFFSET ${safeOffset}`,
      params: sharedParams,
    },
    count: {
      sql: `SELECT LEAST(COUNT(*), ${parsed.limit})::INTEGER AS count${parsed.fromSql}${whereSql}`,
      params: sharedParams,
    },
  };
}

export function buildQueryResultExportQuery(baseQuery: BuiltQuery, searchText: string): BuiltQuery {
  const parsed = parseResultSelect(baseQuery);
  const search = normalizeResultSearch(searchText);
  const whereSql = search === ""
    ? parsed.whereSql
    : `${parsed.whereSql}${parsed.whereSql === "" ? " WHERE " : " AND "}(${RESULT_SEARCH_SQL})`;
  return {
    sql: `${parsed.selectSql}${whereSql} ORDER BY ${parsed.sortColumn} ${parsed.sortDirection} LIMIT ${parsed.limit}`,
    params: search === "" ? baseQuery.params : [...baseQuery.params, search, search, search],
  };
}

interface ParsedResultSelect {
  selectSql: string;
  fromSql: string;
  whereSql: string;
  sortColumn: string;
  sortDirection: "ASC" | "DESC";
  limit: number;
}

function parseResultSelect(query: BuiltQuery): ParsedResultSelect {
  const normalizedSql = query.sql.replace(/\s+/g, " ").trim();
  const match = normalizedSql.match(
    /^(SELECT .+?)( FROM bench\.results)( WHERE .+?)? ORDER BY ([a-z_]+) (ASC|DESC) LIMIT (\d+)$/s,
  );
  if (!match) throw new Error("The results query could not be prepared.");
  return {
    selectSql: `${match[1]}${match[2]}`,
    fromSql: match[2]!,
    whereSql: match[3] ?? "",
    sortColumn: match[4]!,
    sortDirection: match[5] as "ASC" | "DESC",
    limit: Number(match[6]),
  };
}

function normalizeResultSearch(searchText: string): string {
  return searchText.trim();
}

export interface ResultRow extends CostDeploymentFields {
  result_id: string;
  benchmark: string;
  scale_factor: number;
  platform: string;
  platform_id: string;
  driver_version: string | null;
  run_date: string;
  power_score: number | null;
  throughput_at_size?: number | null;
  stream_count?: number | null;
  total_duration_s: number;
  geomean_ms: number | null;
  display_geomean_ms: number | null;
  query_count: number;
  logical_query_count?: number;
  has_display_timing: boolean;
  valid_query_count: number;
  missing_query_count: number;
  zero_timing_count: number;
  display_exclusion_reason: string | null;
  comparison_exclusion_reason: string | null;
  ranking_exclusion_reason: string | null;
  trust_label: string;
  funding: string;
  visibility: string;
  platform_version: string | null;
  execution_mode: string | null;
  tuning_mode: string | null;
  tuning_hash: string | null;
  override_rules?: string | null;
  requested_config_hash?: string | null;
  applied_ledger_hash?: string | null;
  tuning_validation_status?: string | null;
  tuning_policy_generation?: string | null;
  template_evidence?: string | null;
  test_type: string | null;
  validation_status: string | null;
  cost_usd: number | null;
  compliance_class: string | null;
  benchmark_support_status?: string | null;
  is_ranking_eligible: boolean;
  has_plans: boolean;
  plans_published: boolean;
  has_tuning: boolean;
  bundle_download_url: string;
  physical_rendering_id?: string | null;
  arch?: string | null;
  cpu_family?: string | null;
  memory_gb?: number | null;
}

export interface ResultDetailMetricsRow extends Omit<ResultRow, "is_ranking_eligible" | "visibility"> {
  visibility: string;
  funding: string;
  os: string | null;
  arch: string | null;
  cpu_count: number | null;
  memory_gb: number | null;
  python: string | null;
  cpu_model: string | null;
  cpu_family: string | null;
  cpu_identity_provenance: "measured" | "user_attested" | "inferred" | null;
  client_region: string | null;
  client_cloud: string | null;
  statement_overhead_min_ms: number | null;
  statement_overhead_median_ms: number | null;
  link_status: string | null;
  physical_mechanisms?: string | null;
  applied_receipt?: string | null;
  override_rules?: string | null;
  override_evidence?: string | null;
  override_approver?: string | null;
  override_expires?: string | null;
}

export interface QueryDisplayTimingRow {
  result_id: string;
  query_id: string;
  display_ms: number | null;
  sample_count: number;
  is_valid_display_timing: boolean;
  timing_exclusion_reason: string | null;
}

export interface QueryExecutionRow {
  result_id: string;
  query_id: string;
  duration_ms: number;
  status: string;
  run_type: string | null;
  iter: number | null;
  stream: number | null;
}

export interface ResultBasisAvailabilityRow {
  result_id: string;
  has_warmup: boolean;
  measurement_pass_count: number;
  warmup_status: string;
  available_bases: string;
  varying_pass_queries: string | null;
}

export interface BenchmarkMatrixCellRow {
  benchmark: string;
  scale_factor: number;
  phase: string;
  result_id: string;
  platform_id: string;
  query_id: string;
  display_ms: number | null;
  is_valid_display_timing: boolean;
  timing_exclusion_reason: string | null;
  stream_count: number | null;
}

export interface BenchmarkRankingRow extends CostDeploymentFields {
  benchmark: string;
  scale_factor: number;
  phase: string;
  result_id: string;
  platform_id: string;
  platform: string;
  short_id: string;
  trust_label: string;
  funding: string;
  platform_version?: string | null;
  validation_status?: string | null;
  override_rules?: string | null;
  tuning_mode: string | null;
  tuning_hash: string | null;
  execution_mode: string | null;
  compliance_class: string | null;
  run_date: string;
  is_ranking_eligible: boolean;
  has_display_timing: boolean;
  logical_query_count?: number;
  valid_query_count: number;
  missing_query_count: number;
  zero_timing_count: number;
  display_exclusion_reason: string | null;
  comparison_exclusion_reason: string | null;
  ranking_exclusion_reason: string | null;
  power_score: number | null;
  throughput_at_size: number | null;
  stream_count: number | null;
  display_geomean_ms: number | null;
  sample_geomean_ms: number | null;
  cost_usd: number | null;
  primary_metric: string;
  primary_order: "asc" | "desc";
  rank: number | null;
  total_in_cohort: number;
  cohort_ranked_count: number;
  cohort_ranking_exclusion_reason: string | null;
  percentile_p50: number | null;
  percentile_p90: number | null;
  percentile_p95: number | null;
  percentile_p99: number | null;
  speedup_vs_best: number | null;
  speedup_vs_slowest_in_cohort: number | null;
}

export interface PlatformIndexRowRow extends CostDeploymentFields {
  result_id: string;
  short_id: string;
  benchmark: string;
  scale_factor: number;
  phase: string;
  platform: string;
  platform_id: string;
  driver_version: string | null;
  platform_version?: string | null;
  run_date: string;
  power_score: number | null;
  throughput_at_size?: number | null;
  stream_count?: number | null;
  total_duration_s: number;
  geomean_ms: number | null;
  display_geomean_ms: number | null;
  query_count: number;
  logical_query_count?: number;
  has_display_timing: boolean;
  valid_query_count: number;
  missing_query_count: number;
  zero_timing_count: number;
  display_exclusion_reason: string | null;
  comparison_exclusion_reason: string | null;
  ranking_exclusion_reason: string | null;
  trust_label: string;
  funding: string;
  validation_status?: string | null;
  override_rules?: string | null;
  tuning_mode: string | null;
  tuning_validation_status?: string | null;
  execution_mode: string | null;
  compliance_class: string | null;
  cost_usd: number | null;
  primary_metric: string;
  arch?: string | null;
  cpu_family?: string | null;
  memory_gb?: number | null;
}

export interface CohortMetadataRow {
  cohort_key: string;
  benchmark: string;
  scale_factor: number;
  phase: string;
  stream_count: number | null;
  cohort_label: string;
  cohort_href: string;
  platform_count: number;
  cohort_ranked_count: number;
  cohort_ranking_exclusion_reason: string | null;
  primary_metric: string;
  primary_order: "asc" | "desc";
  platform_id: string;
  platform: string;
  result_id: string;
  short_id: string;
  tuning_mode: string | null;
  trust_label: string;
  has_display_timing: boolean;
  logical_query_count?: number;
  valid_query_count: number;
  missing_query_count: number;
  zero_timing_count: number;
  display_exclusion_reason: string | null;
  comparison_exclusion_reason: string | null;
  ranking_exclusion_reason: string | null;
  rank: number | null;
  metric_value: number | null;
  speedup_vs_best: number | null;
}

export interface MetaLeaderboardRow {
  platform_id: string;
  platform: string;
  avg_rank: number | null;
  n_cohorts: number;
}

const RESULTS_SNAPSHOT_PATH = "/results/data/results.duckdb";
const snapshotQueryCache = new Map<string, Promise<unknown>>();

interface SnapshotRowsQuery {
  sql: string;
  params?: readonly unknown[];
}

interface SnapshotQueryCacheOptions<T> {
  cacheResult?: (result: T) => boolean;
}

interface SnapshotRowsCacheOptions {
  cacheEmpty?: boolean;
}

const RESULT_COLUMNS = [
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
  "logical_query_count",
  "has_display_timing",
  "valid_query_count",
  "missing_query_count",
  "zero_timing_count",
  "display_exclusion_reason",
  "comparison_exclusion_reason",
  "ranking_exclusion_reason",
  "trust_label",
  "funding",
  "visibility",
  "platform_version",
  "execution_mode",
  "tuning_mode",
  "tuning_hash",
  "requested_config_hash",
  "applied_ledger_hash",
  "tuning_validation_status",
  "tuning_policy_generation",
  "template_evidence",
  "test_type",
  "validation_status",
  "override_rules",
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
  "storage_tier",
  "compliance_class",
  "is_ranking_eligible",
  "has_plans",
  "plans_published",
  "has_tuning",
  "bundle_download_url",
  "physical_rendering_id",
  "benchmark_support_status",
].join(", ");

const RESULT_HARDWARE_COLUMNS = `${RESULT_COLUMNS}, arch, cpu_family, memory_gb`;

const RESULT_DETAIL_METRICS_COLUMNS = [
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
  "logical_query_count",
  "has_display_timing",
  "valid_query_count",
  "missing_query_count",
  "zero_timing_count",
  "display_exclusion_reason",
  "comparison_exclusion_reason",
  "ranking_exclusion_reason",
  "trust_label",
  "visibility",
  "funding",
  "platform_version",
  "execution_mode",
  "tuning_mode",
  "tuning_hash",
  "requested_config_hash",
  "applied_ledger_hash",
  "tuning_validation_status",
  "applied_receipt",
  "override_rules",
  "override_evidence",
  "override_approver",
  "override_expires",
  "tuning_policy_generation",
  "template_evidence",
  "test_type",
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
  "storage_tier",
  "compliance_class",
  "has_plans",
  "plans_published",
  "has_tuning",
  "bundle_download_url",
  "physical_mechanisms",
  "physical_rendering_id",
  "os",
  "arch",
  "cpu_count",
  "memory_gb",
  "python",
  "cpu_model",
  "cpu_family",
  "cpu_identity_provenance",
  "client_region",
  "client_cloud",
  "statement_overhead_min_ms",
  "statement_overhead_median_ms",
  "link_status",
].join(", ");

const COHORT_METADATA_COLUMNS = [
  "cohort_key",
  "benchmark",
  "scale_factor",
  "phase",
  "stream_count",
  "cohort_label",
  "cohort_href",
  "platform_count",
  "cohort_ranked_count",
  "cohort_ranking_exclusion_reason",
  "primary_metric",
  "primary_order",
  "platform_id",
  "platform",
  "result_id",
  "short_id",
  "tuning_mode",
  "trust_label",
  "has_display_timing",
  "logical_query_count",
  "valid_query_count",
  "missing_query_count",
  "zero_timing_count",
  "display_exclusion_reason",
  "comparison_exclusion_reason",
  "ranking_exclusion_reason",
  "rank",
  "metric_value",
  "speedup_vs_best",
].join(", ");

const BENCHMARK_RANKING_COLUMNS = [
  "br.benchmark",
  "br.scale_factor",
  "br.phase",
  "br.result_id",
  "br.platform_id",
  "br.platform",
  "br.short_id",
  "br.trust_label",
  "br.funding",
  "br.tuning_mode",
  "br.tuning_hash",
  "br.execution_mode",
  "br.compliance_class",
  "br.run_date",
  "br.is_ranking_eligible",
  "br.has_display_timing",
  "br.logical_query_count",
  "br.valid_query_count",
  "br.missing_query_count",
  "br.zero_timing_count",
  "br.display_exclusion_reason",
  "br.comparison_exclusion_reason",
  "br.ranking_exclusion_reason",
  "br.power_score",
  "br.throughput_at_size",
  "br.stream_count",
  "br.display_geomean_ms",
  "br.sample_geomean_ms",
  "br.cost_usd",
  "br.primary_metric",
  "br.primary_order",
  "br.rank",
  "br.total_in_cohort",
  "br.cohort_ranked_count",
  "br.cohort_ranking_exclusion_reason",
  "br.percentile_p50",
  "br.percentile_p90",
  "br.percentile_p95",
  "br.percentile_p99",
  "br.speedup_vs_best",
  "br.speedup_vs_slowest_in_cohort",
].join(", ");

function currentSnapshotCacheKey(): string {
  if (typeof window === "undefined") return RESULTS_SNAPSHOT_PATH;
  return new URL(RESULTS_SNAPSHOT_PATH, window.location.origin).href;
}

function memoizedSnapshotQuery<T>(
  key: string,
  loader: () => Promise<T>,
  options: SnapshotQueryCacheOptions<T> = {},
): Promise<T> {
  const cacheKey = `${currentSnapshotCacheKey()}\u0000${key}`;
  const cached = snapshotQueryCache.get(cacheKey);
  if (cached) return cached as Promise<T>;

  const promise = loader()
    .then((result) => {
      if (options.cacheResult && !options.cacheResult(result)) {
        snapshotQueryCache.delete(cacheKey);
      }
      return result;
    })
    .catch((error: unknown) => {
      snapshotQueryCache.delete(cacheKey);
      throw error;
    });
  snapshotQueryCache.set(cacheKey, promise);
  return promise;
}

export function clearDuckdbQueryCachesForTests() {
  snapshotQueryCache.clear();
}

export function memoizedSnapshotQueryRows<T>(
  key: string,
  query: SnapshotRowsQuery,
  options: SnapshotRowsCacheOptions = {},
): Promise<T[]> {
  const params = query.params ?? [];
  return memoizedSnapshotQuery(
    `rows:${key}\u0000${query.sql}\u0000${JSON.stringify(params)}`,
    () => (params.length > 0 ? queryRows<T>(query.sql, [...params]) : queryRows<T>(query.sql)),
    {
      cacheResult:
        options.cacheEmpty === false
          ? (rows) => rows.length > 0
          : undefined,
    },
  );
}

export async function listResults(
  where: FacetWhereClause = { sql: "", params: [] },
  options: { includeHardware?: boolean } = {},
): Promise<ResultRow[]> {
  const needsHardware = options.includeHardware === true || /\b(?:arch|cpu_family|memory_gb)\b/.test(where.sql);
  const columns = needsHardware ? RESULT_HARDWARE_COLUMNS : RESULT_COLUMNS;
  const source = needsHardware
    ? "(SELECT r.*, d.arch, d.cpu_family, d.memory_gb FROM bench.results r LEFT JOIN bench.result_detail_metrics d USING (result_id))"
    : "bench.results";
  const sql = `SELECT ${columns} FROM ${source} ${where.sql} ORDER BY run_date DESC`;
  return memoizedSnapshotQueryRows<ResultRow>("list-results", { sql, params: where.params }, { cacheEmpty: false });
}

export async function listBenchmarksWithPublicResults(): Promise<string[]> {
  const rows = await memoizedSnapshotQueryRows<{ benchmark: string }>(
    "distinct-benchmarks-with-public-results",
    {
      sql:
        "SELECT DISTINCT CASE WHEN benchmark = 'star_schema' THEN 'ssb' ELSE lower(benchmark) END AS benchmark " +
        "FROM bench.results ORDER BY benchmark",
      params: [],
    },
    { cacheEmpty: false },
  );
  return rows.map((row) => row.benchmark);
}

export async function getResultDetailMetrics(resultId: string): Promise<ResultDetailMetricsRow | null> {
  const rows = await queryRows<ResultDetailMetricsRow>(
    `SELECT ${RESULT_DETAIL_METRICS_COLUMNS} FROM bench.result_detail_metrics WHERE result_id = ?`,
    [resultId],
  );
  return rows[0] ?? null;
}

export async function getQueryDisplayTimings(
  resultIdOrIds: string | readonly string[],
): Promise<QueryDisplayTimingRow[]> {
  if (Array.isArray(resultIdOrIds)) {
    if (resultIdOrIds.length === 0) return [];
    const placeholders = resultIdOrIds.map(() => "?").join(",");
    return queryRows<QueryDisplayTimingRow>(
      "SELECT result_id, query_id, display_ms, sample_count," +
        " is_valid_display_timing, timing_exclusion_reason" +
        " FROM bench.query_display_timings" +
        ` WHERE result_id IN (${placeholders})` +
        " ORDER BY query_id",
      [...resultIdOrIds],
    );
  }
  return queryRows<QueryDisplayTimingRow>(
    "SELECT result_id, query_id, display_ms, sample_count," +
      " is_valid_display_timing, timing_exclusion_reason" +
      " FROM bench.query_display_timings" +
      " WHERE result_id = ?" +
      " ORDER BY query_id",
    [resultIdOrIds],
  );
}

export async function getQueryExecutions(
  resultIdOrIds: string | readonly string[],
): Promise<QueryExecutionRow[]> {
  if (Array.isArray(resultIdOrIds)) {
    if (resultIdOrIds.length === 0) return [];
    const placeholders = resultIdOrIds.map(() => "?").join(",");
    return queryRows<QueryExecutionRow>(
      "SELECT result_id, query_id, duration_ms, status, run_type, iter, stream" +
        " FROM bench.query_executions" +
        ` WHERE result_id IN (${placeholders})` +
        " ORDER BY query_id," +
        " CASE WHEN stream IS NULL THEN 0 ELSE stream END," +
        " CASE WHEN iter IS NULL THEN 0 ELSE iter END",
      [...resultIdOrIds],
    );
  }
  return queryRows<QueryExecutionRow>(
    "SELECT result_id, query_id, duration_ms, status, run_type, iter, stream" +
      " FROM bench.query_executions" +
      " WHERE result_id = ?" +
      " ORDER BY query_id," +
      " CASE WHEN stream IS NULL THEN 0 ELSE stream END," +
      " CASE WHEN iter IS NULL THEN 0 ELSE iter END",
    [resultIdOrIds],
  );
}

export async function getCohortBasisDetails(
  resultIds: readonly string[],
): Promise<Map<string, DetailResult>> {
  if (resultIds.length === 0) return new Map();
  const placeholders = resultIds.map(() => "?").join(",");
  const [wideRows, timingRows, executionRows] = await Promise.all([
    queryRows<ResultDetailMetricsRow>(
      `SELECT ${RESULT_DETAIL_METRICS_COLUMNS} FROM bench.result_detail_metrics WHERE result_id IN (${placeholders})`,
      [...resultIds],
    ),
    getQueryDisplayTimings(resultIds),
    getQueryExecutions(resultIds),
  ]);

  const timingsByResult = new Map<string, QueryDisplayTimingRow[]>();
  for (const row of timingRows) {
    const list = timingsByResult.get(row.result_id);
    if (list) list.push(row);
    else timingsByResult.set(row.result_id, [row]);
  }

  const executionsByResult = new Map<string, QueryExecutionRow[]>();
  for (const row of executionRows) {
    const list = executionsByResult.get(row.result_id);
    if (list) list.push(row);
    else executionsByResult.set(row.result_id, [row]);
  }

  const byId = new Map<string, DetailResult>();
  for (const wide of wideRows) {
    byId.set(
      wide.result_id,
      detailResultFromWideRow(
        wide,
        executionsByResult.get(wide.result_id) ?? [],
        timingsByResult.get(wide.result_id) ?? [],
      ),
    );
  }
  return byId;
}

export async function getResultsBasisAvailability(resultIds: readonly string[]): Promise<ResultBasisAvailabilityRow[]> {
  if (resultIds.length === 0) return [];
  return queryRows<ResultBasisAvailabilityRow>(
    "SELECT result_id, has_warmup, measurement_pass_count, warmup_status, available_bases, varying_pass_queries" +
      ` FROM bench.result_basis_availability WHERE result_id IN (${resultIds.map(() => "?").join(",")})`,
    [...resultIds],
  );
}

export async function getResultBasisAvailability(
  resultId: string,
): Promise<ResultBasisAvailabilityRow | null> {
  const rows = await queryRows<ResultBasisAvailabilityRow>(
    "SELECT result_id, has_warmup, measurement_pass_count, warmup_status," +
      " available_bases, varying_pass_queries" +
      " FROM bench.result_basis_availability" +
      " WHERE result_id = ?",
    [resultId],
  );
  return rows[0] ?? null;
}

function detailResultFromWideRow(
  wide: ResultDetailMetricsRow,
  executionRows: readonly QueryExecutionRow[],
  timingRows: readonly QueryDisplayTimingRow[],
): DetailResult {
  const environment: Environment = {};
  if (wide.os !== null) environment.os = wide.os;
  if (wide.arch !== null) environment.arch = wide.arch;
  if (wide.cpu_count !== null) environment.cpu_count = wide.cpu_count;
  if (wide.memory_gb !== null) environment.memory_gb = wide.memory_gb;
  if (wide.python !== null) environment.python = wide.python;
  if (wide.cpu_model !== null) environment.cpu_model = wide.cpu_model;
  if (wide.cpu_family !== null) environment.cpu_family = wide.cpu_family;
  if (wide.cpu_identity_provenance !== null) environment.cpu_identity_provenance = wide.cpu_identity_provenance;
  if (wide.client_region !== null && wide.client_region !== undefined) environment.client_region = wide.client_region;
  if (wide.client_cloud !== null && wide.client_cloud !== undefined) environment.client_cloud = wide.client_cloud;
  if (wide.statement_overhead_min_ms !== null && wide.statement_overhead_min_ms !== undefined) {
    environment.statement_overhead_min_ms = wide.statement_overhead_min_ms;
  }
  if (wide.statement_overhead_median_ms !== null && wide.statement_overhead_median_ms !== undefined) {
    environment.statement_overhead_median_ms = wide.statement_overhead_median_ms;
  }
  if (wide.link_status !== null && wide.link_status !== undefined) environment.link_status = wide.link_status;

  const display_timings: QueryDisplayTiming[] = timingRows.map((r) => ({
    query_id: r.query_id,
    display_ms: r.display_ms,
    sample_count: r.sample_count,
    is_valid_display_timing: r.is_valid_display_timing,
    timing_exclusion_reason: r.timing_exclusion_reason,
  }));

  const queries: QueryTiming[] = executionRows.map((r) => ({
    query_id: r.query_id,
    duration_ms: r.duration_ms,
    status: r.status === "pass" || r.status === "fail" ? r.status : "fail",
    run_type: r.run_type,
    iter: r.iter,
    stream: r.stream,
  }));

  return {
    result_id: wide.result_id,
    benchmark: wide.benchmark,
    scale_factor: wide.scale_factor,
    platform: wide.platform,
    platform_id: wide.platform_id,
    driver_version: wide.driver_version,
    run_date: wide.run_date,
    total_duration_s: wide.total_duration_s,
    geomean_ms: wide.geomean_ms,
    display_geomean_ms: wide.display_geomean_ms,
    power_score: wide.power_score,
    throughput_at_size: wide.throughput_at_size ?? null,
    stream_count: wide.stream_count ?? null,
    has_display_timing: wide.has_display_timing,
    logical_query_count: wide.logical_query_count,
    valid_query_count: wide.valid_query_count,
    missing_query_count: wide.missing_query_count,
    zero_timing_count: wide.zero_timing_count,
    display_exclusion_reason: wide.display_exclusion_reason,
    comparison_exclusion_reason: wide.comparison_exclusion_reason,
    ranking_exclusion_reason: wide.ranking_exclusion_reason,
    environment,
    queries,
    display_timings,
    has_plans: wide.has_plans,
    plans_published: wide.plans_published,
    has_tuning: wide.has_tuning,
    bundle_download_url: wide.bundle_download_url,
    trust_label: wide.trust_label,
    visibility: wide.visibility,
    funding: wide.funding,
    platform_version: wide.platform_version,
    execution_mode: wide.execution_mode,
    tuning_mode: wide.tuning_mode,
    tuning_hash: wide.tuning_hash,
    requested_config_hash: wide.requested_config_hash ?? null,
    applied_ledger_hash: wide.applied_ledger_hash ?? null,
    tuning_validation_status: wide.tuning_validation_status ?? null,
    applied_receipt: wide.applied_receipt ?? null,
    override_rules: wide.override_rules ?? null,
    override_evidence: wide.override_evidence ?? null,
    override_approver: wide.override_approver ?? null,
    override_expires: wide.override_expires ?? null,
    tuning_policy_generation: wide.tuning_policy_generation ?? null,
    template_evidence: wide.template_evidence ?? null,
    test_type: wide.test_type,
    validation_status: wide.validation_status,
    cost_usd: wide.cost_usd,
    normalized_cost_usd: wide.normalized_cost_usd,
    cost_model_version: wide.cost_model_version,
    cost_model_source: wide.cost_model_source,
    cost_scope: wide.cost_scope,
    cost_status: wide.cost_status,
    billing_unit: wide.billing_unit,
    pricing_region: wide.pricing_region,
    cloud_provider: wide.cloud_provider,
    cloud_region: wide.cloud_region,
    instance_type: wide.instance_type,
    warehouse_size: wide.warehouse_size,
    node_count: wide.node_count,
    cluster_size: wide.cluster_size,
    storage_format: wide.storage_format,
    storage_tier: wide.storage_tier,
    compliance_class: wide.compliance_class,
    physical_mechanisms:
      wide.physical_mechanisms === null || wide.physical_mechanisms === undefined
        ? undefined
        : wide.physical_mechanisms === ""
          ? []
          : wide.physical_mechanisms.split(","),
    physical_rendering_id: wide.physical_rendering_id,
    client_region: wide.client_region,
    client_cloud: wide.client_cloud,
    statement_overhead_min_ms: wide.statement_overhead_min_ms,
    statement_overhead_median_ms: wide.statement_overhead_median_ms,
    link_status: wide.link_status,
  };
}

export async function getDetailResult(resultId: string): Promise<DetailResult | null> {
  const [wide, timingRows, executionRows] = await Promise.all([
    getResultDetailMetrics(resultId),
    getQueryDisplayTimings(resultId),
    getQueryExecutions(resultId),
  ]);
  if (!wide) return null;
  return detailResultFromWideRow(wide, executionRows, timingRows);
}

function streamCountClause(column: string, streamCount: number | null): { sql: string; params: number[] } {
  return streamCount === null
    ? { sql: ` AND ${column} IS NULL`, params: [] }
    : { sql: ` AND ${column} = ?`, params: [streamCount] };
}

export async function getBenchmarkMatrixCells(
  benchmark: string,
  scaleFactor: number,
  phase: string,
  streamCount: number | null = null,
): Promise<BenchmarkMatrixCellRow[]> {
  benchmark = canonicalBenchmarkSlug(benchmark);
  const streams = streamCountClause("stream_count", streamCount);
  return queryRows<BenchmarkMatrixCellRow>(
    "SELECT benchmark, scale_factor, phase, result_id, platform_id, query_id, display_ms," +
      " is_valid_display_timing, timing_exclusion_reason, stream_count" +
      " FROM bench.benchmark_matrix_cells" +
      ` WHERE benchmark = ? AND scale_factor = ? AND phase = ?${streams.sql}` +
      " ORDER BY platform_id, query_id",
    [benchmark, scaleFactor, phase, ...streams.params],
  );
}

export async function getBenchmarkRanking(
  benchmark: string,
  scaleFactor: number,
  phase: string,
  streamCount: number | null = null,
): Promise<BenchmarkRankingRow[]> {
  benchmark = canonicalBenchmarkSlug(benchmark);
  const streams = streamCountClause("br.stream_count", streamCount);
  return queryRows<BenchmarkRankingRow>(
    `SELECT ${BENCHMARK_RANKING_COLUMNS}, r.platform_version, r.validation_status, r.override_rules,` +
      " r.normalized_cost_usd, r.cost_model_version, r.cost_model_source," +
      " r.cost_scope, r.cost_status, r.billing_unit, r.pricing_region," +
      " r.deployment_class, r.cloud_provider, r.cloud_region, r.instance_or_warehouse," +
      " r.storage_format" +
      " FROM bench.benchmark_rankings br" +
      " LEFT JOIN bench.results r USING (result_id)" +
      ` WHERE br.benchmark = ? AND br.scale_factor = ? AND br.phase = ?${streams.sql}` +
      " ORDER BY br.rank NULLS LAST, br.platform_id",
    [benchmark, scaleFactor, phase, ...streams.params],
  );
}

export async function getResultPhaseDurations(resultIds: string[]): Promise<Map<string, Record<string, number>>> {
  const result = new Map<string, Record<string, number>>();
  if (resultIds.length === 0) return result;
  const placeholders = resultIds.map(() => "?").join(", ");
  const rows = await queryRows<{ result_id: string; phase: string; duration_s: number }>(
    `SELECT result_id, phase, duration_s FROM bench.result_phase_durations` + ` WHERE result_id IN (${placeholders})`,
    resultIds,
  );
  for (const row of rows) {
    let bucket = result.get(row.result_id);
    if (!bucket) {
      bucket = {};
      result.set(row.result_id, bucket);
    }
    bucket[row.phase] = row.duration_s;
  }
  return result;
}

export async function getBenchmarkSummaryFromDuckDB(
  benchmark: string,
  scaleFactor: number,
  phase: string,
  streamCount: number | null = null,
): Promise<BenchmarkSummary | null> {
  benchmark = canonicalBenchmarkSlug(benchmark);
  return memoizedSnapshotQuery(
    `benchmark-summary:${benchmark}\u0000${scaleFactor}\u0000${phase}\u0000${streamCount ?? ""}`,
    () => loadBenchmarkSummaryFromDuckDB(benchmark, scaleFactor, phase, streamCount),
  );
}

async function loadBenchmarkSummaryFromDuckDB(
  benchmark: string,
  scaleFactor: number,
  phase: string,
  streamCount: number | null,
): Promise<BenchmarkSummary | null> {
  const [rankingRows, cellRows] = await Promise.all([
    getBenchmarkRanking(benchmark, scaleFactor, phase, streamCount),
    getBenchmarkMatrixCells(benchmark, scaleFactor, phase, streamCount),
  ]);
  if (rankingRows.length === 0) return null;

  const phaseDurationsByResult = await getResultPhaseDurations(rankingRows.map((r) => r.result_id));

  const queryIds = [...new Set(cellRows.map((c) => c.query_id))].sort();
  const cellsByResult = new Map<string, Map<string, BenchmarkMatrixCellRow>>();
  for (const cell of cellRows) {
    let byQuery = cellsByResult.get(cell.result_id);
    if (!byQuery) {
      byQuery = new Map();
      cellsByResult.set(cell.result_id, byQuery);
    }
    byQuery.set(cell.query_id, cell);
  }

  const platforms: PlatformRow[] = rankingRows.map((row) => {
    const byQuery = cellsByResult.get(row.result_id);
    const timings: Record<string, number | null> = {};
    const timingEligibility: PlatformRow["timing_eligibility"] = {};
    for (const qid of queryIds) {
      const cell = byQuery?.get(qid) ?? null;
      timings[qid] = cell?.display_ms ?? null;
      timingEligibility[qid] = {
        is_valid_display_timing: cell?.is_valid_display_timing ?? false,
        timing_exclusion_reason: cell ? cell.timing_exclusion_reason : "missing_timing",
      };
    }
    const percentileStats: PercentileStats | null =
      row.percentile_p50 !== null &&
      row.percentile_p90 !== null &&
      row.percentile_p95 !== null &&
      row.percentile_p99 !== null
        ? {
            p50: row.percentile_p50,
            p90: row.percentile_p90,
            p95: row.percentile_p95,
            p99: row.percentile_p99,
          }
        : null;
    return {
      result_id: row.result_id,
      short_id: row.short_id,
      platform_id: row.platform_id,
      platform: row.platform,
      platform_version: row.platform_version ?? null,
      tuning_mode: row.tuning_mode,
      tuning_hash: row.tuning_hash,
      execution_mode: row.execution_mode,
      trust_label: row.trust_label,
      funding: row.funding,
      validation_status: row.validation_status ?? null,
      override_rules: row.override_rules ?? null,
      run_date: row.run_date,
      is_ranking_eligible: row.is_ranking_eligible,
      has_display_timing: row.has_display_timing,
      logical_query_count: row.logical_query_count,
      valid_query_count: row.valid_query_count,
      missing_query_count: row.missing_query_count,
      zero_timing_count: row.zero_timing_count,
      display_exclusion_reason: row.display_exclusion_reason,
      comparison_exclusion_reason: row.comparison_exclusion_reason,
      ranking_exclusion_reason: row.ranking_exclusion_reason,
      power_score: row.power_score,
      throughput_at_size: row.throughput_at_size,
      display_geomean_ms: row.display_geomean_ms,
      sample_geomean_ms: row.sample_geomean_ms,
      cost_usd: row.cost_usd,
      normalized_cost_usd: row.normalized_cost_usd,
      cost_model_version: row.cost_model_version,
      cost_model_source: row.cost_model_source,
      cost_scope: row.cost_scope,
      cost_status: row.cost_status,
      billing_unit: row.billing_unit,
      pricing_region: row.pricing_region,
      deployment_class: row.deployment_class,
      cloud_provider: row.cloud_provider,
      cloud_region: row.cloud_region,
      instance_or_warehouse: row.instance_or_warehouse,
      storage_format: row.storage_format,
      compliance_class: row.compliance_class,
      percentile_stats: percentileStats,
      phase_durations: phaseDurationsByResult.get(row.result_id) ?? null,
      timings,
      timing_eligibility: timingEligibility,
    };
  });

  const first = rankingRows[0]!;
  return {
    benchmark,
    scale_factor: scaleFactor,
    phase,
    stream_count: streamCount,
    query_ids: queryIds,
    platforms,
    cell_reduction: "median_successful_measurement_ms",
    ranking: {
      primary_metric: first.primary_metric,
      secondary_metric: normalizePrimaryMetric(first.primary_metric) === "display_geomean_ms" ? "power_score" : "display_geomean_ms",
      primary_order: first.primary_order,
    },
  };
}

export async function getPlatformIndexRows(platformId?: string): Promise<PlatformIndexRowRow[]> {
  return memoizedSnapshotQuery(
    `platform-index:${platformId ?? "*"}`,
    () => loadPlatformIndexRows(platformId),
    { cacheResult: hasPlatformIndexData },
  );
}

function hasPlatformIndexData(rows: PlatformIndexRowRow[]): boolean {
  return rows.length > 0;
}

function loadPlatformIndexRows(platformId?: string): Promise<PlatformIndexRowRow[]> {
  const sql =
    "SELECT" +
    " r.result_id," +
    " COALESCE(si.short_id, '') AS short_id," +
    " r.benchmark," +
    " r.scale_factor," +
    ` CASE WHEN br.phase IS NOT NULL THEN br.phase ELSE ${canonicalPhaseSql("r.test_type")} END AS phase,` +
    " r.platform," +
    " r.platform_id," +
    " r.driver_version," +
    " r.platform_version," +
    " r.run_date," +
    " r.power_score," +
    " r.throughput_at_size," +
    " r.stream_count," +
    " r.total_duration_s," +
    " r.geomean_ms," +
    " r.display_geomean_ms," +
    " r.query_count," +
    " r.logical_query_count," +
    " r.has_display_timing," +
    " r.valid_query_count," +
    " r.missing_query_count," +
    " r.zero_timing_count," +
    " r.display_exclusion_reason," +
    " r.comparison_exclusion_reason," +
    " r.ranking_exclusion_reason," +
    " r.trust_label," +
    " r.funding," +
    " r.validation_status," +
    " r.override_rules," +
    " r.tuning_mode," +
    " r.tuning_validation_status," +
    " r.execution_mode," +
    " r.compliance_class," +
    " r.cost_usd," +
    " r.normalized_cost_usd," +
    " r.cost_model_version," +
    " r.cost_model_source," +
    " r.cost_scope," +
    " r.cost_status," +
    " r.billing_unit," +
    " r.pricing_region," +
    " r.deployment_class," +
    " r.cloud_provider," +
    " r.cloud_region," +
    " r.instance_or_warehouse," +
    " r.storage_format," +
    " e.arch," +
    " e.cpu_family," +
    " e.memory_gb," +
    " CASE WHEN br.primary_metric IS NOT NULL THEN br.primary_metric WHEN r.power_score IS NOT NULL THEN 'power_score'" +
    " WHEN r.throughput_at_size IS NOT NULL THEN 'throughput_at_size' ELSE 'display_geomean_ms' END" +
    " AS primary_metric" +
    " FROM bench.results r" +
    " LEFT JOIN bench.short_ids si ON si.result_id = r.result_id" +
    " LEFT JOIN bench.benchmark_rankings br ON br.result_id = r.result_id" +
    " LEFT JOIN bench.result_environment e ON e.result_id = r.result_id";
  if (platformId === undefined) {
    return queryRows<PlatformIndexRowRow>(`${sql} ORDER BY r.run_date DESC`);
  }
  return queryRows<PlatformIndexRowRow>(`${sql} WHERE r.platform_id = ? ORDER BY r.run_date DESC`, [platformId]);
}

export async function getCohort(cohortKey: string): Promise<CohortMetadataRow[]> {
  return memoizedSnapshotQuery(
    `cohort:${cohortKey}`,
    () =>
      queryRows<CohortMetadataRow>(
        `SELECT ${COHORT_METADATA_COLUMNS} FROM bench.cohort_metadata` +
          " WHERE cohort_key = ?" +
          " ORDER BY rank NULLS LAST, platform_id, result_id",
        [cohortKey],
      ),
  );
}

export async function getMetaLeaderboard(): Promise<MetaLeaderboardRow[]> {
  return memoizedSnapshotQuery(
    "meta-leaderboard",
    () =>
      queryRows<MetaLeaderboardRow>(
        "SELECT platform_id, platform, avg_rank, n_cohorts" +
          " FROM bench.meta_leaderboard" +
          " ORDER BY avg_rank NULLS LAST, platform_id",
      ),
  );
}

export async function getMetaLeaderboardData(): Promise<MetaLeaderboard | null> {
  return memoizedSnapshotQuery("meta-leaderboard-data", loadMetaLeaderboardData);
}

async function loadMetaLeaderboardData(): Promise<MetaLeaderboard | null> {
  const [platformRows, cohortRows] = await Promise.all([
    queryRows<MetaLeaderboardRow>(
      "SELECT platform_id, platform, avg_rank, n_cohorts" +
        " FROM bench.meta_leaderboard" +
        " ORDER BY avg_rank NULLS LAST, platform_id",
    ),
    queryRows<CohortMetadataRow>(
      `SELECT ${COHORT_METADATA_COLUMNS} FROM bench.cohort_metadata` +
        " WHERE platform_count >= 2" +
        " ORDER BY benchmark, scale_factor, phase, rank NULLS LAST, platform_id, result_id",
    ),
  ]);

  if (cohortRows.length === 0) return null;

  const cohortsByKey = new Map<string, MetaCohort>();
  const bestByPair = new Map<string, CohortMetadataRow>();

  for (const row of cohortRows) {
    let cohort = cohortsByKey.get(row.cohort_key);
    if (!cohort) {
      cohort = {
        key: row.cohort_key,
        benchmark: row.benchmark,
        scale_factor: row.scale_factor,
        phase: row.phase,
        stream_count: row.stream_count,
        label: row.cohort_label,
        href: row.cohort_href,
        platform_count: row.platform_count,
        cohort_ranked_count: row.cohort_ranked_count,
        cohort_ranking_exclusion_reason: row.cohort_ranking_exclusion_reason,
        primary_metric: row.primary_metric,
        primary_order: row.primary_order,
        platforms: [],
      };
      cohortsByKey.set(row.cohort_key, cohort);
    }
    cohort.platforms!.push({
      platform_id: row.platform_id,
      platform: row.platform,
      result_id: row.result_id,
      rank: row.rank,
      metric_value: row.metric_value,
      speedup_vs_best: row.speedup_vs_best,
      primary_metric: row.primary_metric,
      primary_order: row.primary_order,
      has_display_timing: row.has_display_timing,
      logical_query_count: row.logical_query_count,
      valid_query_count: row.valid_query_count,
      missing_query_count: row.missing_query_count,
      zero_timing_count: row.zero_timing_count,
      display_exclusion_reason: row.display_exclusion_reason,
      comparison_exclusion_reason: row.comparison_exclusion_reason,
      ranking_exclusion_reason: row.ranking_exclusion_reason,
    });

    if (row.rank === null) continue;
    const pairKey = `${row.platform_id}\u0000${row.cohort_key}`;
    const prev = bestByPair.get(pairKey);
    if (prev === undefined || (prev.rank !== null && row.rank < prev.rank)) {
      bestByPair.set(pairKey, row);
    }
  }

  const ranksByPlatform = new Map<string, Record<string, MetaRank>>();
  for (const [pairKey, row] of bestByPair) {
    const pid = pairKey.split("\u0000")[0]!;
    let ranks = ranksByPlatform.get(pid);
    if (!ranks) {
      ranks = {};
      ranksByPlatform.set(pid, ranks);
    }
    ranks[row.cohort_key] = {
      rank: row.rank!,
      total: row.cohort_ranked_count,
      metric_value: row.metric_value,
      speedup_vs_best: row.speedup_vs_best,
    };
  }

  const platformRowsById = new Map(platformRows.map((row) => [row.platform_id, row]));
  const platformNamesById = new Map(cohortRows.map((row) => [row.platform_id, row.platform]));
  const platformIds = new Set([...platformRowsById.keys(), ...platformNamesById.keys()]);
  const platforms: MetaPlatform[] = [...platformIds].map((platformId) => {
    const row = platformRowsById.get(platformId);
    const ranks = ranksByPlatform.get(platformId) ?? {};
    return {
      platform_id: platformId,
      platform: row?.platform ?? platformNamesById.get(platformId) ?? platformId,
      ranks,
      avg_rank: row?.avg_rank ?? null,
      n_cohorts: row?.n_cohorts ?? Object.keys(ranks).length,
    };
  });

  return {
    generated_at: "",
    cohorts: [...cohortsByKey.values()],
    platforms,
  };
}

export async function getPrimaryMetricForBenchmark(benchmark: string, phase?: string): Promise<PrimaryMetric> {
  const rows = await queryRows<{ phase: string; primary_metric: string }>(
    "SELECT DISTINCT phase, primary_metric FROM bench.benchmark_rankings WHERE benchmark = ? ORDER BY phase, primary_metric",
    [canonicalBenchmarkSlug(benchmark)],
  );
  const match = phase === undefined ? undefined : rows.find((row) => row.phase === phase);
  return normalizePrimaryMetric((match ?? rows[0])?.primary_metric);
}

const SHORT_ID_PATTERN = /^[0-9a-f]{8,}$/i;

export async function getExistingResultIds(
  resultIds: string[],
  onInitialExistingIds?: (existingIds: ReadonlySet<string>) => void,
): Promise<ReadonlySet<string>> {
  if (resultIds.length === 0) return new Set();
  const placeholders = resultIds.map(() => "?").join(", ");
  const batchRows = await queryRows<{ result_id: string }>(
    `SELECT result_id FROM bench.result_detail_metrics WHERE result_id IN (${placeholders})`,
    resultIds,
  );
  const existing = new Set(batchRows.map((row) => row.result_id));
  onInitialExistingIds?.(existing);
  const confirmedRows = await Promise.all(
    resultIds.filter((resultId) => !existing.has(resultId)).map((resultId) =>
      queryRows<{ result_id: string | null }>(
        `SELECT MAX(CASE WHEN result_id = ? THEN result_id END) AS result_id
         FROM bench.result_detail_metrics
         HAVING COUNT(DISTINCT result_id) = (SELECT COUNT(*) FROM bench.results)`,
        [resultId],
      ),
    ),
  );
  for (const rows of confirmedRows) {
    for (const row of rows) {
      if (row.result_id !== null) existing.add(row.result_id);
    }
  }
  return existing;
}

export async function resolveShortId(id: string): Promise<string> {
  if (!SHORT_ID_PATTERN.test(id)) return id;
  const rows = await queryRows<{ result_id: string }>("SELECT result_id FROM bench.short_ids WHERE short_id = ?", [id]);
  return rows[0]?.result_id ?? id;
}

export async function toShortIds(fullIds: string[]): Promise<string[]> {
  if (fullIds.length === 0) return [];
  const placeholders = fullIds.map(() => "?").join(", ");
  const rows = await queryRows<{ short_id: string; result_id: string }>(
    `SELECT short_id, result_id FROM bench.short_ids WHERE result_id IN (${placeholders})`,
    fullIds,
  );
  const byFull = new Map(rows.map((r) => [r.result_id, r.short_id]));
  return fullIds.map((id) => byFull.get(id) ?? id);
}
