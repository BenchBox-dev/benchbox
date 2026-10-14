import { render, screen, fireEvent, waitFor, within } from "@testing-library/preact";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { PlatformIndexRowRow } from "@/lib/duckdbQueries";

vi.mock("@/lib/duckdbQueries", async () => {
  const actual = await vi.importActual<typeof import("@/lib/duckdbQueries")>("@/lib/duckdbQueries");
  return {
    ...actual,
    getPlatformIndexRows: vi.fn(),
    getResultsBasisAvailability: vi.fn().mockResolvedValue([]),
    getDetailResult: vi.fn().mockResolvedValue(null),
    getCohortBasisDetails: vi.fn().mockResolvedValue(new Map()),
  };
});

vi.mock("preact-router", async () => {
  const actual = await vi.importActual<typeof import("preact-router")>("preact-router");
  return {
    ...actual,
    route: vi.fn(),
  };
});

import {
  getCohortBasisDetails,
  getResultsBasisAvailability,
  getPlatformIndexRows,
} from "@/lib/duckdbQueries";
import { PlatformIndex } from "@/pages/PlatformIndex";

function makeRow(overrides: Partial<PlatformIndexRowRow> = {}): PlatformIndexRowRow {
  return {
    result_id: "r-base",
    short_id: "base0001",
    benchmark: "tpch",
    scale_factor: 0.1,
    phase: "power",
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: null,
    platform_version: null,
    run_date: "2026-04-01",
    power_score: 3000,
    total_duration_s: 60,
    geomean_ms: 15,
    display_geomean_ms: 12,
    query_count: 22,
    has_display_timing: true,
    valid_query_count: 22,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    trust_label: "maintainer-run",
    funding: "unspecified",
    validation_status: "exact",
    tuning_mode: null,
    execution_mode: null,
    compliance_class: null,
    cost_usd: null,
    normalized_cost_usd: null,
    cost_status: "not_applicable_local",
    cost_scope: null,
    cost_model_version: null,
    deployment_class: "local",
    cloud_provider: null,
    cloud_region: null,
    instance_or_warehouse: null,
    warehouse_size: null,
    storage_format: null,
    primary_metric: "display_geomean_ms",
    ...overrides,
  };
}

const ROWS: PlatformIndexRowRow[] = [
  makeRow({
    result_id: "r-tpch-fast",
    short_id: "aaaabbbb",
    benchmark: "tpch",
    run_date: "2026-04-03",
    power_score: 5000,
    geomean_ms: 5,
    normalized_cost_usd: 1.25,
    cost_status: "normalized",
    cost_scope: "compute_only",
    cost_model_version: "2026.05.0",
    deployment_class: "cloud",
    cloud_provider: "aws",
    cloud_region: "us-east-1",
    instance_or_warehouse: "MEDIUM",
    warehouse_size: "MEDIUM",
    storage_format: "parquet",
  }),
  makeRow({ result_id: "r-ssb-mid", short_id: "ccccdddd", benchmark: "star_schema", run_date: "2026-04-01", power_score: 3000, geomean_ms: 15 }),
  makeRow({ result_id: "r-tpch-slow", short_id: "eeeeffff", benchmark: "tpch", run_date: "2026-04-02", power_score: 1000, geomean_ms: 50 }),
  makeRow({ result_id: "r-null-geo", short_id: "11112222", benchmark: "tpch", run_date: "2026-04-04", power_score: null, geomean_ms: null }),
];

function getRowOrder(container: ParentNode): string[] {
  return Array.from(container.querySelectorAll("tbody tr[data-testid]")).map(
    (tr) => tr.getAttribute("data-testid") ?? "",
  );
}

function openTrendsCard(): void {
  const details = screen.getByTestId("summary-chart-preview-trends") as HTMLDetailsElement;
  details.open = true;
  fireEvent(details, new Event("toggle"));
}

describe("PlatformIndex - sortable table headers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState(null, "", "/results/p/duckdb/");
    vi.mocked(getPlatformIndexRows).mockResolvedValue(ROWS);
  });

  it("sorts by run date descending by default", async () => {
    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    await waitFor(() => expect(document.title).toBe("DuckDB · BenchBox Results"));
    expect(getRowOrder(container)).toEqual(["r-null-geo", "r-tpch-fast", "r-tpch-slow", "r-ssb-mid"]);
    expect(screen.getAllByLabelText(/^Run date /)).toHaveLength(4);
  });

  it("matches lower-case platform URLs against mixed-case platform IDs", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue(
      ROWS.map((row) => ({ ...row, platform_id: "DuckDB" })),
    );

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    expect(screen.queryByText(/No results found for platform/i)).toBeNull();
    expect(getRowOrder(container)).toEqual(["r-null-geo", "r-tpch-fast", "r-tpch-slow", "r-ssb-mid"]);
  });

  it("keeps legacy display-name URLs working when no platform ID matches", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue(
      ROWS.map((row) => ({ ...row, platform_id: "duckdb-main" })),
    );

    const { container } = render(<PlatformIndex platform="DuckDB" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    expect(screen.queryByText(/No results found for platform/i)).toBeNull();
    expect(getRowOrder(container)).toEqual(["r-null-geo", "r-tpch-fast", "r-tpch-slow", "r-ssb-mid"]);
  });

  it("canonicalizes the explicit clickhouse_local route alias", async () => {
    const preactRouter = await import("preact-router");
    const routeMock = vi.mocked(preactRouter.route);
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      ...ROWS,
      makeRow({ result_id: "r-clickhouse", platform_id: "clickhouse-local", platform: "ClickHouse Local" }),
    ]);

    render(<PlatformIndex platform="clickhouse_local" />);
    await waitFor(() => expect(screen.getByText("ClickHouse Local Results")).toBeTruthy());

    await waitFor(() => expect(routeMock).toHaveBeenCalledWith("/results/p/clickhouse-local/", true));
    const switcher = screen.getByTestId("platform-switcher") as HTMLSelectElement;
    expect(switcher.value).toBe("clickhouse-local");
    expect(within(switcher).queryByRole("option", { name: "clickhouse_local" })).toBeNull();
  });

  it("preserves query parameters while canonicalizing platform aliases", async () => {
    const preactRouter = await import("preact-router");
    const routeMock = vi.mocked(preactRouter.route);
    window.history.replaceState(null, "", "/results/p/clickhouse_local/?tuning=notuning");
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      ...ROWS,
      makeRow({ result_id: "r-clickhouse", platform_id: "clickhouse-local", platform: "ClickHouse Local" }),
    ]);

    render(<PlatformIndex platform="clickhouse_local" />);
    await waitFor(() => expect(screen.getByText("ClickHouse Local Results")).toBeTruthy());

    await waitFor(() => expect(routeMock).toHaveBeenCalledWith("/results/p/clickhouse-local/?tuning=notuning", true));
  });

  it("retries a transient empty platform index before rendering a terminal empty state", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValueOnce([]).mockResolvedValueOnce(ROWS);

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    expect(getPlatformIndexRows).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/No results found for platform/i)).toBeNull();
    expect(getRowOrder(container)).toEqual(["r-null-geo", "r-tpch-fast", "r-tpch-slow", "r-ssb-mid"]);
  });

  it("does not brand a genuinely empty snapshot as a missing lower-case platform", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValueOnce([]).mockResolvedValueOnce([]);

    render(<PlatformIndex platform="polars" />);
    await waitFor(() => expect(screen.getByText("No published platform results are available in this snapshot.")).toBeTruthy());

    expect(screen.queryByText("polars Results")).toBeNull();
    expect(screen.queryByText(/No results found for platform/i)).toBeNull();
  });

  it("does not merge distinct platform IDs that share a display name", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({
        result_id: "r-datafusion-current",
        short_id: "dfcurr01",
        platform_id: "datafusion",
        platform: "DataFusion",
        geomean_ms: 10,
      }),
      makeRow({
        result_id: "r-datafusion-44",
        short_id: "dfold044",
        platform_id: "datafusion-44",
        platform: "DataFusion",
        geomean_ms: 20,
      }),
      makeRow({ result_id: "r-duckdb", short_id: "duck0001" }),
    ]);

    const { container } = render(<PlatformIndex platform="datafusion" />);
    await waitFor(() => expect(screen.getByText("DataFusion Results")).toBeTruthy());

    expect(getRowOrder(container)).toEqual(["r-datafusion-current"]);
  });

  it("renders a Platform switcher that routes to a sibling without preserving tuning", async () => {
    const preactRouter = await import("preact-router");
    const routeMock = vi.mocked(preactRouter.route);
    routeMock.mockClear();

    const multiPlatformRows = [
      ...ROWS,
      makeRow({ result_id: "r-pg", short_id: "pg00000a", platform_id: "postgres", platform: "Postgres" }),
    ];
    vi.mocked(getPlatformIndexRows).mockResolvedValue(multiPlatformRows);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const switcher = screen.getByTestId("platform-switcher") as HTMLSelectElement;
    expect(switcher.value).toBe("duckdb");
    expect(within(switcher).getByRole("option", { name: "Postgres" })).toBeTruthy();

    fireEvent.change(switcher, { target: { value: "postgres" } });
    expect(routeMock).toHaveBeenCalledTimes(1);
    expect(routeMock).toHaveBeenCalledWith("/results/p/postgres/");
  });

  it("renders the platform filter strip when the cohort has >=25 rows and reset clears it", async () => {
    const denseRows: PlatformIndexRowRow[] = Array.from({ length: 30 }, (_, idx) =>
      makeRow({
        result_id: `r-dense-${idx}`,
        short_id: `dense${String(idx).padStart(4, "0")}`,
        benchmark: idx < 18 ? "tpch" : "clickbench",
        scale_factor: idx % 2 === 0 ? 0.1 : 1,
        run_date: "2026-04-10",
        geomean_ms: 10 + idx,
      }),
    );
    vi.mocked(getPlatformIndexRows).mockResolvedValue(denseRows);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const filterStrip = screen.getByTestId("platform-detail-filters");
    expect(filterStrip).toBeTruthy();
    expect(screen.getByTestId("platform-run-count").textContent).toBe("30 published runs");

    fireEvent.change(screen.getByTestId("platform-filter-benchmark"), {
      target: { value: "clickbench" },
    });
    await waitFor(() => expect(screen.getByTestId("platform-run-count").textContent).toBe("12 of 30 published runs"));

    fireEvent.click(screen.getByTestId("platform-filter-reset"));
    await waitFor(() => expect(screen.getByTestId("platform-run-count").textContent).toBe("30 published runs"));
    expect(screen.queryByTestId("platform-filter-reset")).toBeNull();
  });

  it("disambiguates canonical and legacy SSB benchmark options in the platform filter", async () => {
    const mixedSsbRows: PlatformIndexRowRow[] = Array.from({ length: 30 }, (_, idx) =>
      makeRow({
        result_id: `r-ssb-filter-${idx}`,
        short_id: `ssbf${String(idx).padStart(4, "0")}`,
        benchmark: idx < 15 ? "star_schema" : "ssb",
        run_date: "2026-04-10",
        geomean_ms: 10 + idx,
      }),
    );
    vi.mocked(getPlatformIndexRows).mockResolvedValue(mixedSsbRows);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const benchmarkSelect = screen.getByTestId("platform-filter-benchmark") as HTMLSelectElement;
    const benchmarkOptions = Array.from(benchmarkSelect.options).map((option) => ({
      value: option.value,
      label: option.text,
    }));
    expect(benchmarkOptions).toEqual([
      { value: "all", label: "All benchmarks" },
      { value: "ssb", label: "SSB" },
      { value: "star_schema", label: "SSB (historical source)" },
    ]);
    expect(within(benchmarkSelect).getByRole("option", { name: "SSB" })).toHaveValue("ssb");
    expect(within(benchmarkSelect).getByRole("option", { name: "SSB (historical source)" })).toHaveValue("star_schema");
  });

  it("locks the cohort signature after the first compare selection", async () => {
    const cohortRows: PlatformIndexRowRow[] = [
      makeRow({
        result_id: "r-tpch-a",
        short_id: "tpcha000",
        benchmark: "tpch",
        scale_factor: 0.1,
        phase: "power",
        run_date: "2026-04-01",
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "r-tpch-b",
        short_id: "tpchb000",
        benchmark: "tpch",
        scale_factor: 0.1,
        phase: "power",
        run_date: "2026-04-02",
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "r-clickbench",
        short_id: "click000",
        benchmark: "clickbench",
        scale_factor: 0.1,
        phase: "power",
        run_date: "2026-04-03",
        primary_metric: "display_geomean_ms",
      }),
    ];
    vi.mocked(getPlatformIndexRows).mockResolvedValue(cohortRows);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const checkboxA = screen.getByTestId("platform-compare-checkbox-r-tpch-a") as HTMLInputElement;
    const checkboxB = screen.getByTestId("platform-compare-checkbox-r-tpch-b") as HTMLInputElement;
    const checkboxClickbench = screen.getByTestId(
      "platform-compare-checkbox-r-clickbench",
    ) as HTMLInputElement;

    expect(checkboxA.disabled).toBe(false);
    expect(checkboxB.disabled).toBe(false);
    expect(checkboxClickbench.disabled).toBe(false);

    fireEvent.click(checkboxA);
    expect(checkboxA.checked).toBe(true);
    expect(checkboxB.disabled).toBe(false);
    expect(checkboxClickbench.disabled).toBe(true);
    expect(checkboxClickbench.title).toContain("differs by benchmark");

    fireEvent.click(checkboxA);
    expect(checkboxClickbench.disabled).toBe(false);
  });

  it("caps Platform compare selections at ten runs", async () => {
    const cohortRows: PlatformIndexRowRow[] = Array.from({ length: 11 }, (_, idx) =>
      makeRow({
        result_id: `r-compatible-${idx}`,
        short_id: `compat${idx}`,
        benchmark: "tpch",
        scale_factor: 0.1,
        phase: "power",
        primary_metric: "display_geomean_ms",
        run_date: `2026-04-${String(idx + 1).padStart(2, "0")}`,
        geomean_ms: 10 + idx,
      }),
    );
    vi.mocked(getPlatformIndexRows).mockResolvedValue(cohortRows);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    for (let idx = 0; idx < 10; idx += 1) {
      fireEvent.click(screen.getByTestId(`platform-compare-checkbox-r-compatible-${idx}`));
    }
    const eleventh = screen.getByTestId("platform-compare-checkbox-r-compatible-10") as HTMLInputElement;
    expect(eleventh.disabled).toBe(true);
    expect(eleventh.title).toContain("Up to 10 runs");
    expect(screen.getByTestId("platform-compare-guidance").textContent).toContain(
      "10 results selected (maximum)",
    );
    expect(screen.queryByText("Use sticky tray to compare")).toBeNull();
    expect(screen.getByTestId("platform-run-count").textContent).toBe("11 published runs");
  });

  it("disables Platform compare selection for non-comparable rows", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-comparable", short_id: "compare1", geomean_ms: 10 }),
      makeRow({
        result_id: "r-not-comparable",
        short_id: "blocked1",
        geomean_ms: 20,
        comparison_exclusion_reason: "insufficient_valid_timings",
      }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const blocked = screen.getByTestId("platform-compare-checkbox-r-not-comparable") as HTMLInputElement;
    expect(blocked.disabled).toBe(true);
    expect(blocked.title).toContain("This run does not include enough usable query timings");
    const visibleReason = screen.getByTestId("platform-disabled-reason");
    expect(visibleReason.textContent).toContain("Why unavailable: Insufficient valid timings");
    expect(visibleReason.textContent).toContain("Choose a run with at least two valid query timings");
    expect(blocked.getAttribute("aria-describedby")).toBe(visibleReason.id);

    fireEvent.click(blocked);
    expect(screen.queryByRole("link", { name: /Compare 1 selected/ })).toBeNull();
    expect(screen.getByTestId("platform-compare-guidance").textContent).toContain("Select two or more");
  });

  it("shows a platform zero-selectable recovery callout when filters expose only disabled rows", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({
        result_id: "r-blocked-a",
        short_id: "blocka01",
        comparison_exclusion_reason: "insufficient_query_coverage",
      }),
      makeRow({
        result_id: "r-blocked-b",
        short_id: "blockb01",
        comparison_exclusion_reason: "insufficient_query_coverage",
      }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const callout = screen.getByTestId("platform-zero-selectable");
    expect(callout.textContent).toContain("No selectable compare rows");
    expect(callout.textContent).toContain("insufficient query coverage");
    expect(within(callout).queryByRole("button", { name: "Clear filters" })).toBeNull();
    expect(screen.getAllByTestId("platform-disabled-reason")[0]?.textContent).toContain(
      "Why unavailable: Insufficient query coverage",
    );
  });

  it("offers to clear filters only when an excluded selectable row proves the remedy", async () => {
    window.history.replaceState(null, "", "/results/p/duckdb/?benchmark=tpch");
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({
        result_id: "r-blocked-tpch",
        short_id: "blockt01",
        benchmark: "tpch",
        comparison_exclusion_reason: "insufficient_query_coverage",
      }),
      makeRow({
        result_id: "r-selectable-ssb",
        short_id: "select01",
        benchmark: "star_schema",
      }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    const callout = await screen.findByTestId("platform-zero-selectable");
    fireEvent.click(within(callout).getByRole("button", { name: "Clear filters" }));

    await waitFor(() => expect(screen.queryByTestId("platform-zero-selectable")).toBeNull());
    expect(screen.getByTestId("r-selectable-ssb")).toBeTruthy();
  });

  it("shows the platform filter strip even when the cohort has fewer than 25 rows", async () => {
    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    expect(screen.getByTestId("platform-detail-filters")).toBeTruthy();
  });

  it("offers Compare All Runs when filtered rows share one small comparable ranking", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-one", short_id: "one00001" }),
      makeRow({ result_id: "r-two", short_id: "two00002" }),
      makeRow({ result_id: "r-three", short_id: "three003" }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.queryByTestId("platform-compare-cta")?.textContent).toBe("Compare All Runs"));

    const cta = screen.getByTestId("platform-compare-cta");
    expect(cta.getAttribute("href")).toBe("/results/compare?ids=one00001,two00002,three003");
    expect(screen.getByTestId("platform-compare-guidance").textContent).toContain(
      "All 3 filtered runs share one comparable ranking and can be compared together.",
    );
  });

  it("shows persistent compare guidance and cohort labels before selection", async () => {
    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const guidance = screen.getByTestId("platform-compare-guidance");
    const status = guidance.querySelector('[aria-live="polite"]');
    expect(status).toHaveAttribute("aria-atomic", "true");
    expect(status?.textContent).toContain("0 results selected");
    expect(guidance.textContent).toContain("Select two or more DuckDB results");
    expect(screen.queryByRole("button", { name: /Select 2 / })).toBeNull();
    const cta = screen.getByTestId("platform-compare-cta") as HTMLButtonElement;
    expect(cta.tagName).toBe("BUTTON");
    expect(cta.disabled).toBe(true);
    expect(cta.className).toContain("btn-primary");
    expect(cta.textContent).toBe("Select 2–10 runs to compare");
    expect(screen.getByTestId("platform-table-scroll-hint").textContent).toContain("Scroll for dates, timings, and source labels");
    expect(screen.getAllByText(/Geomean latency \(lower is better\)/).length).toBeGreaterThan(0);
  });

  it("clicking the Geomean header selects ascending, then flips to descending", async () => {
    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Geomean/ }));
    expect(getRowOrder(container)).toEqual(["r-tpch-fast", "r-ssb-mid", "r-tpch-slow", "r-null-geo"]);
    fireEvent.click(screen.getByRole("button", { name: /Geomean/ }));
    expect(getRowOrder(container)).toEqual(["r-tpch-slow", "r-ssb-mid", "r-tpch-fast", "r-null-geo"]);
  });

  it("clicking the Benchmark header sorts alphabetically ascending", async () => {
    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Benchmark/ }));
    const order = getRowOrder(container);
    expect(order[0]).toBe("r-ssb-mid");
    expect(order.slice(1)).toEqual(["r-tpch-fast", "r-tpch-slow", "r-null-geo"]);
  });

  it("clicking Power score puts nulls last in both directions", async () => {
    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Power score/ }));
    expect(getRowOrder(container).slice(-1)[0]).toBe("r-null-geo");
    fireEvent.click(screen.getByRole("button", { name: /Power score/ }));
    expect(getRowOrder(container).slice(-1)[0]).toBe("r-null-geo");
  });

  it("Enter on a sort header flips the sort (keyboard parity with click)", async () => {
    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    const benchmarkBtn = screen.getByRole("button", { name: /Benchmark/ });
    benchmarkBtn.focus();
    expect(document.activeElement).toBe(benchmarkBtn);
    fireEvent.keyDown(benchmarkBtn, { key: "Enter" });
    fireEvent.click(benchmarkBtn);
    expect(getRowOrder(container)[0]).toBe("r-ssb-mid");
  });

  it("aria-sort reflects the active column and direction", async () => {
    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    const headerCells = container.querySelectorAll("th[aria-sort]");
    const geoTh = Array.from(headerCells).find((th) => th.textContent?.includes("Geomean"));
    const benchTh = Array.from(headerCells).find((th) => th.textContent?.includes("Benchmark"));
    const dateTh = Array.from(headerCells).find((th) => th.textContent?.includes("Date"));
    expect(dateTh?.getAttribute("aria-sort")).toBe("descending");
    expect(geoTh?.getAttribute("aria-sort")).toBe("none");
    expect(benchTh?.getAttribute("aria-sort")).toBe("none");
    fireEvent.click(screen.getByRole("button", { name: /Benchmark/ }));
    expect(benchTh?.getAttribute("aria-sort")).toBe("ascending");
    expect(geoTh?.getAttribute("aria-sort")).toBe("none");
    fireEvent.click(screen.getByRole("button", { name: /Benchmark/ }));
    expect(benchTh?.getAttribute("aria-sort")).toBe("descending");
  });

  it("renders architecture and cpu family columns and supports sorting", async () => {
    const hwRows: PlatformIndexRowRow[] = [
      makeRow({ result_id: "r-arm", short_id: "arm00001", run_date: "2026-04-01", arch: "arm64", cpu_family: "apple_silicon" }),
      makeRow({ result_id: "r-x86-amd", short_id: "x8600002", run_date: "2026-04-02", arch: "x86_64", cpu_family: "amd_epyc" }),
      makeRow({ result_id: "r-x86-intel", short_id: "x8600001", run_date: "2026-04-03", arch: "x86_64", cpu_family: "intel_xeon" }),
      makeRow({ result_id: "r-no-hw", short_id: "nohw0001", run_date: "2026-04-04", arch: null, cpu_family: null }),
    ];
    vi.mocked(getPlatformIndexRows).mockResolvedValue(hwRows);

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const table = screen.getByRole("table");
    expect(within(table).getByText("Apple silicon")).toBeTruthy();
    expect(within(table).getByText("Intel Xeon")).toBeTruthy();
    expect(within(table).getByText("AMD EPYC")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^Arch/ }));
    let order = getRowOrder(container);
    expect(order[0]).toBe("r-arm");
    expect(order[3]).toBe("r-no-hw");

    fireEvent.click(screen.getByRole("button", { name: /^Arch/ }));
    order = getRowOrder(container);
    expect(order[2]).toBe("r-arm");
    expect(order[3]).toBe("r-no-hw");

    fireEvent.click(screen.getByRole("button", { name: /^CPU/ }));
    order = getRowOrder(container);
    expect(order[0]).toBe("r-x86-amd");
    expect(order[1]).toBe("r-arm");
    expect(order[2]).toBe("r-x86-intel");
    expect(order[3]).toBe("r-no-hw");

    fireEvent.click(screen.getByRole("button", { name: /^CPU/ }));
    order = getRowOrder(container);
    expect(order[0]).toBe("r-x86-intel");
    expect(order[1]).toBe("r-arm");
    expect(order[2]).toBe("r-x86-amd");
    expect(order[3]).toBe("r-no-hw");
  });

  it("filters by tuning, architecture, CPU family, and memory, and Reset clears all four", async () => {
    const hwRows: PlatformIndexRowRow[] = [
      makeRow({
        result_id: "r-tuned",
        short_id: "tuned001",
        tuning_mode: "tuned",
        arch: "arm64",
        cpu_family: "apple_silicon",
        memory_gb: 32,
      }),
      makeRow({
        result_id: "r-default",
        short_id: "default1",
        tuning_mode: null,
        arch: "x86_64",
        cpu_family: "amd_epyc",
        memory_gb: 64,
      }),
    ];
    vi.mocked(getPlatformIndexRows).mockResolvedValue(hwRows);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    expect(screen.getByTestId("platform-run-count").textContent).toBe("2 published runs");

    fireEvent.change(screen.getByLabelText("Tuning"), { target: { value: "tuned" } });
    await waitFor(() => expect(screen.getByTestId("platform-run-count").textContent).toBe("1 of 2 published runs"));
    fireEvent.click(screen.getByTestId("platform-filter-reset"));
    await waitFor(() => expect(screen.getByTestId("platform-run-count").textContent).toBe("2 published runs"));

    fireEvent.change(screen.getByLabelText("Architecture"), { target: { value: "arm64" } });
    await waitFor(() => expect(screen.getByTestId("platform-run-count").textContent).toBe("1 of 2 published runs"));
    fireEvent.click(screen.getByTestId("platform-filter-reset"));
    await waitFor(() => expect(screen.getByTestId("platform-run-count").textContent).toBe("2 published runs"));

    fireEvent.change(screen.getByLabelText("CPU family"), { target: { value: "amd_epyc" } });
    await waitFor(() => expect(screen.getByTestId("platform-run-count").textContent).toBe("1 of 2 published runs"));
    fireEvent.click(screen.getByTestId("platform-filter-reset"));
    await waitFor(() => expect(screen.getByTestId("platform-run-count").textContent).toBe("2 published runs"));

    fireEvent.change(screen.getByLabelText("Memory"), { target: { value: "32" } });
    await waitFor(() => expect(screen.getByTestId("platform-run-count").textContent).toBe("1 of 2 published runs"));
    fireEvent.click(screen.getByTestId("platform-filter-reset"));
    await waitFor(() => expect(screen.getByTestId("platform-run-count").textContent).toBe("2 published runs"));
    expect(screen.queryByTestId("platform-filter-reset")).toBeNull();
  });

  it("renders the Queries and Receipt columns with correct aria-colindex and group-row colspan", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-q1", short_id: "queries1", query_count: 22 }),
      makeRow({ result_id: "r-q2", short_id: "queries2", query_count: 22, platform_version: "1.4.0" }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const table = screen.getByRole("table", { name: "DuckDB results" });
    expect(within(table).getByRole("columnheader", { name: "Queries" })).toBeTruthy();
    expect(within(table).getByRole("columnheader", { name: "Receipt" })).toBeTruthy();
    expect(within(table).getAllByText("22").length).toBeGreaterThan(0);
    const receiptLinks = within(table).getAllByRole("link", { name: /Open receipt for/ });
    expect(receiptLinks.length).toBeGreaterThan(0);

    fireEvent.change(screen.getByTestId("platform-group-by"), { target: { value: "engine_version" } });
    const groupRow = table.querySelector("tbody tr td[colspan]") as HTMLTableCellElement | null;
    expect(groupRow).toBeTruthy();
    const colcount = Number(table.getAttribute("aria-colcount"));
    expect(Number(groupRow?.getAttribute("colspan"))).toBe(colcount);
  });

  it("keeps the measurement basis control working from the Results card toolbar", async () => {
    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const basisControl = screen.getByRole("combobox", { name: "Measurement basis" }) as HTMLSelectElement;
    expect(basisControl).toBeTruthy();
    expect(basisControl.closest("section")?.getAttribute("aria-label")).toBe("Measurement basis");
  });

  it("restores platform page URL facets for benchmark, cohort, deployment, and cost filters", async () => {
    window.history.replaceState(
      null,
      "",
      "/results/p/duckdb/?benchmark=tpch&sf=0.1&phase=power&deployment=cloud&cost_status=normalized",
    );

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    expect(getRowOrder(container)).toEqual(["r-tpch-fast"]);
    expect(screen.queryByText("SSB")).toBeNull();
    expect(screen.queryByTestId("r-tpch-slow")).toBeNull();
  });

  it("filters deployment and shape from normalized fields instead of legacy proxy columns", async () => {
    window.history.replaceState(null, "", "/results/p/duckdb/?deployment=cloud&shape=MEDIUM");
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({
        result_id: "r-normalized-cloud",
        deployment_class: "cloud",
        cloud_provider: null,
        instance_or_warehouse: "MEDIUM",
        warehouse_size: null,
      }),
      makeRow({
        result_id: "r-legacy-proxy-cloud",
        deployment_class: "local",
        cloud_provider: "aws",
        instance_or_warehouse: null,
        warehouse_size: "MEDIUM",
      }),
    ]);

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    expect(getRowOrder(container)).toEqual(["r-normalized-cloud"]);
  });

  it("splits trend charts by benchmark, scale, phase, and primary metric", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({
        result_id: "r-tpch-a",
        benchmark: "tpch",
        scale_factor: 0.01,
        phase: "power",
        run_date: "2026-04-01",
        power_score: 1000,
        primary_metric: "power_score",
      }),
      makeRow({
        result_id: "r-tpch-b",
        benchmark: "tpch",
        scale_factor: 0.01,
        phase: "power",
        run_date: "2026-04-02",
        power_score: 1200,
        primary_metric: "power_score",
      }),
      makeRow({
        result_id: "r-tpch-c",
        benchmark: "tpch",
        scale_factor: 0.01,
        phase: "power",
        run_date: "2026-04-03",
        power_score: 1300,
        primary_metric: "power_score",
      }),
      makeRow({
        result_id: "r-ssb-a",
        benchmark: "star_schema",
        scale_factor: 0.1,
        phase: "power",
        run_date: "2026-04-01",
        display_geomean_ms: 20,
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "r-ssb-b",
        benchmark: "star_schema",
        scale_factor: 0.1,
        phase: "power",
        run_date: "2026-04-02",
        display_geomean_ms: 18,
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "r-ssb-c",
        benchmark: "star_schema",
        scale_factor: 0.1,
        phase: "power",
        run_date: "2026-04-03",
        display_geomean_ms: 17,
        primary_metric: "display_geomean_ms",
      }),
    ]);

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    openTrendsCard();

    const tpch = screen.getByTestId("trend-cohort-tpch-sf0.01-power-power_score");
    const ssb = screen.getByTestId("trend-cohort-star_schema-sf0.1-power-display_geomean_ms");
    expect(container.querySelectorAll("svg[aria-label$='trend over time']")).toHaveLength(2);
    expect(tpch.querySelector('[data-result-id="r-tpch-a"]')).toBeTruthy();
    expect(tpch.querySelector('[data-result-id="r-tpch-b"]')).toBeTruthy();
    expect(tpch.querySelector('[data-result-id="r-tpch-c"]')).toBeTruthy();
    expect(tpch.querySelector('[data-result-id="r-ssb-a"]')).toBeNull();
    expect(ssb.querySelector('[data-result-id="r-ssb-a"]')).toBeTruthy();
    expect(ssb.querySelector('[data-result-id="r-ssb-b"]')).toBeTruthy();
    expect(ssb.querySelector('[data-result-id="r-ssb-c"]')).toBeTruthy();
    expect(ssb.querySelector('[data-result-id="r-tpch-a"]')).toBeNull();
  });

  it("keeps throughput runs with different stream counts on separate trend lines", async () => {
    const throughputRow = (resultId: string, streams: number, runDate: string, score: number) =>
      makeRow({
        result_id: resultId,
        benchmark: "tpch",
        scale_factor: 1,
        phase: "throughput",
        stream_count: streams,
        run_date: runDate,
        power_score: null,
        throughput_at_size: score,
        primary_metric: "throughput_at_size",
      });
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      throughputRow("r-t3-a", 3, "2026-04-01", 1000),
      throughputRow("r-t3-b", 3, "2026-04-02", 1100),
      throughputRow("r-t3-c", 3, "2026-04-03", 1050),
      throughputRow("r-t2-a", 2, "2026-04-01", 900),
      throughputRow("r-t2-b", 2, "2026-04-02", 950),
      throughputRow("r-t2-c", 2, "2026-04-03", 930),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    openTrendsCard();

    const three = screen.getByTestId("trend-cohort-tpch-sf1-throughput-3streams-throughput_at_size");
    const two = screen.getByTestId("trend-cohort-tpch-sf1-throughput-2streams-throughput_at_size");
    expect(three.querySelector('[data-result-id="r-t3-a"]')).toBeTruthy();
    expect(three.querySelector('[data-result-id="r-t2-a"]')).toBeNull();
    expect(two.querySelector('[data-result-id="r-t2-b"]')).toBeTruthy();
    expect(three.textContent).toContain("throughput (3 streams)");
  });

  it("shows public-ID receipt links and validation status for each platform result", async () => {
    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const receiptLinks = screen.getAllByRole("link", {
      name: /Open receipt for DuckDB public ID/,
    }) as HTMLAnchorElement[];
    expect(receiptLinks[0]?.getAttribute("href")).toBe("/results/r/r-null-geo#run-receipt");
    expect(screen.getAllByText("Exact").length).toBeGreaterThan(0);
  });

  it("compare tray shows public IDs while the compare URL keeps short aliases", async () => {
    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    fireEvent.click(screen.getByTestId("platform-compare-checkbox-r-tpch-fast"));
    fireEvent.click(screen.getByTestId("platform-compare-checkbox-r-ssb-mid"));

    const tpchRow = screen.getByTestId("compare-tray-row-r-tpch-fast");
    const ssbRow = screen.getByTestId("compare-tray-row-r-ssb-mid");
    expect(tpchRow.textContent).toContain("DuckDB");
    expect(tpchRow.textContent).toContain("TPC-H");
    expect(tpchRow.textContent).toContain("SF 0.1");
    expect(tpchRow.textContent).toContain("power");
    expect(tpchRow.textContent).toContain("2026-04-03");
    expect(tpchRow.textContent).toContain("Public ID r-tpch-fast");
    expect(ssbRow.textContent).toContain("SSB");
    expect(screen.getByTestId("platform-compare-guidance").textContent).toContain("differ by benchmark");
    expect(screen.queryByText("Use sticky tray to compare")).toBeNull();

    const compareLink = screen.getAllByRole("link", { name: /Compare 2 selected/ })[0] as HTMLAnchorElement;
    expect(compareLink.getAttribute("href")).toBe("/results/compare?ids=aaaabbbb,ccccdddd");
  });

  it("caps rendered platform rows and expands them with Show more", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue(
      Array.from({ length: 205 }, (_, index) =>
        makeRow({
          result_id: `r-many-${index}`,
          benchmark: "tpch",
          geomean_ms: index + 1,
          display_geomean_ms: index + 1,
          run_date: `2026-04-${String((index % 28) + 1).padStart(2, "0")}`,
        }),
      ),
    );

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    expect(screen.getByText("Showing 200 of 205 published runs")).toBeTruthy();
    expect(getRowOrder(container)).toHaveLength(200);

    fireEvent.click(screen.getByRole("button", { name: "Show more results" }));

    expect(getRowOrder(container)).toHaveLength(205);
    expect(screen.queryByText(/^Showing /)).toBeNull();
  });

  it("splits platform trend charts by comparable benchmark cohorts", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({
        result_id: "r-tpch-a",
        benchmark: "tpch",
        scale_factor: 0.1,
        run_date: "2026-04-01",
        geomean_ms: 10,
        display_geomean_ms: 10,
        phase: "power",
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "r-tpch-b",
        benchmark: "tpch",
        scale_factor: 0.1,
        run_date: "2026-04-02",
        geomean_ms: 12,
        display_geomean_ms: 12,
        phase: "power",
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "r-tpch-c",
        benchmark: "tpch",
        scale_factor: 0.1,
        run_date: "2026-04-03",
        geomean_ms: 11,
        display_geomean_ms: 11,
        phase: "power",
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "r-ssb-a",
        benchmark: "star_schema",
        scale_factor: 0.1,
        run_date: "2026-04-01",
        geomean_ms: 20,
        display_geomean_ms: 20,
        phase: "power",
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "r-ssb-b",
        benchmark: "star_schema",
        scale_factor: 0.1,
        run_date: "2026-04-02",
        geomean_ms: 22,
        display_geomean_ms: 22,
        phase: "power",
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "r-ssb-c",
        benchmark: "star_schema",
        scale_factor: 0.1,
        run_date: "2026-04-03",
        geomean_ms: 21,
        display_geomean_ms: 21,
        phase: "power",
        primary_metric: "display_geomean_ms",
      }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    openTrendsCard();

    const tpchTrend = screen.getByTestId("trend-cohort-tpch-sf0.1-power-display_geomean_ms");
    const ssbTrend = screen.getByTestId("trend-cohort-star_schema-sf0.1-power-display_geomean_ms");
    expect(screen.getAllByRole("img", { name: "Geomean latency trend over time" })).toHaveLength(2);
    expect(screen.getByText(/TPC-H · SF 0\.1 · power · Geomean latency/)).toBeTruthy();
    expect(screen.getByText(/SSB · SF 0\.1 · power · Geomean latency/)).toBeTruthy();
    expect(tpchTrend.querySelector('[data-result-id="r-tpch-a"]')).toBeTruthy();
    expect(tpchTrend.querySelector('[data-result-id="r-tpch-c"]')).toBeTruthy();
    expect(tpchTrend.querySelector('[data-result-id="r-ssb-a"]')).toBeNull();
    expect(ssbTrend.querySelector('[data-result-id="r-ssb-a"]')).toBeTruthy();
    expect(ssbTrend.querySelector('[data-result-id="r-ssb-c"]')).toBeTruthy();
    expect(ssbTrend.querySelector('[data-result-id="r-tpch-a"]')).toBeNull();
  });

  it("suppresses duplicate same-day trend charts and links the underlying runs", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({
        result_id: "tpch-duckdb-sf0.01-20260403-1111aaaa",
        benchmark: "tpch",
        scale_factor: 0.01,
        run_date: "2026-04-03",
        geomean_ms: 10,
        display_geomean_ms: 10,
        phase: "standard",
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "tpch-duckdb-sf0.01-20260403-2222bbbb",
        benchmark: "tpch",
        scale_factor: 0.01,
        run_date: "2026-04-03",
        geomean_ms: 12,
        display_geomean_ms: 12,
        phase: "standard",
        primary_metric: "display_geomean_ms",
      }),
      makeRow({
        result_id: "tpch-duckdb-sf0.01-20260404-3333cccc",
        benchmark: "tpch",
        scale_factor: 0.01,
        run_date: "2026-04-04",
        geomean_ms: 11,
        display_geomean_ms: 11,
        phase: "standard",
        primary_metric: "display_geomean_ms",
      }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    openTrendsCard();

    const trend = screen.getByTestId("trend-cohort-tpch-sf0.01-standard-display_geomean_ms");
    const duplicateState = within(trend).getByTestId("time-series-duplicate-day");
    expect(screen.queryByRole("img", { name: "Geomean latency trend over time" })).toBeNull();
    expect(duplicateState.textContent).toContain("cannot be ordered in this trend");
    expect(duplicateState.textContent).toContain("same-day runs");
    expect(duplicateState.textContent).toContain("Public ID 1111aaaa");
    expect(duplicateState.textContent).toContain("Public ID 2222bbbb");
    expect(duplicateState.querySelector('a[href="/results/r/tpch-duckdb-sf0.01-20260403-1111aaaa"]')).toBeTruthy();
    expect(
      duplicateState.querySelector('a[href="/results/r/tpch-duckdb-sf0.01-20260403-2222bbbb#run-receipt"]'),
    ).toBeTruthy();
  });

  it("replaces one- and two-observation trends with grammatical sparse-data states", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-tpch-a", benchmark: "tpch", run_date: "2026-04-01", geomean_ms: 10 }),
      makeRow({ result_id: "r-tpch-b", benchmark: "tpch", run_date: "2026-04-02", geomean_ms: 12 }),
      makeRow({
        result_id: "r-ssb-a",
        benchmark: "star_schema",
        scale_factor: 0.01,
        run_date: "2026-04-01",
        geomean_ms: 20,
        display_geomean_ms: 20,
      }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    openTrendsCard();

    expect(screen.queryByRole("img", { name: "Geomean latency trend over time" })).toBeNull();
    const plural = screen.getByTestId("trend-sparse-tpch-sf0.1-power-display_geomean_ms");
    const singular = screen.getByTestId("trend-sparse-star_schema-sf0.01-power-display_geomean_ms");
    expect(plural.textContent).toContain("2 published runs");
    expect(singular.textContent).toContain("1 published run");
    expect(plural.textContent).not.toContain("runin");
    expect(singular.textContent).not.toContain("runin");
    expect(plural.textContent).toContain("Geomean latency (lower is better)");
  });

  it("renders a funding chip on a row that discloses funding, and none otherwise", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-funded", short_id: "funded01", funding: "employer" }),
      makeRow({ result_id: "r-silent", short_id: "silent01", funding: "unspecified" }),
    ]);

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const fundingChips = container.querySelectorAll('[data-role="funding"]');
    expect(fundingChips.length).toBe(1);
    expect(fundingChips[0]?.textContent).toContain("Employer");
  });

  it("surfaces a not_run validation flag in the default (unscrolled) column set", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-not-run", short_id: "notrun01", validation_status: "not_run" }),
      makeRow({ result_id: "r-clean", short_id: "clean001", validation_status: "passed" }),
    ]);

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const notRunFlag = screen.getByTestId("platform-validation-flag-r-not-run");
    const runCell = notRunFlag.closest("td");
    expect(runCell?.querySelector('[data-testid="run-identity-label"]')).not.toBeNull();
    expect(within(notRunFlag).getByText("No validation")).toBeTruthy();

    expect(container.querySelector('[data-testid="platform-validation-flag-r-clean"]')).toBeNull();
  });

  it("exposes the provenance legend on a page that shows the badges", async () => {
    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());
    expect(screen.getByTestId("provenance-legend")).toBeTruthy();
  });

  it("supports grouping rows by engine version", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r1", short_id: "s1", driver_version: "9.9.9", platform_version: "1.4.0", geomean_ms: 10 }),
      makeRow({ result_id: "r2", short_id: "s2", driver_version: "9.9.9", platform_version: "1.3.2", geomean_ms: 20 }),
      makeRow({ result_id: "r3", short_id: "s3", driver_version: "8.8.8", platform_version: "1.4.0", geomean_ms: 15 }),
    ]);
    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByTestId("platform-group-by")).toBeTruthy());
    fireEvent.change(screen.getByTestId("platform-group-by"), { target: { value: "engine_version" } });
    expect(screen.getByText(/1.4.0 \(2 results\)/)).toBeTruthy();
    expect(screen.getByText(/1.3.2 \(1 result\)/)).toBeTruthy();
  });

  it("honors a version facet carried into the platform drill-down", async () => {
    window.history.replaceState(null, "", "/results/p/duckdb/?version=1.4.0");
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r14", short_id: "v140", platform_version: "1.4.0" }),
      makeRow({ result_id: "r13", short_id: "v132", platform_version: "1.3.2" }),
    ]);

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    expect(getRowOrder(container)).toEqual(["r14"]);
  });
});

describe("PlatformIndex - Analysis card grid", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState(null, "", "/results/p/duckdb/");
  });

  function openCoverageCard(): void {
    const details = screen.getByTestId("summary-chart-preview-coverage") as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle"));
  }

  it("draws up to four sparklines in the trends thumbnail, one per trendable ranking", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      ...Array.from({ length: 3 }, (_, i) =>
        makeRow({ result_id: `tpch-${i}`, benchmark: "tpch", scale_factor: 0.1, phase: "power", run_date: `2026-04-0${i + 1}`, primary_metric: "display_geomean_ms", display_geomean_ms: 10 + i }),
      ),
      ...Array.from({ length: 3 }, (_, i) =>
        makeRow({ result_id: `ssb-${i}`, benchmark: "star_schema", scale_factor: 0.1, phase: "power", run_date: `2026-04-0${i + 1}`, primary_metric: "display_geomean_ms", display_geomean_ms: 20 + i }),
      ),
    ]);

    const { container } = render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const card = screen.getByTestId("summary-chart-preview-trends");
    expect(within(card).getAllByRole("img", { name: /trend thumbnail/ })).toHaveLength(2);
    expect(container.querySelector("[data-testid='summary-chart-full-trends']")?.textContent).toBe("");
  });

  it("shows the no-trendable-metric message in the thumbnail when no ranking has enough runs", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r1", benchmark: "tpch", run_date: "2026-04-01" }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const card = screen.getByTestId("summary-chart-preview-trends");
    expect(within(card).getByText("No trendable metric values are available for the selected filters.")).toBeTruthy();
  });

  it("aggregates coverage into the top 5 benchmarks by run count plus an others bar, and lists every benchmark in the full table", async () => {
    const counts: Record<string, number> = { b1: 6, b2: 5, b3: 4, b4: 3, b5: 2, b6: 1, b7: 1 };
    const rows = Object.entries(counts).flatMap(([benchmark, count]) =>
      Array.from({ length: count }, (_, i) =>
        makeRow({ result_id: `${benchmark}-${i}`, benchmark, scale_factor: 0.1, run_date: `2026-04-0${(i % 9) + 1}` }),
      ),
    );
    vi.mocked(getPlatformIndexRows).mockResolvedValue(rows);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const thumbnail = screen.getByTestId("summary-chart-preview-coverage");
    expect(within(thumbnail).getByText("2 others")).toBeTruthy();
    expect(within(thumbnail).queryByText("B6")).toBeNull();

    openCoverageCard();
    const full = screen.getByTestId("summary-chart-full-coverage");
    for (const benchmark of Object.keys(counts)) {
      expect(within(full).getByText(benchmark.toUpperCase())).toBeTruthy();
    }
    const link = within(full).getByText("B1").closest("a") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/results/b1/");
  });

  it("links the coverage table's benchmark cell to the canonical benchmark slug", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r1", benchmark: "star_schema", scale_factor: 0.1, run_date: "2026-04-01" }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    openCoverageCard();
    const full = screen.getByTestId("summary-chart-full-coverage");
    const link = within(full).getByRole("link", { name: "SSB" }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/results/ssb/");
  });

  it("narrows both the trends and coverage cards when a filter is applied", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-tpch-a", benchmark: "tpch", scale_factor: 0.1, run_date: "2026-04-01" }),
      makeRow({ result_id: "r-tpch-b", benchmark: "tpch", scale_factor: 0.1, run_date: "2026-04-02" }),
      makeRow({ result_id: "r-tpch-c", benchmark: "tpch", scale_factor: 0.1, run_date: "2026-04-03" }),
      makeRow({ result_id: "r-ssb-a", benchmark: "star_schema", scale_factor: 0.2, run_date: "2026-04-01" }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    fireEvent.change(screen.getByTestId("platform-filter-benchmark"), { target: { value: "tpch" } });

    const trendsCard = screen.getByTestId("summary-chart-preview-trends");
    expect(within(trendsCard).getByRole("img", { name: /trend thumbnail/ })).toBeTruthy();

    openCoverageCard();
    const full = screen.getByTestId("summary-chart-full-coverage");
    expect(within(full).getAllByRole("row")).toHaveLength(2);
    expect(within(full).queryByText("SSB")).toBeNull();
  });

  it("still renders both cards with their empty styling, and no crash, when filters leave zero rows", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-tpch-a", benchmark: "tpch", scale_factor: 0.1, run_date: "2026-04-01" }),
      makeRow({ result_id: "r-ssb-a", benchmark: "star_schema", scale_factor: 0.2, run_date: "2026-04-01" }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    fireEvent.change(screen.getByTestId("platform-filter-benchmark"), { target: { value: "tpch" } });
    fireEvent.change(screen.getByTestId("platform-filter-scale"), { target: { value: "0.2" } });

    const trendsCard = screen.getByTestId("summary-chart-preview-trends");
    expect(within(trendsCard).getByText("No trendable metric values are available for the selected filters.")).toBeTruthy();
    const coverageCard = screen.getByTestId("summary-chart-preview-coverage");
    expect(within(coverageCard).getByText("No benchmark coverage is available for the selected filters.")).toBeTruthy();
    expect(document.querySelectorAll('[style*="NaN"]')).toHaveLength(0);
  });
});

describe("platform measurement basis", () => {
  it("reduces mixed benchmarks independently and reuses loaded executions", async () => {
    vi.clearAllMocks();
    window.history.replaceState(null, "", "/results/p/duckdb/");
    const rows = [makeRow({ result_id: "run-a", benchmark: "tpch", short_id: "aaaaaaaa" }), makeRow({ result_id: "run-b", benchmark: "clickbench", short_id: "bbbbbbbb" })];
    vi.mocked(getPlatformIndexRows).mockResolvedValue(rows);
    vi.mocked(getResultsBasisAvailability).mockResolvedValue([]);
    vi.mocked(getCohortBasisDetails).mockImplementation(async (ids) => {
      const map = new Map<string, import("@/types").DetailResult>();
      for (const id of ids) {
        map.set(id, {
          ...rows.find((row) => row.result_id === id)!,
          queries: [{ query_id: id === "run-a" ? "Q1" : "Q99", duration_ms: id === "run-a" ? 4 : 25, status: "pass", run_type: "measurement", iter: 1, stream: null }],
          display_timings: [],
          logical_query_count: 1,
        } as unknown as import("@/types").DetailResult);
      }
      return map;
    });
    render(<PlatformIndex platform="duckdb" />);
    const selector = await screen.findByRole("combobox", { name: "Measurement basis" });
    expect(getCohortBasisDetails).not.toHaveBeenCalled();
    fireEvent.change(selector, { target: { value: "all_warm:min" } });
    await waitFor(() => expect(within(screen.getByTestId("run-a")).getByText("4 ms")).toBeTruthy());
    expect(within(screen.getByTestId("run-b")).getByText("25 ms")).toBeTruthy();
    expect(screen.queryByText("3,000")).toBeNull();
    fireEvent.change(selector, { target: { value: "default" } });
    await waitFor(() => expect(within(screen.getByTestId("run-a")).getByText("15 ms")).toBeTruthy());
    fireEvent.change(selector, { target: { value: "all_warm:min" } });
    await waitFor(() => expect(within(screen.getByTestId("run-a")).getByText("4 ms")).toBeTruthy());
    expect(getCohortBasisDetails).toHaveBeenCalledTimes(1);
    expect(getCohortBasisDetails).toHaveBeenCalledWith(["run-a", "run-b"]);
    expect(getResultsBasisAvailability).toHaveBeenCalledTimes(1);
  });
});

it("updates selection eligibility when warmup availability differs from the published basis", async () => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/results/p/duckdb/");
  const rows = [
    makeRow({ result_id: "no-warmup", short_id: "cccccccc" }),
    makeRow({ result_id: "warmup-only", short_id: "dddddddd", comparison_exclusion_reason: "missing_timings", display_exclusion_reason: "missing_timings" }),
  ];
  vi.mocked(getPlatformIndexRows).mockResolvedValue(rows);
  vi.mocked(getResultsBasisAvailability).mockResolvedValue([{ result_id: "warmup-only", available_bases: "default,warmup", has_warmup: true, measurement_pass_count: 0, warmup_status: "available", varying_pass_queries: null }]);
  vi.mocked(getCohortBasisDetails).mockImplementation(async (ids) => {
    const map = new Map<string, import("@/types").DetailResult>();
    for (const id of ids) {
      map.set(id, {
        ...rows.find((row) => row.result_id === id)!,
        queries: ["Q1", "Q2"].map((query_id) => ({ query_id, duration_ms: 10, status: "pass", run_type: id === "warmup-only" ? "warmup" : "measurement", iter: id === "warmup-only" ? 0 : 1, stream: null })),
        display_timings: [], logical_query_count: 2,
      } as unknown as import("@/types").DetailResult);
    }
    return map;
  });
  render(<PlatformIndex platform="duckdb" />);
  const selector = await screen.findByRole("combobox", { name: "Measurement basis" });
  const unavailable = screen.getByTestId("platform-compare-checkbox-no-warmup") as HTMLInputElement;
  fireEvent.click(unavailable);
  expect(unavailable.checked).toBe(true);
  expect((screen.getByTestId("platform-compare-checkbox-warmup-only") as HTMLInputElement).disabled).toBe(true);
  await waitFor(() => expect(selector.querySelector('option[value="warmup"]')).toBeTruthy());
  fireEvent.change(selector, { target: { value: "warmup" } });
  await waitFor(() => expect(unavailable.disabled).toBe(true));
  await waitFor(() => expect(unavailable.checked).toBe(false));
  expect((screen.getByTestId("platform-compare-checkbox-warmup-only") as HTMLInputElement).disabled).toBe(false);
});

it("suspends comparison during pass loading and clears selections after a failed load", async () => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/results/p/duckdb/");
  vi.mocked(getPlatformIndexRows).mockResolvedValue([makeRow({ result_id: "first" }), makeRow({ result_id: "second" })]);
  vi.mocked(getResultsBasisAvailability).mockResolvedValue([]);
  let reject!: (error: Error) => void;
  vi.mocked(getCohortBasisDetails).mockImplementation(() => new Promise((_resolve, fail) => { reject = fail; }));
  render(<PlatformIndex platform="duckdb" />);
  const selector = await screen.findByRole("combobox", { name: "Measurement basis" });
  fireEvent.click(screen.getByTestId("platform-compare-checkbox-first"));
  fireEvent.click(screen.getByTestId("platform-compare-checkbox-second"));
  expect(screen.getByTestId("compare-tray-compare-link")).toBeTruthy();
  fireEvent.change(selector, { target: { value: "all_warm:min" } });
  await waitFor(() => expect(screen.queryByTestId("compare-tray-compare-link")).toBeNull());
  reject(new Error("Pass loading failed"));
  await waitFor(() => expect(screen.getByText(/Could not load measurement passes/)).toBeTruthy());
  expect((screen.getByTestId("platform-compare-checkbox-first") as HTMLInputElement).checked).toBe(false);
  expect((screen.getByTestId("platform-compare-checkbox-second") as HTMLInputElement).checked).toBe(false);
  expect(screen.queryByTestId("compare-tray-compare-link")).toBeNull();
  fireEvent.change(selector, { target: { value: "default" } });
  await waitFor(() => expect((screen.getByTestId("platform-compare-checkbox-first") as HTMLInputElement).disabled).toBe(false));
  fireEvent.click(screen.getByTestId("platform-compare-checkbox-first"));
  fireEvent.click(screen.getByTestId("platform-compare-checkbox-second"));
  expect(screen.getByTestId("compare-tray-compare-link")).toBeTruthy();
});

describe("PlatformIndex - accepted-override validation badges", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState(null, "", "/results/p/duckdb/");
  });

  it("badges an accepted override in both cells even when validation passed", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-over", validation_status: "passed", override_rules: '["timing-plateau"]' }),
      makeRow({ result_id: "r-clean", validation_status: "passed" }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    expect(screen.getByTestId("platform-validation-flag-r-over")).toBeTruthy();
    expect(screen.queryByTestId("platform-validation-flag-r-clean")).toBeNull();

    const row = screen.getByTestId("r-over");
    expect(within(row).getAllByText("Timing-plateau")).toHaveLength(2);
  });
});

describe("PlatformIndex table cells", () => {
  it("shows the platform version, keeps the driver version in the title, and hides it when the platform version is missing", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-sf", short_id: "sf000001", platform_version: "10.35.101", driver_version: "4.7.3" }),
      makeRow({ result_id: "r-bq", short_id: "bq000001", run_date: "2026-04-02", platform_version: null, driver_version: "2.1.0" }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const table = screen.getByRole("table", { name: "DuckDB results" });
    expect(within(table).getByText("v10.35.101")).toBeTruthy();
    expect(within(table).queryByText("v4.7.3")).toBeNull();
    expect(within(table).queryByText("v2.1.0")).toBeNull();
    expect(within(table).getByTitle(/Driver 4\.7\.3/)).toBeTruthy();
    expect(within(table).getByText("bq000001")).toBeTruthy();
  });

  it("shows distinct logical queries and keeps executions in the cell title", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-tpch", short_id: "tpch0001", query_count: 66, logical_query_count: 22 }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const table = screen.getByRole("table", { name: "DuckDB results" });
    const queryCell = within(table).getByTitle("66 query executions");
    expect(queryCell.textContent).toBe("22");
  });

  it("shows a dash and a client-machine title for managed cloud runs", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({
        result_id: "r-cloud",
        short_id: "cloud001",
        deployment_class: "cloud",
        cloud_provider: "snowflake",
        arch: "arm64",
        cpu_family: "apple_silicon",
        memory_gb: 16,
      }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const table = screen.getByRole("table", { name: "DuckDB results" });
    expect(within(table).queryByText("Arm64")).toBeNull();
    expect(within(table).queryByText("Apple silicon")).toBeNull();
    expect(within(table).queryByText("16 GB")).toBeNull();
    const clientCells = within(table).getAllByTitle(
      "Client machine: Arm64, Apple silicon, 16 GB (the warehouse hardware is managed by the provider)",
    );
    expect(clientCells).toHaveLength(3);
    expect(clientCells.every((cell) => cell.textContent === "—")).toBe(true);
  });

  it("shows a dash and a client-machine title for remote server runs", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({
        result_id: "r-remote",
        short_id: "remote01",
        deployment_class: "remote",
        cloud_provider: null,
        arch: "arm64",
        cpu_family: "apple_silicon",
        memory_gb: 16,
      }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const table = screen.getByRole("table", { name: "DuckDB results" });
    expect(within(table).queryByText("Arm64")).toBeNull();
    const clientCells = within(table).getAllByTitle(
      "Client machine: Arm64, Apple silicon, 16 GB (the remote server's hardware is not recorded)",
    );
    expect(clientCells).toHaveLength(3);
    expect(clientCells.every((cell) => cell.textContent === "—")).toBe(true);
  });

  it("marks a run with no ranking value as Not ranked and sentence-cases the phase", async () => {
    vi.mocked(getPlatformIndexRows).mockResolvedValue([
      makeRow({ result_id: "r-unranked", short_id: "unrk0001", phase: "power", power_score: null, primary_metric: "power_score" }),
      makeRow({ result_id: "r-ranked", short_id: "rnkd0001", run_date: "2026-04-02", phase: "power", power_score: 3000, primary_metric: "display_geomean_ms" }),
    ]);

    render(<PlatformIndex platform="duckdb" />);
    await waitFor(() => expect(screen.getByText("DuckDB Results")).toBeTruthy());

    const table = screen.getByRole("table", { name: "DuckDB results" });
    expect(within(table).getByText("Not ranked")).toBeTruthy();
    expect(within(table).queryByText("Power score ↑")).toBeNull();
    expect(within(table).getAllByText("Power").length).toBeGreaterThan(0);
    expect(within(table).queryByText("power")).toBeNull();
  });
});
