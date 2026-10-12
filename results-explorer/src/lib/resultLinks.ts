import { formatRunDateWithAge } from "@/lib/runAge";

export const MAX_COMPARE_SELECTIONS = 10;

export interface ResultLinkSource {
  result_id: string;
  short_id?: string | null;
  platform?: string | null;
  run_date?: string | null;
}

export function compareIdForRow(row: { result_id: string; short_id?: string | null }): string {
  return row.short_id || row.result_id;
}

export function resultDetailHref(rowOrId: ResultLinkSource | string): string {
  return `/results/r/${typeof rowOrId === "string" ? rowOrId : rowOrId.result_id}`;
}

export function resultReceiptHref(rowOrId: ResultLinkSource | string): string {
  return `${resultDetailHref(rowOrId)}#run-receipt`;
}

export function publicResultIdToken(resultId: string): string {
  const parts = resultId.split(/[-_./:]+/).filter(Boolean);
  const tail = parts.at(-1);
  return tail && /^[0-9a-f]{8,}$/i.test(tail) ? tail : resultId;
}

export function visibleResultIdForRow(row: ResultLinkSource): string {
  return publicResultIdToken(row.result_id);
}

export function resultIdentityAriaLabel(row: ResultLinkSource, target: "details" | "receipt"): string {
  const platform = row.platform ? `${row.platform} ` : "";
  const date = row.run_date ? ` from ${formatRunDateWithAge(row.run_date)}` : "";
  return `Open ${target} for ${platform}public ID ${visibleResultIdForRow(row)}${date}`;
}

export function buildCompareUrl(ids: readonly string[]): string {
  return `/results/compare?ids=${ids.map(encodeURIComponent).join(",")}`;
}

export function displayCompareId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}...` : id;
}

export const MIN_WITHIN_RUN_BASES = 2;
export const MAX_WITHIN_RUN_BASES = 4;

export function withinRunCompareHref(
  resultId: string,
  encodedBases: readonly string[],
  referenceIndex = 0,
): string {
  const bases = encodedBases.join(",");
  const ref = Math.min(Math.max(0, referenceIndex), Math.max(0, encodedBases.length - 1));
  return `/results/r/${encodeURIComponent(resultId)}/passes?bases=${encodeURIComponent(bases)}&ref=${ref}`;
}

export function clampReferenceIndex(referenceIndex: number, columnCount: number): number {
  if (columnCount <= 0) return 0;
  if (!Number.isFinite(referenceIndex)) return 0;
  return Math.min(Math.max(0, Math.trunc(referenceIndex)), columnCount - 1);
}
