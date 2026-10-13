import { render, screen } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import type { BenchmarkSummary, DetailResult, PlatformRow } from "@/types";
import { preserveUniqueAfterTruncation, RankTable } from "@/components/RankTable";
import { countUniqueFastestQueries, sharedQueryIdsFor } from "@/lib/compareSummary";

describe("preserveUniqueAfterTruncation", () => {
  it("truncates identities that exceed the cap", () => {
    expect(preserveUniqueAfterTruncation(["DuckDB"], 16)).toEqual(["DuckDB"]);
    expect(preserveUniqueAfterTruncation(["A very long platform name here"], 16)).toEqual([
      "A very lon… here",
    ]);
  });

  it("middle-truncates duplicate-prone identities while preserving the suffix", () => {
    const result = preserveUniqueAfterTruncation(["DataFusion v1.40.0", "DataFusion v1.40.1"], 16);
    expect(new Set(result).size).toBe(2);
    expect(result[0]).toBe("DataFus… v1.40.0");
    expect(result[1]).toBe("DataFus… v1.40.1");
  });

  it("keeps every truncated label within the cap", () => {
    const input = ["DataFusion v1.40.0", "DataFusion v1.40.1", "A very long platform name"];
    const result = preserveUniqueAfterTruncation(input, 16);
    expect(new Set(result).size).toBe(3);
    expect(result.every((label) => label.length <= 16)).toBe(true);
  });
});

function platformRow(overrides: Partial<PlatformRow> & Pick<PlatformRow, "result_id" | "platform">): PlatformRow {
  const timings = overrides.timings ?? {};
  return {
    short_id: "",
    platform_id: overrides.platform.toLowerCase(),
    platform_version: null,
    tuning_mode: null,
    tuning_hash: null,
    execution_mode: null,
    trust_label: "maintainer-run",
    funding: "unspecified",
    run_date: "2026-04-01",
    is_ranking_eligible: true,
    has_display_timing: true,
    valid_query_count: Object.keys(timings).length,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    power_score: 3000,
    display_geomean_ms: 12,
    sample_geomean_ms: 12,
    cost_usd: null,
    compliance_class: null,
    percentile_stats: null,
    phase_durations: null,
    timing_eligibility: Object.fromEntries(
      Object.entries(timings).map(([queryId, ms]) => [
        queryId,
        { is_valid_display_timing: ms !== null, timing_exclusion_reason: ms === null ? "missing_timing" : null },
      ]),
    ),
    ...overrides,
    timings,
  };
}

function detailResult(resultId: string, platform: string, timings: Record<string, number>): DetailResult {
  return {
    result_id: resultId,
    benchmark: "tpcds",
    scale_factor: 10,
    platform,
    platform_id: platform.toLowerCase(),
    driver_version: null,
    run_date: "2026-04-01",
    total_duration_s: 60,
    geomean_ms: 10,
    display_geomean_ms: 10,
    power_score: 3000,
    has_display_timing: true,
    valid_query_count: Object.keys(timings).length,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    environment: {},
    queries: [],
    display_timings: Object.entries(timings).map(([query_id, ms]) => ({
      query_id,
      display_ms: ms,
      sample_count: 3,
      is_valid_display_timing: true,
      timing_exclusion_reason: null,
    })),
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
  };
}

const PARTIAL_RUN_TIMINGS = {
  duckdb: { "1": 10, "2": 20, "18": 5, aggregation_distinct_groupby: 3 },
  snowflake: { "1": 12, "2": 15, "18": 9 },
  clickhouse: { "1": 8, "2": 25, "18": 5, aggregation_distinct_groupby: 2 },
};

function partialRunSummary(): BenchmarkSummary {
  const platforms = [
    platformRow({ result_id: "r-duckdb", platform: "DuckDB", timings: PARTIAL_RUN_TIMINGS.duckdb }),
    platformRow({ result_id: "r-snowflake", platform: "Snowflake", timings: PARTIAL_RUN_TIMINGS.snowflake }),
    platformRow({ result_id: "r-clickhouse", platform: "ClickHouse", timings: PARTIAL_RUN_TIMINGS.clickhouse }),
  ];
  return {
    benchmark: "tpcds",
    scale_factor: 10,
    phase: "standard",
    query_ids: ["1", "2", "18", "aggregation_distinct_groupby"],
    platforms,
    cell_reduction: "none",
    ranking: null,
  };
}

function footerWinCells(): string[] {
  const label = screen.getByText("Fastest on (shared queries)");
  const row = label.closest("tr");
  if (!row) throw new Error("footer row missing");
  return Array.from(row.querySelectorAll("td")).slice(1).map((cell) => cell.textContent?.trim() ?? "");
}

describe("RankTable fastest-on counts", () => {
  it("counts first places over shared queries only, matching the compare summary", () => {
    render(<RankTable summary={partialRunSummary()} />);
    expect(footerWinCells()).toEqual(["0", "1", "1"]);
  });

  it("agrees with the compare summary's shared-query win counts for the same runs", () => {
    const results = [
      detailResult("r-duckdb", "DuckDB", PARTIAL_RUN_TIMINGS.duckdb),
      detailResult("r-snowflake", "Snowflake", PARTIAL_RUN_TIMINGS.snowflake),
      detailResult("r-clickhouse", "ClickHouse", PARTIAL_RUN_TIMINGS.clickhouse),
    ];
    const expected = countUniqueFastestQueries(results, sharedQueryIdsFor(results));
    render(<RankTable summary={partialRunSummary()} />);
    expect(footerWinCells().map(Number)).toEqual(expected);
  });

  it("labels the shared-query basis and states how many queries it covers", () => {
    render(<RankTable summary={partialRunSummary()} />);
    expect(screen.getByText("Fastest on (shared queries)")).toBeTruthy();
    expect(screen.getByText(/counts the 3 of 4 queries/)).toBeTruthy();
    expect(screen.queryByText("1st wins")).toBeNull();
  });
});

describe("RankTable identity labels", () => {
  it("gives a truncated run name a title with the full identity", () => {
    const summary = partialRunSummary();
    summary.platforms[0] = platformRow({
      result_id: "r-long",
      platform: "A very long platform name here",
      timings: PARTIAL_RUN_TIMINGS.duckdb,
    });
    render(<RankTable summary={summary} />);
    const link = Array.from(document.querySelectorAll("th a")).find((a) => a.textContent?.includes("…"));
    expect(link).toBeTruthy();
    expect(link?.getAttribute("title")).toContain("A very long platform name here");
  });

  it("marks each run column with a run-shaped legend marker instead of a dot", () => {
    render(<RankTable summary={partialRunSummary()} />);
    const headers = Array.from(document.querySelectorAll("thead th"));
    const markers = headers.slice(1).map((th) => th.querySelector("svg path"));
    expect(markers.every((path) => path !== null)).toBe(true);
    expect(document.querySelectorAll("thead span.rounded-full").length).toBe(0);
  });
});
