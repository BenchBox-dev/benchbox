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

function resultRow(resultId: string, platform: string): ResultDetailMetricsRow {
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
    expect(within(table).getAllByText("2 successful executions; 2 recorded iterations")).toHaveLength(10);
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
    expect(within(table).getAllByText("No successful measurement execution")).toHaveLength(1);
    expect(within(table).getByText("No execution record for this query")).toBeTruthy();
    await waitFor(() => expect(document.title).toBe("Q1 across runs · BenchBox Results"));
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
    expect(chart.querySelectorAll("circle")).toHaveLength(3);
    expect(screen.getByText(/Selected basis value: median of measurement iteration 2/)).toBeTruthy();

    const table = screen.getByRole("table");
    const row = within(table).getByRole("row", { name: /Engine A/ });
    expect(within(row).getByText("95 ms")).toBeTruthy();
    expect(within(row).getByText("90 ms – 100 ms")).toBeTruthy();
    expect(within(row).getByText("3")).toBeTruthy();
    expect(within(row).getByText(/All-iteration median: 90 ms/)).toBeTruthy();
  });
});
