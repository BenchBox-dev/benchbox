import { render } from "@testing-library/preact";
import { describe, it, expect } from "vitest";
import type { BenchmarkSummary, PlatformRow } from "@/types";
import type { ChartHistoricalEntry } from "@/lib/chartRegistry";

import { PercentileLadder } from "@/components/PercentileLadder";
import { CDFChart } from "@/components/CDFChart";
import { RankTable } from "@/components/RankTable";
import { DistributionBox } from "@/components/DistributionBox";
import { QueryHistogram } from "@/components/QueryHistogram";
import { PowerBar } from "@/components/PowerBar";
import { StackedPhase } from "@/components/StackedPhase";
import { TimeSeries } from "@/components/TimeSeries";
import { CostScatter } from "@/components/CostScatter";
import { SparklineTable } from "@/components/SparklineTable";

function makePlatform(
  overrides: Partial<PlatformRow> = {},
): PlatformRow {
  const timings = overrides.timings ?? { Q1: 10, Q2: 20, Q3: 30 };
  return {
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
    has_display_timing: true,
    valid_query_count: 3,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    power_score: 3000,
    display_geomean_ms: 12,
    sample_geomean_ms: 12,
    cost_usd: null,
    compliance_class: null,
    percentile_stats: null,
    phase_durations: null,
    timings,
    timing_eligibility:
      overrides.timing_eligibility ??
      Object.fromEntries(
        Object.entries(timings).map(([queryId, ms]) => [
          queryId,
          {
            is_valid_display_timing: ms !== null && ms > 0,
            timing_exclusion_reason: ms === null ? "missing_timing" : ms === 0 ? "zero_timing" : null,
          },
        ]),
      ),
    ...overrides,
  };
}

function makeSummary(overrides: Partial<BenchmarkSummary> = {}): BenchmarkSummary {
  return {
    benchmark: "tpch",
    scale_factor: 0.1,
    phase: "power",
    query_ids: ["Q1", "Q2", "Q3"],
    platforms: [
      makePlatform({ result_id: "r1", platform_id: "duckdb", platform: "DuckDB" }),
      makePlatform({
        result_id: "r2",
        platform_id: "sqlite",
        platform: "SQLite",
        power_score: 1500,
        display_geomean_ms: 25,
        timings: { Q1: 20, Q2: 40, Q3: 60 },
      }),
    ],
    cell_reduction: "median",
    ranking: null,
    ...overrides,
  };
}

function emptySummary(): BenchmarkSummary {
  return makeSummary({ platforms: [], query_ids: [] });
}

describe("PercentileLadder", () => {
  it("renders nothing when given no rows", () => {
    const { container } = render(<PercentileLadder rows={[]} />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("renders an svg when given percentile rows", () => {
    const { container } = render(
      <PercentileLadder
        rows={[
          {
            result_id: "r-duckdb-tuned",
            platform: "DuckDB",
            percentile_stats: { p50: 10, p90: 25, p95: 40, p99: 90 },
          },
        ]}
      />,
    );
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("uses unique data-result-id even when two rows share a platform name", () => {
    const { container } = render(
      <PercentileLadder
        rows={[
          {
            result_id: "r-duckdb-tuned",
            platform: "DuckDB",
            percentile_stats: { p50: 10, p90: 25, p95: 40, p99: 90 },
          },
          {
            result_id: "r-duckdb-notuning",
            platform: "DuckDB",
            percentile_stats: { p50: 12, p90: 28, p95: 45, p99: 100 },
          },
        ]}
      />,
    );
    const ids = Array.from(container.querySelectorAll("[data-result-id]")).map(
      (el) => el.getAttribute("data-result-id") ?? "",
    );
    expect(ids).toEqual(["r-duckdb-tuned", "r-duckdb-notuning"]);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("keeps date-qualified labels distinct after truncation", () => {
    const { container } = render(
      <PercentileLadder
        rows={[
          {
            result_id: "r-date-1",
            platform: "DataFusion",
            displayLabel: "DataFusion 2026-05-01 (127 days ago)",
            percentile_stats: { p50: 10, p90: 25, p95: 40, p99: 90 },
          },
          {
            result_id: "r-date-2",
            platform: "DataFusion",
            displayLabel: "DataFusion 2026-05-02 (126 days ago)",
            percentile_stats: { p50: 12, p90: 28, p95: 45, p99: 100 },
          },
        ]}
      />,
    );

    const labels = Array.from(container.querySelectorAll("[data-result-id] text")).map((node) => node.textContent);
    expect(labels[0]).not.toBe(labels[1]);
  });
});

describe("chart run-identity disambiguation", () => {
  function duplicatePlatformSummary(): BenchmarkSummary {
    return makeSummary({
      platforms: [
        makePlatform({
          result_id: "r-df-44",
          platform_id: "datafusion",
          platform: "DataFusion",
          platform_version: "44",
          run_date: "2026-04-01",
        }),
        makePlatform({
          result_id: "r-df-45",
          platform_id: "datafusion",
          platform: "DataFusion",
          platform_version: "45",
          run_date: "2026-04-15",
          power_score: 2200,
          display_geomean_ms: 18,
          timings: { Q1: 15, Q2: 30, Q3: 45 },
        }),
      ],
    });
  }

  it("disambiguates duplicate DataFusion rows in DistributionBox", () => {
    const { container } = render(<DistributionBox summary={duplicatePlatformSummary()} />);
    const labels = Array.from(container.querySelectorAll("svg text"))
      .map((el) => el.textContent ?? "")
      .filter((text) => text.includes("DataFusion"));
    expect(labels.length).toBe(2);
    expect(new Set(labels).size).toBe(2);
  });

  it("disambiguates duplicate DataFusion rows in PowerBar", () => {
    const { container } = render(<PowerBar summary={duplicatePlatformSummary()} />);
    const labels = Array.from(container.querySelectorAll("svg text"))
      .map((el) => el.textContent ?? "")
      .filter((text) => text.includes("DataFusion"));
    expect(labels.length).toBe(2);
    expect(new Set(labels).size).toBe(2);
  });

  it("disambiguates duplicate DataFusion rows in SparklineTable", () => {
    const { container } = render(<SparklineTable summary={duplicatePlatformSummary()} />);
    const cellTexts = Array.from(container.querySelectorAll("tbody td"))
      .map((td) => td.textContent ?? "")
      .filter((text) => text.includes("DataFusion"));
    expect(cellTexts.length).toBe(2);
    expect(new Set(cellTexts).size).toBe(2);
  });
});

describe("CDFChart", () => {
  it("renders nothing for empty summary", () => {
    const { container } = render(<CDFChart summary={emptySummary()} />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("renders an svg for a populated summary", () => {
    const { container } = render(<CDFChart summary={makeSummary()} />);
    expect(container.querySelector("svg")).not.toBeNull();
  });
});

describe("RankTable", () => {
  it("renders nothing when platforms are empty", () => {
    const { container } = render(<RankTable summary={emptySummary()} />);
    expect(container.querySelector("table")).toBeNull();
  });

  it("assigns 1st/2nd rank per query and shows win counts", () => {
    const { container } = render(<RankTable summary={makeSummary()} />);
    const table = container.querySelector("table");
    expect(table).not.toBeNull();
    expect(table!.textContent).toContain("1st");
    expect(table!.textContent).toContain("2nd");
  });

  it("ties - platforms with equal timings get equal rank", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", platform: "A", timings: { Q1: 10 } }),
        makePlatform({ result_id: "r2", platform: "B", timings: { Q1: 10 } }),
      ],
      query_ids: ["Q1"],
    });
    const { container } = render(<RankTable summary={summary} />);
    const cells = container.querySelectorAll("td");
    const ranks = Array.from(cells).map((c) => c.textContent);
    expect(ranks.filter((r) => r === "1st").length).toBe(2);
  });

  it("sorts query rows naturally and preserves explicit labels", () => {
    const summary = makeSummary({
      query_ids: ["Q10", "Q2", "Q1"],
      platforms: [makePlatform({ timings: { Q1: 10, Q2: 20, Q10: 100 } })],
    });
    const { container } = render(<RankTable summary={summary} />);
    const labels = Array.from(container.querySelectorAll("tbody tr td:first-child")).map(
      (cell) => cell.textContent,
    );
    expect(labels).toStrictEqual(["Q1", "Q2", "Q10"]);
  });

  it("shows an empty state instead of an all-dash matrix when every row is excluded", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({
          result_id: "r1",
          platform_id: "duckdb",
          platform: "DuckDB",
          ranking_exclusion_reason: "trust_not_rankable",
        }),
        makePlatform({
          result_id: "r2",
          platform_id: "sqlite",
          platform: "SQLite",
          ranking_exclusion_reason: "validation_not_clean",
        }),
      ],
    });
    const { container } = render(<RankTable summary={summary} />);

    expect(container.querySelector("table")).toBeNull();
    expect(container.textContent).toContain("Rank chart unavailable");
    expect(container.textContent).toContain("The published runs are excluded");
    expect(container.textContent).toContain("Results from this source are not included in rankings.");
    expect(container.textContent).toContain("Validation status excludes this result from ranking.");
  });

  it("makes a ranking-exclusion marker keyboard focusable and self-describing", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", platform: "DuckDB" }),
        makePlatform({
          result_id: "r2",
          platform: "SQLite",
          ranking_exclusion_reason: "validation_not_clean",
        }),
      ],
    });
    const { container } = render(<RankTable summary={summary} />);
    const marker = container.querySelector("span[tabindex='0'][aria-label]");

    expect(marker).not.toBeNull();
    expect(marker?.getAttribute("title")).toContain("Validation status excludes this result");
  });
});

describe("DistributionBox", () => {
  it("renders nothing for empty summary", () => {
    const { container } = render(<DistributionBox summary={emptySummary()} />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("renders an svg for a populated summary", () => {
    const { container } = render(<DistributionBox summary={makeSummary()} />);
    expect(container.querySelector("svg")).not.toBeNull();
  });
});

describe("QueryHistogram", () => {
  it("renders nothing for empty summary", () => {
    const { container } = render(<QueryHistogram summary={emptySummary()} />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("renders one horizontal row with a marker for each run on a populated summary", () => {
    const { container } = render(<QueryHistogram summary={makeSummary()} />);
    const chart = container.querySelector("svg");
    expect(chart?.getAttribute("data-orientation")).toBe("horizontal");
    expect(chart?.getAttribute("aria-describedby")).toBe("query-histogram-description");
    const rows = Array.from(container.querySelectorAll("[data-query-row]"));
    expect(rows).toHaveLength(3);
    expect(rows[0]?.querySelectorAll("[data-platform-marker]")).toHaveLength(2);
    expect(container.querySelector("table caption")?.textContent).toContain("Per-query latency");
    expect(container.textContent).toContain("Latency (ms, log scale)");
    expect(container.querySelector("[role='group'][aria-label='Run legend']")).not.toBeNull();
  });

  it("uses a group role when query labels link out and an image role otherwise", () => {
    const linked = render(<QueryHistogram summary={makeSummary()} queryHref={(queryId) => `#${queryId}`} />);
    expect(linked.container.querySelector("svg")?.getAttribute("role")).toBe("group");
    const { container } = render(<QueryHistogram summary={makeSummary()} />);
    expect(container.querySelector("svg")?.getAttribute("role")).toBe("img");
  });

  it("uses timing eligibility instead of raw positive timings", () => {
    const summary = makeSummary({
      query_ids: ["Q1"],
      platforms: [
        makePlatform({
          timings: { Q1: 10 },
          timing_eligibility: {
            Q1: { is_valid_display_timing: false, timing_exclusion_reason: "zero_timing" },
          },
        }),
      ],
    });
    const { container } = render(<QueryHistogram summary={summary} />);

    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.querySelectorAll("path[data-platform-marker]")).toHaveLength(0);
    expect(container.querySelectorAll("[data-platform-marker]")).toHaveLength(1);
    expect(container.textContent).toContain("A zero timing cannot be used in this ranking.");
  });

  it("uses a mobile-safe responsive width instead of forcing horizontal overflow", () => {
    const { container } = render(<QueryHistogram summary={makeSummary()} />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("width")).toBe("100%");
    expect(svg?.getAttribute("viewBox")).toMatch(/^0 0 300 /);
    expect(Number(svg?.getAttribute("height"))).toBeGreaterThan(100);
  });

  it("keeps up to 10 run markers in one row per query", () => {
    const query_ids = ["Q1", "Q2"];
    const timings = Object.fromEntries(query_ids.map((queryId) => [queryId, 10]));
    const platforms = Array.from({ length: 10 }, (_, index) =>
      makePlatform({ result_id: `r${index}`, platform_id: `p${index}`, platform: `Platform ${index}`, timings }),
    );
    const { container } = render(<QueryHistogram summary={makeSummary({ query_ids, platforms })} />);

    const rows = Array.from(container.querySelectorAll("[data-query-row]"));
    expect(rows).toHaveLength(2);
    expect(rows[0]?.querySelectorAll("[data-platform-marker]")).toHaveLength(10);
    expect(rows[1]?.querySelectorAll("[data-platform-marker]")).toHaveLength(10);
    expect(container.querySelectorAll("table tbody tr")).toHaveLength(20);
  });

  it("auto-splits into multiple panels when query count > 33", () => {
    const query_ids = Array.from({ length: 70 }, (_, i) => `Q${i + 1}`);
    const timings: Record<string, number> = {};
    for (const qid of query_ids) timings[qid] = 10;
    const summary = makeSummary({
      query_ids,
      platforms: [makePlatform({ timings })],
    });
    const { container } = render(<QueryHistogram summary={summary} />);
    const svgs = container.querySelectorAll("svg");
    expect(svgs.length).toBeGreaterThan(1);
  });

  it("truncates a long label at the end and keeps its full name in the link label and a title", () => {
    const longQuery = "aggregation_distinct_groupby";
    const summary = makeSummary({
      query_ids: [longQuery, "Q1"],
      platforms: [makePlatform({ timings: { [longQuery]: 10, Q1: 20 } })],
    });
    const { container } = render(<QueryHistogram summary={summary} queryHref={(queryId) => `#${queryId}`} />);
    const label = container.querySelector(`text[data-query-label='${longQuery}']`);
    expect(label?.firstChild?.textContent?.endsWith("…")).toBe(true);
    expect(label?.firstChild?.textContent).not.toBe(longQuery);
    expect(label?.querySelector("title")?.textContent).toBe(longQuery);
    expect(container.querySelector(`a[aria-label='Compare ${longQuery} across selected runs']`)).not.toBeNull();
  });

  it("adds a title only to truncated labels", () => {
    const longQuery = "broadcast_join_three_tables_with_extra_words";
    const summary = makeSummary({
      query_ids: [longQuery, "Q1"],
      platforms: [makePlatform({ timings: { [longQuery]: 10, Q1: 20 } })],
    });
    const { container } = render(<QueryHistogram summary={summary} />);
    expect(container.querySelector("text[data-query-label='Q1'] title")).toBeNull();
    expect(container.querySelector(`text[data-query-label='${longQuery}'] title`)).not.toBeNull();
    expect(container.querySelector("text[data-query-label='Q1']")?.textContent).toBe("Q1");
  });

  it("keeps every axis tick label inside the svg width", () => {
    const summary = makeSummary({
      query_ids: ["Q1", "Q2", "Q3"],
      platforms: [
        makePlatform({ result_id: "r1", timings: { Q1: 0.4, Q2: 627, Q3: 6840 } }),
        makePlatform({ result_id: "r2", platform_id: "sqlite", platform: "SQLite", timings: { Q1: 0.9, Q2: 127, Q3: 5100 } }),
      ],
    });
    const { container } = render(<QueryHistogram summary={summary} />);
    const svg = container.querySelector("svg");
    const width = Number(svg?.getAttribute("viewBox")?.split(/\s+/)[2]);
    const tickTexts = Array.from(svg?.querySelectorAll("text") ?? []).filter(
      (node) => /ms|s$/.test(node.textContent ?? "") && !node.hasAttribute("data-query-label"),
    );
    expect(tickTexts.length).toBeGreaterThanOrEqual(2);
    for (const node of tickTexts) {
      const x = Number(node.getAttribute("x"));
      const textWidth = (node.textContent ?? "").length * 0.62 * 9;
      const anchor = node.getAttribute("text-anchor");
      const left = anchor === "start" ? x : anchor === "end" ? x - textWidth : x - textWidth / 2;
      const right = left + textWidth;
      expect(left, node.textContent ?? "").toBeGreaterThanOrEqual(0);
      expect(right, node.textContent ?? "").toBeLessThanOrEqual(width);
    }
    const tickLabels = tickTexts.map((node) => node.textContent);
    expect(new Set(tickLabels).size).toBe(tickLabels.length);
  });

  it("draws missing values in a gutter left of the plot start", () => {
    const summary = makeSummary({
      query_ids: ["Q1", "Q2"],
      platforms: [
        makePlatform({ result_id: "r1", timings: { Q1: 10, Q2: 300 } }),
        makePlatform({ result_id: "r2", platform_id: "sqlite", platform: "SQLite", timings: { Q1: null, Q2: 40 } }),
      ],
    });
    const { container } = render(<QueryHistogram summary={summary} />);
    const plotStart = Number(container.querySelector("svg")?.getAttribute("data-plot-start"));
    const missing = container.querySelector("circle[data-platform-marker='r2']");
    expect(missing).not.toBeNull();
    expect(Number(missing?.getAttribute("cx"))).toBeLessThan(plotStart - 8);
    for (const marker of Array.from(container.querySelectorAll("path[data-platform-marker]"))) {
      expect(Number(marker.getAttribute("d")?.match(/M([\d.]+)/)?.[1])).toBeGreaterThan(plotStart - 8);
    }
  });

  it("explains missing values in the legend only when the chart has some", () => {
    const withMissing = render(
      <QueryHistogram
        summary={makeSummary({
          platforms: [makePlatform({ result_id: "r1", timings: { Q1: 10, Q2: null, Q3: 30 } })],
        })}
      />,
    );
    expect(withMissing.container.querySelector("[aria-label='Run legend']")?.textContent).toContain(
      "No successful measurement",
    );
    const complete = render(<QueryHistogram summary={makeSummary()} />);
    expect(complete.container.querySelector("[aria-label='Run legend']")?.textContent).not.toContain(
      "No successful measurement",
    );
  });

  it("sorts query labels naturally and does not strip prefixes", () => {
    const summary = makeSummary({
      query_ids: ["Q10", "Q2", "Q1"],
      platforms: [makePlatform({ timings: { Q1: 10, Q2: 20, Q10: 100 } })],
    });
    const { container } = render(<QueryHistogram summary={summary} />);
    const labels = Array.from(container.querySelectorAll("text[data-query-label]")).map(
      (label) => label.firstChild?.textContent,
    );
    expect(labels).toStrictEqual(["Q1", "Q2", "Q10"]);
  });
});

describe("PowerBar", () => {
  it("shows a 'not available' message when no power_score", () => {
    const summary = makeSummary({
      platforms: [makePlatform({ power_score: null }), makePlatform({ power_score: 0 })],
    });
    const { container } = render(<PowerBar summary={summary} />);
    expect(container.textContent).toContain("not available");
  });

  it("renders bars sorted descending by power_score", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", platform: "Low", power_score: 100 }),
        makePlatform({ result_id: "r2", platform: "High", power_score: 900 }),
        makePlatform({ result_id: "r3", platform: "Mid", power_score: 500 }),
      ],
    });
    const { container } = render(<PowerBar summary={summary} />);
    const labels = Array.from(container.querySelectorAll("text"))
      .map((t) => t.textContent)
      .filter((t): t is string => !!t);
    const highIdx = labels.indexOf("High");
    const midIdx = labels.indexOf("Mid");
    const lowIdx = labels.indexOf("Low");
    expect(highIdx).toBeGreaterThanOrEqual(0);
    expect(highIdx).toBeLessThan(midIdx);
    expect(midIdx).toBeLessThan(lowIdx);
  });

  it("labels subscale Power@Size as unofficial and marks derived scores", () => {
    const summary = makeSummary({
      scale_factor: 0.01,
      platforms: [
        makePlatform({ result_id: "r1", platform: "High", power_score: 900, power_score_source: "derived_from_timings" }),
      ],
    });
    const { container } = render(<PowerBar summary={summary} />);

    expect(container.textContent).toContain("Power@Size (subscale, unofficial) - higher is better");
    expect(container.querySelector("title")?.textContent ?? "").toContain("(derived from query timings)");
  });

  it("labels the metric Power@Size without a QphH unit", () => {
    const summary = makeSummary({
      scale_factor: 1,
      platforms: [makePlatform({ result_id: "r1", platform: "High", power_score: 900 })],
    });
    const { container } = render(<PowerBar summary={summary} />);

    expect(container.textContent).toContain("Power@Size - higher is better");
    expect(container.textContent).not.toContain("QphH");
    const tooltip = container.querySelector("title")?.textContent ?? "";
    expect(tooltip).toContain("Power@Size");
    expect(tooltip).not.toContain("QphH");
  });

  it("excludes power scores from rows that are not rank-safe", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({ result_id: "r1", platform: "Community High", power_score: 9000, ranking_exclusion_reason: "trust_not_rankable" }),
        makePlatform({ result_id: "r2", platform: "Rankable Low", power_score: 1000 }),
      ],
    });
    const { container } = render(<PowerBar summary={summary} />);

    expect(container.textContent).not.toContain("Community High");
    expect(container.textContent).toContain("Rankable Low");
  });
});

describe("StackedPhase", () => {
  it("shows a 'not available' message when no phase_durations", () => {
    const { container } = render(<StackedPhase summary={makeSummary()} />);
    expect(container.textContent).toContain("not available");
  });

  it("renders segments when phase_durations are populated", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({
          phase_durations: { data_loading: 10, power_test: 30, throughput_test: 5 },
        }),
      ],
    });
    const { container } = render(<StackedPhase summary={summary} />);
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.querySelectorAll("rect").length).toBeGreaterThan(0);
  });
});

function makeEntry(
  overrides: Partial<ChartHistoricalEntry> = {},
): ChartHistoricalEntry {
  return {
    result_id: "r1",
    benchmark: "tpch",
    scale_factor: 0.1,
    platform: "DuckDB",
    platform_id: "duckdb",
    run_date: "2026-04-01",
    power_score: 3000,
    display_geomean_ms: 12,
    ...overrides,
  };
}

describe("TimeSeries", () => {
  it("keeps narrow-range latency axis labels distinct", () => {
    const entries = Array.from({ length: 7 }, (_, index) => makeEntry({
      result_id: `narrow-${index}`,
      run_date: `2026-04-0${index + 1}`,
      display_geomean_ms: 11.1 + index * 0.01,
    }));
    const { container } = render(<TimeSeries entries={entries} />);
    const labels = [...container.querySelectorAll("svg text")]
      .map((node) => node.textContent ?? "")
      .filter((label) => label.endsWith(" ms"));
    expect(labels.length).toBeGreaterThanOrEqual(2);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("shows an insufficient-data message when fewer than 2 runs per platform", () => {
    const { container } = render(
      <TimeSeries entries={[makeEntry({ result_id: "r1" })]} />,
    );
    expect(container.textContent).toContain("Not enough");
  });

  it("renders a line chart when ≥2 runs exist", () => {
    const entries = [
      makeEntry({ result_id: "r1", run_date: "2026-03-01", display_geomean_ms: 15 }),
      makeEntry({ result_id: "r2", run_date: "2026-04-01", display_geomean_ms: 10 }),
    ];
    const { container } = render(<TimeSeries entries={entries} />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(Number(svg?.getAttribute("height"))).toBeGreaterThan(100);
    expect(container.querySelector("path")).not.toBeNull();
  });

  it("uses point-specific result identity in titles and aria labels", () => {
    const entries = [
      makeEntry({ result_id: "tpch-duckdb-sf0.01-20260401-1111aaaa", run_date: "2026-04-01", display_geomean_ms: 15 }),
      makeEntry({ result_id: "tpch-duckdb-sf0.01-20260402-2222bbbb", run_date: "2026-04-02", display_geomean_ms: 10 }),
    ];
    const { container } = render(<TimeSeries entries={entries} />);
    const points = Array.from(container.querySelectorAll("circle[data-result-id]"));

    expect(points).toHaveLength(2);
    expect(points[0]?.querySelector("title")?.textContent).toContain("1111aaaa");
    expect(points[0]?.getAttribute("aria-label")).toContain("1111aaaa");
    expect(points[0]?.querySelector("title")?.textContent).toMatch(/2026-04-01.*days ago/);
    expect(points[0]?.getAttribute("aria-label")).toMatch(/2026-04-01.*days ago/);
    expect(container.querySelector('text[aria-label*="days ago"]')).not.toBeNull();
    expect(points[0]?.querySelector("title")?.textContent).not.toContain("2222bbbb");
    expect(points[1]?.querySelector("title")?.textContent).toContain("2222bbbb");
    expect(points[1]?.getAttribute("aria-label")).toContain("2222bbbb");
    expect(points[1]?.querySelector("title")?.textContent).not.toContain("1111aaaa");
  });

  it("suppresses trend lines and links runs when same-platform points share a date", () => {
    const entries = [
      makeEntry({ result_id: "tpch-duckdb-sf0.01-20260403-1111aaaa", run_date: "2026-04-03", display_geomean_ms: 15 }),
      makeEntry({ result_id: "tpch-duckdb-sf0.01-20260403-2222bbbb", run_date: "2026-04-03", display_geomean_ms: 12 }),
      makeEntry({ result_id: "tpch-duckdb-sf0.01-20260404-3333cccc", run_date: "2026-04-04", display_geomean_ms: 10 }),
    ];
    const { container } = render(<TimeSeries entries={entries} />);
    const state = container.querySelector('[data-testid="time-series-duplicate-day"]');

    expect(container.querySelector("svg")).toBeNull();
    expect(state?.textContent).toContain("cannot be ordered in this trend");
    expect(state?.textContent).toContain("same-day runs");
    expect(state?.textContent).toMatch(/2026-04-03/);
    expect(state?.querySelectorAll("[data-result-id]")).toHaveLength(2);
    expect(state?.querySelector('a[href="/results/r/tpch-duckdb-sf0.01-20260403-1111aaaa"]')).toBeTruthy();
    expect(state?.querySelector('a[href="/results/r/tpch-duckdb-sf0.01-20260403-2222bbbb#run-receipt"]')).toBeTruthy();
  });

  it("keeps unaffected platform trend lines when another platform has same-day duplicates", () => {
    const entries = [
      makeEntry({ result_id: "tpch-duckdb-sf0.01-20260403-1111aaaa", run_date: "2026-04-03", display_geomean_ms: 15 }),
      makeEntry({ result_id: "tpch-duckdb-sf0.01-20260403-2222bbbb", run_date: "2026-04-03", display_geomean_ms: 12 }),
      makeEntry({
        result_id: "tpch-polars-sf0.01-20260403-3333cccc",
        platform: "Polars",
        platform_id: "polars",
        run_date: "2026-04-03",
        display_geomean_ms: 20,
      }),
      makeEntry({
        result_id: "tpch-polars-sf0.01-20260404-4444dddd",
        platform: "Polars",
        platform_id: "polars",
        run_date: "2026-04-04",
        display_geomean_ms: 18,
      }),
    ];
    const { container } = render(<TimeSeries entries={entries} />);
    const duplicateState = container.querySelector('[data-testid="time-series-duplicate-day"]');
    const plottedIds = Array.from(container.querySelectorAll("circle[data-result-id]")).map(
      (el) => el.getAttribute("data-result-id"),
    );

    expect(duplicateState).not.toBeNull();
    expect(container.querySelector("svg")).not.toBeNull();
    expect(duplicateState?.querySelectorAll("[data-result-id]")).toHaveLength(2);
    expect(plottedIds).toEqual([
      "tpch-polars-sf0.01-20260403-3333cccc",
      "tpch-polars-sf0.01-20260404-4444dddd",
    ]);
  });

  it("keeps point titles unique when platform display names repeat", () => {
    const entries = [
      makeEntry({
        result_id: "tpch-datafusion-sf0.01-20260401-1111aaaa",
        platform: "DataFusion",
        platform_id: "datafusion",
        run_date: "2026-04-01",
        display_geomean_ms: 15,
      }),
      makeEntry({
        result_id: "tpch-datafusion-sf0.01-20260402-2222bbbb",
        platform: "DataFusion",
        platform_id: "datafusion",
        run_date: "2026-04-02",
        display_geomean_ms: 12,
      }),
      makeEntry({
        result_id: "tpch-datafusion-44-sf0.01-20260401-3333cccc",
        platform: "DataFusion",
        platform_id: "datafusion-44",
        run_date: "2026-04-01",
        display_geomean_ms: 16,
      }),
      makeEntry({
        result_id: "tpch-datafusion-44-sf0.01-20260402-4444dddd",
        platform: "DataFusion",
        platform_id: "datafusion-44",
        run_date: "2026-04-02",
        display_geomean_ms: 13,
      }),
    ];
    const { container } = render(<TimeSeries entries={entries} />);
    const titles = Array.from(container.querySelectorAll("circle title")).map((title) => title.textContent ?? "");

    expect(titles).toHaveLength(4);
    expect(new Set(titles).size).toBe(titles.length);
  });

  it("dedupes duplicate result_id entries before plotting", () => {
    const entries = [
      makeEntry({ result_id: "r1", run_date: "2026-03-01", display_geomean_ms: 15 }),
      makeEntry({ result_id: "r1", run_date: "2026-03-01", display_geomean_ms: 15 }),
      makeEntry({ result_id: "r2", run_date: "2026-04-01", display_geomean_ms: 10 }),
    ];
    const { container } = render(<TimeSeries entries={entries} />);
    const ids = Array.from(container.querySelectorAll("[data-result-id]")).map(
      (el) => el.getAttribute("data-result-id") ?? "",
    );
    expect(new Set(ids).size).toBe(2);
  });
});

describe("CostScatter", () => {
  it("shows why normalized cost is unavailable when metadata is missing", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({
          cost_status: "unavailable",
          normalized_cost_usd: null,
          cloud_provider: null,
          cloud_region: null,
          pricing_region: null,
          warehouse_size: null,
        }),
      ],
    });
    const { container } = render(<CostScatter summary={summary} />);
    expect(container.textContent).toContain("No normalized cost data");
    expect(container.textContent).toContain("cloud provider");
    expect(container.textContent).toContain("region");
  });

  it("shows a legacy-schema message when normalized cost fields are absent", () => {
    const { container } = render(<CostScatter summary={makeSummary()} />);
    expect(container.textContent).toContain("older runs do not include the cost details needed for comparison");
  });

  it("renders points when normalized cost is populated", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({
          result_id: "r1",
          platform: "A",
          cost_usd: 0.5,
          normalized_cost_usd: 0.5,
          cost_status: "normalized",
          cost_scope: "compute_only",
          cost_model_version: "2026.05.0",
        }),
        makePlatform({
          result_id: "r2",
          platform: "B",
          cost_usd: 1.25,
          normalized_cost_usd: 1.25,
          cost_status: "normalized",
          cost_scope: "compute_only",
          cost_model_version: "2026.05.0",
        }),
      ],
    });
    const { container } = render(<CostScatter summary={summary} />);
    expect(container.querySelector("svg")).not.toBeNull();
    const circles = container.querySelectorAll("circle");
    expect(circles.length).toBe(2);
    expect(container.textContent).toContain("model 2026.05.0");
  });

  it("plots estimated runs as hollow markers next to normalized runs", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({
          result_id: "tpch-snowflake-sf10.0-20261003-f0835929",
          platform: "Snowflake",
          cost_usd: 0.5,
          normalized_cost_usd: 0.5,
          cost_status: "normalized",
          cost_scope: "compute_only",
          cost_model_version: "2026.05.0",
        }),
        makePlatform({
          result_id: "amplab-sf01-databricks-sql-20261003-214137-2a7a0cdb",
          platform: "Databricks",
          cost_status: "estimated",
          normalized_cost_usd: null,
          estimated_cost_usd: 0.0156,
          cost_scope: "compute_only",
          cost_model_version: "2026.09",
        }),
      ],
    });
    const { container } = render(<CostScatter summary={summary} />);
    const circles = Array.from(container.querySelectorAll("circle"));

    expect(circles).toHaveLength(2);
    const estimated = circles.find((circle) => circle.querySelector("title")?.textContent?.includes("Databricks"));
    expect(estimated?.getAttribute("fill")).toBe("none");
    expect(estimated?.querySelector("title")?.textContent).toContain("estimated ≈ $0.0156");
    const metered = circles.find((circle) => circle.querySelector("title")?.textContent?.includes("Snowflake"));
    expect(metered?.getAttribute("fill")).not.toBe("none");
    expect(container.textContent).toContain("hollow = estimated");
  });
});

describe("SparklineTable", () => {
  it("renders nothing when platforms are empty", () => {
    const { container } = render(<SparklineTable summary={emptySummary()} />);
    expect(container.querySelector("table")).toBeNull();
  });

  it("renders one row per platform", () => {
    const { container } = render(<SparklineTable summary={makeSummary()} />);
    const bodyRows = container.querySelectorAll("tbody tr");
    expect(bodyRows.length).toBe(2);
  });

  it("hides the normalized cost column when no normalized cost data is present", () => {
    const { container } = render(<SparklineTable summary={makeSummary()} />);
    expect(container.textContent).not.toContain("Normalized cost");
  });

  it("shows the normalized cost column when normalized cost data is present", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({
          cost_usd: 0.5,
          normalized_cost_usd: 0.5,
          cost_status: "normalized",
          cost_scope: "compute_only",
          cost_model_version: "2026.05.0",
        }),
        makePlatform({ result_id: "r2", cost_status: "unavailable", normalized_cost_usd: null }),
      ],
    });
    const { container } = render(<SparklineTable summary={summary} />);
    expect(container.textContent).toContain("Normalized cost");
    expect(container.textContent).toContain("Not recorded");
  });

  it("shows the estimated dollar value with provenance for estimated runs", () => {
    const summary = makeSummary({
      platforms: [
        makePlatform({
          result_id: "tpch-snowflake-sf10.0-20261003-f0835929",
          platform: "Snowflake",
          cost_status: "estimated",
          normalized_cost_usd: null,
          estimated_cost_usd: 0.0156,
          cost_estimate_method: "sum of per-query median runtime hours × warehouse credits per hour × USD per credit",
          cost_estimate_pricing_date: "2026-10-09",
          cost_estimate_assumptions: '["Standard edition is assumed."]',
        }),
        makePlatform({
          result_id: "amplab-sf01-databricks-sql-20261003-214137-2a7a0cdb",
          platform: "Databricks",
          cost_status: "unavailable",
          normalized_cost_usd: null,
        }),
      ],
    });
    const { container } = render(<SparklineTable summary={summary} />);
    const snowflakeCell = Array.from(container.querySelectorAll("tbody tr")[0]!.querySelectorAll("td")).at(-1)!;

    expect(snowflakeCell.textContent).toBe("≈ $0.0156 (est.)");
    expect(snowflakeCell.getAttribute("title")).toContain("Timing-derived estimate, not metered.");
    expect(snowflakeCell.getAttribute("title")).toContain("Pricing date: 2026-10-09");
    expect(snowflakeCell.getAttribute("title")).toContain("- Standard edition is assumed.");
    expect(container.textContent).not.toContain("Estimated cost (not metered)");
    expect(container.textContent).toContain("Not recorded");
  });
});
