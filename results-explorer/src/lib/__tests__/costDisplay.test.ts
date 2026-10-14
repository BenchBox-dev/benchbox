import { describe, it, expect } from "vitest";
import type { CostDeploymentFields } from "@/types";
import {
  costModelSummary,
  costScopeSummary,
  costStatusLabel,
  costStatusShortLabel,
  estimateProvenance,
  estimatedCostValue,
  hasCostEstimate,
  humanizeBillingFragment,
  normalizedCostLabel,
  normalizedCostShortLabel,
  normalizedCostValue,
  parseEstimateAssumptions,
} from "@/lib/costDisplay";

function makeRow(overrides: Partial<CostDeploymentFields> = {}): CostDeploymentFields {
  return {
    cost_status: null,
    cost_scope: null,
    cost_model_version: null,
    cost_model_source: null,
    normalized_cost_usd: null,
    billing_unit: null,
    pricing_region: null,
    deployment_class: null,
    cloud_provider: null,
    cloud_region: null,
    instance_or_warehouse: null,
    storage_format: null,
    ...overrides,
  } as CostDeploymentFields;
}

describe("humanizeBillingFragment", () => {
  it("returns null for null/undefined/empty/whitespace inputs", () => {
    expect(humanizeBillingFragment(null)).toBeNull();
    expect(humanizeBillingFragment(undefined)).toBeNull();
    expect(humanizeBillingFragment("")).toBeNull();
    expect(humanizeBillingFragment("   ")).toBeNull();
  });

  it("returns null for sentinel values (case-insensitive)", () => {
    expect(humanizeBillingFragment("not_applicable")).toBeNull();
    expect(humanizeBillingFragment("Not applicable")).toBeNull();
    expect(humanizeBillingFragment("unknown")).toBeNull();
    expect(humanizeBillingFragment("UNKNOWN")).toBeNull();
    expect(humanizeBillingFragment("none")).toBeNull();
    expect(humanizeBillingFragment("n/a")).toBeNull();
    expect(humanizeBillingFragment("N/A")).toBeNull();
    expect(humanizeBillingFragment("na")).toBeNull();
  });

  it("humanizes real values by replacing underscores with spaces", () => {
    expect(humanizeBillingFragment("per_query")).toBe("per query");
    expect(humanizeBillingFragment("us_west_2")).toBe("us west 2");
    expect(humanizeBillingFragment("EU-Frankfurt")).toBe("EU-Frankfurt");
  });
});

describe("costScopeSummary", () => {
  it("renders just the cost scope when billing and region are sentinel", () => {
    const summary = costScopeSummary(
      makeRow({ cost_scope: "compute_only", billing_unit: "not_applicable", pricing_region: "not_applicable" }),
    );
    expect(summary).toBe("compute only");
    expect(summary).not.toContain("not_applicable");
    expect(summary).not.toContain("billing:");
    expect(summary).not.toContain("region:");
  });

  it("renders billing and region fragments when they carry real values", () => {
    const summary = costScopeSummary(
      makeRow({ cost_scope: "compute_only", billing_unit: "per_query", pricing_region: "us-west-2" }),
    );
    expect(summary).toBe("compute only, Billing unit: per query, Region: us-west-2");
  });

  it("renders only the present fragment when one side is sentinel and the other is real", () => {
    const summary = costScopeSummary(
      makeRow({ cost_scope: "compute_only", billing_unit: "per_hour", pricing_region: "Unknown" }),
    );
    expect(summary).toBe("compute only, Billing unit: per hour");
  });

  it("falls back to 'Not recorded' when cost_scope is missing and billing/region are sentinel", () => {
    const summary = costScopeSummary(
      makeRow({ cost_scope: null, billing_unit: "not_applicable", pricing_region: null }),
    );
    expect(summary).toBe("Not recorded");
  });
});

describe("short cost labels for table cells", () => {
  it("shortens the local-run label so it fits a narrow column", () => {
    expect(costStatusShortLabel(makeRow({ cost_status: "not_applicable_local" }))).toBe("N/A (local run)");
    expect(normalizedCostShortLabel(makeRow({ cost_status: "not_applicable_local" }))).toBe("N/A (local run)");
  });

  it("keeps 'Not recorded' for unavailable cost and sentinel statuses", () => {
    expect(costStatusShortLabel(makeRow({ cost_status: "unavailable" }))).toBe("Not recorded");
    expect(normalizedCostShortLabel(makeRow({ cost_status: "unavailable" }))).toBe("Not recorded");
    expect(normalizedCostShortLabel(makeRow({ cost_status: "not_applicable" }))).toBe("Not recorded");
  });

  it("keeps the dollar and estimate labels unchanged", () => {
    expect(normalizedCostShortLabel(makeRow({ cost_status: "normalized", normalized_cost_usd: 12.5 }))).toBe("$12.50");
    expect(normalizedCostShortLabel(makeRow({ cost_status: "estimated", estimated_cost_usd: 0.004667 }))).toBe(
      "≈ $0.0047 (est.)",
    );
  });

  it("leaves the long receipt label for local runs untouched", () => {
    expect(normalizedCostLabel(makeRow({ cost_status: "not_applicable_local" }))).toBe("Not applicable to this local run");
    expect(costStatusLabel(makeRow({ cost_status: "not_applicable_local" }))).toBe("Not applicable to this local run");
  });
});

describe("costStatusLabel and normalizedCost helpers (unaffected by w1)", () => {
  it("explains that cloud cost does not apply to a local run", () => {
    expect(costStatusLabel(makeRow({ cost_status: "not_applicable_local" }))).toBe("Not applicable to this local run");
  });

  it("returns the dollar value when normalized cost is finite", () => {
    expect(normalizedCostValue(makeRow({ cost_status: "normalized", normalized_cost_usd: 12.5 }))).toBe(12.5);
    expect(normalizedCostLabel(makeRow({ cost_status: "normalized", normalized_cost_usd: 12.5 }))).toBe("$12.50");
    expect(normalizedCostLabel(makeRow({ cost_status: "normalized", normalized_cost_usd: 0.0049 }))).toBe("$0.0049");
  });

  it("falls back to status label when normalized cost is missing", () => {
    expect(normalizedCostLabel(makeRow({ cost_status: "not_applicable_local" }))).toBe("Not applicable to this local run");
  });

  it("renders timing-derived estimates with an estimate marker", () => {
    expect(estimatedCostValue(makeRow({ cost_status: "estimated", estimated_cost_usd: 0.000105 }))).toBe(0.000105);
    expect(estimatedCostValue(makeRow({ cost_status: "normalized", normalized_cost_usd: 12.5 }))).toBeNull();
    expect(normalizedCostLabel(makeRow({ cost_status: "estimated", estimated_cost_usd: 0.000105 })))
      .toBe("\u2248 $0.0001 (est.)");
    expect(costStatusLabel(makeRow({ cost_status: "estimated" }))).toBe("Estimated cost (not metered)");
    expect(costStatusLabel(makeRow({ cost_status: "unavailable" }))).toBe("Not recorded");
  });

  it("does not expose the internal cost model source", () => {
    expect(costModelSummary(makeRow({ cost_model_version: "v3", cost_model_source: "ondemand" }))).toBe("v3");
    expect(costModelSummary(makeRow({ cost_model_version: "v3" }))).toBe("v3");
  });

  it("parses estimate assumptions from JSON with a plain-text fallback", () => {
    expect(parseEstimateAssumptions('["First.", "Second."]')).toEqual(["First.", "Second."]);
    expect(parseEstimateAssumptions("plain assumption")).toEqual(["plain assumption"]);
    expect(parseEstimateAssumptions(null)).toEqual([]);
    expect(parseEstimateAssumptions("  ")).toEqual([]);
    expect(hasCostEstimate(makeRow({ cost_status: "estimated", estimated_cost_usd: 0.5 }))).toBe(true);
    expect(hasCostEstimate(makeRow({ cost_status: "estimated", estimated_cost_usd: null }))).toBe(false);
    expect(hasCostEstimate(makeRow({ cost_status: "unavailable" }))).toBe(false);
  });
});

describe("estimateProvenance", () => {
  it("lists method, pricing date, source and assumptions for an estimated run", () => {
    const provenance = estimateProvenance(
      makeRow({
        cost_status: "estimated",
        estimated_cost_usd: 0.0047,
        cost_estimate_method: "sum of per-query median runtime hours × warehouse DBU per hour × USD per DBU",
        cost_estimate_source: "https://www.databricks.com/product/pricing/databricks-sql",
        cost_estimate_pricing_date: "2026-10-13",
        cost_estimate_assumptions: '["AWS SQL Serverless list rate is $0.70/DBU.", "Photon is included."]',
      }),
    );

    expect(provenance).toContain("Timing-derived estimate, not metered.");
    expect(provenance).toContain("Method: sum of per-query median runtime hours");
    expect(provenance).toContain("Pricing date: 2026-10-13");
    expect(provenance).toContain("Source: https://www.databricks.com/product/pricing/databricks-sql");
    expect(provenance).toContain("- AWS SQL Serverless list rate is $0.70/DBU.");
    expect(provenance).toContain("- Photon is included.");
  });

  it("returns null when the run has no estimate", () => {
    expect(estimateProvenance(makeRow({ cost_status: "normalized", normalized_cost_usd: 1 }))).toBeNull();
    expect(estimateProvenance(makeRow({ cost_status: "estimated", estimated_cost_usd: null }))).toBeNull();
  });

  it("labels a Databricks serverless estimate with the approximate dollar value", () => {
    expect(normalizedCostLabel(makeRow({ cost_status: "estimated", estimated_cost_usd: 0.004667 }))).toBe(
      "\u2248 $0.0047 (est.)",
    );
  });
});
