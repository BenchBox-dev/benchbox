import { render, screen, fireEvent, waitFor, within } from "@testing-library/preact";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { DetailResult } from "@/types";

vi.mock("@/lib/duckdbQueries", async () => {
  const actual = await vi.importActual<typeof import("@/lib/duckdbQueries")>("@/lib/duckdbQueries");
  return {
    ...actual,
    getDetailResult: vi.fn(),
    getPrimaryMetricForBenchmark: vi.fn().mockResolvedValue("power_score"),
    resolveShortId: vi.fn((id: string) => Promise.resolve(id)),
  };
});

import { getDetailResult, getPrimaryMetricForBenchmark, resolveShortId } from "@/lib/duckdbQueries";
import { ResultDetail } from "@/pages/ResultDetail";

function makeDetail(overrides: Partial<DetailResult> = {}): DetailResult {
  return {
    result_id: "r1",
    benchmark: "tpch",
    scale_factor: 0.1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: null,
    run_date: "2026-04-01",
    total_duration_s: 60,
    geomean_ms: 15,
    display_geomean_ms: 12,
    power_score: 3000,
    has_display_timing: true,
    valid_query_count: 2,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    environment: {},
    queries: [
      { query_id: "Q1", duration_ms: 10, status: "pass", run_type: "measurement", iter: 1, stream: null },
      { query_id: "Q1", duration_ms: 11, status: "pass", run_type: "measurement", iter: 2, stream: null },
      { query_id: "Q1", duration_ms: 12, status: "pass", run_type: "measurement", iter: 3, stream: null },
      { query_id: "Q2", duration_ms: 20, status: "pass", run_type: "measurement", iter: 1, stream: null },
      { query_id: "Q2", duration_ms: 21, status: "pass", run_type: "measurement", iter: 2, stream: null },
      { query_id: "Q2", duration_ms: 22, status: "pass", run_type: "measurement", iter: 3, stream: null },
    ],
    display_timings: [
      { query_id: "Q1", display_ms: 11, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      { query_id: "Q2", display_ms: 21, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
    ],
    has_plans: false,
    has_tuning: false,
    bundle_download_url: "https://example.test/download/r1.json",
    trust_label: "maintainer-run",
    visibility: "public-curated",
    funding: "unspecified",
    platform_version: null,
    execution_mode: null,
    tuning_mode: null,
    tuning_hash: null,
    test_type: null,
    validation_status: null,
    cost_usd: null,
    compliance_class: null,
    ...overrides,
  };
}

describe("ResultDetail - median-first contract", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail());
    vi.mocked(getPrimaryMetricForBenchmark).mockResolvedValue("power_score");
    vi.mocked(resolveShortId).mockImplementation((id) => Promise.resolve(id));
    window.history.replaceState(null, "", "/results/r/r1");
  });

  afterEach(() => vi.unstubAllGlobals());

  it("resolves a short ID and canonicalizes the route without losing URL state", async () => {
    vi.mocked(resolveShortId).mockResolvedValue("result-full-id");
    window.history.replaceState(null, "", "/results/r/1234abcd?view=receipt#run-receipt");

    render(<ResultDetail resultId="1234abcd" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(getDetailResult).toHaveBeenCalledWith("result-full-id");
    expect(window.location.pathname).toBe("/results/r/result-full-id");
    expect(window.location.search).toBe("?view=receipt");
    expect(window.location.hash).toBe("#run-receipt");
  });

  it("shows tuning once in the header and not as a separate summary card", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({ has_tuning: true, tuning_mode: "tuned" }));

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.queryByRole("heading", { name: "Tuning" })).toBeNull();
    expect(within(screen.getByTestId("page-header-meta")).getByText(/tuned/i)).toBeTruthy();
  });

  it("gives an invalid result ID a recovery link", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(null);

    render(<ResultDetail resultId="stale-result" />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());

    expect(screen.getByRole("alert")).toHaveTextContent('No result found for "stale-result".');
    expect(screen.getByRole("link", { name: "Find runs" })).toHaveAttribute("href", "/results/query");
    expect(screen.getByRole("link", { name: "Browse benchmarks" })).toHaveAttribute("href", "/results/benchmarks/");
  });

  it("(a) reports one row per display_timing, from the pass table, not per raw sample", async () => {
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.getByText("Query timings (2)")).toBeTruthy();
    await waitFor(() => expect(document.title).toBe("TPC-H · DuckDB · SF 0.1 · BenchBox Results"));

    const passes = screen.getByTestId("detail-passes-scroll-container");
    expect(within(passes).getAllByRole("row")).toHaveLength(4);
    expect(screen.queryByRole("heading", { name: "Passes within this run" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Passes within this run" })).toBeNull();
    expect(screen.getByText(/Warm median is the median/i)).toBeTruthy();
    expect(screen.queryByTestId("detail-timings-scroll-container")).toBeNull();
  });

  it("(a2) falls back to the median table for a run that published no passes", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({ queries: [] }));
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.queryByTestId("detail-passes-scroll-container")).toBeNull();
    expect(screen.getByTestId("detail-timings-scroll-container")).toBeTruthy();
    expect(screen.getAllByText(/Median latency/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Q1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Q2").length).toBeGreaterThanOrEqual(1);
  });

  it("shows the run age beside the result date and in the receipt", async () => {
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(within(screen.getByTestId("page-header")).getByTestId("run-date-chip").getAttribute("aria-label")).toMatch(/^Run date .* ago\)\./);
    const chip = within(screen.getByRole("region", { name: "Run receipt" })).getByTestId("run-date-chip");
    fireEvent.click(chip);
    expect(chip.textContent).toMatch(/ago/);
  });

  it("(b) sample_count cell matches display_timings[i].sample_count", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({ queries: [] }));
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.getByRole("button", { name: /^Samples/ })).toBeTruthy();
    const sampleCells = screen.getAllByText("3");
    expect(sampleCells.length).toBeGreaterThanOrEqual(2);
  });

  it("ignores metric URL params and uses the canonical benchmark metric", async () => {
    window.history.replaceState(null, "", "/results/r/r1?metric=display_geomean_ms");

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(getPrimaryMetricForBenchmark).toHaveBeenCalledWith("tpch", "unknown");
  });

  it("(c) expanding 'Individual samples' reveals raw queries table", async () => {
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    const expander = screen.getByText(/Individual samples \(6\)/i);
    expect(expander).toBeTruthy();

    fireEvent.click(expander);

    expect(screen.getAllByText(/^Duration/i).length).toBeGreaterThan(0);
  });

  it("links to two measurement bases the run can actually answer", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({
      queries: [
        { query_id: "Q1", duration_ms: 10, status: "pass", run_type: "measurement", iter: 3, stream: null },
      ],
      display_timings: [
        { query_id: "Q1", display_ms: 10, sample_count: 1, is_valid_display_timing: true, timing_exclusion_reason: null },
      ],
    }));

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.getByTestId("within-run-compare-link")).toHaveAttribute(
      "href",
      "/results/r/r1/passes?bases=default%2Cwarm_pass_3&ref=0",
    );
  });

  it("hides within-run comparison when a warmup-only run has fewer than two usable bases", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({
      queries: [
        { query_id: "Q1", duration_ms: 10, status: "pass", run_type: "warmup", iter: 0, stream: null },
      ],
      display_timings: [
        { query_id: "Q1", display_ms: null, sample_count: 0, is_valid_display_timing: false, timing_exclusion_reason: "missing_timing" },
      ],
    }));

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.queryByTestId("within-run-compare-link")).toBeNull();
    expect(screen.getByText(/Individual samples \(1\)/i)).toBeTruthy();
  });

  it("hides within-run comparison when available bases have no shared query", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({
      queries: [
        { query_id: "Q2", duration_ms: 20, status: "pass", run_type: "warmup", iter: 0, stream: null },
      ],
      display_timings: [
        { query_id: "Q1", display_ms: 10, sample_count: 1, is_valid_display_timing: true, timing_exclusion_reason: null },
      ],
    }));

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.queryByTestId("within-run-compare-link")).toBeNull();
  });

  it("announces sort state on the median table when it is the per-query view", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({ queries: [] }));
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    const medianHeader = screen.getByRole("columnheader", { name: /Median latency/ });
    expect(medianHeader).toHaveAttribute("aria-sort", "none");
    fireEvent.click(within(medianHeader).getByRole("button"));
    expect(medianHeader).toHaveAttribute("aria-sort", "ascending");
  });

  it("announces sort state on the raw samples table controls", async () => {
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    fireEvent.click(screen.getByText(/Individual samples \(6\)/i));
    const rawDurationHeader = screen.getByRole("columnheader", { name: /Duration/ });
    expect(rawDurationHeader).toHaveAttribute("aria-sort", "none");
    expect(rawDurationHeader.querySelector('[role="button"]')).toBeNull();
    fireEvent.click(within(rawDurationHeader).getByRole("button"));
    expect(rawDurationHeader).toHaveAttribute("aria-sort", "ascending");
  });

  it("(d) registry-driven chart panel uses display_timings-backed charts", async () => {
    const detail = makeDetail();
    vi.mocked(getDetailResult).mockResolvedValue(detail);
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.getByTestId("chart-panel-long")).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByTestId("chart-panel-chart-query_histogram")).toBeTruthy();
  });

  it("renders primary summary and actions before the run receipt", async () => {
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    const header = screen.getByTestId("page-header");
    const summary = screen.getByRole("region", { name: "Result summary" });
    const receipt = screen.getByRole("region", { name: "Run receipt" });
    const charts = screen.getByTestId("chart-panel-long");

    expect(summary).toHaveTextContent("Primary metric · higher is better");
    expect(summary).toHaveTextContent("Power score");
    expect(header).toHaveTextContent("Public ID r1");
    expect(within(header).getByRole("link", { name: "Find a run to compare" })).toHaveAttribute("href", "/results/query?pick=r1");
    expect(summary.compareDocumentPosition(receipt) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(charts.compareDocumentPosition(receipt) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(receipt).toHaveTextContent("Measurement samples");
    expect(receipt).toHaveTextContent("Download bundle");
  });

  it("shows Throughput@Size as the primary metric of a throughput result", async () => {
    vi.mocked(getPrimaryMetricForBenchmark).mockResolvedValueOnce("throughput_at_size");
    vi.mocked(getDetailResult).mockResolvedValue(
      makeDetail({ power_score: null, throughput_at_size: 3741.26, stream_count: 3, test_type: "throughput" }),
    );
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    const summary = screen.getByRole("region", { name: "Result summary" });
    expect(getPrimaryMetricForBenchmark).toHaveBeenCalledWith("tpch", "throughput");
    expect(summary).toHaveTextContent("Primary metric · higher is better");
    expect(summary).toHaveTextContent("Throughput@Size");
    expect(summary).toHaveTextContent("3,741");
    expect(summary).not.toHaveTextContent("Power score");
  });

  it("suppresses absent primary metrics and empty timing surfaces", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(
      makeDetail({
        power_score: null,
        display_geomean_ms: null,
        queries: [],
        display_timings: [],
        has_display_timing: false,
        valid_query_count: 0,
      }),
    );

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    const summary = screen.getByRole("region", { name: "Result summary" });
    expect(summary).not.toHaveTextContent("Primary metric");
    expect(summary).not.toHaveTextContent("N/A");
    expect(screen.queryByRole("heading", { name: /Query Timings/ })).toBeNull();
    expect(screen.queryByTestId("chart-panel-long")).toBeNull();
    expect(screen.getByRole("region", { name: "Run receipt" })).toBeTruthy();
  });

  it("omits a passing title badge but keeps non-passing validation visible", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({ validation_status: "passed" }));

    const passing = render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());
    expect(screen.getByTestId("page-header").querySelector('[data-role="validation"]')).toBeNull();

    passing.unmount();
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({ validation_status: "failed" }));
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());
    expect(screen.getByTestId("page-header").querySelector('[data-role="validation"]')).toHaveTextContent(
      "Failed",
    );
  });

  it("uses scan precision in the primary summary while keeping exact power score available", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({ power_score: 11636.652993864305 }));

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    const summary = screen.getByRole("region", { name: "Result summary" });
    const primaryMetric = within(summary).getByLabelText(
      "11,637. Exact power score: 11,636.652993864305",
    );

    expect(primaryMetric).toHaveTextContent("11,637");
    expect(primaryMetric).toHaveAttribute("title", "Exact power score: 11,636.652993864305");
    expect(summary).not.toHaveTextContent("11,636.652993864305");
  });

  it("links captured execution plans only when plans_published is true", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(
      makeDetail({
        has_plans: true,
        plans_published: true,
        bundle_download_url: `${window.location.origin}/download/r1.json`,
      }),
    );

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    const planLinks = screen.getAllByRole("link", { name: "Download plans" }) as HTMLAnchorElement[];
    expect(planLinks.length).toBeGreaterThanOrEqual(1);
    expect(planLinks[0]?.getAttribute("href")).toBe(`${window.location.origin}/download/r1.plans.json`);
    expect(screen.getByRole("button", { name: "View query plans" })).toBeTruthy();
  });

  it.each([
    ["javascript URL", "javascript:alert(1).json"],
    ["cross-origin URL", "https://attacker.example/run.json"],
  ])("does not expose or fetch plans from an untrusted %s", async (_label, bundleUrl) => {
    vi.mocked(getDetailResult).mockResolvedValue(
      makeDetail({
        has_plans: true,
        plans_published: true,
        bundle_download_url: bundleUrl,
      }),
    );
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.queryByRole("button", { name: "View query plans" })).toBeNull();
    expect(screen.queryAllByRole("link", { name: "Download plans" })).toHaveLength(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not render a Download plans link when plans were not actually published (w1 regression)", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({ has_plans: true }));

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.queryAllByRole("link", { name: "Download plans" })).toHaveLength(0);
    expect(screen.getByText("Execution plans were recorded, but no published plan file is available for this result."))
      .toBeTruthy();
  });

  it("renders the funding chip alongside the trust badge when funding is disclosed", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(
      makeDetail({ funding: "employer", trust_label: "vendor-supplied" }),
    );

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(document.querySelector('[data-role="funding"]')?.textContent).toBe("Employer funded");
    expect(document.querySelector('[data-role="trust"]')?.textContent).toBe("Vendor");
  });

  it("omits the funding chip when funding is unspecified, leaving the trust badge intact", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(makeDetail({ funding: "unspecified" }));

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(document.querySelector('[data-role="funding"]')).toBeNull();
    expect(document.querySelector('[data-role="trust"]')).not.toBeNull();
  });

  it("exposes the provenance legend explaining the badges it renders", async () => {
    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.getByRole("button", { name: /What do these labels mean\?/i })).toBeTruthy();
  });

  it("badges an accepted override in the header even when validation passed", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(
      makeDetail({ validation_status: "passed", override_rules: '["timing-plateau"]' }),
    );

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    const headerMeta = within(screen.getByTestId("page-header-meta"));
    const badge = headerMeta.getByText("Timing-plateau");
    expect(badge.getAttribute("data-tone")).toBe("warning");
  });

  it("hides the header validation badge for a clean pass with no override", async () => {
    vi.mocked(getDetailResult).mockResolvedValue(
      makeDetail({ validation_status: "passed", override_rules: null }),
    );

    render(<ResultDetail resultId="r1" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    const headerMeta = within(screen.getByTestId("page-header-meta"));
    expect(headerMeta.queryByText(/Overridden:/)).toBeNull();
    expect(headerMeta.queryByText("passed")).toBeNull();
  });
});
