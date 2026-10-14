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

  it.each([
    [320, "← slower", "faster →"],
    [1200, "← slower than baseline", "faster than baseline →"],
  ])("fits the axis direction labels at %spx", async (testWidth, slower, faster) => {
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
      configurable: true,
      get: () => testWidth,
    });

    const { container } = render(<NormalizedSpeedupChart queries={QUERIES} results={RESULTS} baselineIdx={0} />);

    await waitFor(() => {
      expect(container.querySelector('[data-axis-direction="slower"]')?.textContent).toBe(slower);
      expect(container.querySelector('[data-axis-direction="faster"]')?.textContent).toBe(faster);
    });
  });

  it("renders a compact parity state when all query speedups are equal", () => {
    const equalQueries = [
      { queryId: "Q1", timings: [{ ms: 100, status: "pass" }, { ms: 100, status: "pass" }] },
      { queryId: "Q2", timings: [{ ms: 200, status: "pass" }, { ms: 200, status: "pass" }] },
    ];
    const { container } = render(<NormalizedSpeedupChart queries={equalQueries} results={RESULTS} baselineIdx={0} />);

    expect(screen.getByText("No meaningful per-query difference")).toBeTruthy();
    expect(screen.getByText(/All 2 compared queries \(2 query-run pairs\) are within 1% of 1×/)).toBeTruthy();
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

describe("NormalizedSpeedupChart axis and labels", () => {
  const WIDE_RESULTS = [{ platform: "DuckDB" }, { platform: "SQLite" }];
  const wideQueries = [
    { queryId: "aggregation_distinct_groupby", timings: [{ ms: 100, status: "pass" }, { ms: 25000, status: "pass" }] },
    { queryId: "q18", timings: [{ ms: 100, status: "pass" }, { ms: 0.0667, status: "pass" }] },
    { queryId: "q7", timings: [{ ms: 100, status: "pass" }, { ms: 0.5, status: "pass" }] },
  ];

  function tickLabels(container: Element): SVGTextElement[] {
    return Array.from(container.querySelectorAll("svg text")).filter((node) =>
      /×$/.test(node.textContent ?? ""),
    ) as SVGTextElement[];
  }

  it("draws distinct ratio ticks with significant digits and never 0.00×", () => {
    const { container } = render(
      <NormalizedSpeedupChart queries={wideQueries} results={WIDE_RESULTS} baselineIdx={0} />,
    );
    const labels = tickLabels(container).map((node) => node.textContent ?? "");
    expect(labels.length).toBeGreaterThanOrEqual(3);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels.some((label) => label.includes("0.00×"))).toBe(false);
    expect(labels).toContain("1×");
    expect(labels.some((label) => label.startsWith("0.0"))).toBe(true);
    expect(labels.some((label) => label.includes(","))).toBe(true);
  });

  it("keeps every tick label inside the svg width", () => {
    const { container } = render(
      <NormalizedSpeedupChart queries={wideQueries} results={WIDE_RESULTS} baselineIdx={0} />,
    );
    const svg = container.querySelector("svg");
    const width = Number(svg?.getAttribute("viewBox")?.split(/\s+/)[2]);
    for (const node of tickLabels(container)) {
      const x = Number(node.getAttribute("x"));
      const anchor = node.getAttribute("text-anchor");
      const halfText = (node.textContent ?? "").length * 0.62 * 9 / 2;
      const left = anchor === "end" ? x - halfText * 2 : anchor === "start" ? x : x - halfText;
      const right = anchor === "start" ? x + halfText * 2 : anchor === "end" ? x : x + halfText;
      expect(left).toBeGreaterThanOrEqual(0);
      expect(right).toBeLessThanOrEqual(width);
    }
  });

  it("truncates a long query label with an ellipsis and keeps the full name in the link and a title", () => {
    const longQuery = {
      queryId: "aggregation_distinct_groupby_with_a_very_long_descriptive_name",
      timings: [{ ms: 100, status: "pass" }, { ms: 50, status: "pass" }],
    };
    const { container } = render(
      <NormalizedSpeedupChart
        queries={[longQuery]}
        results={WIDE_RESULTS}
        baselineIdx={0}
        queryHref={(queryId) => `/compare?query=${queryId}`}
      />,
    );
    const label = container.querySelector("[data-query-label='aggregation_distinct_groupby_with_a_very_long_descriptive_name']");
    expect(label?.firstChild?.textContent?.startsWith("aggregation_")).toBe(true);
    expect(label?.firstChild?.textContent?.endsWith("…")).toBe(true);
    expect(label?.querySelector("title")?.textContent).toBe(longQuery.queryId);
    expect(
      container.querySelector(`a[aria-label='Compare ${longQuery.queryId} across selected runs']`),
    ).not.toBeNull();
  });

  it("leaves short query labels untruncated and without a title", () => {
    const { container } = render(
      <NormalizedSpeedupChart queries={wideQueries} results={WIDE_RESULTS} baselineIdx={0} />,
    );
    const label = container.querySelector("[data-query-label='q7']");
    expect(label?.textContent).toBe("q7");
    expect(label?.querySelector("title")).toBeNull();
  });

  it("draws missing values as a hollow marker in a gutter left of the plot and explains them in the legend", () => {
    const withMissing = [{ queryId: "Q1", timings: [{ ms: 100, status: "pass" }, null] }];
    const { container } = render(
      <NormalizedSpeedupChart queries={withMissing} results={RESULTS} baselineIdx={0} />,
    );
    const plotStart = Number(container.querySelector("svg")?.getAttribute("data-plot-start"));
    const missing = container.querySelector("circle[data-platform-marker='SQLite']");
    expect(Number(missing?.getAttribute("cx"))).toBeLessThan(plotStart);
    expect(screen.getByText("No successful measurement")).toBeTruthy();
  });

  it("labels the axis ends with direction text and no longer uses faster or slower colour swatches", () => {
    const { container } = render(
      <NormalizedSpeedupChart queries={wideQueries} results={WIDE_RESULTS} baselineIdx={0} />,
    );
    expect(container.querySelector("svg")?.textContent).toContain("← slower than baseline");
    expect(container.querySelector("svg")?.textContent).toContain("faster than baseline →");
    expect(container.querySelector("[style*='bb-chart-success']")).toBeNull();
    expect(container.querySelector("[style*='bb-chart-danger']")).toBeNull();
  });
});
