import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { describe, it, expect, afterEach } from "vitest";
import { NormalizedSpeedupChart } from "@/components/NormalizedSpeedupChart";

const QUERIES = [
  {
    queryId: "Q1",
    timings: [
      { ms: 100, status: "pass" },
      { ms: 200, status: "pass" },
    ],
  },
  {
    queryId: "Q2",
    timings: [
      { ms: 50, status: "pass" },
      { ms: 400, status: "pass" },
    ],
  },
];
const RESULTS = [{ platform: "DuckDB" }, { platform: "SQLite" }];

describe("NormalizedSpeedupChart", () => {
  it("renders platform baseline label", () => {
    render(<NormalizedSpeedupChart queries={QUERIES} results={RESULTS} baselineIdx={0} />);
    expect(screen.getByText(/Baseline/)).toBeTruthy();
    expect(screen.getByText("DuckDB")).toBeTruthy();
    const chart = screen.getByRole("img", { name: "Per-query speedups relative to DuckDB" });
    expect(chart.getAttribute("aria-describedby")).toBe("normalized-speedup-description");
    expect(screen.getByText(/Values above 1 are faster; values below 1 are slower/)).toBeTruthy();
    expect(screen.getByRole("table", { name: /Per-query speedups relative to DuckDB/ })).toBeTruthy();
  });

  it("returns null for single result", () => {
    const { container } = render(
      <NormalizedSpeedupChart queries={QUERIES} results={[{ platform: "DuckDB" }]} baselineIdx={0} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("returns null for empty queries", () => {
    const { container } = render(<NormalizedSpeedupChart queries={[]} results={RESULTS} baselineIdx={0} />);
    expect(container.firstChild).toBeNull();
  });

  afterEach(() => {
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
      configurable: true,
      get: () => 0,
    });
  });

  it.each([400, 800, 1200])("SVG viewBox width matches container offsetWidth at %spx", async (testWidth) => {
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
      configurable: true,
      get: () => testWidth,
    });

    const { container } = render(<NormalizedSpeedupChart queries={QUERIES} results={RESULTS} baselineIdx={0} />);

    await waitFor(() => {
      const svg = container.querySelector("svg");
      const viewBox = svg?.getAttribute("viewBox") ?? "";
      expect(viewBox).toMatch(new RegExp(`^0 0 ${testWidth} `));
    });
  });

  it("renders a compact parity state when all query speedups are equal", () => {
    const equalQueries = [
      { queryId: "Q1", timings: [{ ms: 100, status: "pass" }, { ms: 100, status: "pass" }] },
      { queryId: "Q2", timings: [{ ms: 200, status: "pass" }, { ms: 200, status: "pass" }] },
    ];
    const { container } = render(<NormalizedSpeedupChart queries={equalQueries} results={RESULTS} baselineIdx={0} />);

    expect(screen.getByText("No meaningful per-query difference")).toBeTruthy();
    expect(screen.getByText(/All 2 compared queries \(2 query-run pairs\) are within 1% of 1.00×/)).toBeTruthy();
    expect(container.querySelector("svg")).toBeNull();
  });

  it("null timing entry renders a missing marker and accessible table value", () => {
    const queriesWithNull = [{ queryId: "Q1", timings: [{ ms: 100, status: "pass" }, null] }];
    const { container } = render(
      <NormalizedSpeedupChart queries={queriesWithNull} results={RESULTS} baselineIdx={0} />,
    );
    expect(container.querySelector("[data-platform-marker='SQLite']")).not.toBeNull();
    expect(container.querySelector("table")?.textContent).toContain("Not available: comparable timing unavailable");
  });

  it("hides queries with missing speedups by default and toggles them back via the comparable-only checkbox", () => {
    const sparseQueries = [
      { queryId: "Q1", timings: [{ ms: 100, status: "pass" }, { ms: 50, status: "pass" }] },
      { queryId: "Q2", timings: [{ ms: 100, status: "pass" }, null] },
      { queryId: "Q3", timings: [null, { ms: 80, status: "pass" }] },
    ];
    const { container } = render(
      <NormalizedSpeedupChart queries={sparseQueries} results={RESULTS} baselineIdx={0} />,
    );

    const rowsAfterDefault = Array.from(container.querySelectorAll("[data-query-row]")).map((el) =>
      el.getAttribute("data-query-row"),
    );
    expect(rowsAfterDefault).toEqual(["Q1"]);
    expect(screen.getByText(/2 hidden/)).toBeTruthy();

    const toggle = screen.getByTestId("normalized-speedup-comparable-only-toggle") as HTMLInputElement;
    fireEvent.change(toggle, { target: { checked: false } });
    const rowsAfterToggle = Array.from(container.querySelectorAll("[data-query-row]")).map((el) =>
      el.getAttribute("data-query-row"),
    );
    expect(new Set(rowsAfterToggle)).toEqual(new Set(["Q1", "Q2", "Q3"]));
  });

  it("keeps the comparable-only toggle visible when the comparable subset is parity-only", () => {
    const sparseParityQueries = [
      { queryId: "Q1", timings: [{ ms: 100, status: "pass" }, { ms: 100, status: "pass" }] },
      { queryId: "Q2", timings: [{ ms: 100, status: "pass" }, null] },
    ];
    const { container } = render(
      <NormalizedSpeedupChart queries={sparseParityQueries} results={RESULTS} baselineIdx={0} />,
    );

    expect(screen.queryByText("No meaningful per-query difference")).toBeNull();
    expect(screen.getByTestId("normalized-speedup-comparable-only-toggle")).toBeTruthy();
    const queryRows = Array.from(container.querySelectorAll("[data-query-row]")).map((el) =>
      el.getAttribute("data-query-row"),
    );
    expect(queryRows).toEqual(["Q1"]);
  });

  it("renders both rows when two queries share the same queryId", () => {
    const queriesWithDuplicate = [
      { queryId: "Q1", timings: [{ ms: 100, status: "pass" }, { ms: 50, status: "pass" }] },
      { queryId: "Q1", timings: [{ ms: 200, status: "pass" }, { ms: 100, status: "pass" }] },
    ];
    const { container } = render(
      <NormalizedSpeedupChart queries={queriesWithDuplicate} results={RESULTS} baselineIdx={0} />,
    );
    const queryRows = Array.from(container.querySelectorAll("[data-query-row='Q1']"));
    expect(queryRows.length).toBe(2);
  });

  it("keeps one query row with a marker for each nonbaseline run", () => {
    const results = Array.from({ length: 10 }, (_, index) => ({ platform: `Platform ${index}` }));
    const query = {
      queryId: "Q1",
      timings: results.map((_, index) => ({ ms: 100 / (index + 1), status: "pass" })),
    };
    const { container } = render(
      <NormalizedSpeedupChart queries={[query]} results={results} baselineIdx={0} />,
    );

    const rows = container.querySelectorAll("[data-query-row='Q1']");
    expect(rows).toHaveLength(1);
    expect(rows[0]?.querySelectorAll("[data-platform-marker]")).toHaveLength(9);
    expect(container.querySelectorAll("table tbody tr")).toHaveLength(9);
  });
});
