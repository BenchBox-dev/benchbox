import { render } from "@testing-library/preact";
import { describe, it, expect } from "vitest";
import { DivergingBarChart } from "@/components/DivergingBarChart";

const DUCK_BASELINE = {
  result_id: "duckdb-sf1-20261001-aaaa1111",
  platform: "DuckDB",
  run_date: "2026-10-01T12:00:00Z",
  scale_factor: 1,
};
const DUCK_CANDIDATE = {
  result_id: "duckdb-sf1-20261005-bbbb2222",
  platform: "DuckDB",
  run_date: "2026-10-05T12:00:00Z",
  scale_factor: 1,
};
const SQLITE_CANDIDATE = {
  result_id: "sqlite-sf1-20261003-cccc3333",
  platform: "SQLite",
  run_date: "2026-10-03T12:00:00Z",
  scale_factor: 1,
};
const RUNS = [DUCK_BASELINE, DUCK_CANDIDATE, SQLITE_CANDIDATE];

const QUERIES = [
  {
    queryId: "Q1",
    timings: [
      { ms: 100, status: "pass" },
      { ms: 150, status: "pass" },
      { ms: 60, status: "pass" },
    ],
  },
  {
    queryId: "Q2",
    timings: [
      { ms: 100, status: "pass" },
      { ms: 1000, status: "pass" },
      { ms: 30, status: "pass" },
    ],
  },
];

function svgWidth(container: Element): number {
  return Number(container.querySelector("svg")?.getAttribute("viewBox")?.split(/\s+/)[2]);
}

function valueLabels(container: Element): SVGTextElement[] {
  return Array.from(container.querySelectorAll("text[data-value-label]")) as SVGTextElement[];
}

describe("DivergingBarChart", () => {
  it("returns nothing without a comparison", () => {
    const { container } = render(<DivergingBarChart queries={QUERIES} results={[DUCK_BASELINE]} baselineIdx={0} />);
    expect(container.firstChild).toBeNull();
  });

  it("keeps every value label inside the svg and outside its bar", () => {
    const { container } = render(<DivergingBarChart queries={QUERIES} results={RUNS} baselineIdx={0} />);
    const width = svgWidth(container);
    const labels = valueLabels(container);
    expect(labels.length).toBeGreaterThan(0);

    for (const label of labels) {
      const x = Number(label.getAttribute("x"));
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(width);
      const bar = label.parentElement?.querySelector("rect");
      const barX = Number(bar?.getAttribute("x"));
      const barEnd = barX + Number(bar?.getAttribute("width"));
      if (label.getAttribute("text-anchor") === "start") {
        expect(x).toBeGreaterThan(barEnd);
      } else {
        expect(x).toBeLessThan(barX);
      }
    }
  });

  it("marks a change beyond the cap with an arrow and writes its true value outside the bar", () => {
    const { container } = render(<DivergingBarChart queries={QUERIES} results={RUNS} baselineIdx={0} />);
    const overflowGroup = container.querySelector("g[data-query-row='Q2']");
    expect(overflowGroup?.querySelector("polygon")).not.toBeNull();
    const label = Array.from(overflowGroup?.querySelectorAll("text[data-value-label]") ?? []).find(
      (node) => node.textContent === "+900.0%",
    );
    expect(label).toBeTruthy();
    expect(container.querySelector("svg")?.getAttribute("data-overflow-count")).toBe("1");
  });

  it("writes value labels in primary text colour rather than the bar colour", () => {
    const { container } = render(<DivergingBarChart queries={QUERIES} results={RUNS} baselineIdx={0} />);
    for (const label of valueLabels(container)) {
      expect(label.getAttribute("fill")).toBe("var(--bb-data-fg-primary)");
    }
  });

  it("labels runs by identity so two DuckDB runs get different labels", () => {
    const identityQueries = [
      { queryId: "Q1", timings: [{ ms: 100, status: "pass" }, { ms: 150, status: "pass" }, { ms: 60, status: "pass" }] },
    ];
    const { container } = render(
      <DivergingBarChart queries={identityQueries} results={[SQLITE_CANDIDATE, DUCK_BASELINE, DUCK_CANDIDATE]} baselineIdx={0} />,
    );
    const q1Labels = Array.from(container.querySelectorAll("g[data-query-row='Q1'] text[data-value-label]")).map(
      (node) => node.getAttribute("data-value-label") ?? "",
    );
    expect(q1Labels).toHaveLength(2);
    const duckLabels = q1Labels.filter((label) => label.includes("DuckDB"));
    expect(duckLabels).toHaveLength(2);
    expect(duckLabels[0]).not.toBe(duckLabels[1]);
    expect(q1Labels).not.toContain("DuckDB");
  });

  it("keeps run rows at 12 px or less and query padding at 6 px", () => {
    const { container } = render(<DivergingBarChart queries={QUERIES} results={RUNS} baselineIdx={0} />);
    const height = Number(container.querySelector("svg")?.getAttribute("viewBox")?.split(/\s+/)[3]);
    const candidateCount = RUNS.length - 1;
    const maximumHeight = 18 + QUERIES.length * (candidateCount * 12 + 6) + 4;
    expect(height).toBeLessThanOrEqual(maximumHeight);
    for (const bar of Array.from(container.querySelectorAll("svg rect[data-run-index], svg g rect"))) {
      expect(Number(bar.getAttribute("height"))).toBeLessThanOrEqual(12);
    }
  });

  it("truncates a long query label with an ellipsis and keeps the full name in a title", () => {
    const longId = "aggregation_distinct_groupby_with_a_very_long_descriptive_name";
    const { container } = render(
      <DivergingBarChart
        queries={[{ queryId: longId, timings: [{ ms: 100, status: "pass" }, { ms: 120, status: "pass" }] }]}
        results={[DUCK_BASELINE, SQLITE_CANDIDATE]}
        baselineIdx={0}
      />,
    );
    const label = container.querySelector(`[data-query-label='${longId}']`);
    expect(label?.firstChild?.textContent?.endsWith("…")).toBe(true);
    expect(label?.querySelector("title")?.textContent).toBe(longId);
  });
});

describe("DivergingBarChart legend", () => {
  it("leaves out the baseline run even when its colour slot differs from its position", () => {
    const sqliteBaseline = { ...SQLITE_CANDIDATE, platform_id: "sqlite" };
    const duckdb = { ...DUCK_CANDIDATE, platform_id: "duckdb" };
    const snowflake = {
      result_id: "tpch-snowflake-sf10.0-20261003-f0835929",
      platform: "Snowflake",
      platform_id: "snowflake",
      run_date: "2026-10-03T12:00:00Z",
      scale_factor: 1,
    };
    const { getByRole } = render(
      <DivergingBarChart queries={QUERIES} results={[sqliteBaseline, duckdb, snowflake]} baselineIdx={0} />,
    );
    const legend = getByRole("group", { name: "Run legend" });
    expect(legend.textContent).toContain("DuckDB");
    expect(legend.textContent).toContain("Snowflake");
    expect(legend.textContent).not.toContain("SQLite");
  });
});
