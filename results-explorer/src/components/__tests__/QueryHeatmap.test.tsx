import { fireEvent, render, screen, within } from "@testing-library/preact";
import { describe, it, expect } from "vitest";
import { expectNoAxeViolations } from "@/testing/axe-helper";
import { BENCHMARK_MATRIX_DENSITY_CONTRACT, QueryHeatmap } from "@/components/QueryHeatmap";
import type { BenchmarkSummary, PlatformRow } from "@/types";

const TIMING_ELIGIBLE = {
  has_display_timing: true,
  valid_query_count: 2,
  missing_query_count: 0,
  zero_timing_count: 0,
  display_exclusion_reason: null,
  comparison_exclusion_reason: null,
  ranking_exclusion_reason: null,
};

function timingEligibility(timings: Record<string, number | null>): PlatformRow["timing_eligibility"] {
  return Object.fromEntries(
    Object.entries(timings).map(([queryId, ms]) => [
      queryId,
      {
        is_valid_display_timing: ms !== null && ms > 0,
        timing_exclusion_reason: ms === null ? "missing_timing" : ms === 0 ? "zero_timing" : null,
      },
    ]),
  );
}

type PlatformRowFixture = Omit<PlatformRow, "timing_eligibility"> & Partial<Pick<PlatformRow, "timing_eligibility">>;
type SummaryFixtureOverrides = Partial<Omit<BenchmarkSummary, "platforms">> & {
  platforms?: PlatformRowFixture[];
};

function withTimingEligibility(row: PlatformRowFixture): PlatformRow {
  return {
    ...row,
    timing_eligibility: timingEligibility(row.timings),
  };
}

function makeSummary(overrides: SummaryFixtureOverrides = {}): BenchmarkSummary {
  const summary: Omit<BenchmarkSummary, "platforms"> & { platforms: PlatformRowFixture[] } = {
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
        validation_status: "exact",
        run_date: "2026-04-01",
        is_ranking_eligible: true,
        ...TIMING_ELIGIBLE,
        power_score: 3000,
        display_geomean_ms: 10,
        compliance_class: null,
        sample_geomean_ms: 12,
        cost_usd: null,
        percentile_stats: null,
        phase_durations: null,
        timings: { Q1: 10, Q2: 20 },
        timing_eligibility: {},
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
        trust_label: "maintainer-run",
        funding: "unspecified",
        validation_status: "loose",
        run_date: "2026-04-01",
        is_ranking_eligible: true,
        ...TIMING_ELIGIBLE,
        power_score: 800,
        display_geomean_ms: 100,
        compliance_class: null,
        sample_geomean_ms: 120,
        cost_usd: null,
        percentile_stats: null,
        phase_durations: null,
        timings: { Q1: 100, Q2: 200 },
        timing_eligibility: {},
      },
    ],
    cell_reduction: "median_successful_measurement_ms",
    ranking: {
      primary_metric: "power_score",
      secondary_metric: "display_geomean_ms",
      primary_order: "desc",
    },
    ...overrides,
  };
  return {
    ...summary,
    platforms: summary.platforms.map(withTimingEligibility),
  };
}

function matrixThead(): HTMLElement {
  const thead = screen.getByTestId("query-heatmap-scroll-container").querySelector("thead");
  expect(thead).not.toBeNull();
  return thead as HTMLElement;
}

describe("QueryHeatmap rendering", () => {
  it("renders platform names", () => {
    render(<QueryHeatmap summary={makeSummary()} />);
    expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SQLite").length).toBeGreaterThan(0);
  });

  it("renders query column headers", () => {
    render(<QueryHeatmap summary={makeSummary()} />);
    expect(screen.getByRole("button", { name: /^Q1/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Q2/ })).toBeTruthy();
  });

  it("sorts query headers naturally while preserving explicit labels", () => {
    const summary = makeSummary({
      query_ids: ["Q10", "Q2", "Q1"],
      platforms: [
        {
          ...makeSummary().platforms[0]!,
          timings: { Q1: 10, Q2: 20, Q10: 100 },
        },
      ],
    });
    render(<QueryHeatmap summary={summary} />);

    const labels = Array.from(matrixThead().querySelectorAll("button[data-query-label]")).map(
      (button) => button.getAttribute("data-query-label"),
    );
    expect(labels).toStrictEqual(["Q1", "Q2", "Q10"]);
    expect(screen.getByRole("button", { name: /^Q10/ })).toBeTruthy();
  });

  it("applies cumulative sticky-left offsets to the frozen header columns", () => {
    render(<QueryHeatmap summary={makeSummary()} />);
    const thead = matrixThead();
    const primaryHeader = within(thead).getByRole("columnheader", { name: /^Power score/ });
    const platformHeader = within(thead).getByRole("columnheader", { name: /^Platform/ });
    expect(platformHeader.getAttribute("style")).toContain("left: 0rem");
    expect(platformHeader.className).toContain("sticky");
    expect(primaryHeader.getAttribute("style")).toContain("left: 16.5rem");
    expect(within(thead).queryByRole("columnheader", { name: "Trust" })).toBeNull();
  });

  it("shifts sticky offsets right by the checkbox column width when selection is enabled", () => {
    render(<QueryHeatmap summary={makeSummary()} selectedIds={new Set()} onSelectionChange={() => {}} />);
    const thead = matrixThead();
    const platformHeader = within(thead).getByRole("columnheader", { name: /^Platform/ });
    const primaryHeader = within(thead).getByRole("columnheader", { name: /^Power score/ });
    expect(platformHeader.getAttribute("style")).toContain("left: 2.5rem");
    expect(primaryHeader.getAttribute("style")).toContain("left: 19rem");
  });

  it("renders the page-sticky header outside the horizontal scroll container", () => {
    render(<QueryHeatmap summary={makeSummary()} />);
    const pageStickyShell = screen.getByTestId("query-heatmap-page-sticky-header-shell");
    const scrollContainer = screen.getByTestId("query-heatmap-scroll-container");
    const tableHeaderRow = scrollContainer.querySelector("thead tr")!;
    const platformHeader = within(matrixThead()).getByRole("columnheader", { name: /^Platform/ });

    expect(pageStickyShell.className).toContain("sticky");
    expect(pageStickyShell.className).toContain("top-0");
    expect(pageStickyShell.className).toMatch(/\bz-40\b/);
    expect(pageStickyShell.closest(".overflow-x-auto")).toBeNull();
    expect(tableHeaderRow.className).not.toContain("top-0");
    expect(tableHeaderRow.className).not.toMatch(/\bsticky\b/);
    expect(platformHeader.className).toMatch(/\bz-30\b/);
  });

  it("keeps the frozen platform cell to one line of identity", () => {
    const { container } = render(
      <QueryHeatmap
        summary={makeSummary({
          platforms: [{ ...makeSummary().platforms[0]!, platform_version: "V1.2.3" }],
        })}
      />,
    );
    const firstRow = container.querySelector("tbody tr");
    expect(firstRow).not.toBeNull();
    const cells = firstRow!.querySelectorAll('td[role="gridcell"]');
    expect(within(cells[0] as HTMLElement).getByText("V1.2.3")).toBeTruthy();
    expect(cells[0]?.textContent ?? "").not.toContain("vV1.2.3");
    expect(cells[0]?.querySelector("details")).toBeNull();
    expect(firstRow!.querySelectorAll("td.sticky")).toHaveLength(
      BENCHMARK_MATRIX_DENSITY_CONTRACT.frozenColumns.length - 1,
    );
  });

  it("renders a nameable compliance marker instead of inline parenthetical text", () => {
    const noncompliantSummary = makeSummary({
      platforms: [
        {
          ...makeSummary().platforms[0]!,
          result_id: "platform-noncompliant",
          is_ranking_eligible: false,
          ranking_exclusion_reason: "compliance_not_rankable",
          compliance_class: "unofficial_subscale",
        },
      ],
    });
    render(<QueryHeatmap summary={noncompliantSummary} />);
    const marker = screen.getByTestId("heatmap-compliance-marker-platform-noncompliant");
    expect(marker.textContent).toBe("*");
    expect(marker.getAttribute("role")).toBe("img");
    expect(marker.getAttribute("tabindex")).toBe("0");
    expect(marker.getAttribute("title")).toContain("does not meet the requirements for ranking");
    expect(marker.getAttribute("aria-label")).toContain("does not meet the requirements for ranking");
    expect(screen.getAllByRole("img", { name: "This result does not meet the requirements for ranking." })).toHaveLength(2);
    expect(screen.queryByText("(subscale)")).toBeNull();
  });

  it("labels a ranked subscale row", () => {
    const subscaleSummary = makeSummary({
      platforms: [
        {
          ...makeSummary().platforms[0]!,
          result_id: "platform-subscale",
          compliance_class: "unofficial_subscale",
        },
      ],
    });
    render(<QueryHeatmap summary={subscaleSummary} />);

    const marker = screen.getByTestId("heatmap-subscale-marker-platform-subscale");
    expect(marker.textContent).toBe("subscale");
    expect(marker.getAttribute("title")).toBe("Subscale · Unofficial");
    expect(screen.queryByTestId("heatmap-compliance-marker-platform-subscale")).toBeNull();
  });

  it("links the platform name to its receipt and shows validation status without a disclosure", () => {
    render(<QueryHeatmap summary={makeSummary()} />);
    const receiptLinks = screen.getAllByRole("link", { name: /Open receipt for/ }) as HTMLAnchorElement[];

    expect(receiptLinks[0]?.getAttribute("href")).toBe("/results/r/r1#run-receipt");
    expect(receiptLinks[1]?.getAttribute("href")).toBe("/results/r/r2#run-receipt");
    expect(screen.getAllByText("Exact").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Loose").length).toBeGreaterThan(0);
  });

  it("uses the short ID when platform, version, and date still do not identify a row", () => {
    const base = makeSummary().platforms[0]!;
    render(
      <QueryHeatmap
        summary={makeSummary({
          platforms: [
            { ...base, result_id: "result-one", short_id: "aaaaaaaa", platform_version: "1.0", trust_label: "maintainer-run" },
            { ...base, result_id: "result-two", short_id: "bbbbbbbb", platform_version: "1.0", trust_label: "maintainer-run" },
          ],
        })}
      />,
    );

    expect(screen.getAllByTestId("visible-run-qualifier").map((node) => node.textContent)).toEqual(["aaaaaaaa", "bbbbbbbb"]);
  });

  it("surfaces a not_run validation badge in the labels column without expanding anything", () => {
    const summary = makeSummary({
      platforms: [
        {
          ...makeSummary().platforms[0]!,
          result_id: "platform-not-run",
          validation_status: "not_run",
        },
        makeSummary().platforms[1]!,
      ],
    });
    render(<QueryHeatmap summary={summary} />);

    const flag = screen.getByTestId("heatmap-labels-platform-not-run");
    const badge = within(flag).getByText("No validation");
    expect(badge.closest("details")).toBeNull();
    expect(badge.getAttribute("data-role")).toBe("validation");

    const row = flag.closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getAllByText("No validation")).toHaveLength(1);
  });

  it("states a clean validation status once, in the labels column", () => {
    const summary = makeSummary({
      platforms: [
        {
          ...makeSummary().platforms[0]!,
          result_id: "platform-passed",
          validation_status: "passed",
        },
      ],
    });
    const { container } = render(<QueryHeatmap summary={summary} />);
    const labels = screen.getByTestId("heatmap-labels-platform-passed");
    expect(within(labels).getByText("Passed")).toBeTruthy();
    const row = container.querySelector('tbody tr[data-testid="platform-passed"]')!;
    expect(within(row as HTMLElement).getAllByText("Passed")).toHaveLength(1);
  });

  it("clicking a query header sorts rows by that query", () => {
    const { container } = render(<QueryHeatmap summary={makeSummary()} />);
    const rowOrder = () =>
      Array.from(container.querySelectorAll("tbody tr[data-testid]")).map(
        (row) => row.getAttribute("data-testid") ?? "",
      );

    expect(rowOrder()).toEqual(["r1", "r2"]);
    fireEvent.click(screen.getByRole("button", { name: /^Q1/ }));
    expect(rowOrder()).toEqual(["r1", "r2"]);
    fireEvent.click(screen.getByRole("button", { name: /^Q1/ }));
    expect(rowOrder()).toEqual(["r2", "r1"]);
  });

  it("heatmap cells keep visible values and color variables for multi-platform summaries", () => {
    const { container } = render(<QueryHeatmap summary={makeSummary()} />);
    const heatCells = container.querySelectorAll(".heatmap-cell");
    expect(heatCells.length).toBeGreaterThan(0);
    for (const cell of Array.from(heatCells)) {
      expect((cell as HTMLElement).style.getPropertyValue("--cell-hue")).toBeTruthy();
      expect((cell as HTMLElement).style.getPropertyValue("--cell-lightness")).toBeTruthy();
      expect(cell.textContent).not.toBe("");
    }
  });

  it("exposes exact timing and relative-to-fastest accessible names", () => {
    const { container } = render(<QueryHeatmap summary={makeSummary()} />);
    const fastest = container.querySelector<HTMLElement>('[data-cell="0-0"]');
    const slowest = container.querySelector<HTMLElement>('[data-cell="1-0"]');

    expect(fastest?.textContent).toBe("10 ms");
    expect(fastest?.getAttribute("aria-label")).toBe("10 ms, fastest in column");
    expect(slowest?.textContent).toBe("100 ms");
    expect(slowest?.getAttribute("aria-label")).toBe("100 ms, 10× slower than fastest in column");
  });

  it("shows the metric/heatmap legend, excludes exact zeroes, and formats positive sub-millisecond timings", () => {
    const summary = makeSummary({
      platforms: [
        {
          ...makeSummary().platforms[0]!,
          timings: { Q1: 0, Q2: 20 },
        },
        {
          ...makeSummary().platforms[1]!,
          timings: { Q1: 0.4, Q2: 200 },
        },
      ],
    });
    const { container } = render(<QueryHeatmap summary={summary} />);

    const legend = screen.getByTestId("query-heatmap-legend");
    expect(legend.textContent).toContain("Lower is better");
    expect(legend.textContent).toContain("Power score");
    expect(legend.textContent).toContain("higher is better");
    expect(legend.textContent).toContain("<1 ms");
    expect(legend.textContent).toContain("exact zero timings are");

    const zero = container.querySelector<HTMLElement>('[data-cell="0-0"]');
    const subMs = container.querySelector<HTMLElement>('[data-cell="1-0"]');
    expect(zero?.textContent).toBe("Excluded");
    expect(zero?.getAttribute("aria-label")).toContain("0 ms, excluded");
    expect(subMs?.textContent).toBe("<1 ms");
    expect(subMs?.getAttribute("aria-label")).toBe("<1 ms, fastest in column");
  });

  it("preserves per-cell exclusion reasons for excluded non-zero timings", () => {
    const summary = makeSummary();
    summary.platforms[0]!.timings.Q1 = 12;
    summary.platforms[0]!.timing_eligibility.Q1 = {
      is_valid_display_timing: false,
      timing_exclusion_reason: "non_positive_timing",
    };

    const { container } = render(<QueryHeatmap summary={summary} />);

    const excluded = container.querySelector<HTMLElement>('[data-cell="0-0"]');
    expect(excluded?.textContent).toBe("Excluded");
    expect(excluded?.getAttribute("aria-label")).toBe("12 ms, excluded: Timing is not positive.");
    expect(excluded?.getAttribute("title")).toBe("Timing is not positive.");
  });

  it("activates reduced-color class when high contrast is requested", () => {
    const { container } = render(<QueryHeatmap summary={makeSummary()} highContrast />);
    expect(container.firstElementChild?.className).toContain("heatmap-reduced-color");
  });

  it("renders an explicit horizontal scroll affordance with stable matrix cell widths", () => {
    const { container } = render(<QueryHeatmap summary={makeSummary()} />);

    const scrollContainer = screen.getByTestId("query-heatmap-scroll-container");
    const pageStickyHeader = screen.getByTestId("query-heatmap-page-sticky-header");
    expect(scrollContainer.className).toContain("overflow-x-auto");
    scrollContainer.scrollLeft = 96;
    fireEvent.scroll(scrollContainer);
    expect(pageStickyHeader.scrollLeft).toBe(96);
    expect(screen.getByTestId("query-heatmap-scroll-hint").textContent).toContain("query columns");
    expect(screen.getByRole("grid").className).toContain("min-w-max");

    const queryHeaders = Array.from(matrixThead().querySelectorAll("th"));
    expect(queryHeaders.some((header) => header.className.includes("min-w-[7rem]"))).toBe(true);
    const queryCells = Array.from(container.querySelectorAll("tbody td[data-cell]"));
    expect(queryCells.every((cell) => cell.className.includes("min-w-[6rem]"))).toBe(true);
  });

  it("renders compact mobile cards grouped by platform with aggregate metric and query outliers", () => {
    render(<QueryHeatmap summary={makeSummary()} />);

    const cards = screen.getByTestId("query-heatmap-mobile-cards");
    expect(cards.querySelectorAll('[data-testid^="query-heatmap-mobile-card-"]')).toHaveLength(2);

    const sqliteCard = screen.getByTestId("query-heatmap-mobile-card-r2");
    expect(sqliteCard.textContent).toContain("SQLite");
    expect(sqliteCard.textContent).toContain("Power score");
    expect(sqliteCard.textContent).toContain("800");
    expect(sqliteCard.textContent).toContain("Query outliers");
    expect(sqliteCard.textContent).toContain("Q2");
    expect(sqliteCard.textContent).toContain("200 ms");
    expect(sqliteCard.textContent).toContain("10× slower than fastest");
  });

  it("gives the mobile card a linked platform name, a date chip, and a unit-only geomean label", () => {
    render(<QueryHeatmap summary={makeSummary()} />);
    const card = screen.getByTestId("query-heatmap-mobile-card-r1");

    const receiptLink = within(card).getByRole("link", { name: /Open receipt for/ });
    expect(receiptLink.textContent).toBe("DuckDB");
    expect(receiptLink.getAttribute("href")).toBe("/results/r/r1#run-receipt");

    expect(card.textContent).not.toContain("Receipt →");
    expect(card.textContent).not.toMatch(/\d Receipt/);

    expect(within(card).getByTestId("run-date-chip").textContent).toBe("2026-04-01");
    const heading = card.querySelector("h2")!;
    expect(heading.textContent).not.toContain("2026-04-01");

    expect(card.textContent).toContain("Geomean 10 ms");
    expect(card.textContent).not.toContain("Geomean latency");
  });

  it("uses the same comparison selection ids from compact mobile cards", () => {
    let selected: Set<string> | null = null;
    render(
      <QueryHeatmap
        summary={makeSummary()}
        selectedIds={new Set()}
        onSelectionChange={(ids) => {
          selected = ids;
        }}
      />,
    );

    const duckCard = screen.getByTestId("query-heatmap-mobile-card-r1");
    const checkbox = duckCard.querySelector("input[type='checkbox']") as HTMLInputElement;
    fireEvent.click(checkbox);

    expect([...(selected ?? [])]).toEqual(["r1"]);
  });

  it("does not allow comparison selection for rows excluded by the read-model contract", () => {
    let selected: Set<string> | null = null;
    const summary = makeSummary({
      platforms: [
        {
          ...makeSummary().platforms[0]!,
          comparison_exclusion_reason: "insufficient_valid_timings",
        },
      ],
    });
    render(
      <QueryHeatmap
        summary={summary}
        selectedIds={new Set()}
        onSelectionChange={(ids) => {
          selected = ids;
        }}
      />,
    );

    const checkbox = screen.getAllByRole("checkbox")[0] as HTMLInputElement;
    expect(checkbox.disabled).toBe(true);
    fireEvent.click(checkbox);
    expect(selected).toBeNull();
  });

  it("refuses an eleventh comparison selection independently of row eligibility", () => {
    let selected: Set<string> | null = null;
    const baseRow = makeSummary().platforms[0]!;
    const summary = makeSummary({
      platforms: Array.from({ length: 11 }, (_, index) => ({
        ...baseRow,
        result_id: `r${index + 1}`,
        short_id: "",
        platform_id: `platform-${index + 1}`,
        platform: `Platform ${index + 1}`,
      })),
    });
    render(
      <QueryHeatmap
        summary={summary}
        selectedIds={new Set(Array.from({ length: 10 }, (_, index) => `r${index + 1}`))}
        selectionLimitReasonId="selection-limit-reason"
        onSelectionChange={(ids) => {
          selected = ids;
        }}
      />,
    );

    const grid = screen.getByRole("grid");
    const eleventh = within(grid).getAllByRole("checkbox")[10] as HTMLInputElement;
    expect(eleventh.disabled).toBe(true);
    expect(eleventh.getAttribute("aria-describedby")).toBe("selection-limit-reason");
    const eleventhMobileCard = screen.getByTestId("query-heatmap-mobile-card-r11");
    const eleventhMobile = within(eleventhMobileCard).getByRole("checkbox") as HTMLInputElement;
    expect(eleventhMobile.disabled).toBe(true);
    expect(eleventhMobile.getAttribute("aria-describedby")).toBe("selection-limit-reason");
    eleventh.disabled = false;
    fireEvent.click(eleventh);

    expect(selected).toBeNull();
  });

  it("null timing cell renders no-run copy with a missing-run aria-label", () => {
    const summary = makeSummary({
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
          missing_query_count: 1,
          comparison_exclusion_reason: "insufficient_valid_timings",
          power_score: 3000,
          display_geomean_ms: 10,
          compliance_class: null,
          sample_geomean_ms: 12,
          cost_usd: null,
          percentile_stats: null,
          phase_durations: null,
          timings: { Q1: 10, Q2: null },
        },
      ],
    });
    const { container } = render(<QueryHeatmap summary={summary} />);
    const cells = container.querySelectorAll('[aria-label="No published query run for DuckDB Q2"]');
    expect(cells.length).toBeGreaterThan(0);
    expect(cells[0]?.textContent).toBe("No run");
    expect(cells[0]?.getAttribute("title")).toBe("No published run for this query/platform cell.");
  });

  it("single-platform matrix has no heatmap-cell class", () => {
    const summary = makeSummary({
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
          percentile_stats: null,
          phase_durations: null,
          timings: { Q1: 10, Q2: 20 },
        },
      ],
    });
    const { container } = render(<QueryHeatmap summary={summary} />);
    expect(container.querySelectorAll(".heatmap-cell").length).toBe(0);
  });

  it("empty platforms list shows empty-state", () => {
    const summary = makeSummary({ platforms: [] });
    render(<QueryHeatmap summary={summary} />);
    expect(screen.getByText(/No results are available/)).toBeTruthy();
  });

  it("ineligible platforms sort below eligible ones", () => {
    const summary = makeSummary({
      platforms: [
        {
          result_id: "r_ineligible",
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
          power_score: 9999,
          display_geomean_ms: 1,
          compliance_class: null,
          sample_geomean_ms: 1,
          cost_usd: null,
          percentile_stats: null,
          phase_durations: null,
          timings: { Q1: 1, Q2: 1 },
        },
        {
          result_id: "r_eligible",
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
          power_score: 100,
          display_geomean_ms: 100,
          compliance_class: null,
          sample_geomean_ms: 100,
          cost_usd: null,
          percentile_stats: null,
          phase_durations: null,
          timings: { Q1: 100, Q2: 200 },
        },
      ],
    });
    const { container } = render(<QueryHeatmap summary={summary} />);
    const rows = container.querySelectorAll("tbody tr");
    expect(rows[0]?.textContent).toContain("DuckDB");
    expect(rows[1]?.textContent).toContain("SQLite");
  });

  it("ArrowRight moves focus to next column (tabIndex moves from [0,0] to [0,1])", () => {
    const { container } = render(<QueryHeatmap summary={makeSummary()} />);
    const firstCell = container.querySelector('[data-cell="0-0"]') as HTMLElement;
    expect(firstCell).not.toBeNull();
    expect(firstCell.tabIndex).toBe(0);
    fireEvent.keyDown(firstCell, { key: "ArrowRight" });
    const nextCell = container.querySelector('[data-cell="0-1"]') as HTMLElement;
    expect(nextCell.tabIndex).toBe(0);
    expect(firstCell.tabIndex).toBe(-1);
  });

  it("ArrowDown moves focus to the row below (tabIndex moves from [0,0] to [1,0])", () => {
    const { container } = render(<QueryHeatmap summary={makeSummary()} />);
    const firstCell = container.querySelector('[data-cell="0-0"]') as HTMLElement;
    fireEvent.keyDown(firstCell, { key: "ArrowDown" });
    const below = container.querySelector('[data-cell="1-0"]') as HTMLElement;
    expect(below.tabIndex).toBe(0);
    expect(firstCell.tabIndex).toBe(-1);
  });

  it("ArrowLeft at column 0 keeps focus at [0,0] (no crash, no negative index)", () => {
    const { container } = render(<QueryHeatmap summary={makeSummary()} />);
    const firstCell = container.querySelector('[data-cell="0-0"]') as HTMLElement;
    fireEvent.keyDown(firstCell, { key: "ArrowLeft" });
    expect(firstCell.tabIndex).toBe(0);
  });

  it("ArrowUp at row 0 keeps focus at [0,0] (no crash, no negative index)", () => {
    const { container } = render(<QueryHeatmap summary={makeSummary()} />);
    const firstCell = container.querySelector('[data-cell="0-0"]') as HTMLElement;
    fireEvent.keyDown(firstCell, { key: "ArrowUp" });
    expect(firstCell.tabIndex).toBe(0);
  });

  it("has no axe violations when selection is enabled (checkbox column header path)", async () => {
    const { container } = render(
      <QueryHeatmap summary={makeSummary()} selectedIds={new Set()} onSelectionChange={() => {}} />,
    );
    await expectNoAxeViolations(container);
  });
});

it("allows metadata after the identity column to scroll past the frozen region", () => {
  const { container } = render(<QueryHeatmap summary={makeSummary()} />);
  const table = container.querySelector("table")!;
  const headers = [...table.querySelectorAll("thead th")];
  expect(headers.filter((cell) => (cell as HTMLElement).style.position === "static").length).toBeGreaterThanOrEqual(3);
});
