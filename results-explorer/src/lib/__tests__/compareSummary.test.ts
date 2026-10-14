import { describe, expect, it } from "vitest";
import type { DetailResult } from "@/types";
import { buildCompareDecisionSummary } from "@/lib/compareSummary";
import { buildStandings } from "@/components/MultiRunStandings";

function makeResult(overrides: Partial<DetailResult> = {}): DetailResult {
  return {
    result_id: "r1",
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
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    environment: {},
    queries: [],
    display_timings: [
      { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      { query_id: "Q2", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
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

describe("buildCompareDecisionSummary", () => {
  it("selects the power-score winner and counts winner query wins", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({
          result_id: "duck",
          platform: "DuckDB",
          power_score: 3000,
          normalized_cost_usd: 0.5,
          cost_status: "normalized",
        }),
        makeResult({
          result_id: "sqlite",
          platform: "SQLite",
          platform_id: "sqlite",
          power_score: 300,
          normalized_cost_usd: 1.5,
          cost_status: "normalized",
          display_timings: [
            { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q2", display_ms: 200, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          ],
        }),
      ],
      "power_score",
    );

    expect(summary.winner?.platform).toBe("DuckDB");
    expect(summary.comparisonRatio).toBe(10);
    expect(summary.headline).toBe("In these selected runs, DuckDB's power score was 10× better than the lowest selected run.");
    expect(summary.queryRecord).toMatchObject({
      totalQueries: 2,
      comparableQueries: 2,
      wins: 2,
      losses: 0,
      ties: 0,
      missing: 0,
    });
    expect(summary.percentiles[0]).toMatchObject({
      platform: "DuckDB",
      p50: 15,
      p90: 19,
    });
    expect(summary.percentiles[0]?.p99).toBeCloseTo(19.9);
    expect(summary.cost).toMatchObject({
      normalizedResultCount: 2,
      winnerCostUsd: 0.5,
      winnerIsBestCostPerformance: true,
    });
    expect(summary.cost?.winnerCostPerformanceRatioVsWorst).toBe(30);
  });

  it("uses lower geomean as better and tracks losses against faster query rows", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({
          result_id: "fast",
          platform: "FastDB",
          display_geomean_ms: 10,
          display_timings: [
            { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q2", display_ms: 40, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q3", display_ms: 5, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          ],
        }),
        makeResult({
          result_id: "slow",
          platform: "SlowDB",
          platform_id: "slow",
          display_geomean_ms: 40,
          display_timings: [
            { query_id: "Q1", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q2", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q3", display_ms: 5, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          ],
        }),
      ],
      "display_geomean_ms",
    );

    expect(summary.winner?.platform).toBe("FastDB");
    expect(summary.comparisonRatio).toBe(4);
    expect(summary.headline).toBe("In these selected runs, FastDB's geomean query time was 4× faster than the slowest selected run.");
    expect(summary.queryRecord).toMatchObject({
      comparableQueries: 3,
      wins: 1,
      losses: 1,
      ties: 1,
    });
  });

  it("suppresses winner claims when selected runs lack common valid query evidence", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({
          result_id: "winner",
          platform: "WinnerDB",
          display_timings: [
            { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q2", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          ],
        }),
        makeResult({
          result_id: "candidate",
          platform: "CandidateDB",
          platform_id: "candidate",
          power_score: 300,
          display_timings: [
            { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q3", display_ms: 300, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          ],
        }),
      ],
      "power_score",
    );

    expect(summary.claimSuppressed).toBe(true);
    expect(summary.winner).toBeNull();
    expect(summary.headline).toContain("Insufficient comparable query evidence");
    expect(summary.queryRecord).toMatchObject({
      totalQueries: 3,
      comparableQueries: 0,
      wins: 0,
      losses: 0,
      ties: 0,
      missing: 3,
    });
  });

  it("suppresses AMPLab-shaped winner language when zeros leave only one common valid query", () => {
    const queryIds = Array.from({ length: 8 }, (_, index) => `Q${index + 1}`);
    const zeroTimings = queryIds.map((queryId, index) => ({
      query_id: queryId,
      display_ms: index === 0 ? 10 : 0,
      sample_count: index === 0 ? 3 : 0,
      is_valid_display_timing: index === 0,
      timing_exclusion_reason: index === 0 ? null : "zero_timing",
    }));
    const summary = buildCompareDecisionSummary(
      [
        makeResult({
          result_id: "duck",
          platform: "DuckDB",
          display_timings: zeroTimings,
        }),
        makeResult({
          result_id: "df",
          platform: "DataFusion",
          platform_id: "datafusion",
          display_geomean_ms: 12,
          display_timings: zeroTimings.map((timing) => ({
            ...timing,
            display_ms: timing.is_valid_display_timing ? 12 : 0,
          })),
        }),
        makeResult({
          result_id: "sqlite",
          platform: "SQLite",
          platform_id: "sqlite",
          display_geomean_ms: 15,
          display_timings: zeroTimings.map((timing) => ({
            ...timing,
            display_ms: timing.is_valid_display_timing ? 15 : 0,
          })),
        }),
      ],
      "display_geomean_ms",
    );

    expect(summary.claimSuppressed).toBe(true);
    expect(summary.winner).toBeNull();
    expect(summary.headline).toContain("Insufficient comparable query evidence");
    expect(summary.headline).toContain("Winner language is suppressed");
    expect(summary.headline).not.toContain("faster by geomean");
    expect(summary.percentiles[0]).toMatchObject({ p50: 10, p90: 10, p99: 10 });
  });

  it("allows winner claims with two common valid queries and per-result 50 percent coverage", () => {
    const timing = (queryId: string, value: number | null) => ({
      query_id: queryId,
      display_ms: value,
      sample_count: value === null ? 0 : 3,
      is_valid_display_timing: value !== null,
      timing_exclusion_reason: value === null ? "missing_timing" : null,
    });
    const summary = buildCompareDecisionSummary(
      [
        makeResult({
          result_id: "a",
          platform: "A",
          display_timings: [
            timing("Q1", 10),
            timing("Q2", 20),
            timing("Q3", 30),
            timing("Q4", null),
            timing("Q5", null),
            timing("Q6", null),
          ],
        }),
        makeResult({
          result_id: "b",
          platform: "B",
          platform_id: "b",
          display_geomean_ms: 40,
          display_timings: [
            timing("Q1", 20),
            timing("Q2", 40),
            timing("Q3", null),
            timing("Q4", 60),
            timing("Q5", null),
            timing("Q6", null),
          ],
        }),
      ],
      "display_geomean_ms",
    );

    expect(summary.claimSuppressed).toBe(false);
    expect(summary.queryRecord.comparableQueries).toBe(2);
    expect(summary.winner?.platform).toBe("A");
  });

  it("suppresses winner claims when every selected result lacks the primary metric", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ display_geomean_ms: null }),
        makeResult({ result_id: "r2", platform: "SQLite", display_geomean_ms: null }),
      ],
      "display_geomean_ms",
    );

    expect(summary.winner).toBeNull();
    expect(summary.headline).toBe("The selected runs do not have enough primary-metric data for a comparison.");
    expect(summary.queryRecord.missing).toBe(2);
  });

  it("uses only cost_status=normalized rows for cost/performance context", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({
          result_id: "duck",
          normalized_cost_usd: 0.5,
          cost_status: "normalized",
        }),
        makeResult({
          result_id: "legacy",
          platform: "LegacyDB",
          platform_id: "legacy",
          power_score: 300,
          normalized_cost_usd: 0.01,
          cost_status: "unavailable",
        }),
      ],
      "power_score",
    );

    expect(summary.cost).toMatchObject({
      normalizedResultCount: 1,
      winnerCostUsd: 0.5,
      winnerCostPerformanceRatioVsWorst: 1,
    });
  });

  it("omits cost context when no selected rows have normalized cost", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({
          result_id: "duck",
          normalized_cost_usd: null,
          cost_status: "not_applicable_local",
        }),
        makeResult({
          result_id: "sqlite",
          platform: "SQLite",
          platform_id: "sqlite",
          power_score: 300,
          normalized_cost_usd: null,
          cost_status: "unavailable",
        }),
      ],
      "power_score",
    );

    expect(summary.cost).toBeNull();
  });

  it("suppresses winner language when cohort guardrails mark the selection incomparable", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "duck", platform: "DuckDB" }),
        makeResult({ result_id: "sqlite", platform: "SQLite", power_score: 300 }),
      ],
      "power_score",
      {
        suppressWinnerClaims: true,
        suppressionReason: "benchmarks differ",
      },
    );

    expect(summary.claimSuppressed).toBe(true);
    expect(summary.winner).toBeNull();
    expect(summary.headline).toBe(
      "Not directly comparable: benchmarks differ. Winner language is suppressed; raw query evidence remains available.",
    );
  });

  it("renders tie copy when same-platform comparison rounds to 1× (finding #8)", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "polars-a", platform: "Polars", power_score: 1000 }),
        makeResult({ result_id: "polars-b", platform: "Polars", power_score: 1000 }),
      ],
      "power_score",
    );

    expect(summary.isTie).toBe(true);
    expect(summary.headline).toContain("tie threshold");
    expect(summary.headline).not.toContain("leads by");
    expect(summary.headline).not.toContain("faster");
  });

  it("uses the cohort-aware run label in the headline when runLabels are provided", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "polars-a", platform: "Polars", power_score: 1500 }),
        makeResult({ result_id: "polars-b", platform: "Polars", power_score: 1000 }),
      ],
      "power_score",
      {
        runLabels: {
          "polars-a": "Polars 2026-05-08 (deadbeef)",
          "polars-b": "Polars 2026-05-02 (0093bb7a)",
        },
      },
    );

    expect(summary.isTie).toBe(false);
    expect(summary.winnerLabel).toBe("Polars 2026-05-08 (deadbeef)");
    expect(summary.headline).toContain("Polars 2026-05-08 (deadbeef)");
    expect(summary.headline).not.toMatch(/^Polars leads/);
  });

  describe("validation disclosure", () => {
    it("flags a result with an explicit non-clean validation_status", () => {
      const summary = buildCompareDecisionSummary(
        [
          makeResult({ result_id: "duck", platform: "DuckDB", power_score: 3000, validation_status: "passed" }),
          makeResult({
            result_id: "pandas",
            platform: "Pandas",
            power_score: 843,
            validation_status: "not_run",
          }),
        ],
        "power_score",
      );

      expect(summary.nonCleanValidation).toEqual([
        { resultId: "pandas", platform: "Pandas", status: "not_run", label: "No validation" },
      ]);
      expect(summary.validationCaveat).toContain("Pandas: No validation");
      expect(summary.headline.endsWith(summary.validationCaveat!)).toBe(true);
    });

    it("does not flag a result with no validation_status recorded at all", () => {
      const summary = buildCompareDecisionSummary(
        [
          makeResult({ result_id: "duck", platform: "DuckDB", power_score: 3000 }),
          makeResult({ result_id: "sqlite", platform: "SQLite", power_score: 300 }),
        ],
        "power_score",
      );

      expect(summary.nonCleanValidation).toEqual([]);
      expect(summary.validationCaveat).toBeNull();
    });

    it("flags every non-clean status in a multi-way comparison, not just the first", () => {
      const summary = buildCompareDecisionSummary(
        [
          makeResult({ result_id: "duck", platform: "DuckDB", power_score: 3000, validation_status: "passed" }),
          makeResult({ result_id: "pandas", platform: "Pandas", power_score: 843, validation_status: "not_run" }),
          makeResult({ result_id: "polars", platform: "Polars", power_score: 500, validation_status: "uncertain" }),
        ],
        "power_score",
      );

      expect(summary.nonCleanValidation.map((entry) => entry.platform)).toEqual(["Pandas", "Polars"]);
    });

    it("still reports the caveat when the winner claim is suppressed for other reasons", () => {
      const summary = buildCompareDecisionSummary(
        [
          makeResult({ result_id: "duck", platform: "DuckDB", power_score: 3000, validation_status: "passed" }),
          makeResult({ result_id: "pandas", platform: "Pandas", power_score: 843, validation_status: "not_run" }),
        ],
        "power_score",
        { suppressWinnerClaims: true, suppressionReason: "benchmarks differ" },
      );

      expect(summary.nonCleanValidation).toHaveLength(1);
      expect(summary.claimSuppressed).toBe(true);
    });
  });
});

describe("winner claims when no selected run is rank-safe", () => {
  const unrankedReason = { ranking_exclusion_reason: "insufficient_common_valid_timings" } as const;

  it("names no winner by the primary metric and says why", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "duck", platform: "DuckDB", power_score: 3000, display_geomean_ms: 10, ...unrankedReason }),
        makeResult({
          result_id: "sqlite",
          platform: "SQLite",
          platform_id: "sqlite",
          power_score: 300,
          display_geomean_ms: 20,
          ...unrankedReason,
        }),
      ],
      "power_score",
    );

    expect(summary.winner).toBeNull();
    expect(summary.claimSuppressed).toBe(true);
    expect(summary.rankingBlocked).toBe(true);
    expect(summary.comparisonRatio).toBeNull();
    expect(summary.headline.startsWith("No ranked winner: no selected run is rank-safe")).toBe(true);
    expect(summary.headline).not.toContain("power score was");
    expect(summary.headline).toContain("Only descriptive geomean comparisons are shown.");
  });

  it("keeps a descriptive geomean spread when ranking is blocked", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "duck", display_geomean_ms: 10, ...unrankedReason }),
        makeResult({ result_id: "sqlite", platform: "SQLite", display_geomean_ms: 25, ...unrankedReason }),
      ],
      "power_score",
    );

    expect(summary.descriptiveGeomeanSpread).toBeCloseTo(2.5);
  });

  it("does not block winner claims when at least one run is rank-safe", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "duck", platform: "DuckDB", power_score: 3000 }),
        makeResult({ result_id: "sqlite", platform: "SQLite", power_score: 300, ...unrankedReason }),
      ],
      "power_score",
    );

    expect(summary.rankingBlocked).toBe(false);
    expect(summary.winner?.platform).toBe("DuckDB");
    expect(summary.descriptiveGeomeanSpread).toBeNull();
  });
});

describe("query win counts", () => {
  it("count the same shared-query wins that the standings show for the winner", () => {
    const results = [
      makeResult({
        result_id: "duck",
        platform: "DuckDB",
        power_score: 3000,
        display_timings: [
          { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 50, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q3", display_ms: 5, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
      makeResult({
        result_id: "sqlite",
        platform: "SQLite",
        power_score: 300,
        display_timings: [
          { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q3", display_ms: 50, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q4", display_ms: 30, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
    ];

    const summary = buildCompareDecisionSummary(results, "power_score");
    const standings = buildStandings(results, 0, ["DuckDB", "SQLite"]);
    const duckStanding = standings.rows.find((row) => row.resultId === "duck");

    expect(summary.queryRecord).toMatchObject({
      totalQueries: 4,
      comparableQueries: 3,
      wins: 2,
      losses: 1,
      ties: 0,
      missing: 1,
    });
    expect(duckStanding?.queriesWon).toBe(summary.queryRecord.wins);
    expect(standings.sharedQueryIds).toEqual(["Q1", "Q2", "Q3"]);
  });
});
