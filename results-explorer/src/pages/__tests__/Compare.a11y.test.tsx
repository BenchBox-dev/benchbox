import { render, screen, waitFor } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoAxeViolations } from "@/testing/axe-helper";
import type { ResultRow } from "@/lib/duckdbQueries";

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
    listResults: vi.fn(),
  };
});

import { getDetailResult, listResults } from "@/lib/duckdbQueries";
import { Compare } from "@/pages/Compare";
import type { DetailResult } from "@/types";

function makeRow(overrides: Partial<ResultRow> = {}): ResultRow {
  return {
    result_id: "blocked-a",
    benchmark: "tpch",
    scale_factor: 0.1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: null,
    run_date: "2026-04-17",
    power_score: null,
    total_duration_s: 60,
    geomean_ms: null,
    display_geomean_ms: null,
    query_count: 22,
    has_display_timing: true,
    valid_query_count: 1,
    missing_query_count: 21,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: "insufficient_query_coverage",
    ranking_exclusion_reason: "insufficient_query_coverage",
    trust_label: "maintainer-run",
    funding: "unspecified",
    visibility: "public-curated",
    platform_version: null,
    execution_mode: null,
    tuning_mode: null,
    tuning_hash: null,
    test_type: "power",
    validation_status: "exact",
    cost_usd: null,
    compliance_class: null,
    is_ranking_eligible: false,
    has_plans: false,
    plans_published: false,
    has_tuning: false,
    bundle_download_url: "",
    ...overrides,
  };
}

function makeDetail(overrides: Partial<DetailResult> = {}): DetailResult {
  return {
    result_id: "r1",
    benchmark: "tpch",
    scale_factor: 0.1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: "1.0",
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
    environment: { os: "Linux", arch: "x64", cpu_count: 8, memory_gb: 32, python: "3.12" },
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
    platform_version: "0.10",
    execution_mode: "sql",
    tuning_mode: "default",
    tuning_hash: null,
    test_type: "power",
    validation_status: "exact",
    compliance_class: null,
    cost_usd: null,
    normalized_cost_usd: null,
    cost_model_version: "2026.05.0",
    cost_model_source: "benchbox.core.cost.pricing",
    cost_scope: "compute_only",
    cost_status: "unavailable",
    billing_unit: "unknown",
    pricing_region: "unknown",
    ...overrides,
  };
}

describe("Compare visible disabled reasons accessibility", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState(null, "", "/results/compare");
    vi.mocked(listResults).mockResolvedValue([
      makeRow({ result_id: "blocked-a", platform: "DuckDB" }),
      makeRow({ result_id: "blocked-b", platform: "SQLite" }),
    ]);
  });

  it.skip("keeps zero-selectable disabled reasons accessible [retired: table moved to Query]", async () => {
    const { container } = render(<Compare />);

    await waitFor(() => expect(screen.getByTestId("compare-builder-zero-selectable")).toBeTruthy());
    expect(screen.getAllByTestId("compare-disabled-reason").length).toBeGreaterThan(0);
    await expectNoAxeViolations(container);
  });

  it("keeps compare warning links and receipt targets accessible", async () => {
    window.history.replaceState(null, "", "/results/compare?ids=r1,r2");
    vi.mocked(getDetailResult).mockImplementation((id) =>
      Promise.resolve(
        id === "r1"
          ? makeDetail()
          : makeDetail({
              result_id: "r2",
              platform: "SQLite",
              platform_id: "sqlite",
              driver_version: "2.0",
              run_date: "2026-04-03",
              platform_version: "3.45",
            }),
      ),
    );

    const { container } = render(<Compare />);

    await waitFor(() => expect(screen.getByTestId("compare-warning-link")).toBeTruthy());
    expect(screen.getByRole("region", { name: "Warning details" })).toBeTruthy();
    await expectNoAxeViolations(container);
  }, 15_000);
});
