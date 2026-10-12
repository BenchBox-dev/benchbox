import { render, screen, fireEvent, within } from "@testing-library/preact";
import { describe, it, expect } from "vitest";
import type { BenchmarkSummary, PlatformRow } from "@/types";

import { SummaryChartOverview, SummaryMetricTable } from "@/components/SummaryChartOverview";

function makePlatform(overrides: Partial<PlatformRow> = {}): PlatformRow {
  const timings = overrides.timings ?? { Q1: 10, Q2: 20, Q3: 30 };
  return {
    result_id: "r1",
    short_id: "",
    platform_id: "duckdb",
    platform: "DuckDB",
    platform_version: null,
    tuning_mode: null,
    tuning_hash: null,
    execution_mode: null,
    trust_label: "maintainer-run",
    funding: "unspecified",
    run_date: "2026-04-01",
    is_ranking_eligible: true,
    has_display_timing: true,
    valid_query_count: 3,
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
    timings,
    timing_eligibility:
      overrides.timing_eligibility ??
      Object.fromEntries(
        Object.entries(timings).map(([queryId, ms]) => [
          queryId,
          {
            is_valid_display_timing: ms !== null && ms > 0,
            timing_exclusion_reason: ms === null ? "missing_timing" : ms === 0 ? "zero_timing" : null,
          },
        ]),
      ),
    ...overrides,
  };
}

function makeSummary(overrides: Partial<BenchmarkSummary> = {}): BenchmarkSummary {
  return {
    benchmark: "tpch",
    scale_factor: 0.1,
    phase: "standard",
    query_ids: ["Q1", "Q2", "Q3"],
    platforms: [
      makePlatform({ result_id: "r1", platform_id: "duckdb", platform: "DuckDB" }),
      makePlatform({
        result_id: "r2",
        platform_id: "sqlite",
        platform: "SQLite",
        power_score: 1500,
        display_geomean_ms: 25,
        timings: { Q1: 20, Q2: 40, Q3: 60 },
      }),
    ],
    cell_reduction: "median",
    ranking: null,
    ...overrides,
  };
}

function renderOverview(summary: BenchmarkSummary = makeSummary()) {
  return render(<SummaryChartOverview context={{ kind: "summary", summary }} />);
}

describe("SummaryChartOverview section chrome", () => {
  it("exports the shared metric table for compare views", () => {
    const { container } = render(<SummaryMetricTable summary={makeSummary()} />);
    const table = container.querySelector("table.summary-metric-table") as HTMLElement;

    expect(within(table).getByText("Display geomean")).not.toBeNull();
    expect(within(table).getByText("Power@Size")).not.toBeNull();
    expect(within(table).getByText("12 ms")).not.toBeNull();
    expect(within(table).getByText("3,000")).not.toBeNull();
  });

  it("labels the expanded Percentiles view with cohort-aware identities, not bare platform names", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({
          result_id: "r1",
          platform: "DuckDB",
          platform_version: "1.4.3",
          percentile_stats: { p50: 10, p90: 20, p95: 25, p99: 30 },
        }),
        makePlatform({
          result_id: "r2",
          platform: "DuckDB",
          platform_version: "1.5.0",
          percentile_stats: { p50: 12, p90: 22, p95: 27, p99: 32 },
        }),
      ],
    });
    renderOverview(summary);

    const preview = screen.getByTestId("summary-chart-preview-percentile_ladder") as HTMLDetailsElement;
    preview.open = true;
    fireEvent(preview, new Event("toggle"));

    const full = screen.getByTestId("summary-chart-full-percentile_ladder");
    expect(full.textContent).toContain("v1.4.3");
    expect(full.textContent).toContain("v1.5.0");
    expect(full.textContent).not.toMatch(/DuckDB\s*2\b/);
  });

  it("leads with the first chart rather than a restatement of the page", () => {
    const { container } = renderOverview();
    expect(screen.queryByText("What does this cohort show?")).toBeNull();
    expect(screen.queryByText(/Shared scope:/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Copy chart-section link/ })).toBeNull();
    expect(screen.queryByText("Which platforms lead on speed and throughput?")).toBeNull();
    expect(
      screen.queryByText(/Geomean query time and power score in one comparison/),
    ).toBeNull();
    const root = container.querySelector("[data-testid='summary-chart-overview']");
    expect(root?.getAttribute("id")).toBe("cohort-charts");
    expect(screen.getByRole("table", { name: "Speed and throughput by platform" })).not.toBeNull();
  });

  it("names the platform column consistently and puts each value beside its bar", () => {
    const { container } = renderOverview();
    const table = container.querySelector("table.summary-metric-table")!;
    expect(within(table as HTMLElement).getByText("Platform")).not.toBeNull();
    expect(within(table as HTMLElement).queryByText("Engine")).toBeNull();
    const cell = table.querySelector("td.summary-metric-cell")!;
    expect(cell.querySelector(".summary-metric-track")?.parentElement).toBe(
      cell.querySelector("div"),
    );
  });

  it("shows Throughput@Size beside the geomean for a throughput cohort", () => {
    const summary = makeSummary({
      phase: "throughput",
      stream_count: 3,
      ranking: { primary_metric: "throughput_at_size", secondary_metric: "display_geomean_ms", primary_order: "desc" },
      platforms: [
        makePlatform({ result_id: "r1", platform: "Spark", power_score: null, throughput_at_size: 3741 }),
        makePlatform({
          result_id: "r2",
          platform_id: "duckdb",
          platform: "DuckDB",
          power_score: null,
          throughput_at_size: 1500,
        }),
      ],
    });
    const { container } = renderOverview(summary);
    const table = container.querySelector("table.summary-metric-table") as HTMLElement;

    expect(within(table).getByText("Throughput@Size")).not.toBeNull();
    expect(within(table).queryByText("Power@Size")).toBeNull();
    expect(within(table).getByText("3,741")).not.toBeNull();
    expect(within(table).getByText("1,500")).not.toBeNull();
  });

  it("keeps Power@Size for a power cohort", () => {
    const { container } = renderOverview();
    const table = container.querySelector("table.summary-metric-table") as HTMLElement;

    expect(within(table).getByText("Power@Size")).not.toBeNull();
    expect(within(table).queryByText("Throughput@Size")).toBeNull();
  });

  it("states the distribution boundary once and does not link exclusions to the provenance legend", () => {
    renderOverview();
    expect(screen.queryByText(/whiskers min\/max/)).toBeNull();
    const paragraphs = screen
      .getAllByText(/across different queries, not repeated runs/)
      .filter((el) => el.tagName === "P");
    expect(paragraphs).toHaveLength(1);
    const matrixLink = screen.getByTestId("see-per-query-matrix-link") as HTMLAnchorElement;
    expect(screen.getByTestId("summary-chart-full-query_heatmap").textContent).toBe("");
    fireEvent.click(matrixLink);
    expect(screen.getByTestId("summary-chart-full-query_heatmap").textContent).not.toBe("");
    expect(screen.queryByText(/Inspect exclusions/)).toBeNull();
  });

  it("drops the preview once the full chart is open, so the chart is drawn once", () => {
    const { container } = renderOverview();
    const card = container.querySelector<HTMLDetailsElement>("[data-testid^='summary-chart-preview-']")!;
    expect(card.querySelector(".summary-chart-thumbnail")).not.toBeNull();
    card.open = true;
    fireEvent(card, new Event("toggle"));
    expect(card.querySelector(".summary-chart-thumbnail")).toBeNull();
  });

  it("labels the more-views count from the rendered cards", () => {
    const { container } = renderOverview();
    const cards = container.querySelectorAll("[data-testid^='summary-chart-preview-']");
    expect(cards.length).toBeGreaterThan(0);
    expect(screen.getByText(`${cards.length} additional analyses`)).not.toBeNull();
  });

  it("omits the heatmap card when the matrix already shows it", () => {
    const { container } = render(
      <SummaryChartOverview context={{ kind: "summary", summary: makeSummary() }} excludeChartIds={["query_heatmap"]} />,
    );
    expect(container.querySelector("[data-testid='summary-chart-preview-query_heatmap']")).toBeNull();
    const cards = container.querySelectorAll("[data-testid^='summary-chart-preview-']");
    expect(screen.getByText(`${cards.length} additional analyses`)).not.toBeNull();
  });

  it("formats the analyses count label", async () => {
    const { additionalAnalysesLabel } = await import("@/components/SummaryChartOverview");
    expect(additionalAnalysesLabel(0)).toBe("0 additional analyses");
    expect(additionalAnalysesLabel(1)).toBe("1 additional analysis");
    expect(additionalAnalysesLabel(7)).toBe("7 additional analyses");
  });

});
