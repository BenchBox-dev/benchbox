import { describe, expect, it } from "vitest";
import { render } from "@testing-library/preact";
import type { BenchmarkSummary, PlatformRow } from "@/types";
import { DistributionBox } from "@/components/DistributionBox";
import { paletteColor } from "@/lib/chartTheme";

function makePlatform(overrides: Partial<PlatformRow> = {}): PlatformRow {
  const timings = overrides.timings ?? { q1: 100, q2: 200, q3: 300 };
  return {
    result_id: "r-default",
    platform: "DataFusion",
    platform_id: "datafusion",
    platform_version: "v53.0.0",
    driver_version: null,
    run_date: "2026-05-06T12:00:00Z",
    trust_label: "maintainer-run",
    validation_status: "exact",
    deployment_class: null,
    instance_or_warehouse: null,
    is_ranking_eligible: true,
    has_display_timing: true,
    valid_query_count: 3,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    compliance_class: null,
    has_plans: false,
    plans_published: false,
    has_tuning: false,
    bundle_download_url: "",
    cost_status: null,
    cost_scope: null,
    cost_model_version: null,
    cost_model_source: null,
    normalized_cost_usd: null,
    cloud_provider: null,
    cloud_region: null,
    storage_format: null,
    billing_unit: null,
    pricing_region: null,
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
    visibility: "public-curated",
    test_type: null,
    tuning_mode: null,
    tuning_hash: null,
    cost_usd: null,
    power_score: null,
    geomean_ms: 200,
    display_geomean_ms: 200,
    total_duration_s: 1,
    query_count: 3,
    ...overrides,
  } as PlatformRow;
}

function makeSummary(): BenchmarkSummary {
  return {
    benchmark: "tpch",
    scale_factor: 1,
    phase: "power",
    cell_reduction: "median",
    query_ids: ["q1", "q2", "q3"],
    platforms: [
      makePlatform({
        result_id: "tpch-datafusion-sf1-20260506-3b8bbc42",
        run_date: "2026-05-06T12:00:00Z",
      }),
      makePlatform({
        result_id: "tpch-datafusion-sf1-20260507-deadbeef",
        run_date: "2026-05-07T12:00:00Z",
      }),
      makePlatform({
        result_id: "tpch-datafusion-sf1-20260508-cafefeed",
        run_date: "2026-05-08T12:00:00Z",
      }),
      makePlatform({
        result_id: "tpch-datafusion-sf1-20260509-feedface",
        run_date: "2026-05-09T12:00:00Z",
      }),
    ],
  } as BenchmarkSummary;
}

describe("DistributionBox label disambiguation (finding #7)", () => {
  it("excludes cells using timing eligibility instead of raw positive values", () => {
    const summary = {
      ...makeSummary(),
      platforms: [
        makePlatform({
          timings: { q1: 100, q2: 200, q3: 300 },
          timing_eligibility: {
            q1: { is_valid_display_timing: false, timing_exclusion_reason: "zero_timing" },
            q2: { is_valid_display_timing: false, timing_exclusion_reason: "zero_timing" },
            q3: { is_valid_display_timing: false, timing_exclusion_reason: "zero_timing" },
          },
        }),
      ],
    };

    const { container } = render(<DistributionBox summary={summary} />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("preserves the disambiguator when the truncation budget would collide", () => {
    const { container } = render(<DistributionBox summary={makeSummary()} />);
    const labels = Array.from(container.querySelectorAll("text"))
      .map((node) => node.textContent ?? "")
      .filter((text) => text.includes("DataFusion"));
    expect(labels.length).toBeGreaterThanOrEqual(4);
    expect(new Set(labels).size).toBe(labels.length);
    const collapsed = labels.filter((label) => label.startsWith("DataFusion v53.0.0 …"));
    expect(collapsed.length).toBe(0);
  });

  it("renders the full identity inside <title> for screen readers and tooltips", () => {
    const { container } = render(<DistributionBox summary={makeSummary()} />);
    const titles = Array.from(container.querySelectorAll("text > title"))
      .map((node) => node.textContent ?? "")
      .filter((text) => text.includes("DataFusion"));
    expect(titles.length).toBeGreaterThanOrEqual(4);
    expect(titles.some((title) => title.includes("2026-05-06"))).toBe(true);
  });

  it("lists runs fastest first by display geomean regardless of selection order", () => {
    const summary: BenchmarkSummary = {
      benchmark: "tpch",
      scale_factor: 1,
      phase: "power",
      cell_reduction: "median",
      query_ids: ["q1", "q2", "q3"],
      platforms: [
        makePlatform({
          result_id: "tpch-trino-sf1-20260506-aaaa1111",
          platform: "Trino",
          platform_version: "v470",
          timings: { q1: 300, q2: 400, q3: 500 },
          display_geomean_ms: 390,
        }),
        makePlatform({
          result_id: "tpch-duckdb-sf1-20260506-bbbb2222",
          platform: "DuckDB",
          platform_version: "v1.4.3",
          timings: { q1: 20, q2: 30, q3: 40 },
          display_geomean_ms: 29,
        }),
      ],
      ranking: { primary_metric: "display_geomean_ms", secondary_metric: "platform", primary_order: "asc" },
    };
    const { container } = render(<DistributionBox summary={summary} />);
    const rowLabels = Array.from(container.querySelectorAll("svg text"))
      .map((node) => node.textContent ?? "")
      .filter((text) => text.includes("DuckDB") || text.includes("Trino"));
    expect(rowLabels[0]).toContain("DuckDB");
    expect(rowLabels[1]).toContain("Trino");
  });

  it("keeps each run's original colour when rows arrive pre-sorted", () => {
    const trino = makePlatform({ result_id: "tpch-trino-sf1-20260506-aaaa1111", platform: "Trino", timings: { q1: 300, q2: 400, q3: 500 }, display_geomean_ms: 390 });
    const duckdb = makePlatform({ result_id: "tpch-duckdb-sf1-20260506-bbbb2222", platform: "DuckDB", timings: { q1: 20, q2: 30, q3: 40 }, display_geomean_ms: 29 });
    const summary: BenchmarkSummary = {
      benchmark: "tpch",
      scale_factor: 1,
      phase: "power",
      cell_reduction: "median",
      query_ids: ["q1", "q2", "q3"],
      platforms: [duckdb, trino],
      ranking: { primary_metric: "display_geomean_ms", secondary_metric: "platform", primary_order: "asc" },
    };
    const colorIndexFor = (platform: PlatformRow) => (platform.platform === "Trino" ? 0 : 1);
    const { container } = render(<DistributionBox summary={summary} colorIndexFor={colorIndexFor} />);
    const strokeFor = (name: string) => {
      const label = Array.from(container.querySelectorAll("svg text")).find((node) => node.textContent?.includes(name));
      return label!.closest("g")!.querySelector("line")!.getAttribute("stroke");
    };
    expect(strokeFor("DuckDB")).toBe(paletteColor(1));
    expect(strokeFor("Trino")).toBe(paletteColor(0));
  });
});
