import { formatLatencyMs, formatScaleFactor } from "@/lib/metricFormatters";
import { useState } from "preact/hooks";
import type { ComponentChildren } from "preact";
import type { DetailResult } from "@/types";
import { humanizeBenchmark, shortHash } from "@/utils";
import { costModelSummary, costScopeSummary, hasCostEstimate, normalizedCostLabel, parseEstimateAssumptions } from "@/lib/costDisplay";
import {
  formatArchitecture,
  formatCpuFamily,
  formatEnumLabel,
  formatExecutionMode,
  formatFunding,
  formatMemoryGb,
  formatTuningMode,
  formatTrustLabel,
  formatVisibility,
  parseOverrideRules,
} from "@/lib/displayLabels";
import { visibleResultIdForRow } from "@/lib/resultLinks";
import { StatusBadge } from "@/components/StatusBadge";
import { ValidationBadge } from "@/components/TrustBadge";
import { TuningVerificationBadge } from "@/components/TuningVerificationBadge";
import { formatCpuIdentityProvenance } from "@/lib/hardwareProvenance";
import { RunDateChip } from "@/components/RunAge";
import { VersionLabel } from "@/components/VersionLabel";

interface RunReceiptProps {
  detail: DetailResult;
  shortId?: string | null;
  isRankingEligible?: boolean | null;
  reproduceCommand?: string | null;
  plansUrl?: string | null;
}

interface ReceiptRow {
  label: string;
  value: ComponentChildren;
  isMissing?: boolean;
}

const MISSING_PLACEHOLDER = "Not recorded";

function recordedRow(label: string, value: ComponentChildren): ReceiptRow {
  return { label, value };
}

function missingRow(label: string): ReceiptRow {
  return { label, value: MISSING_PLACEHOLDER, isMissing: true };
}

function rowFromString(
  label: string,
  raw: string | number | null | undefined,
  format: (raw: string) => string = (s) => s,
): ReceiptRow {
  if (raw === null || raw === undefined || raw === "") return missingRow(label);
  return recordedRow(label, format(String(raw)));
}

function rowFromSummary(label: string, value: string): ReceiptRow {
  if (value === MISSING_PLACEHOLDER) return missingRow(label);
  return recordedRow(label, value);
}

function estimateProvenanceRow(detail: DetailResult): ReceiptRow {
  const assumptions = parseEstimateAssumptions(detail.cost_estimate_assumptions);
  return recordedRow(
    "Estimate basis",
    <div class="space-y-1 text-xs text-[var(--bb-data-fg-muted)]">
      <p>This timing-derived estimate is not metered usage, an actual cost, or a bill estimate.</p>
      {detail.cost_estimate_method && <p>Method: {detail.cost_estimate_method}</p>}
      {detail.cost_estimate_source && <p>Source: {detail.cost_estimate_source}</p>}
      {detail.cost_estimate_pricing_date && <p>Pricing date: {detail.cost_estimate_pricing_date}</p>}
      {assumptions.length > 0 && (
        <details>
          <summary class="cursor-pointer font-medium">Assumptions ({assumptions.length})</summary>
          <ul class="mt-1 list-disc space-y-0.5 pl-4">
            {assumptions.map((assumption, index) => <li key={index}>{assumption}</li>)}
          </ul>
        </details>
      )}
    </div>,
  );
}

function overrideRows(detail: DetailResult): ReceiptRow[] {
  const rules = parseOverrideRules(detail.override_rules);
  const status = detail.validation_status;
  if (rules.length === 0) {
    if (status === null || status === undefined || status === "") return [missingRow("Validation")];
    return [recordedRow("Validation", <ValidationBadge validationStatus={status} showMissing />)];
  }
  const validation = <ValidationBadge validationStatus={status} overrideRules={rules} showMissing />;
  const details = overrideDetails(detail);
  return [
    recordedRow("Validation", validation),
    ...(details.length > 0 ? [recordedRow("Override", <span class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">{details}</span>)] : []),
  ];
}

function queryIdRepairRows(detail: DetailResult): ReceiptRow[] {
  const repairs = (detail.query_id_repairs ?? "")
    .split(";")
    .map((entry) => entry.trim())
    .filter(Boolean);
  if (repairs.length === 0) return [];
  return [
    recordedRow(
      "Query ID repairs",
      <div class="space-y-1">
        <p class="min-w-0 break-words">
          {repairs.length} query {repairs.length === 1 ? "ID" : "IDs"} repaired for display: {repairs.join("; ")}
        </p>
        <p class="text-xs text-[var(--bb-data-fg-muted)]">
          The published bundle stores these IDs without their leading q; the original bundle is unchanged.
        </p>
      </div>,
    ),
  ];
}

function overrideDetails(detail: DetailResult): ComponentChildren[] {
  const parts: ComponentChildren[] = [];
  if (detail.override_approver?.trim()) {
    parts.push(<span key="approver" class="min-w-0 break-words">by {detail.override_approver.trim()}</span>);
  }
  if (detail.override_evidence?.trim()) {
    parts.push(<OverrideEvidenceLink key="evidence" evidence={detail.override_evidence.trim()} />);
  }
  if (detail.override_expires?.trim()) {
    parts.push(<span key="expires" class="min-w-0 break-words">expires {detail.override_expires.trim()}</span>);
  }
  return parts;
}

function OverrideEvidenceLink({ evidence }: { evidence: string }) {
  const url = httpUrlOrNull(evidence);
  if (url === null) {
    return <span class="min-w-0 break-all font-mono">{evidence}</span>;
  }
  return (
    <a href={url.href} title={url.href} class="min-w-0 break-words text-xs font-medium no-underline" rel="noopener noreferrer">
      {evidenceLinkLabel(url)}
    </a>
  );
}

function httpUrlOrNull(value: string): URL | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url : null;
  } catch {
    return null;
  }
}

function evidenceLinkLabel(url: URL): string {
  const fileName = url.pathname.split("/").filter(Boolean).pop() ?? "";
  return fileName.includes(".") ? fileName : "Evidence link";
}

function formattedRow(label: string, raw: string | null | undefined, format: (raw: string) => string): ReceiptRow {
  if (raw === null || raw === undefined || raw === "") return missingRow(label);
  const value = format(raw);
  return recordedRow(label, value === raw ? value : <span title={`Recorded value: ${raw}`}>{value}</span>);
}

function hashRow(label: string, raw: string | null | undefined): ReceiptRow {
  if (raw === null || raw === undefined || raw === "") return missingRow(label);
  return recordedRow(
    label,
    <code class="font-mono text-xs" title={raw}>
      {shortHash(raw)}
    </code>,
  );
}

export function RunReceipt({
  detail,
  shortId = null,
  isRankingEligible = null,
  reproduceCommand = buildReproduceCommand(detail),
  plansUrl: plansUrlOverride,
}: RunReceiptProps) {
  const [showMissing, setShowMissing] = useState(false);
  const plansUrl = plansUrlOverride === undefined ? planDownloadUrl(detail) : plansUrlOverride;
  const queryCount = detail.display_timings.length || new Set(detail.queries.map((query) => query.query_id)).size;
  const sampleCount =
    detail.display_timings.reduce((sum, timing) => sum + timing.sample_count, 0) || detail.queries.length;
  const clientLocalityRow = buildClientLocalityRow(detail);

  const sections = [
    {
      title: "Workload",
      rows: [
        recordedRow("Benchmark", humanizeBenchmark(detail.benchmark)),
        recordedRow("Scale factor", formatScaleFactor(detail.scale_factor)),
        recordedRow("Run date", <RunDateChip runDate={detail.run_date} />),
        formattedRow("Test phase", detail.test_type, formatEnumLabel),
        recordedRow("Query count", String(queryCount)),
        recordedRow("Measurement samples", String(sampleCount)),
      ],
    },
    {
      title: "Platform",
      rows: [
        recordedRow("Platform", detail.platform),
        detail.platform_version ? recordedRow("Platform version", <VersionLabel version={detail.platform_version} />) : missingRow("Platform version"),
        detail.driver_version ? recordedRow("Driver version", <VersionLabel version={detail.driver_version} />) : missingRow("Driver version"),
        formattedRow("Execution mode", detail.execution_mode, formatExecutionMode),
        formattedRow("Tuning mode", detail.tuning_mode, formatTuningMode),
        rowFromString("Tuning hash", detail.tuning_hash),
        hashRow("Requested config hash", detail.requested_config_hash),
        hashRow("Applied ledger hash", detail.applied_ledger_hash),
        detail.tuning_validation_status
          ? recordedRow(
              "Tuning verification",
              <>
                <TuningVerificationBadge
                  status={detail.tuning_validation_status}
                  receipt={detail.applied_receipt}
                />
                <AppliedReceiptDrilldown raw={detail.applied_receipt} />
              </>,
            )
          : missingRow("Tuning verification"),
        rowFromString("Template evidence", detail.template_evidence),
      ],
    },
    {
      title: "Environment",
      rows: [
        rowFromString("OS", detail.environment.os),
        formattedRow("Architecture", detail.environment.arch, formatArchitecture),
        formattedRow("CPU family", detail.environment.cpu_family, formatCpuFamily),
        rowFromString("CPU model", detail.environment.cpu_model),
        rowFromString("CPU evidence", detail.environment.cpu_identity_provenance, formatCpuIdentityProvenance),
        rowFromString("CPU count", detail.environment.cpu_count),
        memoryRow(detail.environment.memory_gb),
        rowFromString("Python", detail.environment.python),
        ...(clientLocalityRow ? [clientLocalityRow] : []),
      ],
    },
    {
      title: "Integrity",
      rows: [
        rowFromString("Trust", detail.trust_label, formatTrustLabel),
        rowFromString("Funding", detail.funding, formatFunding),
        rowFromString("Visibility", detail.visibility, formatVisibility),
        ...overrideRows(detail),
        ...queryIdRepairRows(detail),
        rowFromString("Compliance", detail.compliance_class),
        rankingEligibilityRow(isRankingEligible),
      ],
    },
    {
      title: "Artifacts",
      rows: [
        recordedRow("Result ID", <code class="min-w-0 break-all font-mono text-xs">{detail.result_id}</code>),
        rowFromString("Short ID", shortId),
        bundleLinkRow(detail.bundle_download_url),
        plansRow(detail, plansUrl),
        reproduceRow(reproduceCommand),
      ],
    },
    {
      title: "Cost",
      rows: [
        recordedRow("Normalized cost", normalizedCostLabel(detail)),
        ...(hasCostEstimate(detail) ? [estimateProvenanceRow(detail)] : []),
        rowFromSummary("Cost model", costModelSummary(detail)),
        rowFromSummary("Cost scope", costScopeSummary(detail)),
      ],
    },
  ];

  const totalMissing = sections.reduce(
    (sum, section) => sum + section.rows.filter((row) => row.isMissing).length,
    0,
  );

  return (
    <section id="run-receipt" aria-label="Run receipt" class="panel-elevated p-4">
      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 class="text-base font-semibold text-[var(--bb-data-fg-primary)]">Run receipt</h2>
          <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">Recorded details for checking and reproducing this run.</p>
        </div>
        <StatusBadge role="generic" tone="neutral">Public ID {shortId ?? visibleResultIdForRow(detail)}</StatusBadge>
      </div>
      {totalMissing > 0 && (
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--bb-data-fg-muted)]">
          <p>{totalMissing} field{totalMissing === 1 ? "" : "s"} not recorded for this run.</p>
          <button
            type="button"
            class="cursor-pointer border-0 bg-transparent p-0 text-xs text-[var(--bb-accent-hover)] underline hover:text-[var(--bb-accent)]"
            onClick={() => setShowMissing((value) => !value)}
            aria-expanded={showMissing}
            aria-controls="run-receipt-missing-fields-region"
          >
            {showMissing ? "Hide missing fields" : "Show missing fields"}
          </button>
        </div>
      )}
      <div
        id="run-receipt-missing-fields-region"
        class="grid gap-4 lg:grid-cols-2"
      >
        {sections.map((section) => (
          <ReceiptSection
            key={section.title}
            title={section.title}
            rows={section.rows}
            showMissing={showMissing}
          />
        ))}
      </div>
    </section>
  );
}

function ReceiptSection({
  title,
  rows,
  showMissing,
}: {
  title: string;
  rows: ReceiptRow[];
  showMissing: boolean;
}) {
  const recordedRows = rows.filter((row) => !row.isMissing);
  const visibleRows = showMissing ? rows : recordedRows;

  return (
    <section aria-labelledby={`run-receipt-${slug(title)}`} class="border-t border-[var(--bb-data-border)] pt-3">
      <h3 id={`run-receipt-${slug(title)}`} class="mb-2 text-xs font-semibold uppercase text-[var(--bb-data-fg-subtle)]">
        {title}
      </h3>
      {recordedRows.length === 0 && !showMissing ? (
        <p class="text-xs text-[var(--bb-data-fg-subtle)]">No {title.toLowerCase()} metadata recorded.</p>
      ) : (
        <dl class="grid grid-cols-[minmax(7rem,0.8fr)_minmax(0,1.2fr)] gap-x-3 gap-y-2 text-sm">
          {visibleRows.map((row) => (
            <div key={row.label} class="contents" data-missing={row.isMissing ? "true" : undefined}>
              <dt class="text-xs text-[var(--bb-data-fg-muted)]">{row.label}</dt>
              <dd
                class={`min-w-0 break-words text-xs font-medium ${
                  row.isMissing
                    ? "text-[var(--bb-data-fg-subtle)] italic"
                    : "text-[var(--bb-data-fg-primary)]"
                }`}
              >
                {row.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

interface AppliedReceiptEntry {
  statement?: unknown;
  phase?: unknown;
  verdict?: unknown;
  kind?: unknown;
  table?: unknown;
  expected_columns?: unknown;
  observed_columns?: unknown;
  diff?: unknown;
  reason?: unknown;
  detail?: unknown;
}

interface ParsedAppliedReceipt {
  entries: AppliedReceiptEntry[];
  truncated: boolean;
  originalEntryCount?: number;
}

function parseAppliedReceipt(raw: string | null | undefined): ParsedAppliedReceipt {
  const empty = { entries: [], truncated: false };
  if (typeof raw !== "string" || raw.trim() === "") return empty;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return empty;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return empty;
  const receipt = parsed as {
    entries?: unknown;
    original_entry_count?: unknown;
    truncated?: unknown;
  };
  if (!Array.isArray(receipt.entries)) return empty;
  return {
    entries: receipt.entries.filter(
      (entry): entry is AppliedReceiptEntry =>
        entry !== null && typeof entry === "object" && !Array.isArray(entry),
    ),
    truncated: receipt.truncated === true,
    originalEntryCount:
      typeof receipt.original_entry_count === "number" ? receipt.original_entry_count : undefined,
  };
}

function receiptText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value.trim() === "" ? null : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const parts = value.map(receiptText).filter((part): part is string => part !== null);
    return parts.length === 0 ? null : parts.join(", ");
  }
  try {
    return JSON.stringify(value);
  } catch {
    return null;
  }
}

function AppliedReceiptDrilldown({ raw }: { raw?: string | null }) {
  const { entries, truncated, originalEntryCount } = parseAppliedReceipt(raw);
  if (entries.length === 0 && !truncated) return null;
  const entryCount =
    truncated && originalEntryCount !== undefined
      ? `${entries.length} of ${originalEntryCount}; truncated`
      : truncated
        ? `${entries.length}; truncated`
        : String(entries.length);

  return (
    <details class="mt-2" data-testid="applied-receipt-drilldown">
      <summary class="cursor-pointer select-none text-xs text-[var(--bb-data-fg-muted)] hover:text-[var(--bb-data-fg-primary)]">
        Receipt entries ({entryCount})
      </summary>
      {truncated && (
        <p class="mt-2 text-xs text-[var(--bb-tone-warning-fg)]">
          The published receipt exceeded the defensive receipt bound; the list below is incomplete.
        </p>
      )}
      <ul class="mt-2 list-none space-y-2 p-0">
        {entries.map((entry, index) => (
          <li
            key={index}
            class="border-t border-[var(--bb-data-border)] pt-2"
            data-testid="applied-receipt-entry"
          >
            <AppliedReceiptEntryBody entry={entry} />
          </li>
        ))}
      </ul>
    </details>
  );
}

function AppliedReceiptEntryBody({ entry }: { entry: AppliedReceiptEntry }) {
  const rawVerdict = receiptText(entry.verdict);
  const verdict =
    rawVerdict === "attested" ? "attested — Recorded; not catalog-checkable" : rawVerdict;
  const statement = receiptText(entry.statement);
  const fields: { label: string; value: string }[] = [
    { label: "Kind", value: receiptText(entry.kind) },
    { label: "Phase", value: receiptText(entry.phase) },
    { label: "Table", value: receiptText(entry.table) },
    { label: "Expected columns", value: receiptText(entry.expected_columns) },
    { label: "Observed columns", value: receiptText(entry.observed_columns) },
    { label: "Diff", value: receiptText(entry.diff) },
    { label: "Reason", value: receiptText(entry.reason) },
    { label: "Detail", value: receiptText(entry.detail) },
  ].filter((field): field is { label: string; value: string } => field.value !== null);

  return (
    <>
      {verdict !== null && (
        <p class="text-xs font-semibold text-[var(--bb-data-fg-primary)]">{verdict}</p>
      )}
      {statement !== null && (
        <code class="mt-1 block break-words font-mono text-xs text-[var(--bb-data-fg-muted)]">
          {statement}
        </code>
      )}
      {fields.length > 0 && (
        <dl class="mt-1 grid grid-cols-[minmax(6rem,auto)_minmax(0,1fr)] gap-x-3 gap-y-1">
          {fields.map((field) => (
            <div key={field.label} class="contents">
              <dt class="text-xs text-[var(--bb-data-fg-muted)]">{field.label}</dt>
              <dd class="min-w-0 break-words text-xs text-[var(--bb-data-fg-primary)]">{field.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}

function bundleLinkRow(url: string): ReceiptRow {
  if (!url) return missingRow("Bundle");
  return recordedRow(
    "Bundle",
    <a href={url} class="text-xs font-medium no-underline">
      Download bundle
    </a>,
  );
}

function plansRow(detail: DetailResult, plansUrl: string | null): ReceiptRow {
  if (!detail.has_plans) return recordedRow("Plans", "Plans not published");
  if (plansUrl === null) return recordedRow("Plans", "Plans recorded; file not published");
  return recordedRow(
    "Plans",
    <>
      <a href="#execution-plans" class="text-xs font-medium no-underline">
        View plans
      </a>
      <span aria-hidden="true"> · </span>
      <a href={plansUrl} class="text-xs font-medium no-underline" download>
        Download plans
      </a>
    </>,
  );
}

function reproduceRow(reproduceCommand: string | null): ReceiptRow {
  if (!reproduceCommand) return missingRow("Reproduce");
  return recordedRow("Reproduce", <code class="min-w-0 break-words font-mono text-xs">{reproduceCommand}</code>);
}

function memoryRow(value: number | null | undefined): ReceiptRow {
  if (value === null || value === undefined) return missingRow("Memory");
  const formatted = formatMemoryGb(value);
  return recordedRow("Memory", <span title={`Recorded value: ${value} GB`}>{formatted}</span>);
}

function buildClientLocalityRow(detail: DetailResult): ReceiptRow | null {
  const env = detail.environment ?? {};
  const region = env.client_region ?? detail.client_region;
  const cloud = env.client_cloud ?? detail.client_cloud;
  const minMs = env.statement_overhead_min_ms ?? detail.statement_overhead_min_ms;
  const medMs = env.statement_overhead_median_ms ?? detail.statement_overhead_median_ms;
  const status = env.link_status ?? detail.link_status;

  const localityParts = [region, cloud].filter((p): p is string => Boolean(p && String(p).trim()));
  const overheadParts: string[] = [];
  if (minMs != null && Number.isFinite(Number(minMs))) {
    overheadParts.push(`${formatLatencyMs(Number(minMs)).valueText} min`);
  }
  if (medMs != null && Number.isFinite(Number(medMs))) {
    overheadParts.push(`${formatLatencyMs(Number(medMs)).valueText} median`);
  }

  if (localityParts.length === 0 && overheadParts.length === 0 && !status) {
    return null;
  }

  const localityStr = localityParts.length > 0 ? localityParts.join(" / ") : "";
  const overheadStr = overheadParts.length > 0 ? `overhead: ${overheadParts.join(", ")}` : "";

  const displayParts: string[] = [];
  if (localityStr && overheadStr) {
    displayParts.push(`${localityStr} (${overheadStr})`);
  } else if (localityStr || overheadStr) {
    displayParts.push(localityStr || overheadStr);
  }
  if (status && String(status).trim()) {
    displayParts.push(`[${String(status).trim()}]`);
  }

  return recordedRow("Client locality", displayParts.join(" "));
}

function rankingEligibilityRow(value: boolean | null | undefined): ReceiptRow {
  if (value === null || value === undefined) return missingRow("Ranking eligibility");
  return recordedRow("Ranking eligibility", value ? "Eligible" : "Not eligible");
}

export function planDownloadUrl(detail: Pick<DetailResult, "plans_published" | "has_plans" | "bundle_download_url">) {
  if (!detail.plans_published) return null;
  if (!detail.has_plans || !detail.bundle_download_url) return null;
  if (!detail.bundle_download_url.endsWith(".json")) return null;
  const trustedOrigin = typeof window === "undefined" ? "https://benchbox.dev" : window.location.origin;
  try {
    const bundleUrl = new URL(detail.bundle_download_url, trustedOrigin);
    if (bundleUrl.origin !== trustedOrigin || !["http:", "https:"].includes(bundleUrl.protocol)) return null;
    if (bundleUrl.username || bundleUrl.password) return null;
  } catch {
    return null;
  }
  return detail.bundle_download_url.replace(/\.json$/, ".plans.json");
}

function buildReproduceCommand(detail: DetailResult) {
  const parts = [
    "benchbox run",
    `--platform ${detail.platform_id}`,
    `--benchmark ${detail.benchmark}`,
    `--scale ${detail.scale_factor}`,
  ];
  if (detail.test_type) parts.push(`--phases ${detail.test_type}`);
  return parts.join(" ");
}

function slug(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
