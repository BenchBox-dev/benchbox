import { render, screen, fireEvent, waitFor, within } from "@testing-library/preact";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { BenchmarkSummary } from "@/types";
import { expectNoAxeViolations } from "@/testing/axe-helper";

vi.mock("@/db", () => ({
  queryRows: vi.fn(),
}));

vi.mock("preact-router", async () => {
  const actual = await vi.importActual<typeof import("preact-router")>("preact-router");
  return {
    ...actual,
    route: vi.fn(),
  };
});

import { queryRows } from "@/db";
import { clearDuckdbQueryCachesForTests } from "@/lib/duckdbQueries";
import { BenchmarkIndex } from "@/pages/BenchmarkIndex";

const originalScrollIntoView = Element.prototype.scrollIntoView;
afterEach(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView;
});

const TIMING_ELIGIBLE = {
  has_display_timing: true,
  valid_query_count: 2,
  missing_query_count: 0,
  zero_timing_count: 0,
  display_exclusion_reason: null,
  comparison_exclusion_reason: null,
  ranking_exclusion_reason: null,
};

const NO_TIMING_INELIGIBLE = {
  has_display_timing: false,
  valid_query_count: 0,
  missing_query_count: 2,
  zero_timing_count: 0,
  display_exclusion_reason: "no_valid_display_timing",
  comparison_exclusion_reason: "insufficient_valid_timings",
  ranking_exclusion_reason: "insufficient_valid_timings",
};

const TIMING_CELL_ELIGIBLE = {
  is_valid_display_timing: true,
  timing_exclusion_reason: null,
};

const RESULT_ROWS = [
  {
    result_id: "r1",
    benchmark: "tpch",
    scale_factor: 0.1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: null,
    run_date: "2026-04-01",
    power_score: 3000,
    total_duration_s: 60,
    geomean_ms: 10,
    display_geomean_ms: 10,
    query_count: 2,
    ...TIMING_ELIGIBLE,
    trust_label: "maintainer-run",
    funding: "unspecified",
    visibility: "public-curated",
    platform_version: null,
    execution_mode: null,
    tuning_mode: null,
    tuning_hash: null,
    test_type: "power",
    validation_status: "exact",
    cost_usd: null,
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
    compliance_class: null,
    is_ranking_eligible: true,
    has_plans: false,
    has_tuning: false,
    bundle_download_url: "",
    arch: "arm64",
    cpu_family: "apple_silicon",
    memory_gb: 64,
  },
  {
    result_id: "r2",
    benchmark: "tpch",
    scale_factor: 0.1,
    platform: "SQLite",
    platform_id: "sqlite",
    driver_version: null,
    run_date: "2026-04-01",
    power_score: null,
    total_duration_s: 600,
    geomean_ms: 200,
    display_geomean_ms: 200,
    query_count: 2,
    ...TIMING_ELIGIBLE,
    ranking_exclusion_reason: "trust_not_rankable",
    trust_label: "community-submission",
    funding: "unspecified",
    visibility: "public-curated",
    platform_version: null,
    execution_mode: null,
    tuning_mode: null,
    tuning_hash: null,
    test_type: "power",
    validation_status: "loose",
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
    compliance_class: null,
    is_ranking_eligible: false,
    has_plans: false,
    has_tuning: false,
    bundle_download_url: "",
    arch: "x86_64",
    cpu_family: "amd_epyc",
    memory_gb: 128,
  },
];

const RANKING_ROWS = [
  {
    benchmark: "tpch",
    scale_factor: 0.1,
    phase: "power",
    result_id: "r1",
    platform_id: "duckdb",
    platform: "DuckDB",
    short_id: "aaaaaaaa",
    trust_label: "maintainer-run",
    funding: "unspecified",
    tuning_mode: null,
    tuning_hash: null,
    execution_mode: null,
    compliance_class: null,
    run_date: "2026-04-01",
    is_ranking_eligible: true,
    ...TIMING_ELIGIBLE,
    power_score: 3000,
    display_geomean_ms: 10,
    sample_geomean_ms: 12,
    cost_usd: null,
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
    primary_metric: "power_score",
    primary_order: "desc",
    rank: 1,
    total_in_cohort: 1,
    cohort_ranked_count: 1,
    cohort_ranking_exclusion_reason: null,
    percentile_p50: null,
    percentile_p90: null,
    percentile_p95: null,
    percentile_p99: null,
  },
  {
    benchmark: "tpch",
    scale_factor: 0.1,
    phase: "power",
    result_id: "r2",
    platform_id: "sqlite",
    platform: "SQLite",
    short_id: "bbbbbbbb",
    trust_label: "community-submission",
    funding: "unspecified",
    tuning_mode: null,
    tuning_hash: null,
    execution_mode: null,
    compliance_class: null,
    run_date: "2026-04-01",
    is_ranking_eligible: false,
    ...TIMING_ELIGIBLE,
    ranking_exclusion_reason: "trust_not_rankable",
    power_score: null,
    display_geomean_ms: 200,
    sample_geomean_ms: 220,
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
    primary_metric: "power_score",
    primary_order: "desc",
    rank: null,
    total_in_cohort: 1,
    cohort_ranked_count: 1,
    cohort_ranking_exclusion_reason: null,
    percentile_p50: null,
    percentile_p90: null,
    percentile_p95: null,
    percentile_p99: null,
  },
];

const CELL_ROWS = [
  { benchmark: "tpch", scale_factor: 0.1, phase: "power", result_id: "r1", platform_id: "duckdb", query_id: "Q1", display_ms: 10, ...TIMING_CELL_ELIGIBLE },
  { benchmark: "tpch", scale_factor: 0.1, phase: "power", result_id: "r1", platform_id: "duckdb", query_id: "Q2", display_ms: 20, ...TIMING_CELL_ELIGIBLE },
  { benchmark: "tpch", scale_factor: 0.1, phase: "power", result_id: "r2", platform_id: "sqlite", query_id: "Q1", display_ms: 100, ...TIMING_CELL_ELIGIBLE },
  { benchmark: "tpch", scale_factor: 0.1, phase: "power", result_id: "r2", platform_id: "sqlite", query_id: "Q2", display_ms: 200, ...TIMING_CELL_ELIGIBLE },
];

const SUMMARY: BenchmarkSummary = {
  benchmark: "tpch",
  scale_factor: 0.1,
  phase: "power",
  query_ids: ["Q1", "Q2"],
  platforms: [
    {
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
      ...TIMING_ELIGIBLE,
      power_score: 3000,
      display_geomean_ms: 10,
      compliance_class: null,
      sample_geomean_ms: 12,
      cost_usd: null,
      deployment_class: "cloud",
      instance_or_warehouse: "MEDIUM",
      percentile_stats: null,
      phase_durations: null,
      timings: { Q1: 10, Q2: 20 },
      timing_eligibility: {
        Q1: { is_valid_display_timing: true, timing_exclusion_reason: null },
        Q2: { is_valid_display_timing: true, timing_exclusion_reason: null },
      },
    },
    {
      result_id: "r2",
      short_id: "",
      platform_id: "sqlite",
      platform: "SQLite",
      platform_version: null,
      tuning_mode: null,
      tuning_hash: null,
      execution_mode: null,
      trust_label: "community-submission",
      funding: "unspecified",
      run_date: "2026-04-01",
      is_ranking_eligible: false,
      ...TIMING_ELIGIBLE,
      ranking_exclusion_reason: "trust_not_rankable",
      power_score: null,
      display_geomean_ms: 200,
      compliance_class: null,
      sample_geomean_ms: 220,
      cost_usd: null,
      deployment_class: "local",
      instance_or_warehouse: null,
      percentile_stats: null,
      phase_durations: null,
      timings: { Q1: 100, Q2: 200 },
      timing_eligibility: {
        Q1: { is_valid_display_timing: true, timing_exclusion_reason: null },
        Q2: { is_valid_display_timing: true, timing_exclusion_reason: null },
      },
    },
  ],
  cell_reduction: "median_successful_measurement_ms",
  ranking: {
    primary_metric: "power_score",
    secondary_metric: "display_geomean_ms",
    primary_order: "desc",
  },
};

type QueryRowsImpl = (sql: string, params?: unknown[]) => Promise<unknown[]>;

function defaultImpl(
  rows: readonly Record<string, unknown>[],
  rankings: readonly Record<string, unknown>[],
  cells: readonly Record<string, unknown>[],
  availableBenchmarks?: readonly string[],
): QueryRowsImpl {
  return async (sql: string) => {
    const s = String(sql).replace(/\s+/g, " ").trim();
    if (s.startsWith("SELECT DISTINCT") && s.includes("FROM bench.results")) {
      const slugs = availableBenchmarks
        ? [...availableBenchmarks]
        : [...new Set(rows.map((row) => String(row.benchmark)))].sort();
      return slugs.map((benchmark) => ({ benchmark }));
    }
    if (s.includes("FROM bench.results")) return [...rows];
    if (s.includes("FROM bench.benchmark_rankings")) return [...rankings];
    if (s.startsWith("SELECT benchmark, scale_factor, phase, result_id, platform_id, query_id, display_ms")) {
      return [...cells];
    }
    if (s.startsWith("SELECT result_id, phase, duration_s FROM bench.result_phase_durations")) return [];
    return [];
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  clearDuckdbQueryCachesForTests();
  window.history.replaceState(null, "", "/results/tpch/");
  vi.mocked(queryRows).mockImplementation(defaultImpl(RESULT_ROWS, RANKING_ROWS, CELL_ROWS));
});

function getRenderedResultOrder(container: ParentNode): string[] {
  return Array.from(container.querySelectorAll("tbody tr[data-testid]")).map(
    (row) => row.getAttribute("data-testid") ?? "",
  ).filter((id) => !id.startsWith("excluded-run-"));
}

function matrixRoot(container: ParentNode): ParentNode {
  const root = container.querySelector('[data-testid="summary-chart-preview-query_heatmap"]');
  if (!root) throw new Error("expected the query_heatmap Analysis card to be rendered");
  return root;
}
function listRoot(container: ParentNode): ParentNode {
  const root = container.querySelector("#benchmark-section-list");
  if (!root) throw new Error("expected #benchmark-section-list to be rendered");
  return root;
}

function openMatrixCard(container: ParentNode): void {
  const details = matrixRoot(container) as HTMLDetailsElement;
  details.open = true;
  fireEvent(details, new Event("toggle"));
}
function openRanksCard(container: ParentNode): void {
  const details = container.querySelector('[data-testid="summary-chart-preview-rank_table"]') as HTMLDetailsElement | null;
  if (!details) throw new Error("expected the rank_table Analysis card to be rendered");
  details.open = true;
  fireEvent(details, new Event("toggle"));
}

describe("BenchmarkIndex", () => {
  it("renders the page title", async () => {
    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByText("TPC-H Results")).toBeTruthy());
    expect(document.title).toBe("TPC-H · BenchBox Results");
  });

  it("sets the not-found title for unknown benchmark slugs", async () => {
    render(<BenchmarkIndex benchmark="does-not-exist" />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Page not found" })).toBeTruthy());
    await waitFor(() => expect(document.title).toBe("Not found · BenchBox Results"));
  });

  it("shows the query matrix card's query column headers once opened", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("summary-chart-preview-query_heatmap")).toBeTruthy());
    openMatrixCard(container);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Q1/ })).toBeTruthy();
      expect(screen.getByRole("button", { name: /^Q2/ })).toBeTruthy();
    });
  });

  it("states the cohort scope in the page header and renders the query matrix as the first Analysis card", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("cohort-counts")).toBeTruthy());
    const meta = screen.getByTestId("page-header-meta");
    expect(meta.textContent).toContain("2 published runs");
    expect(meta.textContent).toContain("2 queries");
    expect(screen.queryByText(/Jump to matrix/)).toBeNull();
    expect(container.querySelector("#benchmark-section-analysis")).not.toBeNull();
    expect(container.querySelector("#provenance-legend")).not.toBeNull();
    const cards = container.querySelectorAll("[data-testid^='summary-chart-preview-']");
    expect(cards.length).toBeGreaterThan(0);
    expect(cards[0]?.getAttribute("data-testid")).toBe("summary-chart-preview-query_heatmap");
    expect(within(cards[0] as HTMLElement).getByText("Query matrix")).toBeTruthy();
  });

  it("matrix view sorts rows from query headers", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("summary-chart-preview-query_heatmap")).toBeTruthy());
    openMatrixCard(container);
    await waitFor(() => expect(screen.getByRole("button", { name: /^Q1/ })).toBeTruthy());

    expect(getRenderedResultOrder(matrixRoot(container))).toEqual(["r1", "r2"]);
    fireEvent.click(screen.getByRole("button", { name: /^Q1/ }));
    expect(getRenderedResultOrder(matrixRoot(container))).toEqual(["r1", "r2"]);
    fireEvent.click(screen.getByRole("button", { name: /^Q1/ }));
    expect(getRenderedResultOrder(matrixRoot(container))).toEqual(["r2", "r1"]);
  });

  it("shows platform names from the summary", async () => {
    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
      expect(screen.getAllByText("SQLite").length).toBeGreaterThan(0);
    });
  });

  it("matrix rows expose receipt links and validation status from result metadata", async () => {
    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });

    expect(screen.queryByText("Run details")).toBeNull();
    const receiptLinks = screen.getAllByRole("link", { name: /Open receipt for/ }) as HTMLAnchorElement[];
    expect(receiptLinks[0]?.getAttribute("href")).toBe("/results/r/r1#run-receipt");
    expect(screen.getAllByText("Exact").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Loose").length).toBeGreaterThan(0);
  });

  it("reaches each run's receipt from its row, without a separate link list", async () => {
    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    expect(screen.queryByTestId("matrix-result-links")).toBeNull();
    expect(screen.queryByTestId("ranks-result-links")).toBeNull();
    expect(
      screen.getAllByRole("link", { name: /Open receipt for DuckDB/ })[0],
    ).toHaveAttribute("href", "/results/r/r1#run-receipt");
  });

  it("shows persistent compare guidance before any rows are selected", async () => {
    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("benchmark-compare-guidance")).toBeTruthy());

    const guidance = screen.getByTestId("benchmark-compare-guidance");
    const status = guidance.querySelector('[aria-live="polite"]');
    expect(status).toHaveAttribute("aria-atomic", "true");
    expect(status?.textContent).toContain("0 results selected");
    expect(guidance.textContent).toContain("Select two or more platforms");
    expect(screen.queryByRole("button", { name: /Select 2 / })).toBeNull();
    expect(screen.getByTestId("benchmark-compare-cta").textContent).toBe("Select 2–10 runs to compare");
  });

  it("renders a Benchmark switcher that routes to a sibling and clears benchmark-specific params", async () => {
    const preactRouter = await import("preact-router");
    const routeMock = vi.mocked(preactRouter.route);
    routeMock.mockClear();
    window.history.replaceState(null, "", "/results/tpch/?sf=10&phase=power&view=ranks");
    vi.mocked(queryRows).mockImplementation(
      defaultImpl(RESULT_ROWS, RANKING_ROWS, CELL_ROWS, ["tpch", "clickbench"]),
    );

    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByText("TPC-H Results")).toBeTruthy());
    await waitFor(() =>
      expect(within(screen.getByTestId("benchmark-switcher")).queryByRole("option", { name: "ClickBench" })).toBeTruthy(),
    );

    const switcher = screen.getByTestId("benchmark-switcher") as HTMLSelectElement;
    expect(switcher.value).toBe("tpch");
    expect(within(switcher).getByRole("option", { name: "ClickBench" })).toBeTruthy();
    expect(within(switcher).getByRole("option", { name: "TPC-H" })).toBeTruthy();

    fireEvent.change(switcher, { target: { value: "clickbench" } });
    expect(routeMock).toHaveBeenCalledTimes(1);
    expect(routeMock).toHaveBeenCalledWith("/results/clickbench/#benchmark-section-ranks");
  });

  it("preserves the clicked section over a stale legacy view when switching benchmarks", async () => {
    const { route } = await import("preact-router");
    window.history.replaceState(null, "", "/results/tpch/?view=ranks#benchmark-section-list");
    vi.mocked(queryRows).mockImplementation(defaultImpl(RESULT_ROWS, RANKING_ROWS, CELL_ROWS, ["tpch", "ssb"]));
    render(<BenchmarkIndex benchmark="tpch" />);
    await screen.findByRole("option", { name: "SSB" });
    fireEvent.change(screen.getByTestId("benchmark-switcher"), { target: { value: "ssb" } });
    expect(route).toHaveBeenCalledWith("/results/ssb/#benchmark-section-list");
  });

  it.each(["ssb", "star_schema"])("includes raw alias rows in the List section on /%s/", async (benchmark) => {
    const rows = RESULT_ROWS.map((row) => ({ ...row, benchmark: "star_schema" }));
    vi.mocked(queryRows).mockImplementation(defaultImpl(rows, [], [], ["ssb"]));
    render(<BenchmarkIndex benchmark={benchmark} />);
    expect(await screen.findByTestId("list-r1")).toBeTruthy();
  });

  it("hides no-result benchmarks from the switcher once the public corpus list resolves", async () => {
    vi.mocked(queryRows).mockImplementation(
      defaultImpl(RESULT_ROWS, RANKING_ROWS, CELL_ROWS, ["tpch", "clickbench"]),
    );

    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() =>
      expect(within(screen.getByTestId("benchmark-switcher")).queryByRole("option", { name: "ClickBench" })).toBeTruthy(),
    );

    const switcher = screen.getByTestId("benchmark-switcher") as HTMLSelectElement;
    const labels = Array.from(switcher.options).map((option) => option.textContent ?? "");
    expect(labels).toContain("TPC-H");
    expect(labels).toContain("ClickBench");
    expect(labels).not.toContain("AMPLab");
    expect(labels).not.toContain("TPC-DS");
  });

  it("does not expose a same-label alias unless that exact slug has public results", async () => {
    const legacyRows = RESULT_ROWS.map((row) => ({ ...row, benchmark: "ssb" }));
    const legacyRankings = RANKING_ROWS.map((row) => ({ ...row, benchmark: "ssb" }));
    const legacyCells = CELL_ROWS.map((row) => ({ ...row, benchmark: "ssb" }));
    vi.mocked(queryRows).mockImplementation(
      defaultImpl(legacyRows, legacyRankings, legacyCells, ["ssb", "tpch"]),
    );

    render(<BenchmarkIndex benchmark="ssb" />);
    await waitFor(() =>
      expect(within(screen.getByTestId("benchmark-switcher")).queryByRole("option", { name: "TPC-H" })).toBeTruthy(),
    );

    const switcher = screen.getByTestId("benchmark-switcher") as HTMLSelectElement;
    const optionValues = Array.from(switcher.options).map((option) => option.value);
    expect(switcher.value).toBe("ssb");
    expect(optionValues).toContain("ssb");
    expect(optionValues).not.toContain("star_schema");
    expect(optionValues).not.toContain("amplab");
  });

  it("hides no-result benchmarks even when the corpus-list query fails", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.startsWith("SELECT DISTINCT") && s.includes("FROM bench.results")) {
        throw new Error("simulated snapshot failure");
      }
      if (s.includes("FROM bench.results")) return [...RESULT_ROWS];
      if (s.includes("FROM bench.benchmark_rankings")) return [...RANKING_ROWS];
      if (s.startsWith("SELECT benchmark, scale_factor, phase, result_id, platform_id, query_id, display_ms")) {
        return [...CELL_ROWS];
      }
      if (s.startsWith("SELECT result_id, phase, duration_s FROM bench.result_phase_durations")) return [];
      return [];
    });

    render(<BenchmarkIndex benchmark="tpch" />);
    try {
      await waitFor(() =>
        expect(warnSpy).toHaveBeenCalledWith(
          expect.stringContaining("listBenchmarksWithPublicResults failed"),
          expect.any(Error),
        ),
      );

      const switcher = screen.getByTestId("benchmark-switcher") as HTMLSelectElement;
      const optionValues = Array.from(switcher.options).map((option) => option.value);
      expect(optionValues).toEqual(["tpch"]);
      expect(optionValues).not.toContain("amplab");
      expect(optionValues).not.toContain("clickbench");
    } finally {
      warnSpy.mockRestore();
    }
  });

  it.each([
    ["ssb", "star_schema"],
    ["tsbs_devops", "tsbs-devops"],
  ])("keeps legacy benchmark slug %s selected without surfacing empty alias %s", async (legacySlug, canonicalSlug) => {
    const legacyRows = RESULT_ROWS.map((row) => ({ ...row, benchmark: legacySlug }));
    const legacyRankings = RANKING_ROWS.map((row) => ({ ...row, benchmark: legacySlug }));
    const legacyCells = CELL_ROWS.map((row) => ({ ...row, benchmark: legacySlug }));
    vi.mocked(queryRows).mockImplementation(defaultImpl(legacyRows, legacyRankings, legacyCells));

    render(<BenchmarkIndex benchmark={legacySlug} />);
    await waitFor(() => expect(screen.getByTestId("benchmark-switcher")).toBeTruthy());

    const switcher = screen.getByTestId("benchmark-switcher") as HTMLSelectElement;
    const optionValues = Array.from(switcher.options).map((option) => option.value);
    expect(switcher.value).toBe(legacySlug);
    expect(optionValues).toContain(legacySlug);
    expect(optionValues).not.toContain(canonicalSlug);
  });

  it("labels the Star Schema Benchmark and SSB equivalence explicitly", async () => {
    const ssbRows = RESULT_ROWS.map((row) => ({ ...row, benchmark: "star_schema" }));
    const ssbRankings = RANKING_ROWS.map((row) => ({ ...row, benchmark: "star_schema" }));
    const ssbCells = CELL_ROWS.map((row) => ({ ...row, benchmark: "star_schema" }));
    vi.mocked(queryRows).mockImplementation(defaultImpl(ssbRows, ssbRankings, ssbCells));

    render(<BenchmarkIndex benchmark="star_schema" />);
    await waitFor(() => expect(screen.getByText("SSB Results")).toBeTruthy());

    expect(screen.getByTestId("benchmark-context-note").textContent).toContain("Star Schema Benchmark (SSB)");
  });

  it("offers Compare All Runs when the filtered view holds a small comparable set", async () => {
    const resultRows = [0, 1, 2].map((index) => ({
      ...RESULT_ROWS[0]!,
      result_id: `r-all-${index + 1}`,
      platform: `Platform ${index + 1}`,
      platform_id: `platform-${index + 1}`,
    }));
    const rankingRows = [0, 1, 2].map((index) => ({
      ...RANKING_ROWS[0]!,
      result_id: `r-all-${index + 1}`,
      short_id: `all000${index + 1}`,
      platform: `Platform ${index + 1}`,
      platform_id: `platform-${index + 1}`,
    }));
    const cellRows = rankingRows.flatMap((row) => [
      { ...CELL_ROWS[0]!, result_id: row.result_id, platform_id: row.platform_id },
      { ...CELL_ROWS[1]!, result_id: row.result_id, platform_id: row.platform_id },
    ]);
    vi.mocked(queryRows).mockImplementation(defaultImpl(resultRows, rankingRows, cellRows));

    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("summary-chart-preview-query_heatmap")).toBeTruthy());
    await waitFor(() => expect(screen.queryByTestId("benchmark-compare-cta")?.textContent).toBe("Compare All Runs"));

    const cta = screen.getByTestId("benchmark-compare-cta");
    expect(cta.getAttribute("href")).toBe("/results/compare?ids=all0001,all0002,all0003");
    expect(screen.getByTestId("benchmark-compare-guidance").textContent).toContain(
      "All 3 comparable runs in this view can be compared together.",
    );
  });

  it("makes Compare N selected the primary action when a selection exists in a small comparable set", async () => {
    const resultRows = [0, 1, 2].map((index) => ({
      ...RESULT_ROWS[0]!,
      result_id: `r-all-${index + 1}`,
      platform: `Platform ${index + 1}`,
      platform_id: `platform-${index + 1}`,
    }));
    const rankingRows = [0, 1, 2].map((index) => ({
      ...RANKING_ROWS[0]!,
      result_id: `r-all-${index + 1}`,
      short_id: `all000${index + 1}`,
      platform: `Platform ${index + 1}`,
      platform_id: `platform-${index + 1}`,
    }));
    const cellRows = rankingRows.flatMap((row) => [
      { ...CELL_ROWS[0]!, result_id: row.result_id, platform_id: row.platform_id },
      { ...CELL_ROWS[1]!, result_id: row.result_id, platform_id: row.platform_id },
    ]);
    vi.mocked(queryRows).mockImplementation(defaultImpl(resultRows, rankingRows, cellRows));

    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.queryByTestId("benchmark-compare-cta")?.textContent).toBe("Compare All Runs"));

    const checkboxes = await waitFor(() => {
      const boxes = within(listRoot(container) as HTMLElement).getAllByRole("checkbox") as HTMLInputElement[];
      expect(boxes.filter((box) => !box.disabled)).toHaveLength(3);
      return boxes;
    });
    fireEvent.click(checkboxes[0]!);
    fireEvent.click(checkboxes[1]!);

    await waitFor(() => expect(screen.getByTestId("benchmark-compare-cta").textContent).toBe("Compare 2 selected"));
    const primary = screen.getByTestId("benchmark-compare-cta") as HTMLAnchorElement;
    expect(primary.className).toContain("btn-primary");
    expect(primary.getAttribute("href")).toMatch(/^\/results\/compare\?ids=all000[12],all000[12]$/);
    const all = screen.getByTestId("benchmark-compare-cta-all") as HTMLAnchorElement;
    expect(all.textContent).toBe("Compare All Runs");
    expect(all.getAttribute("href")).toBe("/results/compare?ids=all0001,all0002,all0003");
    expect(screen.getByTestId("benchmark-compare-guidance").textContent).toContain("2 results selected");
  });

  it("compare tray shows selected metadata and public IDs while compare URLs keep short aliases", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    const list = () => listRoot(container) as HTMLElement;
    await waitFor(() => {
      const boxes = within(list()).getAllByRole("checkbox") as HTMLInputElement[];
      expect(boxes.filter((box) => !box.disabled)).toHaveLength(2);
    });
    const checkboxes = within(list()).getAllByRole("checkbox") as HTMLInputElement[];
    fireEvent.click(checkboxes[0]!);
    fireEvent.click(checkboxes[1]!);

    await waitFor(() => expect(screen.getByTestId("compare-tray-row-aaaaaaaa")).toBeTruthy());

    const duckRow = screen.getByTestId("compare-tray-row-aaaaaaaa");
    const sqliteRow = screen.getByTestId("compare-tray-row-bbbbbbbb");
    expect(duckRow.textContent).toContain("DuckDB");
    expect(duckRow.textContent).toContain("TPC-H");
    expect(duckRow.textContent).toContain("SF 0.1");
    expect(duckRow.textContent).toContain("power");
    expect(duckRow.textContent).toContain("2026-04-01");
    expect(duckRow.textContent).toContain("Public ID r1");
    expect(sqliteRow.textContent).toContain("SQLite");

    const compareLink = screen.getAllByRole("link", { name: /Compare 2 selected/ })[0] as HTMLAnchorElement;
    expect(compareLink.getAttribute("href")).toBe("/results/compare?ids=aaaaaaaa,bbbbbbbb");
  });

  it("blocks an eleventh comparison selection and describes the cap outside the accessible name", async () => {
    const resultRows = Array.from({ length: 11 }, (_, index) => ({
      ...RESULT_ROWS[0]!,
      result_id: `r-cap-${index + 1}`,
      platform: `Platform ${index + 1}`,
      platform_id: `platform-${index + 1}`,
      power_score: 3000 - index * 100,
      geomean_ms: 10 + index,
      display_geomean_ms: 10 + index,
    }));
    const rankingRows = Array.from({ length: 11 }, (_, index) => ({
      ...RANKING_ROWS[0]!,
      result_id: `r-cap-${index + 1}`,
      short_id: `cap0000${index + 1}`,
      platform: `Platform ${index + 1}`,
      platform_id: `platform-${index + 1}`,
      power_score: 3000 - index * 100,
      display_geomean_ms: 10 + index,
      rank: index + 1,
      total_in_cohort: 11,
      cohort_ranked_count: 11,
    }));
    const cellRows = rankingRows.flatMap((row, index) => [
      {
        ...CELL_ROWS[0]!,
        result_id: row.result_id,
        platform_id: row.platform_id,
        display_ms: 10 + index,
      },
      {
        ...CELL_ROWS[1]!,
        result_id: row.result_id,
        platform_id: row.platform_id,
        display_ms: 20 + index,
      },
    ]);
    vi.mocked(queryRows).mockImplementation(defaultImpl(resultRows, rankingRows, cellRows));

    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => {
      const boxes = within(listRoot(container) as HTMLElement).getAllByRole("checkbox") as HTMLInputElement[];
      expect(boxes.filter((box) => !box.disabled)).toHaveLength(11);
    });
    const list = listRoot(container) as HTMLElement;
    const checkboxes = within(list).getAllByRole("checkbox") as HTMLInputElement[];
    expect(checkboxes).toHaveLength(11);
    for (const checkbox of checkboxes.slice(0, 10)) fireEvent.click(checkbox);

    const eleventh = within(list).getAllByRole("checkbox")[10] as HTMLInputElement;
    await waitFor(() => expect(eleventh.disabled).toBe(true));
    expect(eleventh.checked).toBe(false);
    expect(eleventh.getAttribute("aria-label")).not.toContain("Selection limit");
    const selectionLimit = screen.getByTestId("benchmark-selection-limit");
    expect(selectionLimit.id).toBe("benchmark-selection-limit");
    expect(eleventh.getAttribute("aria-describedby")).toBe(selectionLimit.id);
    expect(selectionLimit.textContent).toContain("Selection limit reached");
    expect(selectionLimit.textContent).toContain("Clear one selected run before adding another");
    expect(screen.getByTestId("benchmark-compare-guidance").textContent).toContain(
      "10 results selected (maximum)",
    );
    expect(screen.queryByText("Use sticky tray to compare")).toBeNull();
    expect(screen.getAllByRole("link", { name: /Compare 10 selected/ }).length).toBeGreaterThan(0);
  });

  it("excludes no-timing rows from analysis and keeps receipt provenance in Excluded runs", async () => {
    const noTimingResult = {
      ...RESULT_ROWS[0]!,
      result_id: "r3",
      platform: "NoTimingDB",
      platform_id: "notimingdb",
      power_score: null,
      geomean_ms: null,
      display_geomean_ms: null,
      query_count: 0,
      ...NO_TIMING_INELIGIBLE,
    };
    const noTimingRanking = {
      ...RANKING_ROWS[0]!,
      result_id: "r3",
      short_id: "cccccccc",
      platform: "NoTimingDB",
      platform_id: "notimingdb",
      power_score: null,
      display_geomean_ms: null,
      sample_geomean_ms: null,
      is_ranking_eligible: false,
      rank: null,
      ...NO_TIMING_INELIGIBLE,
    };
    vi.mocked(queryRows).mockImplementation(
      defaultImpl([...RESULT_ROWS, noTimingResult], [...RANKING_ROWS, noTimingRanking], CELL_ROWS),
    );

    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("summary-chart-preview-query_heatmap")).toBeTruthy());
    openMatrixCard(container);
    await waitFor(() => expect(screen.getByRole("button", { name: /^Q1/ })).toBeTruthy());

    expect(getRenderedResultOrder(matrixRoot(container))).toEqual(["r1", "r2"]);

    const excluded = screen.getByTestId("excluded-runs");
    expect(excluded.textContent).toContain("Excluded runs (1)");
    expect(within(excluded).getByTestId("excluded-run-r3")).toHaveTextContent("NoTimingDB");
    expect(within(excluded).getByTestId("excluded-run-r3")).toHaveTextContent("No valid display timing is available");
    expect(
      within(excluded).getByRole("link", {
        name: /Open receipt for NoTimingDB public ID r3 from 2026-04-01 \(.*days ago\)/,
      }),
    ).toHaveAttribute("href", "/results/r/r3#run-receipt");
  });

  it("preserves direct rank URLs but gates all-unrankable cohorts", async () => {
    window.history.replaceState(null, "", "/results/tpch/?view=ranks");
    const unrankableRows = RANKING_ROWS.map((row) => ({
      ...row,
      is_ranking_eligible: false,
      rank: null,
      ranking_exclusion_reason: "trust_not_rankable",
      cohort_ranked_count: 0,
      cohort_ranking_exclusion_reason: "no_rankable_results",
    }));
    vi.mocked(queryRows).mockImplementation(defaultImpl(RESULT_ROWS, unrankableRows, CELL_ROWS));

    render(<BenchmarkIndex benchmark="tpch" />);

    await waitFor(() => expect(screen.getByTestId("rank-gate-notice")).toBeTruthy());
    expect(screen.getByRole("heading", { name: "Query ranks" })).toBeTruthy();
    expect(screen.getByTestId("rank-gate-notice").textContent).toContain("Ranks are unavailable");
    expect(screen.getByTestId("rank-gate-notice").textContent).toContain("No results meet the requirements for ranking");
    expect(new URL(window.location.href).searchParams.get("view")).toBe("ranks");
    expect(screen.queryByRole("table", { name: /Rank/i })).toBeNull();
  });

  it("shows the rank table in the Query ranks card when the cohort is not rank-gated", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("summary-chart-preview-rank_table")).toBeTruthy());
    openRanksCard(container);
    await waitFor(() => expect(screen.getByTestId("summary-chart-full-rank_table").textContent).not.toBe(""));
    expect(screen.queryByTestId("rank-gate-notice")).toBeNull();
    expect(screen.getByRole("heading", { name: "Query ranks" })).toBeTruthy();
  });

  it("disables compare selection for benchmark rows excluded from comparison", async () => {
    const rows = RANKING_ROWS.map((row) =>
      row.result_id === "r2"
        ? {
            ...row,
            comparison_exclusion_reason: "insufficient_valid_timings",
          }
        : row,
    );
    vi.mocked(queryRows).mockImplementation(defaultImpl(RESULT_ROWS, rows, CELL_ROWS));

    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => {
      const boxes = screen.getAllByLabelText(/Select SQLite/) as HTMLInputElement[];
      expect(boxes.length).toBeGreaterThan(0);
      for (const box of boxes) {
        const describedBy = box.getAttribute("aria-describedby");
        expect(describedBy).toBeTruthy();
        expect(document.getElementById(describedBy!)?.textContent).toContain(
          "Why unavailable: Insufficient valid timings",
        );
      }
    });
    const sqliteCheckboxes = screen.getAllByLabelText(/Select SQLite/) as HTMLInputElement[];
    for (const sqliteCheckbox of sqliteCheckboxes) {
      expect(sqliteCheckbox.disabled).toBe(true);
    }
  });

  it("shows a benchmark zero-selectable recovery callout when all matrix rows are disabled", async () => {
    const blockedRankings = RANKING_ROWS.map((row) => ({
      ...row,
      comparison_exclusion_reason: "insufficient_query_coverage",
    }));
    vi.mocked(queryRows).mockImplementation(defaultImpl(RESULT_ROWS, blockedRankings, CELL_ROWS));

    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("benchmark-zero-selectable")).toBeTruthy());

    const callout = screen.getByTestId("benchmark-zero-selectable");
    expect(callout.textContent).toContain("No selectable compare rows");
    expect(callout.textContent).toContain("insufficient query coverage");
    expect(within(callout).getByRole("link", { name: "Choose another ranking" })).toHaveAttribute(
      "href",
      "/results/compare",
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("list-row-disabled-reason")[0]?.textContent).toContain(
        "Why unavailable: Insufficient query coverage",
      ),
    );
  });

  it("restores benchmark URL facets without letting cohort facets block option recovery", async () => {
    window.history.replaceState(
      null,
      "",
      "/results/tpch/?sf=0.1&phase=power&platform=duckdb&deployment=cloud&cost_status=normalized",
    );

    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByText(/Showing \d+ of \d+ results/)).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("cohort-counts")).toBeTruthy());

    expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    expect(within(listRoot(container) as HTMLElement).queryByText("SQLite")).toBeNull();

    const resultCall = vi
      .mocked(queryRows)
      .mock.calls.find(([sql]) => {
        const s = String(sql).replace(/\s+/g, " ").trim();
        return s.includes("bench.results") && s.includes("WHERE");
      });
    const resultSql = String(resultCall?.[0]);
    expect(resultSql).toContain("CASE WHEN benchmark = 'star_schema' THEN 'ssb'");
    expect(resultSql).toContain("(platform IN (?) OR platform_id IN (?))");
    expect(resultSql).toContain("deployment_class IN (?)");
    expect(resultSql).toContain("cost_status IN (?)");
    expect(resultSql).not.toContain("scale_factor IN (?)");
    expect(resultSql).not.toContain("test_type IN (?)");
    expect(resultCall?.[1]).toEqual(["tpch", "duckdb", "duckdb", "cloud", "normalized"]);

    const rankingCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) =>
      String(sql).replace(/\s+/g, " ").includes("FROM bench.benchmark_rankings"),
    );
    expect(rankingCalls[rankingCalls.length - 1]?.[1]).toEqual(["tpch", 0.1, "power"]);
  });

  it("keeps a filter with no real choice visible but disabled, with an explanation", async () => {
    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByText(/Showing \d+ of \d+ results/)).toBeTruthy());

    const versionFilter = screen.getByTestId("benchmark-version-filter") as HTMLSelectElement;
    expect(versionFilter).toBeDisabled();
    expect(versionFilter.title).toBe("No data recorded for this filter in the current results.");
  });

  it("filters the cohort by architecture", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByText(/Showing \d+ of \d+ results/)).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("cohort-counts")).toBeTruthy());
    const list = () => listRoot(container) as HTMLElement;
    expect(within(list()).getByText("SQLite")).toBeTruthy();

    const archFilter = screen.getByTestId("benchmark-arch-filter") as HTMLSelectElement;
    expect(archFilter).not.toBeDisabled();
    fireEvent.change(archFilter, { target: { value: "arm64" } });
    await waitFor(() => expect(within(list()).queryByText("SQLite")).toBeNull());
    expect(within(list()).getByText("DuckDB")).toBeTruthy();
  });

  it("filters the cohort by CPU family", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByText(/Showing \d+ of \d+ results/)).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("cohort-counts")).toBeTruthy());
    const list = () => listRoot(container) as HTMLElement;
    expect(within(list()).getByText("DuckDB")).toBeTruthy();

    const cpuFamilyFilter = screen.getByTestId("benchmark-cpu-family-filter") as HTMLSelectElement;
    fireEvent.change(cpuFamilyFilter, { target: { value: "amd_epyc" } });
    await waitFor(() => expect(within(list()).queryByText("DuckDB")).toBeNull());
    expect(within(list()).getByText("SQLite")).toBeTruthy();
  });

  it("filters the cohort by memory", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByText(/Showing \d+ of \d+ results/)).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("cohort-counts")).toBeTruthy());
    const list = () => listRoot(container) as HTMLElement;
    expect(within(list()).getByText("SQLite")).toBeTruthy();

    const memoryFilter = screen.getByTestId("benchmark-memory-filter") as HTMLSelectElement;
    fireEvent.change(memoryFilter, { target: { value: "64" } });
    await waitFor(() => expect(within(list()).queryByText("SQLite")).toBeNull());
    expect(within(list()).getByText("DuckDB")).toBeTruthy();
  });

  it("list section renders its own Geomean column, and opening the query matrix card exposes its own query columns", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Geomean/ })).toBeTruthy());
    expect(screen.queryByRole("button", { name: /^Q1/ })).toBeNull();

    await waitFor(() => expect(screen.getByTestId("summary-chart-preview-query_heatmap")).toBeTruthy());
    openMatrixCard(container);
    await waitFor(() => expect(screen.getByRole("button", { name: /^Q1/ })).toBeTruthy());

    expect(screen.getByRole("button", { name: /^Q2/ })).toBeTruthy();
    expect(screen.getAllByLabelText(/^Run date .* days ago\)\./).length).toBeGreaterThan(0);
  });

  it("scrolls to the Results section for a `?view=list` deep link, leaving the Analysis cards intact", async () => {
    window.history.replaceState(null, "", "/results/tpch/?view=list");
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    const { container } = render(<BenchmarkIndex benchmark="tpch" />);

    await waitFor(() => expect(screen.getByRole("button", { name: /Geomean/ })).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("summary-chart-preview-query_heatmap")).toBeTruthy());
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith(expect.objectContaining({ block: "start" })));
    const listSection = container.querySelector("#benchmark-section-list");
    expect(scrollIntoView.mock.instances[0]).toBe(listSection);
  });

  it("waits for the matrix before honoring a section hash and prefers it to legacy view", async () => {
    window.history.replaceState(null, "", "/results/tpch/?view=ranks#benchmark-section-list");
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    let finishRanking!: (rows: unknown[]) => void;
    const ranking = new Promise<unknown[]>((resolve) => { finishRanking = resolve; });
    const fallback = defaultImpl(RESULT_ROWS, RANKING_ROWS, CELL_ROWS);
    vi.mocked(queryRows).mockImplementation((sql, params) =>
      sql.includes("FROM bench.benchmark_rankings") ? ranking : fallback(sql, params),
    );
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await screen.findByTestId("list-r1");
    expect(scrollIntoView).not.toHaveBeenCalled();
    finishRanking([...RANKING_ROWS]);
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    expect(scrollIntoView.mock.instances[0]).toBe(container.querySelector("#benchmark-section-list"));

    window.history.pushState(null, "", "/results/tpch/#benchmark-section-ranks");
    fireEvent(window, new Event("hashchange"));
    const rankCard = container.querySelector("#benchmark-section-ranks");
    expect(rankCard).not.toBeNull();
    await waitFor(() => expect(scrollIntoView.mock.instances).toContain(rankCard));
  });

  it("anchors every saveable analysis card so saved views deep-link to their chart", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    const chartIds = [
      "query_heatmap",
      "percentile_ladder",
      "cdf_chart",
      "query_histogram",
      "stacked_phase",
      "time_series",
      "rank_table",
      "cost_scatter",
    ];
    for (const chartId of chartIds) {
      const card = await screen.findByTestId(`summary-chart-preview-${chartId}`);
      expect(card.getAttribute("id")).toMatch(/^benchmark-section-/);
    }
    const anchorIds = chartIds.map((chartId) =>
      container.querySelector(`[data-testid="summary-chart-preview-${chartId}"]`)?.getAttribute("id")
    );
    expect(new Set(anchorIds).size).toBe(chartIds.length);
  });

  it("opens the saved analysis card for a card deep link", async () => {
    window.history.replaceState(null, "", "/results/tpch/#benchmark-section-cdf");
    Element.prototype.scrollIntoView = vi.fn();

    render(<BenchmarkIndex benchmark="tpch" />);

    await waitFor(() =>
      expect(screen.getByTestId("summary-chart-full-cdf_chart").textContent).not.toBe("")
    );
  });

  it("list view sorts rows from table headers", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Geomean/ })).toBeTruthy());
    expect(getRenderedResultOrder(listRoot(container))).toEqual(["list-r1", "list-r2"]);
    fireEvent.click(screen.getByRole("button", { name: /Geomean/ }));
    expect(getRenderedResultOrder(listRoot(container))).toEqual(["list-r2", "list-r1"]);
  });

  it("list view renders Arch and CPU headers and sorts by them", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Geomean/ })).toBeTruthy());

    const list = listRoot(container) as HTMLElement;
    expect(within(list).queryByText("Arm64")).toBeNull();
    expect(within(list).queryByText("Apple silicon")).toBeNull();
    expect(list.querySelector('[title^="Client machine: Arm64, Apple silicon"]')).not.toBeNull();
    expect(within(list).getByText("x86-64")).toBeTruthy();
    expect(within(list).getByText("AMD EPYC")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^Arch/ }));
    expect(getRenderedResultOrder(listRoot(container))).toEqual(["list-r1", "list-r2"]);

    fireEvent.click(screen.getByRole("button", { name: /^Arch/ }));
    expect(getRenderedResultOrder(listRoot(container))).toEqual(["list-r2", "list-r1"]);

    fireEvent.click(screen.getByRole("button", { name: /^CPU/ }));
    expect(getRenderedResultOrder(listRoot(container))).toEqual(["list-r2", "list-r1"]);

    fireEvent.click(screen.getByRole("button", { name: /^CPU/ }));
    expect(getRenderedResultOrder(listRoot(container))).toEqual(["list-r1", "list-r2"]);
  });

  it("list view shows dashes and the remote client title for a remote run", async () => {
    const remoteRows = RESULT_ROWS.map((row) =>
      row.result_id === "r1" ? { ...row, deployment_class: "remote", cloud_provider: null } : row,
    );
    vi.mocked(queryRows).mockImplementation(defaultImpl(remoteRows, RANKING_ROWS, CELL_ROWS));
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Geomean/ })).toBeTruthy());

    const remoteRow = within(listRoot(container) as HTMLElement).getByTestId("list-r1");
    const hardwareCells = remoteRow.querySelectorAll('td[title^="Client machine: Arm64, Apple silicon"]');
    expect(hardwareCells).toHaveLength(3);
    hardwareCells.forEach((cell) => {
      expect(cell.textContent).toBe("—");
      expect(cell.getAttribute("title")).toBe(
        "Client machine: Arm64, Apple silicon, 64 GB (the remote server's hardware is not recorded)",
      );
    });
  });

  it("list view caps rendered rows and expands them with Show more", async () => {
    const manyRows = Array.from({ length: 205 }, (_, index) => ({
      ...RESULT_ROWS[0]!,
      result_id: `r-list-${index}`,
      platform: `DuckDB ${index}`,
      platform_id: `duckdb-${index}`,
      display_geomean_ms: index + 1,
      geomean_ms: index + 1,
    }));
    let finishRanking!: (rows: unknown[]) => void;
    const ranking = new Promise<unknown[]>((resolve) => { finishRanking = resolve; });
    const fallback = defaultImpl(manyRows, RANKING_ROWS, CELL_ROWS);
    vi.mocked(queryRows).mockImplementation((sql, params) =>
      sql.includes("FROM bench.benchmark_rankings") ? ranking : fallback(sql, params),
    );

    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByText("Showing 200 of 205 results for SF 0.1")).toBeTruthy());
    expect(getRenderedResultOrder(listRoot(container))).toHaveLength(200);
    const list = listRoot(container) as HTMLElement;
    expect(within(list).getByRole("status")).toHaveTextContent("Compare eligibility is loading.");
    const pendingCheckbox = within(list).getAllByRole("checkbox")[0]!;
    expect(pendingCheckbox).toBeDisabled();
    expect(pendingCheckbox).toHaveAttribute("aria-describedby", "benchmark-compare-eligibility-status");
    expect(within(list).queryByTestId("list-row-disabled-reason")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show more results" }));

    await waitFor(() => expect(getRenderedResultOrder(listRoot(container))).toHaveLength(205));
    expect(screen.getByText("Showing 205 of 205 results for SF 0.1")).toBeTruthy();

    finishRanking([...RANKING_ROWS]);
    await waitFor(() => expect(screen.getByTestId("summary-chart-preview-query_heatmap")).toBeTruthy());
    openMatrixCard(container);
    await waitFor(() => expect(screen.getByRole("button", { name: /^Q1/ })).toBeTruthy());
    expect(getRenderedResultOrder(listRoot(container))).toHaveLength(205);
    expect(screen.getByText("Showing 205 of 205 results for SF 0.1")).toBeTruthy();
  }, 15_000);

  it("supports grouping rows by engine version in list view", async () => {
    const listRows = [
      { ...RESULT_ROWS[0]!, result_id: "r1", driver_version: "9.9.9", platform_version: "1.4.0", display_geomean_ms: 10 },
      { ...RESULT_ROWS[1]!, result_id: "r2", driver_version: "9.9.9", platform_version: "1.3.2", display_geomean_ms: 20 },
      { ...RESULT_ROWS[0]!, result_id: "r3", driver_version: "8.8.8", platform_version: "1.4.0", display_geomean_ms: 15 },
    ];
    vi.mocked(queryRows).mockImplementation(defaultImpl(listRows, RANKING_ROWS, CELL_ROWS));

    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("benchmark-list-group-by")).toBeTruthy());
    fireEvent.change(screen.getByTestId("benchmark-list-group-by"), { target: { value: "engine_version" } });
    expect(screen.getByText(/1.4.0 \(2 results\)/)).toBeTruthy();
    expect(screen.getByText(/1.3.2 \(1 result\)/)).toBeTruthy();
  });

  it("shows the platform version in the run label, the driver version only in its title, and distinct query counts", async () => {
    window.history.replaceState(null, "", "/results/tpch/?view=list");
    const listRows = [
      {
        ...RESULT_ROWS[0]!,
        result_id: "r1",
        platform_version: "2.0.0-alpha38615",
        driver_version: "1.6.0.dev365",
        query_count: 66,
        logical_query_count: 22,
      },
      { ...RESULT_ROWS[0]!, result_id: "r2", platform_version: "1.4.0", driver_version: "1.6.0.dev365" },
    ];
    vi.mocked(queryRows).mockImplementation(defaultImpl(listRows, RANKING_ROWS, CELL_ROWS));

    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(getRenderedResultOrder(listRoot(container))).toEqual(["list-r1", "list-r2"]));

    const list = listRoot(container) as HTMLElement;
    const label = within(list).getAllByTestId("run-identity-label")[0]!;
    expect(label.textContent).toContain("v2.0.0");
    expect(label.textContent).not.toContain("1.6.0.dev365");
    expect(label.getAttribute("title")).toContain("Version v2.0.0-alpha38615");
    expect(label.getAttribute("title")).toContain("Driver 1.6.0.dev365");
    const queryCell = within(list).getByTitle("66 query executions");
    expect(queryCell.textContent).toBe("22");
  });

  it("honors a version facet carried into the benchmark drill-down", async () => {
    window.history.replaceState(null, "", "/results/tpch/?version=1.4.0&view=list");
    const listRows = [
      { ...RESULT_ROWS[0]!, result_id: "r1", platform_version: "1.4.0" },
    ];
    vi.mocked(queryRows).mockImplementation(defaultImpl(listRows, RANKING_ROWS, CELL_ROWS));

    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => screen.getAllByText("DuckDB"));

    expect(getRenderedResultOrder(listRoot(container))).toEqual(["list-r1"]);
    expect(vi.mocked(queryRows).mock.calls.some(([sql]) => String(sql).includes("platform_version IN (?)"))).toBe(true);
  });

  it("trust tier select offers every trust tier present in the cohort", async () => {
    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => screen.getAllByText("DuckDB"));

    await waitFor(() => expect(screen.getByTestId("benchmark-trust-filter")).not.toBeDisabled());
    const trustFilter = screen.getByTestId("benchmark-trust-filter") as HTMLSelectElement;
    const optionLabels = Array.from(trustFilter.options).map((option) => option.text);
    expect(optionLabels).toContain("Maintainer Run");
    expect(optionLabels).toContain("Community Submission");
  });

  it("keeps active matrix controls on the data surface theme contract", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("summary-chart-preview-query_heatmap")).toBeTruthy());
    openMatrixCard(container);

    const reducedColor = await screen.findByRole("button", { name: "Reduced color" });
    fireEvent.click(reducedColor);

    expect(reducedColor.className).toContain("--bb-tone-info-bg");
    expect(reducedColor.className).not.toContain("--bb-bg-elevated");
  });

  it("selecting a trust tier hides other tiers' rows without extra fetch", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("benchmark-trust-filter")).not.toBeDisabled());
    const trustFilter = screen.getByTestId("benchmark-trust-filter") as HTMLSelectElement;
    const list = () => listRoot(container) as HTMLElement;

    const callsBefore = vi.mocked(queryRows).mock.calls.length;

    fireEvent.change(trustFilter, { target: { value: "maintainer-run" } });

    await waitFor(() => expect(within(list()).queryByText("SQLite")).toBeNull());

    expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);

    expect(vi.mocked(queryRows).mock.calls.length).toBe(callsBefore);
  });

  it("keeps filtered-out selections in the status and compare tray", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    const list = () => listRoot(container) as HTMLElement;
    await waitFor(() => {
      const boxes = within(list()).getAllByRole("checkbox") as HTMLInputElement[];
      expect(boxes.filter((box) => !box.disabled)).toHaveLength(2);
    });
    const checkboxes = within(list()).getAllByRole("checkbox") as HTMLInputElement[];
    fireEvent.click(checkboxes[0]!);
    fireEvent.click(checkboxes[1]!);

    await waitFor(() => expect(screen.getAllByRole("link", { name: /Compare 2 selected/ }).length).toBeGreaterThan(0));
    fireEvent.change(screen.getByTestId("benchmark-trust-filter"), { target: { value: "maintainer-run" } });

    await waitFor(() => expect(within(list()).queryByText("SQLite")).toBeNull());
    expect(screen.getByTestId("benchmark-compare-guidance").textContent).toContain("2 results selected");
    expect(screen.getByTestId("compare-tray-row-bbbbbbbb").textContent).toContain("SQLite");
    expect(screen.getAllByRole("link", { name: /Compare 2 selected/ }).length).toBeGreaterThan(0);
  });

  it("empty cohort omits the Analysis section (and its matrix/ranks cards) entirely", async () => {
    vi.mocked(queryRows).mockImplementation(defaultImpl(RESULT_ROWS, [], []));
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => expect(screen.getByTestId("benchmark-compare-guidance")).toBeTruthy());
    expect(container.querySelector("#benchmark-section-analysis")).toBeNull();
    expect(container.querySelector("[data-testid^='summary-chart-preview-']")).toBeNull();
  });

  it("has no serious/critical axe violations when matrix is loaded", async () => {
    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => screen.getAllByText("DuckDB"));
    await expectNoAxeViolations(container);
  });

  it("QueryHeatmap has no serious/critical axe violations", async () => {
    const { QueryHeatmap } = await import("@/components/QueryHeatmap");
    const { container } = render(<QueryHeatmap summary={SUMMARY} />);
    await expectNoAxeViolations(container);
  });

  it("loads throughput-phase cohort when only throughput is available for the SF", async () => {
    const throughputRows = RESULT_ROWS.map((r) => ({ ...r, test_type: "throughput" }));
    const throughputRankings = RANKING_ROWS.map((r) => ({ ...r, phase: "throughput" }));
    const throughputCells = CELL_ROWS.map((c) => ({ ...c, phase: "throughput" }));
    vi.mocked(queryRows).mockImplementation(defaultImpl(throughputRows, throughputRankings, throughputCells));

    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => screen.getAllByText("DuckDB"));
    await waitFor(() =>
      expect(new URL(window.location.href).searchParams.get("phase")).toBe("throughput"),
    );

    const rankingCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) =>
      String(sql).replace(/\s+/g, " ").includes("FROM bench.benchmark_rankings"),
    );
    expect(rankingCalls[rankingCalls.length - 1]?.[1]).toEqual(["tpch", 0.1, "throughput"]);
  });

  it("coerces an unavailable phase URL value to the rendered cohort phase", async () => {
    window.history.replaceState(null, "", "/results/tpch/?sf=0.1&phase=throughput");

    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => screen.getAllByText("DuckDB"));

    await waitFor(() =>
      expect(new URL(window.location.href).searchParams.get("phase")).toBeNull(),
    );

    const rankingCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) =>
      String(sql).replace(/\s+/g, " ").includes("FROM bench.benchmark_rankings"),
    );
    expect(rankingCalls[rankingCalls.length - 1]?.[1]).toEqual(["tpch", 0.1, "power"]);
  });

  it("coerces an invalid scale-factor URL value to the rendered scale", async () => {
    window.history.replaceState(null, "", "/results/tpch/?sf=abc");

    render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => screen.getAllByText("DuckDB"));

    await waitFor(() =>
      expect(new URL(window.location.href).searchParams.get("sf")).toBe("0.1"),
    );

    const rankingCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) =>
      String(sql).replace(/\s+/g, " ").includes("FROM bench.benchmark_rankings"),
    );
    expect(rankingCalls[rankingCalls.length - 1]?.[1]).toEqual(["tpch", 0.1, "power"]);
  });

  it("filters list view rows by the selected phase when multiple phases exist at the same scale", async () => {
    const mixedRows = [
      ...RESULT_ROWS,
      {
        ...RESULT_ROWS[0],
        result_id: "throughput-r1",
        test_type: "throughput",
        platform: "ClickHouse",
        platform_id: "clickhouse",
      },
    ];
    vi.mocked(queryRows).mockImplementation(defaultImpl(mixedRows, RANKING_ROWS, CELL_ROWS));

    const { container } = render(<BenchmarkIndex benchmark="tpch" />);
    await waitFor(() => screen.getAllByText("DuckDB"));

    const listSection = container.querySelector("#benchmark-section-list");
    expect(listSection).toBeTruthy();
    expect(within(listSection as HTMLElement).getByText("Showing 2 of 2 results for SF 0.1")).toBeTruthy();
    expect(within(listSection as HTMLElement).queryByText("ClickHouse")).toBeNull();
  });

  describe("throughput cohorts split by stream count", () => {
    function throughputFixtures() {
      const base = RESULT_ROWS[0]!;
      const rows = [
        { ...base, result_id: "tp-spark-3", platform: "Spark", platform_id: "spark", test_type: "throughput", stream_count: 3, throughput_at_size: 3741, power_score: null },
        { ...base, result_id: "tp-duck-3", platform: "DuckDB", platform_id: "duckdb", test_type: "throughput", stream_count: 3, throughput_at_size: 1500, power_score: null },
        { ...base, result_id: "tp-spark-2", platform: "Spark", platform_id: "spark", test_type: "throughput", stream_count: 2, throughput_at_size: 3500, power_score: null },
      ];
      const rankings = RANKING_ROWS.map((r) => ({ ...r, phase: "throughput", primary_metric: "throughput_at_size" }));
      const cells = CELL_ROWS.map((c) => ({ ...c, phase: "throughput" }));
      return { rows, rankings, cells };
    }

    function rankingParams() {
      return vi
        .mocked(queryRows)
        .mock.calls.filter(([sql]) => String(sql).replace(/\s+/g, " ").includes("FROM bench.benchmark_rankings"))
        .map(([, params]) => params);
    }

    it("offers a Streams filter and loads the stream count with the most runs by default", async () => {
      const { rows, rankings, cells } = throughputFixtures();
      vi.mocked(queryRows).mockImplementation(defaultImpl(rows, rankings, cells));

      render(<BenchmarkIndex benchmark="tpch" />);

      const streams = (await screen.findByTestId("benchmark-stream-filter")) as HTMLSelectElement;
      expect(Array.from(streams.options).map((option) => option.textContent)).toEqual(["2 streams", "3 streams"]);
      await waitFor(() => expect(streams.value).toBe("3"));
      await waitFor(() => expect(rankingParams().at(-1)).toEqual(["tpch", 0.1, "throughput", 3]));
      await waitFor(() => expect(screen.getByText("throughput (3 streams)")).toBeTruthy());
    });

    it("chooses the default stream count from rankable runs only", async () => {
      const { rows, rankings, cells } = throughputFixtures();
      const unrankable = rows.map((row) =>
        row.stream_count === 3 ? { ...row, ranking_exclusion_reason: "missing_primary_metric" } : row,
      );
      vi.mocked(queryRows).mockImplementation(defaultImpl(unrankable, rankings, cells));

      render(<BenchmarkIndex benchmark="tpch" />);

      const streams = (await screen.findByTestId("benchmark-stream-filter")) as HTMLSelectElement;
      await waitFor(() => expect(streams.value).toBe("2"));
      await waitFor(() => expect(rankingParams().at(-1)).toEqual(["tpch", 0.1, "throughput", 2]));
    });

    it("reloads the cohort when another stream count is selected", async () => {
      const { rows, rankings, cells } = throughputFixtures();
      vi.mocked(queryRows).mockImplementation(defaultImpl(rows, rankings, cells));

      render(<BenchmarkIndex benchmark="tpch" />);
      const streams = (await screen.findByTestId("benchmark-stream-filter")) as HTMLSelectElement;
      await waitFor(() => expect(streams.value).toBe("3"));

      fireEvent.change(streams, { target: { value: "2" } });

      await waitFor(() => expect(rankingParams().at(-1)).toEqual(["tpch", 0.1, "throughput", 2]));
      expect(new URL(window.location.href).searchParams.get("streams")).toBe("2");
    });

    it("coerces an unavailable streams URL value to the rendered stream count", async () => {
      const { rows, rankings, cells } = throughputFixtures();
      vi.mocked(queryRows).mockImplementation(defaultImpl(rows, rankings, cells));
      window.history.replaceState(null, "", "/results/tpch/?sf=0.1&phase=throughput&streams=9");

      render(<BenchmarkIndex benchmark="tpch" />);

      await waitFor(() => expect(new URL(window.location.href).searchParams.get("streams")).toBe("3"));
      await waitFor(() => expect(rankingParams().at(-1)).toEqual(["tpch", 0.1, "throughput", 3]));
    });

    it("does not show a Streams filter for a power cohort", async () => {
      render(<BenchmarkIndex benchmark="tpch" />);
      await waitFor(() => screen.getAllByText("DuckDB"));

      expect(screen.queryByTestId("benchmark-stream-filter")).toBeNull();
      await waitFor(() => expect(rankingParams().at(-1)).toEqual(["tpch", 0.1, "power"]));
    });
  });
});
