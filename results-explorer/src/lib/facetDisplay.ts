import { formatScaleFactor } from "@/lib/metricFormatters";
import type { FacetKey } from "@/lib/facetModel";

const CLOUD_PROVIDER_LABELS: Record<string, string> = {
  amazon: "AWS",
  "amazon web services": "AWS",
  aws: "AWS",
  azure: "Azure",
  "microsoft azure": "Azure",
  gcp: "GCP",
  google: "GCP",
  "google cloud": "GCP",
  "google cloud platform": "GCP",
};

const DEPLOYMENT_LABELS: Record<string, string> = {
  cloud: "cloud",
  local: "local",
  unavailable: "unavailable",
};

const COST_STATUS_LABELS: Record<string, string> = {
  normalized: "normalized",
  not_applicable_local: "not applicable local",
  unavailable: "unavailable",
};

const CPU_FAMILY_LABELS: Record<string, string> = {
  apple_silicon: "Apple Silicon",
  graviton: "AWS Graviton",
  intel_xeon: "Intel Xeon",
  intel_core: "Intel Core",
  amd_epyc: "AMD EPYC",
  amd_ryzen: "AMD Ryzen",
  ampere_altra: "Ampere Altra",
  arm_neoverse: "ARM Neoverse",
  unknown: "Unknown CPU",
};

export function formatFacetDisplayValue(
  key: FacetKey | string,
  value: string,
  fallbackLabel?: string,
): string {
  const trimmed = value.trim();
  if (trimmed === "") return fallbackLabel ?? "unknown";

  if (key === "cloud_provider") {
    return CLOUD_PROVIDER_LABELS[normalizeLookup(trimmed)] ?? fallbackLabel ?? trimmed;
  }

  if (key === "deployment_class") {
    return DEPLOYMENT_LABELS[normalizeLookup(trimmed)] ?? fallbackLabel ?? readableToken(trimmed);
  }

  if (key === "cost_status") {
    return COST_STATUS_LABELS[trimmed.toLowerCase()] ?? fallbackLabel ?? readableToken(trimmed);
  }

  if (key === "cpu_family") {
    return CPU_FAMILY_LABELS[trimmed.toLowerCase()] ?? fallbackLabel ?? readableToken(trimmed);
  }

  if (key === "arch") {
    return trimmed;
  }

  if (key === "memory_gb") {
    return `${trimmed} GB`;
  }

  if (key === "stream_count") {
    return `${trimmed} ${trimmed === "1" ? "stream" : "streams"}`;
  }

  if (key === "date_window") {
    return trimmed === "all" ? "All time" : `Last ${trimmed}`;
  }

  if (key === "scale_factor") {
    return formatScaleFactor(trimmed);
  }

  return fallbackLabel ?? readableToken(trimmed);
}

function normalizeLookup(value: string): string {
  return value.toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
}

function readableToken(value: string): string {
  return value.replace(/_+/g, " ");
}
