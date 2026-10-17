export const SUBSCALE_BADGE = "Subscale · Unofficial";
export const NONSTANDARD_BADGE = "Non-standard · Unofficial";
export const DERIVED_POWER_SOURCE = "derived_from_timings";

const SUBSCALE_TPC_NAMES: Record<string, string> = { tpch: "TPC-H", tpcds: "TPC-DS" };

export function subscaleTpcName(benchmark: string | null | undefined, scaleFactor: number | string | null | undefined) {
  const name = SUBSCALE_TPC_NAMES[(benchmark ?? "").toLowerCase().replace(/[^a-z0-9]/g, "")];
  const scale = Number(scaleFactor);
  return name && Number.isFinite(scale) && scale > 0 && scale < 1 ? name : null;
}

export function isSubscaleTpc(benchmark: string | null | undefined, scaleFactor: number | string | null | undefined) {
  return subscaleTpcName(benchmark, scaleFactor) !== null;
}

export function subscaleCohortNote(benchmarkName: string): string {
  return (
    `SF<1 is below ${benchmarkName}'s minimum scale. Power@Size uses the TPC formula for comparison ` +
    "within this cohort only; not a TPC result."
  );
}

export function complianceBadge(complianceClass: string | null | undefined): string | null {
  switch (complianceClass) {
    case "unofficial_subscale":
      return SUBSCALE_BADGE;
    case "unofficial_nonstandard":
      return NONSTANDARD_BADGE;
    default:
      return null;
  }
}

export function powerScoreTitle(subscale: boolean): string {
  return subscale ? "Power@Size (subscale, unofficial)" : "Power@Size";
}

export function isDerivedPowerScore(source: string | null | undefined): boolean {
  return source === DERIVED_POWER_SOURCE;
}

export const DERIVED_POWER_NOTE =
  "Derived: this run stored no Power@Size, so it is computed from the exported query timings with the same formula.";

export function complianceExportCaption(complianceClass: unknown): string {
  if (complianceClass === "unofficial_subscale") {
    return (
      `${SUBSCALE_BADGE}: below the TPC minimum scale factor. Power@Size uses the TPC formula for ` +
      "comparison within its cohort only; not a TPC result."
    );
  }
  if (complianceClass === "unofficial_nonstandard") return `${NONSTANDARD_BADGE}: not a TPC result.`;
  return "";
}

export function exportColumnsWithCompliance(visibleColumns: readonly string[]): string[] {
  const columns = [...visibleColumns];
  if (!columns.includes("compliance_class")) columns.push("compliance_class");
  if (columns.includes("power_score") && !columns.includes("power_score_source")) columns.push("power_score_source");
  return columns;
}

export function withComplianceCaption<T extends Record<string, unknown>>(row: T): T & { compliance_note: string } {
  return { ...row, compliance_note: complianceExportCaption(row.compliance_class) };
}
