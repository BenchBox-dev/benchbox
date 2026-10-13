import { render, screen } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import type { DetailResult } from "@/types";
import { CompareSummary } from "@/components/CompareSummary";
import { buildCompareDecisionSummary } from "@/lib/compareSummary";

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
    normalized_cost_usd: 0.5,
    cost_status: "normalized",
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

describe("CompareSummary", () => {
  it("renders the computed headline, query record, tail stats, and cost context", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult(),
        makeResult({
          result_id: "r2",
          platform: "SQLite",
          platform_id: "sqlite",
          power_score: 300,
          normalized_cost_usd: 1.5,
          display_timings: [
            { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q2", display_ms: 200, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          ],
        }),
      ],
      "power_score",
    );

    render(<CompareSummary summary={summary} />);

    const summaryRegion = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    expect(summaryRegion).not.toBeNull();
    expect(summaryRegion).toHaveTextContent("In these selected runs, DuckDB's power score was 10× better than the lowest selected run.");
    expect(summaryRegion).toHaveTextContent("Where DuckDB wins");
    expect(summaryRegion).toHaveTextContent("2 of 2 shared queries");
    expect(summaryRegion).toHaveTextContent("p50");
    expect(summaryRegion).toHaveTextContent("15 ms");
    expect(summaryRegion).toHaveTextContent("winner cost $0.50");
    expect(summaryRegion).toHaveTextContent("30× cost/performance");
  });

  it("renders suppressed winner state for incomparable cohorts", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult(),
        makeResult({
          result_id: "r2",
          platform: "SQLite",
          platform_id: "sqlite",
          power_score: 300,
        }),
      ],
      "power_score",
      {
        suppressWinnerClaims: true,
        suppressionReason: "benchmarks differ",
      },
    );

    render(<CompareSummary summary={summary} />);

    const summaryRegion = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    expect(summaryRegion).toHaveTextContent("Not directly comparable: benchmarks differ.");
    expect(summaryRegion).toHaveTextContent("No winner named");
    expect(summaryRegion).toHaveTextContent("Not claimed");
    expect(summaryRegion).toHaveTextContent("Review the individual query measurements below");
  });

  it("caveats the winner claim when one selected candidate is unvalidated (not_run)", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "duck", platform: "DuckDB", power_score: 3000, validation_status: "passed" }),
        makeResult({
          result_id: "pandas",
          platform: "Pandas",
          platform_id: "pandas",
          power_score: 843,
          validation_status: "not_run",
          display_timings: [
            { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q2", display_ms: 200, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          ],
        }),
      ],
      "power_score",
    );

    expect(summary.claimSuppressed).toBe(false);
    expect(summary.winner?.platform).toBe("DuckDB");
    expect(summary.headline).toContain("DuckDB's power score was");
    expect(summary.headline).toContain("Validation caution");
    expect(summary.headline).toContain("Pandas: No validation");

    render(<CompareSummary summary={summary} />);
    const summaryRegion = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    expect(summaryRegion).toHaveTextContent("Unvalidated result");
    expect(summaryRegion).toHaveTextContent("Validation caution:");
    expect(summaryRegion).toHaveTextContent("Pandas: No validation");
    expect(screen.getByTestId("compare-validation-caveat")).toBeTruthy();
  });

  it("does not show a validation caveat when both candidates are clean (passed)", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "duck", platform: "DuckDB", power_score: 3000, validation_status: "passed" }),
        makeResult({
          result_id: "r2",
          platform: "SQLite",
          platform_id: "sqlite",
          power_score: 300,
          validation_status: "passed",
          display_timings: [
            { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q2", display_ms: 200, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          ],
        }),
      ],
      "power_score",
    );

    expect(summary.nonCleanValidation).toHaveLength(0);
    expect(summary.validationCaveat).toBeNull();
    expect(summary.headline).toBe("In these selected runs, DuckDB's power score was 10× better than the lowest selected run.");

    render(<CompareSummary summary={summary} />);
    const summaryRegion = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    expect(summaryRegion).not.toHaveTextContent("Unvalidated result");
    expect(screen.queryByTestId("compare-validation-caveat")).toBeNull();
  });

  it("does not show a validation caveat when validation_status is simply absent (not a non-clean status)", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "duck", platform: "DuckDB", power_score: 3000 }),
        makeResult({
          result_id: "r2",
          platform: "SQLite",
          platform_id: "sqlite",
          power_score: 300,
          display_timings: [
            { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q2", display_ms: 200, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          ],
        }),
      ],
      "power_score",
    );

    expect(summary.nonCleanValidation).toHaveLength(0);
    render(<CompareSummary summary={summary} />);
    const summaryRegion = screen.getByRole("heading", { name: "Comparison summary" }).closest("section");
    expect(summaryRegion).not.toHaveTextContent("Unvalidated result");
  });
});

describe("copy for the summary", () => {
  it("uses singular grammar for one normalized run", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "duck", ranking_exclusion_reason: "insufficient_common_valid_timings" }),
        makeResult({
          result_id: "sqlite",
          platform: "SQLite",
          ranking_exclusion_reason: "insufficient_common_valid_timings",
          normalized_cost_usd: null,
          cost_status: "not_normalized",
        }),
      ],
      "power_score",
    );
    render(<CompareSummary summary={summary} />);
    expect(screen.getByText(/1 selected run has normalized cost; winner cost is unavailable\./)).toBeTruthy();
  });

  it("shows the hardware boundary once, beside the leading-run claim", () => {
    const boundary = "Hardware boundary: architecture differs. This compares recorded runs, not platforms in isolation.";
    const summary = buildCompareDecisionSummary(
      [makeResult({ result_id: "duck" }), makeResult({ result_id: "sqlite", platform: "SQLite", power_score: 300 })],
      "power_score",
      { comparisonBoundary: boundary },
    );
    const { container } = render(<CompareSummary summary={summary} />);
    expect(container.textContent?.split("not platforms in isolation").length).toBe(2);
    expect(screen.getByText(/In these selected runs, DuckDB's power score/).textContent).toContain(boundary);
  });

  it("labels the query record as shared queries and states the fastest-outright definition", () => {
    const summary = buildCompareDecisionSummary(
      [
        makeResult({ result_id: "duck" }),
        makeResult({
          result_id: "sqlite",
          platform: "SQLite",
          power_score: 300,
          display_timings: [
            { query_id: "Q1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
            { query_id: "Q2", display_ms: 200, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
          ],
        }),
      ],
      "power_score",
    );
    render(<CompareSummary summary={summary} />);
    expect(screen.getByText("2 of 2 shared queries")).toBeTruthy();
    expect(screen.getByText(/Fastest outright on 2 · 0 slower · 0 tied/)).toBeTruthy();
  });
});
