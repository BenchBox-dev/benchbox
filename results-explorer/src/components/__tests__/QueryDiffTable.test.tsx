import { render, screen } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import type { DetailResult } from "@/types";
import {
  DEFAULT_QUERY_DIFF_LIMIT,
  QueryDiffTable,
  applyQueryDiffLimiter,
  buildQueryDiffRows,
  queryDiffCountSentence,
  type QueryDiffRow,
} from "@/components/QueryDiffTable";

function makeResult(overrides: Partial<DetailResult> = {}): DetailResult {
  return {
    result_id: "baseline",
    benchmark: "tpch",
    scale_factor: 0.1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: null,
    run_date: "2026-04-01",
    total_duration_s: 60,
    geomean_ms: 10,
    display_geomean_ms: 10,
    power_score: 3000,
    has_display_timing: true,
    valid_query_count: 2,
    missing_query_count: 1,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    environment: {},
    queries: [],
    display_timings: [
      { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      { query_id: "Q2", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      { query_id: "Q3", display_ms: null, sample_count: 0, is_valid_display_timing: false, timing_exclusion_reason: "missing_timing" },
    ],
    has_plans: false,
    has_tuning: false,
    bundle_download_url: "",
    trust_label: "maintainer-run",
    visibility: "public-curated",
    funding: "unspecified",
    platform_version: null,
    execution_mode: null,
    tuning_mode: null,
    tuning_hash: null,
    test_type: null,
    validation_status: null,
    compliance_class: null,
    cost_usd: null,
    ...overrides,
  };
}

describe("buildQueryDiffRows", () => {
  it("builds baseline-to-candidate ratios, deltas, and statuses", () => {
    const rows = buildQueryDiffRows([
      makeResult(),
      makeResult({
        result_id: "candidate",
        platform: "SQLite",
        platform_id: "sqlite",
        display_timings: [
          { query_id: "Q1", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q3", display_ms: null, sample_count: 0, is_valid_display_timing: false, timing_exclusion_reason: "missing_timing" },
        ],
      }),
    ]);

    expect(rows).toEqual([
      {
        queryId: "Q1",
        candidateResultId: "candidate",
        candidatePlatform: "SQLite",
        baselineMs: 10,
        candidateMs: 20,
        speedupRatio: 0.5,
        deltaMs: 10,
        status: "slower",
        baselineSamples: 3,
        candidateSamples: 3,
        comparable: true,
      },
      {
        queryId: "Q2",
        candidateResultId: "candidate",
        candidatePlatform: "SQLite",
        baselineMs: 20,
        candidateMs: 10,
        speedupRatio: 2,
        deltaMs: -10,
        status: "faster",
        baselineSamples: 3,
        candidateSamples: 3,
        comparable: true,
      },
      {
        queryId: "Q3",
        candidateResultId: "candidate",
        candidatePlatform: "SQLite",
        baselineMs: null,
        candidateMs: null,
        speedupRatio: null,
        deltaMs: null,
        status: "missing",
        baselineSamples: 0,
        candidateSamples: 0,
        comparable: false,
      },
    ]);
  });

  it("normalizes an out-of-range baseline index before selecting candidates", () => {
    const rows = buildQueryDiffRows(
      [
        makeResult({ result_id: "baseline", platform: "DuckDB" }),
        makeResult({
          result_id: "candidate",
          platform: "SQLite",
          platform_id: "sqlite",
          display_timings: [{ query_id: "Q1", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null }],
        }),
      ],
      99,
    );

    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({
      queryId: "Q1",
      candidateResultId: "candidate",
      baselineMs: 10,
      candidateMs: 20,
    });
    expect(rows.some((row) => row.candidateResultId === "baseline")).toBe(false);
  });

  it("builds one query row per non-baseline candidate for multi-result compares", () => {
    const rows = buildQueryDiffRows([
      makeResult({
        result_id: "baseline",
        display_timings: [
          { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
      makeResult({
        result_id: "candidate-a",
        platform: "SQLite",
        platform_id: "sqlite",
        display_timings: [
          { query_id: "Q1", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 40, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
      makeResult({
        result_id: "candidate-b",
        platform: "PostgreSQL",
        platform_id: "postgresql",
        display_timings: [
          { query_id: "Q1", display_ms: 5, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
    ]);

    expect(rows).toHaveLength(4);
    expect(rows.map((row) => `${row.queryId}:${row.candidateResultId}`)).toEqual([
      "Q1:candidate-a",
      "Q1:candidate-b",
      "Q2:candidate-a",
      "Q2:candidate-b",
    ]);
  });

  it("treats exact-zero display timings as excluded, not faster evidence", () => {
    const rows = buildQueryDiffRows([
      makeResult({
        display_timings: [
          { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
      makeResult({
        result_id: "candidate",
        platform: "SQLite",
        platform_id: "sqlite",
        display_timings: [
          { query_id: "Q1", display_ms: 0, sample_count: 0, is_valid_display_timing: false, timing_exclusion_reason: "zero_timing" },
        ],
      }),
    ]);

    expect(rows[0]).toMatchObject({
      queryId: "Q1",
      baselineMs: 10,
      candidateMs: null,
      speedupRatio: null,
      deltaMs: null,
      status: "missing",
    });
  });
});

describe("QueryDiffTable", () => {
  it("renders a dense diff table with baseline and candidate evidence", () => {
    render(
      <QueryDiffTable
        results={[
          makeResult(),
          makeResult({
            result_id: "candidate",
            platform: "SQLite",
            platform_id: "sqlite",
            display_timings: [
              { query_id: "Q1", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
              { query_id: "Q2", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            ],
          }),
        ]}
      />,
    );

    const table = screen.getByRole("heading", { name: "Query-level differences" }).closest("section");
    expect(table).not.toBeNull();
    expect(table).toHaveTextContent("Baseline: DuckDB");
    expect(table).toHaveTextContent("Showing 3 of 3 queries.");
    expect(table).toHaveTextContent("Q1");
    expect(table).toHaveTextContent("SQLite");
    expect(table).toHaveTextContent("2×");
    expect(table).toHaveTextContent("+10 ms");
    expect(table).toHaveTextContent("Slower");
    expect(table).toHaveTextContent("0.5×");
    expect(table).toHaveTextContent("−10 ms");
    expect(table).toHaveTextContent("Faster");
    expect(table).toHaveTextContent("Not comparable");
  });

  it("counts logical queries rather than candidate rows in multi-run comparisons", () => {
    render(
      <QueryDiffTable
        results={[
          makeResult({ result_id: "baseline" }),
          makeResult({ result_id: "candidate-a", platform: "SQLite", platform_id: "sqlite" }),
          makeResult({ result_id: "candidate-b", platform: "PostgreSQL", platform_id: "postgresql" }),
        ]}
        queryFilter={["Q1", "Q2"]}
        limiter="movement"
      />,
    );

    const table = screen.getByRole("heading", { name: "Query-level differences" }).closest("section");
    expect(table).toHaveTextContent("Showing 2 of 3 queries.");
    expect(table).not.toHaveTextContent("Showing 4 of 6 queries.");
  });
});

describe("the Top-N limiter", () => {
  const row = (queryId: string, deltaMs: number | null, comparable = true): QueryDiffRow => ({
    queryId,
    candidateResultId: "c",
    candidatePlatform: "SQLite",
    baselineMs: 10,
    candidateMs: comparable ? 10 + (deltaMs ?? 0) : null,
    speedupRatio: comparable && deltaMs !== null ? 10 / (10 + deltaMs) : null,
    deltaMs: comparable ? deltaMs : null,
    status: comparable ? (deltaMs === null ? "missing" : deltaMs < 0 ? "faster" : "slower") : "missing",
    baselineSamples: 3,
    candidateSamples: comparable ? 3 : 0,
    comparable,
  });

  const rows = [row("Q1", -50), row("Q2", 30), row("Q3", -5), row("Q4", 80), row("Q5", null, false)];

  it("pins the product limit to the requested ten queries", () => {
    expect(DEFAULT_QUERY_DIFF_LIMIT).toBe(10);
  });

  it("returns everything for 'all', including rows that cannot be compared", () => {
    expect(applyQueryDiffLimiter(rows, "all", 2)).toHaveLength(5);
  });

  it("ranks the largest speedups by magnitude", () => {
    const out = applyQueryDiffLimiter(rows, "speedups", 10);
    expect(out.map((r) => r.queryId)).toEqual(["Q1", "Q3"]);
  });

  it("ranks the largest slowdowns by magnitude", () => {
    const out = applyQueryDiffLimiter(rows, "slowdowns", 10);
    expect(out.map((r) => r.queryId)).toEqual(["Q4", "Q2"]);
  });

  it("ranks movement in either direction by absolute magnitude", () => {
    const out = applyQueryDiffLimiter(rows, "movement", 3);
    expect(out.map((r) => r.queryId)).toEqual(["Q4", "Q1", "Q2"]);
  });

  it("caps at N", () => {
    expect(applyQueryDiffLimiter(rows, "movement", 2)).toHaveLength(2);
  });

  it("never ranks an uncomparable row into a 'largest' view", () => {
    for (const limiter of ["speedups", "slowdowns", "movement"] as const) {
      expect(applyQueryDiffLimiter(rows, limiter, 10).every((r) => r.comparable)).toBe(true);
    }
  });
});

describe("the count sentence", () => {
  it("names how many of how many are shown", () => {
    expect(queryDiffCountSentence(3, 10, "all")).toBe("Showing 3 of 10 queries.");
  });

  it("keeps the denominator when a filter matches nothing", () => {
    expect(queryDiffCountSentence(0, 103, "speedups")).toContain("of 103");
    expect(queryDiffCountSentence(0, 103, "speedups")).toContain("largest speedups");
  });

  it("distinguishes an empty comparison from an empty filter", () => {
    expect(queryDiffCountSentence(0, 0, "all")).toBe("No queries to compare.");
  });

  it("uses singular grammar for one query", () => {
    expect(queryDiffCountSentence(1, 1, "all")).toBe("Showing 1 of 1 query.");
  });
});
