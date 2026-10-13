import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/db", async () => {
  const actual = await vi.importActual<typeof import("@/db")>("@/db");
  return {
    ...actual,
    queryRows: vi.fn(),
  };
});

vi.mock("@/lib/duckdbQueries", async () => {
  const actual = await vi.importActual<typeof import("@/lib/duckdbQueries")>("@/lib/duckdbQueries");
  return {
    ...actual,
    getDetailResult: vi.fn(),
    resolveShortId: vi.fn((id: string) => Promise.resolve(id)),
  };
});

import { DUCKDB_USER_QUERY_TIMEOUT_MS, queryRows } from "@/db";
import { clearDuckdbQueryCachesForTests, getDetailResult, resolveShortId } from "@/lib/duckdbQueries";
import { DEFAULT_ROW_LIMIT, UNLIMITED_ROW_LIMIT } from "@/lib/queryFilters";
import { Query } from "@/pages/Query";

const BASE_SCHEMA_COLUMNS = [
  { name: "result_id", type: "VARCHAR" },
  { name: "benchmark", type: "VARCHAR" },
  { name: "platform", type: "VARCHAR" },
  { name: "platform_version", type: "VARCHAR" },
  { name: "scale_factor", type: "DOUBLE" },
  { name: "run_date", type: "VARCHAR" },
  { name: "power_score", type: "DOUBLE" },
  { name: "total_duration_s", type: "DOUBLE" },
  { name: "geomean_ms", type: "DOUBLE" },
  { name: "trust_label", type: "VARCHAR" },
  { name: "tuning_mode", type: "VARCHAR" },
  { name: "test_type", type: "VARCHAR" },
  { name: "primary_metric", type: "VARCHAR" },
  { name: "comparison_exclusion_reason", type: "VARCHAR" },
  { name: "validation_status", type: "VARCHAR" },
  { name: "cost_usd", type: "DOUBLE" },
  { name: "normalized_cost_usd", type: "DOUBLE" },
  { name: "cost_model_version", type: "VARCHAR" },
  { name: "cost_status", type: "VARCHAR" },
  { name: "deployment_class", type: "VARCHAR" },
  { name: "cloud_provider", type: "VARCHAR" },
  { name: "cloud_region", type: "VARCHAR" },
  { name: "instance_or_warehouse", type: "VARCHAR" },
  { name: "storage_format", type: "VARCHAR" },
  { name: "physical_rendering_id", type: "VARCHAR" },
];
let schemaColumns = BASE_SCHEMA_COLUMNS;

const BASE_ROWS = [
  {
    result_id: "r1",
    benchmark: "clickbench",
    platform: "DuckDB",
    scale_factor: 0.1,
    run_date: "2026-04-17T12:00:00Z",
    power_score: null,
    total_duration_s: 65.25,
    geomean_ms: 10,
    trust_label: "maintainer-run",
    test_type: "power",
    primary_metric: "display_geomean_ms",
    comparison_exclusion_reason: null,
    cost_status: "not_applicable_local",
    deployment_class: "local",
    cloud_provider: null,
    instance_or_warehouse: null,
  },
  {
    result_id: "r2",
    benchmark: "clickbench",
    platform: "SQLite",
    scale_factor: 0.1,
    run_date: "2026-04-16T12:00:00Z",
    power_score: null,
    total_duration_s: 72,
    geomean_ms: 20,
    trust_label: "community-submission",
    test_type: "power",
    primary_metric: "display_geomean_ms",
    comparison_exclusion_reason: null,
    cost_status: "normalized",
    deployment_class: "cloud",
    cloud_provider: "aws",
    instance_or_warehouse: "MEDIUM",
  },
];
let resultRows: Record<string, unknown>[] = BASE_ROWS;
let resultQueryError: Error | null = null;

function normalizeSql(sql: string): string {
  return sql.replace(/\s+/g, " ").trim();
}

function isDefaultResultSelect(sql: unknown): boolean {
  return normalizeSql(String(sql)).startsWith("SELECT result_id, benchmark, scale_factor");
}

function appearsBefore(first: Element, second: Element): boolean {
  return Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);
}

function isFacetCountSql(normalized: string, column: string): boolean {
  return normalized.includes(
    `SELECT CASE WHEN ${column} IS NULL THEN 'unknown' ELSE CAST(${column} AS VARCHAR) END AS value`,
  );
}

function facetCountCalls() {
  return vi.mocked(queryRows).mock.calls.filter(([sql]) => {
    const normalized = normalizeSql(String(sql));
    return normalized.includes("COUNT(*) AS count") || normalized.includes("COUNT(*) FROM bench.results");
  });
}

beforeEach(() => {
  vi.mocked(queryRows).mockReset();
  clearDuckdbQueryCachesForTests();
  schemaColumns = BASE_SCHEMA_COLUMNS;
  resultRows = BASE_ROWS;
  resultQueryError = null;
  vi.mocked(getDetailResult).mockReset();
  vi.mocked(resolveShortId).mockImplementation((id) => Promise.resolve(id));
  window.history.replaceState(null, "", "/results/query");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ columns: schemaColumns }),
    }),
  );
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    disconnect() {}
  });
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:csv");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

  vi.mocked(queryRows).mockImplementation(async (sql: string, params: unknown[] = []) => {
    const normalized = normalizeSql(sql);
    const searchTerm = normalized.includes("CONTAINS(LOWER(COALESCE(CAST(platform AS VARCHAR)")
      ? String(params.at(-1) ?? "").toLowerCase()
      : "";
    const matchingRows = searchTerm === ""
      ? resultRows
      : resultRows.filter((row) =>
          [row.platform, row.platform_version, row.result_id]
            .some((value) => String(value ?? "").toLowerCase().includes(searchTerm)),
        );

    if (normalized.includes("FROM duckdb_columns()")) {
      return schemaColumns;
    }
    if (isFacetCountSql(normalized, "benchmark")) {
      return [{ value: "clickbench", count: 2 }];
    }
    if (isFacetCountSql(normalized, "platform")) {
      return [
        { value: "DuckDB", count: 1 },
        { value: "SQLite", count: 1 },
      ];
    }
    if (isFacetCountSql(normalized, "scale_factor")) {
      return [{ value: "0.1", count: 2 }];
    }
    if (isFacetCountSql(normalized, "tuning_mode")) {
      return [
        { value: "tuned", count: 1 },
        { value: "auto", count: 1 },
      ];
    }
    if (isFacetCountSql(normalized, "trust_label")) {
      return [
        { value: "maintainer-run", count: 1 },
        { value: "community-submission", count: 1 },
      ];
    }
    if (isFacetCountSql(normalized, "validation_status")) {
      return [{ value: "exact", count: 2 }];
    }
    if (isFacetCountSql(normalized, "cost_status")) {
      return [
        { value: "normalized", count: 1 },
        { value: "not_applicable_local", count: 1 },
      ];
    }
    if (isFacetCountSql(normalized, "cost_model_version")) {
      return [{ value: "2026.05.0", count: 1 }];
    }
    if (isFacetCountSql(normalized, "deployment_class")) {
      return [
        { value: "cloud", count: 1 },
        { value: "local", count: 1 },
      ];
    }
    if (isFacetCountSql(normalized, "cloud_provider")) {
      return [
        { value: "aws", count: 1 },
        { value: "unknown", count: 1 },
      ];
    }
    if (isFacetCountSql(normalized, "cloud_region")) {
      return [{ value: "us-east-1", count: 1 }];
    }
    if (isFacetCountSql(normalized, "instance_or_warehouse")) {
      return [{ value: "MEDIUM", count: 1 }];
    }
    if (isFacetCountSql(normalized, "storage_format")) {
      return [{ value: "parquet", count: 1 }];
    }
    if (isFacetCountSql(normalized, "physical_rendering_id")) {
      return [{ value: "databricks_liquid_auto", count: 1 }];
    }
    if (normalized.includes("SELECT CASE WHEN cost_usd IS NULL THEN 'no' ELSE 'yes' END AS value")) {
      return [
        { value: "yes", count: 1 },
        { value: "no", count: 1 },
      ];
    }
    if (normalized.includes("SELECT '30d' AS value") && normalized.includes("UNION ALL")) {
      return [
        { value: "30d", count: 1 },
        { value: "90d", count: 2 },
        { value: "365d", count: 2 },
      ];
    }
    if (normalized.startsWith("CREATE TABLE")) {
      throw new Error("read-only connection");
    }
    if (normalized.startsWith("SELECT LEAST(COUNT(*)")) {
      return [{ count: matchingRows.length }];
    }
    if (isDefaultResultSelect(sql)) {
      if (resultQueryError) throw resultQueryError;
      const limit = Number(normalized.match(/ LIMIT (\d+)/)?.[1] ?? matchingRows.length);
      const offset = Number(normalized.match(/ OFFSET (\d+)/)?.[1] ?? 0);
      return matchingRows.slice(offset, offset + limit);
    }
    if (normalized.startsWith("SELECT * FROM bench.results")) {
      return resultRows;
    }
    return resultRows;
  });
});

describe("Query", () => {
  it("preserves a run handed off from the one-run Compare route", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(BASE_ROWS[0] as never);

    render(<Query url="/results/query?pick=r1" />);

    await waitFor(() => expect(screen.getByTestId("query-compare-selected-r1")).toBeTruthy());
    expect(resolveShortId).toHaveBeenCalledWith("r1");
    expect(screen.getByTestId("query-compare-tray")).toHaveTextContent("1 result selected");
  });

  it("clears the pinned run from the URL when removing the handed-off selection", async () => {
    window.history.replaceState(null, "", "/results/query?pick=r1");
    vi.mocked(getDetailResult).mockResolvedValue(BASE_ROWS[0] as never);

    render(<Query />);

    await waitFor(() => expect(screen.getByTestId("query-compare-selected-r1")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Remove .*Public ID r1, from comparison/ }));

    expect(new URL(window.location.href).searchParams.has("pick")).toBe(false);
    expect(screen.queryByTestId("query-compare-selected-r1")).toBeNull();
  });

  it("rejects an ineligible handed-off run", async () => {
    window.history.replaceState(null, "", "/results/query?pick=r1");
    vi.mocked(getDetailResult).mockResolvedValue({
      ...BASE_ROWS[0],
      comparison_exclusion_reason: "zero_timings_only",
    } as never);

    render(<Query />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Only exact zero timings are available");
    expect(screen.queryByTestId("query-compare-selected-r1")).toBeNull();
    expect(screen.getByTestId("query-compare-launch").textContent).toBe("Compare All Runs");
  });

  it("shows narrowing facets and clears them when no rows match", async () => {
    window.history.replaceState(null, "", "/results/query?benchmark=tpch&platform=MissingDB");
    resultRows = [];

    render(<Query />);

    const emptyState = await screen.findByTestId("query-empty-state");
    expect(within(emptyState).getByRole("status")).toBeTruthy();
    expect(emptyState.textContent).toContain("No results match these filters");
    expect(emptyState.textContent).toContain("Benchmark: TPC-H");
    expect(emptyState.textContent).toContain("Platform: MissingDB");
    expect(within(emptyState).queryByRole("table")).toBeNull();

    fireEvent.click(within(emptyState).getByRole("button", { name: "Clear all filters" }));
    await waitFor(() => expect(window.location.search).toBe(""));
  });

  it("shows a load error instead of the empty-by-filter state", async () => {
    resultRows = [];
    resultQueryError = new Error("simulated result query failure");

    render(<Query />);

    expect(await screen.findByRole("alert")).toHaveTextContent("simulated result query failure");
    expect(screen.queryByTestId("query-empty-state")).toBeNull();
  });

  it("falls back to picks when filtered rows span multiple cohorts", async () => {
    resultRows = [
      BASE_ROWS[0]!,
      BASE_ROWS[1]!,
      { ...BASE_ROWS[0]!, result_id: "r5", platform: "DuckDB extra" },
      { ...BASE_ROWS[0]!, result_id: "r3", benchmark: "tpch", platform: "DuckDB tpch" },
      { ...BASE_ROWS[1]!, result_id: "r4", benchmark: "tpch", platform: "SQLite tpch" },
    ];

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const idle = screen.getByTestId("query-compare-launch") as HTMLButtonElement;
    expect(idle.tagName).toBe("BUTTON");
    expect(idle.disabled).toBe(true);
    expect(idle.textContent).toBe("Select 2–10 runs to compare");

    fireEvent.click(screen.getByTestId("query-compare-checkbox-r1"));
    fireEvent.click(screen.getByTestId("query-compare-checkbox-r2"));
    const launch = screen.getByTestId("query-compare-launch") as HTMLAnchorElement;
    expect(launch.textContent).toBe("Compare 2 selected");
    expect(launch.className).toContain("btn-primary");
    expect(launch.href).toContain("/results/compare?ids=");
    const subset = screen.getByTestId("query-compare-launch-all") as HTMLAnchorElement;
    expect(subset.textContent).toBe("Compare All Runs");
    expect(subset.href).toContain("/results/compare?ids=");
  });

  it("falls back to picks when the comparable set exceeds ten runs", async () => {
    resultRows = Array.from({ length: 12 }, (_, index) => ({
      ...BASE_ROWS[0]!,
      result_id: `r-big-${index + 1}`,
      platform: `Platform ${index + 1}`,
    }));

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("Platform 1").length).toBeGreaterThan(0));

    expect(screen.queryByText("Compare All Runs")).toBeNull();
    const control = screen.getByTestId("query-compare-launch") as HTMLButtonElement;
    expect(control.disabled).toBe(true);
    expect(control.textContent).toBe("Select 2–10 runs to compare");
  });

  it("renders the compare tray and offers Compare All Runs for a small single-cohort set", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const tray = screen.getByTestId("query-compare-tray");
    expect(tray.textContent).toContain("All 2 comparable runs in these results can be compared together.");
    const initial = screen.getByTestId("query-compare-launch") as HTMLAnchorElement;
    expect(initial.textContent).toBe("Compare All Runs");
    expect(initial.href).toContain("/results/compare?ids=");

    fireEvent.click(screen.getByTestId("query-compare-checkbox-r1"));
    expect(screen.getByTestId("query-compare-tray").textContent).toContain("1 result selected");
    expect(screen.getByTestId("query-compare-launch").textContent).toBe("Compare All Runs");

    fireEvent.click(screen.getByTestId("query-compare-checkbox-r2"));
    expect(screen.getByTestId("query-compare-tray").textContent).toContain("2 results selected");
    const launch = screen.getByTestId("query-compare-launch") as HTMLAnchorElement;
    expect(launch.textContent).toBe("Compare 2 selected");
    expect(launch.href).toContain("/results/compare?ids=");

    fireEvent.click(screen.getByTestId("query-compare-clear"));
    expect(screen.getByTestId("query-compare-tray").textContent).toContain("All 2 comparable runs in these results can be compared together.");
    expect(screen.getByTestId("query-compare-launch").textContent).toBe("Compare All Runs");
  });

  it("locks compare selection by hidden phase and primary-metric metadata", async () => {
    resultRows = [
      ...BASE_ROWS,
      {
        ...BASE_ROWS[0]!,
        result_id: "r3",
        platform: "DuckDB throughput",
        run_date: "2026-04-15T12:00:00Z",
        test_type: "throughput",
        primary_metric: "power_score",
      },
    ];

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    fireEvent.click(screen.getByTestId("query-compare-checkbox-r1"));
    fireEvent.click(screen.getByTestId("query-compare-compatible-only"));
    const incompatible = screen.getByTestId("query-compare-checkbox-r3") as HTMLInputElement;
    expect(incompatible.disabled).toBe(true);
    expect(incompatible.title).toContain("phase");
    expect(incompatible.title).toContain("primary metric");
  });

  it("disables Query Workbench compare selection for non-comparable rows", async () => {
    resultRows = [
      BASE_ROWS[0]!,
      {
        ...BASE_ROWS[1]!,
        comparison_exclusion_reason: "insufficient_valid_timings",
      },
    ];

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const blocked = screen.getByTestId("query-compare-checkbox-r2") as HTMLInputElement;
    expect(blocked.disabled).toBe(true);
    expect(blocked.title).toContain("This run does not include enough usable query timings");
    const visibleReason = screen.getByTestId("query-disabled-reason");
    expect(visibleReason.textContent).toContain("Why unavailable: Insufficient valid timings");
    expect(visibleReason.textContent).toContain("Choose a run with at least two valid query timings");
    expect(blocked.getAttribute("aria-describedby")).toBe(visibleReason.id);
    expect(visibleReason.closest("td")?.className).not.toContain("hidden");

    fireEvent.click(blocked);
    expect(screen.getByTestId("query-compare-tray").textContent).toContain("Select two or more rows");
    expect(screen.getByTestId("query-compare-launch")).toBeDisabled();
  });

  it("shows a Query Workbench zero-selectable recovery callout when every visible row is disabled", async () => {
    resultRows = [
      {
        ...BASE_ROWS[0]!,
        comparison_exclusion_reason: "insufficient_query_coverage",
      },
      {
        ...BASE_ROWS[1]!,
        comparison_exclusion_reason: "insufficient_query_coverage",
      },
    ];

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const callout = screen.getByTestId("query-zero-selectable");
    expect(callout.textContent).toContain("No selectable compare rows");
    expect(callout.textContent).toContain("insufficient query coverage");
    expect(within(callout).queryByRole("button", { name: "Clear filters" })).toBeNull();
    expect(callout).toHaveTextContent("A run can be unavailable because of its evidence");
    expect(screen.getAllByTestId("query-disabled-reason")[0]?.textContent).toContain(
      "Why unavailable: Insufficient query coverage",
    );
  });

  it("query workbench hides incompatible rows by default and surfaces the hidden count", async () => {
    resultRows = [
      ...BASE_ROWS,
      {
        ...BASE_ROWS[0]!,
        result_id: "r3",
        platform: "DuckDB throughput",
        run_date: "2026-04-15T12:00:00Z",
        test_type: "throughput",
        primary_metric: "power_score",
      },
    ];

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    expect(screen.queryByTestId("query-compare-compatible-only")).toBeNull();
    expect(screen.getByTestId("query-compare-checkbox-r3")).toBeTruthy();

    fireEvent.click(screen.getByTestId("query-compare-checkbox-r1"));
    await waitFor(() =>
      expect((screen.getByTestId("query-compare-compatible-only") as HTMLInputElement).checked).toBe(true),
    );
    expect(screen.queryByTestId("query-compare-checkbox-r3")).toBeNull();
    expect(screen.getByTestId("query-compare-tray").textContent).toContain("1 incompatible row hidden");
  });

  it("removes an off-page selection and selects a replacement without clearing the set", async () => {
    resultRows = [
      { ...BASE_ROWS[0]!, result_id: "r1", platform: "DuckDB compatible" },
      ...Array.from({ length: 60 }, (_, index) => ({
        ...BASE_ROWS[0]!,
        result_id: `page-row-${index}`,
        platform: `Page row ${index}`,
        run_date: `2026-04-${String(16 - (index % 15)).padStart(2, "0")}T12:00:00Z`,
      })),
    ];

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB compatible").length).toBeGreaterThan(0));
    expect(screen.getByText("Showing 1–24 of 61 matching runs")).toBeTruthy();

    for (const id of ["r1", ...Array.from({ length: 9 }, (_, index) => `page-row-${index}`)]) {
      fireEvent.click(screen.getByTestId(`query-compare-checkbox-${id}`));
    }
    expect(screen.getByTestId("query-compare-tray").textContent).toContain("10 results selected (maximum)");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() => expect(screen.getByText("Showing 25–48 of 61 matching runs")).toBeTruthy());
    const replacement = screen.getByTestId("query-compare-checkbox-page-row-23") as HTMLInputElement;
    expect(replacement.disabled).toBe(true);

    fireEvent.click(
      screen.getByRole("button", {
        name: "Remove DuckDB compatible, Public ID r1, from comparison",
      }),
    );
    expect(screen.queryByTestId("query-compare-selected-r1")).toBeNull();
    expect(replacement.disabled).toBe(false);

    fireEvent.click(replacement);
    expect(screen.getByTestId("query-compare-selected-page-row-23")).toBeTruthy();
    expect(screen.getByTestId("query-compare-tray").textContent).toContain("10 results selected (maximum)");
    expect(new URL(window.location.href).searchParams.get("page")).toBe("2");
    const latestSelect = vi.mocked(queryRows).mock.calls.filter(([sql]) => isDefaultResultSelect(sql)).at(-1);
    expect(latestSelect?.[0]).toContain("LIMIT 24 OFFSET 24");
    expect(screen.queryByRole("button", { name: "Show more results" })).toBeNull();
  });

  it("query workbench checkbox aria-labels disambiguate same-platform rows", async () => {
    resultRows = [
      {
        ...BASE_ROWS[0]!,
        result_id: "tpch-duckdb-sf0.1-20260502-aaaa1111",
        platform: "DuckDB",
        run_date: "2026-05-02T18:11:00Z",
      },
      {
        ...BASE_ROWS[0]!,
        result_id: "tpch-duckdb-sf0.1-20260508-bbbb2222",
        platform: "DuckDB",
        run_date: "2026-05-08T18:11:00Z",
      },
    ];

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const labelA = screen
      .getByTestId("query-compare-checkbox-tpch-duckdb-sf0.1-20260502-aaaa1111")
      .getAttribute("aria-label") ?? "";
    const labelB = screen
      .getByTestId("query-compare-checkbox-tpch-duckdb-sf0.1-20260508-bbbb2222")
      .getAttribute("aria-label") ?? "";
    expect(labelA).not.toBe(labelB);
    expect(labelA).toContain("aaaa1111");
    expect(labelB).toContain("bbbb2222");
  });

  it("caps Query compare selections at ten runs", async () => {
    resultRows = Array.from({ length: 11 }, (_, index) => ({
      ...BASE_ROWS[0]!,
      result_id: `r${index + 1}`,
      platform: `DuckDB ${index + 1}`,
      run_date: `2026-04-${17 - index}T12:00:00Z`,
    }));

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB 1").length).toBeGreaterThan(0));

    for (let index = 1; index <= 10; index += 1) {
      const id = `r${index}`;
      fireEvent.click(screen.getByTestId(`query-compare-checkbox-${id}`));
    }
    const eleventh = screen.getByTestId("query-compare-checkbox-r11") as HTMLInputElement;
    expect(eleventh.disabled).toBe(true);
    expect(eleventh.title).toContain("Up to 10 runs");
    expect(screen.getByTestId("query-compare-tray").textContent).toContain("10 results selected (maximum)");
  });

  it("loads facet counts and table rows from DuckDB", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));
    expect(document.title).toBe("Find runs · BenchBox Results");
    const resultsTable = screen.getAllByRole("table")[0]!;

    expect(screen.getAllByText("SQLite").length).toBeGreaterThan(0);
    expect(within(resultsTable).getAllByText("ClickBench").length).toBeGreaterThan(0);
    expect(within(resultsTable).getByText("Maintainer run")).toBeTruthy();
  });

  it("uses public table labels and formatted values in the default result table", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));
    const resultsTable = screen.getAllByRole("table")[0]!;

    expect(within(resultsTable).getByRole("columnheader", { name: /^Benchmark/ })).toBeTruthy();
    expect(within(resultsTable).getByRole("columnheader", { name: /^Scale/ })).toBeTruthy();
    expect(within(resultsTable).getByRole("columnheader", { name: /^Run date/ })).toBeTruthy();
    expect(within(resultsTable).getByRole("columnheader", { name: /^Geomean latency/ })).toBeTruthy();
    expect(within(resultsTable).getAllByText("SF 0.1").length).toBeGreaterThan(0);
    expect(within(resultsTable).getAllByText("2026-04-17").length).toBeGreaterThan(0);
    expect(
      within(resultsTable)
        .getAllByTestId("run-date-chip")
        .some((chip) => /2026-04-17 \(.*days ago\)/.test(chip.getAttribute("title") ?? "")),
    ).toBe(true);
    expect(within(resultsTable).getByText("10 ms")).toBeTruthy();

    fireEvent.click(screen.getByText("Configure visible columns"));
    fireEvent.click(screen.getByLabelText("Total duration"));
    await waitFor(() => expect(within(resultsTable).getByText("65.25 s")).toBeTruthy());
  });

  it("does not pin the results-table action column, so it can't paint over trailing cells", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const scrollContainer = screen.getByTestId("query-results-scroll-container");
    const headerRow = scrollContainer.querySelector("thead tr")!;
    const bodyRow = scrollContainer.querySelector("tbody tr")!;

    const headerCells = Array.from(headerRow.children) as HTMLElement[];
    const bodyCells = Array.from(bodyRow.children) as HTMLElement[];
    headerCells.forEach((cell, index) => {
      if (index === 0) {
        expect(cell.className).toMatch(/\bsticky\b/);
      } else {
        expect(cell.className).not.toMatch(/\bsticky\b/);
      }
    });
    bodyCells.forEach((cell, index) => {
      if (index === 0) {
        expect(cell.className).toMatch(/\bsticky\b/);
      } else {
        expect(cell.className).not.toMatch(/\bsticky\b/);
      }
    });

    const actionCell = bodyCells[bodyCells.length - 1]!;
    expect(within(actionCell).getByText("View →")).toBeTruthy();
  });

  it("orders mobile query controls so results appear before deep filters", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const heading = screen.getByRole("heading", { name: "Find benchmark runs" });
    const resultSummary = screen.getByTestId("query-result-summary");
    const mobileDrawer = screen.getByTestId("query-mobile-filter-drawer");
    const resultsPanel = screen.getByTestId("query-results-panel");
    const advancedSql = screen.getByText("Advanced SQL").closest("details")!;
    const visibleColumns = screen.getByTestId("query-visible-columns");
    const desktopFilters = screen.getByTestId("query-desktop-filters");

    expect(appearsBefore(heading, resultSummary)).toBe(true);
    expect(appearsBefore(resultSummary, mobileDrawer)).toBe(true);
    expect(appearsBefore(mobileDrawer, resultsPanel)).toBe(true);
    expect(appearsBefore(resultsPanel, advancedSql)).toBe(true);
    expect(appearsBefore(resultsPanel, visibleColumns)).toBe(true);
    expect(appearsBefore(resultsPanel, desktopFilters)).toBe(true);
    expect(visibleColumns.tagName).toBe("DETAILS");
    expect(visibleColumns.textContent).toContain("Configure visible columns");
    expect(mobileDrawer.className).toContain("lg:hidden");
    expect(desktopFilters.className).toContain("hidden");
  });

  it("provides Reset / Select all / Clear optional bulk actions in the Configure visible columns disclosure", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const disclosure = screen.getByTestId("query-visible-columns");
    expect(within(disclosure).getByRole("button", { name: "Reset to default" })).toBeTruthy();
    expect(within(disclosure).getByRole("button", { name: "Select all" })).toBeTruthy();
    const clearOptional = within(disclosure).getByRole("button", { name: "Clear optional" });

    fireEvent.click(within(disclosure).getByRole("button", { name: "Select all" }));
    await waitFor(() => {
      const checkboxes = within(disclosure).getAllByRole("checkbox") as HTMLInputElement[];
      expect(checkboxes.length).toBeGreaterThan(0);
      expect(checkboxes.every((cb) => cb.checked)).toBe(true);
    });

    fireEvent.click(clearOptional);
    await waitFor(() => {
      const checked = (within(disclosure).getAllByRole("checkbox") as HTMLInputElement[]).filter((cb) => cb.checked);
      expect(checked.length).toBe(1);
    });
  });

  it("opens full query facets from the mobile drawer trigger", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const mobileDrawer = screen.getByTestId("query-mobile-filter-drawer");
    const trigger = within(mobileDrawer).getByRole("button", { name: /Filters/ });
    expect(trigger.getAttribute("data-result-count")).toBe("2");

    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Filter results" });
    expect(within(dialog).getByText("Benchmark")).toBeTruthy();
    expect(within(dialog).getByLabelText(/^Benchmark: ClickBench/)).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Apply filters" })).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Reset filters" })).toBeTruthy();
    expect(within(dialog).getAllByText("Has cost").length).toBeGreaterThan(0);
  });

  it("shows active URL filters in the closed mobile drawer trigger", async () => {
    window.history.replaceState(null, "", "/results/query?platform=DuckDB&has_cost=yes");

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const mobileDrawer = screen.getByTestId("query-mobile-filter-drawer");
    expect(within(mobileDrawer).getAllByText("Platform: DuckDB").length).toBeGreaterThan(0);
    expect(within(mobileDrawer).getAllByText("Has cost: Has cost").length).toBeGreaterThan(0);
  });

  it("uses facet display formatters for active URL filter chips", async () => {
    window.history.replaceState(
      null,
      "",
      "/results/query?benchmark=star_schema,ssb&trust=maintainer-run&cost_status=not_applicable_local",
    );

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const mobileDrawer = screen.getByTestId("query-mobile-filter-drawer");
    expect(within(mobileDrawer).getAllByText("Benchmark: SSB").length).toBeGreaterThan(0);
    expect(within(mobileDrawer).getAllByText("Benchmark: SSB (historical source)").length).toBeGreaterThan(0);
    expect(within(mobileDrawer).getAllByText("Trust: Maintainer run").length).toBeGreaterThan(0);
    expect(within(mobileDrawer).getAllByText("Cost status: not applicable (local)").length).toBeGreaterThan(0);
    expect(within(mobileDrawer).queryByText("Trust: maintainer-run")).toBeNull();
    expect(within(mobileDrawer).queryByText("Cost status: not applicable local")).toBeNull();
  });

  it("updates the select SQL when facet filters change and sort toggles", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getAllByText("Platform").length).toBeGreaterThan(0));
    const resultsTable = screen.getAllByRole("table")[0]!;

    fireEvent.click(screen.getByLabelText(/^Platform: DuckDB/));
    await waitFor(() => {
      const selectCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) => isDefaultResultSelect(sql));
      expect(selectCalls.at(-1)?.[0]).toContain("platform IN (?)");
    });

    fireEvent.click(within(resultsTable).getAllByText(/^Benchmark(?:\s[↑↓])?$/)[0]!);
    await waitFor(() => {
      const selectCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) => isDefaultResultSelect(sql));
      expect(selectCalls.at(-1)?.[0]).toContain("ORDER BY benchmark ASC");
    });
  });

  it("does not refetch facet counts when result-only controls change", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));
    await waitFor(() => expect(facetCountCalls().length).toBeGreaterThanOrEqual(15));
    const initialFacetCallCount = facetCountCalls().length;
    const resultsTable = screen.getAllByRole("table")[0]!;

    fireEvent.click(within(resultsTable).getAllByText(/^Benchmark(?:\s[↑↓])?$/)[0]!);
    await waitFor(() => {
      const selectCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) => isDefaultResultSelect(sql));
      expect(selectCalls.at(-1)?.[0]).toContain("ORDER BY benchmark ASC");
    });
    expect(facetCountCalls()).toHaveLength(initialFacetCallCount);

    fireEvent.click(screen.getByText("Configure visible columns"));
    fireEvent.click(screen.getByLabelText(/^Geomean latency/));
    await waitFor(() => {
      const selectCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) => isDefaultResultSelect(sql));
      expect(selectCalls.at(-1)?.[0]).not.toContain("geomean_ms");
    });
    expect(facetCountCalls()).toHaveLength(initialFacetCallCount);

    fireEvent.click(screen.getByRole("button", { name: /^All$/ }));
    await waitFor(() => {
      const countCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) =>
        normalizeSql(sql).startsWith("SELECT LEAST(COUNT(*)"),
      );
      expect(countCalls.at(-1)?.[0]).toContain(`LEAST(COUNT(*), ${UNLIMITED_ROW_LIMIT})`);
    });
    expect(facetCountCalls()).toHaveLength(initialFacetCallCount);
  });

  it("reads shared facet aliases and rewrites them to canonical query params", async () => {
    window.history.replaceState(
      null,
      "",
      "/results/query?bm=clickbench&scale_factor=0.1&trust_tier=maintainer-run&instance_type=MEDIUM",
    );

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    await waitFor(() => {
      const params = new URL(window.location.href).searchParams;
      expect(params.get("benchmark")).toBe("clickbench");
      expect(params.get("sf")).toBe("0.1");
      expect(params.get("trust")).toBe("maintainer-run");
      expect(params.get("shape")).toBe("MEDIUM");
      expect(params.get("bm")).toBeNull();
      expect(params.get("scale_factor")).toBeNull();
      expect(params.get("trust_tier")).toBeNull();
      expect(params.get("instance_type")).toBeNull();
    });

    const selectCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) => isDefaultResultSelect(sql));
    const latest = String(selectCalls.at(-1)?.[0]);
    expect(latest).toContain("benchmark IN (?)");
    expect(latest).toContain("scale_factor IN (?)");
    expect(latest).toContain("trust_label IN (?)");
    expect(latest).toContain("instance_or_warehouse IN (?)");
    expect(selectCalls.at(-1)?.[1]).toEqual(["clickbench", 0.1, "maintainer-run", "MEDIUM"]);
  });

  it("keeps hidden result IDs available for row actions without showing the column", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const resultsTable = screen.getAllByRole("table")[0]!;
    expect(within(resultsTable).queryByRole("columnheader", { name: /^result_id$/ })).toBeNull();
    expect(within(resultsTable).getAllByRole("link", { name: /View/i })[0]).toHaveAttribute(
      "href",
      "/results/r/r1",
    );
    expect(vi.mocked(queryRows).mock.calls.some(([sql]) => isDefaultResultSelect(sql))).toBe(true);
  });

  it("applies normalized cost and deployment facets to the generated query", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getAllByText("Cost status").length).toBeGreaterThan(0));

    const desktopFilters = screen.getByTestId("query-desktop-filters");
    const costStatus = within(desktopFilters).getByRole("heading", { name: "Cost status" }).closest("section")!;
    const deployment = within(desktopFilters).getByRole("heading", { name: "Deployment" }).closest("section")!;
    const cloudProvider = within(desktopFilters).getByRole("heading", { name: "Cloud provider" }).closest("section")!;
    const shape = within(desktopFilters).getByRole("heading", { name: "Instance / warehouse" }).closest("section")!;
    fireEvent.click(within(costStatus).getByRole("button", { name: /^Cost status/ }));
    fireEvent.click(within(deployment).getByRole("button", { name: /^Deployment/ }));
    fireEvent.click(within(cloudProvider).getByRole("button", { name: /^Cloud provider/ }));
    fireEvent.click(within(shape).getByRole("button", { name: /^Instance \/ warehouse/ }));
    fireEvent.click(within(costStatus).getByLabelText(/^Cost status: normalized/i));
    fireEvent.click(within(deployment).getByLabelText(/^Deployment: cloud/i));
    fireEvent.click(within(cloudProvider).getByLabelText(/^Cloud provider: aws/i));
    fireEvent.click(within(shape).getByLabelText(/^Instance \/ warehouse: MEDIUM/i));

    await waitFor(() => {
      const selectCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) => isDefaultResultSelect(sql));
      const latest = String(selectCalls.at(-1)?.[0]);
      expect(latest).toContain("cost_status IN (?)");
      expect(latest).toContain("deployment_class IN (?)");
      expect(latest).toContain("cloud_provider IN (?)");
      expect(latest).toContain("instance_or_warehouse IN (?)");
    });
    const params = new URL(window.location.href).searchParams;
    expect(params.get("cost_status")).toBe("normalized");
    expect(params.get("deployment")).toBe("cloud");
    expect(params.get("cloud_provider")).toBe("aws");
    expect(params.get("shape")).toBe("MEDIUM");
  });

  it("applies the physical_rendering_id secondary facet to the generated query", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getAllByText("Physical rendering").length).toBeGreaterThan(0));

    const desktopFilters = screen.getByTestId("query-desktop-filters");
    const physical = within(desktopFilters)
      .getByRole("heading", { name: "Physical rendering" })
      .closest("section")!;
    fireEvent.click(within(physical).getByRole("button", { name: /^Physical rendering/ }));
    fireEvent.click(within(physical).getByLabelText(/^Physical rendering: databricks liquid auto/i));

    await waitFor(() => {
      const selectCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) => isDefaultResultSelect(sql));
      expect(String(selectCalls.at(-1)?.[0])).toContain("physical_rendering_id IN (?)");
    });
    expect(new URL(window.location.href).searchParams.get("physical_rendering_id")).toBe(
      "databricks_liquid_auto",
    );
  });

  it("does not build facet SQL for URL params absent from the introspected schema", async () => {
    schemaColumns = BASE_SCHEMA_COLUMNS.filter(
      (column) => !["cost_status", "deployment_class", "cloud_provider", "instance_or_warehouse"].includes(column.name),
    );
    window.history.replaceState(
      null,
      "",
      "/results/query?cost_status=normalized&deployment=cloud&cloud_provider=aws&shape=MEDIUM",
    );

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const sqlCalls = vi.mocked(queryRows).mock.calls.map(([sql]) => String(sql));
    expect(sqlCalls.join("\n")).not.toContain("CAST(cost_status AS VARCHAR)");
    expect(sqlCalls.join("\n")).not.toContain("CAST(deployment_class AS VARCHAR)");
    const selectCalls = sqlCalls.filter((sql) => isDefaultResultSelect(sql));
    expect(selectCalls.at(-1)).not.toContain("cost_status IN (?)");
    expect(selectCalls.at(-1)).not.toContain("deployment_class IN (?)");
    expect(selectCalls.at(-1)).not.toContain("cloud_provider IN (?)");
    expect(selectCalls.at(-1)).not.toContain("instance_or_warehouse IN (?)");
  });

  it("switches the result table row limit through URL state", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    const countCallsBefore = vi.mocked(queryRows).mock.calls.filter(([sql]) =>
      normalizeSql(sql).startsWith("SELECT LEAST(COUNT(*)"),
    );
    expect(countCallsBefore.at(-1)?.[0]).toContain(`LEAST(COUNT(*), ${DEFAULT_ROW_LIMIT})`);

    fireEvent.click(screen.getByRole("button", { name: /^All$/ }));

    await waitFor(() =>
      expect(new URL(window.location.href).searchParams.get("limit")).toBe("all"),
    );
    await waitFor(() => {
      const countCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) =>
        normalizeSql(sql).startsWith("SELECT LEAST(COUNT(*)"),
      );
      expect(countCalls.at(-1)?.[0]).toContain(`LEAST(COUNT(*), ${UNLIMITED_ROW_LIMIT})`);
    });
    expect(screen.getByText("Showing 1–2 of 2 matching runs")).toBeTruthy();
    expect(screen.queryByText(/Query limit:/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^Up to 10,000$/ }));

    await waitFor(() =>
      expect(new URL(window.location.href).searchParams.get("limit")).toBeNull(),
    );
  });

  it("searches platform, platform version, and public ID in SQL", async () => {
    resultRows = [
      { ...BASE_ROWS[0]!, result_id: "public-duckdb-1", platform_version: "1.4.0" },
      { ...BASE_ROWS[1]!, result_id: "public-sqlite-2", platform_version: "3.49.1" },
    ];

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    fireEvent.input(screen.getByRole("searchbox", { name: "Search results" }), {
      target: { value: "3.49" },
    });

    await waitFor(() => expect(screen.getByText("Showing 1–1 of 1 matching run")).toBeTruthy());
    expect(screen.getAllByText("SQLite").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("query-compare-checkbox-public-duckdb-1")).toBeNull();
    expect(new URL(window.location.href).searchParams.get("q")).toBe("3.49");
    const latestSelect = vi.mocked(queryRows).mock.calls.filter(([sql]) => isDefaultResultSelect(sql)).at(-1);
    expect(latestSelect?.[0]).toContain("CAST(platform AS VARCHAR)");
    expect(latestSelect?.[0]).toContain("CAST(platform_version AS VARCHAR)");
    expect(latestSelect?.[0]).toContain("CAST(result_id AS VARCHAR)");
    expect(latestSelect?.[1]?.slice(-3)).toEqual(["3.49", "3.49", "3.49"]);
  });

  it("coerces invalid row-limit URL state back to the default", async () => {
    window.history.replaceState(null, "", "/results/query?limit=bogus");

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    await waitFor(() =>
      expect(new URL(window.location.href).searchParams.get("limit")).toBeNull(),
    );
    expect(screen.getByRole("button", { name: /^Up to 10,000$/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("exports the complete filtered row set as quoted CSV without filesystem access", async () => {
    let capturedBlob: Blob | null = null;
    resultRows = [{ ...BASE_ROWS[0], platform: 'Duck, "DB"\nNext', trust_label: null }];
    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation(((tagName: string) => {
      if (tagName === "a") {
        return {
          click() {},
          set href(_: string) {},
          set download(_: string) {},
        } as unknown as HTMLAnchorElement;
      }
      return originalCreateElement(tagName);
    }) as typeof document.createElement);
    vi.spyOn(URL, "createObjectURL").mockImplementation((blob: Blob | MediaSource) => {
      if (blob instanceof Blob) capturedBlob = blob;
      return "blob:csv";
    });

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole("button", { name: "Download CSV (filtered rows)" }));
    await waitFor(() => expect(capturedBlob).not.toBeNull());
    expect(capturedBlob).toBeTruthy();
    const csvText = await capturedBlob!.text();
    expect(csvText).toContain("benchmark,platform,scale_factor");
    expect(csvText).toContain('"Duck, ""DB""\nNext"');
    const exportCall = vi
      .mocked(queryRows)
      .mock.calls.find(([sql]) => normalizeSql(sql).startsWith("SELECT benchmark, platform, scale_factor"));
    expect(exportCall?.[0]).toContain(`LIMIT ${DEFAULT_ROW_LIMIT}`);
  });

  it("loads a starter query into the SQL editor when its button is clicked", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    fireEvent.click(screen.getByText("Advanced SQL"));
    const starterButton = screen.getByRole("button", { name: "Recent results" });
    fireEvent.click(starterButton);

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea.value).toContain("FROM bench.results");
    expect(textarea.value).toContain("ORDER BY run_date DESC");
  });

  it("loads normalized cost starter queries into the SQL editor", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    fireEvent.click(screen.getByText("Advanced SQL"));
    fireEvent.click(screen.getByRole("button", { name: "Normalized cost leaderboard" }));

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea.value).toContain("normalized_cost_usd");
    expect(textarea.value).toContain("cost_status = 'normalized'");
    expect(textarea.value).toContain("cloud_provider");
  });

  it("exports all filtered rows, not only the current page, as JSON", async () => {
    let capturedBlob: Blob | null = null;
    resultRows = Array.from({ length: 61 }, (_, index) => ({
      ...BASE_ROWS[0]!,
      result_id: `json-${index}`,
      platform: `JSON platform ${index}`,
    }));
    vi.spyOn(URL, "createObjectURL").mockImplementation((blob: Blob | MediaSource) => {
      if (blob instanceof Blob) capturedBlob = blob;
      return "blob:json";
    });

    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole("button", { name: "Download JSON (filtered rows)" }));
    await waitFor(() => expect(capturedBlob).not.toBeNull());
    const jsonText = await capturedBlob!.text();
    const parsed = JSON.parse(jsonText);
    expect(Array.isArray(parsed)).toBe(true);
    expect(parsed).toHaveLength(61);
    expect(parsed[0]).toHaveProperty("benchmark");
    expect(parsed[0]).not.toHaveProperty("result_id");
  });

  it("preselects every run listed in the ids parameter and keeps pick as the pinned run", async () => {
    vi.mocked(getDetailResult).mockImplementation(async (id: string) =>
      (BASE_ROWS.find((row) => row.result_id === id) ?? null) as never,
    );

    render(<Query url="/results/query?pick=r2&ids=r2,r1" />);

    await waitFor(() => expect(screen.getByTestId("query-compare-selected-r1")).toBeTruthy());
    expect(screen.getByTestId("query-compare-selected-r2")).toBeTruthy();
    expect(screen.getByTestId("query-compare-tray")).toHaveTextContent("2 results selected");
    expect(resolveShortId).toHaveBeenCalledWith("r1");
  });

  it("keeps the results header and cells in the same column count on the Find runs table", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));
    const resultsTable = screen.getAllByRole("table")[0]!;

    const headerCount = resultsTable.querySelectorAll("thead tr th").length;
    const firstRow = resultsTable.querySelector("tbody tr");
    expect(firstRow).not.toBeNull();
    expect(firstRow!.querySelectorAll("td").length).toBe(headerCount);
    expect(resultsTable.textContent).not.toContain("Compare state");
    expect(resultsTable.textContent).not.toContain("Selectable");
    expect(within(resultsTable).getByRole("columnheader", { name: /^Power score$/ })).toBeTruthy();
  });

  it("runs read-only SQL and surfaces DDL errors", async () => {
    render(<Query />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));

    fireEvent.click(screen.getByText("Advanced SQL"));
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.input(textarea, { target: { value: "CREATE TABLE x AS SELECT 1" } });
    fireEvent.click(screen.getByRole("button", { name: "Run SQL" }));

    await waitFor(() => expect(screen.getByText("read-only connection")).toBeTruthy());

    const sqlCall = vi.mocked(queryRows).mock.calls.find(([sql]) => String(sql).startsWith("CREATE TABLE"));
    expect(sqlCall?.[2]).toBe(DUCKDB_USER_QUERY_TIMEOUT_MS);
  });
});
