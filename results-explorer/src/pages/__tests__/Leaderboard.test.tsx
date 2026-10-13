import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/db", () => ({
  queryRows: vi.fn(),
  resetDuckDbInitializationFailures: vi.fn(),
}));

import { queryRows } from "@/db";
import { clearDuckdbQueryCachesForTests } from "@/lib/duckdbQueries";
import {
  EXPLORER_PERFORMANCE_MARKS,
  EXPLORER_PERFORMANCE_MEASURES,
  clearExplorerPerformanceEntriesForTests,
} from "@/lib/performanceMarks";
import { toggleFacetValue } from "@/lib/facetMatching";
import { PickingStateProvider } from "@/lib/pickingState";
import { LEADERBOARD_SHELL_GEOMETRY_CLASSES, Leaderboard } from "@/pages/Leaderboard";
import { COHORT_ROWS, META_LEADERBOARD_ROWS, RESULT_ROWS } from "./fixtures/corpus";

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function expectDocumentOrder(first: Element, second: Element) {
  expect(Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
}

function makeHavocResult(index: number, overrides: Record<string, unknown> = {}) {
  return {
    ...RESULT_ROWS[2]!,
    result_id: `havoc-run-${index}`,
    benchmark: "tpchavoc",
    scale_factor: 10,
    platform: `Havoc platform ${index}`,
    platform_id: `havoc-platform-${index}`,
    run_date: `2026-10-${String(index).padStart(2, "0")}T12:00:00Z`,
    test_type: "power",
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: "known_defective_data",
    is_ranking_eligible: false,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  clearDuckdbQueryCachesForTests();
  clearExplorerPerformanceEntriesForTests();
  window.history.replaceState(null, "", "/results/");
  vi.mocked(queryRows).mockImplementation(async (sql: string) => {
    const s = String(sql).replace(/\s+/g, " ").trim();
    if (s.includes("FROM bench.results")) return RESULT_ROWS;
    if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
      return META_LEADERBOARD_ROWS;
    }
    if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
    return [];
  });
});

describe("Leaderboard", () => {
  it("keeps the leaderboard shell stable until a no-leaderboard snapshot finishes loading", async () => {
    const resultRows = deferred<typeof RESULT_ROWS>();
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return resultRows.promise;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) return [];
      if (s.includes("FROM bench.cohort_metadata")) return [];
      return [];
    });

    render(<Leaderboard />);

    await waitFor(() => {
      expect(performance.getEntriesByName(EXPLORER_PERFORMANCE_MARKS.HOME_LEADERBOARD_DATA_READY, "mark"))
        .toHaveLength(1);
    });
    expect(screen.getByRole("region", { name: "Leaderboard ranking selector" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Cross-benchmark leaderboard loading" })).toHaveAttribute(
      "aria-busy",
      "true",
    );

    resultRows.resolve(RESULT_ROWS);
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Cross-benchmark leaderboard loading" })).toBeNull(),
    );
    expect(document.title).toBe("Compare benchmark results · BenchBox");
    expect(screen.queryByText("Initializing static DuckDB snapshot...")).toBeNull();
    expect(screen.queryByText("Cross-benchmark rankings")).toBeNull();
  });


  it("keeps the loading state while an empty result snapshot conflicts with leaderboard metadata", async () => {
    const metaRows = deferred<typeof META_LEADERBOARD_ROWS>();
    const cohortRows = deferred<typeof COHORT_ROWS>();
    const retryRows = deferred<typeof RESULT_ROWS>();
    let resultCalls = 0;

    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) {
        resultCalls += 1;
        return resultCalls === 1 ? [] : retryRows.promise;
      }
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return metaRows.promise;
      }
      if (s.includes("FROM bench.cohort_metadata")) {
        return cohortRows.promise;
      }
      return [];
    });

    render(<Leaderboard />);

    await waitFor(() => expect(resultCalls).toBe(1));
    await waitFor(() => expect(screen.getByText("Initializing static DuckDB snapshot...")).toBeTruthy());
    expect(screen.getByRole("heading", { level: 1, name: "Compare benchmark results" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Cross-benchmark leaderboard loading" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.queryByText("Recent results")).toBeNull();
    expect(screen.queryByText("No leaderboard cells match the current filters.")).toBeNull();

    metaRows.resolve(META_LEADERBOARD_ROWS);
    cohortRows.resolve(COHORT_ROWS);

    await waitFor(() => expect(resultCalls).toBe(2));
    expect(screen.getByText("Initializing static DuckDB snapshot...")).toBeTruthy();
    expect(screen.queryByText("Recent results")).toBeNull();
    expect(screen.queryByText("No leaderboard cells match the current filters.")).toBeNull();

    retryRows.resolve(RESULT_ROWS);

    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());
    expect(screen.queryByText("Initializing static DuckDB snapshot...")).toBeNull();
    expect(screen.queryByText("No leaderboard cells match the current filters.")).toBeNull();
  });

  it("renders a recoverable error when the empty result snapshot persists after retry", async () => {
    let resultCalls = 0;

    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) {
        resultCalls += 1;
        return [];
      }
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) {
        return COHORT_ROWS;
      }
      return [];
    });

    render(<Leaderboard />);

    await waitFor(() => expect(resultCalls).toBe(2));
    expect(screen.getByText("Could not load all results")).toBeTruthy();
    expect(screen.getByText(/The rankings loaded, but the run list did not/)).toBeTruthy();
    expect(screen.queryByText("Initializing static DuckDB snapshot...")).toBeNull();
    expect(screen.queryByText("Recent results")).toBeNull();
  });

  it("retries both results and meta-leaderboard data when onRetry is clicked", async () => {
    let resultCalls = 0;
    let metaCalls = 0;
    let shouldFail = true;

    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) {
        resultCalls += 1;
        if (shouldFail) throw new Error("Network error loading results");
        return RESULT_ROWS;
      }
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        metaCalls += 1;
        if (shouldFail) throw new Error("Network error loading metadata");
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) {
        return COHORT_ROWS;
      }
      return [];
    });

    render(<Leaderboard />);

    await waitFor(() => expect(screen.getByText("Could not load results")).toBeTruthy());
    expect(resultCalls).toBe(1);
    expect(metaCalls).toBe(1);

    shouldFail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());
    expect(resultCalls).toBe(2);
    expect(metaCalls).toBe(2);
  });

  it("renders the leaderboard-first product identity and dense cohort controls", async () => {
    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    expect(screen.getByRole("heading", { level: 1, name: "Compare benchmark results" })).toBeTruthy();
    expect(
      screen.getByText("See how published platform runs compare across BenchBox rankings. Open any result to inspect its evidence."),
    ).toBeTruthy();

    const selector = screen.getByRole("region", { name: "Leaderboard ranking selector" });
    expect(within(selector).getByLabelText("Benchmark")).toBeTruthy();
    expect(within(selector).getByLabelText("Scale")).toBeTruthy();
    expect(within(selector).getByLabelText("Phase")).toBeTruthy();
    expect(screen.getByText("Ranking scope")).toBeTruthy();
    expect(screen.getByText("Ranked results only")).toBeTruthy();

    fireEvent.click(screen.getByText("Advanced filters"));
    expect(within(selector).getByRole("button", { name: "All tuning labels" })).toBeTruthy();
    expect(within(selector).getByRole("button", { name: /not recorded/i })).toBeTruthy();
  });




  it("carries the provenance legend at the bottom of the data surface, since Leaderboard is /results/compare's empty-selection state", async () => {
    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const dataSurface = screen.getByTestId("home-data-surface");
    const legend = within(dataSurface).getByTestId("provenance-legend");
    expect(legend).toBeTruthy();
    expect(dataSurface.lastElementChild).toBe(legend);
  });

  it("keeps tuning metadata visible while hiding a non-discriminating tuning filter", async () => {
    const unlabelledRows = RESULT_ROWS.map((row) => ({ ...row, tuning_mode: null }));
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return unlabelledRows;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const selector = screen.getByRole("region", { name: "Leaderboard ranking selector" });
    fireEvent.click(screen.getByText("Advanced filters"));
    expect(within(selector).getByTestId("tuning-filter-unavailable")).toHaveTextContent(
      "Tuning filter unavailable",
    );
    expect(within(selector).queryByRole("button", { name: "All tuning labels" })).toBeNull();
    expect(within(selector).getByTestId("tuning-filter-unavailable")).toHaveTextContent("4 public results have");
  });

  it("states leaderboard cohort scope separately from public browse scope", async () => {
    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    expect(screen.getByText("Showing 2 of 2 ranked-scope platforms across 2 leaderboard rankings")).toBeTruthy();
    expect(screen.getAllByText(/Ranked results cover 2 of 3 published benchmarks/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/SSB/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/1 published platform is not represented in the rankings/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/1 of 4 public results is not recorded for tuning/).length).toBeGreaterThan(0);
  });

  it("offers comparable unranked TPC-Havoc SF10 runs with an explicit defect warning", async () => {
    const havocRows = [makeHavocResult(1), makeHavocResult(2), makeHavocResult(3)];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return havocRows;
      if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    window.history.replaceState(null, "", "/results/?benchmark=tpchavoc&sf=10&phase=power");

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByTestId("leaderboard-compare-picker")).toBeTruthy());

    const benchmarkFilter = screen.getByLabelText("Benchmark") as HTMLSelectElement;
    expect([...benchmarkFilter.options].map((option) => option.value)).toContain("tpchavoc");
    const picker = screen.getByTestId("leaderboard-compare-picker");
    expect(within(picker).getByText(/No ranked comparable runs are available/)).toBeTruthy();
    fireEvent.click(within(picker).getByRole("radio", { name: "All published comparable runs" }));

    await waitFor(() => expect(within(picker).getAllByRole("checkbox", { name: /Select .* run .* for comparison/ })).toHaveLength(3));
    expect(within(picker).getAllByText("Known defective data; available for comparison, excluded from rankings.")).toHaveLength(3);
    const compareLink = within(picker).getByRole("link", { name: "Compare All Runs" }) as HTMLAnchorElement;
    expect(compareLink.getAttribute("href")).toBe("/results/compare?ids=havoc-run-1,havoc-run-2,havoc-run-3");
    expect(within(picker).getByText("All 3 published comparable runs in this cohort can be compared together.")).toBeTruthy();
  });

  it("keeps a compact compare entry point near the filters and scopes guidance to ranked runs", async () => {
    const havocRows = [makeHavocResult(1), makeHavocResult(2)];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return havocRows;
      if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    window.history.replaceState(null, "", "/results/?benchmark=tpchavoc&sf=10&phase=power");

    render(<Leaderboard />);
    const picker = await screen.findByTestId("leaderboard-compare-picker");

    const jump = screen.getByTestId("home-compare-jump");
    expect(jump.querySelector("a")?.getAttribute("href")).toBe("#leaderboard-compare-picker");
    expect(within(picker).getByText(/No ranked comparable runs are available/)).toBeTruthy();

    fireEvent.click(within(picker).getByTestId("leaderboard-include-unranked"));
    await waitFor(() =>
      expect(within(picker).getByRole("link", { name: "Compare All Runs" }).getAttribute("href")).toBe(
        "/results/compare?ids=havoc-run-1,havoc-run-2",
      ),
    );
    expect(within(picker).queryByTestId("leaderboard-include-unranked")).toBeNull();
  });

  it("names the ranked scope in Compare All Runs guidance", async () => {
    const rankedRows = [
      makeHavocResult(1, { ranking_exclusion_reason: null, is_ranking_eligible: true }),
      makeHavocResult(2, { ranking_exclusion_reason: null, is_ranking_eligible: true }),
    ];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return rankedRows;
      if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    window.history.replaceState(null, "", "/results/?benchmark=tpchavoc&sf=10&phase=power");

    render(<Leaderboard />);
    const picker = await screen.findByTestId("leaderboard-compare-picker");

    expect(within(picker).getByRole("link", { name: "Compare All Runs" })).toBeTruthy();
    expect(within(picker).getByText("All 2 ranked comparable runs in this cohort can be compared together.")).toBeTruthy();
  });

  it("ignores picks from another view so a 2-10 run cohort still offers Compare All Runs", async () => {
    const havocRows = [makeHavocResult(1), makeHavocResult(2)];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return havocRows;
      if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    window.history.replaceState(null, "", "/results/?benchmark=tpchavoc&sf=10&phase=power&compare_scope=comparable");

    render(
      <PickingStateProvider
        initialRestoreIds={["other-run-1", "other-run-2"]}
        recoveryDependencies={{
          resolveId: async (id) => id,
          findExistingIds: async (ids) => new Set(ids),
          loadResult: async (id) => ({ result_id: id }),
        }}
      >
        <Leaderboard />
      </PickingStateProvider>,
    );

    const picker = await screen.findByTestId("leaderboard-compare-picker");
    const compareLink = await within(picker).findByRole("link", { name: "Compare All Runs" }) as HTMLAnchorElement;
    expect(compareLink.getAttribute("href")).toBe("/results/compare?ids=havoc-run-1,havoc-run-2");
    for (const checkbox of within(picker).getAllByRole("checkbox", { name: /Select .* run .* for comparison/ })) {
      expect(checkbox).toBeEnabled();
    }
    expect(within(picker).queryByTestId("leaderboard-compare-cta-all")).toBeNull();
  });

  it("keeps an explicit subset comparison available alongside Compare All Runs", async () => {
    const havocRows = [makeHavocResult(1), makeHavocResult(2), makeHavocResult(3)];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return havocRows;
      if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    window.history.replaceState(null, "", "/results/?benchmark=tpchavoc&sf=10&phase=power&compare_scope=comparable");

    render(<Leaderboard />);

    const picker = await screen.findByTestId("leaderboard-compare-picker");
    await within(picker).findByRole("link", { name: "Compare All Runs" });
    const boxes = within(picker).getAllByRole("checkbox", { name: /Select .* run .* for comparison/ });
    fireEvent.click(boxes[0]!);
    fireEvent.click(boxes[1]!);
    const primary = await within(picker).findByTestId("leaderboard-compare-cta") as HTMLAnchorElement;
    expect(primary.textContent).toBe("Compare 2 selected");
    expect(primary.className).toContain("btn-primary");
    expect(primary.getAttribute("href")).toBe("/results/compare?ids=havoc-run-1,havoc-run-2");
    const all = within(picker).getByTestId("leaderboard-compare-cta-all") as HTMLAnchorElement;
    expect(all.textContent).toBe("Compare All Runs");
    expect(all.className).toContain("btn-secondary");
    expect(all.getAttribute("href")).toBe("/results/compare?ids=havoc-run-1,havoc-run-2,havoc-run-3");
  });

  it("carries the page mode and basis into the compare links", async () => {
    const havocRows = [makeHavocResult(1), makeHavocResult(2), makeHavocResult(3)];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return havocRows;
      if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    window.history.replaceState(
      null,
      "",
      "/results/?benchmark=tpchavoc&sf=10&phase=power&compare_scope=comparable&mode=times&basis=all_warm",
    );

    render(<Leaderboard />);

    const picker = await screen.findByTestId("leaderboard-compare-picker");
    const boxes = within(picker).getAllByRole("checkbox", { name: /Select .* run .* for comparison/ });
    fireEvent.click(boxes[0]!);
    fireEvent.click(boxes[1]!);

    const selected = within(picker).getByTestId("leaderboard-compare-cta") as HTMLAnchorElement;
    expect(selected.getAttribute("href")).toBe(
      "/results/compare?ids=havoc-run-1,havoc-run-2&mode=times&basis=all_warm",
    );
    const all = within(picker).getByTestId("leaderboard-compare-cta-all") as HTMLAnchorElement;
    expect(all.getAttribute("href")).toBe(
      "/results/compare?ids=havoc-run-1,havoc-run-2,havoc-run-3&mode=times&basis=all_warm",
    );
  });

  it("shows the cohort compare entry point as a secondary button linking to the picker", async () => {
    const havocRows = [makeHavocResult(1), makeHavocResult(2)];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return havocRows;
      if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    window.history.replaceState(null, "", "/results/?benchmark=tpchavoc&sf=10&phase=power");

    render(<Leaderboard />);
    await screen.findByTestId("leaderboard-compare-picker");

    const jump = screen.getByTestId("home-compare-jump").querySelector("a") as HTMLAnchorElement;
    expect(jump.className).toContain("btn-secondary");
    expect(jump.classList.contains("underline")).toBe(false);
  });

  it("chooses one cohort from a multi-benchmark filter before comparing", async () => {
    const comparisonRows = [
      makeHavocResult(1, { result_id: "tpch-run-1", benchmark: "tpch", ranking_exclusion_reason: null }),
      makeHavocResult(2, { result_id: "tpch-run-2", benchmark: "tpch", ranking_exclusion_reason: null }),
      makeHavocResult(3, { result_id: "tpcds-run-1", benchmark: "tpcds", ranking_exclusion_reason: null }),
      makeHavocResult(4, { result_id: "tpcds-run-2", benchmark: "tpcds", ranking_exclusion_reason: null }),
    ];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return comparisonRows;
      if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    window.history.replaceState(null, "", "/results/?benchmark=tpch,tpcds&sf=10&phase=power");

    render(<Leaderboard />);

    const picker = await screen.findByTestId("leaderboard-compare-picker");
    const cohortSelect = within(picker).getByRole("combobox", { name: "Comparison cohort" }) as HTMLSelectElement;
    expect(cohortSelect.options).toHaveLength(3);
    expect(within(picker).getByRole("button", { name: "Select 2–10 runs to compare" })).toBeDisabled();
    const tpchOption = [...cohortSelect.options].find((option) => option.textContent?.startsWith("TPC-H"));
    expect(tpchOption).toBeTruthy();
    fireEvent.change(cohortSelect, { target: { value: tpchOption!.value } });

    const compareLink = await within(picker).findByRole("link", { name: "Compare All Runs" }) as HTMLAnchorElement;
    expect(compareLink.getAttribute("href")).toBe("/results/compare?ids=tpch-run-1,tpch-run-2");
  });

  it("requires an explicit selection for cohorts with more than ten comparable runs", async () => {
    const havocRows = Array.from({ length: 12 }, (_, index) => makeHavocResult(index + 1));
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return havocRows;
      if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    window.history.replaceState(null, "", "/results/?benchmark=tpchavoc&sf=10&phase=power");

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByTestId("leaderboard-compare-picker")).toBeTruthy());
    const picker = screen.getByTestId("leaderboard-compare-picker");
    fireEvent.click(within(picker).getByRole("radio", { name: "All published comparable runs" }));

    const checkboxes = await waitFor(() => {
      const rows = within(picker).getAllByRole("checkbox", { name: /Select .* run .* for comparison/ }) as HTMLInputElement[];
      expect(rows).toHaveLength(12);
      return rows;
    });
    expect(within(picker).getByTestId("leaderboard-compare-cta")).toBeDisabled();
    expect(within(picker).getByText("Select 2 to 10 of the 12 published comparable runs in this cohort.")).toBeTruthy();

    fireEvent.click(checkboxes[0]!);
    fireEvent.click(checkboxes[1]!);
    const compareLink = within(picker).getByRole("link", { name: "Compare 2 selected" }) as HTMLAnchorElement;
    expect(compareLink.getAttribute("href")).toBe("/results/compare?ids=havoc-run-1,havoc-run-2");

    for (const checkbox of checkboxes.slice(2, 10)) fireEvent.click(checkbox);
    expect(within(picker).getByTestId("leaderboard-selection-limit")).toBeTruthy();
    expect(checkboxes[10]).toBeDisabled();
    expect(within(picker).getByRole("link", { name: "Compare 10 selected" })).toBeTruthy();
  });

  it("ignores global picks outside an oversized cohort and compares only cohort selections", async () => {
    const havocRows = Array.from({ length: 12 }, (_, index) => makeHavocResult(index + 1));
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return havocRows;
      if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    window.history.replaceState(null, "", "/results/?benchmark=tpchavoc&sf=10&phase=power&compare_scope=comparable");

    render(
      <PickingStateProvider
        initialRestoreIds={["other-run-1", "other-run-2"]}
        recoveryDependencies={{
          resolveId: async (id) => id,
          findExistingIds: async (ids) => new Set(ids),
          loadResult: async (id) => ({ result_id: id }),
        }}
      >
        <Leaderboard />
      </PickingStateProvider>,
    );

    const picker = await screen.findByTestId("leaderboard-compare-picker");
    const checkboxes = await waitFor(() => {
      const rows = within(picker).getAllByRole("checkbox", { name: /Select .* run .* for comparison/ }) as HTMLInputElement[];
      expect(rows).toHaveLength(12);
      return rows;
    });
    expect(within(picker).getByTestId("leaderboard-compare-cta")).toBeDisabled();
    expect(within(picker).getByText(/selected runs are from another results view and will be ignored/)).toBeTruthy();

    fireEvent.click(checkboxes[0]!);
    fireEvent.click(checkboxes[1]!);

    const compareLink = within(picker).getByRole("link", { name: "Compare 2 selected" }) as HTMLAnchorElement;
    expect(compareLink.getAttribute("href")).toBe("/results/compare?ids=havoc-run-1,havoc-run-2");
  });

  it("does not link to Compare for zero or one comparable run", async () => {
    for (const [rows, count] of [
      [[makeHavocResult(1)], 1],
      [[makeHavocResult(1, { comparison_exclusion_reason: "insufficient_valid_timings" })], 0],
    ] as const) {
      vi.mocked(queryRows).mockImplementation(async (sql: string) => {
        const s = String(sql).replace(/\s+/g, " ").trim();
        if (s.includes("FROM bench.results")) return [...rows];
        if (s.startsWith("SELECT platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
          return META_LEADERBOARD_ROWS;
        }
        if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
        return [];
      });
      window.history.replaceState(null, "", "/results/?benchmark=tpchavoc&sf=10&phase=power&compare_scope=comparable");
      clearDuckdbQueryCachesForTests();

      const { unmount } = render(<Leaderboard />);
      await waitFor(() => expect(screen.getByTestId("leaderboard-compare-picker")).toBeTruthy());
      const picker = screen.getByTestId("leaderboard-compare-picker");
      await waitFor(() => expect(within(picker).queryAllByRole("checkbox", { name: /Select .* run .* for comparison/ })).toHaveLength(count));
      const cta = within(picker).getByTestId("leaderboard-compare-cta");
      expect(cta.tagName).toBe("BUTTON");
      expect(cta).toBeDisabled();
      expect(within(picker).queryByRole("link", { name: /Compare/ })).toBeNull();
      expect(within(picker).queryAllByRole("checkbox", { name: /Select .* run .* for comparison/ })).toHaveLength(count);
      unmount();
    }
  });

  it("does not describe mixed ranked and unranked leaderboard evidence totals as ranked", async () => {
    const resultRows = [
      ...RESULT_ROWS,
      {
        ...RESULT_ROWS[0],
        result_id: "r5",
        platform: "Postgres",
        platform_id: "postgres",
        power_score: null,
        has_display_timing: false,
        valid_query_count: 0,
        display_exclusion_reason: "missing_timing_context",
        comparison_exclusion_reason: "missing_timing_context",
        ranking_exclusion_reason: "missing_timing_context",
        is_ranking_eligible: false,
      },
    ];
    const cohortRows = [
      ...COHORT_ROWS,
      {
        ...COHORT_ROWS[0],
        platform_id: "postgres",
        platform: "Postgres",
        result_id: "r5",
        rank: null,
        metric_value: null,
        speedup_vs_best: null,
        has_display_timing: false,
        valid_query_count: 0,
        display_exclusion_reason: "missing_timing_context",
        comparison_exclusion_reason: "missing_timing_context",
        ranking_exclusion_reason: "missing_timing_context",
      },
    ];

    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return resultRows;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return cohortRows;
      return [];
    });

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const selector = screen.getByRole("region", { name: "Leaderboard ranking selector" });
    expect(selector.textContent).toContain(
      "The current table includes 3 of 3 platforms found in the selected rankings: 2 ranked and 1 unranked.",
    );
    expect(selector.textContent).toContain(
      "Across all rankings, 2 of 3 platforms are ranked and 1 is unranked.",
    );
    expect(selector.textContent).not.toContain("3 ranked platform IDs");
  });

  it("keeps the home filter band on the dark surface and data sections on the light surface", async () => {
    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const hero = screen.getByTestId("home-hero-filter-band");
    const selector = within(hero).getByRole("region", { name: "Leaderboard ranking selector" });
    const dataSurface = screen.getByTestId("home-data-surface");

    expect(hero.className).toContain("surface-hero");
    expect(hero).toHaveAttribute("data-surface", "hero");
    expect(selector.className).toContain("bg-[var(--bb-bg-panel)]");
    expect(dataSurface.className).toContain("surface-app");
    expect(dataSurface).toHaveAttribute("data-surface", "app");
    expect(dataSurface.contains(selector)).toBe(false);
    expect(screen.getByTestId("leaderboard-scope-summary-mobile")).toBeTruthy();
  });

  it("routes every loaded and skeleton shell region through the shared geometry source", async () => {
    const resultRows = deferred<typeof RESULT_ROWS>();
    const metaRows = deferred<typeof META_LEADERBOARD_ROWS>();
    const cohortRows = deferred<typeof COHORT_ROWS>();
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return resultRows.promise;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return metaRows.promise;
      }
      if (s.includes("FROM bench.cohort_metadata")) return cohortRows.promise;
      return [];
    });

    const expectSharedGeometry = () => {
      const scopeDetails =
        screen.queryByTestId("leaderboard-scope-summary-mobile") ??
        screen.getByTestId("home-loading-scope-details-reserve");
      const advancedDetails =
        screen.queryByTestId("leaderboard-advanced-filters") ??
        screen.getByTestId("home-loading-advanced-details-reserve");
      const pairs: Array<[HTMLElement, string]> = [
        [screen.getByTestId("home-hero-filter-band"), LEADERBOARD_SHELL_GEOMETRY_CLASSES.heroSurface],
        [screen.getByTestId("home-hero-wrapper"), LEADERBOARD_SHELL_GEOMETRY_CLASSES.heroWrapper],
        [screen.getByTestId("home-hero-intro"), LEADERBOARD_SHELL_GEOMETRY_CLASSES.heroIntro],
        [
          screen.getByRole("region", { name: "Leaderboard ranking selector" }),
          LEADERBOARD_SHELL_GEOMETRY_CLASSES.rankingSelector,
        ],
        [screen.getByTestId("home-ranking-selector-grid"), LEADERBOARD_SHELL_GEOMETRY_CLASSES.rankingGrid],
        [scopeDetails, LEADERBOARD_SHELL_GEOMETRY_CLASSES.scopeDetails],
        [advancedDetails, LEADERBOARD_SHELL_GEOMETRY_CLASSES.advancedDetails],
        [screen.getByTestId("home-data-surface"), LEADERBOARD_SHELL_GEOMETRY_CLASSES.dataSurface],
      ];

      for (const [element, expectedClasses] of pairs) {
        expect(element.className).toBe(expectedClasses);
      }
    };

    render(<Leaderboard />);

    expectSharedGeometry();
    expect(screen.queryByRole("region", { name: "Active leaderboard filters" })).toBeNull();
    expect(screen.queryByText("What counts as a ranked result?")).toBeNull();

    resultRows.resolve(RESULT_ROWS);
    metaRows.resolve(META_LEADERBOARD_ROWS);
    cohortRows.resolve(COHORT_ROWS);

    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());
    expectSharedGeometry();
    expect(screen.queryByRole("region", { name: "Active leaderboard filters" })).toBeNull();
    expect(screen.getByText("What counts as a ranked result?")).toBeTruthy();
  });

  it("keeps the ranking selector between the headline and the matrix", async () => {
    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const headline = screen.getByRole("heading", { level: 1, name: "Compare benchmark results" });
    const leaderboard = screen.getByRole("region", { name: "Cross-benchmark rankings" });
    const selector = screen.getByRole("region", { name: "Leaderboard ranking selector" });

    expectDocumentOrder(headline, selector);
    expectDocumentOrder(selector, leaderboard);
  });

  it("records first leaderboard data and render performance entries", async () => {
    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    await waitFor(() => {
      expect(performance.getEntriesByName(EXPLORER_PERFORMANCE_MARKS.HOME_LEADERBOARD_DATA_READY, "mark"))
        .toHaveLength(1);
      expect(performance.getEntriesByName(EXPLORER_PERFORMANCE_MARKS.LEADERBOARD_RENDERED, "mark"))
        .toHaveLength(1);
      expect(performance.getEntriesByName(EXPLORER_PERFORMANCE_MEASURES.HOME_LEADERBOARD_DATA, "measure"))
        .toHaveLength(1);
      expect(performance.getEntriesByName(EXPLORER_PERFORMANCE_MEASURES.LEADERBOARD_RENDER_AFTER_DATA, "measure"))
        .toHaveLength(1);
    });
  });

  it("restores and updates the leaderboard mode through the URL", async () => {
    window.history.replaceState(null, "", "/results/");

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    expect(screen.getByRole("radio", { name: "Relative to best" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText("0.5×")).toBeTruthy();
    expect(new URL(window.location.href).searchParams.get("mode")).toBeNull();

    fireEvent.click(screen.getByRole("radio", { name: "Times" }));
    await waitFor(() => expect(new URL(window.location.href).searchParams.get("mode")).toBe("times"));

    fireEvent.click(screen.getByRole("radio", { name: "Relative to best" }));
    await waitFor(() => expect(new URL(window.location.href).searchParams.get("mode")).toBeNull());

    fireEvent.click(screen.getByRole("radio", { name: "Ranks" }));

    await waitFor(() => expect(new URL(window.location.href).searchParams.get("mode")).toBe("ranks"));
  });

  it("renders the comparison picker below the leaderboard data surface", async () => {
    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const selector = screen.getByRole("region", { name: "Leaderboard ranking selector" });
    expect(within(selector).queryByTestId("leaderboard-compare-picker")).toBeNull();
    const dataSurface = screen.getByTestId("home-data-surface");
    const picker = screen.getByTestId("leaderboard-compare-picker");
    expect(dataSurface.compareDocumentPosition(picker) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(picker).getByRole("radio", { name: "Ranked results only" })).toBeTruthy();
    expect(within(picker).getByRole("radio", { name: "All published comparable runs" })).toBeTruthy();
    expect(within(picker).getByRole("combobox", { name: "Comparison cohort" })).toBeTruthy();
    expect(within(picker).getByRole("button", { name: "Select 2–10 runs to compare" })).toBeDisabled();
  });


  it("treats a benchmark selector change as isolate-not-exclude from the default all state", async () => {
    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const grid = screen.getByRole("grid", { name: "Cross-benchmark leaderboard" });
    expect(within(grid).getByRole("link", { name: /^ClickBench SF0.1/ })).toBeTruthy();
    expect(within(grid).getByRole("link", { name: /^TPC-H SF1/ })).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Benchmark"), { target: { value: "clickbench" } });

    await waitFor(() => {
      expect(within(grid).getByRole("link", { name: /^ClickBench SF0.1/ })).toBeTruthy();
      expect(within(grid).queryByRole("link", { name: /^TPC-H SF1/ })).toBeNull();
    });
  });

  it("shows active facet combination and targeted reset actions when filters remove all coverage", async () => {
    window.history.replaceState(null, "", "/results/?sf=999&platform=duckdb");

    render(<Leaderboard />);

    const emptyState = await screen.findByRole("region", {
      name: "No leaderboard cells match the current filters",
    });
    expect(within(emptyState).getByText("Scale factor")).toBeTruthy();
    expect(within(emptyState).getByText("SF 999")).toBeTruthy();
    expect(within(emptyState).getByText("Platform")).toBeTruthy();
    expect(within(emptyState).getByText("DuckDB")).toBeTruthy();
    expect(within(emptyState).getByRole("button", { name: "Clear scale factor" })).toBeTruthy();
    expect(within(emptyState).getByRole("button", { name: "Clear platform" })).toBeTruthy();
    expect(within(emptyState).getByRole("button", { name: "Reset all" })).toBeTruthy();

    fireEvent.click(within(emptyState).getByRole("button", { name: "Clear scale factor" }));

    await waitFor(() => {
      expect(new URLSearchParams(window.location.search).get("sf")).toBeNull();
      expect(screen.queryByRole("region", { name: "No leaderboard cells match the current filters" })).toBeNull();
    });
    expect(new URLSearchParams(window.location.search).get("platform")).toBe("duckdb");
    expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy();
  });

  it("restores canonical URL facets and applies them to the leaderboard result query", async () => {
    window.history.replaceState(
      null,
      "",
      "/results/?benchmark=clickbench&sf=0.1&phase=power&platform=DuckDB&deployment=cloud&cost_status=normalized",
    );

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const grid = screen.getByRole("grid", { name: "Cross-benchmark leaderboard" });
    expect(within(grid).getByText("DuckDB")).toBeTruthy();
    expect(within(grid).queryByText("SQLite")).toBeNull();
    expect(within(grid).queryByRole("link", { name: /^TPC-H SF1/ })).toBeNull();

    const resultCall = vi
      .mocked(queryRows)
      .mock.calls.find(([sql]) => String(sql).replace(/\s+/g, " ").trim().includes("FROM bench.results WHERE"));
    expect(String(resultCall?.[0])).toContain("CASE WHEN benchmark = 'star_schema' THEN 'ssb'");
    expect(String(resultCall?.[0])).toContain("scale_factor IN (?)");
    expect(String(resultCall?.[0])).toContain("THEN 'power' ELSE trim(lower(test_type)) END IN (?)");
    expect(String(resultCall?.[0])).toContain("(platform IN (?) OR platform_id IN (?))");
    expect(String(resultCall?.[0])).toContain("deployment_class IN (?)");
    expect(String(resultCall?.[0])).toContain("cost_status IN (?)");
    expect(resultCall?.[1]).toEqual(["clickbench", 0.1, "power", "DuckDB", "DuckDB", "cloud", "normalized"]);

    const cohortLink = within(grid).getByRole("link", { name: /^ClickBench SF0.1/ }) as HTMLAnchorElement;
    expect(cohortLink.getAttribute("href")).toContain("sf=0.1");
    expect(cohortLink.getAttribute("href")).toContain("phase=power");
    expect(cohortLink.getAttribute("href")).toContain("platform=DuckDB");
    expect(cohortLink.getAttribute("href")).toContain("deployment=cloud");
    expect(cohortLink.getAttribute("href")).toContain("cost_status=normalized");
  });

  it("restores URL facet aliases while keeping coverage labels visible", async () => {
    window.history.replaceState(
      null,
      "",
      "/results/?bm=clickbench&scale_factor=0.1&trust_tier=maintainer-run",
    );

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    await waitFor(() => {
      const params = new URL(window.location.href).searchParams;
      expect(params.get("benchmark")).toBe("clickbench");
      expect(params.get("sf")).toBe("0.1");
      expect(params.get("trust")).toBe("maintainer-run");
      expect(params.get("bm")).toBeNull();
      expect(params.get("scale_factor")).toBeNull();
      expect(params.get("trust_tier")).toBeNull();
    });

    const grid = screen.getByRole("grid", { name: "Cross-benchmark leaderboard" });
    expect(within(grid).getByRole("columnheader", { name: "Avg rank over covered rankings" })).toBeTruthy();
    expect(within(grid).getByText("1/1 rankings")).toBeTruthy();
    expect(within(grid).getByText("over 1/1")).toBeTruthy();
  });

  it("surfaces leaderboard receipt links with trust and validation metadata", async () => {
    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const grid = screen.getByRole("grid", { name: "Cross-benchmark leaderboard" });
    const receiptLink = within(grid).getByRole("link", { name: "1×; Native: 10 ms" }) as HTMLAnchorElement;

    expect(receiptLink.getAttribute("href")).toBe("/results/r/r1#run-receipt");
    expect(within(grid).getAllByText("Maintainer").length).toBeGreaterThan(0);
    expect(within(grid).getAllByText("Community").length).toBeGreaterThan(0);
    expect(within(grid).getAllByText("Exact").length).toBeGreaterThan(0);
    expect(within(grid).getAllByRole("gridcell", { name: /Run age:/ }).length).toBeGreaterThan(0);
  });


  it("filters the matrix by trust tier and preserves tuning in cohort links", async () => {
    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const grid = screen.getByRole("grid", { name: "Cross-benchmark leaderboard" });
    expect(within(grid).getByText("DuckDB")).toBeTruthy();
    expect(within(grid).getByText("SQLite")).toBeTruthy();

    fireEvent.click(screen.getByText("Advanced filters"));
    fireEvent.click(screen.getByRole("button", { name: "auto" }));
    fireEvent.click(screen.getByRole("button", { name: "community-submission" }));

    await waitFor(() => {
      expect(within(grid).queryByText("DuckDB")).toBeNull();
    });
    expect(within(grid).getByText("SQLite")).toBeTruthy();

    const cohortLink = within(grid).getByRole("link", { name: /^ClickBench SF0.1/ }) as HTMLAnchorElement;
    expect(cohortLink.getAttribute("href")).toContain("/results/clickbench/");
    expect(cohortLink.getAttribute("href")).toContain("sf=0.1");
    expect(cohortLink.getAttribute("href")).toContain("phase=power");
    expect(cohortLink.getAttribute("href")).toContain("tuning=auto");
  });

  it("lists throughput cohorts per stream count and links each to its own stream count", async () => {
    const throughputResult = (resultId: string, platform: string, streams: number) => ({
      ...RESULT_ROWS[0]!,
      result_id: resultId,
      benchmark: "tpch",
      scale_factor: 1,
      platform,
      platform_id: platform.toLowerCase(),
      test_type: "throughput",
      power_score: null,
      throughput_at_size: 1000 + streams,
      stream_count: streams,
    });
    const throughputCohort = (resultId: string, platform: string, streams: number, rank: number) => ({
      ...COHORT_ROWS[2]!,
      cohort_key: `tpch-sf1-throughput-${streams}streams`,
      phase: "throughput",
      stream_count: streams,
      cohort_label: `TPC-H SF1 Throughput (${streams} streams)`,
      cohort_href: `/results/tpch/?sf=1&phase=throughput&streams=${streams}`,
      platform_count: 2,
      cohort_ranked_count: 2,
      primary_metric: "throughput_at_size",
      platform_id: platform.toLowerCase(),
      platform,
      result_id: resultId,
      rank,
      metric_value: 1000 + streams,
    });
    const results = [
      throughputResult("t3-spark", "Spark", 3),
      throughputResult("t3-duck", "DuckDB", 3),
      throughputResult("t2-spark", "Spark", 2),
      throughputResult("t2-duck", "DuckDB", 2),
    ];
    const cohorts = [
      throughputCohort("t3-spark", "Spark", 3, 1),
      throughputCohort("t3-duck", "DuckDB", 3, 2),
      throughputCohort("t2-spark", "Spark", 2, 1),
      throughputCohort("t2-duck", "DuckDB", 2, 2),
    ];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return results;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return cohorts;
      return [];
    });

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const grid = screen.getByRole("grid", { name: "Cross-benchmark leaderboard" });
    const threeStreams = within(grid).getByRole("link", { name: /^TPC-H SF1 Throughput \(3 streams\)/ });
    const twoStreams = within(grid).getByRole("link", { name: /^TPC-H SF1 Throughput \(2 streams\)/ });
    expect(new URL((threeStreams as HTMLAnchorElement).href).searchParams.get("streams")).toBe("3");
    expect(new URL((twoStreams as HTMLAnchorElement).href).searchParams.get("streams")).toBe("2");
    expect(new URL((threeStreams as HTMLAnchorElement).href).searchParams.get("phase")).toBe("throughput");
  });

  it("narrows throughput cohorts to the streams URL value", async () => {
    window.history.replaceState(null, "", "/results/?streams=3");
    const results = [3, 2].flatMap((streams) => [
      {
        ...RESULT_ROWS[0]!,
        result_id: `t${streams}-spark`,
        benchmark: "tpch",
        scale_factor: 1,
        platform: "Spark",
        platform_id: "spark",
        test_type: "throughput",
        stream_count: streams,
      },
      {
        ...RESULT_ROWS[0]!,
        result_id: `t${streams}-duck`,
        benchmark: "tpch",
        scale_factor: 1,
        platform: "DuckDB",
        platform_id: "duckdb",
        test_type: "throughput",
        stream_count: streams,
      },
    ]);
    const cohorts = results.map((result, index) => ({
      ...COHORT_ROWS[2]!,
      cohort_key: `tpch-sf1-throughput-${result.stream_count}streams`,
      phase: "throughput",
      stream_count: result.stream_count,
      cohort_label: `TPC-H SF1 Throughput (${result.stream_count} streams)`,
      platform_count: 2,
      cohort_ranked_count: 2,
      primary_metric: "throughput_at_size",
      platform_id: result.platform_id,
      platform: result.platform,
      result_id: result.result_id,
      rank: (index % 2) + 1,
    }));
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return results;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return cohorts;
      return [];
    });

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const grid = screen.getByRole("grid", { name: "Cross-benchmark leaderboard" });
    expect(within(grid).getByRole("link", { name: /^TPC-H SF1 Throughput \(3 streams\)/ })).toBeTruthy();
    expect(within(grid).queryByRole("link", { name: /^TPC-H SF1 Throughput \(2 streams\)/ })).toBeNull();
  });

  it("keeps raw tuning option values when display labels are trimmed", async () => {
    const resultRows = RESULT_ROWS.map((row) =>
      row.result_id === "r2" ? { ...row, tuning_mode: " auto " } : row,
    );
    const cohortRows = COHORT_ROWS.map((row) =>
      row.result_id === "r2" ? { ...row, tuning_mode: " auto " } : row,
    );
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return resultRows;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return cohortRows;
      return [];
    });

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const grid = screen.getByRole("grid", { name: "Cross-benchmark leaderboard" });
    fireEvent.click(screen.getByText("Advanced filters"));
    fireEvent.click(screen.getByRole("button", { name: "auto" }));

    await waitFor(() => {
      expect(within(grid).queryByText("DuckDB")).toBeNull();
    });
    expect(within(grid).getByText("SQLite")).toBeTruthy();

    const cohortLink = within(grid).getByRole("link", { name: /^ClickBench SF0.1/ }) as HTMLAnchorElement;
    expect(cohortLink.getAttribute("href")).toContain("tuning=+auto+");
  });
});

describe("toggleFacetValue (w13)", () => {
  it("removes a value when it is already selected, leaving siblings intact", () => {
    expect(toggleFacetValue(["tpch", "clickbench"], "tpch")).toEqual(["clickbench"]);
    expect(toggleFacetValue(["tpch", "clickbench"], "clickbench")).toEqual(["tpch"]);
  });

  it("adds a value when it is not already selected", () => {
    expect(toggleFacetValue(["tpch"], "clickbench")).toEqual(["tpch", "clickbench"]);
  });

  it("returns an empty list when toggling the only selected value", () => {
    expect(toggleFacetValue(["tpch"], "tpch")).toEqual([]);
  });

  it("does not mutate the input list", () => {
    const input = ["tpch", "clickbench"];
    toggleFacetValue(input, "tpch");
    expect(input).toEqual(["tpch", "clickbench"]);
  });

  it("renders Platform version selector in the ranking selector grid when results have engine versions", async () => {
    const versionedRows = RESULT_ROWS.map((r, i) => ({
      ...r,
      platform_version: i === 0 ? "1.4.0" : "1.3.2",
    }));
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return versionedRows;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());
    const grid = screen.getByTestId("home-ranking-selector-grid");
    expect(within(grid).getByText("Platform version")).toBeTruthy();
    expect(within(grid).getByText("All versions")).toBeTruthy();
  });

  it("keeps sibling engine versions selectable and carries the facet into drill-down links", async () => {
    window.history.replaceState(null, "", "/results/?version=1.4.0");
    const versionedRows = RESULT_ROWS.map((row, index) => ({
      ...row,
      platform_version: index === 0 ? "1.4.0" : "1.3.2",
    }));
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) {
        return s.includes("platform_version IN")
          ? versionedRows.filter((row) => row.platform_version === "1.4.0")
          : versionedRows;
      }
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const versionControl = screen.getByRole("combobox", { name: "Platform version" });
    expect(within(versionControl).getByRole("option", { name: "1.3.2" })).toBeTruthy();

    const grid = screen.getByRole("grid", { name: "Cross-benchmark leaderboard" });
    const cohortLink = within(grid).getByRole("link", { name: /^ClickBench SF0.1/ }) as HTMLAnchorElement;
    expect(cohortLink.getAttribute("href")).toContain("version=1.4.0");
    const platformLink = within(grid).getByRole("link", { name: "DuckDB" }) as HTMLAnchorElement;
    expect(platformLink.getAttribute("href")).toContain("version=1.4.0");
  });

  it("shows architecture constraints and carries them through leaderboard drill-down links", async () => {
    window.history.replaceState(null, "", "/results/?arch=arm64&cpu_family=apple_silicon");
    const hardwareRows = RESULT_ROWS.map((row) => ({
      ...row,
      arch: "arm64",
      cpu_family: "apple_silicon",
    }));
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("JOIN bench.result_detail_metrics")) return hardwareRows;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });

    render(<Leaderboard />);
    await waitFor(() => expect(screen.getByText("Cross-benchmark rankings")).toBeTruthy());

    const activeFilters = screen.getByLabelText("Active filter chips");
    expect(within(activeFilters).getByText("Architecture: arm64")).toBeTruthy();
    expect(within(activeFilters).getByText("CPU family: Apple Silicon")).toBeTruthy();

    const grid = screen.getByRole("grid", { name: "Cross-benchmark leaderboard" });
    const cohortHref = (within(grid).getByRole("link", { name: /^ClickBench SF0.1/ }) as HTMLAnchorElement).href;
    const platformHref = (within(grid).getByRole("link", { name: "DuckDB" }) as HTMLAnchorElement).href;
    for (const href of [cohortHref, platformHref]) {
      expect(href).toContain("arch=arm64");
      expect(href).toContain("cpu_family=apple_silicon");
    }
  });
});
