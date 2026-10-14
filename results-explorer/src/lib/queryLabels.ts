import { formatFacetDisplayValue } from "@/lib/facetDisplay";
import { formatDurationSeconds, formatLatencyMs, formatPlainNumber, formatPowerScore, formatThroughputScore, formatUsd, formatScaleFactor, MISSING_VALUE_GLYPH } from "@/lib/metricFormatters";
import { humanizeBenchmark } from "@/utils";
import { formatRunDateWithAge } from "@/lib/runAge";
import {
  formatArchitecture,
  formatExecutionMode,
  formatTrustLabel,
  formatTuningMode,
  formatValidationStatus,
  formatVisibility,
} from "@/lib/displayLabels";

const QUERY_ID_COLLATOR = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: "base",
});

export function compareQueryIds(a: string, b: string): number {
  return QUERY_ID_COLLATOR.compare(a, b) || a.localeCompare(b);
}

export function sortQueryIds(queryIds: readonly string[]): string[] {
  const seen = new Set<string>();
  const unique: string[] = [];
  for (const id of queryIds) {
    if (seen.has(id)) continue;
    seen.add(id);
    unique.push(id);
  }
  return unique.sort(compareQueryIds);
}

export function queryDisplayLabel(queryId: string): string {
  return queryId;
}

const QUERY_COLUMN_LABELS: Record<string, string> = {
  result_id: "Result ID",
  benchmark: "Benchmark",
  platform: "Platform",
  scale_factor: "Scale",
  run_date: "Run date",
  power_score: "Power score (higher is better)",
  throughput_at_size: "Throughput@Size (higher is better)",
  stream_count: "Streams",
  total_duration_s: "Total duration",
  geomean_ms: "Geomean latency (lower is better)",
  display_geomean_ms: "Display geomean (lower is better)",
  query_count: "Queries",
  trust_label: "Trust tier",
  visibility: "Visibility",
  platform_version: "Platform version",
  execution_mode: "Execution mode",
  tuning_mode: "Tuning",
  tuning_hash: "Tuning hash",
  test_type: "Phase",
  validation_status: "Validation",
  cost_usd: "Cost",
  normalized_cost_usd: "Normalized cost",
  cost_model_version: "Cost model",
  cost_scope: "Cost scope",
  cost_status: "Cost status",
  deployment_class: "Deployment",
  cloud_provider: "Cloud provider",
  cloud_region: "Cloud region",
  instance_or_warehouse: "Instance / warehouse",
  storage_format: "Storage format",
};

const PUBLIC_IDENTIFIER_FACETS = new Set(["platform", "cloud_region", "instance_or_warehouse", "cost_model"]);

export function formatQueryColumnLabel(column: string): string {
  return QUERY_COLUMN_LABELS[column] ?? formatFacetDisplayValue(column, column);
}

export function formatQueryFacetValue(key: string, value: string): string {
  const trimmed = value.trim();
  if (trimmed === "") return "unknown";
  if (key === "benchmark") return humanizeBenchmark(trimmed);
  if (key === "scale_factor") return formatScaleFactor(trimmed);
  if (key === "execution_mode") return formatExecutionMode(trimmed);
  if (key === "tuning_mode") return formatTuningMode(trimmed);
  if (key === "trust_label") return formatTrustLabel(trimmed);
  if (key === "validation_status") return formatValidationStatus(trimmed);
  if (key === "visibility") return formatVisibility(trimmed);
  if (key === "arch") return formatArchitecture(trimmed);
  if (PUBLIC_IDENTIFIER_FACETS.has(key)) return trimmed;
  return formatFacetDisplayValue(key, trimmed);
}

export function formatQueryCell(column: string, value: unknown): string {
  if (value === null || value === undefined || value === "") {
    if (column === "power_score") return "No power score";
    if (column === "throughput_at_size") return "No throughput score";
    if (column === "geomean_ms" || column === "display_geomean_ms") return "No timing recorded";
    if (column === "total_duration_s") return "No duration recorded";
    if (column === "cost_usd" || column === "normalized_cost_usd") return "No cost recorded";
    return "Not recorded";
  }
  if (column === "run_date") return formatRunDateWithAge(typeof value === "string" ? value : null);
  if (column === "power_score") return typeof value === "number" ? formatPowerScore(value).valueText : String(value);
  if (column === "throughput_at_size") {
    return typeof value === "number" ? formatThroughputScore(value).valueText : String(value);
  }
  if (column === "geomean_ms" || column === "display_geomean_ms") {
    return typeof value === "number" ? formatLatencyMs(value).valueText : String(value);
  }
  if (column === "total_duration_s") {
    const seconds = typeof value === "number" ? value : Number(value);
    return Number.isFinite(seconds) ? formatDurationSeconds(seconds).valueText : String(value);
  }
  if (column === "cost_usd" || column === "normalized_cost_usd") {
    return typeof value === "number" ? formatUsd(value).valueText : String(value);
  }
  if (typeof value === "string") return formatQueryFacetValue(column, value);
  if (column === "scale_factor") return formatScaleFactor(String(value));
  return formatPlainCell(value);
}

function formatPlainCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return MISSING_VALUE_GLYPH;
  if (typeof value === "number") return formatPlainNumber(value).valueText;
  return String(value);
}
