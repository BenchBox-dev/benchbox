import { describe, expect, it } from "vitest";
import {
  formatArchitecture,
  formatCpuFamily,
  formatBenchmarkLabel,
  canonicalBenchmarkSlug,
  canonicalPhase,
  describeOverride,
  describeValidationStatus,
  parseOverrideRules,
  formatCostStatus,
  formatEnumLabel,
  formatFunding,
  formatTrustLabel,
  formatValidationStatus,
  formatVisibility,
} from "@/lib/displayLabels";

describe("formatTrustLabel", () => {
  it("humanizes the canonical trust labels", () => {
    expect(formatTrustLabel("maintainer-run")).toBe("Maintainer run");
    expect(formatTrustLabel("community-submission")).toBe("Community submission");
  });

  it("returns 'unknown' for null/empty values", () => {
    expect(formatTrustLabel(null)).toBe("unknown");
    expect(formatTrustLabel("")).toBe("unknown");
    expect(formatTrustLabel(undefined)).toBe("unknown");
  });

  it("falls back to underscore/dash humanization for unknown values", () => {
    expect(formatTrustLabel("future_tier")).toBe("future tier");
    expect(formatTrustLabel("some-new-tier")).toBe("some new tier");
  });
});

describe("formatFunding", () => {
  it("humanizes every canonical funding source", () => {
    expect(formatFunding("employer")).toBe("employer funded");
    expect(formatFunding("personal")).toBe("personally funded");
    expect(formatFunding("free-trial")).toBe("free trial");
    expect(formatFunding("vendor-sponsored")).toBe("vendor sponsored");
    expect(formatFunding("grant")).toBe("grant funded");
    expect(formatFunding("unspecified")).toBe("No funding information provided");
  });

  it("returns 'unspecified' for null/empty values", () => {
    expect(formatFunding(null)).toBe("No funding information provided");
    expect(formatFunding("")).toBe("No funding information provided");
    expect(formatFunding(undefined)).toBe("No funding information provided");
  });

  it("falls back to underscore/dash humanization for unknown values", () => {
    expect(formatFunding("crowd_funded")).toBe("crowd funded");
    expect(formatFunding("some-new-source")).toBe("some new source");
  });
});

describe("formatValidationStatus", () => {
  it("humanizes known statuses including not_applicable", () => {
    expect(formatValidationStatus("passed")).toBe("Passed");
    expect(formatValidationStatus("not_applicable")).toBe("Not applicable");
  });

  it("returns 'Unknown' for missing values", () => {
    expect(formatValidationStatus(null)).toBe("Unknown");
  });

  it("humanizes every NON_CLEAN_VALIDATION_STATUSES value", () => {
    expect(formatValidationStatus("failed")).toBe("Failed");
    expect(formatValidationStatus("interrupted")).toBe("Interrupted");
    expect(formatValidationStatus("partial")).toBe("Partial pass");
    expect(formatValidationStatus("error")).toBe("Validation error");
    expect(formatValidationStatus("not_run")).toBe("No validation");
    expect(formatValidationStatus("not_validated")).toBe("Not validated");
    expect(formatValidationStatus("uncertain")).toBe("Uncertain");
    expect(formatValidationStatus("unknown")).toBe("Unknown");
  });

  it("sentence-cases the label without changing the raw status it describes", () => {
    const info = describeValidationStatus("some_future_status");
    expect(info.label).toBe("Some future status");
    expect(info.status).toBe("some_future_status");
  });
});

describe("describeValidationStatus", () => {
  it("returns the raw status alongside the reader-facing label", () => {
    const info = describeValidationStatus("not_run");
    expect(info.status).toBe("not_run");
    expect(info.label).toBe("No validation");
    expect(info.description.length).toBeGreaterThan(0);
    expect(info.isClean).toBe(false);
  });

  it("marks passed (and its aliases) as the only clean status", () => {
    expect(describeValidationStatus("passed").isClean).toBe(true);
    expect(describeValidationStatus("pass").isClean).toBe(true);
    expect(describeValidationStatus("exact").isClean).toBe(true);
    expect(describeValidationStatus("full").isClean).toBe(true);
    expect(describeValidationStatus("not_run").isClean).toBe(false);
    expect(describeValidationStatus("failed").isClean).toBe(false);
  });

  it("gives CLI-failure statuses a danger tone", () => {
    for (const status of ["failed", "interrupted", "partial", "error"]) {
      expect(describeValidationStatus(status).tone).toBe("danger");
    }
  });

  it("gives never-validated statuses a warning tone, never neutral", () => {
    for (const status of ["not_run", "not_validated", "uncertain", "unknown"]) {
      const info = describeValidationStatus(status);
      expect(info.tone).toBe("warning");
      expect(info.tone).not.toBe("neutral");
    }
  });

  it("normalizes case and whitespace before matching", () => {
    expect(describeValidationStatus("  Not_Run ").label).toBe("No validation");
  });

  it("handles a missing status without throwing", () => {
    const info = describeValidationStatus(null);
    expect(info.status).toBeNull();
    expect(info.label).toBe("Not recorded");
    expect(info.isClean).toBe(false);
    expect(info.tone).toBe("neutral");
  });

  it("never gives an unrecognised non-null status the neutral tone", () => {
    expect(describeValidationStatus("some_future_status").tone).toBe("warning");
  });
});

describe("formatVisibility", () => {
  it("turns internal slug into a public-readable label", () => {
    expect(formatVisibility("public-curated")).toBe("Published, maintainer reviewed");
    expect(formatVisibility("public-community")).toBe("Published, community submitted");
    expect(formatVisibility("internal")).toBe("Not public");
  });
});

describe("formatCostStatus", () => {
  it("formats common statuses", () => {
    expect(formatCostStatus("normalized")).toBe("normalized");
    expect(formatCostStatus("not_applicable_local")).toBe("not applicable (local)");
    expect(formatCostStatus("not_applicable")).toBe("not applicable");
    expect(formatCostStatus("unavailable")).toBe("unavailable");
  });
});

describe("formatEnumLabel", () => {
  it("replaces underscores and dashes with spaces", () => {
    expect(formatEnumLabel("foo_bar")).toBe("foo bar");
    expect(formatEnumLabel("foo-bar-baz")).toBe("foo bar baz");
    expect(formatEnumLabel("mixed_dash-and_underscore")).toBe("mixed dash and underscore");
  });
});

describe("formatBenchmarkLabel", () => {
  it("marks the historical star_schema source while keeping ssb canonical", () => {
    expect(formatBenchmarkLabel("star_schema")).toBe("SSB (historical source)");
    expect(formatBenchmarkLabel("ssb")).toBe("SSB");
    expect(formatBenchmarkLabel("star_schema")).not.toBe(formatBenchmarkLabel("ssb"));
  });

  it("canonicalizes aliases without changing raw evidence", () => {
    expect(canonicalBenchmarkSlug("star_schema")).toBe("ssb");
    expect(canonicalBenchmarkSlug(" SSB ")).toBe("ssb");
    expect(canonicalPhase(null)).toBe("unknown");
    expect(canonicalPhase(" POWER ")).toBe("power");
    expect(canonicalPhase(" Standard ")).toBe("power");
    expect(canonicalPhase("throughput")).toBe("throughput");
  });

  it("falls through to humanizeBenchmark for other slugs", () => {
    expect(formatBenchmarkLabel("tpch")).toBe("TPC-H");
    expect(formatBenchmarkLabel("clickbench")).toBe("ClickBench");
  });
});

describe("parseOverrideRules", () => {
  it("parses the canonical JSON array string", () => {
    expect(parseOverrideRules('["timing-plateau", "scale-invariant"]')).toEqual([
      "timing-plateau",
      "scale-invariant",
    ]);
  });

  it("returns [] for absent values", () => {
    expect(parseOverrideRules(null)).toEqual([]);
    expect(parseOverrideRules(undefined)).toEqual([]);
    expect(parseOverrideRules("")).toEqual([]);
  });

  it("never invents an override from unreadable values", () => {
    expect(parseOverrideRules("not json")).toEqual([]);
    expect(parseOverrideRules('{"rule": "timing-plateau"}')).toEqual([]);
    expect(parseOverrideRules("[1, 2]")).toEqual([]);
    expect(parseOverrideRules("timing-plateau")).toEqual([]);
  });
});

describe("describeOverride", () => {
  it("returns null when no override was accepted", () => {
    expect(describeOverride([])).toBeNull();
    expect(describeOverride(null)).toBeNull();
    expect(describeOverride(undefined)).toBeNull();
  });

  it("names the covered rules and the audit trail", () => {
    const info = describeOverride(["timing-plateau"], {
      approver: "reviewer",
      expires: "2099-01-01",
    });
    expect(info).not.toBeNull();
    expect(info!.label).toBe("Timing-plateau");
    expect(info!.fullLabel).toBe("Overridden: timing-plateau");
    expect(info!.title).toContain("timing-plateau");
    expect(info!.title).toContain("Approved by reviewer.");
    expect(info!.title).toContain("Override expires 2099-01-01.");
    expect(info!.title).toContain("not a clean pass");
  });

  it("shows a short sentence-case label and keeps the full label for multiple rules", () => {
    const info = describeOverride(["scale-invariant", "timing-plateau"]);
    expect(info!.label).toBe("Scale-invariant, timing-plateau");
    expect(info!.fullLabel).toBe("Overridden: scale-invariant, timing-plateau");
  });
});

describe("hardware display labels", () => {
  it("maps known CPU family enums to vendor labels", () => {
    expect(formatCpuFamily("apple_silicon")).toBe("Apple silicon");
    expect(formatCpuFamily("amd_epyc")).toBe("AMD EPYC");
    expect(formatCpuFamily("intel_xeon")).toBe("Intel Xeon");
  });

  it("falls back to the shared enum humanizer for unknown CPU families", () => {
    expect(formatCpuFamily("ampere_altra")).toBe("ampere altra");
  });

  it("maps architecture enums to readable names", () => {
    expect(formatArchitecture("x86_64")).toBe("x86-64");
    expect(formatArchitecture("amd64")).toBe("x86-64");
    expect(formatArchitecture("arm64")).toBe("Arm64");
  });
});
