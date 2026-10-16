import { render, screen, waitFor } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DetailResult } from "@/types";

const localState = vi.hoisted(() => ({ preview: null as null | { detail: DetailResult; fileName: string; primaryMetric: "power_score" } }));

vi.mock("@/lib/localResultState", () => ({
  useLocalResultState: () => ({
    preview: localState.preview,
    importFile: vi.fn(),
    clear: vi.fn(),
  }),
}));

vi.mock("@/lib/duckdbQueries", async () => {
  const actual = await vi.importActual<typeof import("@/lib/duckdbQueries")>("@/lib/duckdbQueries");
  return {
    ...actual,
    getDetailResult: vi.fn(),
    getPrimaryMetricForBenchmark: vi.fn(),
  };
});

import { getDetailResult, getPrimaryMetricForBenchmark } from "@/lib/duckdbQueries";
import { ResultDetail } from "@/pages/ResultDetail";

function localDetail(): DetailResult {
  return {
    result_id: "local-aabbccddeeff",
    benchmark: "tpch",
    scale_factor: 1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: "1.4.3",
    run_date: "2026-09-05",
    total_duration_s: 4,
    geomean_ms: 20,
    display_geomean_ms: 20,
    power_score: 1234,
    has_display_timing: true,
    logical_query_count: 2,
    valid_query_count: 2,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: "local_result",
    environment: { os: "macOS" },
    queries: [
      { query_id: "1", duration_ms: 10, status: "pass", run_type: "measurement", iter: 1, stream: null },
      { query_id: "2", duration_ms: 40, status: "pass", run_type: "measurement", iter: 1, stream: null },
    ],
    display_timings: [
      { query_id: "1", display_ms: 10, sample_count: 1, is_valid_display_timing: true, timing_exclusion_reason: null },
      { query_id: "2", display_ms: 40, sample_count: 1, is_valid_display_timing: true, timing_exclusion_reason: null },
    ],
    has_plans: false,
    plans_published: false,
    has_tuning: false,
    bundle_download_url: "",
    trust_label: "local-run",
    visibility: "local-preview",
    funding: "unspecified",
    platform_version: "1.4.3",
    execution_mode: "sql",
    tuning_mode: null,
    tuning_hash: null,
    test_type: "power",
    validation_status: "passed",
    cost_usd: null,
    compliance_class: null,
  };
}

describe("local ResultDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localState.preview = { detail: localDetail(), fileName: "run.json", primaryMetric: "power_score" };
  });

  it("reuses detail rendering with local provenance and submission actions", async () => {
    render(<ResultDetail resultId="local-aabbccddeeff" source="local" />);
    await waitFor(() => expect(screen.queryByText("Loading result...")).toBeNull());

    expect(screen.getByTestId("local-result-banner")).toHaveTextContent("has not been uploaded");
    expect(screen.getByText("Local", { selector: "span" })).toBeTruthy();
    expect(screen.getAllByText("Local Run").length).toBeGreaterThan(0);
    expect(screen.getByText(/Local preview ID/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Submit for public review" })).toHaveAttribute(
      "href",
      "/docs/contributing-results.html",
    );
    expect(screen.getByRole("link", { name: "Submit for public review" })).toHaveAttribute(
      "referrerpolicy",
      "no-referrer",
    );
    expect(screen.getByRole("button", { name: "Open another result" })).toBeTruthy();
    expect(screen.queryByText("Compare this result")).toBeNull();
    expect(screen.queryByText("Add to comparison")).toBeNull();
    expect(screen.queryByText("Download bundle")).toBeNull();
    expect(screen.queryByTestId("within-run-compare-link")).toBeNull();
    expect(getDetailResult).not.toHaveBeenCalled();
    expect(getPrimaryMetricForBenchmark).not.toHaveBeenCalled();
  });

  it("asks for the file again after a reload loses memory state", async () => {
    localState.preview = null;
    render(<ResultDetail resultId="local-aabbccddeeff" source="local" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("no longer available");
    expect(screen.getByRole("button", { name: "Open result file again" })).toBeTruthy();
  });

  it("offers the file picker with focus when no local result is open yet", () => {
    render(<ResultDetail resultId="" source="local" />);

    const chooseButton = screen.getByRole("button", { name: "Choose result file" });
    expect(document.activeElement).toBe(chooseButton);
    expect(screen.getByTestId("local-result-file-input")).toHaveAttribute("accept", ".json,application/json");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
