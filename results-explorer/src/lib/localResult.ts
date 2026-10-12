import type { DetailResult, Environment, QueryDisplayTiming, QueryTiming } from "@/types";
import { canonicalPhase } from "@/lib/displayLabels";
import type { PrimaryMetric } from "@/lib/displayEligibility";

export const MAX_LOCAL_RESULT_BYTES = 10 * 1024 * 1024;

const SUPPORTED_SCHEMA_VERSIONS = new Set(["2.0", "2.1", "2.2", "2.3"]);
const PASS_STATUSES = new Set(["SUCCESS", "PASS", "pass", "success"]);
const NON_FAILED_QUERY_ROW_STATUSES = new Set(["SUCCESS", "SKIPPED"]);
const EXECUTION_RUN_TYPES = new Set(["measurement", "warmup"]);
const EXECUTION_MODES = new Set(["sql", "dataframe"]);
const TUNING_MODES = new Set(["tuned", "tuned-fallback", "notuning", "auto", "custom"]);
const FUNDING_SOURCES = new Set(["employer", "personal", "free-trial", "vendor-sponsored", "grant", "unspecified"]);
const LOCAL_COST_MODEL_VERSION = "2026.09";
const LOCAL_COST_MODEL_SOURCE = "benchbox.core.cost.pricing";
const CPU_FAMILY_PATTERNS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\bapple\s+(?:m\d|a\d)/i, "apple_silicon"],
  [/\bgraviton/i, "graviton"],
  [/\bxeon\b/i, "intel_xeon"],
  [/\b(?:intel.*core|core\(tm\))\b/i, "intel_core"],
  [/\bepyc\b/i, "amd_epyc"],
  [/\b(?:ryzen|threadripper)\b/i, "amd_ryzen"],
  [/\b(?:ampere|altra)\b/i, "ampere_altra"],
  [/\bneoverse\b/i, "arm_neoverse"],
];
const KNOWN_LOGICAL_QUERY_COUNTS: Record<string, number> = {
  tpch: 22,
  tpch_skew: 22,
  tpchavoc: 22,
  tpcds: 99,
  ssb: 13,
  star_schema: 13,
  clickbench: 43,
};

type JsonObject = Record<string, unknown>;
export type LocalPrimaryMetric = PrimaryMetric;

export interface LocalResultPreview {
  detail: DetailResult;
  fileName: string;
  primaryMetric: LocalPrimaryMetric;
}

export class LocalResultImportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LocalResultImportError";
  }
}

export async function importLocalResultFile(file: File): Promise<LocalResultPreview> {
  if (!file.name.toLowerCase().endsWith(".json")) {
    throw new LocalResultImportError("Choose a BenchBox result bundle in JSON format.");
  }
  if (file.size > MAX_LOCAL_RESULT_BYTES) {
    throw new LocalResultImportError("This result is larger than the 10 MiB local preview limit.");
  }
  let text: string;
  try {
    text = await file.text();
  } catch {
    throw new LocalResultImportError("The selected file could not be read.");
  }
  return parseLocalResultText(text, file.name);
}

export async function parseLocalResultText(text: string, fileName = "local-result.json"): Promise<LocalResultPreview> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new LocalResultImportError("This file is not valid JSON.");
  }
  const bundle = objectOrError(parsed, "The JSON root must be an object.");
  const hasOwn = (key: string): boolean => Object.prototype.hasOwnProperty.call(bundle, key);
  const versionValue = hasOwn("result_schema_version")
    ? bundle.result_schema_version
    : hasOwn("version")
      ? bundle.version
      : bundle.schema_version;
  if (
    typeof bundle.result_schema_version === "string" &&
    typeof bundle.version === "string" &&
    bundle.result_schema_version.trim() !== "" &&
    bundle.version.trim() !== "" &&
    bundle.result_schema_version.trim() !== bundle.version.trim()
  ) {
    throw new LocalResultImportError("result_schema_version and version must match when both are present.");
  }
  const version = requiredString(versionValue, "result_schema_version");
  if (!SUPPORTED_SCHEMA_VERSIONS.has(version)) {
    throw new LocalResultImportError(
      `Schema ${version} is not supported. Local preview accepts BenchBox result schemas 2.0, 2.1, 2.2, and 2.3.`,
    );
  }

  const run = objectOrError(bundle.run, "The result is missing its run metadata.");
  const benchmark = objectOrError(bundle.benchmark, "The result is missing its benchmark metadata.");
  const platform = objectOrError(bundle.platform, "The result is missing its platform metadata.");
  const summary = objectOrError(bundle.summary, "The result is missing its summary.");
  if (!Array.isArray(bundle.queries)) {
    throw new LocalResultImportError("The result is missing its query list.");
  }

  requiredString(run.id, "run.id");
  const timestamp = requiredString(run.timestamp, "run.timestamp");
  const benchmarkId = requiredString(benchmark.id, "benchmark.id");
  const scaleFactor = requiredFiniteNumber(benchmark.scale_factor, "benchmark.scale_factor");
  const platformName = requiredString(platform.name, "platform.name");
  const totalDurationMs = requiredFiniteNumber(run.total_duration_ms, "run.total_duration_ms");
  if (!isObject(summary.queries)) {
    throw new LocalResultImportError("The result is missing summary.queries metadata.");
  }

  const queries = queryTimings(bundle.queries);
  const displayTimings = buildDisplayTimings(queries);
  const logicalQueryCount = inferLogicalQueryCount(bundle, benchmarkId, displayTimings);
  const eligibility = timingEligibility(displayTimings, logicalQueryCount);
  const powerScore = firstFiniteNumber(objectValue(summary, "tpc_metrics"), ["power_at_size"]);
  const phase = testType(bundle, benchmark);
  const throughputScore = throughputPhaseClean(objectValue(bundle, "phases"))
    ? firstFiniteNumber(objectValue(summary, "tpc_metrics"), ["throughput_at_size"])
    : null;
  const streamCount = phase !== null && canonicalPhase(phase) === "throughput"
    ? throughputStreamCount(objectValue(bundle, "phases"))
    : null;
  const resultId = localResultId();
  const validationStatus = validationStatusFor(bundle, failedQueryCount(bundle));
  const environment = safeEnvironment(bundle.environment);
  const clientFields = {
    client_region: stringOrNull(environment.client_region),
    client_cloud: stringOrNull(environment.client_cloud),
    statement_overhead_min_ms: finiteNumberOrNull(environment.statement_overhead_min_ms),
    statement_overhead_median_ms: finiteNumberOrNull(environment.statement_overhead_median_ms),
    link_status: stringOrNull(environment.link_status),
  };
  const tuning = objectValue(platform, "tuning");
  const logicalProfile = objectValue(tuning, "logical_profile");
  const funding = fundingSource(bundle.provenance);
  const normalizedCost = normalizedCostFields(bundle);

  const detail: DetailResult = {
    result_id: resultId,
    benchmark: benchmarkId,
    scale_factor: scaleFactor,
    platform: platformName,
    platform_id: platformId(platformName),
    driver_version: driverVersion(bundle, platform),
    run_date: timestamp.slice(0, 10),
    total_duration_s: totalDurationMs / 1000,
    geomean_ms: geometricMean(rawMeasurementDurations(queries)),
    display_geomean_ms: geometricMean(
      displayTimings.flatMap((timing) => timing.display_ms !== null && timing.display_ms > 0 ? [timing.display_ms] : []),
    ),
    power_score: powerScore,
    throughput_at_size: throughputScore,
    stream_count: streamCount,
    has_display_timing: eligibility.validQueryCount > 0,
    logical_query_count: logicalQueryCount,
    valid_query_count: eligibility.validQueryCount,
    missing_query_count: eligibility.missingQueryCount,
    zero_timing_count: eligibility.zeroTimingCount,
    display_exclusion_reason: eligibility.displayExclusionReason,
    comparison_exclusion_reason: eligibility.comparisonExclusionReason,
    ranking_exclusion_reason: "local_result",
    environment,
    queries,
    display_timings: displayTimings,
    has_plans: false,
    plans_published: false,
    has_tuning: false,
    bundle_download_url: "",
    trust_label: "local-run",
    visibility: "local-preview",
    funding,
    platform_version: nonUnknownString(platform.version),
    execution_mode: executionMode(bundle),
    tuning_mode: tuningMode(bundle),
    tuning_hash: null,
    requested_config_hash: stringOrNull(tuning.requested_config_hash),
    applied_ledger_hash: stringOrNull(tuning.applied_ledger_hash),
    tuning_validation_status: stringOrNull(tuning.validation_status),
    applied_receipt: null,
    override_rules: null,
    override_evidence: null,
    override_approver: null,
    override_expires: null,
    tuning_policy_generation: stringOrNull(tuning.tuning_policy_generation),
    template_evidence: stringOrNull(tuning.template_evidence),
    test_type: phase,
    validation_status: validationStatus,
    cost_usd: normalizedCost.normalized_cost_usd,
    ...normalizedCost,
    compliance_class: stringOrNull(benchmark.compliance_class),
    physical_mechanisms: Array.isArray(logicalProfile.physical_mechanisms)
      ? logicalProfile.physical_mechanisms.map(String)
      : undefined,
    physical_rendering_id: stringOrNull(logicalProfile.physical_rendering_id),
    ...clientFields,
  };

  return {
    detail,
    fileName,
    primaryMetric: localPrimaryMetric(benchmarkId, phase, powerScore),
  };
}

function queryTimings(rawQueries: unknown[]): QueryTiming[] {
  const timings: QueryTiming[] = [];
  for (const raw of rawQueries) {
    if (!isObject(raw)) continue;
    const rawRunType = raw.run_type;
    const runType = rawRunType === null || rawRunType === undefined
      ? null
      : typeof rawRunType === "string"
        ? rawRunType
        : "invalid";
    if (runType !== null && !EXECUTION_RUN_TYPES.has(runType)) continue;
    const queryId = raw.id || raw.query_id || "";
    const duration = finiteNumberOrNull(raw.ms ?? raw.execution_time_ms) ?? 0;
    const rawStatus = Object.prototype.hasOwnProperty.call(raw, "status") ? raw.status : "pass";
    timings.push({
      query_id: String(queryId),
      duration_ms: duration,
      status: PASS_STATUSES.has(String(rawStatus)) ? "pass" : "fail",
      run_type: runType,
      iter: integerOrNull(raw.iter),
      stream: integerOrNull(raw.stream),
    });
  }
  return timings;
}

function buildDisplayTimings(timings: QueryTiming[]): QueryDisplayTiming[] {
  const grouped = new Map<string, QueryTiming[]>();
  for (const timing of timings) {
    const current = grouped.get(timing.query_id) ?? [];
    current.push(timing);
    grouped.set(timing.query_id, current);
  }
  return [...grouped.entries()].map(([queryId, group]) => {
    const passing = group.filter((timing) => timing.status === "pass");
    const measurements = passing.filter((timing) => timing.run_type === "measurement");
    const legacy = passing.filter((timing) => timing.run_type === null);
    const candidates = measurements.length > 0 ? measurements : legacy;
    const durations = candidates.map((timing) => timing.duration_ms).sort((a, b) => a - b);
    const displayMs = median(durations);
    return {
      query_id: queryId,
      display_ms: displayMs,
      sample_count: candidates.length,
      is_valid_display_timing: displayMs !== null && Number.isFinite(displayMs) && displayMs > 0,
      timing_exclusion_reason: timingExclusionReason(displayMs),
    };
  });
}

function inferLogicalQueryCount(bundle: JsonObject, benchmarkId: string, timings: QueryDisplayTiming[]): number {
  const rawQueries = Array.isArray(bundle.queries) ? bundle.queries : [];
  let dataframeSkipTotal: number | null = null;
  for (const raw of rawQueries) {
    if (!isObject(raw) || !isObject(raw.dataframe_skip_summary)) continue;
    const executed = integerOrNull(raw.dataframe_skip_summary.executed_total);
    const skipped = integerOrNull(raw.dataframe_skip_summary.skipped_total);
    if (executed !== null && skipped !== null && executed + skipped > (dataframeSkipTotal ?? 0)) {
      dataframeSkipTotal = executed + skipped;
    }
  }
  if (dataframeSkipTotal !== null) return dataframeSkipTotal;

  const summary = objectValue(bundle, "summary");
  const querySummary = objectValue(summary, "queries");
  const rawCount = integerOrNull(querySummary.total) ?? 0;
  const observedCount = new Set(timings.map((timing) => timing.query_id).filter(Boolean)).size;
  if (observedCount <= 0) return rawCount;
  if (rawCount <= observedCount) return rawCount || observedCount;
  const knownCount = KNOWN_LOGICAL_QUERY_COUNTS[benchmarkId];
  if (knownCount && observedCount <= knownCount && rawCount % knownCount === 0) return knownCount;
  if (rawCount % observedCount === 0 && timings.some((timing) => timing.sample_count > 1)) return observedCount;
  return rawCount;
}

function timingEligibility(timings: QueryDisplayTiming[], logicalCount: number) {
  let validQueryCount = 0;
  let missingQueryCount = 0;
  let zeroTimingCount = 0;
  const seen = new Set<string>();
  for (const timing of timings) {
    seen.add(timing.query_id);
    const reason = timingExclusionReason(timing.display_ms);
    if (reason === null) validQueryCount += 1;
    else if (reason === "zero_timing") zeroTimingCount += 1;
    else missingQueryCount += 1;
  }
  missingQueryCount += Math.max(logicalCount - seen.size, 0);
  let displayExclusionReason: string | null = null;
  if (validQueryCount === 0) {
    if (logicalCount <= 0) displayExclusionReason = "no_queries";
    else if (zeroTimingCount > 0 && missingQueryCount === 0) displayExclusionReason = "zero_timings_only";
    else if (missingQueryCount > 0 && zeroTimingCount === 0) displayExclusionReason = "missing_timings";
    else displayExclusionReason = "no_valid_display_timing";
  }
  let comparisonExclusionReason = displayExclusionReason;
  if (comparisonExclusionReason === null && validQueryCount < 2) comparisonExclusionReason = "insufficient_valid_queries";
  if (comparisonExclusionReason === null && logicalCount > 0 && validQueryCount * 2 < logicalCount) {
    comparisonExclusionReason = "insufficient_query_coverage";
  }
  return { validQueryCount, missingQueryCount, zeroTimingCount, displayExclusionReason, comparisonExclusionReason };
}

function safeEnvironment(raw: unknown): Environment {
  const source = isObject(raw) ? raw : {};
  const environment: Environment = {};
  for (const key of ["os", "arch", "python", "cpu_model", "cpu_family"] as const) {
    const value = stringOrNull(source[key]);
    if (value !== null) environment[key] = value;
  }
  for (const key of ["cpu_count", "memory_gb"] as const) {
    const value = finiteNumberOrNull(source[key]);
    if (value !== null) environment[key] = value;
  }
  if (environment.cpu_model && !environment.cpu_family) {
    environment.cpu_family = normalizeCpuFamily(environment.cpu_model);
  }
  const provenance = stringOrNull(source.cpu_identity_provenance);
  if (provenance === "measured" || provenance === "user_attested" || provenance === "inferred") {
    environment.cpu_identity_provenance = provenance;
  }
  const clientLink = objectValue(source, "client_link");
  const overhead = objectValue(clientLink, "statement_overhead_ms");
  environment.client_region = stringOrNull(clientLink.client_region);
  environment.client_cloud = stringOrNull(clientLink.client_cloud);
  environment.link_status = stringOrNull(clientLink.collection_status);
  environment.statement_overhead_min_ms = finiteNumberOrNull(overhead.min);
  environment.statement_overhead_median_ms = finiteNumberOrNull(overhead.median);
  return environment;
}

function normalizeCpuFamily(rawModel: string): string {
  const match = CPU_FAMILY_PATTERNS.find(([pattern]) => pattern.test(rawModel));
  return match?.[1] ?? "unknown";
}

function executionMode(bundle: JsonObject): string | null {
  const candidates = [
    objectValue(bundle, "config").execution_mode,
    objectValue(objectValue(bundle, "platform"), "config").execution_mode,
    objectValue(bundle, "config").mode,
    objectValue(bundle, "execution").execution_mode,
  ];
  for (const raw of candidates) {
    const value = stringOrNull(raw)?.toLowerCase() ?? null;
    if (value !== null && EXECUTION_MODES.has(value)) return value;
  }
  return null;
}

function tuningMode(bundle: JsonObject): string | null {
  for (const raw of [objectValue(bundle, "config").tuning_mode, objectValue(bundle, "execution").tuning_mode]) {
    const value = stringOrNull(raw);
    if (value !== null && TUNING_MODES.has(value)) return value;
  }
  return null;
}

function driverVersion(bundle: JsonObject, platform: JsonObject): string | null {
  const execution = objectValue(bundle, "execution");
  const isDuckDb = stringOrNull(platform.name)?.toLowerCase() === "duckdb";
  if (isDuckDb) {
    const resolved = firstString(execution, [
      "driver_version_resolved", "driver_version_requested", "driver_resolved_version", "driver_requested_version",
    ]);
    if (resolved !== null) return resolved;
    const clientVersion = nonUnknownString(platform.client_version);
    if (clientVersion !== null) return clientVersion;
  }
  return firstString(execution, [
    "driver_version_actual", "driver_version_resolved", "driver_version_requested",
    "driver_actual_version", "driver_resolved_version", "driver_requested_version",
  ]);
}

function validationStatusFor(bundle: JsonObject, failedCount: number): string | null {
  const summary = objectValue(bundle, "summary");
  const raw = summary.validation;
  const value = typeof raw === "string" ? raw : isObject(raw) ? raw.status : null;
  const normalized = stringOrNull(value)?.toLowerCase() ?? null;
  if (failedCount > 0 && (normalized === null || normalized === "passed")) return "partial";
  if ((normalized === null || normalized === "passed") && translationIsNonClean(bundle)) {
    return "uncertain";
  }
  return normalized;
}

function failedQueryCount(bundle: JsonObject): number {
  const querySummary = objectValue(objectValue(bundle, "summary"), "queries");
  const explicitFailed = exactIntegerOrNull(querySummary.failed);
  if (explicitFailed !== null && explicitFailed > 0) return explicitFailed;

  const total = exactIntegerOrNull(querySummary.total);
  const passed = exactIntegerOrNull(querySummary.passed);
  const skipped = exactIntegerOrNull(querySummary.skipped) ?? 0;
  if (total !== null && passed !== null && total > passed + skipped) {
    return Math.max(total - passed - skipped, 0);
  }

  if (!Array.isArray(bundle.queries)) return 0;
  return bundle.queries.filter((query) => {
    if (!isObject(query)) return false;
    const runType = String(query.run_type || "measurement").toLowerCase();
    if (runType !== "measurement" || query.status === null || query.status === undefined) return false;
    return !NON_FAILED_QUERY_ROW_STATUSES.has(String(query.status).toUpperCase());
  }).length;
}

function translationIsNonClean(bundle: JsonObject): boolean {
  const raw = objectValue(bundle, "execution").translation;
  const status = stringOrNull(isObject(raw) ? raw.status : raw)?.toLowerCase() ?? null;
  return status === "fallback" || status === "failed";
}

function normalizedCostFields(bundle: JsonObject) {
  const root = objectValue(bundle, "normalized_cost");
  const cost = objectValue(bundle, "cost");
  const nestedNormalizedCost = objectValue(cost, "normalized_cost");
  const nestedNormalized = objectValue(cost, "normalized");
  const hasNormalizedCostShape = [
    "normalized_cost_usd", "cost_model_version", "cost_model_source", "cost_scope", "cost_status",
  ].some((key) => Object.prototype.hasOwnProperty.call(cost, key));
  const normalized = Object.keys(root).length > 0
    ? root
    : Object.keys(nestedNormalizedCost).length > 0
      ? nestedNormalizedCost
      : Object.keys(nestedNormalized).length > 0
        ? nestedNormalized
        : hasNormalizedCostShape
          ? cost
          : null;
  if (normalized === null) {
    return {
      normalized_cost_usd: null,
      cost_model_version: LOCAL_COST_MODEL_VERSION,
      cost_model_source: LOCAL_COST_MODEL_SOURCE,
      cost_scope: "compute_only",
      cost_status: "unavailable",
      billing_unit: "unknown",
      pricing_region: "unknown",
    };
  }
  return {
    normalized_cost_usd: finiteNumberOrNull(normalized.normalized_cost_usd),
    cost_model_version: stringOrNull(normalized.cost_model_version),
    cost_model_source: stringOrNull(normalized.cost_model_source),
    cost_scope: stringOrNull(normalized.cost_scope),
    cost_status: stringOrNull(normalized.cost_status),
    billing_unit: stringOrNull(normalized.billing_unit),
    pricing_region: stringOrNull(normalized.pricing_region),
  };
}

function testType(bundle: JsonObject, benchmark: JsonObject): string | null {
  const explicit = stringOrNull(benchmark.test_type);
  if (explicit !== null) return explicit;
  const phases = objectValue(bundle, "phases");
  if (phaseExecuted(phases.power_test)) return "power";
  if (phaseExecuted(phases.throughput_test)) return "throughput";
  return null;
}

function throughputStreamCount(phases: JsonObject): number | null {
  const throughput = objectValue(phases, "throughput_test");
  const streams = throughput.stream_results;
  return Array.isArray(streams) && streams.length > 0 ? streams.length : null;
}

function throughputPhaseClean(phases: JsonObject): boolean {
  const throughput = objectValue(phases, "throughput_test");
  if (String(throughput.status ?? "").toUpperCase() !== "COMPLETED") return false;
  const streams = throughput.stream_results;
  if (!Array.isArray(streams) || streams.length === 0) return false;
  if (!streams.every((stream) => isObject(stream) && stream.success === true)) return false;
  return isEmptyEvidence(throughput.errors) && isEmptyEvidence(throughput.outstanding_work);
}

function isEmptyEvidence(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (Array.isArray(value)) return value.length === 0;
  if (isObject(value)) return Object.keys(value).length === 0;
  return !value;
}

function localPrimaryMetric(benchmarkId: string, phase: string | null, powerScore: number | null): LocalPrimaryMetric {
  if (benchmarkId !== "tpch" && benchmarkId !== "tpcds") return "display_geomean_ms";
  if (phase !== null && canonicalPhase(phase) === "throughput") return "throughput_at_size";
  return powerScore !== null ? "power_score" : "display_geomean_ms";
}

function phaseExecuted(phase: unknown): boolean {
  if (!isObject(phase) || Object.keys(phase).length === 0) return false;
  return String(phase.status ?? "").toUpperCase() !== "NOT_RUN";
}

function rawMeasurementDurations(queries: QueryTiming[]): number[] {
  return queries
    .filter((query) => query.run_type !== "warmup" && query.duration_ms > 0)
    .map((query) => query.duration_ms);
}

function fundingSource(raw: unknown): string {
  const provenance = isObject(raw) ? raw : {};
  const token = stringOrNull(provenance.funding)?.toLowerCase() ?? "unspecified";
  return FUNDING_SOURCES.has(token) ? token : "unspecified";
}

function platformId(platformName: string): string {
  return platformName
    .replace(/[-_]trust[-_](ci|community|local|unknown)/gi, "")
    .trim()
    .toLowerCase()
    .replace(/ /g, "-");
}

function localResultId(): string {
  if (!globalThis.crypto?.getRandomValues) {
    throw new LocalResultImportError("This browser cannot create a private local preview ID.");
  }
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(6));
  const token = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `local-${token}`;
}

function objectOrError(raw: unknown, message: string): JsonObject {
  if (!isObject(raw)) throw new LocalResultImportError(message);
  return raw;
}

function requiredString(raw: unknown, field: string): string {
  const value = stringOrNull(raw);
  if (value === null) throw new LocalResultImportError(`The result is missing ${field}.`);
  return value;
}

function requiredFiniteNumber(raw: unknown, field: string): number {
  const value = finiteNumberOrNull(raw);
  if (value === null) throw new LocalResultImportError(`The result has an invalid ${field}.`);
  return value;
}

function objectValue(raw: JsonObject, key: string): JsonObject {
  return isObject(raw[key]) ? raw[key] : {};
}

function firstFiniteNumber(raw: JsonObject, keys: string[]): number | null {
  for (const key of keys) {
    const value = finiteNumberOrNull(raw[key]);
    if (value !== null) return value;
  }
  return null;
}

function firstString(raw: JsonObject, keys: string[]): string | null {
  for (const key of keys) {
    const value = stringOrNull(raw[key]);
    if (value !== null) return value;
  }
  return null;
}

function nonUnknownString(raw: unknown): string | null {
  const value = stringOrNull(raw);
  return value !== null && value.toLowerCase() !== "unknown" ? value : null;
}

function stringOrNull(raw: unknown): string | null {
  if (raw === null || raw === undefined) return null;
  const value = String(raw).trim();
  return value || null;
}

function finiteNumberOrNull(raw: unknown): number | null {
  if (raw === null || raw === undefined || raw === "") return null;
  const value = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(value) ? value : null;
}

function integerOrNull(raw: unknown): number | null {
  const value = finiteNumberOrNull(raw);
  return value === null ? null : Math.trunc(value);
}

function exactIntegerOrNull(raw: unknown): number | null {
  if (typeof raw === "boolean") return Number(raw);
  if (typeof raw === "number") return Number.isInteger(raw) ? raw : null;
  if (typeof raw !== "string" || !/^[+-]?\d+$/.test(raw)) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) ? value : null;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const middle = Math.floor(values.length / 2);
  if (values.length % 2 === 1) return values[middle] ?? null;
  return ((values[middle - 1] ?? 0) + (values[middle] ?? 0)) / 2;
}

function geometricMean(values: number[]): number | null {
  const positive = values.filter((value) => Number.isFinite(value) && value > 0);
  if (positive.length === 0) return null;
  return Math.exp(positive.reduce((sum, value) => sum + Math.log(value), 0) / positive.length);
}

function timingExclusionReason(value: number | null): string | null {
  if (value === null) return "missing_timing";
  if (!Number.isFinite(value)) return "invalid_timing";
  if (value === 0) return "zero_timing";
  if (value < 0) return "non_positive_timing";
  return null;
}

function isObject(raw: unknown): raw is JsonObject {
  return typeof raw === "object" && raw !== null && !Array.isArray(raw);
}
