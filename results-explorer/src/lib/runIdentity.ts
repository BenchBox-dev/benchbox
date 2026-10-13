import { platformVersionDisplay, splitVersion } from "@/lib/versionLabel";

import { formatRunDate, formatRunDateWithAge } from "@/lib/runAge";
import { formatScaleFactor } from "@/lib/metricFormatters";

export interface RunIdentitySource {
  result_id: string;
  short_id?: string | null;
  platform: string;
  platform_version?: string | null;
  driver_version?: string | null;
  run_date?: string | null;
  scale_factor?: number | null;
  deployment_class?: string | null;
  instance_or_warehouse?: string | null;
  trust_label?: string | null;
}

export type RunIdentityVariant =
  | "compact"
  | "chart"
  | "table"
  | "selectOption"
  | "tooltip";

export interface RunIdentityLabels {
  compact: string;
  disambiguated: string;
  table: string;
  full: string;
  title: string;
  ariaLabel: string;
}

interface QualifierDescriptor {
  key: string;
  value: (source: RunIdentitySource) => string | null;
}

interface QualifierSlot {
  key: string;
  value: string;
}

const RESULT_ID_QUALIFIER_KEYS = new Set(["short_result_id", "result_id"]);

const NATURAL_QUALIFIERS: QualifierDescriptor[] = [
  {
    key: "version",
    value: (s) => {
      const parts = splitVersion(s.platform_version ?? null);
      return parts ? `${parts.core}${parts.suffix ? "…" : ""}` : null;
    },
  },
  { key: "run_date", value: (s) => (s.run_date ? formatRunDate(s.run_date) : null) },
  {
    key: "scale_factor",
    value: (s) => (s.scale_factor !== null && s.scale_factor !== undefined ? formatScaleFactor(s.scale_factor) : null),
  },
  {
    key: "deployment",
    value: (s) => {
      const parts: string[] = [];
      if (s.deployment_class && s.deployment_class !== "local") parts.push(s.deployment_class);
      if (s.instance_or_warehouse) parts.push(s.instance_or_warehouse);
      return parts.length > 0 ? parts.join(" ") : null;
    },
  },
  { key: "trust_label", value: (s) => (s.trust_label ? s.trust_label : null) },
];

const NATURAL_QUALIFIERS_CHART: QualifierDescriptor[] = NATURAL_QUALIFIERS.filter(
  (qualifier) => qualifier.key !== "run_date",
);

function qualifiersForVariant(variant: RunIdentityVariant): QualifierDescriptor[] {
  return variant === "chart" ? NATURAL_QUALIFIERS_CHART : NATURAL_QUALIFIERS;
}

function describeNaturalQualifiers(source: RunIdentitySource): string[] {
  const out: string[] = [];
  for (const qualifier of NATURAL_QUALIFIERS) {
    const value = qualifier.value(source);
    if (value !== null && value !== "") out.push(value);
  }
  return out;
}

function shortResultIdToken(resultId: string): string {
  const parts = resultId.split(/[-_./:]+/).filter(Boolean);
  if (parts.length > 1) return parts[parts.length - 1]!;
  return resultId.slice(-8);
}

function compactIdToken(source: RunIdentitySource): string {
  const shortId = source.short_id?.trim();
  return shortId && /^[0-9a-f]{8,}$/i.test(shortId) ? shortId : shortResultIdToken(source.result_id);
}

function describeCohortQualifierSlots(source: RunIdentitySource, qualifiers: readonly QualifierDescriptor[]): QualifierSlot[] {
  const slots: QualifierSlot[] = [];
  for (const qualifier of qualifiers) {
    const value = qualifier.value(source);
    if (value !== null && value !== "") slots.push({ key: qualifier.key, value });
  }
  slots.push({ key: "short_result_id", value: compactIdToken(source) });
  slots.push({ key: "result_id", value: source.result_id });
  return slots;
}

function distinguishingQualifierKeys(slotsBySource: readonly QualifierSlot[][], indices: readonly number[]): Set<string> {
  const keys = new Set(slotsBySource.flatMap((slots) => slots.map((slot) => slot.key)));
  const distinguishing = new Set<string>();
  for (const key of keys) {
    const values = new Set(
      indices.map((index) => slotsBySource[index]!.find((slot) => slot.key === key)?.value ?? ""),
    );
    if (values.size > 1) distinguishing.add(key);
  }
  return distinguishing;
}

function naturalQualifierLists(
  slotsBySource: readonly QualifierSlot[][],
  indices: readonly number[],
  distinguishingKeys: ReadonlySet<string>,
): Map<number, string[]> {
  const filteredNaturalSlots = new Map(
    indices.map((i) => [
      i,
      slotsBySource[i]!.filter(
        (slot) => !RESULT_ID_QUALIFIER_KEYS.has(slot.key) && distinguishingKeys.has(slot.key),
      ),
    ]),
  );
  const allNaturalSlots = new Map(
    indices.map((i) => [
      i,
      slotsBySource[i]!.filter((slot) => !RESULT_ID_QUALIFIER_KEYS.has(slot.key)),
    ]),
  );
  const filteredLabels = indices.map((i) => filteredNaturalSlots.get(i)!.map((slot) => slot.value).join("\u0000"));
  const filteredNaturalLabelsAreUnique = new Set(filteredLabels).size === filteredLabels.length;

  return new Map(
    indices.map((i) => {
      const filtered = filteredNaturalSlots.get(i)!;
      const allNatural = allNaturalSlots.get(i)!;
      if (!filteredNaturalLabelsAreUnique || (filtered.length === 0 && allNatural.length > 0)) {
        return [i, allNatural.map((slot) => slot.value)];
      }
      return [i, filtered.map((slot) => slot.value)];
    }),
  );
}

function findUniqueQualifierRound(
  sources: readonly RunIdentitySource[],
  indices: readonly number[],
  qualifierLists: ReadonlyMap<number, string[]>,
): number | null {
  const maxQualifiers = Math.max(...indices.map((i) => qualifierLists.get(i)!.length));
  for (let round = 0; round <= maxQualifiers; round += 1) {
    const provisional = indices.map((i) => {
      const used = qualifierLists.get(i)!.slice(0, round);
      return `${sources[i]!.platform}${used.length > 0 ? " " + used.join(" ") : ""}`;
    });
    if (new Set(provisional).size === provisional.length) return round;
  }
  return null;
}

function joinForVariant(base: string, qualifiers: string[], variant: RunIdentityVariant): string {
  if (qualifiers.length === 0) return base;
  switch (variant) {
    case "compact":
    case "chart":
      return `${base} ${qualifiers.join(" ")}`;
    case "table":
    case "selectOption":
      return `${base} · ${qualifiers.join(" · ")}`;
    case "tooltip":
      return `${base}\n${qualifiers.join("\n")}`;
    default:
      return base;
  }
}

function withTooltipDriver(label: string, source: RunIdentitySource, variant: RunIdentityVariant): string {
  if (variant !== "tooltip") return label;
  const driver = platformVersionDisplay(source).driverTitle;
  return driver ? `${label}\n${driver}` : label;
}

export function formatRunIdentity(source: RunIdentitySource, variant: RunIdentityVariant): string {
  return withTooltipDriver(joinForVariant(source.platform, describeNaturalQualifiers(source), variant), source, variant);
}

export function formatRunIdentitiesForCohort(
  sources: readonly RunIdentitySource[],
  variant: RunIdentityVariant,
): string[] {
  const baseLabels = sources.map((source) => source.platform);
  const bucketsByLabel = new Map<string, number[]>();
  baseLabels.forEach((label, i) => {
    if (!bucketsByLabel.has(label)) bucketsByLabel.set(label, []);
    bucketsByLabel.get(label)!.push(i);
  });

  const qualifiers = qualifiersForVariant(variant);
  const slotsBySource = sources.map((source) => describeCohortQualifierSlots(source, qualifiers));
  const usedQualifiers = sources.map((): string[] => []);

  for (const [, indices] of bucketsByLabel) {
    if (indices.length < 2) continue;
    const distinguishingKeys = distinguishingQualifierKeys(slotsBySource, indices);
    let qualifierLists = naturalQualifierLists(slotsBySource, indices, distinguishingKeys);
    let round = findUniqueQualifierRound(sources, indices, qualifierLists);
    if (round === null) {
      qualifierLists = new Map(indices.map((i) => [i, slotsBySource[i]!.map((slot) => slot.value)]));
      round =
        findUniqueQualifierRound(sources, indices, qualifierLists) ??
        Math.max(...indices.map((i) => qualifierLists.get(i)!.length));
    }
    for (const i of indices) usedQualifiers[i] = qualifierLists.get(i)!.slice(0, round);
  }

  return sources.map((source, i) => {
    return withTooltipDriver(joinForVariant(source.platform, usedQualifiers[i]!, variant), source, variant);
  });
}

export function formatRunIdentityFull(source: RunIdentitySource): string {
  const version = platformVersionDisplay(source);
  const parts = [
    source.platform,
    version.text,
    version.driverTitle,
    source.run_date ? formatRunDateWithAge(source.run_date) : null,
    source.scale_factor !== null && source.scale_factor !== undefined ? formatScaleFactor(source.scale_factor) : null,
    NATURAL_QUALIFIERS.find((qualifier) => qualifier.key === "deployment")?.value(source) ?? null,
    source.trust_label ?? null,
    compactIdToken(source),
  ].filter((part): part is string => part !== null && part !== "");
  return parts.join(" · ");
}

export function formatRunIdentityLabelsForCohort(
  sources: readonly RunIdentitySource[],
): RunIdentityLabels[] {
  const compact = formatRunIdentitiesForCohort(sources, "compact");
  const disambiguated = formatRunIdentitiesForCohort(sources, "chart");
  const table = formatRunIdentitiesForCohort(sources, "table");
  return sources.map((source, index) => {
    const full = formatRunIdentityFull(source);
    return {
      compact: compact[index] ?? source.platform,
      disambiguated: disambiguated[index] ?? source.platform,
      table: table[index] ?? source.platform,
      full,
      title: full,
      ariaLabel: full,
    };
  });
}

export function truncateRunIdentityLabel(label: string, maxLen: number): string {
  if (label.length <= maxLen) return label;
  if (maxLen <= 1) return "…";

  const trailingToken = label.split(/\s+/).filter(Boolean).at(-1) ?? "";
  if (trailingToken.length >= 4 && trailingToken.length <= maxLen - 3) {
    const suffix = ` ${trailingToken}`;
    const headLen = Math.max(1, maxLen - suffix.length - 1);
    return `${label.slice(0, headLen).trimEnd()}…${suffix}`;
  }

  return middleTruncate(label, maxLen);
}

function middleTruncate(label: string, maxLen: number): string {
  if (label.length <= maxLen) return label;
  if (maxLen <= 1) return "…";
  const tailLen = Math.max(4, Math.floor((maxLen - 1) / 2));
  const headLen = Math.max(1, maxLen - tailLen - 1);
  return `${label.slice(0, headLen).trimEnd()}…${label.slice(-tailLen)}`;
}

export function preserveUniqueAfterTruncation(identities: readonly string[], maxLen: number): string[] {
  const truncated = identities.map((identity) => truncateRunIdentityLabel(identity, maxLen));
  const counts = new Map<string, number>();
  for (const value of truncated) counts.set(value, (counts.get(value) ?? 0) + 1);
  if ([...counts.values()].every((count) => count === 1)) return truncated;

  const repaired = identities.map((identity, index) => {
    const candidate = truncated[index]!;
    return (counts.get(candidate) ?? 0) > 1 ? middleTruncate(identity, maxLen) : candidate;
  });
  const repairedCounts = new Map<string, number>();
  for (const value of repaired) repairedCounts.set(value, (repairedCounts.get(value) ?? 0) + 1);
  if ([...repairedCounts.values()].every((count) => count === 1)) return repaired;

  return identities.map((identity, index) => {
    const candidate = repaired[index]!;
    return (repairedCounts.get(candidate) ?? 0) > 1 ? middleTruncate(`${identity} ${index + 1}`, maxLen) : candidate;
  });
}
