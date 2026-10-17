import { describe, expect, it } from "vitest";
import {
  complianceBadge,
  complianceExportCaption,
  exportColumnsWithCompliance,
  isSubscaleTpc,
  powerScoreTitle,
  subscaleCohortNote,
  subscaleTpcName,
  withComplianceCaption,
} from "@/lib/subscale";
import { complianceLabel } from "@/utils";
import { formatTimingExclusion } from "@/lib/displayEligibility";

describe("subscale labels", () => {
  it.each([
    ["tpch", 0.01, "TPC-H"],
    ["TPCDS", "0.1", "TPC-DS"],
    ["TPC-H", 0.01, "TPC-H"],
    ["tpch", 1, null],
    ["ssb", 0.01, null],
    ["tpch", null, null],
  ])("names %s SF %s as subscale TPC: %s", (benchmark, scaleFactor, expected) => {
    expect(subscaleTpcName(benchmark, scaleFactor)).toBe(expected);
    expect(isSubscaleTpc(benchmark, scaleFactor)).toBe(expected !== null);
  });

  it("states the cohort disclaimer", () => {
    expect(subscaleCohortNote("TPC-H")).toBe(
      "SF<1 is below TPC-H's minimum scale. Power@Size uses the TPC formula for comparison within this cohort only; not a TPC result.",
    );
  });

  it("labels compliance classes on rank rows", () => {
    expect(complianceBadge("unofficial_subscale")).toBe("Subscale · Unofficial");
    expect(complianceBadge("unofficial_nonstandard")).toBe("Non-standard · Unofficial");
    expect(complianceBadge("official")).toBeNull();
    expect(complianceLabel("unofficial_subscale")).toBe("Subscale · Unofficial");
    expect(complianceLabel("something_else")).toBe("Unofficial");
  });

  it("titles subscale Power@Size as unofficial", () => {
    expect(powerScoreTitle(true)).toBe("Power@Size (subscale, unofficial)");
    expect(powerScoreTitle(false)).toBe("Power@Size");
  });

  it("explains the non-standard ranking exclusion", () => {
    expect(formatTimingExclusion("unofficial_compliance")).toBe("Non-standard runs are unofficial and are not ranked.");
  });

  it("adds compliance class, power source and a caption to exports", () => {
    expect(exportColumnsWithCompliance(["platform", "power_score"])).toEqual([
      "platform",
      "power_score",
      "compliance_class",
      "power_score_source",
    ]);
    expect(exportColumnsWithCompliance(["platform", "compliance_class"])).toEqual(["platform", "compliance_class"]);
    expect(withComplianceCaption({ compliance_class: "unofficial_subscale" }).compliance_note).toContain(
      "Subscale · Unofficial",
    );
    expect(complianceExportCaption(null)).toBe("");
  });
});
