import type { StatusTone } from "@/components/StatusBadge";
import { humanizeBenchmark } from "@/utils";

const TRUST_LABEL_LABELS: Record<string, string> = {
  "maintainer-run": "Maintainer run",
  "community-submission": "Community submission",
  "local-run": "Local run",
};

const VALIDATION_STATUS_LABELS: Record<string, string> = {
  passed: "Passed",
  failed: "Failed",
  warning: "Warning",
  not_applicable: "Not applicable",
  pending: "Pending",
  interrupted: "Interrupted",
  partial: "Partial pass",
  error: "Validation error",
  not_run: "No validation",
  not_validated: "Not validated",
  uncertain: "Uncertain",
  unknown: "Unknown",
};

const VALIDATION_STATUS_DESCRIPTIONS: Record<string, string> = {
  passed: "BenchBox checked this result against the expected answers and found no differences.",
  failed: "Validation ran and found this result incorrect.",
  warning: "Validation ran and flagged a concern short of a hard failure.",
  not_applicable: "Validation does not apply to this result.",
  pending: "Validation has not completed yet.",
  interrupted: "The run was interrupted before validation could complete.",
  partial: "Some queries failed validation; this is a partial pass.",
  error: "The validation process itself errored and produced no verdict.",
  not_run: "Validation was not run for this result. Its measurements have not been checked against the expected answers.",
  not_validated: "This result was not checked against the expected answers.",
  uncertain: "Validation completed with reduced confidence; treat this result with caution.",
  unknown: "Validation status was not recorded for this result.",
};

const VALIDATION_CLI_FAILURE_STATUSES = new Set(["failed", "interrupted", "partial", "error"]);

const VALIDATION_CLEAN_STATUSES = new Set(["passed", "pass", "exact", "full"]);

export interface ValidationStatusInfo {
  status: string | null;
  label: string;
  description: string;
  tone: StatusTone;
  isClean: boolean;
}

export function describeValidationStatus(raw: string | null | undefined): ValidationStatusInfo {
  const status = raw === null || raw === undefined ? null : raw.trim().toLowerCase() || null;
  if (status === null) {
    return {
      status: null,
      label: "Not recorded",
      description: "No validation status was recorded for this result.",
      tone: "neutral",
      isClean: false,
    };
  }
  const label = VALIDATION_STATUS_LABELS[status] ?? sentenceCase(formatEnumLabel(status));
  const description = VALIDATION_STATUS_DESCRIPTIONS[status] ?? "No further detail is defined for this status.";
  const isClean = VALIDATION_CLEAN_STATUSES.has(status);
  let tone: StatusTone;
  if (isClean) {
    tone = "info";
  } else if (status === "loose" || status === "range") {
    tone = "warning";
  } else if (VALIDATION_CLI_FAILURE_STATUSES.has(status)) {
    tone = "danger";
  } else {
    tone = "warning";
  }
  return { status, label, description, tone, isClean };
}

const VISIBILITY_LABELS: Record<string, string> = {
  "public-curated": "Published, maintainer reviewed",
  "public-community": "Published, community submitted",
  "local-preview": "Local preview",
  internal: "Not public",
};

const FUNDING_LABELS: Record<string, string> = {
  employer: "employer funded",
  personal: "personally funded",
  "free-trial": "free trial",
  "vendor-sponsored": "vendor sponsored",
  grant: "grant funded",
  unspecified: "No funding information provided",
};

const COST_STATUS_LABELS: Record<string, string> = {
  normalized: "normalized",
  not_applicable: "not applicable",
  not_applicable_local: "not applicable (local)",
  unavailable: "unavailable",
};

export function formatTrustLabel(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return "unknown";
  return TRUST_LABEL_LABELS[raw] ?? formatEnumLabel(raw);
}

export function formatFunding(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return FUNDING_LABELS.unspecified!;
  return FUNDING_LABELS[raw] ?? formatEnumLabel(raw);
}

export function formatValidationStatus(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return "Unknown";
  return describeValidationStatus(raw).label;
}

export function parseOverrideRules(raw: string | null | undefined): string[] {
  if (raw === null || raw === undefined || raw === "") return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((entry): entry is string => typeof entry === "string" && entry !== "");
  } catch {
    return [];
  }
}

export interface OverrideBadgeInfo {
  rules: string[];
  label: string;
  fullLabel: string;
  title: string;
}

export function describeOverride(
  rules: string[] | null | undefined,
  opts?: { approver?: string | null; expires?: string | null },
): OverrideBadgeInfo | null {
  if (!rules || rules.length === 0) return null;
  const approver = opts?.approver?.trim() ? ` Approved by ${opts.approver!.trim()}.` : "";
  const expires = opts?.expires?.trim() ? ` Override expires ${opts.expires!.trim()}.` : "";
  return {
    rules,
    label: sentenceCase(rules.join(", ")),
    fullLabel: `Overridden: ${rules.join(", ")}`,
    title:
      `This result was accepted under a committed plausibility override covering ${rules.join(", ")}.` +
      `${approver}${expires} It is not a clean pass.`,
  };
}

export function isValidationNotClean(raw: string | null | undefined): boolean {
  return (raw?.trim().toLowerCase() ?? "") !== "passed";
}

export function formatVisibility(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return "unknown";
  return VISIBILITY_LABELS[raw] ?? formatEnumLabel(raw);
}

export function formatCostStatus(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return "unknown";
  return COST_STATUS_LABELS[raw] ?? formatEnumLabel(raw);
}

export function formatEnumLabel(raw: string): string {
  return raw.replace(/[_-]+/g, " ").trim();
}

function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function formatArchitecture(raw: string): string {
  const normalized = raw.trim().toLowerCase();
  if (normalized === "x86_64" || normalized === "amd64") return "x86-64";
  if (normalized === "arm64" || normalized === "aarch64") return "Arm64";
  return formatEnumLabel(raw);
}

const CPU_FAMILY_LABELS: Record<string, string> = {
  amd_epyc: "AMD EPYC",
  intel_xeon: "Intel Xeon",
  apple_silicon: "Apple silicon",
};

export function formatCpuFamily(raw: string): string {
  const normalized = raw.trim().toLowerCase();
  return CPU_FAMILY_LABELS[normalized] ?? formatEnumLabel(raw);
}

export function formatExecutionMode(raw: string): string {
  const normalized = raw.trim().toLowerCase();
  if (normalized === "sql") return "SQL";
  if (normalized === "dataframe") return "DataFrame";
  return formatEnumLabel(raw);
}

export function formatTuningMode(raw: string): string {
  const normalized = raw.trim().toLowerCase();
  const labels: Record<string, string> = {
    notuning: "No tuning",
    tuned: "Tuned",
    "tuned-fallback": "Tuned with fallback settings",
    auto: "Automatic tuning",
    custom: "Custom tuning",
  };
  return labels[normalized] ?? formatEnumLabel(raw);
}

export function formatMemoryGb(value: number): string {
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value)} GB`;
}

export function canonicalBenchmarkSlug(raw: string): string {
  const normalized = raw.trim().toLowerCase();
  return normalized === "star_schema" ? "ssb" : normalized;
}

export function canonicalPhase(raw: string | null | undefined): string {
  const normalized = (raw ?? "").trim().toLowerCase();
  if (!normalized) return "unknown";
  return normalized === "standard" ? "power" : normalized;
}

export function formatPhaseWithStreams(phase: string, streamCount: number | null | undefined): string {
  if (streamCount === null || streamCount === undefined) return phase;
  return `${phase} (${streamCount} ${streamCount === 1 ? "stream" : "streams"})`;
}

export function formatBenchmarkLabel(slug: string): string {
  if (slug === "star_schema") return "SSB (historical source)";
  return humanizeBenchmark(slug);
}
