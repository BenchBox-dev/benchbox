import { fitLabel, fitLabelMiddle } from "@/lib/chartLabels";
import { isValidTimingValue } from "@/lib/displayEligibility";

export interface RunRow<T> {
  run: T;
  sourceIndex: number;
}

export function orderRunsByDisplayGeomean<T extends { display_geomean_ms: number | null }>(
  runs: readonly T[],
): RunRow<T>[] {
  return runs
    .map((run, sourceIndex) => ({ run, sourceIndex }))
    .sort((left, right) => {
      const leftKey = geomeanSortKey(left.run);
      const rightKey = geomeanSortKey(right.run);
      if (leftKey !== rightKey) return leftKey < rightKey ? -1 : 1;
      return left.sourceIndex - right.sourceIndex;
    });
}

function geomeanSortKey(run: { display_geomean_ms: number | null }): number {
  return isValidTimingValue(run.display_geomean_ms) ? run.display_geomean_ms : Number.POSITIVE_INFINITY;
}

export function fitRunLabels(labels: readonly string[], maxWidthPx: number, fontSizePx: number): string[] {
  const fitted = labels.map((label) => fitLabel(label, maxWidthPx, fontSizePx).text);
  const occurrences = new Map<string, number>();
  for (const text of fitted) occurrences.set(text, (occurrences.get(text) ?? 0) + 1);
  return fitted.map((text, index) =>
    (occurrences.get(text) ?? 0) > 1 ? fitLabelMiddle(labels[index]!, maxWidthPx, fontSizePx).text : text,
  );
}
