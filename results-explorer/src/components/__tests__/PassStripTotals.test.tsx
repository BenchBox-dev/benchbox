import { render, screen, within } from "@testing-library/preact";
import { describe, it, expect } from "vitest";
import type { QueryTiming } from "@/types";

import { PassStrip, summarizeQueryPasses, summarizeRunPasses } from "@/components/PassStrip";

function measurement(queryId: string, ms: number, iter: number): QueryTiming {
  return { query_id: queryId, duration_ms: ms, status: "pass", run_type: "measurement", iter, stream: null };
}

function warmup(queryId: string, ms: number): QueryTiming {
  return { query_id: queryId, duration_ms: ms, status: "pass", run_type: "warmup", iter: 0, stream: null };
}

function failedMeasurement(queryId: string, ms: number): QueryTiming {
  return { query_id: queryId, duration_ms: ms, status: "fail", run_type: "measurement", iter: 1, stream: null };
}

const QUERIES: QueryTiming[] = [
  warmup("Q1", 30),
  measurement("Q1", 10, 1),
  measurement("Q1", 12, 2),
  measurement("Q1", 14, 3),
  warmup("Q2", 40),
  measurement("Q2", 20, 1),
  measurement("Q2", 22, 2),
  measurement("Q2", 24, 3),
];

describe("summarizeRunPasses", () => {
  it("sums each column over the queries that recorded it", () => {
    const totals = summarizeRunPasses(summarizeQueryPasses(QUERIES));
    expect(totals.queryCount).toBe(2);
    expect(totals.passCount).toBe(6);
    expect(totals.warmMedianMs).toBe(34);
    expect(totals.warmMinMs).toBe(30);
    expect(totals.spreadMs).toBe(8);
    expect(totals.warmupMs).toBe(70);
    expect(totals.warmupRatio).toBeCloseTo(70 / 34, 10);
  });

  it("measures the warmup ratio against the same queries the warmup total covers", () => {
    const totals = summarizeRunPasses(
      summarizeQueryPasses(QUERIES.filter((row) => !(row.query_id === "Q2" && row.run_type === "warmup"))),
    );
    expect(totals.warmupQueryCount).toBe(1);
    expect(totals.warmupMs).toBe(30);
    expect(totals.warmupRatio).toBeCloseTo(30 / 12, 10);
  });

  it("reports a missing column as absent rather than zero", () => {
    const totals = summarizeRunPasses(
      summarizeQueryPasses(QUERIES.filter((row) => row.run_type !== "warmup")),
    );
    expect(totals.warmupMs).toBeNull();
    expect(totals.warmupRatio).toBeNull();
    expect(totals.warmupQueryCount).toBe(0);
  });

  it("drops a query with a warmup but no warm median from both sides of the ratio", () => {
    const totals = summarizeRunPasses(
      summarizeQueryPasses([
        warmup("Q1", 30),
        measurement("Q1", 10, 1),
        warmup("Q2", 900),
        failedMeasurement("Q2", 20),
      ]),
    );
    expect(totals.warmupRatio).toBeCloseTo(30 / 10, 10);
    expect(totals.warmupMs).toBe(930);
    expect(totals.warmupQueryCount).toBe(2);
  });

  it("has no spread to report when a query ran once", () => {
    const totals = summarizeRunPasses(summarizeQueryPasses([measurement("Q1", 10, 1)]));
    expect(totals.spreadMs).toBeNull();
    expect(totals.warmMedianMs).toBe(10);
  });
});

describe("PassStrip totals row", () => {
  it("closes the table with the run's own totals", () => {
    render(<PassStrip queries={QUERIES} />);
    const totals = screen.getByTestId("pass-strip-totals");
    expect(within(totals).getByText("Overall")).toBeTruthy();
    expect(totals.textContent).toContain("34 ms");
    expect(within(totals).getByTestId("warmup-ratio-overall").textContent).toBe("2.06×");
  });

  it("says the totals cover queries the truncated table does not list", () => {
    const many = Array.from({ length: 30 }, (_, index) => measurement(`Q${index + 1}`, 10, 1));
    render(<PassStrip queries={many} limit={25} />);
    expect(screen.getByTestId("pass-strip-totals").textContent).toContain("300 ms");
    expect(screen.getByText(/including the 5 not listed above/)).toBeTruthy();
  });
});

it("includes every warmup stream in totals while comparing per-query medians", () => {
  const queries = [
    { ...warmup("Q1", 10), stream: 1 },
    { ...warmup("Q1", 30), stream: 2 },
    { ...warmup("Q1", 50), stream: 2, iter: 1 },
    { ...measurement("Q1", 5, 1), stream: 1 },
    { ...measurement("Q1", 15, 1), stream: 2 },
  ];
  for (const rows of [queries, [...queries].reverse()]) {
    const summaries = summarizeQueryPasses(rows);
    expect(summaries[0]!.warmupMs).toBe(30);
    expect(summaries[0]!.warmupTotalMs).toBe(90);
    const totals = summarizeRunPasses(summaries);
    expect(totals.warmupMs).toBe(90);
    expect(totals.warmupRatio).toBe(3);
    expect(totals.passCount).toBe(2);
  }
  render(<PassStrip queries={queries} />);
  expect(screen.getByRole("columnheader", { name: "Warmup median" })).toBeTruthy();
  expect(screen.getByTestId("pass-strip-totals").textContent).toContain("90 ms");
  expect(screen.getByText(/Warmup overall is the total of all passing warmup executions/)).toBeTruthy();
});

it("excludes failed and invalid warmup durations from both reductions", () => {
  const rows = [
    warmup("Q1", Number.NaN),
    warmup("Q1", 0),
    warmup("Q1", -1),
    { ...warmup("Q1", 1000), status: "fail" as const },
    warmup("Q1", 20),
    measurement("Q1", 10, 1),
  ];
  const summaries = summarizeQueryPasses(rows);
  expect(summaries[0]!.warmupMs).toBe(20);
  expect(summarizeRunPasses(summaries).warmupMs).toBe(20);
});
