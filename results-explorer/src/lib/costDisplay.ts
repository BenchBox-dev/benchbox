import type { CostDeploymentFields } from "@/types";
import { formatUsd } from "@/lib/metricFormatters";

export function normalizedCostValue(row: CostDeploymentFields): number | null {
  if (row.cost_status !== "normalized") return null;
  const value = row.normalized_cost_usd;
  if (value === null || value === undefined) return null;
  return Number.isFinite(value) ? value : null;
}

export function estimatedCostValue(row: CostDeploymentFields): number | null {
  if (row.cost_status !== "estimated") return null;
  const value = row.estimated_cost_usd;
  if (value === null || value === undefined) return null;
  return Number.isFinite(value) ? value : null;
}

export function estimatedCostLabel(row: CostDeploymentFields): string | null {
  const value = estimatedCostValue(row);
  if (value === null) return null;
  return `\u2248 ${formatUsd(value).valueText} (est.)`;
}

export function hasCostEstimate(row: CostDeploymentFields): boolean {
  return estimatedCostValue(row) !== null;
}

export function parseEstimateAssumptions(value: string | null | undefined): string[] {
  if (value === null || value === undefined) return [];
  const trimmed = value.trim();
  if (trimmed === "") return [];
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (Array.isArray(parsed)) {
      return parsed.filter((entry): entry is string => typeof entry === "string" && entry.trim() !== "");
    }
  } catch {
  }
  return [trimmed];
}

export function estimateProvenance(row: CostDeploymentFields): string | null {
  if (!hasCostEstimate(row)) return null;
  const lines = [
    "Timing-derived estimate, not metered.",
    row.cost_estimate_method ? `Method: ${row.cost_estimate_method}` : null,
    row.cost_estimate_pricing_date ? `Pricing date: ${row.cost_estimate_pricing_date}` : null,
    row.cost_estimate_source ? `Source: ${row.cost_estimate_source}` : null,
    ...parseEstimateAssumptions(row.cost_estimate_assumptions).map((assumption) => `- ${assumption}`),
  ];
  return lines.filter((line): line is string => line !== null).join("\n");
}

export function normalizedCostLabel(row: CostDeploymentFields): string {
  const value = normalizedCostValue(row);
  if (value !== null) return formatUsd(value).valueText;
  return estimatedCostLabel(row) ?? costStatusLabel(row);
}

export function costStatusLabel(row: CostDeploymentFields): string {
  if (row.cost_status === "not_applicable_local") return "Not applicable to this local run";
  if (row.cost_status === "estimated") return "Estimated cost (not metered)";
  if (row.cost_status === "unavailable") return "Not recorded";
  return "Not recorded";
}

export function costStatusShortLabel(row: CostDeploymentFields): string {
  if (row.cost_status === "not_applicable_local") return "N/A (local run)";
  if (row.cost_status === "estimated") return "Estimated (not metered)";
  return "Not recorded";
}

export function normalizedCostShortLabel(row: CostDeploymentFields): string {
  const value = normalizedCostValue(row);
  if (value !== null) return formatUsd(value).valueText;
  return estimatedCostLabel(row) ?? costStatusShortLabel(row);
}

export function costModelDisclosure(rows: readonly CostDeploymentFields[]): string {
  const normalized = rows.filter((row) => normalizedCostValue(row) !== null);
  const versions = uniqueNonEmpty(normalized.map((row) => row.cost_model_version));
  const scopes = uniqueNonEmpty(normalized.map((row) => formatCostScope(row.cost_scope)));
  const versionText =
    versions.length === 1
      ? `model ${versions[0]}`
      : versions.length > 1
        ? `multiple models: ${versions.join(", ")}`
        : "model unavailable";
  const scopeText =
    scopes.length === 1
      ? scopes[0]
      : scopes.length > 1
        ? `mixed scopes: ${scopes.join(", ")}`
        : "scope unavailable";
  const estimateNote = rows.some((row) => estimatedCostValue(row) !== null)
    ? "; includes timing-derived estimates (not metered)"
    : "";
  return `Normalized USD, ${scopeText}, ${versionText}${estimateNote}`;
}

export function costModelSummary(row: CostDeploymentFields): string {
  return row.cost_model_version || "Not recorded";
}

export function costScopeSummary(row: CostDeploymentFields): string {
  const billing = humanizeBillingFragment(row.billing_unit);
  const region = humanizeBillingFragment(row.pricing_region);
  return [
    formatCostScope(row.cost_scope) ?? "Not recorded",
    billing !== null ? `Billing unit: ${billing}` : null,
    region !== null ? `Region: ${region}` : null,
  ].filter((part): part is string => part !== null).join(", ");
}

function formatCostScope(scope: string | null | undefined): string | null {
  if (!scope) return null;
  return scope.split("_").join(" ");
}

const BILLING_FRAGMENT_SENTINELS = new Set([
  "not_applicable",
  "not applicable",
  "unknown",
  "none",
  "n/a",
  "na",
]);

export function humanizeBillingFragment(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const trimmed = value.trim();
  if (trimmed === "") return null;
  if (BILLING_FRAGMENT_SENTINELS.has(trimmed.toLowerCase())) return null;
  return trimmed.split("_").join(" ");
}

function uniqueNonEmpty(values: Array<string | null | undefined>): string[] {
  return [...new Set(values.filter((value): value is string => Boolean(value)))];
}
