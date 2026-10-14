import { render, screen, waitFor, within } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { QueryExecutionRow, ResultDetailMetricsRow } from "@/lib/duckdbQueries";

vi.mock("@/lib/duckdbQueries", async () => {
  const actual = await vi.importActual<typeof import("@/lib/duckdbQueries")>("@/lib/duckdbQueries");
  return {
    ...actual,
    getExistingResultIds: vi.fn(),
    getQueryExecutions: vi.fn(),
    getResultDetailMetrics: vi.fn(),
    resolveShortId: vi.fn(),
  };
});

import {
  getExistingResultIds,
  getQueryExecutions,
  getResultDetailMetrics,
  resolveShortId,
} from "@/lib/duckdbQueries";
import { QueryRunComparison } from "@/pages/QueryRunComparison";

function resultRow(
  resultId: string,
  platform: string,
  overrides: Partial<ResultDetailMetricsRow> = {},
): ResultDetailMetricsRow {
  return {
    result_id: resultId,
    short_id: null,
    platform,
    platform_version: "1.0",
    driver_version: null,
    run_date: "2026-10-01",
    scale_factor: 1,
    deployment_class: null,
    instance_or_warehouse: null,
    trust_label: "maintainer-run",
    ...overrides,
  } as unknown as ResultDetailMetricsRow;
}

function execution(
  resultId: string,
  overrides: Partial<QueryExecutionRow> = {},
): QueryExecutionRow {
  return {
    result_id: resultId,
    query_id: "Q1",
    duration_ms: 10,
    status: "pass",
    run_type: "measurement",
    iter: 1,
    stream: null,
    ...overrides,
  };
}

describe("QueryRunComparison", () => {
  let results: Map<string, ResultDetailMetricsRow>;
  let executions: QueryExecutionRow[];

  beforeEach(() => {
    vi.clearAllMocks();
    results = new Map();
    executions = [];
    vi.mocked(resolveShortId).mockImplementation(async (id) => id);
    vi.mocked(getExistingResultIds).mockImplementation(async (ids) => new Set(ids));
    vi.mocked(getResultDetailMetrics).mockImplementation(async (id) => results.get(id) ?? null);
    vi.mocked(getQueryExecutions).mockImplementation(async (resultIds, queryId) => {
      const ids = typeof resultIds === "string" ? [resultIds] : [...resultIds];
      return executions.filter((row) => ids.includes(row.result_id) && (queryId === undefined || row.query_id === queryId));
    });
  });

  it("renders ten runs in the supplied order with an accessible point chart and table", async () => {
    const ids = Array.from({ length: 10 }, (_, index) => `run-${index + 1}`);
    ids.forEach((id, index) => {
      results.set(id, resultRow(id, `Platform ${index + 1}`));
      executions.push(
        execution(id, { duration_ms: 10 + index, iter: 1 }),
        execution(id, { duration_ms: 20 + index, iter: 2 }),
      );
    });

    render(
      <QueryRunComparison
        queryId="Q1"
        url={`/results/compare/query/Q1?ids=${encodeURIComponent(ids.join(","))}`}
      />,
    );

    expect(await screen.findByRole("img", { name: /Q1 successful measurement execution times/ })).toBeTruthy();
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(11);
    expect(within(table).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual(
      ids.map((id) => `/results/r/${id}`),
    );
    expect(getQueryExecutions).toHaveBeenCalledWith(ids, "Q1");
    expect(within(table).getAllByTitle("2 successful executions; 2 recorded iterations")).toHaveLength(10);
  });

  it("loads three full-ID runs for one query without generated short IDs", async () => {
    const ids = [
      "tpch-duckdb-sf0.01-20260403-f0835929",
      "tpch-datafusion-sf0.01-20260403-91c44a02",
      "tpch-clickhouse-sf0.01-20260403-55d0e91b",
    ];
    ids.forEach((id, index) => {
      results.set(id, resultRow(id, `Platform ${index + 1}`));
      executions.push(execution(id, { query_id: "Q18", duration_ms: 10 + index, iter: 1 }));
    });

    render(
      <QueryRunComparison
        queryId="Q18"
        url={`/results/compare/query/Q18?ids=${encodeURIComponent(ids.join(","))}`}
      />,
    );

    expect(await screen.findByRole("img", { name: /Q18 successful measurement execution times/ })).toBeTruthy();
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(getQueryExecutions).toHaveBeenCalledWith(ids, "Q18");
  });

  it("shows missing query records, failed measurements, and warmups as separate states", async () => {
    results.set("failed-run", resultRow("failed-run", "Engine A"));
    results.set("missing-query-run", resultRow("missing-query-run", "Engine B"));
    executions = [
      execution("failed-run", { status: "fail", duration_ms: 20, iter: 1 }),
      execution("failed-run", { run_type: "warmup", iter: 0, duration_ms: 15 }),
    ];

    render(
      <QueryRunComparison
        queryId="Q1"
        url="/results/compare/query/Q1?ids=failed-run%2Cmissing-query-run"
      />,
    );

    const table = await screen.findByRole("table");
    expect(within(table).getByText("1 / 0")).toBeTruthy();
    expect(within(table).getAllByTitle("No successful measurement execution")).toHaveLength(1);
    expect(within(table).getByTitle("No execution record for this query")).toBeTruthy();
    await waitFor(() => expect(document.title).toBe("Q1 across runs · BenchBox Results"));
  });

  it("resolves public run tokens to full result_ids before loading the runs", async () => {
    const snowflake = "tpch-snowflake-sf10.0-20261003-f0835929";
    const duckdb = "tpch-duckdb-sf10.0-20261002-5f5ccaf2";
    const publicToFull: Record<string, string> = { f0835929: snowflake, "5f5ccaf2": duckdb };
    vi.mocked(resolveShortId).mockImplementation(async (id) => publicToFull[id] ?? id);
    vi.mocked(getExistingResultIds).mockImplementation(async (ids) => new Set(ids.filter((id) => results.has(id))));
    results.set(snowflake, resultRow(snowflake, "Snowflake"));
    results.set(duckdb, resultRow(duckdb, "DuckDB"));
    executions = [
      execution(snowflake, { query_id: "18", duration_ms: 120, iter: 1 }),
      execution(duckdb, { query_id: "18", duration_ms: 90, iter: 1 }),
    ];

    render(
      <QueryRunComparison
        queryId="18"
        url="/results/compare/query/18?ids=f0835929%2C5f5ccaf2"
      />,
    );

    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(getQueryExecutions).toHaveBeenCalledWith([snowflake, duckdb], "18");
    expect(screen.queryByText(/Loading 18 executions/)).toBeNull();
  });

  it("leaves the loading state and marks an unresolvable run as unavailable", async () => {
    const known = "tpch-duckdb-sf10.0-20261002-5f5ccaf2";
    results.set(known, resultRow(known, "DuckDB"));
    executions = [execution(known, { query_id: "18", duration_ms: 90, iter: 1 })];
    vi.mocked(getExistingResultIds).mockImplementation(async (ids) => new Set(ids.filter((id) => results.has(id))));

    render(
      <QueryRunComparison
        queryId="18"
        url={`/results/compare/query/18?ids=f0835929%2C${encodeURIComponent(known)}`}
      />,
    );

    expect(await screen.findByText(/This run is no longer published/)).toBeTruthy();
    expect(screen.queryByText(/Loading 18 executions/)).toBeNull();
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
  });

  it("shows the error state when loading the query executions throws", async () => {
    const known = "tpch-duckdb-sf10.0-20261002-5f5ccaf2";
    results.set(known, resultRow(known, "DuckDB"));
    vi.mocked(getQueryExecutions).mockRejectedValueOnce(new Error("database unavailable"));

    render(
      <QueryRunComparison
        queryId="18"
        url={`/results/compare/query/18?ids=${encodeURIComponent(known)}`}
      />,
    );

    expect(await screen.findByText("Cannot compare query executions")).toBeTruthy();
    expect(screen.getByText("database unavailable")).toBeTruthy();
    expect(screen.queryByText(/Loading 18 executions/)).toBeNull();
  });

  it("uses legacy unlabelled executions only as a per-run fallback", async () => {
    results.set("legacy-run", resultRow("legacy-run", "Engine A"));
    results.set("mixed-run", resultRow("mixed-run", "Engine B"));
    executions = [
      execution("legacy-run", { run_type: null, duration_ms: 12, iter: 1 }),
      execution("mixed-run", { run_type: "measurement", duration_ms: 20, iter: 1 }),
      execution("mixed-run", { run_type: null, duration_ms: 40, iter: 2 }),
    ];

    render(
      <QueryRunComparison
        queryId="Q1"
        url="/results/compare/query/Q1?ids=legacy-run%2Cmixed-run"
      />,
    );

    const table = await screen.findByRole("table");
    expect(within(table).getByText("legacy run type fallback")).toBeTruthy();
    expect(within(table).getByText("1 legacy row ignored")).toBeTruthy();
    expect(within(table).getByText("12 ms")).toBeTruthy();
    expect(within(table).getByText("20 ms")).toBeTruthy();
    expect(within(table).queryByText("40 ms")).toBeNull();
  });

  it("keeps the all-iteration spread visible when the URL selects one iteration basis", async () => {
    results.set("run-a", resultRow("run-a", "Engine A"));
    executions = [
      execution("run-a", { duration_ms: 10, iter: 1 }),
      execution("run-a", { duration_ms: 90, iter: 2 }),
      execution("run-a", { duration_ms: 100, iter: 2, stream: 1 }),
    ];

    render(
      <QueryRunComparison
        queryId="Q1"
        url="/results/compare/query/Q1?ids=run-a&basis=warm_pass_2"
      />,
    );

    const chart = await screen.findByTestId("query-run-comparison-chart");
    expect(chart.querySelectorAll('[data-sample="true"]')).toHaveLength(3);
    expect(screen.getByText(/Selected basis value: median of measurement iteration 2/)).toBeTruthy();

    const table = screen.getByRole("table");
    const row = within(table).getByRole("row", { name: /Engine A/ });
    expect(within(row).getByText("95 ms")).toBeTruthy();
    expect(within(row).getByText("90 ms – 100 ms")).toBeTruthy();
    expect(within(row).getByText("3")).toBeTruthy();
    expect(within(row).getByText(/All-iteration median: 90 ms/)).toBeTruthy();
  });

  it("titles a numeric query with its benchmark in the heading and breadcrumb", async () => {
    const snowflake = "tpch-snowflake-sf10.0-20261003-f0835929";
    results.set(snowflake, resultRow(snowflake, "Snowflake", { benchmark: "tpch", scale_factor: 10 }));
    executions = [execution(snowflake, { query_id: "18", duration_ms: 120, iter: 1 })];

    render(<QueryRunComparison queryId="18" url={`/results/compare/query/18?ids=${snowflake}`} />);

    expect(await screen.findByRole("heading", { level: 1, name: "TPC-H Q18" })).toBeTruthy();
    const breadcrumb = document.querySelector('[aria-label="Breadcrumb"]');
    expect(breadcrumb?.textContent).toContain("Compare");
    expect(breadcrumb?.textContent).toContain("TPC-H Q18");
    await waitFor(() => expect(document.title).toBe("TPC-H Q18 across runs · BenchBox Results"));
  });

  it("keeps a named query id after the benchmark name", async () => {
    const run = "read-primitives-duckdb-sf1-20261001-1a2b3c4d";
    results.set(run, resultRow(run, "DuckDB", { benchmark: "read_primitives" }));
    executions = [execution(run, { query_id: "aggregation_distinct_groupby", duration_ms: 8, iter: 1 })];

    render(
      <QueryRunComparison
        queryId="aggregation_distinct_groupby"
        url={`/results/compare/query/aggregation_distinct_groupby?ids=${run}`}
      />,
    );

    expect(
      await screen.findByRole("heading", { level: 1, name: "Read Primitives · aggregation_distinct_groupby" }),
    ).toBeTruthy();
  });

  it("labels chart categories with platform identities and uses a log axis for a wide spread", async () => {
    const fast = "tpch-duckdb-sf10.0-20261002-5f5ccaf2";
    const slow = "tpch-spark-sf10.0-20261003-f0835929";
    results.set(fast, resultRow(fast, "DuckDB"));
    results.set(slow, resultRow(slow, "Spark"));
    executions = [
      execution(fast, { query_id: "1", duration_ms: 41, iter: 1 }),
      execution(slow, { query_id: "1", duration_ms: 12300, iter: 1 }),
    ];

    render(<QueryRunComparison queryId="1" url={`/results/compare/query/1?ids=${fast},${slow}`} />);

    const chart = await screen.findByTestId("query-run-comparison-chart");
    expect(chart.textContent).toContain("DuckDB");
    expect(chart.textContent).toContain("Spark");
    expect(chart.textContent).not.toContain("Run 1");
    expect(chart.textContent).toContain("Duration (log scale)");
    expect(chart.querySelectorAll('[data-sample="true"]')).toHaveLength(2);
  });

  it("uses a linear axis with distinct round tick labels for a narrow spread", async () => {
    const first = "tpch-duckdb-sf10.0-20261002-5f5ccaf2";
    const second = "tpch-datafusion-sf10.0-20261003-f0835929";
    results.set(first, resultRow(first, "DuckDB"));
    results.set(second, resultRow(second, "DataFusion"));
    executions = [
      execution(first, { query_id: "1", duration_ms: 10, iter: 1 }),
      execution(second, { query_id: "1", duration_ms: 20, iter: 1 }),
    ];

    render(<QueryRunComparison queryId="1" url={`/results/compare/query/1?ids=${first},${second}`} />);

    const chart = await screen.findByTestId("query-run-comparison-chart");
    expect(chart.textContent).toContain("Duration");
    expect(chart.textContent).not.toContain("log scale");
    const tickLabels = Array.from(chart.querySelectorAll('text[text-anchor="end"]')).map((text) => text.textContent);
    expect(tickLabels).toContain("10 ms");
    expect(new Set(tickLabels).size).toBe(tickLabels.length);
  });

  it("uses one row per run on narrow viewports so every run stays visible", async () => {
    const original = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: true,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia;
    try {
      const ids = ["run-a", "run-b", "run-c"];
      ids.forEach((id, index) => {
        results.set(id, resultRow(id, `Engine ${index + 1}`));
        executions.push(execution(id, { duration_ms: 10 + index, iter: 1 }), execution(id, { duration_ms: 20 + index, iter: 2 }));
      });

      render(<QueryRunComparison queryId="Q1" url={`/results/compare/query/Q1?ids=${ids.join(",")}`} />);

      await screen.findByTestId("query-run-comparison-chart");
      await waitFor(() =>
        expect(screen.getByTestId("query-run-comparison-chart").getAttribute("data-layout")).toBe("rows"),
      );
      const chart = screen.getByTestId("query-run-comparison-chart");
      expect(chart.querySelectorAll('[data-sample="true"]')).toHaveLength(6);
      expect(chart.textContent).toContain("Engine 3");
    } finally {
      window.matchMedia = original;
    }
  });

  it("warns above the chart when the selected runs differ in scale factor", async () => {
    results.set("sf1-run", resultRow("sf1-run", "DuckDB", { benchmark: "tpch", scale_factor: 1 }));
    results.set("sf10-run", resultRow("sf10-run", "Snowflake", { benchmark: "tpch", scale_factor: 10 }));
    executions = [
      execution("sf1-run", { duration_ms: 10, iter: 1 }),
      execution("sf10-run", { duration_ms: 20, iter: 1 }),
    ];

    render(<QueryRunComparison queryId="Q1" url="/results/compare/query/Q1?ids=sf1-run%2Csf10-run" />);

    const warning = await screen.findByTestId("query-run-scope-warning");
    expect(warning.textContent).toContain("scale factor (SF 1, SF 10)");
  });

  it("hides the Other records column when no selected run has an entry for it", async () => {
    results.set("run-a", resultRow("run-a", "Engine A"));
    results.set("run-b", resultRow("run-b", "Engine B"));
    executions = [
      execution("run-a", { duration_ms: 10, iter: 1 }),
      execution("run-b", { duration_ms: 12, iter: 1 }),
    ];

    render(<QueryRunComparison queryId="Q1" url="/results/compare/query/Q1?ids=run-a%2Crun-b" />);

    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).queryByRole("columnheader", { name: "Other records" })).toBeNull();
  });

  it("shows the query plans section only for runs with published plans", async () => {
    const planned = "tpch-snowflake-sf10.0-20261003-f0835929";
    const unplanned = "tpch-duckdb-sf10.0-20261002-5f5ccaf2";
    results.set(
      planned,
      resultRow(planned, "Snowflake", {
        has_plans: true,
        plans_published: true,
        bundle_download_url: `${window.location.origin}/downloads/tpch-snowflake-sf10.0-20261003-f0835929.json`,
      }),
    );
    results.set(unplanned, resultRow(unplanned, "DuckDB", { has_plans: false, plans_published: false }));
    executions = [
      execution(planned, { query_id: "18", duration_ms: 120, iter: 1 }),
      execution(unplanned, { query_id: "18", duration_ms: 90, iter: 1 }),
    ];

    render(<QueryRunComparison queryId="18" url={`/results/compare/query/18?ids=${planned},${unplanned}`} />);

    expect(await screen.findByRole("heading", { name: "Query plans" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "View query plans" })).toHaveLength(1);
    expect(screen.getByText("Snowflake · f0835929")).toBeTruthy();
  });

  it("omits the query plans section when no selected run has plans", async () => {
    results.set("run-a", resultRow("run-a", "Engine A"));
    executions = [execution("run-a", { duration_ms: 10, iter: 1 })];

    render(<QueryRunComparison queryId="Q1" url="/results/compare/query/Q1?ids=run-a" />);

    await screen.findByRole("table");
    expect(screen.queryByRole("heading", { name: "Query plans" })).toBeNull();
    expect(screen.queryByRole("button", { name: "View query plans" })).toBeNull();
  });

  it("links to the run picker with the current run IDs", async () => {
    results.set("run-a", resultRow("run-a", "Engine A"));
    results.set("run-b", resultRow("run-b", "Engine B"));
    executions = [execution("run-a", { duration_ms: 10 }), execution("run-b", { duration_ms: 12 })];

    render(<QueryRunComparison queryId="Q1" url="/results/compare/query/Q1?ids=run-a%2Crun-b" />);

    const link = await screen.findByRole("link", { name: "Add or change runs" });
    const target = new URL(link.getAttribute("href") ?? "", "https://benchbox.dev");
    expect(target.pathname).toBe("/results/query");
    expect(target.searchParams.get("ids")).toBe("run-a,run-b");
    expect(target.searchParams.get("pick")).toBe("run-a");
  });
});
