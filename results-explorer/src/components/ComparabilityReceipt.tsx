import { formatLatencyMs, formatScaleFactor } from "@/lib/metricFormatters";
import { RunDateChip } from "@/components/RunAge";
import type { DetailResult, Environment } from "@/types";
import { humanizeBenchmark, shortHash } from "@/utils";
import { costModelSummary, costScopeSummary, normalizedCostLabel } from "@/lib/costDisplay";
import { formatCount, formatWarningCount } from "@/lib/copyFormatters";
import {
  formatArchitecture,
  formatCpuFamily,
  formatEnumLabel,
  formatExecutionMode,
  formatMemoryGb,
  formatTuningMode,
  formatValidationStatus,
  parseOverrideRules,
} from "@/lib/displayLabels";
import { StatusBadge, type StatusTone } from "@/components/StatusBadge";
import { formatCpuIdentityProvenance } from "@/lib/hardwareProvenance";
import { formatRunDate, formatRunDateWithAge } from "@/lib/runAge";

interface ComparabilityReceiptProps {
  results: DetailResult[];
}

type ComparabilityStatus = "match" | "diff" | "missing";

export const COMPARABILITY_RECEIPT_ID = "comparability-receipt";
export const COMPARABILITY_WARNING_TARGET_ID = "comparability-receipt-warnings";

export interface ComparabilityField {
  label: string;
  status: ComparabilityStatus;
  summary: string;
  detail?: string;
  dates?: { platform: string; runDate: string }[];
}

export function ComparabilityReceipt({ results }: ComparabilityReceiptProps) {
  if (results.length === 0) return null;

  const fields = buildComparabilityFields(results);
  const warningFields = comparabilityWarningFields(fields);
  const warningCount = warningFields.length;

  return (
    <section id={COMPARABILITY_RECEIPT_ID} aria-label="Comparison checks" class="panel-elevated mb-8 p-4">
      <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-base font-semibold text-[var(--bb-data-fg-primary)]">Comparison checks</h2>
          <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">
            Check the workload, software, validation, and hardware before you compare these runs.
          </p>
        </div>
        <StatusBadge role="comparison" tone={warningCount > 0 ? "warning" : "success"}>
          {warningCount > 0 ? formatWarningCount(warningCount) : "No differences"}
        </StatusBadge>
      </div>

      {warningCount > 0 && (
        <section
          id={COMPARABILITY_WARNING_TARGET_ID}
          tabIndex={-1}
          aria-label="Warning details"
          class="mb-4 rounded-md border border-[var(--bb-tone-warning-border)] bg-[var(--bb-tone-warning-bg)] px-3 py-2 text-xs text-[var(--bb-tone-warning-fg)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--bb-accent)]"
          data-testid="comparability-warning-target"
        >
          <p class="font-semibold">Review these differences before drawing a conclusion.</p>
          <ul class="mt-1 list-disc space-y-1 pl-4">
            {warningFields.map((field) => (
              <li key={field.label}>
                <span class="font-medium">{field.label}:</span> {field.dates ? <DateWindowChips dates={field.dates} /> : field.summary}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {fields.map((field) => (
          <ComparabilityFieldRow key={field.label} field={field} />
        ))}
      </div>
      <span class="sr-only">
        Environment: {formatPerPlatform(results.map((r) => ({ platform: r.platform, value: formatEnvironment(r.environment) })))}
      </span>
    </section>
  );
}

export function buildComparabilityFields(results: DetailResult[]): ComparabilityField[] {
  if (results.length === 0) return [];

  const fields: ComparabilityField[] = [
    compareValues("Benchmark", results, (result) => humanizeBenchmark(result.benchmark)),
    compareValues("Scale factor", results, (result) => formatScaleFactor(result.scale_factor)),
    compareValues("Test phase", results, (result) => result.test_type ? formatEnumLabel(result.test_type) : "Not recorded"),
    compareValues("Query scope", results, (result) => formatCount(queryCount(result), "query", "queries")),
    buildDateWindowField(results),
    compareValues("Platform version", results, (result) => valueOrMissing(result.platform_version)),
    compareValues("Driver version", results, (result) => valueOrMissing(result.driver_version)),
    compareValues("Execution mode", results, (result) => result.execution_mode ? formatExecutionMode(result.execution_mode) : "Not recorded"),
    compareValues(
      "Execution engine",
      results,
      (result) => result.requested_execution_engine ?? "Not recorded",
    ),
    compareValues(
      "Gateway",
      results,
      (result) => (result.gateway_routed ? `Routed via ${result.gateway_name ?? "unknown"}` : "Native"),
    ),
    buildTuningField(results),
    compareValues(
      "Validation",
      results,
      (result) =>
        formatValidationStatusReceiptValue(result.validation_status, parseOverrideRules(result.override_rules)),
    ),
    compareHardwareValues("Architecture", results, (result) => result.environment?.arch ? formatArchitecture(result.environment.arch) : "Not recorded"),
    compareHardwareValues("CPU family", results, (result) => result.environment?.cpu_family ? formatCpuFamily(result.environment.cpu_family) : "Not recorded"),
    compareHardwareValues("CPU model", results, (result) => valueOrMissing(result.environment?.cpu_model)),
    compareHardwareValues("CPU evidence", results, (result) =>
      formatCpuIdentityProvenance(result.environment?.cpu_identity_provenance),
    ),
    compareHardwareValues("CPU count", results, (result) =>
      result.environment?.cpu_count !== undefined ? `${result.environment.cpu_count} CPU` : "Not recorded",
    ),
    compareHardwareValues("Memory", results, (result) =>
      result.environment?.memory_gb !== undefined ? formatMemoryGb(result.environment.memory_gb) : "Not recorded",
    ),
    buildLocalityField(results),
    compareValues("Normalized cost", results, normalizedCostLabel),
    compareValues("Cost model", results, costModelSummary),
    compareValues("Cost scope", results, costScopeSummary),
  ];

  const physicalMechanismsField = buildPhysicalMechanismsField(results);
  if (physicalMechanismsField) fields.push(physicalMechanismsField);

  const tuningPolicyGenerationField = buildTuningPolicyGenerationField(results);
  if (tuningPolicyGenerationField) fields.push(tuningPolicyGenerationField);

  const overrideField = buildOverrideField(results);
  if (overrideField) fields.push(overrideField);

  return fields;
}

const PRE_SEAM_GENERATION = "pre-seam";

function buildTuningPolicyGenerationField(results: DetailResult[]): ComparabilityField | null {
  const tunedResults = results.filter((result) => result.tuning_mode === "tuned");
  if (tunedResults.length < 2) return null;

  const generationOf = (result: DetailResult) => result.tuning_policy_generation ?? PRE_SEAM_GENERATION;
  const uniqueGenerations = [...new Set(tunedResults.map(generationOf))];

  if (uniqueGenerations.length === 1) {
    return {
      label: "Tuning rules version",
      status: "match",
      summary: uniqueGenerations[0] === PRE_SEAM_GENERATION ? "Earlier rules" : uniqueGenerations[0]!,
    };
  }

  return {
    label: "Tuning rules version",
    status: "diff",
    summary: "The selected runs used different generations of BenchBox tuning rules",
    detail: formatPerPlatform(
      tunedResults.map((result) => ({
        platform: result.platform,
        value: generationOf(result) === PRE_SEAM_GENERATION ? "Earlier rules" : generationOf(result),
      })),
    ),
  };
}

function buildPhysicalMechanismsField(results: DetailResult[]): ComparabilityField | null {
  const tunedResults = results.filter((result) => result.tuning_mode === "tuned");
  if (tunedResults.length < 2) return null;
  if (tunedResults.some((result) => result.physical_mechanisms === undefined)) return null;

  const sets = tunedResults.map((result) => new Set(result.physical_mechanisms ?? []));
  const allMatch = sets.every((set) => setsEqual(set, sets[0]!));

  if (allMatch) {
    const count = sets[0]!.size;
    return {
      label: "Applied tuning features",
      status: "match",
      summary: count > 0 ? formatCount(count, "feature", "features") : "None applied",
    };
  }

  return {
    label: "Applied tuning features",
    status: "diff",
    summary: "The selected runs applied different tuning features",
    detail: formatPerPlatform(
      tunedResults.map((result) => ({
        platform: result.platform,
        value: (result.physical_mechanisms ?? []).join(", ") || "none",
      })),
    ),
  };
}

function isRemoteOrCloudPlatform(result: DetailResult): boolean {
  if (result.deployment_class === "cloud" || result.deployment_class === "remote") {
    return true;
  }
  if (result.cloud_provider && result.cloud_provider !== "local" && result.cloud_provider !== "none") {
    return true;
  }
  if (result.cloud_region && result.cloud_region !== "unknown" && result.cloud_region !== "local") {
    return true;
  }
  return false;
}

function getClientRegion(result: DetailResult): string | null {
  const r = result.environment?.client_region ?? result.client_region;
  return r && String(r).trim() ? String(r).trim() : null;
}

function getPlatformRegion(result: DetailResult): string | null {
  const r = result.cloud_region ?? (result.pricing_region !== "unknown" ? result.pricing_region : null);
  return r && String(r).trim() ? String(r).trim() : null;
}

function getClientCloud(result: DetailResult): string | null {
  const c = result.environment?.client_cloud ?? result.client_cloud;
  if (!c || !String(c).trim()) return null;
  const token = String(c).trim().toLowerCase();
  if (token === "unknown" || token === "local" || token === "none") return null;
  return String(c).trim();
}

function getPlatformCloud(result: DetailResult): string | null {
  const c = result.cloud_provider;
  return c && String(c).trim() && String(c).trim().toLowerCase() !== "unknown"
    ? String(c).trim()
    : null;
}

function getOverheadMedian(result: DetailResult): number | null {
  const m = result.environment?.statement_overhead_median_ms ?? result.statement_overhead_median_ms;
  const n = m == null ? NaN : Number(m);
  return Number.isFinite(n) ? n : null;
}

function canonicalRegion(region: string): string {
  return region
    .toLowerCase()
    .replace(/^(aws|azure|gcp)_/, "")
    .replace(/[^a-z0-9]/g, "");
}

function buildLocalityField(results: DetailResult[]): ComparabilityField {
  const entries = results.map((result) => {
    const isRemote = isRemoteOrCloudPlatform(result);
    const clientReg = getClientRegion(result);
    const platformReg = getPlatformRegion(result);

    if (isRemote) {
      if (!clientReg) {
        return {
          platform: result.platform,
          value: "Unknown client locality",
          warn: true,
        };
      }
      const clientCloud = getClientCloud(result);
      const platformCloud = getPlatformCloud(result);
      if (
        clientCloud != null &&
        platformCloud != null &&
        clientCloud.toLowerCase() !== platformCloud.toLowerCase()
      ) {
        return {
          platform: result.platform,
          value: `Cross-cloud: client in ${clientReg} (${clientCloud}), platform in ${platformReg ?? "unknown locality"} (${platformCloud})`,
          warn: true,
        };
      }
      if (!platformReg) {
        return {
          platform: result.platform,
          value: `Client in ${clientReg}, platform locality unknown`,
          warn: true,
        };
      }
      if (canonicalRegion(clientReg) !== canonicalRegion(platformReg)) {
        const overhead = getOverheadMedian(result);
        const overheadCtx =
          overhead != null ? ` (client statement floor ${formatLatencyMs(overhead).valueText})` : "";
        return {
          platform: result.platform,
          value: `Cross-region: client in ${clientReg}, platform in ${platformReg}${overheadCtx}`,
          warn: true,
        };
      }
      return {
        platform: result.platform,
        value: `Collocated (${clientReg})`,
        warn: false,
      };
    }

    return {
      platform: result.platform,
      value: clientReg ? `Local (${clientReg})` : "Local",
      warn: false,
    };
  });

  const values = entries.map((e) => e.value);
  const uniqueValues = [...new Set(values)];
  const hasWarning = entries.some((e) => e.warn);
  const hasDiff = hasWarning || uniqueValues.length > 1;

  if (hasDiff) {
    let summary: string;
    if (uniqueValues.length === 1) {
      summary = uniqueValues[0]!;
    } else {
      summary = `${uniqueValues.length} localities differ`;
    }
    return {
      label: "Locality",
      status: "diff",
      summary,
      detail: formatPerPlatform(entries),
    };
  }

  return {
    label: "Locality",
    status: "match",
    summary: uniqueValues[0] ?? "Local",
  };
}

function setsEqual(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  if (a.size !== b.size) return false;
  for (const item of a) {
    if (!b.has(item)) return false;
  }
  return true;
}

export function comparabilityWarningFields(fields: readonly ComparabilityField[]): ComparabilityField[] {
  return fields.filter((field) => field.status === "diff");
}

export function orderWarningLabelsForSummary(warningFields: readonly ComparabilityField[]): string[] {
  const labels = warningFields.map((field) => field.label);
  const priority = labels.filter((label) => label === "Validation");
  const rest = labels.filter((label) => label !== "Validation");
  return [...priority, ...rest];
}

function DateWindowChips({ dates }: { dates: NonNullable<ComparabilityField["dates"]> }) {
  return <span class="inline-flex flex-wrap gap-2">{dates.map((entry, index) => <span key={index} class="inline-flex items-center gap-1">{entry.platform} <RunDateChip runDate={entry.runDate} /></span>)}</span>;
}

function ComparabilityFieldRow({ field }: { field: ComparabilityField }) {
  return (
    <div class="min-w-0 break-words rounded-md border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-3 py-2">
      <div class="mb-1 flex items-center justify-between gap-2">
        <h3 class="text-xs font-semibold uppercase text-[var(--bb-data-fg-subtle)]">{field.label}</h3>
        <StatusBadge role="comparison" tone={statusTone(field.status)}>{statusLabel(field.status)}</StatusBadge>
      </div>
      <p class="break-words text-xs font-medium text-[var(--bb-data-fg-primary)]">{field.dates ? <DateWindowChips dates={field.dates} /> : field.summary}</p>
      {field.detail && !field.dates && <p class="mt-1 break-words text-xs text-[var(--bb-data-fg-muted)]">{field.detail}</p>}
    </div>
  );
}

function compareValues(
  label: string,
  results: DetailResult[],
  readValue: (result: DetailResult) => string,
): ComparabilityField {
  const entries = results.map((result) => ({
    platform: result.platform,
    value: readValue(result),
  }));
  const values = entries.map((entry) => entry.value);
  const uniqueValues = [...new Set(values)];
  const allMissing = values.every((value) => value === "Not recorded");
  if (allMissing) {
    return {
      label,
      status: "missing",
      summary: "Not recorded",
    };
  }
  if (uniqueValues.length === 1) {
    return {
      label,
      status: "match",
      summary: uniqueValues[0]!,
    };
  }
  return {
    label,
    status: "diff",
    summary: `${uniqueValues.length} values differ`,
    detail: formatPerPlatform(entries),
  };
}

function buildDateWindowField(results: DetailResult[]): ComparabilityField {
  const dateEntries = results.map((result) => ({ platform: result.platform, runDate: result.run_date }));
  const dates = results.map((result) => formatRunDate(result.run_date));
  const uniqueDates = [...new Set(dates)];
  if (uniqueDates.length === 1) {
    return {
      label: "Date window",
      status: "match",
      summary: formatRunDateWithAge(results[0]!.run_date),
      dates: dateEntries,
    };
  }
  const sortedDates = [...uniqueDates].sort();
  const labelForDate = (date: string) =>
    formatRunDateWithAge(results.find((result) => formatRunDate(result.run_date) === date)?.run_date);
  return {
    label: "Date window",
    status: "diff",
    summary: `${labelForDate(sortedDates[0]!)} to ${labelForDate(sortedDates[sortedDates.length - 1]!)}`,
    dates: dateEntries,
    detail: formatPerPlatform(
      results.map((result) => ({
        platform: result.platform,
        value: formatRunDateWithAge(result.run_date),
      })),
    ),
  };
}

function compareHardwareValues(
  label: string,
  results: DetailResult[],
  readValue: (result: DetailResult) => string,
): ComparabilityField {
  const entries = results.map((result) => ({
    platform: result.platform,
    value: readValue(result),
  }));
  const values = entries.map((entry) => entry.value);
  const uniqueValues = [...new Set(values)];
  const allMissing = values.every((value) => value === "Not recorded");
  if (allMissing) {
    return {
      label,
      status: "missing",
      summary: "Not recorded",
    };
  }

  const anyMissing = values.some((value) => value === "Not recorded");
  if (anyMissing) {
    return {
      label,
      status: "missing",
      summary: "Not recorded",
      detail: formatPerPlatform(entries),
    };
  }

  if (uniqueValues.length === 1) {
    return {
      label,
      status: "match",
      summary: uniqueValues[0]!,
    };
  }

  return {
    label,
    status: "diff",
    summary: `${uniqueValues.length} values differ`,
    detail: formatPerPlatform(entries),
  };
}

function queryCount(result: DetailResult) {
  return result.display_timings.length || new Set(result.queries.map((query) => query.query_id)).size;
}

function formatTuning(result: DetailResult) {
  const requestedHash = result.requested_config_hash;
  const appliedHash = result.applied_ledger_hash;
  if (!result.tuning_mode && !requestedHash && !appliedHash && !result.has_tuning) {
    return "Not recorded";
  }
  const parts = [
    result.tuning_mode ? formatTuningMode(result.tuning_mode) : "Recorded",
    requestedHash ? `requested ${shortHash(requestedHash)}` : null,
    appliedHash ? `applied ${shortHash(appliedHash)}` : null,
  ].filter((part): part is string => part !== null);
  return parts.join(", ");
}

function buildTuningField(results: DetailResult[]): ComparabilityField {
  const field = compareValues("Tuning", results, formatTuning);
  if (field.status !== "diff") return field;

  const requestedHashes = results.map((result) => result.requested_config_hash);
  const appliedHashes = results.map((result) => result.applied_ledger_hash ?? null);
  const sameRecordedRequest = requestedHashes.every(Boolean) && new Set(requestedHashes).size === 1;
  const appliedStatementsDiffer = new Set(appliedHashes).size > 1;
  if (!sameRecordedRequest || !appliedStatementsDiffer) return field;

  return {
    ...field,
    summary: "Requested configuration matches; applied statements differ",
  };
}

function formatEnvironment(environment: Environment) {
  const parts = [
    environment.os,
    environment.arch ? formatArchitecture(environment.arch) : null,
    environment.cpu_count !== undefined ? `${environment.cpu_count} CPU` : null,
    environment.memory_gb !== undefined ? formatMemoryGb(environment.memory_gb) : null,
    environment.python ? `Python ${environment.python}` : null,
  ].filter((part): part is string => part !== null && part !== undefined && part !== "");
  return parts.length > 0 ? parts.join(", ") : "Not recorded";
}

function formatPerPlatform(entries: { platform: string; value: string }[]) {
  return entries.map((entry) => `${entry.platform}: ${entry.value}`).join("; ");
}

function formatValidationStatusReceiptValue(status: string | null | undefined, overrideRules?: string[]) {
  const suffix = overrideRules && overrideRules.length > 0 ? ` — overridden (${overrideRules.join(", ")})` : "";
  if (!status) return overrideRules && overrideRules.length > 0 ? `Not recorded${suffix}` : "Not recorded";
  const label = formatValidationStatus(status);
  const base = label === status ? label : `${label} (${status})`;
  return `${base}${suffix}`;
}

function buildOverrideField(results: DetailResult[]): ComparabilityField | null {
  if (results.some((result) => result.override_rules === undefined)) return null;
  const withRules = results.filter((result) => parseOverrideRules(result.override_rules).length > 0);
  if (withRules.length === 0) return null;
  return {
    label: "Override",
    status: "diff",
    summary: "One or more compared runs were accepted under a plausibility override",
    detail: formatPerPlatform(
      results.map((result) => {
        const rules = parseOverrideRules(result.override_rules);
        const value = rules.length > 0 ? `overridden (${rules.join(", ")})` : "no override";
        const audit =
          rules.length > 0
            ? [
                result.override_approver ? `approved by ${result.override_approver}` : null,
                result.override_expires ? `expires ${result.override_expires}` : null,
                result.override_evidence ? `evidence: ${result.override_evidence}` : null,
              ]
                .filter((part): part is string => part !== null)
                .join("; ")
            : null;
        return { platform: result.platform, value: audit ? `${value} — ${audit}` : value };
      }),
    ),
  };
}

function valueOrMissing(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "Not recorded";
  return String(value);
}

function statusLabel(status: ComparabilityStatus) {
  if (status === "match") return "Match";
  if (status === "diff") return "Differs";
  return "Not recorded";
}

function statusTone(status: ComparabilityStatus): StatusTone {
  if (status === "match") return "success";
  if (status === "diff") return "warning";
  return "neutral";
}
