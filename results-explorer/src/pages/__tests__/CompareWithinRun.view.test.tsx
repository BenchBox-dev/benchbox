import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { CompareWithinRun } from "@/pages/CompareWithinRun";
import type { DetailResult } from "@/types";

vi.mock("@/lib/duckdbQueries", () => ({
  getDetailResult: vi.fn(),
  resolveShortId: vi.fn((id: string) => Promise.resolve(id)),
}));

import { getDetailResult, resolveShortId } from "@/lib/duckdbQueries";

function makeDetail(): DetailResult {
  return {
    result_id: "res-123",
    benchmark: "tpch",
    scale_factor: 1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: "1.0",
    run_date: "2026-04-18T00:00:00Z",
    total_duration_s: 10,
    geomean_ms: 25,
    display_geomean_ms: 25,
    power_score: null,
    has_display_timing: true,
    valid_query_count: 2,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    environment: {
      cpu_model: "Apple M1",
      os: "Darwin",
      logical_cores: 8,
      physical_cores: 8,
      memory_gb: 16,
    } as any,
    queries: [
      { query_id: "Q1", duration_ms: 50, status: "pass", run_type: "warmup", iter: null, stream: 0 },
      { query_id: "Q1", duration_ms: 20, status: "pass", run_type: "measurement", iter: 1, stream: 0 },
      { query_id: "Q1", duration_ms: 10, status: "pass", run_type: "measurement", iter: 2, stream: 0 },
      { query_id: "Q2", duration_ms: 100, status: "pass", run_type: "warmup", iter: null, stream: 0 },
      { query_id: "Q2", duration_ms: 40, status: "pass", run_type: "measurement", iter: 1, stream: 0 },
      { query_id: "Q2", duration_ms: 30, status: "pass", run_type: "measurement", iter: 2, stream: 0 },
    ],
    display_timings: [
      { query_id: "Q1", display_ms: 15, is_valid_display_timing: true, timing_exclusion_reason: null, sample_count: 1 },
      { query_id: "Q2", display_ms: 35, is_valid_display_timing: true, timing_exclusion_reason: null, sample_count: 1 },
    ],
    has_plans: false,
    has_tuning: false,
    bundle_download_url: "",
    trust_label: "certified",
    visibility: "public",
    funding: "self",
    platform_version: "1.0",
    execution_mode: null,
    tuning_mode: null,
    tuning_hash: null,
    compliance_class: null, test_type: "standard", validation_status: "valid", cost_usd: null,
  };
}

describe("CompareWithinRun page component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (getDetailResult as any).mockResolvedValue(makeDetail());
    vi.mocked(resolveShortId).mockImplementation((id) => Promise.resolve(id));
    window.history.replaceState(null, "", "/results/r/res-123/passes");
  });

  it("resolves a short ID and canonicalizes the passes route", async () => {
    vi.mocked(resolveShortId).mockResolvedValue("res-123");
    window.history.replaceState(null, "", "/results/r/1234abcd/passes?bases=default,warm_pass_1#comparison");

    render(<CompareWithinRun resultId="1234abcd" />);
    await waitFor(() => expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy());

    expect(getDetailResult).toHaveBeenCalledWith("res-123");
    expect(window.location.pathname).toBe("/results/r/res-123/passes");
    expect(window.location.search).toContain("bases=");
    expect(window.location.hash).toBe("#comparison");
  });

  it("renders within-run comparisons with reference badge and ratio calculations", async () => {
    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => {
      expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy();
    });

    expect(screen.getByText("Q1")).toBeTruthy();
    expect(screen.getByText("Q2")).toBeTruthy();
    expect(screen.getAllByText(/Geomean query time/i).length).toBeGreaterThan(0);
  });

  it("reconciles a bare route to passes the run actually recorded", async () => {
    const detail = makeDetail();
    (getDetailResult as any).mockResolvedValue({
      ...detail,
      queries: [
        { query_id: "Q1", duration_ms: 20, status: "pass", run_type: "measurement", iter: 3, stream: 0 },
        { query_id: "Q2", duration_ms: 40, status: "pass", run_type: "measurement", iter: 3, stream: 0 },
      ],
    });

    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => expect(screen.getByTestId("reference-radio-warm_pass_3")).toBeTruthy());
    expect(screen.queryByTestId("reference-radio-warm_pass_1")).toBeNull();
    expect(window.location.search).toContain("warm_pass_3");
  });

  it("labels failed basis evidence as failed rather than unrecorded", async () => {
    const detail = makeDetail();
    (getDetailResult as any).mockResolvedValue({
      ...detail,
      queries: detail.queries.map((query) =>
        query.query_id === "Q2" && query.run_type === "measurement" && query.iter === 1
          ? { ...query, status: "fail" }
          : query,
      ),
    });
    window.history.replaceState(null, "", "/results/r/res-123/passes?bases=default,warm_pass_1&ref=0");

    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => expect(screen.getByTestId("failed-cell")).toHaveTextContent("failed"));
    expect(screen.getByTestId("failed-cell")).toHaveAttribute(
      "title",
      "No execution of this query passed under that basis.",
    );
  });

  it("shows an unavailable state when no two bases share query evidence", async () => {
    const detail = makeDetail();
    (getDetailResult as any).mockResolvedValue({
      ...detail,
      queries: [
        { query_id: "Q2", duration_ms: 20, status: "pass", run_type: "warmup", iter: 0, stream: 0 },
      ],
      display_timings: [
        { query_id: "Q1", display_ms: 10, is_valid_display_timing: true, timing_exclusion_reason: null, sample_count: 1 },
      ],
    });

    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => {
      expect(screen.getByText("This run does not have two measurement bases with shared query evidence.")).toBeTruthy();
    });
    expect(screen.queryByText("measurement bases compared")).toBeNull();
  });

  it.each(["nonsense", "default,default"])(
    "repairs malformed or duplicate fallback bases to a shared pair: %s",
    async (rawBases) => {
      const detail = makeDetail();
      (getDetailResult as any).mockResolvedValue({
        ...detail,
        queries: [
          { query_id: "Q2", duration_ms: 20, status: "pass", run_type: "measurement", iter: 1, stream: 0 },
          { query_id: "Q2", duration_ms: 30, status: "pass", run_type: "measurement", iter: 2, stream: 0 },
        ],
        display_timings: [
          { query_id: "Q1", display_ms: 10, is_valid_display_timing: true, timing_exclusion_reason: null, sample_count: 1 },
        ],
      });
      window.history.replaceState(null, "", `/results/r/res-123/passes?bases=${rawBases}`);

      render(<CompareWithinRun resultId="res-123" />);

      await waitFor(() => expect(screen.getByTestId("reference-radio-all_warm:min")).toBeTruthy());
      expect(screen.getByTestId("reference-radio-warm_pass_1")).toBeTruthy();
      expect(screen.queryByTestId("reference-radio-default")).toBeNull();
      expect(window.location.search).toContain("warm_pass_1");
      expect(window.location.search).toContain("all_warm%3Amin");
    },
  );

  it("repairs explicit bases that are available but share no query evidence", async () => {
    const detail = makeDetail();
    (getDetailResult as any).mockResolvedValue({
      ...detail,
      queries: [
        { query_id: "Q1", duration_ms: 50, status: "pass", run_type: "warmup", iter: null, stream: 0 },
        { query_id: "Q2", duration_ms: 40, status: "pass", run_type: "measurement", iter: 1, stream: 0 },
      ],
      display_timings: [
        { query_id: "Q1", display_ms: 50, is_valid_display_timing: true, timing_exclusion_reason: null, sample_count: 1 },
      ],
    });
    window.history.replaceState(null, "", "/results/r/res-123/passes?bases=warmup%3Amedian,warm_pass_1&ref=0");

    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => expect(window.location.search).toContain("default"));
    expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy();
    expect(screen.getByText("1 of 2 queries comparable")).toBeTruthy();
  });

  it("changes reference basis when clicking the reference radio button", async () => {
    window.history.replaceState(null, "", "/results/r/res-123/passes?bases=default,warm_pass_1&ref=0");
    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => {
      expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy();
    });

    const radioWarmPass1 = screen.getByTestId("reference-radio-warm_pass_1");
    fireEvent.click(radioWarmPass1);

    expect(window.location.search).toContain("ref=1");
  });

  it("allows removing a basis column when more than 2 are present", async () => {
    window.history.replaceState(null, "", "/results/r/res-123/passes?bases=default,warm_pass_1,warm_pass_2&ref=0");
    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => {
      expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy();
    });

    const removeButtons = screen.getAllByRole("button", { name: /^Remove / });
    expect(removeButtons.length).toBe(3);

    fireEvent.click(removeButtons[2]!);

    await waitFor(() => {
      expect(window.location.search).not.toContain("warm_pass_2");
    });
  });

  it("allows adding an available basis column", async () => {
    window.history.replaceState(null, "", "/results/r/res-123/passes?bases=default,warm_pass_1&ref=0");
    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => {
      expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy();
    });

    const addButton = screen.getByRole("button", { name: "+ Add basis" });
    fireEvent.click(addButton);

    await waitFor(() => {
      expect(screen.getAllByRole("radio").length).toBe(3);
    });
  });

  it("suppresses aggregate direction when only one query is comparable", async () => {
    const detail = makeDetail();
    (getDetailResult as any).mockResolvedValue({
      ...detail,
      queries: detail.queries.filter(
        (query) => query.query_id === "Q1" || query.run_type !== "measurement" || query.iter !== 1,
      ),
    });
    window.history.replaceState(null, "", "/results/r/res-123/passes?bases=default,warm_pass_1&ref=0");

    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => {
      expect(screen.getByText("1 of 2 queries comparable")).toBeTruthy();
    });
    expect(screen.queryByText("faster")).toBeNull();
    expect(screen.queryByText("slower")).toBeNull();
    expect(screen.queryByText("parity")).toBeNull();
    expect(screen.queryByText("0.5×")).toBeNull();
    expect(screen.queryByText("0.5× (−50 ms)")).toBeNull();
  });

  it("does not style tie-band query ratios as faster", async () => {
    const detail = makeDetail();
    (getDetailResult as any).mockResolvedValue({
      ...detail,
      queries: detail.queries.map((query) => {
        if (query.run_type !== "measurement" || query.iter !== 1) return query;
        return {
          ...query,
          duration_ms: query.query_id === "Q1" ? 14.97 : 34.93,
        };
      }),
    });
    window.history.replaceState(null, "", "/results/r/res-123/passes?bases=default,warm_pass_1&ref=0");

    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => {
      expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy();
    });
    const displayedTieRatios = screen.getAllByText(/^1× \(/);
    expect(displayedTieRatios.length).toBeGreaterThan(0);
    for (const ratio of displayedTieRatios) {
      expect(ratio.className).not.toContain("bb-tone-success-fg");
      expect(ratio.className).not.toContain("font-medium");
    }
  });
});

describe("CompareWithinRun passes page presentation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(resolveShortId).mockImplementation((id) => Promise.resolve(id));
    window.history.replaceState(null, "", "/results/r/res-123/passes");
  });

  it("defaults to the published median against the warmup pass when a warmup was recorded", async () => {
    (getDetailResult as any).mockResolvedValue(makeDetail());
    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy());
    expect(screen.getAllByText("published median").length).toBeGreaterThan(0);
    expect(screen.getAllByText("warmup pass").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("one-measured-pass-note")).toBeNull();
  });

  it("states that the median equals the only pass instead of a table of 1×", async () => {
    (getDetailResult as any).mockResolvedValue(
      {
        ...makeDetail(),
        queries: [
          { query_id: "Q1", duration_ms: 20, status: "pass", run_type: "measurement", iter: 1, stream: 0 },
          { query_id: "Q2", duration_ms: 40, status: "pass", run_type: "measurement", iter: 1, stream: 0 },
        ],
        display_timings: [
          { query_id: "Q1", display_ms: 20, is_valid_display_timing: true, timing_exclusion_reason: null, sample_count: 1 },
          { query_id: "Q2", display_ms: 40, is_valid_display_timing: true, timing_exclusion_reason: null, sample_count: 1 },
        ],
      },
    );
    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => expect(screen.getByTestId("one-measured-pass-note")).toBeTruthy());
    expect(screen.getByTestId("one-measured-pass-note").textContent).toBe(
      "Only one measured pass: the median equals pass 1.",
    );
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("1×")).toBeNull();
  });

  it("links the page header back to the run through a breadcrumb", async () => {
    (getDetailResult as any).mockResolvedValue(makeDetail());
    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy());
    const breadcrumb = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
    expect(breadcrumb.getByRole("link", { name: "DuckDB" }).getAttribute("href")).toBe("/results/r/res-123");
    expect(breadcrumb.getByRole("link", { name: "Results" }).getAttribute("href")).toBe("/results/");
  });

  it("shows the reference label once in the header, not on every column", async () => {
    (getDetailResult as any).mockResolvedValue(makeDetail());
    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy());
    expect(screen.getAllByText(/^Reference:/)).toHaveLength(1);
    const table = within(screen.getByRole("table"));
    expect(table.queryAllByText("reference")).toHaveLength(0);
    expect(table.queryAllByText("ref")).toHaveLength(0);
  });

  it("uses the shared primary button style for adding a basis", async () => {
    (getDetailResult as any).mockResolvedValue(makeDetail());
    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy());
    const addButton = screen.getByRole("button", { name: "+ Add basis" });
    expect(addButton.className).toContain("btn-primary");
    expect(addButton.className).not.toContain("bg-[var(--bb-accent)]");
  });

  it("keeps the query column pinned while the value columns scroll on a phone", async () => {
    (getDetailResult as any).mockResolvedValue(makeDetail());
    render(<CompareWithinRun resultId="res-123" />);

    await waitFor(() => expect(screen.getByText("Compare measurements from one DuckDB run")).toBeTruthy());
    expect(screen.getByTestId("within-run-scroll-container")).toBeTruthy();
    expect(screen.getByText("Q1").className).toContain("sticky left-0");
    expect(screen.getByRole("columnheader", { name: "Query" }).className).toContain("sticky left-0");
  });
});
