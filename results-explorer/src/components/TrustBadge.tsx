import { describeOverride, describeValidationStatus } from "@/lib/displayLabels";
import { StatusBadge, type StatusTone } from "./StatusBadge";

const TRUST_CONFIG: Record<string, { label: string; tone: StatusTone; title: string }> = {
  "maintainer-run": {
    label: "Maintainer",
    tone: "success",
    title: "A BenchBox maintainer ran and reviewed this result.",
  },
  "community-submission": {
    label: "Community",
    tone: "info",
    title: "A community member submitted this result. Open it to review the source details.",
  },
  "vendor-supplied": {
    label: "Vendor",
    tone: "warning",
    title:
      "The platform vendor produced this result. Compare it with independent results before drawing conclusions.",
  },
  "ci-verified": {
    label: "CI",
    tone: "neutral",
    title: "An automated test run produced this result.",
  },
  "ci-validated": {
    label: "CI",
    tone: "neutral",
    title: "An automated test run produced this result.",
  },
  ci: {
    label: "CI",
    tone: "neutral",
    title: "An automated test run produced this result.",
  },
  "local-run": {
    label: "Local",
    tone: "neutral",
    title: "This result came from a developer machine, so its environment may differ from other runs.",
  },
  local: {
    label: "Local",
    tone: "neutral",
    title: "This result came from a developer machine, so its environment may differ from other runs.",
  },
  "unofficial-research": {
    label: "Unofficial",
    tone: "warning",
    title: "This result used a nonstandard configuration and is not included in rankings.",
  },
};

const DEFAULT_CONFIG = {
  label: "Unknown",
  tone: "neutral" as StatusTone,
  title: "The source of this result was not recorded.",
};

export function trustLabelDescription(trustLabel: string): string {
  return TRUST_CONFIG[trustLabel]?.title ?? DEFAULT_CONFIG.title;
}

interface TrustBadgeProps {
  trustLabel: string;
  compact?: boolean;
}

interface ValidationBadgeProps {
  validationStatus?: string | null;
  showMissing?: boolean;
  overrideRules?: string[] | null;
}

export interface OverrideBadgeProps {
  rules?: string[] | null;
  approver?: string | null;
  evidence?: string | null;
  expires?: string | null;
  compact?: boolean;
}

export function TrustBadge({ trustLabel, compact = false }: TrustBadgeProps) {
  const known = trustLabel ? TRUST_CONFIG[trustLabel] : undefined;
  const config =
    known ??
    (trustLabel
      ? {
          ...DEFAULT_CONFIG,
          label: trustLabel,
          title: `The source label “${trustLabel}” is not recognized. Contact the BenchBox maintainers for details.`,
        }
      : DEFAULT_CONFIG);
  const text = compact ? (config.label.split(" ")[0] ?? config.label) : config.label;
  return (
    <StatusBadge role="trust" tone={config.tone} title={config.title}>
      {text}
    </StatusBadge>
  );
}

export function ValidationBadge({ validationStatus, showMissing = false, overrideRules }: ValidationBadgeProps) {
  const override = describeOverride(overrideRules ?? []);
  if (!validationStatus && !showMissing && !override) return null;
  if (!validationStatus) {
    if (override) {
      return (
        <StatusBadge
          role="validation"
          tone="warning"
          title={override.title}
          ariaLabel={`${override.fullLabel}. ${override.title}`}
        >
          {override.label}
        </StatusBadge>
      );
    }
    return (
      <StatusBadge role="validation" tone="neutral" title="No validation status was recorded for this result.">
        Not recorded
      </StatusBadge>
    );
  }
  const info = describeValidationStatus(validationStatus);
  if (override) {
    const fullTitle = `${info.description} Recorded status: ${info.status}. ${override.title}`;
    return (
      <StatusBadge role="validation" tone="warning" title={fullTitle} ariaLabel={`${override.fullLabel}. ${fullTitle}`}>
        {override.label}
      </StatusBadge>
    );
  }
  return (
    <StatusBadge role="validation" tone={info.tone} title={`${info.description} Recorded status: ${info.status}.`}>
      {info.label}
    </StatusBadge>
  );
}

export function OverrideBadge({ rules, approver, evidence, expires, compact = false }: OverrideBadgeProps) {
  const override = describeOverride(rules ?? [], { approver, expires });
  if (!override) return null;
  const text = compact ? "Overridden" : override.label;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <StatusBadge
        role="override"
        tone="warning"
        title={override.title}
        ariaLabel={`${override.fullLabel}. ${override.title}`}
      >
        {text}
      </StatusBadge>
      {!compact && approver ? (
        <span className="text-xs text-[var(--bb-data-fg-muted)]" title={`Override approved by ${approver}`}>
          by {approver}
        </span>
      ) : null}
      {!compact && evidence ? (
        <span className="font-mono text-xs text-[var(--bb-data-fg-muted)]" title={`Override evidence: ${evidence}`}>
          {evidence}
        </span>
      ) : null}
      {!compact && expires ? (
        <span className="text-xs text-[var(--bb-data-fg-muted)]" title={`Override expires ${expires}`}>
          expires {expires}
        </span>
      ) : null}
    </span>
  );
}


export default TrustBadge;
