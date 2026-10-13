import { MAX_COMPARE_SELECTIONS } from "@/lib/resultLinks";

export const MIN_COMPARE_SELECTIONS = 2;

export interface CompareCtaProps {
  selectedIds: readonly string[];
  allIds: readonly string[];
  buildHref: (ids: readonly string[]) => string;
  testId?: string;
}

export function isCompareAllAvailable(allIds: readonly string[]): boolean {
  return allIds.length >= MIN_COMPARE_SELECTIONS && allIds.length <= MAX_COMPARE_SELECTIONS;
}

function sameIdSet(left: readonly string[], right: readonly string[]): boolean {
  if (new Set(left).size !== new Set(right).size) return false;
  const rightSet = new Set(right);
  return left.every((id) => rightSet.has(id));
}

export function CompareCta({ selectedIds, allIds, buildHref, testId = "compare-cta" }: CompareCtaProps) {
  if (selectedIds.length > MAX_COMPARE_SELECTIONS) {
    return (
      <div class="flex min-w-0 max-w-full flex-wrap items-center gap-2" data-testid="compare-cta-group">
        <button type="button" class="btn btn-primary max-w-full whitespace-normal text-center" disabled aria-disabled="true" data-testid={testId}>
          Select at most {MAX_COMPARE_SELECTIONS} runs
        </button>
      </div>
    );
  }

  if (selectedIds.length >= MIN_COMPARE_SELECTIONS) {
    const showCompareAll = isCompareAllAvailable(allIds) && !sameIdSet(selectedIds, allIds);
    return (
      <div class="flex min-w-0 max-w-full flex-wrap items-center gap-2" data-testid="compare-cta-group">
        <a href={buildHref(selectedIds)} class="btn btn-primary max-w-full whitespace-normal text-center no-underline" data-testid={testId}>
          Compare {selectedIds.length} selected
        </a>
        {showCompareAll && (
          <a href={buildHref(allIds)} class="btn btn-secondary max-w-full whitespace-normal text-center no-underline" data-testid={`${testId}-all`}>
            Compare All Runs
          </a>
        )}
      </div>
    );
  }

  if (isCompareAllAvailable(allIds)) {
    return (
      <div class="flex min-w-0 max-w-full flex-wrap items-center gap-2" data-testid="compare-cta-group">
        <a href={buildHref(allIds)} class="btn btn-primary max-w-full whitespace-normal text-center no-underline" data-testid={testId}>
          Compare All Runs
        </a>
      </div>
    );
  }

  return (
    <div class="flex min-w-0 max-w-full flex-wrap items-center gap-2" data-testid="compare-cta-group">
      <button type="button" class="btn btn-primary max-w-full whitespace-normal text-center" disabled aria-disabled="true" data-testid={testId}>
        Select 2–{MAX_COMPARE_SELECTIONS} runs to compare
      </button>
    </div>
  );
}
