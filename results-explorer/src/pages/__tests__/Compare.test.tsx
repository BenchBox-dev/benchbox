import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { route } from "preact-router";
import type { DetailResult } from "@/types";

vi.mock("preact-router", () => ({
  route: vi.fn(),
}));

vi.mock("@/lib/duckdbQueries", async () => {
  const actual = await vi.importActual<typeof import("@/lib/duckdbQueries")>("@/lib/duckdbQueries");
  return {
    ...actual,
    getDetailResult: vi.fn(),
    getExistingResultIds: vi.fn((ids: string[]) => Promise.resolve(new Set(ids))),
    resolveShortId: vi.fn((id: string) => Promise.resolve(id)),
    toShortIds: vi.fn((ids: string[]) => Promise.resolve(ids)),
    getPrimaryMetricForBenchmark: vi.fn().mockResolvedValue("power_score"),
    listResults: vi.fn().mockResolvedValue([]),
  };
});

import {
  getDetailResult,
  getPrimaryMetricForBenchmark,
  listResults,
  resolveShortId,
  toShortIds,
  type ResultRow,
} from "@/lib/duckdbQueries";
import { buildComparisonBoundary, Compare } from "@/pages/Compare";

describe("buildComparisonBoundary", () => {
  it("reports differing and unrecorded hardware fields together", () => {
    expect(
      buildComparisonBoundary([
        { label: "Architecture", status: "diff" },
        { label: "CPU model", status: "missing" },
      ]),
    ).toBe(
      "Hardware boundary: architecture differs; CPU model is not recorded for every run. This compares recorded runs, not platforms in isolation.",
    );
  });
});

function makeResult(overrides: Partial<DetailResult> = {}): DetailResult {
  return {
    result_id: "r1",
    benchmark: "tpch",
    scale_factor: 0.1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: null,
    run_date: "2026-04-01",
    total_duration_s: 60,
    geomean_ms: 10,
    display_geomean_ms: 10,
    power_score: 3000,
    has_display_timing: true,
    valid_query_count: 2,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    environment: {},
    queries: [],
    display_timings: [
      { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      { query_id: "Q2", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
    ],
    has_plans: false,
    has_tuning: false,
    bundle_download_url: "",
    trust_label: "maintainer-run",
    visibility: "public-curated",
    funding: "unspecified",
    platform_version: null,
    execution_mode: null,
    tuning_mode: null,
    tuning_hash: null,
    test_type: null,
    validation_status: null,
    compliance_class: null,
    cost_usd: null,
    ...overrides,
  };
}

const DUCKDB = makeResult({
  result_id: "r1",
  platform: "DuckDB",
  display_geomean_ms: 10,
  power_score: 3000,
});

const SQLITE = makeResult({
  result_id: "r2",
  platform: "SQLite",
  platform_id: "sqlite",
  display_geomean_ms: 100,
  power_score: 300,
  display_timings: [
    { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
    { query_id: "Q2", display_ms: 200, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
  ],
});

const POSTGRES = makeResult({
  result_id: "r3",
  platform: "PostgreSQL",
  platform_id: "postgresql",
  display_geomean_ms: 50,
  power_score: 1000,
  display_timings: [
    { query_id: "Q1", display_ms: 50, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
    { query_id: "Q2", display_ms: 80, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
  ],
});

function makeResultRow(overrides: Partial<ResultRow> = {}): ResultRow {
  return {
    result_id: "r1",
    benchmark: "tpch",
    scale_factor: 0.1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: null,
    run_date: "2026-04-17",
    power_score: 3000,
    total_duration_s: 60,
    geomean_ms: 10,
    display_geomean_ms: 10,
    query_count: 22,
    has_display_timing: true,
    valid_query_count: 22,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    trust_label: "maintainer-run",
    funding: "unspecified",
    visibility: "public-curated",
    platform_version: null,
    execution_mode: null,
    tuning_mode: null,
    tuning_hash: null,
    test_type: "measurement",
    validation_status: null,
    cost_usd: null,
    compliance_class: null,
    is_ranking_eligible: true,
    has_plans: false,
    plans_published: false,
    has_tuning: false,
    bundle_download_url: "",
    normalized_cost_usd: null,
    cost_model_version: null,
    cost_status: null,
    cost_scope: null,
    deployment_class: null,
    cloud_provider: null,
    cloud_region: null,
    instance_or_warehouse: null,
    storage_format: null,
    ...overrides,
  };
}

function setupUrl(ids: string[], extraParams: Record<string, string> = {}) {
  const params = new URLSearchParams({ ids: ids.join(","), ...extraParams });
  window.history.replaceState(null, "", `/results/compare?${params.toString()}`);
}

beforeEach(() => {
  vi.clearAllMocks();
  setupUrl(["r1", "r2"]);
  vi.mocked(resolveShortId).mockImplementation((id) => Promise.resolve(id));
  vi.mocked(getPrimaryMetricForBenchmark).mockResolvedValue("power_score");
  vi.mocked(getDetailResult).mockImplementation((id) =>
    id === "r1" ? Promise.resolve(DUCKDB) : Promise.resolve(SQLITE)
  );
});

describe("Compare", () => {
  it("shows the ranking table when a single run is pinned", async () => {
    setupUrl(["a556e716"]);
    vi.mocked(resolveShortId).mockResolvedValue("tpch-duckdb-sf0.01-20260403-7fe93365");
    vi.mocked(getDetailResult).mockResolvedValue(DUCKDB);
    vi.mocked(listResults).mockResolvedValue([
      makeResultRow({
        result_id: "tpch-duckdb-sf0.01-20260403-7fe93365",
        platform: "DuckDB",
        platform_id: "duckdb",
        run_date: "2026-04-03",
      }),
    ]);

    render(<Compare />);

    await waitFor(() => expect(screen.getByTestId("home-hero-filter-band")).toBeTruthy());
    expect(screen.getByRole("heading", { level: 1, name: "Compare benchmark results" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Find runs to compare with this run" }).getAttribute("href")).toBe(`/results/query?pick=${DUCKDB.result_id}`);
    expect(getDetailResult).toHaveBeenCalledWith("tpch-duckdb-sf0.01-20260403-7fe93365");
    expect(route).not.toHaveBeenCalledWith(expect.stringMatching(/^\/results\/r\//), true);
  });

  it("keeps a single unknown compare ID on the Compare error path", async () => {
    setupUrl(["does-not-exist"]);
    vi.mocked(getDetailResult).mockResolvedValue(null);

    render(<Compare />);

    await waitFor(() =>
      expect(screen.getByText(/No result found for: does-not-exist/)).toBeTruthy(),
    );
    expect(route).not.toHaveBeenCalled();
  });

  it("retains resolvable IDs when a shared compare URL contains a stale ID", async () => {
    setupUrl(["r1", "stale-result", "r2"]);
    vi.mocked(getDetailResult).mockImplementation((id) => {
      if (id === "r1") return Promise.resolve(DUCKDB);
      if (id === "r2") return Promise.resolve(SQLITE);
      return Promise.resolve(null);
    });

    render(<Compare />);

    await waitFor(() => expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy());
    const notice = screen.getByTestId("compare-url-notice");
    expect(notice).toHaveTextContent("Ignored unavailable result ID: “stale-result”.");
    expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy();
    expect(screen.queryByText(/Cannot compare/i)).toBeNull();
    await waitFor(() => {
      expect(new URL(window.location.href).searchParams.get("ids")).toBe("r1,r2");
    });
  });

  it("deduplicates IDs and discloses the ten-result comparison limit", async () => {
    const ids = Array.from({ length: 12 }, (_, index) => `r${index + 1}`);
    setupUrl(["r1", ...ids]);
    const byId: Record<string, DetailResult> = Object.fromEntries(ids.map((id, index) => [
      id,
      makeResult({ result_id: id, platform: `Platform ${index + 1}`, platform_id: `platform-${index + 1}` }),
    ]));
    vi.mocked(getDetailResult).mockImplementation((id) => Promise.resolve(byId[id] ?? null));

    render(<Compare />);

    await waitFor(() => expect(document.title).toBe("Compare (10) · BenchBox Results"));
    const notice = screen.getByTestId("compare-url-notice");
    expect(notice).toHaveTextContent("Ignored duplicate result ID: “r1”.");
    expect(notice).toHaveTextContent("Ignored 2 additional result IDs (“r11”, “r12”); comparisons are limited to 10 unique results.");
    expect(screen.getAllByText("Platform 10").length).toBeGreaterThan(0);
    expect(screen.queryByText("Platform 11")).toBeNull();
    await waitFor(() => {
      expect(new URL(window.location.href).searchParams.get("ids")).toBe(ids.slice(0, 10).join(","));
    });
  });

  it("deduplicates short-ID aliases without changing retained order", async () => {
    setupUrl(["r1", "alias-r1", "r2"]);
    vi.mocked(resolveShortId).mockImplementation((id) => Promise.resolve(id === "alias-r1" ? "r1" : id));
    vi.mocked(getDetailResult).mockImplementation((id) => {
      if (id === "r1") return Promise.resolve(DUCKDB);
      if (id === "r2") return Promise.resolve(SQLITE);
      return Promise.resolve(null);
    });

    render(<Compare />);

    await waitFor(() => expect(document.title).toBe("Compare (2) · BenchBox Results"));
    expect(screen.getByTestId("compare-url-notice")).toHaveTextContent(
      "Ignored duplicate result ID after alias resolution: “alias-r1”.",
    );
    await waitFor(() => {
      expect(new URL(window.location.href).searchParams.get("ids")).toBe("r1,r2");
    });
    expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy();
  });

  it("resolves aliases before applying the comparison limit", async () => {
    setupUrl(["r1", "alias-r1", "r2", "r3", "r4"]);
    vi.mocked(resolveShortId).mockImplementation((id) =>
      Promise.resolve(id === "alias-r1" ? "r1" : id),
    );
    const r4 = makeResult({ result_id: "r4", platform: "Trino", platform_id: "trino" });
    const byId: Record<string, DetailResult> = { r1: DUCKDB, r2: SQLITE, r3: POSTGRES, r4 };
    vi.mocked(getDetailResult).mockImplementation((id) => Promise.resolve(byId[id] ?? null));

    render(<Compare />);

    await waitFor(() => expect(document.title).toBe("Compare (4) · BenchBox Results"));
    expect(screen.getAllByText("Trino").length).toBeGreaterThan(0);
    expect(screen.getByTestId("compare-url-notice")).toHaveTextContent(
      "Ignored duplicate result ID after alias resolution: “alias-r1”.",
    );
  });

  it("loads public run tokens as their full result_ids", async () => {
    const snowflakeId = "tpch-snowflake-sf10.0-20261003-f0835929";
    const duckdbId = "tpch-duckdb-sf10.0-20261002-5f5ccaf2";
    setupUrl(["f0835929", "5f5ccaf2"]);
    const publicToFull: Record<string, string> = { f0835929: snowflakeId, "5f5ccaf2": duckdbId };
    vi.mocked(resolveShortId).mockImplementation((id) => Promise.resolve(publicToFull[id] ?? id));
    vi.mocked(getDetailResult).mockImplementation((id) =>
      Promise.resolve(id === snowflakeId ? { ...SQLITE, result_id: snowflakeId } : { ...DUCKDB, result_id: duckdbId }),
    );

    render(<Compare />);

    await waitFor(() => expect(getDetailResult).toHaveBeenCalledWith(snowflakeId));
    expect(getDetailResult).toHaveBeenCalledWith(duckdbId);
    expect(getDetailResult).not.toHaveBeenCalledWith("f0835929");
    expect(getDetailResult).not.toHaveBeenCalledWith("5f5ccaf2");
  });

  it("distinguishes unprocessed IDs from resolved overflow", async () => {
    const ids = Array.from({ length: 25 }, (_, index) => `r${index + 1}`);
    setupUrl(ids);
    const details = Object.fromEntries(
      ids.slice(0, 10).map((id, index) => [
        id,
        makeResult({
          result_id: id,
          platform: `Platform ${index + 1}`,
          platform_id: `platform-${index + 1}`,
        }),
      ]),
    );
    vi.mocked(getDetailResult).mockImplementation((id) => Promise.resolve(details[id] ?? null));

    render(<Compare />);

    await waitFor(() => expect(document.title).toBe("Compare (10) · BenchBox Results"));
    const notice = screen.getByTestId("compare-url-notice");
    expect(notice).toHaveTextContent(
      "Ignored 10 additional result IDs (“r11”, “r12”, “r13”, “r14”, “r15”, “r16”, “r17”, “r18”, “r19”, “r20”); comparisons are limited to 10 unique results.",
    );
    expect(notice).toHaveTextContent(
      "Did not process 5 additional result IDs to keep this page responsive",
    );
    expect(notice).not.toHaveTextContent("Ignored 15 additional result IDs");
  });

  it("preserves requested IDs when a detail lookup rejects", async () => {
    setupUrl(["r1", "r2", "r3"]);
    vi.mocked(getDetailResult).mockImplementation((id) => {
      if (id === "r2") return Promise.reject(new Error("transient detail failure"));
      return Promise.resolve(id === "r1" ? DUCKDB : POSTGRES);
    });

    render(<Compare />);

    await waitFor(() => expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy());
    expect(new URL(window.location.href).searchParams.get("ids")).toBe("r1,r2,r3");
  });

  it("shows the ranking table when no IDs are provided", async () => {
    setupUrl([]);

    render(<Compare />);

    await waitFor(() => expect(screen.getByTestId("home-hero-filter-band")).toBeTruthy());
    expect(screen.getByRole("heading", { level: 1, name: "Compare benchmark results" })).toBeTruthy();
    expect(screen.queryByText(/Add \?ids=/)).toBeNull();
    expect(route).not.toHaveBeenCalled();
  });

  it.skip("compare builder enforces same-cohort selection and launches with two compatible runs [retired: candidate table moved to Query]", async () => {
    setupUrl([]);
    vi.mocked(listResults).mockResolvedValue([
      {
        result_id: "r1",
        benchmark: "tpch",
        scale_factor: 0.1,
        platform: "DuckDB",
        platform_id: "duckdb",
        driver_version: null,
        run_date: "2026-04-17",
        power_score: 3000,
        total_duration_s: 60,
        geomean_ms: 10,
        display_geomean_ms: 10,
        query_count: 22,
        trust_label: "maintainer-run",
        visibility: "public-curated",
        platform_version: null,
        execution_mode: null,
        tuning_mode: null,
        tuning_hash: null,
        test_type: "measurement",
        validation_status: null,
        cost_usd: null,
        compliance_class: null,
        is_ranking_eligible: true,
        has_plans: false,
        plans_published: false,
        has_tuning: false,
        bundle_download_url: "",
        normalized_cost_usd: null,
        cost_model_version: null,
        cost_status: null,
        cost_scope: null,
        deployment_class: null,
        cloud_provider: null,
        cloud_region: null,
        instance_or_warehouse: null,
        storage_format: null,
      } as never,
      {
        result_id: "r2",
        benchmark: "tpch",
        scale_factor: 0.1,
        platform: "SQLite",
        platform_id: "sqlite",
        driver_version: null,
        run_date: "2026-04-17",
        power_score: 300,
        total_duration_s: 600,
        geomean_ms: 100,
        display_geomean_ms: 100,
        query_count: 22,
        trust_label: "maintainer-run",
        visibility: "public-curated",
        platform_version: null,
        execution_mode: null,
        tuning_mode: null,
        tuning_hash: null,
        test_type: "measurement",
        validation_status: null,
        cost_usd: null,
        compliance_class: null,
        is_ranking_eligible: true,
        has_plans: false,
        plans_published: false,
        has_tuning: false,
        bundle_download_url: "",
        normalized_cost_usd: null,
        cost_model_version: null,
        cost_status: null,
        cost_scope: null,
        deployment_class: null,
        cloud_provider: null,
        cloud_region: null,
        instance_or_warehouse: null,
        storage_format: null,
      } as never,
      {
        result_id: "r3",
        benchmark: "clickbench",
        scale_factor: 0.1,
        platform: "DuckDB",
        platform_id: "duckdb",
        driver_version: null,
        run_date: "2026-04-17",
        power_score: null,
        total_duration_s: 60,
        geomean_ms: 10,
        display_geomean_ms: 10,
        query_count: 43,
        trust_label: "maintainer-run",
        visibility: "public-curated",
        platform_version: null,
        execution_mode: null,
        tuning_mode: null,
        tuning_hash: null,
        test_type: "measurement",
        validation_status: null,
        cost_usd: null,
        compliance_class: null,
        is_ranking_eligible: true,
        has_plans: false,
        plans_published: false,
        has_tuning: false,
        bundle_download_url: "",
        normalized_cost_usd: null,
        cost_model_version: null,
        cost_status: null,
        cost_scope: null,
        deployment_class: null,
        cloud_provider: null,
        cloud_region: null,
        instance_or_warehouse: null,
        storage_format: null,
      } as never,
    ]);

    const { getByTestId } = render(<Compare />);

    await waitFor(() => expect(getByTestId("compare-builder")).toBeTruthy());
    await waitFor(() => expect(getByTestId("compare-builder-row-r1")).toBeTruthy());

    const launch = getByTestId("compare-builder-launch") as HTMLButtonElement;
    expect(launch.disabled).toBe(true);

    const r1Checkbox = within(getByTestId("compare-builder-row-r1")).getByRole("checkbox") as HTMLInputElement;
    r1Checkbox.click();

    await waitFor(() => {
      expect((getByTestId("compare-builder-compatible-only") as HTMLInputElement).checked).toBe(true);
    });
    expect(() => getByTestId("compare-builder-row-r3")).toThrow();
    (getByTestId("compare-builder-compatible-only") as HTMLInputElement).click();
    await waitFor(() => {
      const r3Checkbox = within(getByTestId("compare-builder-row-r3")).getByRole("checkbox") as HTMLInputElement;
      expect(r3Checkbox.disabled).toBe(true);
    });

    const r2Checkbox = within(getByTestId("compare-builder-row-r2")).getByRole("checkbox") as HTMLInputElement;
    r2Checkbox.click();
    await waitFor(() => expect((getByTestId("compare-builder-launch") as HTMLButtonElement).disabled).toBe(false));

    (getByTestId("compare-builder-launch") as HTMLButtonElement).click();
    await waitFor(() => {
      const calls = vi.mocked(route).mock.calls.map((c) => String(c[0]));
      const compareCall = calls.find((url) => url.startsWith("/results/compare?ids="));
      expect(compareCall).toBeTruthy();
      expect(compareCall).toContain("r1");
      expect(compareCall).toContain("r2");
    });
  });

  it.skip("compare picker hides incompatible candidates by default and surfaces the hidden count [retired: candidate table moved to Query]", async () => {
    setupUrl([]);
    vi.mocked(listResults).mockResolvedValue([
      makeResultRow({ result_id: "r1", platform: "DuckDB", platform_id: "duckdb" }),
      makeResultRow({
        result_id: "r2",
        platform: "SQLite",
        platform_id: "sqlite",
        power_score: 220,
        geomean_ms: 70,
      }),
      makeResultRow({
        result_id: "r3",
        platform: "DuckDB",
        platform_id: "duckdb",
        benchmark: "clickbench",
      }),
    ]);
    const { getByTestId, queryByTestId } = render(<Compare />);
    await waitFor(() => expect(getByTestId("compare-builder")).toBeTruthy());
    await waitFor(() => expect(getByTestId("compare-builder-row-r1")).toBeTruthy());

    expect(queryByTestId("compare-builder-compatible-only")).toBeNull();
    expect(getByTestId("compare-builder-row-r3")).toBeTruthy();

    const r1Checkbox = within(getByTestId("compare-builder-row-r1")).getByRole("checkbox") as HTMLInputElement;
    r1Checkbox.click();

    await waitFor(() => {
      expect((getByTestId("compare-builder-compatible-only") as HTMLInputElement).checked).toBe(true);
    });
    expect(queryByTestId("compare-builder-row-r3")).toBeNull();
    expect(getByTestId("compare-builder-status").textContent).toContain("1 incompatible row hidden");
  });

  it.skip("names the four-result ceiling in the builder status [retired: candidate table moved to Query]", async () => {
    setupUrl([]);
    vi.mocked(listResults).mockResolvedValue(
      Array.from({ length: 5 }, (_, index) =>
        makeResultRow({
          result_id: `r${index + 1}`,
          platform: `Platform ${index + 1}`,
          platform_id: `platform-${index + 1}`,
        }),
      ),
    );
    const { getByTestId } = render(<Compare />);
    await waitFor(() => expect(getByTestId("compare-builder-row-r1")).toBeTruthy());

    for (const id of ["r1", "r2", "r3", "r4"]) {
      const checkbox = within(getByTestId(`compare-builder-row-${id}`)).getByRole("checkbox") as HTMLInputElement;
      fireEvent.click(checkbox);
    }

    expect(getByTestId("compare-builder-status").textContent).toContain("4 results selected (maximum)");
  });

  it.skip("compare picker disables non-comparable candidates before launch [retired: candidate table moved to Query]", async () => {
    setupUrl([]);
    vi.mocked(listResults).mockResolvedValue([
      makeResultRow({ result_id: "r1", platform: "DuckDB", platform_id: "duckdb" }),
      makeResultRow({
        result_id: "r2",
        platform: "SQLite",
        platform_id: "sqlite",
        comparison_exclusion_reason: "insufficient_valid_timings",
      }),
    ]);
    const { getByTestId } = render(<Compare />);
    await waitFor(() => expect(getByTestId("compare-builder-row-r1")).toBeTruthy());

    const blocked = within(getByTestId("compare-builder-row-r2")).getByRole("checkbox") as HTMLInputElement;
    expect(blocked.disabled).toBe(true);
    expect(blocked.title).toContain("Result does not have enough valid query timings");
    const visibleReason = within(getByTestId("compare-builder-row-r2")).getByTestId("compare-disabled-reason");
    expect(visibleReason.textContent).toContain("Why unavailable: Insufficient valid timings");
    expect(visibleReason.textContent).toContain("Choose a run with at least two valid query timings");
    expect(blocked.getAttribute("aria-describedby")).toBe(visibleReason.id);

    blocked.click();
    expect((getByTestId("compare-builder-launch") as HTMLButtonElement).disabled).toBe(true);
  });

  it.skip("compare picker shows a zero-selectable recovery callout when every filtered row is disabled [retired: candidate table moved to Query]", async () => {
    setupUrl([]);
    vi.mocked(listResults).mockResolvedValue([
      makeResultRow({
        result_id: "blocked-a",
        platform: "DuckDB",
        comparison_exclusion_reason: "insufficient_query_coverage",
      }),
      makeResultRow({
        result_id: "blocked-b",
        platform: "SQLite",
        comparison_exclusion_reason: "insufficient_query_coverage",
      }),
    ]);

    const { getByTestId } = render(<Compare />);
    await waitFor(() => expect(getByTestId("compare-builder-row-blocked-a")).toBeTruthy());

    const callout = getByTestId("compare-builder-zero-selectable");
    expect(callout.textContent).toContain("No selectable compare rows");
    expect(callout.textContent).toContain("insufficient query coverage");
    expect(within(callout).getByRole("button", { name: "Clear filters" })).toBeTruthy();
    expect(getByTestId("compare-builder-row-blocked-a").textContent).toContain(
      "Why unavailable: Insufficient query coverage",
    );
  });

  it.skip("compare picker uses disambiguated aria-labels for same-platform rows [retired: candidate table moved to Query]", async () => {
    setupUrl([]);
    vi.mocked(listResults).mockResolvedValue([
      makeResultRow({
        result_id: "tpch-duckdb-sf0.1-20260502-aaaa1111",
        platform: "DuckDB",
        platform_id: "duckdb",
        run_date: "2026-05-02",
      }),
      makeResultRow({
        result_id: "tpch-duckdb-sf0.1-20260508-bbbb2222",
        platform: "DuckDB",
        platform_id: "duckdb",
        run_date: "2026-05-08",
      }),
    ]);
    const { findAllByRole } = render(<Compare />);
    const checkboxes = (await findAllByRole("checkbox")) as HTMLInputElement[];
    const labels = checkboxes
      .map((cb) => cb.getAttribute("aria-label") ?? "")
      .filter((label) => label.startsWith("Select "));
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels.some((label) => label.includes("aaaa1111"))).toBe(true);
    expect(labels.some((label) => label.includes("bbbb2222"))).toBe(true);
  });

  it.skip("loads selected runs after builder navigation changes only the compare query string [retired: candidate table moved to Query]", async () => {
    setupUrl([]);
    vi.mocked(listResults).mockResolvedValue([
      makeResultRow({ result_id: "r1", platform: "DuckDB", platform_id: "duckdb" }),
      makeResultRow({
        result_id: "r2",
        platform: "SQLite",
        platform_id: "sqlite",
        power_score: 300,
        geomean_ms: 100,
        display_geomean_ms: 100,
      }),
    ]);
    vi.mocked(toShortIds).mockResolvedValue(["r1", "r2"]);

    const { getByTestId, rerender } = render(<Compare url="/results/compare" />);

    await waitFor(() => expect(getByTestId("compare-builder-row-r1")).toBeTruthy());
    (within(getByTestId("compare-builder-row-r1")).getByRole("checkbox") as HTMLInputElement).click();
    (within(getByTestId("compare-builder-row-r2")).getByRole("checkbox") as HTMLInputElement).click();

    await waitFor(() => expect((getByTestId("compare-builder-launch") as HTMLButtonElement).disabled).toBe(false));
    (getByTestId("compare-builder-launch") as HTMLButtonElement).click();
    await waitFor(() => expect(route).toHaveBeenCalledWith("/results/compare?ids=r1,r2"));

    window.history.pushState(null, "", "/results/compare?ids=r1,r2");
    rerender(<Compare url="/results/compare?ids=r1,r2" />);

    await waitFor(() => expect(screen.queryByTestId("compare-builder")).toBeNull());
    await waitFor(() => expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy());
    expect(getDetailResult).toHaveBeenCalledWith("r1");
    expect(getDetailResult).toHaveBeenCalledWith("r2");
  });

  it("shows a 10× ratio to the lowest selected score in the DuckDB summary card (power_score primary)", async () => {
    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });
    await waitFor(() => expect(document.title).toBe("Compare (2) · BenchBox Results"));
    expect(screen.getAllByText(/Compared with lowest selected score/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText("10×").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/2026-04-01.*days ago/).length).toBeGreaterThan(0);
  });

  it("labels the lowest selected score instead of calling a run tied with itself", async () => {
    render(<Compare />);
    await waitFor(() => expect(screen.getAllByText("SQLite").length).toBeGreaterThan(0));

    const sqliteCard = screen.getByText("Public ID r2").closest(".card");
    expect(sqliteCard).toBeTruthy();
    expect(sqliteCard).toHaveTextContent("Lowest selected");
    expect(sqliteCard).not.toHaveTextContent("Tied");
  });

  it("lists the same rows in the same order on every run card and uses one wording for the comparison row", async () => {
    render(<Compare />);
    await waitFor(() => expect(screen.getAllByText("SQLite").length).toBeGreaterThan(0));

    const cards = ["Public ID r1", "Public ID r2"].map((text) => screen.getByText(text).closest(".card"));
    const rowLabels = cards.map((card) => Array.from(card!.querySelectorAll("dt")).map((dt) => dt.textContent));
    expect(rowLabels[0]).toEqual(rowLabels[1]);
    expect(rowLabels[0]?.slice(-3)).toEqual(["Compared with lowest selected score", "Mode", "Test type"]);
    expect(screen.queryByText("Relative position")).toBeNull();
    expect(screen.getByText("Lowest selected").closest("dd")).toBeTruthy();
  });

  it("keeps the fastest badge on one line", async () => {
    render(<Compare />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));
    const badges = screen.getAllByText("fastest");
    expect(badges.length).toBeGreaterThan(0);
    for (const badge of badges) expect(badge.classList.contains("whitespace-nowrap")).toBe(true);
  });

  it("renders a funding chip on the primary compare cards, not just the builder table", async () => {
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1" ? Promise.resolve(makeResult({ result_id: "r1", platform: "DuckDB", funding: "employer" })) : Promise.resolve(SQLITE),
    );

    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });

    expect(screen.getAllByText("Employer").length).toBeGreaterThan(0);
  });

  it("renders a computed decision summary before charts and query evidence", async () => {
    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });

    const summary = screen.getByRole("heading", { name: "Comparison summary" });
    const chartsHeading = screen.getByTestId("chart-panel-group-overview");
    const queryDiffHeading = screen.getByRole("heading", { name: "Query-level differences" });

    expect(summary.closest("section")).toHaveTextContent(
      "In these selected runs, DuckDB's power score was 10× better than the lowest selected run.",
    );
    expect(summary.closest("section")).toHaveTextContent("Where DuckDB wins");
    expect(summary.closest("section")).toHaveTextContent("2 of 2 shared queries");
    expect(summary.compareDocumentPosition(chartsHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(summary.compareDocumentPosition(queryDiffHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(chartsHeading.compareDocumentPosition(queryDiffHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("omits summary cost context when normalized costs are absent", async () => {
    render(<Compare />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy();
    });

    const summary = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    expect(summary).not.toHaveTextContent("Cost/performance:");
  });

  it("renders normalized cost/performance context when cost fields are available", async () => {
    const duckdb = makeResult({
      result_id: "r1",
      platform: "DuckDB",
      normalized_cost_usd: 0.5,
      cost_status: "normalized",
    });
    const sqlite = makeResult({
      result_id: "r2",
      platform: "SQLite",
      platform_id: "sqlite",
      power_score: 300,
      display_timings: SQLITE.display_timings,
      normalized_cost_usd: 1.5,
      cost_status: "normalized",
    });
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1" ? Promise.resolve(duckdb) : Promise.resolve(sqlite),
    );

    render(<Compare />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy();
    });

    const summary = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    expect(summary).toHaveTextContent("Cost/performance:");
    expect(summary).toHaveTextContent("winner cost $0.50");
    expect(summary).toHaveTextContent("30× cost/performance");
  });

  it("suppresses winner claims but keeps missing query evidence visible in the query diff table", async () => {
    const missingSqlite = makeResult({
      result_id: "r2",
      platform: "SQLite",
      platform_id: "sqlite",
      power_score: 300,
      display_timings: [
        { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        { query_id: "Q3", display_ms: 300, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      ],
    });
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1" ? Promise.resolve(DUCKDB) : Promise.resolve(missingSqlite),
    );

    render(<Compare />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy();
    });

    const summary = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    const queryDiff = screen.getByRole("heading", { name: "Query-level differences" }).closest("section");
    expect(summary).toHaveTextContent("No winner named");
    expect(summary).toHaveTextContent("Insufficient comparable query evidence");
    expect(summary).toHaveTextContent("Selected runs do not share at least two valid query timings");
    expect(queryDiff).toHaveTextContent("Showing 3 of 3 queries.");
    expect(queryDiff).toHaveTextContent("This page does not name a winner because Selected runs do not share at least two valid query timings");
    expect(queryDiff).toHaveTextContent("Not comparable");
  });

  it("suppresses multi-run standings when fewer than two queries are shared", async () => {
    setupUrl(["r1", "r2", "r3"]);
    const results = {
      r1: makeResult({
        result_id: "r1",
        platform: "DuckDB",
        display_timings: [
          { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q2", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
      r2: makeResult({
        result_id: "r2",
        platform: "SQLite",
        platform_id: "sqlite",
        display_timings: [
          { query_id: "Q1", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q3", display_ms: 30, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
      r3: makeResult({
        result_id: "r3",
        platform: "PostgreSQL",
        platform_id: "postgres",
        display_timings: [
          { query_id: "Q1", display_ms: 30, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          { query_id: "Q4", display_ms: 40, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        ],
      }),
    };
    vi.mocked(getDetailResult).mockImplementation((id) => Promise.resolve(results[id as keyof typeof results]));

    render(<Compare />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Standings" })).toBeTruthy());

    const standings = screen.getByRole("heading", { name: "Standings" }).closest("section");
    expect(standings).toHaveTextContent("Standings are unavailable because");
    expect(standings).toHaveTextContent("Selected runs do not share at least two valid query timings");
    expect(standings?.querySelector("table")).toBeNull();
    expect(screen.getByRole("heading", { name: "Query by run" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Query-level differences" })).toBeNull();
    expect(screen.getByRole("link", { name: "Compare Q1 iterations across selected runs" }).getAttribute("href"))
      .toBe("/results/compare/query/Q1?ids=r1%2Cr2%2Cr3");
  });

  it("links single-query views with full result IDs so generated short IDs are not required", async () => {
    const fullIds = [
      "tpch-duckdb-sf0.01-20260403-f0835929",
      "tpch-datafusion-sf0.01-20260403-91c44a02",
      "tpch-clickhouse-sf0.01-20260403-55d0e91b",
    ];
    setupUrl(fullIds);
    const fullResults = Object.fromEntries(
      fullIds.map((id, index) => [
        id,
        makeResult({
          result_id: id,
          platform: `Platform ${index + 1}`,
          platform_id: `platform-${index + 1}`,
        }),
      ]),
    );
    vi.mocked(getDetailResult).mockImplementation((id) => Promise.resolve(fullResults[id] ?? null));

    render(<Compare />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Query by run" })).toBeTruthy());

    const href = screen
      .getByRole("link", { name: "Compare Q1 iterations across selected runs" })
      .getAttribute("href")!;
    const idsParam = new URL(href, "https://benchbox.dev").searchParams.get("ids");
    expect(idsParam).toBe(fullIds.join(","));
  });

  it("links compare warning counts to the Comparability Receipt and names warning classes", async () => {
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1"
        ? Promise.resolve(DUCKDB)
        : Promise.resolve(
            makeResult({
              result_id: "r2",
              platform: "SQLite",
              platform_id: "sqlite",
              run_date: "2026-04-03",
              platform_version: "3.45",
              power_score: 300,
              display_timings: SQLITE.display_timings,
            }),
          ),
    );

    render(<Compare />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy());

    const guardrails = screen.getByRole("region", { name: "Compare guardrails" });
    const warningLink = screen.getByTestId("compare-warning-link") as HTMLAnchorElement;
    const receipt = screen.getByRole("region", { name: "Comparison checks" });
    const warningTarget = screen.getByTestId("comparability-warning-target") as HTMLElement;
    expect(warningLink.getAttribute("href")).toBe("#comparability-receipt-warnings");
    expect(warningLink.getAttribute("aria-label")).toBe("2 warnings; review comparison differences");
    expect(receipt.getAttribute("id")).toBe("comparability-receipt");
    expect(warningTarget.getAttribute("id")).toBe("comparability-receipt-warnings");
    expect(guardrails.textContent).toContain("Warning classes:");
    expect(guardrails.textContent).toContain("Date window");
    expect(guardrails.textContent).toContain("Platform version");

    fireEvent.click(warningLink);
    expect(document.activeElement).toBe(warningTarget);
    expect(window.location.hash).toBe("#comparability-receipt-warnings");
  });

  it("names Validation explicitly in the guardrails summary and caveats the winner claim (DuckDB vs Pandas, unvalidated)", async () => {
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1"
        ? Promise.resolve(
            makeResult({
              result_id: "r1",
              platform: "DuckDB",
              power_score: 3560,
              platform_version: "1.1.0",
              driver_version: "1.1.0",
              execution_mode: "native",
              validation_status: "passed",
            }),
          )
        : Promise.resolve(
            makeResult({
              result_id: "r2",
              platform: "Pandas",
              platform_id: "pandas",
              power_score: 1000,
              platform_version: "2.2.0",
              driver_version: "2.2.0",
              execution_mode: "dataframe",
              validation_status: "not_run",
              display_timings: [
                { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
                { query_id: "Q2", display_ms: 200, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
              ],
            }),
          ),
    );

    render(<Compare />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy());

    const guardrails = screen.getByRole("region", { name: "Compare guardrails" });
    expect(guardrails.textContent).toMatch(/Warning classes: Validation,/);

    const summary = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    expect(summary).toHaveTextContent("DuckDB");
    expect(summary).toHaveTextContent("Validation caution");
    expect(summary).toHaveTextContent("Unvalidated result");
  });

  it("uses singular warning copy for one compare guardrail warning", async () => {
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1"
        ? Promise.resolve(DUCKDB)
        : Promise.resolve(
            makeResult({
              result_id: "r2",
              platform: "SQLite",
              platform_id: "sqlite",
              platform_version: "3.45",
              power_score: 300,
              display_timings: SQLITE.display_timings,
            }),
          ),
    );

    render(<Compare />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy());

    const guardrails = screen.getByRole("region", { name: "Compare guardrails" });
    const receipt = screen.getByRole("region", { name: "Comparison checks" });
    expect(guardrails).toHaveTextContent("1 warning");
    expect(guardrails).not.toHaveTextContent("1 warnings");
    expect(receipt).toHaveTextContent("1 warning");
    expect(receipt).not.toHaveTextContent("1 warnings");
    expect(screen.getByRole("link", { name: "1 warning; review comparison differences" })).toBeTruthy();
  });

  it("summary card speedup and query diff ratio are consistent for 2-platform fixture", async () => {
    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });
    const cells = screen.getAllByText("10×");
    expect(cells.length).toBeGreaterThanOrEqual(2);
  });

  it("shows 'Power score' as the primary metric label for tpch", async () => {
    render(<Compare />);
    await waitFor(() => {
      const spans = screen.getAllByText("DuckDB");
      expect(spans.length).toBeGreaterThan(0);
    });
    const labels = screen.getAllByText(/Power score/i);
    expect(labels.length).toBeGreaterThan(0);
  });

  it("ignores metric URL params and uses the canonical benchmark metric", async () => {
    setupUrl(["r1", "r2"], { metric: "display_geomean_ms" });

    render(<Compare />);

    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });
    expect(getPrimaryMetricForBenchmark).toHaveBeenCalledWith("tpch", "unknown");
    const labels = screen.getAllByText(/Power score/i);
    expect(labels.length).toBeGreaterThan(0);
  });

  it("preserves facet URL params when canonicalizing long compare IDs", async () => {
    setupUrl(["tpch-duckdb-long-id", "tpch-sqlite-long-id"], {
      benchmark: "tpch",
      cost_status: "normalized",
    });
    vi.mocked(resolveShortId).mockImplementation((id) =>
      Promise.resolve(id.includes("duckdb") ? "r1" : "r2"),
    );
    vi.mocked(toShortIds).mockResolvedValue(["short1", "short2"]);

    render(<Compare />);

    await waitFor(() =>
      expect(new URL(window.location.href).searchParams.get("ids")).toBe("short1,short2"),
    );
    const params = new URL(window.location.href).searchParams;
    expect(params.get("benchmark")).toBe("tpch");
    expect(params.get("cost_status")).toBe("normalized");
  });

  it("shows a secondary Geomean row when power_score is primary", async () => {
    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });
    const geomeans = screen.getAllByText(/Geomean/i);
    expect(geomeans.length).toBeGreaterThan(0);
  });

  it("shows 'Geomean query time' as the primary label for clickbench", async () => {
    const cbDuck = makeResult({ result_id: "r1", benchmark: "clickbench", platform: "DuckDB" });
    const cbSq = makeResult({
      result_id: "r2",
      benchmark: "clickbench",
      platform: "SQLite",
      platform_id: "sqlite",
    });
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1" ? Promise.resolve(cbDuck) : Promise.resolve(cbSq)
    );

    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });
    const labels = screen.getAllByText(/Geomean query time/i);
    expect(labels.length).toBeGreaterThan(0);
  });

  it("baseline selector shows both platforms as options", async () => {
    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Baseline" })).toBeTruthy();
    });
    const select = screen.getByRole("combobox", { name: "Baseline" }) as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.text);
    expect(options).toContain("DuckDB");
    expect(options).toContain("SQLite");
  });

  it("baseline selector defaults to the first result and records its stable ID", async () => {
    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Baseline" })).toBeTruthy();
    });
    const select = screen.getByRole("combobox", { name: "Baseline" }) as HTMLSelectElement;
    expect(select.value).toBe("r1");
    expect(select.options[0]!.text).toBe("DuckDB");
  });

  it("baseline selector updates query diffs and chart baseline without changing compare URL membership", async () => {
    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });

    const beforeIds = new URL(window.location.href).searchParams.get("ids");
    const select = screen.getByLabelText("Baseline") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "r2" } });

    const diffTable = screen.getByRole("heading", { name: "Query-level differences" }).closest("section");
    expect(select.value).toBe("r2");
    expect(diffTable).toHaveTextContent("Baseline: SQLite");
    expect(diffTable).toHaveTextContent("DuckDB");
    expect(diffTable).toHaveTextContent("10×");
    expect(new URL(window.location.href).searchParams.get("ids")).toBe(beforeIds);
    expect(new URL(window.location.href).searchParams.get("baseline")).toBe("r2");
    expect(screen.getAllByText("SQLite").length).toBeGreaterThan(0);
  });

  it("stores the shared query subset in the URL without reloading the runs", async () => {
    render(<Compare />);
    await waitFor(() => expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0));
    const callsBeforeChange = vi.mocked(getDetailResult).mock.calls.length;

    fireEvent.change(screen.getByRole("combobox", { name: "Queries shown" }), {
      target: { value: "speedups" },
    });

    expect(new URL(window.location.href).searchParams.get("queries")).toBe("speedups");
    expect(vi.mocked(getDetailResult).mock.calls.length).toBe(callsBeforeChange);
    expect(screen.getByRole("combobox", { name: "Queries shown" })).toHaveValue("speedups");
  });

  it("restores the shared baseline and query subset from the URL", async () => {
    setupUrl(["r1", "r2"], { baseline: "r2", queries: "slowdowns" });

    render(<Compare />);
    await waitFor(() => expect(screen.getByLabelText("Baseline")).toHaveValue("r2"));

    expect(screen.getByRole("combobox", { name: "Queries shown" })).toHaveValue("slowdowns");
    expect(screen.getByRole("heading", { name: "Query-level differences" }).closest("section")).toHaveTextContent(
      "Baseline: SQLite",
    );
  });

  it("keeps a hardware mismatch inseparable from the leading-run claim", async () => {
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1"
        ? Promise.resolve(makeResult({
            result_id: "r1",
            environment: {
              arch: "arm64",
              cpu_family: "apple_silicon",
              cpu_model: "Apple M4",
              logical_cores: 10,
              memory_gb: 32,
            },
          }))
        : Promise.resolve(makeResult({
            result_id: "r2",
            platform: "SQLite",
            platform_id: "sqlite",
            power_score: 300,
            environment: {
              arch: "x86_64",
              cpu_family: "intel_xeon",
              cpu_model: "Intel Xeon",
              logical_cores: 16,
              memory_gb: 64,
            },
          })),
    );

    render(<Compare />);
    const summary = await screen.findByRole("heading", { name: "Comparison summary" });
    const summarySection = summary.closest("section");
    expect(summarySection).toHaveTextContent("Leading run on Power score");
    expect(summarySection).toHaveTextContent("This compares recorded runs, not platforms in isolation.");
    expect(summarySection).toHaveTextContent("architecture");
    expect(summarySection).toHaveTextContent("CPU model");
    expect(screen.queryByText("Winner", { exact: true })).toBeNull();
  });

  it("supports three-result compare and baseline switching without changing URL membership", async () => {
    setupUrl(["r1", "r2", "r3"]);
    vi.mocked(getDetailResult).mockImplementation((id) => {
      if (id === "r1") return Promise.resolve(DUCKDB);
      if (id === "r2") return Promise.resolve(SQLITE);
      if (id === "r3") return Promise.resolve(POSTGRES);
      return Promise.resolve(null);
    });

    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("PostgreSQL").length).toBeGreaterThan(0);
    });

    await waitFor(() => expect(document.title).toBe("Compare (3) · BenchBox Results"));
    const summary = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    const select = screen.getByLabelText("Baseline") as HTMLSelectElement;
    const options = Array.from(select.options).map((option) => option.text);
    const beforeIds = new URL(window.location.href).searchParams.get("ids");

    expect(summary).toHaveTextContent("In these selected runs, DuckDB's power score was 10× better than the lowest selected run.");
    expect(options).toEqual(["DuckDB", "SQLite", "PostgreSQL"]);
    expect(screen.getByRole("heading", { name: "Query by run" }).closest("section")).toHaveTextContent(
      "Showing 2 of 2 queries.",
    );
    expect(screen.queryByRole("heading", { name: "Query-level differences" })).toBeNull();

    fireEvent.change(select, { target: { value: "r3" } });

    const queryGrid = screen.getByRole("heading", { name: "Query by run" }).closest("section");
    expect(select.value).toBe("r3");
    expect(queryGrid).toHaveTextContent("Ratio against PostgreSQL");
    expect(screen.getByTestId("cell-Q1-0")).toHaveTextContent("0.2×");
    expect(screen.getByTestId("cell-Q1-2")).toHaveTextContent("1×");
    expect(new URL(window.location.href).searchParams.get("ids")).toBe(beforeIds);
    expect(new URL(window.location.href).searchParams.get("baseline")).toBe("r3");
  });

  it("renders severe cohort mismatches with guardrails and no winner claim", async () => {
    const clickbench = makeResult({
      result_id: "r2",
      benchmark: "clickbench",
      scale_factor: 1,
      platform: "SQLite",
      platform_id: "sqlite",
      power_score: 300,
      display_timings: [
        { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
        { query_id: "Q2", display_ms: 200, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      ],
    });
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1" ? Promise.resolve(DUCKDB) : Promise.resolve(clickbench),
    );

    render(<Compare />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy();
    });

    expect(screen.getByRole("heading", { name: "Mixed Benchmark Comparison" })).toBeTruthy();
    expect(screen.queryByText(/Cannot compare results from different benchmarks/)).toBeNull();
    const guardrails = screen.getByRole("region", { name: "Compare guardrails" });
    const receipt = screen.getByRole("region", { name: "Comparison checks" });
    const summary = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    const queryDiff = screen.getByRole("heading", { name: "Query-level differences" }).closest("section");
    expect(guardrails).toHaveTextContent(
      "The summary does not rank these runs because runs differ in benchmark and scale factor.",
    );
    expect(receipt).toHaveTextContent("Benchmark");
    expect(receipt).toHaveTextContent("Scale factor");
    expect(summary).toHaveTextContent("Not directly comparable: runs differ in benchmark and scale factor.");
    expect(summary).toHaveTextContent("No winner named");
    expect(summary).not.toHaveTextContent("DuckDB's power score was");
    expect(summary).not.toHaveTextContent("fastest");
    expect(screen.queryByText("vs worst")).toBeNull();
    expect(queryDiff).toHaveTextContent(
      "This page does not name a winner because runs differ in benchmark and scale factor",
    );
  });

  it("uses canonical SSB identity for the heading while keeping aliases comparable", async () => {
    const canonicalSsb = makeResult({
      result_id: "r1",
      benchmark: "ssb",
      platform: "DuckDB",
      platform_id: "duckdb",
    });
    const legacySsb = makeResult({
      result_id: "r2",
      benchmark: "star_schema",
      platform: "SQLite",
      platform_id: "sqlite",
      power_score: 300,
      display_geomean_ms: 100,
      display_timings: SQLITE.display_timings,
    });
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1" ? Promise.resolve(canonicalSsb) : Promise.resolve(legacySsb),
    );

    render(<Compare />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy();
    });

    expect(screen.getByRole("heading", { name: "SSB Comparison" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Mixed Benchmark Comparison" })).toBeNull();

    const guardrails = screen.getByRole("region", { name: "Compare guardrails" });
    const summary = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    expect(guardrails).toHaveTextContent(
      "These runs share the same benchmark, scale, and test phase. Review the differences below before drawing conclusions.",
    );
    expect(guardrails).toHaveTextContent("Same ranking");
    expect(summary).not.toHaveTextContent("No winner named");
    expect(summary).toHaveTextContent("DuckDB's power score was");
  });

  it("suppresses winner claims for direct compare URLs with mixed phases", async () => {
    const powerRun = makeResult({
      result_id: "r1",
      platform: "DuckDB",
      platform_id: "duckdb",
      test_type: "power",
    });
    const throughputRun = makeResult({
      result_id: "r2",
      platform: "SQLite",
      platform_id: "sqlite",
      power_score: 300,
      display_timings: SQLITE.display_timings,
      test_type: "throughput",
    });
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1" ? Promise.resolve(powerRun) : Promise.resolve(throughputRun),
    );

    render(<Compare />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy();
    });

    const guardrails = screen.getByRole("region", { name: "Compare guardrails" });
    const receipt = screen.getByRole("region", { name: "Comparison checks" });
    const summary = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    const queryDiff = screen.getByRole("heading", { name: "Query-level differences" }).closest("section");
    expect(guardrails).toHaveTextContent("The summary does not rank these runs because runs differ in phase.");
    expect(receipt).toHaveTextContent("Test phase");
    expect(receipt).toHaveTextContent("DuckDB: power; SQLite: throughput");
    expect(summary).toHaveTextContent("Not directly comparable: runs differ in phase.");
    expect(summary).toHaveTextContent("No winner named");
    expect(summary).not.toHaveTextContent("DuckDB's power score was");
    expect(screen.queryByText("vs worst")).toBeNull();
    expect(queryDiff).toHaveTextContent("This page does not name a winner because runs differ in phase");
  });

  it("suppresses winner claims for direct compare URLs with different throughput stream counts", async () => {
    const threeStreams = makeResult({
      result_id: "r1",
      platform: "DuckDB",
      platform_id: "duckdb",
      test_type: "throughput",
      stream_count: 3,
    });
    const twoStreams = makeResult({
      result_id: "r2",
      platform: "SQLite",
      platform_id: "sqlite",
      power_score: 300,
      display_timings: SQLITE.display_timings,
      test_type: "throughput",
      stream_count: 2,
    });
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1" ? Promise.resolve(threeStreams) : Promise.resolve(twoStreams),
    );

    render(<Compare />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Comparison summary" })).toBeTruthy();
    });

    const summary = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    expect(summary).toHaveTextContent("Not directly comparable: runs differ in stream count.");
    expect(summary).toHaveTextContent("No winner named");
    expect(summary).not.toHaveTextContent("DuckDB's power score was");
  });

  it("keeps the detailed comparability receipt after decision and chart evidence", async () => {
    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });

    const receipt = screen.getByRole("region", { name: "Comparison checks" });
    const chartsHeading = screen.getByTestId("chart-panel-group-overview");
    const queryDiffHeading = screen.getByRole("heading", { name: "Query-level differences" });

    expect(receipt).toHaveTextContent("Benchmark");
    expect(receipt).toHaveTextContent("Query scope");
    expect(receipt).toHaveTextContent("Normalized cost");
    expect(receipt).toHaveTextContent("Cost model");
    expect(chartsHeading.compareDocumentPosition(receipt) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(queryDiffHeading.compareDocumentPosition(receipt) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows compare receipt warnings for tuning and environment differences", async () => {
    const duckdb = makeResult({
      result_id: "r1",
      platform: "DuckDB",
      platform_id: "duckdb",
      driver_version: "1.0",
      environment: { os: "Linux", arch: "x64", cpu_count: 8 },
      tuning_mode: "default",
    });
    const sqlite = makeResult({
      result_id: "r2",
      platform: "SQLite",
      platform_id: "sqlite",
      driver_version: "2.0",
      environment: { os: "macOS", arch: "arm64", cpu_count: 10 },
      tuning_mode: "manual",
    });
    vi.mocked(getDetailResult).mockImplementation((id) =>
      id === "r1" ? Promise.resolve(duckdb) : Promise.resolve(sqlite)
    );

    render(<Compare />);
    await waitFor(() => {
      expect(screen.getAllByText("DuckDB").length).toBeGreaterThan(0);
    });

    const receipt = screen.getByRole("region", { name: "Comparison checks" });
    expect(receipt).toHaveTextContent("Tuning");
    expect(receipt).toHaveTextContent("DuckDB: default; SQLite: manual");
    expect(receipt).toHaveTextContent("Environment");
    expect(receipt).toHaveTextContent("DuckDB: Linux, x64, 8 CPU");
    expect(receipt).toHaveTextContent("SQLite: macOS, Arm64, 10 CPU");
  });
});
