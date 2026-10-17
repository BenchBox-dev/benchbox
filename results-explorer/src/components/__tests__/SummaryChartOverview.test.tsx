import { render, screen, fireEvent, within } from "@testing-library/preact";
import { describe, it, expect } from "vitest";
import type { BenchmarkSummary, PlatformRow } from "@/types";
import type { ChartHistoricalEntry } from "@/lib/chartRegistry";

import { SummaryChartOverview, SummaryMetricTable } from "@/components/SummaryChartOverview";
import { assignRunColorSlots, paletteColor } from "@/lib/chartTheme";

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
    scale_factor: 1,
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

function historyEntry(overrides: Partial<ChartHistoricalEntry>): ChartHistoricalEntry {
  return {
    result_id: "h1",
    benchmark: "tpch",
    scale_factor: 10,
    platform: "DuckDB",
    platform_id: "duckdb",
    run_date: "2026-04-01",
    power_score: 3000,
    display_geomean_ms: 12,
    ...overrides,
  };
}

describe("SummaryChartOverview readable layout", () => {
  it("gives the metric table and the latency box plot visible headings", () => {
    renderOverview();
    expect(screen.getByRole("heading", { name: "Geomean and Power@Size" })).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Latency distribution" })).not.toBeNull();
  });

  it("uses the same metric-table heading on the compare page", () => {
    render(<SummaryMetricTable summary={makeSummary()} scope="compare" />);
    expect(screen.getByRole("heading", { name: "Geomean and Power@Size" })).not.toBeNull();
  });

  it("orders the latency box plot by geomean, fastest first, like the summary table", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", platform: "Zeta", display_geomean_ms: 50, timings: { Q1: 50, Q2: 60, Q3: 70 } }),
        makePlatform({ result_id: "r2", platform: "Alpha", display_geomean_ms: 10, timings: { Q1: 10, Q2: 12, Q3: 14 } }),
        makePlatform({ result_id: "r3", platform: "Mid", display_geomean_ms: 30, timings: { Q1: 30, Q2: 33, Q3: 36 } }),
      ],
    });
    const { container } = renderOverview(summary);

    const boxText = container.querySelector("svg[aria-label='Distribution box plots of per-query latency']")!.textContent!;
    expect(boxText.indexOf("Alpha")).toBeGreaterThan(-1);
    expect(boxText.indexOf("Alpha")).toBeLessThan(boxText.indexOf("Mid"));
    expect(boxText.indexOf("Mid")).toBeLessThan(boxText.indexOf("Zeta"));

    const tableRows = Array.from(container.querySelectorAll("table.summary-metric-table tbody th")).map((th) =>
      th.textContent?.trim(),
    );
    expect(tableRows).toEqual(["Alpha", "Mid", "Zeta"]);
  });

  it("states the population with normal spacing", () => {
    renderOverview();
    expect(screen.getByText(/2 submitted runs · 2 display-safe rows · 3 queries/)).not.toBeNull();
  });

  it("says nothing about ranking when every run is ranked", () => {
    renderOverview();
    expect(screen.queryByText(/Not ranked/)).toBeNull();
    expect(screen.queryByText(/rank-safe rows/)).toBeNull();
    expect(screen.queryByText(/persisted ranking contract/)).toBeNull();
  });

  it("names a ranking exclusion in plain words with the count", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", platform: "DuckDB", ranking_exclusion_reason: "known_defective_data" }),
        makePlatform({ result_id: "r2", platform_id: "sqlite", platform: "SQLite", power_score: 1500 }),
      ],
    });
    renderOverview(summary);
    expect(screen.getByText("Not ranked: 1 of 2 runs is excluded from ranking — Known defective data.")).not.toBeNull();
    expect(screen.queryByText(/Not in the latency chart/)).toBeNull();
    expect(screen.queryByText(/excluded from the ranking chart dataset/)).toBeNull();
  });

  it("explains a missing ranking score when the export records no exclusion reason", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", platform: "DuckDB" }),
        makePlatform({ result_id: "r2", platform_id: "sqlite", platform: "SQLite", power_score: null }),
      ],
    });
    renderOverview(summary);
    expect(
      screen.getByText("Not ranked: 1 of 2 runs is excluded from ranking — Power@Size is not recorded."),
    ).not.toBeNull();
  });

  it("draws the query-matrix thumbnail on the main heatmap's sequential scale, not a hue ramp", () => {
    const { container } = renderOverview();
    const thumbnail = container.querySelector("[aria-label='Heatmap thumbnail']") as HTMLElement;
    const cells = Array.from(thumbnail.children) as HTMLElement[];
    expect(cells.length).toBe(6);
    expect(cells.every((cell) => cell.classList.contains("heatmap-cell"))).toBe(true);
    expect(cells.every((cell) => cell.style.getPropertyValue("--cell-lightness") !== "")).toBe(true);
    expect(container.innerHTML).not.toContain("hsl(");
  });

  it("keeps every thumbnail the same height so sibling cards align", () => {
    const { container } = renderOverview();
    const thumbnails = Array.from(container.querySelectorAll(".summary-chart-thumbnail"));
    expect(thumbnails.length).toBeGreaterThan(1);
    expect(thumbnails.every((thumbnail) => thumbnail.classList.contains("h-[7.5rem]"))).toBe(true);
  });

  it("does not render thumbnail text below 10px", () => {
    const { container } = renderOverview();
    expect(container.innerHTML).not.toContain("text-[8px]");
  });

  it("shows one trend empty message when no series has two points to plot", () => {
    const { container } = render(
      <SummaryChartOverview
        context={{
          kind: "summary",
          summary: makeSummary(),
          historical: [
            historyEntry({ result_id: "h1", platform_id: "duckdb", platform: "DuckDB" }),
            historyEntry({ result_id: "h2", platform_id: "sqlite", platform: "SQLite" }),
          ],
        }}
      />,
    );
    const trendThumbnail = container.querySelector("[data-testid='summary-chart-preview-time_series']") as HTMLElement;
    expect(trendThumbnail.textContent).toContain("Not enough history for a trend");
    expect(trendThumbnail.querySelector("svg")).toBeNull();
  });

  it("shows the cost empty message instead of a lone dot when only one run has a normalized cost", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", platform: "DuckDB", cost_status: "normalized", normalized_cost_usd: 1.25 }),
        makePlatform({ result_id: "r2", platform_id: "sqlite", platform: "SQLite", power_score: 1500 }),
      ],
    });
    const { container } = renderOverview(summary);
    const costThumbnail = container.querySelector("[data-testid='summary-chart-preview-cost_scatter']") as HTMLElement;
    expect(costThumbnail.textContent).toContain("No normalized cost recorded");
    expect(costThumbnail.querySelector(".rounded-full")).toBeNull();
  });

  it("labels each percentile row with its run identity instead of repeating the platform name", () => {
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
    const { container } = renderOverview(summary);
    const thumbnail = container.querySelector("[data-testid='summary-chart-preview-percentile_ladder']") as HTMLElement;
    const labels = Array.from(thumbnail.querySelectorAll("span[title]")).map((span) => span.getAttribute("title"));
    expect(labels).toEqual(expect.arrayContaining(["DuckDB v1.4.3", "DuckDB v1.5.0"]));
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("truncates a long platform name to the available width and keeps the full name in a title", () => {
    const longName = "Apache Spark Declarative Pipelines Experimental Preview Build";
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", platform: longName }),
        makePlatform({ result_id: "r2", platform_id: "sqlite", platform: "SQLite", power_score: 1500 }),
      ],
    });
    const { container } = renderOverview(summary);
    const truncated = Array.from(container.querySelectorAll("[title]")).find(
      (el) => el.textContent?.includes("…") && (el.getAttribute("title") ?? "").startsWith(longName.slice(0, 12)),
    );
    expect(truncated).toBeDefined();
    expect(truncated!.textContent!.trim().length).toBeLessThan(longName.length);
    expect(truncated!.getAttribute("title")).toContain(longName);
  });
});

describe("SummaryMetricTable framing and columns", () => {
  it("wraps itself in a card by default and drops the card when framed is false", () => {
    const { container, unmount } = render(<SummaryMetricTable summary={makeSummary()} />);
    expect(container.querySelector("section.card")).not.toBeNull();
    unmount();

    const framed = render(<SummaryMetricTable summary={makeSummary()} framed={false} />);
    expect(framed.container.querySelector(".card")).toBeNull();
    expect(screen.getByRole("heading", { name: "Geomean and Power@Size" })).not.toBeNull();
  });

  it("hides the Power@Size column when no run has a value for it", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", platform_id: "duckdb", platform: "DuckDB", power_score: null }),
        makePlatform({ result_id: "r2", platform_id: "sqlite", platform: "SQLite", power_score: null }),
      ],
    });
    const { container } = render(<SummaryMetricTable summary={summary} />);
    const table = container.querySelector("table.summary-metric-table") as HTMLElement;

    expect(within(table).queryByText("Power@Size")).toBeNull();
    expect(table.querySelectorAll("thead th")).toHaveLength(2);
    expect(table.querySelectorAll("tbody tr td")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "Geomean" })).not.toBeNull();
    expect(table.querySelector("caption")?.textContent).not.toContain("Power@Size");
  });

  it("keeps the Throughput@Size column when throughput is the ranking metric", () => {
    const summary = makeSummary({
      ranking: { primary_metric: "throughput_at_size", secondary_metric: "display_geomean_ms", primary_order: "desc" },
      platforms: [makePlatform({ result_id: "r1", throughput_at_size: 297653, power_score: null })],
    });
    const { container } = render(<SummaryMetricTable summary={summary} />);
    const table = container.querySelector("table.summary-metric-table") as HTMLElement;
    expect(within(table).getByText("Throughput@Size")).not.toBeNull();
  });
});

describe("run colours on the benchmark page", () => {
  const rankedSummary = makeSummary({
    platforms: [
      makePlatform({ result_id: "r1", platform_id: "zeta", platform: "Zeta", display_geomean_ms: 50, timings: { Q1: 50, Q2: 60, Q3: 70 } }),
      makePlatform({ result_id: "r2", platform_id: "alpha", platform: "Alpha", display_geomean_ms: 10, timings: { Q1: 10, Q2: 12, Q3: 14 } }),
      makePlatform({ result_id: "r3", platform_id: "mid", platform: "Mid", display_geomean_ms: 30, timings: { Q1: 30, Q2: 33, Q3: 36 } }),
    ],
  });

  const alphaColor = paletteColor(assignRunColorSlots(rankedSummary.platforms).get("r2")!);

  it("gives a run the same colour in the summary table, the box plot and the CDF", () => {
    const { container } = renderOverview(rankedSummary);
    const table = container.querySelector("table.summary-metric-table") as HTMLElement;
    const alphaRow = Array.from(table.querySelectorAll("tbody tr")).find((row) => row.textContent?.includes("Alpha"));
    expect(alphaRow?.querySelector("span.rounded-full")?.getAttribute("style")).toContain(alphaColor);

    const latencySection = screen.getByRole("heading", { name: "Latency distribution" }).closest("section") as HTMLElement;
    const boxLabel = Array.from(latencySection.querySelectorAll("text")).find((node) =>
      node.textContent?.endsWith("Alpha"),
    );
    expect(boxLabel?.parentElement?.querySelector("rect")?.getAttribute("fill")).toBe(alphaColor);

    const cdfCard = screen.getByTestId("summary-chart-preview-cdf_chart");
    fireEvent.click(within(cdfCard).getByText("Open full chart ↗", { selector: "span" }));
    fireEvent(cdfCard, new Event("toggle"));
    const cdfPath = Array.from(container.querySelectorAll("path")).find(
      (path) => path.querySelector("title")?.textContent?.startsWith("Alpha"),
    );
    expect(cdfPath?.getAttribute("stroke")).toBe(alphaColor);
  });
});

describe("SummaryMetricTable population", () => {
  it("names the shared query count when compared runs do not all answer every query", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", timings: { Q1: 10, Q2: 20, Q3: 30 } }),
        makePlatform({ result_id: "r2", platform_id: "sqlite", platform: "SQLite", timings: { Q1: 20, Q2: 40, Q3: null } }),
      ],
    });
    render(<SummaryMetricTable summary={summary} scope="compare" />);
    expect(screen.getByText(/2 shared of 3 queries/)).toBeTruthy();
  });

  it("keeps the plain query count on cohort pages", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", timings: { Q1: 10, Q2: 20, Q3: 30 } }),
        makePlatform({ result_id: "r2", platform_id: "sqlite", platform: "SQLite", timings: { Q1: 20, Q2: 40, Q3: null } }),
      ],
    });
    render(<SummaryMetricTable summary={summary} />);
    expect(screen.getByText(/· 3 queries/)).toBeTruthy();
  });
});

describe("SummaryChartOverview thumbnails", () => {
  it("exposes the labelled grid thumbnails as images", () => {
    const { container } = renderOverview();
    for (const label of ["Heatmap thumbnail", "Rank table thumbnail"]) {
      const thumbnail = container.querySelector(`[aria-label='${label}']`);
      expect(thumbnail?.getAttribute("role")).toBe("img");
    }
  });

  it("draws first-place rank cells with theme text instead of hard-coded white", () => {
    const { container } = renderOverview();
    const cells = Array.from(container.querySelectorAll("[aria-label='Rank table thumbnail'] > span")) as HTMLElement[];
    expect(cells.length).toBeGreaterThan(0);
    expect(cells.some((cell) => cell.style.color === "white")).toBe(false);
    expect(cells.some((cell) => cell.style.color === "var(--bb-surface-data)")).toBe(true);
  });
});
