import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/db", () => ({
  queryRows: vi.fn(),
}));

import { queryRows } from "@/db";
import {
  clearDuckdbQueryCachesForTests,
  listResults,
  getResultDetailMetrics,
  getQueryDisplayTimings,
  getQueryExecutions,
  getCohortBasisDetails,
  getDetailResult,
  getBenchmarkMatrixCells,
  getBenchmarkRanking,
  getBenchmarkSummaryFromDuckDB,
  getPrimaryMetricForBenchmark,
  getPlatformIndexRows,
  getCohort,
  getMetaLeaderboard,
  getMetaLeaderboardData,
  getExistingResultIds,
  memoizedSnapshotQueryRows,
  resolveShortId,
  toShortIds,
  type ResultDetailMetricsRow,
} from "@/lib/duckdbQueries";
import { buildComparabilityFields } from "@/components/ComparabilityReceipt";

const mockedQueryRows = vi.mocked(queryRows);

beforeEach(() => {
  mockedQueryRows.mockReset();
  clearDuckdbQueryCachesForTests();
});

describe("duckdbQueries - SQL targets and parameters", () => {
  it("listResults selects from bench.results", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await listResults();
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.results/);
    expect(params).toBeUndefined();
  });

  it("listResults accepts a parameterized facet WHERE clause", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await listResults({ sql: "WHERE benchmark IN (?)", params: ["tpch"] });
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toContain("WHERE benchmark IN (?)");
    expect(params).toEqual(["tpch"]);
  });

  it("joins hardware fields onto ranking-eligible result rows", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await listResults({ sql: "WHERE arch IN (?)", params: ["arm64"] });
    const [sql] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toContain("FROM (SELECT r.*, d.arch, d.cpu_family, d.memory_gb FROM bench.results r");
    expect(sql).toContain("LEFT JOIN bench.result_detail_metrics d USING (result_id)");
    expect(sql).toContain("is_ranking_eligible");
  });

  it("joins hardware fields when a raw WHERE clause filters memory", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await listResults({ sql: "WHERE memory_gb >= ?", params: [64] });
    const [sql] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toContain("FROM (SELECT r.*, d.arch, d.cpu_family, d.memory_gb FROM bench.results r");
    expect(sql).toContain("WHERE memory_gb >= ?");
  });

  it("memoizes listResults by WHERE clause and params for the current snapshot", async () => {
    const rows = [{ result_id: "r1" }];
    mockedQueryRows.mockResolvedValueOnce(rows);

    const first = await listResults({ sql: "WHERE benchmark IN (?)", params: ["tpch"] });
    const second = await listResults({ sql: "WHERE benchmark IN (?)", params: ["tpch"] });

    expect(first).toBe(second);
    expect(mockedQueryRows).toHaveBeenCalledTimes(1);

    mockedQueryRows.mockResolvedValueOnce([]);
    await listResults({ sql: "WHERE benchmark IN (?)", params: ["tpcds"] });
    expect(mockedQueryRows).toHaveBeenCalledTimes(2);
  });

  it("does not cache empty listResults responses so cold-load retries can recover", async () => {
    const recoveredRows = [{ result_id: "r1" }];
    mockedQueryRows.mockResolvedValueOnce([]);
    mockedQueryRows.mockResolvedValueOnce(recoveredRows);

    await expect(listResults()).resolves.toEqual([]);
    await expect(listResults()).resolves.toBe(recoveredRows);
    expect(mockedQueryRows).toHaveBeenCalledTimes(2);
  });

  it("memoizes ad hoc snapshot row queries by caller key, SQL, and params", async () => {
    const rows = [{ value: "tpch", count: 2 }];
    const query = {
      sql: "SELECT benchmark AS value, COUNT(*) AS count FROM bench.results WHERE platform = ? GROUP BY 1",
      params: ["duckdb"],
    };
    mockedQueryRows.mockResolvedValueOnce(rows);

    const first = await memoizedSnapshotQueryRows("query-facet:benchmark", query);
    const second = await memoizedSnapshotQueryRows("query-facet:benchmark", query);

    expect(first).toBe(second);
    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
  });

  it("getResultDetailMetrics returns the single matching row or null", async () => {
    mockedQueryRows.mockResolvedValueOnce([{ result_id: "r1" }]);
    const row = await getResultDetailMetrics("r1");
    expect(row).toEqual({ result_id: "r1" });

    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.result_detail_metrics/);
    expect(sql).toMatch(/result_id = \?/);
    expect(params).toEqual(["r1"]);

    mockedQueryRows.mockResolvedValueOnce([]);
    const missing = await getResultDetailMetrics("nope");
    expect(missing).toBeNull();
  });

  it("getQueryDisplayTimings orders by query_id", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await getQueryDisplayTimings("r1");
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.query_display_timings/);
    expect(sql).toMatch(/ORDER BY query_id/);
    expect(params).toEqual(["r1"]);
  });

  it("getQueryExecutions preserves null-as-zero stream and iteration ordering without COALESCE", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await getQueryExecutions("r1");
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.query_executions/);
    expect(sql).toMatch(/CASE WHEN stream IS NULL THEN 0 ELSE stream END/);
    expect(sql).toMatch(/CASE WHEN iter IS NULL THEN 0 ELSE iter END/);
    expect(sql).not.toMatch(/COALESCE/);
    expect(params).toEqual(["r1"]);
  });

  it("getQueryDisplayTimings accepts an array of result IDs and queries them in one IN clause", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    const emptyResult = await getQueryDisplayTimings([]);
    expect(emptyResult).toEqual([]);
    expect(mockedQueryRows).not.toHaveBeenCalled();

    await getQueryDisplayTimings(["r1", "r2"]);
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.query_display_timings/);
    expect(sql).toContain("WHERE result_id IN (?,?)");
    expect(sql).toMatch(/ORDER BY query_id/);
    expect(params).toEqual(["r1", "r2"]);
  });

  it("getQueryExecutions accepts an array of result IDs and queries them in one IN clause", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    const emptyResult = await getQueryExecutions([]);
    expect(emptyResult).toEqual([]);
    expect(mockedQueryRows).not.toHaveBeenCalled();

    await getQueryExecutions(["r1", "r2"]);
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.query_executions/);
    expect(sql).toContain("WHERE result_id IN (?,?)");
    expect(sql).toMatch(/CASE WHEN stream IS NULL THEN 0 ELSE stream END/);
    expect(params).toEqual(["r1", "r2"]);
  });

  it("getQueryExecutions limits selected runs to one query when requested", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await getQueryExecutions(["r1", "r2"], "Q1");
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toContain("WHERE result_id IN (?,?) AND query_id = ?");
    expect(params).toEqual(["r1", "r2", "Q1"]);
  });

  describe("getCohortBasisDetails - bulk cohort executions accessor", () => {
    it("returns empty map immediately without querying when given empty IDs", async () => {
      const result = await getCohortBasisDetails([]);
      expect(result.size).toBe(0);
      expect(mockedQueryRows).not.toHaveBeenCalled();
    });

    function makeCohortWideRow(overrides: Partial<ResultDetailMetricsRow>): ResultDetailMetricsRow {
      return {
        result_id: "r1",
        benchmark: "tpch",
        scale_factor: 0.1,
        platform: "DuckDB",
        platform_id: "duckdb",
        driver_version: null,
        run_date: "2026-04-01",
        power_score: null,
        total_duration_s: 60,
        geomean_ms: 10,
        display_geomean_ms: 10,
        query_count: 2,
        has_display_timing: true,
        valid_query_count: 2,
        missing_query_count: 0,
        zero_timing_count: 0,
        display_exclusion_reason: null,
        comparison_exclusion_reason: null,
        ranking_exclusion_reason: null,
        trust_label: "maintainer-run",
        funding: "unspecified",
        visibility: "public-curated",
        platform_version: null,
        execution_mode: "sql",
        tuning_mode: "tuned",
        tuning_hash: null,
        test_type: "power",
        validation_status: "exact",
        cost_usd: null,
        compliance_class: null,
        has_plans: false,
        plans_published: false,
        has_tuning: true,
        bundle_download_url: "",
        os: null,
        arch: null,
        cpu_count: null,
        memory_gb: null,
        python: null,
        cpu_model: null,
        cpu_family: null,
        cpu_identity_provenance: null,
        client_region: null,
        client_cloud: null,
        statement_overhead_min_ms: null,
        statement_overhead_median_ms: null,
        link_status: null,
        physical_mechanisms: null,
        ...overrides,
      };
    }

    it("fetches wide rows, display timings, and executions for all cohort IDs in one query each and groups them into DetailResult objects", async () => {
      mockedQueryRows.mockResolvedValueOnce([
        makeCohortWideRow({ result_id: "r1" }),
        makeCohortWideRow({
          result_id: "r2",
          comparison_exclusion_reason: "hidden_result",
          ranking_exclusion_reason: "hidden_result",
          visibility: "hidden",
        }),
      ]);
      mockedQueryRows.mockResolvedValueOnce([
        { result_id: "r1", query_id: "Q1", display_ms: 9, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      ]);
      mockedQueryRows.mockResolvedValueOnce([
        { result_id: "r1", query_id: "Q1", duration_ms: 10, status: "pass", run_type: "measurement", iter: 1, stream: null },
        { result_id: "r1", query_id: "Q2", duration_ms: 20, status: "pass", run_type: "measurement", iter: 1, stream: null },
        { result_id: "r2", query_id: "Q1", duration_ms: 50, status: "pass", run_type: "measurement", iter: 1, stream: null },
      ]);

      const map = await getCohortBasisDetails(["r1", "r2"]);
      expect(mockedQueryRows).toHaveBeenCalledTimes(3);
      const [wideSql, wideParams] = mockedQueryRows.mock.calls[0]!;
      expect(wideSql).toContain("FROM bench.result_detail_metrics");
      expect(wideSql).toContain("WHERE result_id IN (?,?)");
      expect(wideParams).toEqual(["r1", "r2"]);

      expect(map.size).toBe(2);
      const r1 = map.get("r1");
      const r2 = map.get("r2");
      expect(r1).toBeDefined();
      expect(r2).toBeDefined();
      expect(r1?.result_id).toBe("r1");
      expect(r1?.queries).toEqual([
        { query_id: "Q1", duration_ms: 10, status: "pass", run_type: "measurement", iter: 1, stream: null },
        { query_id: "Q2", duration_ms: 20, status: "pass", run_type: "measurement", iter: 1, stream: null },
      ]);
      expect(r1?.display_timings).toEqual([
        { query_id: "Q1", display_ms: 9, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      ]);
      expect(r1?.comparison_exclusion_reason).toBeNull();
      expect(r1?.trust_label).toBe("maintainer-run");

      expect(r2?.result_id).toBe("r2");
      expect(r2?.queries).toEqual([
        { query_id: "Q1", duration_ms: 50, status: "pass", run_type: "measurement", iter: 1, stream: null },
      ]);
      expect(r2?.comparison_exclusion_reason).toBe("hidden_result");
      expect(r2?.ranking_exclusion_reason).toBe("hidden_result");
      expect(r2?.visibility).toBe("hidden");
    });
  });

  describe("getDetailResult - physical_mechanisms unknown vs recorded-empty (ADR-2 §3)", () => {
    function makeWideRow(overrides: Partial<ResultDetailMetricsRow>): ResultDetailMetricsRow {
      return {
        result_id: "r1",
        benchmark: "tpch",
        scale_factor: 0.1,
        platform: "DuckDB",
        platform_id: "duckdb",
        driver_version: null,
        run_date: "2026-04-01",
        power_score: null,
        total_duration_s: 60,
        geomean_ms: 10,
        display_geomean_ms: 10,
        query_count: 2,
        has_display_timing: true,
        valid_query_count: 2,
        missing_query_count: 0,
        zero_timing_count: 0,
        display_exclusion_reason: null,
        comparison_exclusion_reason: null,
        ranking_exclusion_reason: null,
        trust_label: "maintainer-run",
        funding: "unspecified",
        visibility: "public-curated",
        platform_version: null,
        execution_mode: "sql",
        tuning_mode: "tuned",
        tuning_hash: null,
        test_type: "power",
        validation_status: "exact",
        cost_usd: null,
        compliance_class: null,
        has_plans: false,
        plans_published: false,
        has_tuning: true,
        bundle_download_url: "",
        os: null,
        arch: null,
        cpu_count: null,
        memory_gb: null,
        python: null,
        cpu_model: null,
        cpu_family: null,
        cpu_identity_provenance: null,
        client_region: null,
        client_cloud: null,
        statement_overhead_min_ms: null,
        statement_overhead_median_ms: null,
        link_status: null,
        physical_mechanisms: null,
        ...overrides,
      };
    }

    async function fetchDetail(resultId: string, wideRow: ResultDetailMetricsRow) {
      mockedQueryRows.mockResolvedValueOnce([wideRow]);
      mockedQueryRows.mockResolvedValueOnce([]);
      mockedQueryRows.mockResolvedValueOnce([]);
      return getDetailResult(resultId);
    }

    it("carries CPU identity from the wide row through to the environment", async () => {
      const detail = await fetchDetail(
        "with-cpu",
        makeWideRow({
          result_id: "with-cpu",
          cpu_model: "Apple M1 Max",
          cpu_family: "apple_silicon",
          cpu_identity_provenance: "measured",
        }),
      );
      expect(detail?.environment.cpu_model).toBe("Apple M1 Max");
      expect(detail?.environment.cpu_family).toBe("apple_silicon");
      expect(detail?.environment.cpu_identity_provenance).toBe("measured");
    });

    it("omits CPU identity when the snapshot recorded none", async () => {
      const detail = await fetchDetail("no-cpu", makeWideRow({ result_id: "no-cpu" }));
      expect(detail?.environment.cpu_model).toBeUndefined();
      expect(detail?.environment.cpu_family).toBeUndefined();
    });

    it("selects the CPU columns it reads", async () => {
      await fetchDetail("sql-check", makeWideRow({ result_id: "sql-check" }));
      const detailSql = String(mockedQueryRows.mock.calls[0]?.[0] ?? "");
      expect(detailSql).toContain("result_detail_metrics");
      expect(detailSql).toContain("cpu_model");
      expect(detailSql).toContain("cpu_family");
      expect(detailSql).toContain("cpu_identity_provenance");
      expect(detailSql).toContain("client_region");
      expect(detailSql).toContain("client_cloud");
      expect(detailSql).toContain("statement_overhead_min_ms");
      expect(detailSql).toContain("statement_overhead_median_ms");
      expect(detailSql).toContain("link_status");
    });

    it("preserves client locality and overhead fields when present", async () => {
      const detail = await fetchDetail(
        "with-locality",
        makeWideRow({
          result_id: "with-locality",
          client_region: "us-east-1",
          client_cloud: "aws",
          statement_overhead_min_ms: 1.25,
          statement_overhead_median_ms: 2.5,
          link_status: "measured",
        }),
      );
      expect(detail?.environment.client_region).toBe("us-east-1");
      expect(detail?.environment.client_cloud).toBe("aws");
      expect(detail?.environment.statement_overhead_min_ms).toBe(1.25);
      expect(detail?.environment.statement_overhead_median_ms).toBe(2.5);
      expect(detail?.environment.link_status).toBe("measured");

      expect(detail?.client_region).toBe("us-east-1");
      expect(detail?.client_cloud).toBe("aws");
      expect(detail?.statement_overhead_min_ms).toBe(1.25);
      expect(detail?.statement_overhead_median_ms).toBe(2.5);
      expect(detail?.link_status).toBe("measured");
    });

    it("a legacy row (no logical_profile recorded -> NULL) yields undefined, not []", async () => {
      const legacy = await fetchDetail("legacy", makeWideRow({ result_id: "legacy", physical_mechanisms: null }));
      expect(legacy?.physical_mechanisms).toBeUndefined();
    });

    it("a modern row with a recorded-but-empty mechanism list ('') yields [], not undefined", async () => {
      const modernEmpty = await fetchDetail(
        "modern-empty",
        makeWideRow({ result_id: "modern-empty", physical_mechanisms: "" }),
      );
      expect(modernEmpty?.physical_mechanisms).toEqual([]);
    });

    it("production path: a legacy result paired with a modern tuned result skips the mechanism-warning field (unknown, not a false mismatch)", async () => {
      const legacy = await fetchDetail("legacy", makeWideRow({ result_id: "legacy", physical_mechanisms: null }));
      const modern = await fetchDetail(
        "modern",
        makeWideRow({ result_id: "modern", physical_mechanisms: "indexes,clustering" }),
      );
      expect(legacy).not.toBeNull();
      expect(modern).not.toBeNull();

      const fields = buildComparabilityFields([legacy!, modern!]);
      expect(fields.find((f) => f.label === "Physical tuning mechanisms")).toBeUndefined();
    });

    it("production path: two modern tuned results with recorded-empty mechanisms participate in comparison as a match", async () => {
      const a = await fetchDetail("a", makeWideRow({ result_id: "a", physical_mechanisms: "" }));
      const b = await fetchDetail(
        "b",
        makeWideRow({ result_id: "b", platform: "SQLite", physical_mechanisms: "" }),
      );
      expect(a).not.toBeNull();
      expect(b).not.toBeNull();

      const fields = buildComparabilityFields([a!, b!]);
      expect(fields.find((f) => f.label === "Applied tuning features")).toMatchObject({
        status: "match",
        summary: "None applied",
      });
    });
  });

  it("getBenchmarkMatrixCells scopes by (benchmark, scale_factor, phase)", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await getBenchmarkMatrixCells("tpch", 0.1, "power");
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.benchmark_matrix_cells/);
    expect(params).toEqual(["tpch", 0.1, "power"]);
  });

  it("getBenchmarkRanking sorts by rank NULLS LAST", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await getBenchmarkRanking("tpch", 0.1, "power");
    const [sql] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.benchmark_rankings/);
    expect(sql).toMatch(/LEFT JOIN bench\.results/);
    expect(sql).toMatch(/normalized_cost_usd/);
    expect(sql).toMatch(/cost_model_version/);
    expect(sql).toMatch(/deployment_class/);
    expect(sql).toMatch(/instance_or_warehouse/);
    expect(sql).toMatch(/ORDER BY br\.rank NULLS LAST/);
  });

  it("getBenchmarkSummaryFromDuckDB preserves per-cell timing eligibility", async () => {
    mockedQueryRows
      .mockResolvedValueOnce([
        {
          benchmark: "tpch",
          scale_factor: 0.1,
          phase: "power",
          result_id: "r1",
          platform_id: "duckdb",
          platform: "DuckDB",
          short_id: "r1",
          trust_label: "maintainer-run",
          run_date: "2026-04-01",
          is_ranking_eligible: true,
          has_display_timing: true,
          valid_query_count: 1,
          missing_query_count: 0,
          zero_timing_count: 1,
          display_exclusion_reason: null,
          comparison_exclusion_reason: "insufficient_valid_timings",
          ranking_exclusion_reason: "insufficient_valid_timings",
          power_score: null,
          display_geomean_ms: 10,
          sample_geomean_ms: 10,
          cost_usd: null,
          compliance_class: null,
          primary_metric: "display_geomean_ms",
          primary_order: "asc",
          percentile_p50: null,
          percentile_p90: null,
          percentile_p95: null,
          percentile_p99: null,
        },
      ])
      .mockResolvedValueOnce([
        {
          benchmark: "tpch",
          scale_factor: 0.1,
          phase: "power",
          result_id: "r1",
          platform_id: "duckdb",
          query_id: "Q1",
          display_ms: 10,
          is_valid_display_timing: true,
          timing_exclusion_reason: null,
        },
        {
          benchmark: "tpch",
          scale_factor: 0.1,
          phase: "power",
          result_id: "r1",
          platform_id: "duckdb",
          query_id: "Q2",
          display_ms: 0,
          is_valid_display_timing: false,
          timing_exclusion_reason: "zero_timing",
        },
      ])
      .mockResolvedValueOnce([]);

    const summary = await getBenchmarkSummaryFromDuckDB("tpch", 0.1, "power");

    expect(summary?.platforms[0]?.timings).toEqual({ Q1: 10, Q2: 0 });
    expect(summary?.platforms[0]?.timing_eligibility).toEqual({
      Q1: { is_valid_display_timing: true, timing_exclusion_reason: null },
      Q2: { is_valid_display_timing: false, timing_exclusion_reason: "zero_timing" },
    });
  });

  it("getPlatformIndexRows omits the filter when platformId is undefined", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await getPlatformIndexRows();
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.results r/);
    expect(sql).toMatch(/COALESCE\(si\.short_id, ''\) AS short_id/);
    expect(sql).toMatch(/LEFT JOIN bench\.short_ids si ON si\.result_id = r\.result_id/);
    expect(sql).toMatch(/LEFT JOIN bench\.benchmark_rankings br ON br\.result_id = r\.result_id/);
    expect(sql).toMatch(/CASE WHEN br\.phase IS NOT NULL THEN br\.phase/);
    expect(sql).toMatch(/br\.primary_metric/);
    expect(sql).toMatch(/validation_status/);
    expect(sql).toMatch(/normalized_cost_usd/);
    expect(sql).toMatch(/deployment_class/);
    expect(sql).toMatch(/cloud_provider/);
    expect(sql).toMatch(/instance_or_warehouse/);
    expect(sql).toMatch(/storage_format/);
    expect(sql.match(/COALESCE\(/g)).toHaveLength(1);
    expect(sql).not.toMatch(/WHERE/);
    expect(params).toBeUndefined();
  });

  it("getPlatformIndexRows filters by platform_id when provided", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await getPlatformIndexRows("duckdb");
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/WHERE r\.platform_id = \?/);
    expect(params).toEqual(["duckdb"]);
  });

  it("does not cache empty platform index rows so cold-load retries can recover", async () => {
    const recoveredRows = [{ result_id: "r1" }];
    mockedQueryRows.mockResolvedValueOnce([]);
    mockedQueryRows.mockResolvedValueOnce(recoveredRows);

    await expect(getPlatformIndexRows()).resolves.toEqual([]);
    await expect(getPlatformIndexRows()).resolves.toBe(recoveredRows);
    expect(mockedQueryRows).toHaveBeenCalledTimes(2);
  });

  it("getCohort selects all variants for a cohort_key", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await getCohort("tpch-sf0.1-power");
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.cohort_metadata/);
    expect(sql).toMatch(/WHERE cohort_key = \?/);
    expect(params).toEqual(["tpch-sf0.1-power"]);
  });

  it("getMetaLeaderboard orders by avg_rank NULLS LAST", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await getMetaLeaderboard();
    const [sql] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.meta_leaderboard/);
    expect(sql).toMatch(/ORDER BY avg_rank NULLS LAST/);
  });

  it("memoizes stable metadata helper reads for the current snapshot", async () => {
    const rows = [{ platform_id: "duckdb", platform: "DuckDB", avg_rank: 1, n_cohorts: 2 }];
    mockedQueryRows.mockResolvedValueOnce(rows);

    const first = await getMetaLeaderboard();
    const second = await getMetaLeaderboard();

    expect(first).toBe(second);
    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
  });

  it("evicts failed metadata helper reads so a retry can recover", async () => {
    mockedQueryRows.mockRejectedValueOnce(new Error("snapshot not ready"));
    await expect(getMetaLeaderboard()).rejects.toThrow("snapshot not ready");

    const rows = [{ platform_id: "duckdb", platform: "DuckDB", avg_rank: 1, n_cohorts: 2 }];
    mockedQueryRows.mockResolvedValueOnce(rows);

    await expect(getMetaLeaderboard()).resolves.toEqual(rows);
    expect(mockedQueryRows).toHaveBeenCalledTimes(2);
  });
});

describe("getMetaLeaderboardData", () => {
  function makeCohortRow(overrides: Partial<Record<string, unknown>>) {
    return {
      cohort_key: "clickbench-sf0.1-power",
      benchmark: "clickbench",
      scale_factor: 0.1,
      phase: "power",
      cohort_label: "ClickBench SF0.1",
      cohort_href: "/results/clickbench/",
      platform_count: 2,
      primary_metric: "display_geomean_ms",
      primary_order: "asc",
      platform_id: "duckdb",
      platform: "DuckDB",
      result_id: "r1",
      short_id: "",
      tuning_mode: "tuned",
      trust_label: "maintainer-run",
      rank: 1,
      metric_value: 10,
      speedup_vs_best: 1,
      ...overrides,
    };
  }

  it("returns null when cohort_metadata is empty", async () => {
    mockedQueryRows
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    await expect(getMetaLeaderboardData()).resolves.toBeNull();
  });

  it("pivots cohort rows into cohorts[] and platforms[].ranks by platform_id", async () => {
    mockedQueryRows
      .mockResolvedValueOnce([
        { platform_id: "duckdb", platform: "DuckDB", avg_rank: 1, n_cohorts: 2 },
        { platform_id: "sqlite", platform: "SQLite", avg_rank: 2, n_cohorts: 1 },
      ])
      .mockResolvedValueOnce([
        makeCohortRow({ platform_id: "duckdb", result_id: "r1", rank: 1 }),
        makeCohortRow({
          platform_id: "sqlite",
          platform: "SQLite",
          result_id: "r2",
          rank: 2,
          metric_value: 20,
          speedup_vs_best: 0.5,
          tuning_mode: "auto",
          trust_label: "community-submission",
        }),
        makeCohortRow({
          cohort_key: "tpch-sf1-power",
          benchmark: "tpch",
          scale_factor: 1,
          phase: "power",
          cohort_label: "TPC-H SF1",
          cohort_href: "/results/tpch/",
          platform_count: 1,
          primary_metric: "power_score",
          primary_order: "desc",
          platform_id: "duckdb",
          result_id: "r3",
          rank: 1,
          metric_value: 3000,
          speedup_vs_best: 1,
        }),
      ]);

    const data = await getMetaLeaderboardData();
    expect(data).not.toBeNull();
    expect(data!.cohorts).toHaveLength(2);
    const clickbench = data!.cohorts.find((c) => c.key === "clickbench-sf0.1-power")!;
    expect(clickbench.label).toBe("ClickBench SF0.1");
    expect(clickbench.platforms).toHaveLength(2);

    const duckdb = data!.platforms.find((p) => p.platform_id === "duckdb")!;
    expect(duckdb.ranks["clickbench-sf0.1-power"]?.rank).toBe(1);
    expect(duckdb.ranks["tpch-sf1-power"]?.rank).toBe(1);
    expect(duckdb.avg_rank).toBe(1);
    expect(duckdb.n_cohorts).toBe(2);

    const sqlite = data!.platforms.find((p) => p.platform_id === "sqlite")!;
    expect(sqlite.ranks["clickbench-sf0.1-power"]?.rank).toBe(2);
    expect(sqlite.ranks["tpch-sf1-power"]).toBeUndefined();
  });

  it("keeps the best (lowest) rank when a platform has multiple variants in one cohort", async () => {
    mockedQueryRows
      .mockResolvedValueOnce([{ platform_id: "duckdb", platform: "DuckDB", avg_rank: 1, n_cohorts: 1 }])
      .mockResolvedValueOnce([
        makeCohortRow({ result_id: "r-tuned", rank: 1, tuning_mode: "tuned" }),
        makeCohortRow({ result_id: "r-auto", rank: 3, tuning_mode: "auto" }),
      ]);

    const data = await getMetaLeaderboardData();
    const duckdb = data!.platforms.find((p) => p.platform_id === "duckdb")!;
    expect(duckdb.ranks["clickbench-sf0.1-power"]?.rank).toBe(1);
    expect(data!.cohorts[0]!.platforms).toHaveLength(2);
  });

  it("skips platforms with null rank when building the ranks map", async () => {
    mockedQueryRows
      .mockResolvedValueOnce([{ platform_id: "duckdb", platform: "DuckDB", avg_rank: null, n_cohorts: 0 }])
      .mockResolvedValueOnce([makeCohortRow({ rank: null, metric_value: null, speedup_vs_best: null })]);

    const data = await getMetaLeaderboardData();
    const duckdb = data!.platforms.find((p) => p.platform_id === "duckdb")!;
    expect(duckdb.ranks).toEqual({});
  });
});

describe("resolveShortId", () => {
  const noSuffixMatch = { short_match: null, suffix_count: 0, suffix_match: null };

  it("reports omitted IDs as absent from one snapshot-guarded query", async () => {
    const onInitialExistingIds = vi.fn();
    mockedQueryRows.mockResolvedValueOnce([
      { result_id: "known-a", found: true },
      { result_id: "missing-b", found: false },
    ]);

    await expect(
      getExistingResultIds(["known-a", "missing-b"], onInitialExistingIds),
    ).resolves.toEqual(new Set(["known-a"]));
    expect(onInitialExistingIds).toHaveBeenCalledWith(new Set(["known-a"]));
    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/VALUES \(\?\), \(\?\)/);
    expect(sql).toMatch(/WHERE snapshot\.results_count > 0 AND snapshot\.metrics_count = snapshot\.results_count/);
    expect(sql).toMatch(/FROM bench\.result_detail_metrics m WHERE m\.result_id = requested\.result_id/);
    expect(params).toEqual(["known-a", "missing-b"]);
  });

  it("returns only the requested IDs that exist when several are missing", async () => {
    mockedQueryRows.mockResolvedValueOnce([
      { result_id: "tpch-snowflake-sf10.0-20261003-f0835929", found: true },
      { result_id: "deadbeef", found: false },
      { result_id: "f0835930", found: false },
    ]);
    await expect(
      getExistingResultIds(["tpch-snowflake-sf10.0-20261003-f0835929", "deadbeef", "f0835930"]),
    ).resolves.toEqual(new Set(["tpch-snowflake-sf10.0-20261003-f0835929"]));
    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
  });

  it("returns an empty set when the snapshot guard yields no row (cold read)", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await expect(getExistingResultIds(["known-a"])).resolves.toEqual(new Set());
  });

  it("does not query existing detail IDs for an empty input", async () => {
    await expect(getExistingResultIds([])).resolves.toEqual(new Set());
    expect(mockedQueryRows).not.toHaveBeenCalled();
  });

  it("returns the input unchanged when it is not a short-id-shaped string", async () => {
    const fullId = "tpch-duckdb-abcdef12";
    await expect(resolveShortId(fullId)).resolves.toBe(fullId);
    expect(mockedQueryRows).not.toHaveBeenCalled();
  });

  it("guards the short-id lookup on both snapshot counts so lagging detail metrics read as cold", async () => {
    mockedQueryRows.mockResolvedValueOnce([{ short_match: "full-result-id", suffix_count: 0, suffix_match: null }]);
    await resolveShortId("abcdef12");
    const [sql] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.result_detail_metrics/);
    expect(sql).toMatch(/metrics_count = snapshot\.results_count/);
  });

  it("resolves a short id from short_ids in one query", async () => {
    mockedQueryRows.mockResolvedValueOnce([
      { short_match: "full-result-id", suffix_count: 0, suffix_match: null },
    ]);
    const out = await resolveShortId("abcdef12");
    expect(out).toBe("full-result-id");
    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.short_ids WHERE short_id = \?/);
    expect(params).toEqual(["%-abcdef12", "abcdef12"]);
  });

  it.each(["1' OR '1'='1", "%", "f08_5929", "F0835929; DROP", " "])(
    "passes a non-token id %j through unchanged without querying",
    async (raw) => {
      await expect(resolveShortId(raw)).resolves.toBe(raw);
      expect(mockedQueryRows).not.toHaveBeenCalled();
    },
  );

  it("returns the input unchanged and in one query when no match is found", async () => {
    mockedQueryRows.mockResolvedValueOnce([noSuffixMatch]);
    await expect(resolveShortId("deadbeef")).resolves.toBe("deadbeef");
    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
  });

  it("returns the input unchanged when the snapshot guard yields no row (cold read)", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await expect(resolveShortId("deadbeef")).resolves.toBe("deadbeef");
  });

  it("resolves a public run token to the full result_id that ends with it", async () => {
    mockedQueryRows.mockResolvedValueOnce([
      { short_match: null, suffix_count: 1, suffix_match: "tpch-snowflake-sf10.0-20261003-f0835929" },
    ]);
    await expect(resolveShortId("f0835929")).resolves.toBe("tpch-snowflake-sf10.0-20261003-f0835929");
    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/FROM bench\.results WHERE result_id LIKE \?/);
    expect(params).toEqual(["%-f0835929", "f0835929"]);
  });

  it("returns the input unchanged when a public token matches more than one result_id", async () => {
    mockedQueryRows.mockResolvedValueOnce([
      { short_match: null, suffix_count: 2, suffix_match: "tpch-snowflake-sf10.0-20261003-f0835929" },
    ]);
    await expect(resolveShortId("f0835929")).resolves.toBe("f0835929");
    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
  });

  it("prefers a short_ids match over a public suffix match", async () => {
    mockedQueryRows.mockResolvedValueOnce([
      { short_match: "tpch-snowflake-sf10.0-20261003-aaaa1111", suffix_count: 1, suffix_match: "other-run-aaaa1111" },
    ]);
    await expect(resolveShortId("aaaa1111")).resolves.toBe("tpch-snowflake-sf10.0-20261003-aaaa1111");
  });

  it("passes a full result_id through without querying", async () => {
    const fullId = "tpch-snowflake-sf10.0-20261003-f0835929";
    await expect(resolveShortId(fullId)).resolves.toBe(fullId);
    expect(mockedQueryRows).not.toHaveBeenCalled();
  });
});

describe("toShortIds", () => {
  it("returns [] immediately for an empty input without issuing a query", async () => {
    await expect(toShortIds([])).resolves.toEqual([]);
    expect(mockedQueryRows).not.toHaveBeenCalled();
  });

  it("maps each full id to its short id, falling back to the full id on misses", async () => {
    mockedQueryRows.mockResolvedValueOnce([
      { result_id: "id-a", short_id: "aaaaaaaa" },
      { result_id: "id-b", short_id: null },
      { result_id: "id-c", short_id: "cccccccc" },
    ]);
    const out = await toShortIds(["id-a", "id-b", "id-c"]);
    expect(out).toEqual(["aaaaaaaa", "id-b", "cccccccc"]);
    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
    const [sql, params] = mockedQueryRows.mock.calls[0]!;
    expect(sql).toMatch(/VALUES \(\?\), \(\?\), \(\?\)/);
    expect(sql).toMatch(/WHERE snapshot\.results_count > 0 AND snapshot\.metrics_count = snapshot\.results_count/);
    expect(params).toEqual(["id-a", "id-b", "id-c"]);
  });

  it("falls back to the full ids when the snapshot guard yields no row", async () => {
    mockedQueryRows.mockResolvedValueOnce([]);
    await expect(toShortIds(["id-a", "id-b"])).resolves.toEqual(["id-a", "id-b"]);
  });
});

describe("throughput cohorts split by stream count", () => {
  it("scopes matrix cells and rankings by stream_count when one is given", async () => {
    mockedQueryRows.mockResolvedValueOnce([]).mockResolvedValueOnce([]);

    await getBenchmarkMatrixCells("tpch", 1, "throughput", 3);
    await getBenchmarkRanking("tpch", 1, "throughput", 3);

    const [cellsSql, cellsParams] = mockedQueryRows.mock.calls[0]!;
    expect(cellsSql).toContain("AND stream_count = ?");
    expect(cellsParams).toEqual(["tpch", 1, "throughput", 3]);
    const [rankingSql, rankingParams] = mockedQueryRows.mock.calls[1]!;
    expect(rankingSql).toContain("AND br.stream_count = ?");
    expect(rankingSql).toMatch(/br\.throughput_at_size/);
    expect(rankingParams).toEqual(["tpch", 1, "throughput", 3]);
  });

  it("matches only rows without a stream_count when none is given", async () => {
    mockedQueryRows.mockResolvedValueOnce([]).mockResolvedValueOnce([]);

    await getBenchmarkMatrixCells("tpch", 1, "power");
    await getBenchmarkRanking("tpch", 1, "power");

    expect(mockedQueryRows.mock.calls[0]![0]).toContain("AND stream_count IS NULL");
    expect(mockedQueryRows.mock.calls[1]![0]).toContain("AND br.stream_count IS NULL");
  });

  it("keeps separate cached summaries per stream count", async () => {
    mockedQueryRows.mockResolvedValue([]);

    await getBenchmarkSummaryFromDuckDB("tpch", 1, "throughput", 2);
    await getBenchmarkSummaryFromDuckDB("tpch", 1, "throughput", 3);
    await getBenchmarkSummaryFromDuckDB("tpch", 1, "throughput", 3);

    const rankingCalls = mockedQueryRows.mock.calls.filter(([sql]) => String(sql).includes("benchmark_rankings"));
    expect(rankingCalls.map(([, params]) => params)).toEqual([
      ["tpch", 1, "throughput", 2],
      ["tpch", 1, "throughput", 3],
    ]);
  });

  it("builds a throughput summary ranked on Throughput@Size", async () => {
    mockedQueryRows
      .mockResolvedValueOnce([
        {
          benchmark: "tpch",
          scale_factor: 1,
          phase: "throughput",
          result_id: "r1",
          platform_id: "spark",
          platform: "Spark",
          short_id: "r1",
          trust_label: "maintainer-run",
          run_date: "2026-10-03",
          is_ranking_eligible: true,
          has_display_timing: true,
          valid_query_count: 22,
          missing_query_count: 0,
          zero_timing_count: 0,
          display_exclusion_reason: null,
          comparison_exclusion_reason: null,
          ranking_exclusion_reason: null,
          power_score: null,
          throughput_at_size: 3741.26,
          stream_count: 3,
          display_geomean_ms: 2509.5,
          sample_geomean_ms: 2509.5,
          cost_usd: null,
          compliance_class: "official",
          primary_metric: "throughput_at_size",
          primary_order: "desc",
          rank: 1,
          percentile_p50: null,
          percentile_p90: null,
          percentile_p95: null,
          percentile_p99: null,
        },
      ])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);

    const summary = await getBenchmarkSummaryFromDuckDB("tpch", 1, "throughput", 3);

    expect(summary?.stream_count).toBe(3);
    expect(summary?.ranking).toEqual({
      primary_metric: "throughput_at_size",
      secondary_metric: "display_geomean_ms",
      primary_order: "desc",
    });
    expect(summary?.platforms[0]?.throughput_at_size).toBe(3741.26);
  });

  it("reads the primary metric for a benchmark phase in a single query on the canonical slug", async () => {
    mockedQueryRows.mockResolvedValueOnce([
      { phase: "power", primary_metric: "power_score" },
      { phase: "throughput", primary_metric: "throughput_at_size" },
    ]);

    await expect(getPrimaryMetricForBenchmark("tpch", "throughput")).resolves.toBe("throughput_at_size");

    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
    expect(mockedQueryRows.mock.calls[0]![0]).toContain("FROM bench.benchmark_rankings WHERE benchmark = ?");
    expect(mockedQueryRows.mock.calls[0]![1]).toEqual(["tpch"]);
  });

  it("looks up star_schema results under the canonical ssb ranking rows without a second query", async () => {
    mockedQueryRows.mockResolvedValueOnce([{ phase: "power", primary_metric: "display_geomean_ms" }]);

    await expect(getPrimaryMetricForBenchmark("star_schema", "power")).resolves.toBe("display_geomean_ms");

    expect(mockedQueryRows).toHaveBeenCalledTimes(1);
    expect(mockedQueryRows.mock.calls[0]![1]).toEqual(["ssb"]);
  });

  it("falls back to the benchmark's first ranking row when the phase has none, and to geomean when it has no rows", async () => {
    mockedQueryRows.mockResolvedValueOnce([{ phase: "power", primary_metric: "power_score" }]);
    await expect(getPrimaryMetricForBenchmark("tpch", "unknown")).resolves.toBe("power_score");

    mockedQueryRows.mockResolvedValueOnce([{ phase: "power", primary_metric: "power_score" }]);
    await expect(getPrimaryMetricForBenchmark("tpch")).resolves.toBe("power_score");

    mockedQueryRows.mockResolvedValueOnce([]);
    await expect(getPrimaryMetricForBenchmark("tpch", "power")).resolves.toBe("display_geomean_ms");
    expect(mockedQueryRows).toHaveBeenCalledTimes(3);
  });

  it("carries stream_count from cohort_metadata onto the cohort", async () => {
    mockedQueryRows.mockResolvedValueOnce([]).mockResolvedValueOnce([
      {
        cohort_key: "tpch-sf1-throughput-3streams",
        benchmark: "tpch",
        scale_factor: 1,
        phase: "throughput",
        stream_count: 3,
        cohort_label: "TPC-H SF1 Throughput (3 streams)",
        cohort_href: "/results/tpch/?sf=1&phase=throughput&streams=3",
        platform_count: 3,
        cohort_ranked_count: 3,
        cohort_ranking_exclusion_reason: null,
        primary_metric: "throughput_at_size",
        primary_order: "desc",
        platform_id: "spark",
        platform: "Spark",
        result_id: "r1",
        short_id: "",
        tuning_mode: "notuning",
        trust_label: "maintainer-run",
        rank: 1,
        metric_value: 3741.26,
        speedup_vs_best: 1,
      },
    ]);

    const data = await getMetaLeaderboardData();

    expect(data!.cohorts[0]).toMatchObject({ stream_count: 3, primary_metric: "throughput_at_size" });
  });
});
