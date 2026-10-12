import { describe, expect, it } from "vitest";
import type { QueryExecutionRow } from "@/lib/duckdbQueries";
import { DEFAULT_BASIS, warmPass } from "@/lib/measurementBasis";
import {
  MAX_QUERY_COMPARISON_RUNS,
  parseQueryRunSelection,
  queryRunComparisonHref,
  summarizeQueryRunExecutions,
} from "@/lib/queryRunComparison";

function execution(overrides: Partial<QueryExecutionRow> = {}): QueryExecutionRow {
  return {
    result_id: "run-a",
    query_id: "Q1",
    duration_ms: 10,
    status: "pass",
    run_type: "measurement",
    iter: 1,
    stream: null,
    ...overrides,
  };
}

describe("single-query run comparison URL state", () => {
  it("keeps selected run order and parses an optional iteration basis", () => {
    const selected = parseQueryRunSelection(
      "/results/compare/query/Q1?ids=run-b%2Crun-a&basis=warm_pass_2:min",
      "Q1",
    );

    expect(selected.requestedIds).toEqual(["run-b", "run-a"]);
    expect(selected.basis).toEqual({ passes: warmPass(2), statistic: "min" });
    expect(selected.basisNotice).toBeNull();
  });

  it("limits selections to ten and reports duplicate and overflow IDs", () => {
    const ids = Array.from({ length: 11 }, (_, index) => `run-${index + 1}`);
    const selected = parseQueryRunSelection(
      `/results/compare/query/Q1?ids=${encodeURIComponent([...ids.slice(0, 2), ids[0]!, ...ids.slice(2)].join(","))}`,
      "Q1",
    );

    expect(MAX_QUERY_COMPARISON_RUNS).toBe(10);
    expect(selected.requestedIds).toEqual(ids.slice(0, 10));
    expect(selected.duplicateIds).toEqual([ids[0]]);
    expect(selected.overflowIds).toEqual([ids[10]]);
  });

  it("builds a shareable URL without reordering IDs and omits the default basis", () => {
    expect(queryRunComparisonHref("Q1", ["run-b", "run-a"])).toBe(
      "/results/compare/query/Q1?ids=run-b%2Crun-a",
    );
    expect(queryRunComparisonHref("Q/1", ["run-a"], { passes: warmPass(2), statistic: "min" })).toBe(
      "/results/compare/query/Q%2F1?ids=run-a&basis=warm_pass_2%3Amin",
    );
    expect(parseQueryRunSelection("/results/compare/query/Q1?ids=run-a", "Q1").basis).toEqual(DEFAULT_BASIS);
  });
});

describe("query execution summaries", () => {
  it("uses successful measurement executions, excluding failures and warmups", () => {
    const summary = summarizeQueryRunExecutions([
      execution({ duration_ms: 10, iter: 1 }),
      execution({ duration_ms: 20, iter: 2 }),
      execution({ duration_ms: 100, run_type: "warmup", iter: 0 }),
      execution({ duration_ms: 30, status: "fail", iter: 3 }),
    ], "Q1");

    expect(summary.samples.map((sample) => [sample.iteration, sample.durationMs])).toEqual([[1, 10], [2, 20]]);
    expect(summary.medianMs).toBe(15);
    expect(summary.minMs).toBe(10);
    expect(summary.maxMs).toBe(20);
    expect(summary.sampleCount).toBe(2);
    expect(summary.iterationCount).toBe(2);
    expect(summary.measurementFailureCount).toBe(1);
    expect(summary.warmupPassCount).toBe(1);
  });

  it("uses legacy unlabelled rows only when no measurement-tagged row exists", () => {
    const legacyOnly = summarizeQueryRunExecutions([
      execution({ run_type: null, duration_ms: 12, iter: 1 }),
      execution({ run_type: null, duration_ms: 14, iter: 2 }),
    ], "Q1");
    const mixed = summarizeQueryRunExecutions([
      execution({ run_type: "measurement", duration_ms: 12, iter: 1 }),
      execution({ run_type: null, duration_ms: 14, iter: 2 }),
    ], "Q1");
    const failedMeasurement = summarizeQueryRunExecutions([
      execution({ run_type: "measurement", status: "fail", duration_ms: 12, iter: 1 }),
      execution({ run_type: null, duration_ms: 14, iter: 2 }),
    ], "Q1");

    expect(legacyOnly.samples.map((sample) => sample.durationMs)).toEqual([12, 14]);
    expect(legacyOnly.samples.every((sample) => sample.legacy)).toBe(true);
    expect(mixed.samples.map((sample) => sample.durationMs)).toEqual([12]);
    expect(mixed.ignoredLegacyCount).toBe(1);
    expect(mixed.legacyFallback).toBe(false);
    expect(failedMeasurement.samples).toEqual([]);
    expect(failedMeasurement.measurementFailureCount).toBe(1);
    expect(failedMeasurement.ignoredLegacyCount).toBe(1);
  });

  it("keeps every iteration in the spread while calculating the selected basis value separately", () => {
    const selected = parseQueryRunSelection(
      "/results/compare/query/Q1?ids=run-a&basis=warm_pass_2",
      "Q1",
    );
    const summary = summarizeQueryRunExecutions([
      execution({ duration_ms: 10, iter: 1 }),
      execution({ duration_ms: 90, iter: 2 }),
      execution({ duration_ms: 100, iter: 2, stream: 1 }),
      execution({ duration_ms: 50, run_type: "warmup", iter: 0 }),
    ], "Q1", selected.basis);

    expect(selected.basis).toEqual({ passes: warmPass(2), statistic: "median" });
    expect(summary.samples.map((sample) => sample.durationMs)).toEqual([10, 90, 100]);
    expect(summary.medianMs).toBe(90);
    expect(summary.minMs).toBe(10);
    expect(summary.maxMs).toBe(100);
    expect(summary.selectedValueMs).toBe(95);
    expect(summary.basisActive).toBe(true);
    expect(summary.displayValueMs).toBe(95);
    expect(summary.displayMinMs).toBe(90);
    expect(summary.displayMaxMs).toBe(100);
    expect(summary.displaySamples.map((sample) => sample.durationMs)).toEqual([90, 100]);
    expect(summary.sampleCount).toBe(3);
    expect(summary.iterationCount).toBe(2);
    expect(summary.warmupPassCount).toBe(1);
  });

  it("leads with the all-iteration median when the default basis is active", () => {
    const summary = summarizeQueryRunExecutions([
      execution({ duration_ms: 10, iter: 1 }),
      execution({ duration_ms: 90, iter: 2 }),
      execution({ duration_ms: 100, iter: 2, stream: 1 }),
    ], "Q1");

    expect(summary.basisActive).toBe(false);
    expect(summary.displayValueMs).toBe(summary.medianMs);
    expect(summary.displayMinMs).toBe(summary.minMs);
    expect(summary.displayMaxMs).toBe(summary.maxMs);
    expect(summary.displaySamples).toEqual(summary.samples);
  });

  it("keeps invalid durations out of statistics and reports missing query records", () => {
    const invalid = summarizeQueryRunExecutions([
      execution({ duration_ms: 0, iter: 1 }),
      execution({ duration_ms: Number.NaN, iter: 2 }),
    ], "Q1");
    const missing = summarizeQueryRunExecutions([execution({ query_id: "Q2", duration_ms: 10, iter: 1 })], "Q1");

    expect(invalid.samples).toEqual([]);
    expect(invalid.invalidMeasurementTimingCount).toBe(2);
    expect(invalid.medianMs).toBeNull();
    expect(missing.queryRecordCount).toBe(0);
  });
});
