import { describe, expect, it } from "vitest";
import {
  formatAverageRank,
  formatCompactLatencyMs,
  formatCoverage,
  formatDurationSeconds,
  formatLatencyMs,
  formatMetricValue,
  formatPercentChange,
  formatPowerScore,
  formatPowerScoreExact,
  formatRank,
  formatRatio,
  formatScaleFactor,
  formatSpeedup,
  formatUsd,
} from "@/lib/metricFormatters";

function formatPlainNumberMissing(): string {
  return formatMetricValue({ type: "plain_number", value: null }).valueText;
}

describe("metric formatters", () => {
  it("formats ratios with a multiplication sign and significant-digit precision", () => {
    expect(formatRatio(1)).toBe("1×");
    expect(formatRatio(1.0001)).toBe("1×");
    expect(formatRatio(2.05)).toBe("2.05×");
    expect(formatRatio(4.5)).toBe("4.5×");
    expect(formatRatio(41.46)).toBe("41×");
    expect(formatRatio(8892.6)).toBe("8,893×");
    expect(formatRatio(0.03)).toBe("0.03×");
    expect(formatRatio(0.2)).toBe("0.2×");
    expect(formatRatio(null)).toBe("—");
    expect(formatRatio(undefined, { missingText: "n/a" })).toBe("n/a");
  });

  it("formats signed percentage changes with a true minus sign and no sign at zero", () => {
    expect(formatPercentChange(12.34)).toBe("+12.3%");
    expect(formatPercentChange(-4.5)).toBe("−4.5%");
    expect(formatPercentChange(0)).toBe("0.0%");
    expect(formatPercentChange(-0.04)).toBe("0.0%");
    expect(formatPercentChange(null)).toBe("—");
  });

  it("formats scale factors with a space and no trailing zeros", () => {
    expect(formatScaleFactor(10)).toBe("SF 10");
    expect(formatScaleFactor("10.0")).toBe("SF 10");
    expect(formatScaleFactor(0.01)).toBe("SF 0.01");
    expect(formatScaleFactor("0.1")).toBe("SF 0.1");
    expect(formatScaleFactor("")).toBe("—");
    expect(formatScaleFactor(null)).toBe("—");
  });

  it("uses the shared missing-value glyph for absent numeric values", () => {
    expect(formatLatencyMs(null).valueText).toBe("—");
    expect(formatPowerScore(undefined).valueText).toBe("—");
    expect(formatSpeedup(null).valueText).toBe("—");
    expect(formatPlainNumberMissing()).toBe("—");
    expect(formatCompactLatencyMs(null)).toBe("—");
  });

  it("keeps the compact sub-millisecond form for table cells", () => {
    expect(formatCompactLatencyMs(0.42)).toBe("<1 ms");
    expect(formatCompactLatencyMs(12.4)).toBe("12 ms");
    expect(formatCompactLatencyMs(1200)).toBe("1.2 s");
  });

  it("formats large power scores compactly without losing exact tooltip text", () => {
    expect(formatPowerScore(3000.42).valueText).toBe("3,000");
    expect(formatPowerScore(1520.99).valueText).toBe("1,521");
    expect(formatPowerScore(11636.652993864305).valueText).toBe("11,637");
    expect(formatPowerScoreExact(3000.42).valueText).toBe("3,000.42");
    expect(formatPowerScoreExact(11636.652993864305).valueText).toBe("11,636.652993864305");
    expect(formatPowerScore(3000.42).sortValue).toBe(3000.42);
  });

  it("keeps latency in milliseconds below 1000 ms and seconds above the threshold", () => {
    expect(formatLatencyMs(0.42).valueText).toBe("0.4 ms");
    expect(formatLatencyMs(0.42, { subMillisecond: "compact" }).valueText).toBe("<1 ms");
    expect(formatLatencyMs(999.4).valueText).toBe("999 ms");
    expect(formatLatencyMs(1200).valueText).toBe("1.2 s");
  });

  it("formats duration seconds separately from millisecond latency", () => {
    expect(formatDurationSeconds(0.0049).valueText).toBe("0.0049 s");
    expect(formatDurationSeconds(12.345).valueText).toBe("12.35 s");
    expect(formatDurationSeconds(null).valueText).toBe("No duration recorded");
  });

  it("formats speedups, ranks, and coverage with explicit denominators", () => {
    expect(formatSpeedup(1.589).valueText).toBe("1.59×");
    expect(formatSpeedup(1).valueText).toBe("1×");
    expect(formatRank(2, 11).valueText).toBe("2/11");
    expect(formatAverageRank(1).valueText).toBe("1.0");
    expect(formatCoverage(3, 5, { unitLabel: "cohorts" }).valueText).toBe("3/5 cohorts");
  });

  it("uses magnitude-aware currency precision", () => {
    expect(formatUsd(12.5).valueText).toBe("$12.50");
    expect(formatUsd(0.0049).valueText).toBe("$0.0049");
    expect(formatUsd(1234.56).valueText).toBe("$1,235");
  });

  it("returns a common contract for semantic formatter dispatch", () => {
    expect(formatMetricValue({ type: "latency_ms", value: 42 }).accessibleText).toBe("42 ms latency");
    expect(formatMetricValue({ type: "rank", rank: null }).sortValue).toBeNull();
  });
});
