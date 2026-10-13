import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/db", () => ({
  queryRows: vi.fn(),
  resetDuckDbInitializationFailures: vi.fn(),
}));

import { queryRows, resetDuckDbInitializationFailures } from "@/db";
import { clearDuckdbQueryCachesForTests } from "@/lib/duckdbQueries";
import { Home } from "@/pages/Home";
import { COHORT_ROWS, META_LEADERBOARD_ROWS, RESULT_ROWS } from "./fixtures/corpus";

beforeEach(() => {
  vi.clearAllMocks();
  clearDuckdbQueryCachesForTests();
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

function browseLabels(heading: string): string[] {
  const section = screen.getByRole("heading", { name: heading }).closest("section");
  return within(section as HTMLElement)
    .getAllByRole("link")
    .map((link) => link.textContent ?? "");
}

describe("Overview", () => {
  it("leads with the overview identity and sends ranking work to the compare page", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeTruthy();
    expect(document.title).toBe("Overview · BenchBox");
    expect(screen.getByTestId("overview-compare-cta")).toHaveAttribute("href", "/results/compare");
  });

  it("carries no ranking filters, which now live on the compare page", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    expect(screen.queryByRole("region", { name: "Leaderboard ranking selector" })).toBeNull();
    expect(screen.queryByText("Cross-benchmark rankings")).toBeNull();
    expect(screen.queryByTestId("home-hero-filter-band")).toBeNull();
  });

  it("distinguishes supported benchmark coverage from published public corpus counts", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    const summary = screen.getByRole("region", { name: "Corpus summary" });
    expect(within(summary).getByText("supported benchmarks")).toBeTruthy();
    expect(within(summary).getByText("3 with public results")).toBeTruthy();
    expect(within(summary).getByText("published runs")).toBeTruthy();
    expect(within(summary).getByText("platforms with public results")).toBeTruthy();
    expect(within(summary).getByText("rankings")).toBeTruthy();
    expect(within(summary).queryByText(/^Benchmarks$/)).toBeNull();
  });

  it("renders singular corpus-summary labels when the count is one", async () => {
    const singleCohort = COHORT_ROWS.filter((row) => row.cohort_key === "tpch-sf1-power");
    const singleResultRow = RESULT_ROWS.filter((row) => row.result_id === "r3");
    const singleMetaLeaderboard = [{ platform_id: "duckdb", platform: "DuckDB", avg_rank: 1, n_cohorts: 1 }];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return singleResultRow;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return singleMetaLeaderboard;
      }
      if (s.includes("FROM bench.cohort_metadata")) return singleCohort;
      return [];
    });

    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    const summary = screen.getByRole("region", { name: "Corpus summary" });
    expect(within(summary).getByText("published run")).toBeTruthy();
    expect(within(summary).queryByText("published runs")).toBeNull();
    expect(within(summary).getByText("platform with public results")).toBeTruthy();
    expect(within(summary).queryByText("platforms with public results")).toBeNull();
    expect(within(summary).getByText("ranking")).toBeTruthy();
    expect(within(summary).queryByText(/^rankings$/)).toBeNull();
  });

  it("reads a corpus with no rankings as plural rather than as one ranking", async () => {
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return RESULT_ROWS;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return [];
      return [];
    });

    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    const summary = screen.getByRole("region", { name: "Corpus summary" });
    expect(within(summary).getByText("rankings")).toBeTruthy();
    expect(within(summary).queryByText(/^ranking$/)).toBeNull();
  });

  it("orders the browse lists by display name rather than by platform or benchmark id", async () => {
    const extraBenchmarks = ["coffeeshop", "datavault", "flightdata"].map((benchmark) => ({
      ...RESULT_ROWS[0]!,
      result_id: `${benchmark}-1`,
      benchmark,
    }));
    const dorisRow = { ...RESULT_ROWS[0]!, result_id: "doris-1", platform_id: "zz-doris", platform: "Apache Doris" };
    const rows = [...RESULT_ROWS, ...extraBenchmarks, dorisRow];
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return rows;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });

    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    expect(browseLabels("Browse public benchmark results")).toEqual([
      "ClickBench",
      "CoffeeShop",
      "Flight Data",
      "SSB",
      "TPC-H",
      "TPC-H Data Vault",
    ]);
    expect(browseLabels("Browse public platform results")).toEqual(["Apache Doris", "DuckDB", "Postgres", "SQLite"]);
  });

  it("explains a ranking as one benchmark at one scale and phase, with the ranked platform count", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    const summary = screen.getByRole("region", { name: "Corpus summary" });
    expect(within(summary).getByText(/Each ranking is one benchmark at one scale and phase\./)).toBeTruthy();
    expect(within(summary).getByText(/2 platforms appear in at least one ranking/)).toBeTruthy();
    expect(screen.getByText("3 platforms with public results, whether or not a ranking includes it.")).toBeTruthy();
  });

  it("hides the scale and date columns from the recent results header on phones", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    const headerClass = (name: string) =>
      screen.getAllByRole("columnheader").find((th) => th.textContent === name)?.className ?? "";
    expect(headerClass("Scale")).toContain("hidden sm:table-cell");
    expect(headerClass("Date")).toContain("hidden sm:table-cell");
    expect(headerClass("Geomean")).not.toContain("hidden");
  });

  it("shows normalized cost in recent results only when normalized cost metadata is present", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());
    expect(screen.getByText("Normalized cost")).toBeTruthy();
    expect(screen.getByText("$1.10")).toBeTruthy();

    const withoutCost = RESULT_ROWS.map((row) => ({
      ...row,
      normalized_cost_usd: null,
      cost_status: null,
      cost_scope: null,
      cost_model_version: null,
    }));
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return withoutCost;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    clearDuckdbQueryCachesForTests();
    render(<Home />);
    await waitFor(() => expect(screen.getAllByText("Recent results").length).toBe(2));
    expect(screen.queryAllByText("Normalized cost")).toHaveLength(1);
  });

  it("shows power score in recent results only when a power score is present", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());
    expect(screen.getByText("Power score")).toBeTruthy();

    const withoutPower = RESULT_ROWS.map((row) => ({ ...row, power_score: null }));
    vi.mocked(queryRows).mockImplementation(async (sql: string) => {
      const s = String(sql).replace(/\s+/g, " ").trim();
      if (s.includes("FROM bench.results")) return withoutPower;
      if (s.startsWith("SELECT variant_id, variant_label, platform_id, platform, avg_rank, n_cohorts FROM bench.meta_leaderboard")) {
        return META_LEADERBOARD_ROWS;
      }
      if (s.includes("FROM bench.cohort_metadata")) return COHORT_ROWS;
      return [];
    });
    clearDuckdbQueryCachesForTests();
    render(<Home />);
    await waitFor(() => expect(screen.getAllByText("Recent results").length).toBe(2));
    expect(screen.queryAllByText("Power score")).toHaveLength(1);
  });

  it("does not pin the recent-results action column, so it can't paint over trailing cells", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    const scrollContainer = screen.getByTestId("recent-results-scroll-container");
    const headerRow = scrollContainer.querySelector("thead tr")!;
    expect(headerRow.className).not.toMatch(/\bsticky\b/);
    for (const cell of Array.from(headerRow.children)) {
      expect(cell.className).not.toMatch(/\bsticky\b/);
    }
    const bodyRow = scrollContainer.querySelector("tbody tr")!;
    for (const cell of Array.from(bodyRow.children)) {
      expect(cell.className).not.toMatch(/\bsticky\b/);
    }
  });

  it("keeps the contribution workflow and both browse sections", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    expect(screen.getByText("Run, compare, and submit")).toBeTruthy();
    const workflow = screen.getByRole("navigation", { name: "Result contribution workflow" });
    const runLink = within(workflow).getByRole("link", { name: "Run a benchmark" });
    expect(runLink).toHaveAttribute("href", "/docs/usage/installation.html");
    expect(runLink).toHaveAttribute("data-native", "true");
    expect(within(workflow).getByRole("link", { name: "Compare your result" })).toHaveAttribute(
      "href",
      "/results/query",
    );
    const submitLink = within(workflow).getByRole("link", { name: "Submit a bundle" });
    expect(submitLink).toHaveAttribute("href", "/docs/contributing-results.html");
    expect(submitLink).toHaveAttribute("data-native", "true");

    expect(screen.getByRole("heading", { name: "Browse public benchmark results" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Browse public platform results" })).toBeTruthy();
  });

  it("no longer carries the provenance legend - it moved to the compare landing page", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Recent results")).toBeTruthy());

    expect(screen.queryByTestId("provenance-legend")).toBeNull();
    expect(screen.queryByText("What do these labels mean?")).toBeNull();
  });
});

it("retries ranking metadata along with results after an initialization failure", async () => {
  const healthy = vi.mocked(queryRows).getMockImplementation()!;
  vi.mocked(queryRows).mockRejectedValue(new Error("worker lost"));
  render(<Home />);
  await screen.findByRole("button", { name: "Retry" });
  vi.mocked(queryRows).mockImplementation(healthy);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("Recent results");
  await waitFor(() => expect(screen.queryByText("0 ranked platforms")).toBeNull());
  expect(resetDuckDbInitializationFailures).toHaveBeenCalledOnce();
  const metadataCalls = vi.mocked(queryRows).mock.calls.filter(([sql]) => String(sql).includes("FROM bench.meta_leaderboard"));
  expect(metadataCalls).toHaveLength(2);
});

it("does not report zero rankings when only ranking metadata fails", async () => {
  const healthy = vi.mocked(queryRows).getMockImplementation()!;
  vi.mocked(queryRows).mockImplementation(async (...args) => {
    if (String(args[0]).includes("FROM bench.meta_leaderboard")) throw new Error("worker lost");
    return healthy(...args);
  });
  render(<Home />);
  await screen.findByText("Recent results");
  expect(screen.getByText("Ranking data unavailable")).toBeTruthy();
  expect(screen.queryByText("0 ranked platforms")).toBeNull();
});
