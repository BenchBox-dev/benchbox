import { describe, expect, it } from "vitest";
import { render } from "@testing-library/preact";
import type { BenchmarkSummary, PlatformRow } from "@/types";
import { CDFChart } from "@/components/CDFChart";
import { paletteColor } from "@/lib/chartTheme";

function makePlatform(overrides: Partial<PlatformRow>): PlatformRow {
  const timings = overrides.timings ?? { q1: 100, q2: 200, q3: 300 };
  return {
    result_id: "r-default",
    platform: "DuckDB",
    platform_id: "duckdb",
    platform_version: "v1.4.3",
    run_date: "2026-05-06T12:00:00Z",
    trust_label: "maintainer-run",
    validation_status: "exact",
    is_ranking_eligible: true,
    has_display_timing: true,
    valid_query_count: 3,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    power_score: null,
    display_geomean_ms: 200,
    sample_geomean_ms: 200,
    cost_usd: null,
    compliance_class: null,
    percentile_stats: null,
    phase_durations: null,
    timings,
    timing_eligibility: Object.fromEntries(
      Object.entries(timings).map(([queryId, ms]) => [
        queryId,
        { is_valid_display_timing: ms !== null && ms > 0, timing_exclusion_reason: null },
      ]),
    ),
    ...overrides,
  } as PlatformRow;
}

function makeSummary(platforms: PlatformRow[]): BenchmarkSummary {
  return {
    benchmark: "tpch",
    scale_factor: 1,
    phase: "power",
    cell_reduction: "median",
    query_ids: ["q1", "q2", "q3"],
    platforms,
    ranking: { primary_metric: "display_geomean_ms", secondary_metric: "platform", primary_order: "asc" },
  };
}

describe("CDFChart", () => {
  it("lists legend entries fastest first by display geomean", () => {
    const summary = makeSummary([
      makePlatform({ result_id: "r-trino", platform: "Trino", timings: { q1: 300, q2: 400, q3: 500 }, display_geomean_ms: 390 }),
      makePlatform({ result_id: "r-duck", platform: "DuckDB", timings: { q1: 20, q2: 30, q3: 40 }, display_geomean_ms: 29 }),
    ]);
    const { container } = render(<CDFChart summary={summary} />);
    const legend = Array.from(container.querySelectorAll("ul li")).map((item) => item.textContent ?? "");
    expect(legend[0]).toContain("DuckDB");
    expect(legend[1]).toContain("Trino");
  });

  it("does not truncate a legend name that fits the chart width", () => {
    const summary = makeSummary([
      makePlatform({ result_id: "r-clickhouse", platform: "ClickHouse Local", platform_version: "v25.8" }),
    ]);
    const { container } = render(<CDFChart summary={summary} />);
    const legendItem = container.querySelector("ul li");
    expect(legendItem?.textContent).toContain("ClickHouse Local");
    expect(legendItem?.textContent).not.toContain("…");
  });

  it("keeps each run's original colour when series arrive pre-sorted", () => {
    const trino = makePlatform({ result_id: "r-trino", platform: "Trino", timings: { q1: 300, q2: 400, q3: 500 }, display_geomean_ms: 390 });
    const duck = makePlatform({ result_id: "r-duck", platform: "DuckDB", timings: { q1: 20, q2: 30, q3: 40 }, display_geomean_ms: 29 });
    const colorIndexFor = (platform: PlatformRow) => (platform.platform === "Trino" ? 0 : 1);
    const { container } = render(<CDFChart summary={makeSummary([duck, trino])} colorIndexFor={colorIndexFor} />);
    const swatchColor = (name: string) =>
      Array.from(container.querySelectorAll("ul li")).find((item) => item.textContent?.includes(name))?.querySelector("span")?.style.backgroundColor;
    expect(swatchColor("DuckDB")).toBe(paletteColor(1).toString());
    expect(swatchColor("Trino")).toBe(paletteColor(0).toString());
  });
});
