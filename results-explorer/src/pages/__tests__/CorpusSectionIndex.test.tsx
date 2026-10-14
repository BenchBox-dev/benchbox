import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/duckdbQueries", () => ({
  listResults: vi.fn(),
}));

import { listResults, type ResultRow } from "@/lib/duckdbQueries";
import { BenchmarksIndex } from "@/pages/BenchmarksIndex";
import { PlatformsIndex } from "@/pages/PlatformsIndex";

const ROWS = [
  resultRow({
    result_id: "duckdb-tpch",
    benchmark: "tpch",
    platform: "DuckDB",
    platform_id: "duckdb",
    run_date: "2026-08-20T12:00:00Z",
  }),
  resultRow({
    result_id: "sqlite-tpch",
    benchmark: "tpch",
    platform: "SQLite",
    platform_id: "sqlite",
    run_date: "2026-08-18T12:00:00Z",
  }),
  resultRow({
    result_id: "sqlite-ssb-legacy",
    benchmark: "star_schema",
    platform: "SQLite",
    platform_id: "sqlite",
    run_date: "2026-08-19T12:00:00Z",
  }),
  resultRow({
    result_id: "duckdb-ssb",
    benchmark: "ssb",
    platform: "DuckDB",
    platform_id: "duckdb",
    run_date: "2026-08-21T12:00:00Z",
  }),
] satisfies ResultRow[];

const SORT_ROWS = [
  ...ROWS,
  resultRow({
    result_id: "duckdb-tpch-older",
    benchmark: "tpch",
    platform: "DuckDB",
    platform_id: "duckdb",
    run_date: "2026-08-17T12:00:00Z",
  }),
] satisfies ResultRow[];

const SECTION_CASES = [
  {
    kind: "benchmarks",
    title: "Benchmarks",
    listId: "benchmarks-index-list",
    expectedCount: 2,
    expectedLinkName: /TPC-H/,
    expectedHref: "/results/tpch/",
    resultSortFirst: "TPC-H",
    recentSortFirst: "SSB",
  },
  {
    kind: "platforms",
    title: "Platforms",
    listId: "platforms-index-list",
    expectedCount: 2,
    expectedLinkName: /DuckDB/,
    expectedHref: "/results/p/duckdb/",
    resultSortFirst: "DuckDB",
    recentSortFirst: "DuckDB",
  },
] as const;

function renderIndex(kind: (typeof SECTION_CASES)[number]["kind"]) {
  return kind === "benchmarks" ? render(<BenchmarksIndex />) : render(<PlatformsIndex />);
}

describe("corpus section indexes", () => {
  beforeEach(() => {
    vi.mocked(listResults).mockReset();
    window.history.replaceState(null, "", "/results/benchmarks/");
  });

  it.each(SECTION_CASES)("$title keeps the loading state visible until the cold read resolves", ({ kind, listId }) => {
    vi.mocked(listResults).mockReturnValue(new Promise(() => {}));

    renderIndex(kind);

    expect(screen.getByRole("status")).toHaveTextContent(`Loading ${kind}...`);
    expect(screen.queryByTestId(listId)).toBeNull();
  });

  it.each(SECTION_CASES)(
    "$title retries one cold empty read before listing every canonical entry",
    async ({ kind, title, listId, expectedCount, expectedLinkName, expectedHref }) => {
      vi.mocked(listResults).mockResolvedValueOnce([]).mockResolvedValueOnce(ROWS);

      renderIndex(kind);

      const list = await screen.findByTestId(listId);
      expect(listResults).toHaveBeenCalledTimes(2);
      expect(within(list).getAllByRole("link")).toHaveLength(expectedCount);
      expect(within(list).getByRole("link", { name: expectedLinkName })).toHaveAttribute("href", expectedHref);
      expect(within(list).getAllByText(/^Latest$/)).toHaveLength(expectedCount);
      expect(screen.queryByText(new RegExp(`No published ${kind}`, "i"))).toBeNull();
      expect(document.title).toBe(`${title} · BenchBox Results`);
    },
  );

  it.each(SECTION_CASES)(
    "$title uses singular nouns on a card with exactly one run and one covered counterpart",
    async ({ kind, listId }) => {
      const oneRunRow = resultRow({
        result_id: "only-run",
        benchmark: "tpch",
        platform: "DuckDB",
        platform_id: "duckdb",
        run_date: "2026-08-20T12:00:00Z",
      });
      vi.mocked(listResults).mockResolvedValue([oneRunRow]);

      renderIndex(kind);

      const list = await screen.findByTestId(listId);
      const coverageNoun = kind === "benchmarks" ? "platform" : "benchmark";
      expect(within(list).getByText(`1 run · 1 ${coverageNoun}`)).toBeTruthy();
      expect(within(list).queryByText(/1 runs/)).toBeNull();
      expect(within(list).queryByText(/1 platforms/)).toBeNull();
      expect(within(list).queryByText(/1 benchmarks/)).toBeNull();
    },
  );

  it.each(SECTION_CASES)("$title renders the settled empty state after the retry is also empty", async ({ kind }) => {
    vi.mocked(listResults).mockResolvedValue([]);

    renderIndex(kind);

    expect(await screen.findByText(`No published ${kind}`)).toBeTruthy();
    expect(listResults).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("link", { name: "Back to leaderboards" })).toHaveAttribute("href", "/results/");
  });

  it.each(SECTION_CASES)("$title renders a load failure instead of an empty state", async ({ kind }) => {
    vi.mocked(listResults).mockRejectedValue(new Error("snapshot unavailable"));

    renderIndex(kind);

    expect(await screen.findByRole("alert")).toHaveTextContent("snapshot unavailable");
    expect(screen.queryByText(new RegExp(`No published ${kind}`, "i"))).toBeNull();
  });

  it.each(SECTION_CASES)(
    "$title sorts from URL state and keeps changes in the URL",
    async ({ kind, listId, resultSortFirst, recentSortFirst, expectedLinkName, expectedHref }) => {
      window.history.replaceState(null, "", `/results/${kind}/?sort=results`);
      vi.mocked(listResults).mockResolvedValue(SORT_ROWS);

      renderIndex(kind);

      const list = await screen.findByTestId(listId);
      expect(within(list).getAllByRole("link")[0]).toHaveTextContent(resultSortFirst);
      expect(screen.getByRole("combobox", { name: `Sort ${kind}` })).toHaveValue("results");

      fireEvent.change(screen.getByRole("combobox", { name: `Sort ${kind}` }), {
        target: { value: "recent" },
      });
      expect(new URL(window.location.href).searchParams.get("sort")).toBe("recent");
      await waitFor(() => expect(within(list).getAllByRole("link")[0]).toHaveTextContent(recentSortFirst));

      fireEvent.change(screen.getByRole("combobox", { name: `Sort ${kind}` }), {
        target: { value: "name" },
      });
      expect(window.location.search).toBe("");
      expect(within(list).getByRole("link", { name: expectedLinkName })).toHaveAttribute("href", expectedHref);
    },
  );

  it("benchmarks group cards by support status with badges", async () => {
    vi.mocked(listResults).mockResolvedValue([
      resultRow({
        result_id: "duckdb-tpch",
        benchmark: "tpch",
        platform: "DuckDB",
        platform_id: "duckdb",
        run_date: "2026-08-20T12:00:00Z",
        benchmark_support_status: "stable",
      }),
      resultRow({
        result_id: "duckdb-vector",
        benchmark: "vector_search",
        platform: "DuckDB",
        platform_id: "duckdb",
        run_date: "2026-08-21T12:00:00Z",
        benchmark_support_status: "beta",
      }),
      resultRow({
        result_id: "duckdb-custom",
        benchmark: "custom-internal",
        platform: "DuckDB",
        platform_id: "duckdb",
        run_date: "2026-08-22T12:00:00Z",
        benchmark_support_status: null,
      }),
    ]);

    render(<BenchmarksIndex />);

    const list = await screen.findByTestId("benchmarks-index-list");
    const headings = within(list).getAllByRole("heading", { level: 2 });
    expect(headings.map((heading) => heading.textContent)).toEqual([
      "Stable benchmarks",
      "Beta benchmarks",
      "Other benchmarks",
    ]);
    expect(within(list).getByTestId("support-badge-stable")).toHaveTextContent("Stable");
    expect(within(list).getByTestId("support-badge-beta")).toHaveTextContent("Beta");
    expect(within(list).queryByTestId("support-badge-other")).toBeNull();
    expect(within(list).getAllByRole("link")).toHaveLength(3);
  });

  it("benchmarks coalesce unrecognized and missing statuses into one Other section", async () => {
    vi.mocked(listResults).mockResolvedValue([
      resultRow({
        result_id: "duckdb-future-a",
        benchmark: "future_a",
        platform: "DuckDB",
        platform_id: "duckdb",
        run_date: "2026-08-20T12:00:00Z",
        benchmark_support_status: "ga",
      }),
      resultRow({
        result_id: "duckdb-future-b",
        benchmark: "future_b",
        platform: "DuckDB",
        platform_id: "duckdb",
        run_date: "2026-08-21T12:00:00Z",
        benchmark_support_status: "preview",
      }),
      resultRow({
        result_id: "duckdb-custom",
        benchmark: "custom-internal",
        platform: "DuckDB",
        platform_id: "duckdb",
        run_date: "2026-08-22T12:00:00Z",
        benchmark_support_status: null,
      }),
    ]);

    render(<BenchmarksIndex />);

    const list = await screen.findByTestId("benchmarks-index-list");
    const headings = within(list).getAllByRole("heading", { level: 2 });
    expect(headings.map((heading) => heading.textContent)).toEqual(["Other benchmarks"]);
    expect(within(list).getAllByRole("link")).toHaveLength(3);
    expect(within(list).queryByTestId(/support-badge-/)).toBeNull();
  });

  it.each(SECTION_CASES)(
    "$title uses a two-column compact grid below sm and lets long names wrap",
    async ({ kind, listId }) => {
      const longPlatform = "Apache DataFusion Comet Accelerated Vectorized Engine Preview Build";
      vi.mocked(listResults).mockResolvedValue([
        ...ROWS,
        resultRow({
          result_id: "long-platform",
          benchmark: "tpch",
          platform: longPlatform,
          platform_id: "datafusion-comet-preview",
          run_date: "2026-08-22T12:00:00Z",
        }),
      ]);

      renderIndex(kind);

      const list = await screen.findByTestId(listId);
      const links = within(list).getAllByRole("link");
      expect(links).toHaveLength(kind === "benchmarks" ? 2 : 3);

      const [first] = links;
      if (first === undefined) throw new Error(`${kind} index rendered no entries`);

      const grid = first.closest("ul");
      expect(grid).toHaveClass("grid", "grid-cols-2", "gap-2", "sm:grid-cols-2", "sm:gap-3");

      const card = first.closest("li")?.firstElementChild;
      expect(card).toHaveClass("p-3", "sm:p-4");

      const heading = first.closest("h3");
      expect(heading).toHaveClass("break-words", "text-sm", "sm:text-base");
    },
  );

  it("platforms wrap a long platform name instead of truncating it", async () => {
    const longPlatform = "Apache DataFusion Comet Accelerated Vectorized Engine Preview Build";
    vi.mocked(listResults).mockResolvedValue([
      resultRow({
        result_id: "long-platform",
        benchmark: "tpch",
        platform: longPlatform,
        platform_id: "datafusion-comet-preview",
        run_date: "2026-08-22T12:00:00Z",
      }),
    ]);

    render(<PlatformsIndex />);

    const list = await screen.findByTestId("platforms-index-list");
    const link = within(list).getByRole("link", { name: longPlatform });
    expect(link).toHaveAttribute("href", "/results/p/datafusion-comet-preview/");
    expect(link.closest("h3")).toHaveClass("break-words");
    expect(link.closest("h3")).not.toHaveClass("truncate");
  });

  it("platforms render one ungrouped list without badges", async () => {
    vi.mocked(listResults).mockResolvedValue(ROWS);

    render(<PlatformsIndex />);

    const list = await screen.findByTestId("platforms-index-list");
    expect(within(list).queryByRole("heading", { level: 2 })).toBeNull();
    expect(within(list).queryByTestId(/support-badge-/)).toBeNull();
    expect(within(list).getAllByRole("link")).toHaveLength(2);
  });
});

function resultRow(overrides: Partial<ResultRow>): ResultRow {
  return {
    result_id: "result",
    benchmark: "tpch",
    scale_factor: 1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: null,
    run_date: "2026-08-20T12:00:00Z",
    power_score: 100,
    total_duration_s: 1,
    geomean_ms: 1,
    display_geomean_ms: 1,
    query_count: 1,
    has_display_timing: true,
    valid_query_count: 1,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    trust_label: "maintainer-run",
    funding: "unspecified",
    visibility: "public-curated",
    platform_version: null,
    execution_mode: "sql",
    tuning_mode: null,
    tuning_hash: null,
    test_type: "standard",
    validation_status: "passed",
    cost_usd: null,
    normalized_cost_usd: null,
    cost_model_version: null,
    cost_model_source: null,
    cost_scope: null,
    cost_status: "unavailable",
    billing_unit: null,
    pricing_region: null,
    deployment_class: null,
    cloud_provider: null,
    cloud_region: null,
    instance_or_warehouse: null,
    instance_type: null,
    warehouse_size: null,
    node_count: null,
    cluster_size: null,
    storage_format: null,
    storage_tier: null,
    compliance_class: null,
    is_ranking_eligible: true,
    has_plans: false,
    plans_published: false,
    has_tuning: false,
    bundle_download_url: "",
    ...overrides,
  };
}
