import { describe, expect, it } from "vitest";
import { render } from "@testing-library/preact";
import type { BenchmarkSummary, PlatformRow } from "@/types";
import { SparklineTable } from "@/components/SparklineTable";

function makePlatform(overrides: Partial<PlatformRow> = {}): PlatformRow {
  const timings = overrides.timings ?? { q1: 100, q2: 200 };
  return {
    result_id: "r-default",
    short_id: "",
    platform: "DuckDB",
    platform_id: "duckdb",
    platform_version: "1.4.3",
    tuning_mode: null,
    tuning_hash: null,
    execution_mode: null,
    trust_label: "maintainer-run",
    funding: "unspecified",
    validation_status: "exact",
    run_date: "2026-04-03",
    is_ranking_eligible: true,
    has_display_timing: true,
    valid_query_count: 2,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    power_score: null,
    display_geomean_ms: 150,
    sample_geomean_ms: 150,
    cost_usd: null,
    compliance_class: null,
    percentile_stats: { p50: 100, p90: 200, p95: 250, p99: 300 },
    phase_durations: null,
    timings,
    timing_eligibility: Object.fromEntries(
      Object.entries(timings).map(([queryId, ms]) => [
        queryId,
        {
          is_valid_display_timing: ms !== null && ms > 0,
          timing_exclusion_reason: ms === null ? "missing_timing" : ms === 0 ? "zero_timing" : null,
        },
      ]),
    ),
    ...overrides,
  } as PlatformRow;
}

function makeSummary(overrides: Partial<BenchmarkSummary> = {}): BenchmarkSummary {
  return {
    benchmark: "tpch",
    scale_factor: 0.1,
    phase: "power",
    query_ids: ["q1", "q2"],
    platforms: [
      makePlatform({ result_id: "r1", platform: "DuckDB" }),
      makePlatform({ result_id: "r2", platform: "SQLite", display_geomean_ms: 900 }),
    ],
    ranking: { primary_metric: "display_geomean_ms", secondary_metric: null, primary_order: "asc" },
    ...overrides,
  } as BenchmarkSummary;
}

describe("SparklineTable", () => {
  it("gives the platform column only the width its content needs, not the leftover row space", () => {
    const { container } = render(<SparklineTable summary={makeSummary()} />);

    const headerCells = Array.from(container.querySelectorAll("thead th"));
    const platformHeader = headerCells[0]!;
    expect(platformHeader.className).toContain("w-px");
    expect(platformHeader.className).toContain("whitespace-normal");
    expect(platformHeader.className).toContain("sm:whitespace-nowrap");

    const firstRowCells = Array.from(container.querySelectorAll("tbody tr")[0]!.querySelectorAll("td"));
    const platformCell = firstRowCells[0]!;
    const barCell = firstRowCells[1]!;
    expect(platformCell.className).toContain("w-px");
    expect(platformCell.className).toContain("whitespace-normal");
    expect(platformCell.className).toContain("sm:whitespace-nowrap");
    expect(barCell.className).toContain("w-2/5");
  });

  it("renders one row per platform with a spark bar and a formatted geomean value", () => {
    const { container } = render(<SparklineTable summary={makeSummary()} />);
    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toContain("DuckDB");
    expect(rows[0]!.textContent).toContain("150 ms");
    expect(rows[1]!.textContent).toContain("SQLite");
    expect(rows[1]!.textContent).toContain("900 ms");
  });

  it("lists runs fastest first by display geomean with runs lacking a timing last", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r-119", platform: "Trino", display_geomean_ms: 119 }),
        makePlatform({ result_id: "r-missing", platform: "Presto", display_geomean_ms: null }),
        makePlatform({ result_id: "r-29", platform: "DuckDB", display_geomean_ms: 29 }),
        makePlatform({ result_id: "r-47", platform: "Snowflake", display_geomean_ms: 47 }),
      ],
    });
    const { container } = render(<SparklineTable summary={summary} />);
    const platformNames = Array.from(container.querySelectorAll("tbody tr")).map(
      (row) => row.querySelector("td")?.textContent?.trim() ?? "",
    );
    expect(platformNames).toEqual(["DuckDB", "Snowflake", "Trino", "Presto"]);
  });

  it("draws geomean bars proportional to latency with the slowest run longest", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r-119", platform: "Trino", display_geomean_ms: 119 }),
        makePlatform({ result_id: "r-29", platform: "DuckDB", display_geomean_ms: 29 }),
        makePlatform({ result_id: "r-37", platform: "ClickHouse", display_geomean_ms: 37 }),
        makePlatform({ result_id: "r-47", platform: "Snowflake", display_geomean_ms: 47 }),
      ],
    });
    const { container } = render(<SparklineTable summary={summary} />);
    const widthByPlatform = new Map(
      Array.from(container.querySelectorAll("tbody tr")).map((row) => {
        const bar = row.querySelectorAll("td")[1]!.querySelector("span > span") as HTMLElement;
        return [row.querySelector("td")!.textContent!.trim(), parseFloat(bar.style.width)] as const;
      }),
    );
    expect(widthByPlatform.get("Trino")).toBe(100);
    expect(widthByPlatform.get("DuckDB")!).toBeLessThan(widthByPlatform.get("ClickHouse")!);
    expect(widthByPlatform.get("ClickHouse")!).toBeLessThan(widthByPlatform.get("Snowflake")!);
    expect(widthByPlatform.get("Snowflake")!).toBeLessThan(widthByPlatform.get("Trino")!);
    expect(widthByPlatform.get("DuckDB")!).toBeLessThan(50);
  });

  it("omits the Power@Size column when no rank-safe run has a score", () => {
    const { container } = render(<SparklineTable summary={makeSummary()} />);
    const headers = Array.from(container.querySelectorAll("thead th")).map((th) => th.textContent?.trim());
    expect(headers).not.toContain("Power@Size");
    expect(container.querySelector("tbody tr")!.textContent).not.toContain("Power");
  });
});
