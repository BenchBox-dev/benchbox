import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { ChartPanel } from "@/components/ChartPanel";
import { QueryHistogram } from "@/components/QueryHistogram";
import { applicableCharts, buildRenderableSummary, CHART_REGISTRY, type ChartHistoricalEntry } from "@/lib/chartRegistry";
import { assignRunColorSlots, paletteColor } from "@/lib/chartTheme";
import { formatPowerScore } from "@/lib/metricFormatters";
import type {
  BenchmarkSummary,
  DetailResult,
  PercentileStats,
  PlatformRow,
  RankingConfig,
} from "@/types";

const RANKING: RankingConfig = {
  primary_metric: "display_geomean_ms",
  secondary_metric: "platform",
  primary_order: "asc",
};

const PERCENTILES: PercentileStats = {
  p50: 10,
  p90: 18,
  p95: 21,
  p99: 25,
};

function makePlatformRow(overrides: Partial<PlatformRow> = {}): PlatformRow {
  const timings = overrides.timings ?? { Q1: 10, Q2: 12 };
  return {
    result_id: overrides.result_id ?? "row-1",
    short_id: overrides.short_id ?? "row-1",
    platform_id: overrides.platform_id ?? "duckdb",
    platform: overrides.platform ?? "DuckDB",
    platform_version: overrides.platform_version ?? null,
    tuning_mode: overrides.tuning_mode ?? "default",
    tuning_hash: overrides.tuning_hash ?? null,
    execution_mode: overrides.execution_mode ?? "sql",
    trust_label: overrides.trust_label ?? "maintainer-run",
    funding: overrides.funding ?? "unspecified",
    run_date: overrides.run_date ?? "2026-04-17T12:00:00Z",
    is_ranking_eligible: overrides.is_ranking_eligible ?? true,
    has_display_timing: overrides.has_display_timing ?? true,
    valid_query_count: overrides.valid_query_count ?? 2,
    missing_query_count: overrides.missing_query_count ?? 0,
    zero_timing_count: overrides.zero_timing_count ?? 0,
    display_exclusion_reason: overrides.display_exclusion_reason ?? null,
    comparison_exclusion_reason: overrides.comparison_exclusion_reason ?? null,
    ranking_exclusion_reason: overrides.ranking_exclusion_reason ?? null,
    power_score: overrides.power_score ?? 1000,
    display_geomean_ms: overrides.display_geomean_ms ?? 10,
    sample_geomean_ms: overrides.sample_geomean_ms ?? 10,
    cost_usd: overrides.cost_usd ?? 1.25,
    normalized_cost_usd: overrides.normalized_cost_usd ?? 1.25,
    cost_model_version: overrides.cost_model_version ?? "2026.05.0",
    cost_model_source: overrides.cost_model_source ?? "benchbox.core.cost.pricing",
    cost_scope: overrides.cost_scope ?? "compute_only",
    cost_status: overrides.cost_status ?? "normalized",
    billing_unit: overrides.billing_unit ?? "credit",
    pricing_region: overrides.pricing_region ?? "us-east-1",
    cloud_provider: overrides.cloud_provider ?? "aws",
    cloud_region: overrides.cloud_region ?? "us-east-1",
    instance_type: overrides.instance_type ?? null,
    warehouse_size: overrides.warehouse_size ?? "MEDIUM",
    node_count: overrides.node_count ?? null,
    cluster_size: overrides.cluster_size ?? null,
    storage_format: overrides.storage_format ?? null,
    storage_tier: overrides.storage_tier ?? null,
    compliance_class: overrides.compliance_class ?? null,
    percentile_stats: overrides.percentile_stats ?? PERCENTILES,
    phase_durations: overrides.phase_durations ?? { load: 1.2, power: 3.4 },
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
  };
}

function makeSummary(overrides: Partial<BenchmarkSummary> = {}): BenchmarkSummary {
  return {
    benchmark: overrides.benchmark ?? "nyctaxi",
    scale_factor: overrides.scale_factor ?? 0.01,
    phase: overrides.phase ?? "power",
    query_ids: overrides.query_ids ?? ["Q1", "Q2"],
    platforms:
      overrides.platforms ??
      [
        makePlatformRow(),
        makePlatformRow({
          result_id: "row-2",
          short_id: "row-2",
          platform_id: "sqlite",
          platform: "SQLite",
          display_geomean_ms: 18,
          sample_geomean_ms: 18,
          power_score: 650,
          cost_usd: 1.9,
          timings: { Q1: 18, Q2: 22 },
        }),
      ],
    cell_reduction: overrides.cell_reduction ?? "median",
    ranking: overrides.ranking ?? RANKING,
  };
}

function makeHistoricalEntry(
  overrides: Partial<ChartHistoricalEntry> = {},
): ChartHistoricalEntry {
  return {
    result_id: overrides.result_id ?? "hist-1",
    benchmark: overrides.benchmark ?? "nyctaxi",
    scale_factor: overrides.scale_factor ?? 0.01,
    platform: overrides.platform ?? "DuckDB",
    platform_id: overrides.platform_id ?? "duckdb",
    run_date: overrides.run_date ?? "2026-04-17T12:00:00Z",
    power_score: overrides.power_score ?? null,
    display_geomean_ms: overrides.display_geomean_ms ?? 10,
  };
}

function makeDetail(overrides: Partial<DetailResult> = {}): DetailResult {
  return {
    result_id: overrides.result_id ?? "detail-1",
    benchmark: overrides.benchmark ?? "nyctaxi",
    scale_factor: overrides.scale_factor ?? 0.01,
    platform: overrides.platform ?? "DuckDB",
    platform_id: overrides.platform_id ?? "duckdb",
    driver_version: overrides.driver_version ?? null,
    run_date: overrides.run_date ?? "2026-04-17T12:00:00Z",
    total_duration_s: overrides.total_duration_s ?? 10,
    geomean_ms: overrides.geomean_ms ?? 10,
    display_geomean_ms: overrides.display_geomean_ms ?? 10,
    power_score: overrides.power_score ?? 1000,
    has_display_timing: overrides.has_display_timing ?? true,
    valid_query_count: overrides.valid_query_count ?? 2,
    missing_query_count: overrides.missing_query_count ?? 0,
    zero_timing_count: overrides.zero_timing_count ?? 0,
    display_exclusion_reason: overrides.display_exclusion_reason ?? null,
    comparison_exclusion_reason: overrides.comparison_exclusion_reason ?? null,
    ranking_exclusion_reason: overrides.ranking_exclusion_reason ?? null,
    environment: overrides.environment ?? { os: "macOS" },
    queries:
      overrides.queries ?? [
        { query_id: "Q1", duration_ms: 10, status: "pass", run_type: null, iter: 1, stream: null },
        { query_id: "Q2", duration_ms: 12, status: "pass", run_type: null, iter: 1, stream: null },
      ],
    display_timings:
      overrides.display_timings ?? [
        { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        { query_id: "Q2", display_ms: 12, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      ],
    has_plans: overrides.has_plans ?? false,
    has_tuning: overrides.has_tuning ?? false,
    bundle_download_url: overrides.bundle_download_url ?? "/bundle.json",
    trust_label: overrides.trust_label ?? "maintainer-run",
    visibility: overrides.visibility ?? "public-curated",
    funding: overrides.funding ?? "unspecified",
    platform_version: overrides.platform_version ?? null,
    execution_mode: overrides.execution_mode ?? "sql",
    tuning_mode: overrides.tuning_mode ?? "default",
    tuning_hash: overrides.tuning_hash ?? null,
    test_type: overrides.test_type ?? "power",
    validation_status: overrides.validation_status ?? "exact",
    cost_usd: overrides.cost_usd ?? 1.25,
    normalized_cost_usd: overrides.normalized_cost_usd ?? 1.25,
    cost_model_version: overrides.cost_model_version ?? "2026.05.0",
    cost_model_source: overrides.cost_model_source ?? "benchbox.core.cost.pricing",
    cost_scope: overrides.cost_scope ?? "compute_only",
    cost_status: overrides.cost_status ?? "normalized",
    billing_unit: overrides.billing_unit ?? "credit",
    pricing_region: overrides.pricing_region ?? "us-east-1",
    cloud_provider: overrides.cloud_provider ?? "aws",
    cloud_region: overrides.cloud_region ?? "us-east-1",
    instance_type: overrides.instance_type ?? null,
    warehouse_size: overrides.warehouse_size ?? "MEDIUM",
    node_count: overrides.node_count ?? null,
    cluster_size: overrides.cluster_size ?? null,
    storage_format: overrides.storage_format ?? null,
    storage_tier: overrides.storage_tier ?? null,
    compliance_class: overrides.compliance_class ?? null,
  };
}

describe("ChartPanel", () => {
  it("shows the latest run age in summary-only history", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: null,
          historical: [
            makeHistoricalEntry(),
            makeHistoricalEntry({ result_id: "hist-2", run_date: "2026-04-18T12:00:00Z" }),
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(screen.getByText("Latest run").parentElement?.querySelector("button")?.getAttribute("aria-label")).toMatch(/2026-04-18.*days ago/);
  });

  it("groups summary charts by analytical question", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary(),
          historical: [
            makeHistoricalEntry(),
            makeHistoricalEntry({ result_id: "hist-2", run_date: "2026-04-18T12:00:00Z" }),
          ],
        }}
      />,
    );

    expect(screen.getByRole("tablist", { name: "Chart question groups" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toStrictEqual([
      "Overview",
      "Per-query",
      "Distribution",
      "Cost",
      "Trend",
      "Rank",
    ]);
    expect(screen.getByRole("button", { name: "Geomean and cost by run" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Per-query latency heatmap" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Comparison by run" })).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Trend" }));

    expect(screen.getByRole("tab", { name: "Trend" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("img", { name: "Geomean latency trend over time" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Geomean and cost by run" })).toBeNull();
  });

  it("moves through chart question tabs with arrows, Home, and End", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary(),
          historical: [makeHistoricalEntry(), makeHistoricalEntry({ result_id: "hist-2" })],
        }}
      />,
    );

    const overview = screen.getByRole("tab", { name: "Overview" });
    overview.focus();
    fireEvent.keyDown(overview, { key: "ArrowRight" });
    const perQuery = screen.getByRole("tab", { name: "Per-query" });
    expect(perQuery).toHaveAttribute("aria-selected", "true");
    expect(perQuery).toHaveAttribute("tabindex", "0");
    expect(document.activeElement).toBe(perQuery);

    fireEvent.keyDown(perQuery, { key: "End" });
    const rank = screen.getByRole("tab", { name: "Rank" });
    expect(document.activeElement).toBe(rank);
    expect(rank).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(rank, { key: "Home" });
    expect(document.activeElement).toBe(overview);
    expect(overview).toHaveAttribute("aria-selected", "true");
  });

  it("opens a summary cohort on a chart rather than on the sparkline table", () => {
    render(<ChartPanel context={{ kind: "summary", summary: makeSummary() }} />);

    expect(screen.getByRole("img", { name: /performance comparison/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Geomean latency by run" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
  });

  it("records the open chart in the URL so the view can be shared", () => {
    render(<ChartPanel context={{ kind: "summary", summary: makeSummary() }} />);

    fireEvent.click(screen.getByRole("button", { name: "Geomean and cost by run" }));

    expect(new URLSearchParams(window.location.search).get("chart")).toBe("sparkline_table");
  });

  it("restores the chart named in the URL", () => {
    window.history.replaceState(null, "", "/?chart=stacked_phase");

    render(<ChartPanel context={{ kind: "summary", summary: makeSummary() }} />);

    expect(screen.getByRole("button", { name: "Time by phase" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
  });

  it("canonicalises a chart id that does not apply to this cohort", async () => {
    window.history.replaceState(null, "", "/?chart=comparison_bar");

    render(<ChartPanel context={{ kind: "summary", summary: makeSummary() }} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Geomean latency by run" }).getAttribute("aria-pressed")).toBe(
        "true",
      );
    });
    expect(new URLSearchParams(window.location.search).get("chart")).toBeNull();
  });

  it("leaves the route's own parameters alone", () => {
    window.history.replaceState(null, "", "/results/tpch/?sf=1&phase=power");

    render(<ChartPanel context={{ kind: "summary", summary: makeSummary() }} />);
    fireEvent.click(screen.getByRole("button", { name: "Geomean and cost by run" }));

    const params = new URLSearchParams(window.location.search);
    expect(params.get("sf")).toBe("1");
    expect(params.get("phase")).toBe("power");
    expect(params.get("chart")).toBe("sparkline_table");
  });

  it("hides charts whose ids are listed in excludeChartIds", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary(),
          historical: [
            makeHistoricalEntry(),
            makeHistoricalEntry({ result_id: "hist-2", run_date: "2026-04-18T12:00:00Z" }),
          ],
        }}
        excludeChartIds={["query_heatmap"]}
      />,
    );

    expect(screen.getByRole("tab", { name: "Per-query" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Per-query latency heatmap" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Per-query" }));
    expect(screen.queryByRole("button", { name: "Per-query latency heatmap" })).toBeNull();
    expect(screen.getByRole("tabpanel", { name: "Per-query" })).toBeTruthy();
  });

  it("threads cohort-aware labels into Compare per-query charts", () => {
    const details = [
      makeDetail({
        result_id: "df-44",
        platform: "DataFusion",
        platform_id: "datafusion-44",
        platform_version: "44",
        display_timings: [
          { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 30, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
      makeDetail({
        result_id: "df-45",
        platform: "DataFusion",
        platform_id: "datafusion-45",
        platform_version: "45",
        display_timings: [
          { query_id: "Q1", display_ms: 12, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 15, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
    ];
    const { container } = render(
      <ChartPanel context={{ kind: "compare", results: details, primaryMetric: "display_geomean_ms" }} />,
    );

    expect(screen.getAllByText("DataFusion v44").length).toBeGreaterThan(0);
    expect(screen.getAllByText("DataFusion v45").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Comparison by run" }));
    const tooltips = Array.from(container.querySelectorAll("rect title")).map((title) => title.textContent ?? "");
    expect(tooltips.some((text) => text.includes("DataFusion v44"))).toBe(true);
    expect(tooltips.some((text) => text.includes("DataFusion v45"))).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Per-query comparison vs baseline" }));
    expect(screen.queryByRole("img", { name: "Diverging bar chart" })).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Difference (%)" }));
    const chart = screen.getByRole("img", { name: "Diverging bar chart" });
    expect(chart.getAttribute("aria-describedby")).toBe("diverging-bar-description");
    expect(screen.getByText(/An accessible table follows the chart/)).toBeTruthy();
    expect(screen.getByRole("table", { name: /Per-query percentage changes relative to DataFusion v44/ })).toBeTruthy();
  });

  it("shows one per-query figure whose Chart view control swaps speedup and difference", () => {
    const timings = (first: number, second: number) => [
      { query_id: "Q1", display_ms: first, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      { query_id: "Q2", display_ms: second, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
    ];
    const { container } = render(
      <ChartPanel
        summaryLayout="long"
        context={{
          kind: "compare",
          results: [
            makeDetail({
              result_id: "duck-1",
              platform: "DuckDB",
              platform_id: "duckdb",
              display_geomean_ms: 10,
              geomean_ms: 10,
              display_timings: timings(10, 20),
            }),
            makeDetail({
              result_id: "sqlite-1",
              platform: "SQLite",
              platform_id: "sqlite",
              display_geomean_ms: 18,
              geomean_ms: 18,
              display_timings: timings(18, 22),
            }),
          ],
        }}
      />,
    );

    const viewControl = screen.getByRole("radiogroup", { name: "Chart view" });
    expect(viewControl).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Speedup" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("img", { name: /Per-query speedups relative to DuckDB/ })).toBeTruthy();
    expect(screen.queryByRole("img", { name: "Diverging bar chart" })).toBeNull();
    expect(container.querySelector("[data-chart-id='diverging_bar']")).toBeNull();

    fireEvent.click(screen.getByRole("radio", { name: "Difference (%)" }));
    expect(screen.getByRole("radio", { name: "Difference (%)" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("img", { name: "Diverging bar chart" })).toBeTruthy();
    expect(screen.queryByRole("img", { name: /Per-query speedups relative to/ })).toBeNull();
    expect(screen.getAllByRole("radiogroup", { name: "Chart view" })).toHaveLength(1);
  });

  it("uses responsive segmented chart controls with short visible labels", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary(),
          historical: [
            makeHistoricalEntry(),
            makeHistoricalEntry({ result_id: "hist-2", run_date: "2026-04-18T12:00:00Z" }),
          ],
        }}
      />,
    );

    const groupTabs = screen.getByRole("tablist", { name: "Chart question groups" });
    expect(groupTabs.className).toContain("flex");
    expect(groupTabs.className).toContain("flex-wrap");
    expect(screen.getByRole("tab", { name: "Per-query" }).className).toContain("min-h-9");

    const chartControls = screen.getByRole("group", { name: "Overview charts" });
    expect(chartControls.className).toContain("flex");
    expect(chartControls.className).toContain("flex-wrap");

    const sparkline = screen.getByRole("button", { name: "Geomean and cost by run" });
    const stacked = screen.getByRole("button", { name: "Time by phase" });
    expect(sparkline.textContent).toBe("Sparklines");
    expect(stacked.textContent).toBe("Phases");
    expect(sparkline.className).toContain("min-h-9");
    expect(stacked.className).toContain("min-h-9");

    fireEvent.click(screen.getByRole("button", { name: "Geomean latency by run" }));
    expect(screen.getByRole("button", { name: "Geomean latency by run" }).className).toContain("min-h-9");
  });

  it("uses log scale for performance bars when latency spans an order of magnitude", () => {
    const { container } = render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary({
            platforms: [
              makePlatformRow({ result_id: "fast", platform: "FastDB", display_geomean_ms: 10 }),
              makePlatformRow({ result_id: "middle", platform: "MidDB", display_geomean_ms: 100 }),
              makePlatformRow({ result_id: "slow", platform: "SlowDB", display_geomean_ms: 1000 }),
            ],
          }),
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Geomean latency by run" }));

    expect(screen.getByRole("img", { name: "Geomean performance comparison (log scale)" })).toBeTruthy();
    expect(screen.getByText("Geomean query time (log scale) - lower is better")).toBeTruthy();

    const widths = Array.from(
      container.querySelectorAll('svg[aria-label="Geomean performance comparison (log scale)"] rect'),
    ).map((rect) => Number(rect.getAttribute("width")));
    expect(widths).toHaveLength(3);
    expect(widths[0]).toBeGreaterThan(2);
    expect(widths[0]).toBeLessThan(widths[1]!);
    expect(widths[1]!).toBeLessThan(widths[2]!);
  });

  it("places performance labels away from cramped bar edges and keeps tooltip values", () => {
    const { container } = render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary({
            platforms: [
              makePlatformRow({ result_id: "fast", platform: "FastDB", display_geomean_ms: 10 }),
              makePlatformRow({ result_id: "slow", platform: "SlowDB", display_geomean_ms: 1000 }),
            ],
          }),
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Geomean latency by run" }));

    const valueLabels = Array.from(container.querySelectorAll("text[data-value-placement]"));
    expect(valueLabels.map((label) => label.getAttribute("data-value-placement"))).toContain("outside");
    expect(valueLabels.map((label) => label.getAttribute("data-value-placement"))).toContain("inside");
    expect(
      valueLabels.find((label) => label.textContent === "1 s")?.getAttribute("data-value-placement"),
    ).toBe("inside");

    const tooltips = Array.from(container.querySelectorAll("rect title")).map((title) => title.textContent);
    expect(tooltips.some((tooltip) => tooltip?.includes("FastDB") && tooltip.includes("10 ms"))).toBe(true);
    expect(tooltips.some((tooltip) => tooltip?.includes("SlowDB") && tooltip.includes("1 s"))).toBe(true);
  });

  it("hides the Cost tab when no cohort row carries normalized cost data", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary({
            platforms: [
              makePlatformRow({
                cost_status: "unavailable",
                normalized_cost_usd: null,
                cloud_provider: null,
                cloud_region: null,
                pricing_region: null,
                warehouse_size: null,
              }),
            ],
          }),
        }}
      />,
    );

    expect(screen.queryByRole("tab", { name: "Cost" })).toBeNull();
  });

  it("renders compare-only tabs and hides historical-only charts", () => {
    render(
      <ChartPanel
        context={{
          kind: "compare",
          results: [
            makeDetail(),
            makeDetail({
              result_id: "detail-2",
              platform: "SQLite",
              platform_id: "sqlite",
              display_geomean_ms: 18,
              geomean_ms: 18,
              display_timings: [
                { query_id: "Q1", display_ms: 18, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
                { query_id: "Q2", display_ms: 22, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
              ],
            }),
          ],
        }}
      />,
    );

    expect(screen.getByRole("button", { name: "Comparison by run" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Per-query comparison vs baseline" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Per-query latency heatmap" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Per-query" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByRole("button", { name: "Performance over time" })).toBeNull();
  });

  it("renders the shared summary table above compare charts in the long layout", () => {
    render(
      <ChartPanel
        summaryLayout="long"
        context={{
          kind: "compare",
          results: [
            makeDetail(),
            makeDetail({
              result_id: "detail-2",
              platform: "SQLite",
              platform_id: "sqlite",
              display_geomean_ms: 18,
              geomean_ms: 18,
              display_timings: [
                { query_id: "Q1", display_ms: 18, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
                { query_id: "Q2", display_ms: 22, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
              ],
            }),
          ],
          primaryMetric: "display_geomean_ms",
        }}
      />,
    );

    const table = screen.getByTestId("chart-panel-compare-summary-table");
    expect(table.textContent).toContain("Display geomean");
    expect(table.textContent).toContain("selected runs");
    expect(screen.getByTestId("chart-panel-chart-normalized_speedup")).toBeTruthy();
  });

  it("uses a controlled compare baseline without rendering a second selector", () => {
    render(
      <ChartPanel
        baselineIndex={1}
        onBaselineIndexChange={() => undefined}
        context={{
          kind: "compare",
          results: [
            makeDetail(),
            makeDetail({
              result_id: "detail-2",
              platform: "SQLite",
              platform_id: "sqlite",
              display_geomean_ms: 18,
              geomean_ms: 18,
              display_timings: [
                { query_id: "Q1", display_ms: 18, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
                { query_id: "Q2", display_ms: 22, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
              ],
            }),
          ],
        }}
      />,
    );

    expect(screen.queryByRole("combobox", { name: "Baseline" })).toBeNull();
    expect(screen.getByText("Baseline:")).toBeTruthy();
    expect(screen.getAllByText("SQLite").length).toBeGreaterThan(0);
  });

  it("preserves the compared run phase in compare-mode summary content", () => {
    render(
      <ChartPanel
        context={{
          kind: "compare",
          results: [
            makeDetail({ test_type: "throughput" }),
            makeDetail({
              result_id: "detail-2",
              platform: "SQLite",
              platform_id: "sqlite",
              display_geomean_ms: 18,
              geomean_ms: 18,
              test_type: "throughput",
              display_timings: [
                { query_id: "Q1", display_ms: 18, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
                { query_id: "Q2", display_ms: 22, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
              ],
            }),
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    fireEvent.click(screen.getByRole("button", { name: "Run summary" }));
    expect(screen.getByText("Throughput")).toBeTruthy();
    expect(screen.queryByText("throughput")).toBeNull();
  });

  it("renders detail charts without compare-only tabs", () => {
    render(
      <ChartPanel
        context={{
          kind: "detail",
          detail: makeDetail(),
        }}
      />,
    );

    expect(screen.getByRole("button", { name: "Per-query Latency" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Rank" }));
    expect(screen.getByRole("table", { name: "Per-query platform rankings (1st = fastest)" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Per-query comparison vs baseline" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Performance over time" })).toBeNull();
  });

  it("activates the Rank tab from the default Overview state on a fresh render (finding #5)", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary(),
        }}
      />,
    );

    expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getByRole("tab", { name: "Rank" }));

    expect(screen.getByRole("tab", { name: "Rank" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("false");
    expect(screen.getByRole("table", { name: "Per-query platform rankings (1st = fastest)" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Geomean and cost by run" })).toBeNull();
  });

  it("shows a rank-chart empty state when every submitted row is unrankable", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary({
            platforms: [
              makePlatformRow({
                result_id: "row-1",
                platform: "Community DuckDB",
                ranking_exclusion_reason: "trust_not_rankable",
              }),
              makePlatformRow({
                result_id: "row-2",
                platform: "Failed SQLite",
                ranking_exclusion_reason: "validation_not_clean",
              }),
            ],
          }),
        }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Rank" }));

    expect(screen.getByRole("status", { name: "Query rank summary unavailable" })).toBeTruthy();
    expect(screen.queryByRole("table", { name: "Per-query platform rankings (1st = fastest)" })).toBeNull();
    expect(screen.getByText("No ranked runs")).toBeTruthy();
    expect(screen.getByText(/Results from this source are not included in rankings/)).toBeTruthy();
    expect(screen.getByText(/Validation status excludes this result from ranking/)).toBeTruthy();
  });

  it("keeps per-query charts selectable when every timing row is display-excluded", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary({
            platforms: [
              makePlatformRow({
                result_id: "row-1",
                platform: "Zero DuckDB",
                has_display_timing: false,
                valid_query_count: 0,
                zero_timing_count: 2,
                display_exclusion_reason: "zero_timings_only",
                display_geomean_ms: null,
                sample_geomean_ms: null,
                timings: { Q1: 0, Q2: 0 },
              }),
              makePlatformRow({
                result_id: "row-2",
                platform: "Invalid SQLite",
                has_display_timing: false,
                valid_query_count: 0,
                zero_timing_count: 2,
                display_exclusion_reason: "zero_timings_only",
                display_geomean_ms: null,
                sample_geomean_ms: null,
                timings: { Q1: 0, Q2: 0 },
              }),
            ],
          }),
        }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Per-query" }));

    expect(screen.getByRole("button", { name: "Per-query latency heatmap" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Per-query Latency" })).toBeTruthy();
    expect(screen.getByRole("status", { name: "Per-query latency heatmap unavailable" })).toBeTruthy();
    expect(screen.getByText("No valid display timings")).toBeTruthy();
    expect(screen.getByText(/Only exact zero timings are available/)).toBeTruthy();
  });

  it("keeps the power chart selectable when every power row is unrankable", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary({
            ranking: { primary_metric: "power_score", secondary_metric: "display_geomean_ms", primary_order: "desc" },
            platforms: [
              makePlatformRow({
                result_id: "community-high",
                platform: "Community High",
                power_score: 9000,
                ranking_exclusion_reason: "trust_not_rankable",
              }),
              makePlatformRow({
                result_id: "failed-low",
                platform: "Failed Low",
                power_score: 1000,
                ranking_exclusion_reason: "validation_not_clean",
              }),
            ],
          }),
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Power@Size by run" }));

    expect(screen.getByRole("status", { name: "Power@Size by run unavailable" })).toBeTruthy();
    expect(screen.getByText("No ranked runs")).toBeTruthy();
    expect(screen.getByText(/Results from this source are not included in rankings/)).toBeTruthy();
    expect(screen.getByText(/Validation status excludes this result from ranking/)).toBeTruthy();
  });

  it("filters rank-unsafe rows out of power charts and discloses the exclusion", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary({
            ranking: { primary_metric: "power_score", secondary_metric: "display_geomean_ms", primary_order: "desc" },
            platforms: [
              makePlatformRow({
                result_id: "community-high",
                platform: "Community High",
                power_score: 9000,
                ranking_exclusion_reason: "trust_not_rankable",
              }),
              makePlatformRow({
                result_id: "rankable-low",
                platform: "Rankable Low",
                power_score: 1000,
              }),
            ],
          }),
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Power@Size by run" }));

    expect(screen.getByText("Rankable Low")).toBeTruthy();
    expect(screen.queryByText("Community High")).toBeNull();
    expect(screen.getByText(/1 row excluded from this chart's ranked runs dataset/)).toBeTruthy();
  });

  it("uses winner language by default in the summary box", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary(),
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Run summary" }));
    expect(screen.getByText("Best geomean")).toBeTruthy();
    expect(screen.queryByText(/Lowest geomean in ranking/)).toBeNull();
    expect(screen.queryByText(/ranking mismatch/)).toBeNull();
  });

  it("suppresses winner language in the summary box when suppressWinnerClaims is on (w18)", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary(),
        }}
        suppressWinnerClaims
        suppressionReason="benchmarks differ across results"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Run summary" }));
    expect(screen.queryByText("Best geomean")).toBeNull();
    expect(screen.getByText("Lowest geomean in ranking")).toBeTruthy();
    expect(screen.getByText("Not comparable: benchmarks differ across results")).toBeTruthy();
    expect(screen.queryByText(/ranking mismatch/)).toBeNull();
    expect(screen.queryByText(/DuckDB ·/)).toBeNull();
  });

  it("filters chartSummary queries and preserves queryFilter ranking order", () => {
    const summary = makeSummary({
      query_ids: ["Q1", "Q2", "Q3"],
      platforms: [makePlatformRow({ timings: { Q1: 10, Q2: 20, Q3: 30 } })],
    });
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary,
        }}
        queryFilter={["Q3", "Q1"]}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Per-query" }));
    expect(screen.getByText("Q3")).toBeTruthy();
    expect(screen.getByText("Q1")).toBeTruthy();
    expect(screen.queryByText("Q2")).toBeNull();
  });

  it("recomputes multi-run comparison-bar geomeans over the shared query filter", () => {
    const details = [
      makeDetail({
        result_id: "a",
        platform: "A",
        display_geomean_ms: 999,
        display_timings: [
          { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
      makeDetail({
        result_id: "b",
        platform: "B",
        display_geomean_ms: 999,
        display_timings: [
          { query_id: "Q1", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
      makeDetail({
        result_id: "c",
        platform: "C",
        display_geomean_ms: 999,
        display_timings: [
          { query_id: "Q1", display_ms: 40, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
    ];
    const { container } = render(
      <ChartPanel
        context={{ kind: "compare", results: details, primaryMetric: "display_geomean_ms" }}
        queryFilter={["Q1"]}
      />,
    );

    const titles = Array.from(container.querySelectorAll("rect title")).map((title) => title.textContent ?? "");
    expect(titles.some((title) => title.includes("A") && title.includes("10 ms"))).toBe(true);
    expect(titles.some((title) => title.includes("B") && title.includes("20 ms"))).toBe(true);
    expect(titles.some((title) => title.includes("C") && title.includes("40 ms"))).toBe(true);
    expect(titles.every((title) => !title.includes("999 ms"))).toBe(true);
    expect(screen.queryByRole("button", { name: "Latency percentiles" })).toBeNull();
  });

  it("renders an explicit no-matching-queries message when queryFilter has no matches", () => {
    const summary = makeSummary();
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary,
        }}
        queryFilter={["Q999"]}
      />,
    );
    expect(screen.getByText("No queries match the selected filter.")).toBeTruthy();
  });

  it("clears power_score and switches power ranking to geomean latency under active queryFilter", () => {
    const summary = makeSummary({
      ranking: {
        primary_metric: "power_score",
        secondary_metric: "display_geomean_ms",
        primary_order: "desc",
      },
      platforms: [
        makePlatformRow({
          result_id: "p1",
          platform: "Platform 1",
          power_score: 5000,
          display_geomean_ms: 100,
          timings: { Q1: 10, Q2: 20 },
        }),
        makePlatformRow({
          result_id: "p2",
          platform: "Platform 2",
          power_score: 1000,
          display_geomean_ms: 20,
          timings: { Q1: 5, Q2: 10 },
        }),
      ],
      query_ids: ["Q1", "Q2"],
    });

    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary,
        }}
        queryFilter={["Q1"]}
      />,
    );

    expect(screen.queryByText(/5,000/)).toBeNull();
    expect(screen.queryByRole("tab", { name: "Power" })).toBeNull();
  });

  it("hides whole-run normalized cost charts under an active queryFilter", () => {
    render(
      <ChartPanel
        context={{ kind: "summary", summary: makeSummary() }}
        queryFilter={["Q1"]}
      />,
    );
    expect(screen.queryByRole("tab", { name: "Cost" })).toBeNull();
  });

  it("uses the long summary layout without duplicating the page-owned heatmap", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary(),
          historical: [
            makeHistoricalEntry(),
            makeHistoricalEntry({ result_id: "hist-2", run_date: "2026-04-18T12:00:00Z" }),
          ],
        }}
        summaryLayout="long"
        excludeChartIds={["query_heatmap"]}
      />,
    );

    expect(screen.getByTestId("summary-chart-overview")).toBeTruthy();
    const metricTable = screen.getByRole("table", { name: "Speed and throughput by platform" });
    expect(metricTable).toBeTruthy();
    expect(within(metricTable).getByText("DuckDB")).toBeTruthy();
    expect(screen.getByTestId("summary-chart-preview-cdf_chart")).toBeTruthy();
    expect(screen.queryByTestId("summary-chart-preview-query_heatmap")).toBeNull();
    expect(screen.queryByRole("tablist", { name: "Chart question groups" })).toBeNull();
    expect(screen.queryByRole("img", { name: "Cumulative distribution of per-query latency" })).toBeNull();

    const cdfCard = screen.getByTestId("summary-chart-preview-cdf_chart");
    fireEvent.click(within(cdfCard).getByText("Open full chart ↗", { selector: "span" }));
    fireEvent(cdfCard, new Event("toggle"));

    expect(screen.getByRole("img", { name: "Cumulative distribution of per-query latency" })).toBeTruthy();
  });

  it("hides power scores for rows excluded from ranking in the long summary", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary({
            ranking: { primary_metric: "power_score", secondary_metric: "display_geomean_ms", primary_order: "desc" },
            platforms: [
              makePlatformRow({ result_id: "rankable", platform: "Rankable", power_score: 1000 }),
              makePlatformRow({
                result_id: "excluded",
                platform: "Excluded",
                power_score: 9000,
                ranking_exclusion_reason: "trust_not_rankable",
              }),
            ],
          }),
        }}
        summaryLayout="long"
      />,
    );

    const table = screen.getByRole("table", { name: "Speed and throughput by platform" });
    expect(within(table).getByText("1,000")).toBeTruthy();
    expect(within(table).queryByText("9,000")).toBeNull();
  });

  it("uses only eligible rows in long-layout thumbnails", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary({
            platforms: [
              makePlatformRow({ result_id: "rankable", platform: "Rankable", power_score: 1000 }),
              makePlatformRow({
                result_id: "excluded",
                platform: "Excluded",
                power_score: 9000,
                ranking_exclusion_reason: "trust_not_rankable",
              }),
            ],
          }),
        }}
        summaryLayout="long"
      />,
    );

    const ranks = screen.getByTestId("summary-chart-preview-rank_table");
    const grid = within(ranks).getByLabelText("Rank table thumbnail");
    expect(grid).toHaveStyle({ gridTemplateColumns: "repeat(1, minmax(0, 1fr))" });
  });

  it("keeps long-layout cost and trend thumbnails aligned with the ranking metric", () => {
    render(
      <ChartPanel
        context={{
          kind: "summary",
          summary: makeSummary({
            ranking: { primary_metric: "power_score", secondary_metric: "display_geomean_ms", primary_order: "desc" },
            platforms: [
              makePlatformRow({
                result_id: "high-power",
                platform: "High power",
                power_score: 9000,
                display_geomean_ms: 100,
                normalized_cost_usd: 1,
              }),
              makePlatformRow({
                result_id: "low-power",
                platform: "Low power",
                power_score: 1000,
                display_geomean_ms: 10,
                normalized_cost_usd: 2,
              }),
            ],
          }),
          historical: [
            makeHistoricalEntry({ result_id: "hist-1", run_date: "2026-04-17T12:00:00Z", power_score: 1000 }),
            makeHistoricalEntry({ result_id: "hist-2", run_date: "2026-04-18T12:00:00Z", power_score: 2000 }),
          ],
        }}
        summaryLayout="long"
      />,
    );

    const cost = screen.getByTestId("summary-chart-preview-cost_scatter");
    const highPowerDot = within(cost).getByTitle("High power") as HTMLElement;
    const lowPowerDot = within(cost).getByTitle("Low power") as HTMLElement;
    expect(Number.parseFloat(highPowerDot.style.top)).toBeLessThan(Number.parseFloat(lowPowerDot.style.top));

    const trend = within(screen.getByTestId("summary-chart-preview-time_series")).getByRole("img", {
      name: "Trend thumbnail",
    });
    expect(trend.querySelector("path")?.getAttribute("d")).toContain("M12.0,72.0 L228.0,10.0");
  });

  it("renders every compare chart openly with no tab gating in the long layout", () => {
    const { container } = render(
      <ChartPanel
        summaryLayout="long"
        context={{
          kind: "compare",
          results: [
            makeDetail(),
            makeDetail({
              result_id: "detail-2",
              platform: "SQLite",
              platform_id: "sqlite",
              display_geomean_ms: 18,
              geomean_ms: 18,
              display_timings: [
                { query_id: "Q1", display_ms: 18, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
                { query_id: "Q2", display_ms: 22, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
              ],
            }),
          ],
        }}
      />,
    );

    expect(screen.getByTestId("chart-panel-long")).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    expect(screen.queryByRole("heading", { name: "What does this comparison show?" })).toBeNull();
    expect(screen.queryByText("Headline metrics")).toBeNull();
    expect(screen.getByTestId("chart-panel-chart-comparison_bar")).not.toHaveTextContent(
      "How does each query compare with the baseline?",
    );
    expect(screen.getByTestId("chart-panel-chart-query_heatmap")).not.toHaveTextContent(
      "Which queries drive the difference?",
    );
    expect(screen.queryByText("Top-line metric summaries and phase composition")).toBeNull();
    expect(
      screen.queryByText("Paired side-by-side bars comparing two runs per query with % change annotations"),
    ).toBeNull();
    expect(container.querySelector("h3.sr-only")).toBeNull();
    expect(screen.getByTestId("chart-panel-group-overview")).toBeTruthy();
    expect(screen.getByTestId("chart-panel-group-distribution")).toBeTruthy();
    expect(screen.getByTestId("chart-panel-group-per_query")).toBeTruthy();
    const groupOrder = Array.from(container.querySelectorAll("[data-testid^='chart-panel-group-']"))
      .map((group) => group.getAttribute("data-testid"));
    expect(groupOrder.indexOf("chart-panel-group-distribution")).toBeLessThan(
      groupOrder.indexOf("chart-panel-group-per_query"),
    );
    const chartFigures = Array.from(container.querySelectorAll("[data-testid^='chart-panel-chart-']"));
    expect(chartFigures.length).toBeGreaterThan(1);
    for (const figure of chartFigures) {
      expect(figure.querySelector("h3")?.textContent?.trim().length).toBeGreaterThan(0);
    }
    expect(screen.getByTestId("chart-panel-chart-comparison_bar")).toBeTruthy();
    expect(screen.getByTestId("chart-panel-chart-query_heatmap")).toBeTruthy();
    expect(container.querySelectorAll("[data-chart-container]").length).toBeGreaterThan(1);
    expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
  });

  it("gives every figure a title without adding internal captions to summary_box or sparklines", () => {
    render(
      <ChartPanel
        summaryLayout="long"
        context={{
          kind: "compare",
          results: [makeDetail(), makeDetail({ result_id: "detail-2" })],
        }}
      />,
    );

    const summaryBox = screen.getByTestId("chart-panel-chart-summary_box");
    expect(summaryBox.querySelector("h4")).toBeNull();
    expect(summaryBox.querySelector("p")).toBeNull();
    expect(within(summaryBox).getByRole("heading", { level: 3, name: "Run summary" })).toBeTruthy();

    const sparklineTable = screen.getByTestId("chart-panel-chart-sparkline_table");
    expect(sparklineTable.querySelector("h4")).toBeNull();
    expect(sparklineTable.querySelector("p")).toBeNull();
    expect(within(sparklineTable).getByRole("heading", { level: 3, name: "Geomean and cost by run" })).toBeTruthy();
  });

  it("honors excludeChartIds in the long layout", () => {
    render(
      <ChartPanel
        summaryLayout="long"
        excludeChartIds={["performance_bar", "power_bar"]}
        context={{
          kind: "compare",
          results: [makeDetail(), makeDetail({ result_id: "detail-2" })],
        }}
      />,
    );

    expect(screen.getByTestId("chart-panel-long")).toBeTruthy();
    expect(screen.queryByTestId("chart-panel-chart-performance_bar")).toBeNull();
    expect(screen.queryByTestId("chart-panel-chart-power_bar")).toBeNull();
    expect(screen.getByTestId("chart-panel-chart-sparkline_table")).toBeTruthy();
    expect(screen.getByTestId("chart-panel-chart-comparison_bar")).toBeTruthy();
  });

  it("suppresses winner language across openly rendered compare charts when suppressWinnerClaims is on", () => {
    render(
      <ChartPanel
        summaryLayout="long"
        suppressWinnerClaims
        suppressionReason="benchmarks differ across results"
        context={{
          kind: "compare",
          results: [makeDetail(), makeDetail({ result_id: "detail-2" })],
        }}
      />,
    );

    expect(screen.queryByText("Best geomean")).toBeNull();
    expect(screen.getByText("Lowest geomean in ranking")).toBeTruthy();
    expect(screen.getByText("Not comparable: benchmarks differ across results")).toBeTruthy();
    expect(screen.queryByText(/ranking mismatch/)).toBeNull();
  });

  it("uses a controlled compare baseline without rendering a second selector in the long layout", () => {
    render(
      <ChartPanel
        summaryLayout="long"
        baselineIndex={1}
        onBaselineIndexChange={() => undefined}
        context={{
          kind: "compare",
          results: [
            makeDetail(),
            makeDetail({
              result_id: "detail-2",
              platform: "SQLite",
              platform_id: "sqlite",
              display_geomean_ms: 18,
              geomean_ms: 18,
              display_timings: [
                { query_id: "Q1", display_ms: 18, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
                { query_id: "Q2", display_ms: 22, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
              ],
            }),
          ],
        }}
      />,
    );

    expect(screen.getByTestId("chart-panel-long")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Baseline" })).toBeNull();
    expect(screen.queryByRole("tablist")).toBeNull();
  });

});

describe("buildRenderableSummary cost estimates", () => {
  it("keeps timing-derived cost estimates on compared runs", () => {
    const estimated: DetailResult = {
      ...makeDetail({ result_id: "tpch-snowflake-sf10.0-20261003-f0835929" }),
      cost_status: "estimated",
      normalized_cost_usd: null,
      estimated_cost_usd: 0.015589,
      cost_estimate_method: "sum of per-query median runtime hours × warehouse credits per hour × USD per credit",
      cost_estimate_pricing_date: "2026-10-09",
    };
    const metered = makeDetail({ result_id: "tpch-bigquery-sf10.0-20261003-d3495b28" });
    const summary = buildRenderableSummary({
      kind: "compare",
      results: [estimated, metered],
      primaryMetric: "display_geomean_ms",
    });
    const row = summary?.platforms.find((platform) => platform.result_id === estimated.result_id);
    expect(row?.cost_status).toBe("estimated");
    expect(row?.estimated_cost_usd).toBe(0.015589);
    expect(row?.cost_estimate_pricing_date).toBe("2026-10-09");
  });
});

describe("ChartPanel summary presentation", () => {
  it("gives each summary chart a reader-facing title", () => {
    const titles = Object.fromEntries(CHART_REGISTRY.map((entry) => [entry.id, entry.title]));
    expect(titles).toMatchObject({
      summary_box: "Run summary",
      sparkline_table: "Geomean and cost by run",
      cdf_chart: "Cumulative latency distribution",
      distribution_box: "Latency distribution",
      rank_table: "Query rank summary",
      query_heatmap: "Per-query latency heatmap",
      performance_bar: "Geomean latency by run",
      power_bar: "Power@Size by run",
      percentile_ladder: "Latency percentiles",
      stacked_phase: "Time by phase",
      cost_scatter: "Cost vs performance",
      time_series: "Performance over time",
      comparison_bar: "Comparison by run",
    });
  });

  it("renders the compare summary table outside the chart card so it is not nested in a second card", () => {
    render(
      <ChartPanel
        summaryLayout="long"
        context={{
          kind: "compare",
          results: [
            makeDetail(),
            makeDetail({
              result_id: "detail-2",
              platform: "SQLite",
              platform_id: "sqlite",
              display_geomean_ms: 18,
              geomean_ms: 18,
            }),
          ],
          primaryMetric: "display_geomean_ms",
        }}
      />,
    );

    const longCard = screen.getByTestId("chart-panel-long");
    const table = screen.getByTestId("chart-panel-compare-summary-table");
    expect(longCard.contains(table)).toBe(true);
    expect(table.closest(".card")).toBe(longCard);
    expect(table.querySelectorAll(".card")).toHaveLength(0);
    expect(document.querySelectorAll(".card")).toHaveLength(1);
  });

  it("labels the sample count in the run summary as Samples", () => {
    render(<ChartPanel summaryLayout="long" context={{ kind: "detail", detail: makeDetail() }} />);
    const summaryFigure = screen.getByTestId("chart-panel-chart-summary_box");

    expect(within(summaryFigure).getByText("Samples")).toBeTruthy();
    expect(screen.queryByText("Median samples")).toBeNull();
  });

  it("shows the best power value even when the best run is not rank-safe, with a not ranked note", () => {
    const summary = makeSummary({
      ranking: { ...RANKING, primary_metric: "power_score", primary_order: "desc" },
      platforms: [
        makePlatformRow({ power_score: 1000 }),
        makePlatformRow({
          result_id: "row-snowflake",
          short_id: "row-snowflake",
          platform_id: "snowflake",
          platform: "Snowflake",
          power_score: 29876,
          ranking_exclusion_reason: "trust_not_rankable",
        }),
      ],
    });
    render(<ChartPanel context={{ kind: "summary", summary }} />);
    fireEvent.click(screen.getByRole("button", { name: "Run summary" }));

    const bestCard = screen.getByText("Best power").parentElement as HTMLElement;
    expect(bestCard.textContent).toContain("Snowflake");
    expect(bestCard.textContent).toContain(formatPowerScore(29876).valueText);
    expect(bestCard.textContent).toContain("not ranked");
    expect(bestCard.textContent).not.toContain("-");
  });

  it("keeps the best card free of a not ranked note when the best run is rank-safe", () => {
    const summary = makeSummary({
      ranking: { ...RANKING, primary_metric: "power_score", primary_order: "desc" },
      platforms: [makePlatformRow({ power_score: 1000 })],
    });
    render(<ChartPanel context={{ kind: "summary", summary }} />);
    fireEvent.click(screen.getByRole("button", { name: "Run summary" }));

    const bestCard = screen.getByText("Best power").parentElement as HTMLElement;
    expect(bestCard.textContent).not.toContain("not ranked");
  });
});

describe("ChartPanel run colours and run summary wording", () => {
  const rankedRuns = [
    makePlatformRow({ result_id: "zeta", platform_id: "zeta", platform: "Zeta", display_geomean_ms: 50, timings: { Q1: 50, Q2: 52 } }),
    makePlatformRow({ result_id: "alpha", platform_id: "alpha", platform: "Alpha", display_geomean_ms: 10, timings: { Q1: 10, Q2: 12 } }),
    makePlatformRow({ result_id: "mid", platform_id: "mid", platform: "Mid", display_geomean_ms: 30, timings: { Q1: 30, Q2: 33 } }),
  ];

  const runColorSlots = assignRunColorSlots(rankedRuns);

  function barFillFor(container: Element, runLabel: string) {
    return Array.from(container.querySelectorAll("rect")).find((rect) =>
      rect.querySelector("title")?.textContent?.startsWith(runLabel),
    )?.getAttribute("fill");
  }

  function cdfStrokeFor(container: Element, runLabel: string) {
    return Array.from(container.querySelectorAll("path")).find((path) =>
      path.querySelector("title")?.textContent?.startsWith(runLabel),
    )?.getAttribute("stroke");
  }

  it("keeps each run's colour on the ranking bars, box plot and CDF when the bars sort by geomean", () => {
    const { container } = render(
      <ChartPanel context={{ kind: "summary", summary: makeSummary({ platforms: rankedRuns }) }} />,
    );

    const alphaColor = paletteColor(runColorSlots.get("alpha")!);
    expect(barFillFor(container, "Alpha")).toBe(alphaColor);
    expect(barFillFor(container, "Zeta")).toBe(paletteColor(runColorSlots.get("zeta")!));

    fireEvent.click(screen.getByRole("tab", { name: "Distribution" }));
    fireEvent.click(screen.getByRole("button", { name: "Latency distribution" }));
    expect(fillForBoxLabel(container, "Alpha")).toBe(alphaColor);

    fireEvent.click(screen.getByRole("button", { name: "Cumulative latency distribution" }));
    expect(cdfStrokeFor(container, "Alpha")).toBe(alphaColor);
  });

  it("shows Not comparable with the reason instead of naming a highest value when runs differ in phase", () => {
    render(
      <ChartPanel
        context={{ kind: "summary", summary: makeSummary({ platforms: rankedRuns }) }}
        suppressWinnerClaims
        suppressionReason="phases differ"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Run summary" }));

    expect(screen.getByText("Not comparable: phases differ")).toBeTruthy();
    expect(screen.queryByText(/Zeta ·|Alpha ·|Mid ·/)).toBeNull();
    expect(screen.queryByText(/ranking mismatch/)).toBeNull();
  });

  it("sentence-cases the phase in the run summary", () => {
    render(
      <ChartPanel
        context={{ kind: "summary", summary: makeSummary({ phase: "throughput", platforms: rankedRuns }) }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Run summary" }));

    const phaseCard = screen.getByText("Phase").parentElement as HTMLElement;
    expect(phaseCard.textContent).toContain("Throughput");
    expect(phaseCard.textContent).not.toContain("throughput");
  });
});

function fillForBoxLabel(scope: Element, runLabel: string) {
  const label = Array.from(scope.querySelectorAll("text")).find((node) => node.textContent?.endsWith(runLabel));
  return label?.parentElement?.querySelector("rect")?.getAttribute("fill") ?? null;
}

describe("run colours follow the platform across compare pages", () => {
  function compareTimings(displayMs: number) {
    return [
      { query_id: "Q1", display_ms: displayMs, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      { query_id: "Q2", display_ms: displayMs + 2, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
    ];
  }

  function snowflakeRun(resultId: string, displayMs: number, platform = "Snowflake", platformId = "snowflake") {
    return makeDetail({
      result_id: resultId,
      platform,
      platform_id: platformId,
      display_geomean_ms: displayMs,
      geomean_ms: displayMs,
      display_timings: compareTimings(displayMs),
    });
  }

  function markFillFor(container: Element, runLabel: string) {
    return Array.from(container.querySelectorAll("path")).find((path) =>
      path.querySelector("title")?.textContent?.startsWith(runLabel),
    )?.getAttribute("fill");
  }

  function legendFillFor(container: Element, runLabel: string) {
    const legend = container.querySelector('[aria-label="Run legend"]') as HTMLElement;
    return Array.from(legend.querySelectorAll("span"))
      .find((span) => span.textContent?.includes(runLabel))
      ?.querySelector("path")
      ?.getAttribute("fill");
  }

  it("gives Snowflake the same bar colour in two compare selections with different run orders", () => {
    const first = render(
      <ChartPanel
        context={{
          kind: "compare",
          results: [
            makeDetail({ result_id: "duck-a", platform: "DuckDB", platform_id: "duckdb", display_geomean_ms: 8, geomean_ms: 8, display_timings: compareTimings(8) }),
            snowflakeRun("snow-a", 12),
            makeDetail({ result_id: "polars-a", platform: "Polars", platform_id: "polars", display_geomean_ms: 20, geomean_ms: 20, display_timings: compareTimings(20) }),
          ],
          primaryMetric: "display_geomean_ms",
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Comparison by run" }));
    const inFirstPage = barFillForRun(first.container, "Snowflake");
    first.unmount();

    const second = render(
      <ChartPanel
        context={{
          kind: "compare",
          results: [
            snowflakeRun("snow-b", 6),
            makeDetail({ result_id: "databricks-b", platform: "Databricks", platform_id: "databricks", display_geomean_ms: 5, geomean_ms: 5, display_timings: compareTimings(5) }),
          ],
          primaryMetric: "display_geomean_ms",
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Comparison by run" }));
    const inSecondPage = barFillForRun(second.container, "Snowflake");

    expect(inFirstPage).toBeDefined();
    expect(inSecondPage).toBe(inFirstPage);
    expect(inFirstPage).toBe(paletteColor(assignRunColorSlots([{ result_id: "snow-a", platform_id: "snowflake", platform: "Snowflake" }]).get("snow-a")!));
  });

  it("shows each run's legend swatch in the same colour as its marks", () => {
    const summary = buildRenderableSummary({
      kind: "compare",
      results: [
        snowflakeRun("snow-a", 12),
        makeDetail({ result_id: "polars-a", platform: "Polars", platform_id: "polars", display_geomean_ms: 20, geomean_ms: 20, display_timings: compareTimings(20) }),
      ],
      primaryMetric: "display_geomean_ms",
    });
    const { container } = render(<QueryHistogram summary={summary!} />);

    expect(markFillFor(container, "Snowflake")).toBeDefined();
    expect(legendFillFor(container, "Snowflake")).toBe(markFillFor(container, "Snowflake"));
    expect(legendFillFor(container, "Polars")).toBe(markFillFor(container, "Polars"));
  });
});

function barFillForRun(container: Element, runLabel: string) {
  return Array.from(container.querySelectorAll("rect")).find((rect) =>
    rect.querySelector("title")?.textContent?.includes(` - ${runLabel}:`),
  )?.getAttribute("fill");
}

describe("cost chart applicability", () => {
  it("offers the cost chart when every compared run has only an estimated cost", () => {
    const estimate = (resultId: string, amount: number): DetailResult => ({
      ...makeDetail({ result_id: resultId }),
      cost_status: "estimated",
      normalized_cost_usd: null,
      estimated_cost_usd: amount,
    });
    const charts = applicableCharts({
      kind: "compare",
      results: [
        estimate("tpch-snowflake-sf10.0-20261003-f0835929", 0.0156),
        estimate("tpch-databricks-sf10.0-20261003-5f5ccaf2", 0.0236),
      ],
      primaryMetric: "display_geomean_ms",
    });
    expect(charts.map((chart) => chart.id)).toContain("cost_scatter");
  });

  it("does not offer the cost chart when no run has a cost", () => {
    const charts = applicableCharts({
      kind: "compare",
      results: ["r1", "r2"].map((resultId): DetailResult => ({
        ...makeDetail({ result_id: resultId }),
        cost_status: "unavailable",
        normalized_cost_usd: null,
        estimated_cost_usd: null,
      })),
      primaryMetric: "display_geomean_ms",
    });
    expect(charts.map((chart) => chart.id)).not.toContain("cost_scatter");
  });
});
