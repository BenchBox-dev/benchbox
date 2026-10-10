import { describe, expect, it } from "vitest";
import type { DetailResult, PlatformRow } from "@/types";
import {
  normalizePrimaryMetric,
  phaseScoreValue,
  primaryMetricHigherIsBetter,
  primaryMetricLabel,
  primaryMetricValue,
  validPrimaryMetricValue,
} from "@/lib/displayEligibility";
import { buildCompareDecisionSummary } from "@/lib/compareSummary";
import {
  compareCohortMismatches,
  compareCohortSignatureForRow,
  compareCohortSummary,
} from "@/lib/compareCohort";
import { formatPhaseWithStreams } from "@/lib/displayLabels";
import { formatFacetDisplayValue } from "@/lib/facetDisplay";
import { facetsToWhereClause, normalizeFacetState, readFacetParam } from "@/lib/facetModel";
import { appendFacetParams, matchesFacetRow } from "@/lib/facetMatching";
import { formatMetricValue, formatScoreMetric, formatThroughputScore } from "@/lib/metricFormatters";
import { parseLocalResultText } from "@/lib/localResult";

function platformRow(overrides: Partial<PlatformRow> = {}): PlatformRow {
  return {
    result_id: "r1",
    short_id: "r1",
    platform_id: "spark",
    platform: "Spark",
    platform_version: null,
    tuning_mode: null,
    tuning_hash: null,
    execution_mode: null,
    trust_label: "maintainer-run",
    funding: "unspecified",
    run_date: "2026-10-03",
    is_ranking_eligible: true,
    has_display_timing: true,
    valid_query_count: 22,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    power_score: null,
    throughput_at_size: 3741.26,
    display_geomean_ms: 2509.5,
    sample_geomean_ms: 2509.5,
    cost_usd: null,
    compliance_class: "official",
    percentile_stats: null,
    phase_durations: null,
    timings: {},
    timing_eligibility: {},
    ...overrides,
  };
}

function detailResult(overrides: Partial<DetailResult> = {}): DetailResult {
  return {
    result_id: "r1",
    benchmark: "tpch",
    scale_factor: 1,
    platform: "Spark",
    platform_id: "spark",
    driver_version: null,
    run_date: "2026-10-03",
    total_duration_s: 119,
    geomean_ms: 2509.5,
    display_geomean_ms: 2509.5,
    power_score: null,
    throughput_at_size: 3741.26,
    stream_count: 3,
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
      { query_id: "1", display_ms: 100, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      { query_id: "2", display_ms: 200, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
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
    test_type: "throughput",
    validation_status: "passed",
    compliance_class: "official",
    cost_usd: null,
    ...overrides,
  };
}

describe("primary metric helpers", () => {
  it("normalizes the stored primary metric", () => {
    expect(normalizePrimaryMetric("power_score")).toBe("power_score");
    expect(normalizePrimaryMetric("throughput_at_size")).toBe("throughput_at_size");
    expect(normalizePrimaryMetric("display_geomean_ms")).toBe("display_geomean_ms");
    expect(normalizePrimaryMetric("something_else")).toBe("display_geomean_ms");
    expect(normalizePrimaryMetric(undefined)).toBe("display_geomean_ms");
  });

  it("ranks both TPC scores higher-is-better and geomean lower-is-better", () => {
    expect(primaryMetricHigherIsBetter("power_score")).toBe(true);
    expect(primaryMetricHigherIsBetter("throughput_at_size")).toBe(true);
    expect(primaryMetricHigherIsBetter("display_geomean_ms")).toBe(false);
  });

  it("reads Throughput@Size from a row and rejects non-positive values", () => {
    const row = platformRow();
    expect(primaryMetricValue(row, "throughput_at_size")).toBe(3741.26);
    expect(primaryMetricValue(row, "power_score")).toBeNull();
    expect(primaryMetricValue(row, "display_geomean_ms")).toBe(2509.5);
    expect(validPrimaryMetricValue(platformRow({ throughput_at_size: 0 }), "throughput_at_size")).toBeNull();
    expect(validPrimaryMetricValue(platformRow({ throughput_at_size: undefined }), "throughput_at_size")).toBeNull();
    expect(primaryMetricLabel("throughput_at_size")).toBe("Throughput@Size");
  });

  it("formats Throughput@Size like the other TPC scores", () => {
    expect(formatThroughputScore(3741.26).valueText).toBe("3,741");
    expect(formatThroughputScore(3741.26).accessibleText).toBe("throughput at size 3,741");
    expect(formatThroughputScore(null).valueText).toBe("-");
    expect(formatScoreMetric("throughput_at_size", 3741.26)).toBe("3,741");
    expect(formatScoreMetric("power_score", 3741.26)).toBe("3,741");
    expect(formatMetricValue({ type: "throughput_score", value: 12.5 }).valueText).toBe("12.5");
  });
});

describe("phase score column", () => {
  it("shows Throughput@Size for throughput rows and Power@Size otherwise, never the other metric", () => {
    expect(phaseScoreValue({ test_type: "throughput", power_score: 10, throughput_at_size: 3741 })).toBe(3741);
    expect(phaseScoreValue({ test_type: "throughput", power_score: 10, throughput_at_size: null })).toBeNull();
    expect(phaseScoreValue({ phase: "throughput", power_score: null, throughput_at_size: 5 })).toBe(5);
    expect(phaseScoreValue({ test_type: "power", power_score: 10, throughput_at_size: 3741 })).toBe(10);
    expect(phaseScoreValue({ test_type: "standard", power_score: null, throughput_at_size: 3741 })).toBeNull();
  });
});

describe("stream count facet", () => {
  it("round-trips through the streams URL parameter", () => {
    expect(readFacetParam(new URLSearchParams("streams=2,3"), "stream_count")).toEqual(["2", "3"]);
    expect(readFacetParam(new URLSearchParams(""), "stream_count")).toEqual([]);
  });

  it("filters results on the stream_count column", () => {
    const { sql, params } = facetsToWhereClause({ stream_count: ["3"] });

    expect(sql).toBe("WHERE stream_count IN (?)");
    expect(params).toEqual([3]);
    expect(facetsToWhereClause({}).sql).toBe("");
  });

  it("matches rows by stream count and never matches rows without one", () => {
    const facets = normalizeFacetState({ stream_count: ["3"] });

    expect(matchesFacetRow({ stream_count: 3 }, facets, { keys: ["stream_count"] })).toBe(true);
    expect(matchesFacetRow({ stream_count: 2 }, facets, { keys: ["stream_count"] })).toBe(false);
    expect(matchesFacetRow({ stream_count: null }, facets, { keys: ["stream_count"] })).toBe(false);
    expect(matchesFacetRow({ stream_count: null }, normalizeFacetState({}), { keys: ["stream_count"] })).toBe(true);
  });

  it("carries the stream count into drill-down links", () => {
    const params = new URLSearchParams();

    appendFacetParams(params, normalizeFacetState({ stream_count: ["3"] }), new Set());

    expect(params.get("streams")).toBe("3");
  });

  it("labels stream counts and phases", () => {
    expect(formatFacetDisplayValue("stream_count", "3")).toBe("3 streams");
    expect(formatFacetDisplayValue("stream_count", "1")).toBe("1 stream");
    expect(formatPhaseWithStreams("throughput", 3)).toBe("throughput (3 streams)");
    expect(formatPhaseWithStreams("throughput", 1)).toBe("throughput (1 stream)");
    expect(formatPhaseWithStreams("power", null)).toBe("power");
    expect(formatPhaseWithStreams("power", undefined)).toBe("power");
  });
});

describe("compare cohort stream count", () => {
  const signature = compareCohortSignatureForRow({
    benchmark: "tpch",
    scale_factor: 1,
    phase: "throughput",
    stream_count: 3,
  });

  it("treats runs with a different stream count as a different cohort", () => {
    const other = { benchmark: "tpch", scale_factor: 1, phase: "throughput", stream_count: 2 };

    expect(compareCohortMismatches(other, signature)).toEqual(["streams"]);
    expect(compareCohortMismatches({ ...other, stream_count: 3 }, signature)).toEqual([]);
  });

  it("does not add a stream requirement to cohorts without one", () => {
    const power = compareCohortSignatureForRow({ benchmark: "tpch", scale_factor: 1, phase: "power" });

    expect(power.streamCount).toBe("");
    expect(
      compareCohortMismatches({ benchmark: "tpch", scale_factor: 1, phase: "power" }, power),
    ).toEqual([]);
    expect(compareCohortSummary(signature)).toContain("3 streams");
    expect(compareCohortSummary(power)).not.toContain("streams");
  });
});

describe("compare decision summary on Throughput@Size", () => {
  it("names the metric and leads with the higher score", () => {
    const summary = buildCompareDecisionSummary(
      [
        detailResult({ result_id: "spark", platform: "Spark", throughput_at_size: 3741 }),
        detailResult({ result_id: "duck", platform: "DuckDB", throughput_at_size: 1500 }),
      ],
      "throughput_at_size",
    );

    expect(summary.primaryMetricLabel).toBe("Throughput@Size");
    expect(summary.winner?.resultId).toBe("spark");
    expect(summary.headline).toContain("Throughput@Size");
    expect(summary.headline).toContain("better than the lowest selected run");
  });
});

describe("local throughput import", () => {
  function throughputBundle(overrides: Record<string, unknown> = {}) {
    return {
      version: "2.2",
      run: { id: "run", timestamp: "2026-10-03T18:42:47Z", total_duration_ms: 119000 },
      benchmark: { id: "tpch", name: "TPC-H", scale_factor: 1, test_type: "throughput" },
      platform: { name: "Spark", version: "4.2.0" },
      config: { mode: "sql" },
      summary: {
        queries: { total: 4, passed: 4, failed: 0 },
        validation: "passed",
        tpc_metrics: { throughput_at_size: 3741.26 },
      },
      phases: {
        power_test: { status: "NOT_RUN" },
        throughput_test: {
          status: "COMPLETED",
          stream_results: [
            { stream_id: 1, success: true },
            { stream_id: 2, success: true },
            { stream_id: 3, success: true },
          ],
        },
      },
      environment: {},
      queries: [
        { id: "1", ms: 10, iter: 1, run_type: "measurement", status: "SUCCESS", stream: 1 },
        { id: "2", ms: 20, iter: 1, run_type: "measurement", status: "SUCCESS", stream: 1 },
      ],
      ...overrides,
    };
  }

  it("withholds the local throughput score when run.streams disagrees with stream_results", async () => {
    const preview = await parseLocalResultText(JSON.stringify(throughputBundle({
      run: { id: "run", timestamp: "2026-10-03T18:42:47Z", total_duration_ms: 119000, streams: 4 },
    })), "tp.json");

    expect(preview.detail.throughput_at_size).toBeNull();
    expect(preview.primaryMetric).toBe("throughput_at_size");
  });

  it("keeps the local throughput score when run.streams is absent", async () => {
    const preview = await parseLocalResultText(JSON.stringify(throughputBundle()), "tp.json");

    expect(preview.detail.throughput_at_size).toBe(3741.26);
  });

  it("uses config.streams when run.streams is absent", async () => {
    const preview = await parseLocalResultText(JSON.stringify(throughputBundle({
      run: { id: "run", timestamp: "2026-10-03T18:42:47Z", total_duration_ms: 119000 },
      config: { mode: "sql", streams: 4 },
    })), "tp.json");

    expect(preview.detail.throughput_at_size).toBeNull();
  });

  it("reads Throughput@Size and the stream count and ranks on the throughput metric", async () => {
    const preview = await parseLocalResultText(JSON.stringify(throughputBundle()), "tp.json");

    expect(preview.detail.throughput_at_size).toBe(3741.26);
    expect(preview.detail.stream_count).toBe(3);
    expect(preview.detail.power_score).toBeNull();
    expect(preview.primaryMetric).toBe("throughput_at_size");
  });

  it("treats a timed-out stream as having no Throughput@Size even when the bundle carries a score", async () => {
    const bundle = throughputBundle({
      summary: {
        queries: { total: 4, passed: 4, failed: 0 },
        validation: "passed",
        tpc_metrics: { throughput_at_size: 500 },
      },
      phases: {
        power_test: { status: "NOT_RUN" },
        throughput_test: {
          status: "FAILED",
          stream_results: [
            { stream_id: 1, success: true },
            { stream_id: 2, success: true },
          ],
          errors: ["Stream 3 timed out after 600s"],
        },
      },
    });
    const preview = await parseLocalResultText(JSON.stringify(bundle), "tp.json");

    expect(preview.detail.throughput_at_size).toBeNull();
    expect(preview.detail.stream_count).toBe(2);
    expect(preview.primaryMetric).toBe("throughput_at_size");
  });

  it.each([
    ["a failed status", { status: "FAILED" }],
    ["an unsuccessful stream", { stream_results: [{ stream_id: 1, success: false }] }],
    ["a non-boolean success", { stream_results: [{ stream_id: 1, success: "true" }] }],
    ["no stream results", { stream_results: [] }],
    ["phase errors", { errors: ["boom"] }],
    ["outstanding work", { outstanding_work: { stream_ids: [3], cleanup_state: "outstanding" } }],
  ])("drops the score for %s", async (_label, change) => {
    const base = throughputBundle() as { phases: { throughput_test: Record<string, unknown> } };
    base.phases.throughput_test = { ...base.phases.throughput_test, ...change };
    const preview = await parseLocalResultText(JSON.stringify(base), "tp.json");

    expect(preview.detail.throughput_at_size).toBeNull();
  });

  it("keeps the score when the error fields are present but empty", async () => {
    const base = throughputBundle() as { phases: { throughput_test: Record<string, unknown> } };
    base.phases.throughput_test = { ...base.phases.throughput_test, errors: [], outstanding_work: null };
    const preview = await parseLocalResultText(JSON.stringify(base), "tp.json");

    expect(preview.detail.throughput_at_size).toBe(3741.26);
  });

  it("reports a missing score as the throughput metric rather than falling back to geomean", async () => {
    const bundle = throughputBundle({
      summary: { queries: { total: 4, passed: 4, failed: 0 }, validation: "passed", tpc_metrics: {} },
    });
    const preview = await parseLocalResultText(JSON.stringify(bundle), "tp.json");

    expect(preview.detail.throughput_at_size).toBeNull();
    expect(preview.primaryMetric).toBe("throughput_at_size");
  });

  it("does not assign a stream count to power runs", async () => {
    const bundle = throughputBundle({
      benchmark: { id: "tpch", name: "TPC-H", scale_factor: 1, test_type: "power" },
      summary: {
        queries: { total: 4, passed: 4, failed: 0 },
        validation: "passed",
        tpc_metrics: { power_at_size: 1234 },
      },
    });
    const preview = await parseLocalResultText(JSON.stringify(bundle), "power.json");

    expect(preview.detail.stream_count).toBeNull();
    expect(preview.primaryMetric).toBe("power_score");
  });
});
