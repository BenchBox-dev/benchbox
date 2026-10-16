import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { JSX } from "preact";
import type { BenchmarkSummary, PlatformRow, SortDirection, SortState } from "@/types";
import { TrustBadge, ValidationBadge } from "@/components/TrustBadge";
import { FundingChip } from "@/components/FundingChip";
import { parseOverrideRules } from "@/lib/displayLabels";
import { TableScrollHint } from "@/components/TableScrollHint";
import { fmtMs as formatDurationMs, fmtGeomean } from "@/utils";
import { formatCompactLatencyMs, formatScoreMetric, formatSpeedup, MISSING_VALUE_GLYPH, formatScaleFactor } from "@/lib/metricFormatters";
import { queryDisplayLabel, sortQueryIds } from "@/lib/queryLabels";
import { compareSelectionLabel } from "@/lib/compareCohort";
import { MAX_COMPARE_SELECTIONS, resultIdentityAriaLabel, resultReceiptHref } from "@/lib/resultLinks";
import { formatRunIdentitiesForCohort } from "@/lib/runIdentity";
import { RunDateChip } from "@/components/RunAge";
import { VersionLabel } from "@/components/VersionLabel";
import {
  describeCompareExclusionReason,
  type CompareExclusionReasonCopy,
} from "@/lib/compareExclusionReasons";
import {
  formatTimingExclusion,
  isComparable,
  isRankable,
  normalizePrimaryMetric,
  platformTimingValue,
  primaryMetricLabel,
  validPrimaryMetricValue,
} from "@/lib/displayEligibility";

import { colorForCell, lightnessForCell } from "@/lib/chartMath";
export { colorForCell, lightnessForCell };

const STICKY_COL_REM = {
  checkbox: 2.5,
  platform: 10,
  run: 6.5,
  primary: 6.5,
  geomean: 6,
  labels: 12,
} as const;

type StickyColKey = keyof typeof STICKY_COL_REM;

export const BENCHMARK_MATRIX_DENSITY_CONTRACT = {
  maxCollapsedRowHeightPx: 44,
  frozenColumns: [
    "selection",
    "platform identity",
    "run date",
    "primary metric",
    "secondary geomean",
    "labels",
  ] as const,
} as const;

const STICKY_ORDER: StickyColKey[] = ["checkbox", "platform", "run", "primary", "geomean", "labels"];

function cumulativeStickyLeft(
  options: { hasSelection: boolean; showGeomeanCol: boolean },
  target: StickyColKey,
): number {
  let offset = 0;
  for (const key of STICKY_ORDER) {
    if (key === target) return offset;
    if (key === "checkbox" && !options.hasSelection) continue;
    if (key === "geomean" && !options.showGeomeanCol) continue;
    offset += STICKY_COL_REM[key];
  }
  return offset;
}

function stickyWidthStyle(rem: number): JSX.CSSProperties {
  return { width: `${rem}rem`, minWidth: `${rem}rem` };
}

function stickyLeftStyle(rem: number): JSX.CSSProperties {
  return { left: `${rem}rem` };
}

interface QueryHeatmapProps {
  summary: BenchmarkSummary;
  selectedIds?: Set<string>;
  onSelectionChange?: (ids: Set<string>) => void;
  selectionLimitReasonId?: string;
  highContrast?: boolean;
  preserveOrder?: boolean;
  variant?: "default" | "card";
}

const MOBILE_OUTLIER_LIMIT = 3;

function CompareDisabledReason({ id, copy }: { id?: string; copy: CompareExclusionReasonCopy }) {
  return (
    <div id={id} class="mt-1 text-xs text-[var(--bb-data-fg-muted)]" data-testid="query-heatmap-disabled-reason">
      <span class="font-medium text-[var(--bb-tone-warning-fg)]">Why unavailable: {copy.shortText}</span>
      <span class="block">{copy.recoveryHint}</span>
    </div>
  );
}

export function QueryHeatmap({
  summary,
  selectedIds,
  onSelectionChange,
  selectionLimitReasonId,
  highContrast = false,
  preserveOrder = false,
  variant = "default",
}: QueryHeatmapProps) {
  const { query_ids, platforms, ranking } = summary;
  const sortedQueryIds = useMemo(
    () => (preserveOrder ? [...query_ids] : sortQueryIds(query_ids)),
    [query_ids, preserveOrder],
  );
  const isCard = variant === "card";
  const hasSelection = !isCard && onSelectionChange !== undefined;
  const showLabelsCol = !isCard;
  const selectionAtCap = selectedIds !== undefined && selectedIds.size >= MAX_COMPARE_SELECTIONS;
  const gridRef = useRef<HTMLTableElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const pageStickyHeaderRef = useRef<HTMLDivElement>(null);

  const [focusPos, setFocusPos] = useState({ row: 0, col: 0 });
  const [announcement, setAnnouncement] = useState("");

  const colMins = useMemo<Record<string, number | null>>(() => {
    const mins: Record<string, number | null> = {};
    for (const qid of sortedQueryIds) {
      const vals = platforms
        .map((p) => platformTimingValue(p, qid))
        .filter((v): v is number => v !== null);
      mins[qid] = vals.length > 0 ? Math.min(...vals) : null;
    }
    return mins;
  }, [platforms, sortedQueryIds]);

  const primaryMetric = normalizePrimaryMetric(ranking?.primary_metric);
  const higherIsBetter = ranking?.primary_order === "desc";
  const defaultPrimaryDirection: SortDirection = higherIsBetter ? "desc" : "asc";
  type MatrixSortKey = "platform" | "primary" | "geomean" | `query:${string}`;
  const [sort, setSort] = useState<SortState<MatrixSortKey> | null>(null);
  const activeSort = sort ?? { key: "primary", direction: defaultPrimaryDirection };
  const showGeomeanCol = ranking?.secondary_metric === "display_geomean_ms";

  function getPrimaryValue(row: PlatformRow): number | null {
    return validPrimaryMetricValue(row, primaryMetric);
  }

  function fmtPrimary(val: number | null): string {
    if (val === null) return MISSING_VALUE_GLYPH;
    return primaryMetric === "display_geomean_ms" ? fmtGeomean(val) : formatScoreMetric(primaryMetric, val);
  }

  function compareNullableNumber(a: number | null, b: number | null, direction: SortDirection): number {
    if (a === null && b === null) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    return direction === "asc" ? a - b : b - a;
  }

  function compareMatrixRows(a: PlatformRow, b: PlatformRow, current: SortState<MatrixSortKey>): number {
    if (current.key === "platform") {
      return current.direction === "asc"
        ? a.platform.localeCompare(b.platform)
        : b.platform.localeCompare(a.platform);
    }
    if (current.key === "primary") {
      if (isRankable(a) !== isRankable(b)) {
        return isRankable(a) ? -1 : 1;
      }
      return compareNullableNumber(getPrimaryValue(a), getPrimaryValue(b), current.direction);
    }
    if (current.key === "geomean") {
      return compareNullableNumber(
        validPrimaryMetricValue(a, "display_geomean_ms"),
        validPrimaryMetricValue(b, "display_geomean_ms"),
        current.direction,
      );
    }
    if (current.key.startsWith("query:")) {
      const queryId = current.key.slice("query:".length);
      return compareNullableNumber(platformTimingValue(a, queryId), platformTimingValue(b, queryId), current.direction);
    }
    return 0;
  }

  const sorted = useMemo(
    () => [...platforms].sort((a, b) => compareMatrixRows(a, b, activeSort)),
    [activeSort, platforms],
  );
  const identityCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of sorted) {
      const key = `${row.platform}\0${row.platform_version}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [sorted]);
  const rowIdentityLabels = useMemo(
    () => formatRunIdentitiesForCohort(sorted.map((row) => ({
      result_id: row.result_id,
      short_id: row.short_id,
      platform: row.platform,
      platform_version: row.platform_version,
      run_date: row.run_date,
      scale_factor: summary.scale_factor,
      trust_label: row.trust_label,
    })), "table"),
    [sorted, summary.scale_factor],
  );

  function syncPageStickyHeaderScroll() {
    if (!scrollContainerRef.current || !pageStickyHeaderRef.current) return;
    pageStickyHeaderRef.current.scrollLeft = scrollContainerRef.current.scrollLeft;
  }

  useEffect(() => {
    syncPageStickyHeaderScroll();
  }, [hasSelection, showGeomeanCol, sortedQueryIds.length]);

  function rowKey(row: PlatformRow): string {
    return row.short_id || row.result_id;
  }

  function toggleRow(row: PlatformRow) {
    if (!onSelectionChange || !selectedIds) return;
    if (!isComparable(row)) return;
    const key = rowKey(row);
    if (!selectedIds.has(key) && selectedIds.size >= MAX_COMPARE_SELECTIONS) return;
    const next = new Set(selectedIds);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onSelectionChange(next);
  }

  function toggleSort(key: MatrixSortKey, initialDirection: SortDirection = "asc") {
    setSort((prev) => {
      const current = prev ?? { key: "primary" as const, direction: defaultPrimaryDirection };
      if (current.key === key) {
        return { key, direction: current.direction === "asc" ? "desc" : "asc" };
      }
      return { key, direction: initialDirection };
    });
  }

  function ariaSort(key: MatrixSortKey): "ascending" | "descending" | "none" {
    if (activeSort.key !== key) return "none";
    return activeSort.direction === "asc" ? "ascending" : "descending";
  }

  function sortArrow(key: MatrixSortKey) {
    if (activeSort.key !== key) return " ↕";
    return activeSort.direction === "asc" ? " ↑" : " ↓";
  }

  function sortAnnouncement(key: MatrixSortKey) {
    if (activeSort.key !== key) return null;
    return (
      <span class="sr-only">
        {activeSort.direction === "asc" ? "sorted ascending" : "sorted descending"}
      </span>
    );
  }

  function handleCellKey(e: KeyboardEvent, rowIdx: number, colIdx: number) {
    let nextRow = rowIdx;
    let nextCol = colIdx;
    switch (e.key) {
      case "ArrowRight":
        nextCol = Math.min(colIdx + 1, sortedQueryIds.length - 1);
        break;
      case "ArrowLeft":
        nextCol = Math.max(colIdx - 1, 0);
        break;
      case "ArrowDown":
        nextRow = Math.min(rowIdx + 1, sorted.length - 1);
        break;
      case "ArrowUp":
        nextRow = Math.max(rowIdx - 1, 0);
        break;
      case "Home":
        nextCol = 0;
        break;
      case "End":
        nextCol = sortedQueryIds.length - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    if (nextRow === rowIdx && nextCol === colIdx) return;
    setFocusPos({ row: nextRow, col: nextCol });
    const target = gridRef.current?.querySelector(
      `[data-cell="${nextRow}-${nextCol}"]`,
    ) as HTMLElement | null;
    target?.focus();
  }

  if (platforms.length === 0) {
    return (
      <div class="rounded-lg border border-dashed border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] p-10 text-center text-[var(--bb-data-fg-subtle)]">
        No results are available for these settings.
      </div>
    );
  }

  const suppressHeat = sorted.length < 2;

  const primaryLabel = primaryMetric === "display_geomean_ms" ? "Geomean" : primaryMetricLabel(primaryMetric);
  const primaryDirectionLabel = primaryMetric === "display_geomean_ms" ? "lower is better" : "higher is better";
  const heatmapMeaning = suppressHeat
    ? "Heat color is unavailable because fewer than two platforms can be compared in this ranking."
    : "Heat color compares each query column with the fastest published timing; darker cells are slower.";

  function renderHeaderSortControl(
    label: string,
    key: MatrixSortKey,
    options: {
      className?: string;
      initialDirection?: SortDirection;
      pageSticky?: boolean;
      queryLabel?: string;
    } = {},
  ) {
    const className =
      options.className ?? "table-th-dense block w-full cursor-pointer select-none border-0 bg-transparent text-left";
    const content = (
      <>
        {label}{sortArrow(key)}
        {options.pageSticky ? null : sortAnnouncement(key)}
      </>
    );

    if (options.pageSticky) return <span class={className}>{content}</span>;

    return (
      <button
        type="button"
        class={className}
        data-query-label={options.queryLabel}
        onClick={() => toggleSort(key, options.initialDirection)}
      >
        {content}
      </button>
    );
  }

  function stickyHeaderStyle(target: StickyColKey): JSX.CSSProperties {
    return {
      ...stickyLeftStyle(cumulativeStickyLeft({ hasSelection, showGeomeanCol }, target)),
      ...stickyWidthStyle(STICKY_COL_REM[target]),
      ...(target !== "checkbox" && target !== "platform" ? { position: "static" as const } : {}),
    };
  }

  const stickyCellStyle = stickyHeaderStyle;

  function renderHeaderRow(pageSticky = false) {
    return (
      <tr role="row" class="bg-[var(--bb-surface-data-muted)]">
        {hasSelection && (
          <th
            role="columnheader"
            scope="col"
            aria-label="Select for comparison"
            class="table-th-dense sticky z-30 bg-[var(--bb-surface-data-muted)] px-2"
            style={stickyHeaderStyle("checkbox")}
          />
        )}
        <th
          role="columnheader"
          scope="col"
          class="sticky z-30 bg-[var(--bb-surface-data-muted)] p-0"
          style={stickyHeaderStyle("platform")}
          aria-sort={ariaSort("platform")}
        >
          {renderHeaderSortControl("Platform", "platform", { pageSticky })}
        </th>
        <th
          role="columnheader"
          scope="col"
          class="table-th-dense sticky z-30 whitespace-nowrap bg-[var(--bb-surface-data-muted)]"
          style={stickyHeaderStyle("run")}
        >
          Run
        </th>
        <th
          role="columnheader"
          scope="col"
          aria-sort={ariaSort("primary")}
          class="sticky z-30 whitespace-nowrap bg-[var(--bb-surface-data-muted)] p-0"
          style={stickyHeaderStyle("primary")}
          title={primaryLabel}
        >
          {renderHeaderSortControl(primaryLabel, "primary", {
            initialDirection: defaultPrimaryDirection,
            pageSticky,
          })}
        </th>
        {showGeomeanCol && (
          <th
            role="columnheader"
            scope="col"
            class="sticky z-30 whitespace-nowrap bg-[var(--bb-surface-data-muted)] p-0"
            style={stickyHeaderStyle("geomean")}
            aria-sort={ariaSort("geomean")}
            title="Geometric mean of per-query display times"
          >
            {renderHeaderSortControl("Geomean", "geomean", {
              className:
                "table-th-dense block w-full cursor-pointer select-none border-0 bg-transparent text-left text-[var(--bb-data-fg-subtle)]",
              pageSticky,
            })}
          </th>
        )}
        {showLabelsCol && (
          <th
            role="columnheader"
            scope="col"
            class="table-th-dense sticky z-30 whitespace-nowrap bg-[var(--bb-surface-data-muted)]"
            style={stickyHeaderStyle("labels")}
          >
            Labels
          </th>
        )}
        {sortedQueryIds.map((qid) => (
          <th
            key={qid}
            role="columnheader"
            scope="col"
            class="min-w-[7rem] whitespace-nowrap p-0 font-mono"
            aria-sort={ariaSort(`query:${qid}`)}
          >
            {renderHeaderSortControl(queryDisplayLabel(qid), `query:${qid}`, {
              className:
                "table-th-dense block w-full cursor-pointer select-none border-0 bg-transparent text-left font-mono",
              pageSticky,
              queryLabel: pageSticky ? undefined : qid,
            })}
          </th>
        ))}
      </tr>
    );
  }

  const hasUnrankableRow = sorted.some((row) => !isRankable(row));

  const renderLegend = (testId: string) => (
    <details
      class="bg-[var(--bb-surface-data-muted)] text-xs text-[var(--bb-data-fg-muted)]"
      data-testid={testId}
    >
      <summary class="cursor-pointer px-3 py-2 font-semibold text-[var(--bb-data-fg-primary)]">
        Lower is better. How to read this matrix
      </summary>
      <div class="space-y-1 px-3 pb-2">
        <p>
          {heatmapMeaning} <strong>&lt;1 ms</strong> means a positive sub-millisecond timing; exact zero timings are{" "}
          excluded from heat, ranks, and comparisons. <strong>No run</strong> means missing data. The{" "}
          {primaryLabel} column uses its own metric and direction ({primaryDirectionLabel}).
        </p>
        <p>
          Labels show each run’s trust and validation status. A failed or unverified validation excludes the run from ranking; its query timings remain visible.
        </p>
        {hasUnrankableRow && (
          <p data-testid="ranking-eligibility-legend">
            <span aria-hidden="true" class="font-semibold">*</span> after a platform name means the run is not
            eligible for ranking; hover or focus the marker for the reason.
          </p>
        )}
      </div>
    </details>
  );

  return (
    <div class={`relative ${highContrast ? "heatmap-reduced-color" : ""}`}>
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        class="sr-only"
      >
        {announcement}
      </div>

      <div class="mb-3 overflow-hidden rounded-lg border border-[var(--bb-data-border)] md:hidden">
        {renderLegend("query-heatmap-legend-mobile")}
      </div>

      <div
        class="space-y-3 md:hidden"
        data-testid="query-heatmap-mobile-cards"
        role="list"
        aria-label={`${summary.benchmark} compact query result cards`}
      >
        {sorted.map((row, rowIdx) => {
          const rowIdentity = rowIdentityLabels[rowIdx] ?? row.platform;
          const isSelected = selectedIds?.has(rowKey(row)) ?? false;
          const comparable = isComparable(row);
          const capDisabled = selectionAtCap && !isSelected;
          const selectionDisabled = !comparable || capDisabled;
          const comparisonExclusion = comparable ? null : formatTimingExclusion(row.comparison_exclusion_reason);
          const comparisonCopy = describeCompareExclusionReason(row.comparison_exclusion_reason);
          const comparisonReasonId = comparisonCopy
            ? `query-heatmap-mobile-compare-reason-${row.result_id}`
            : capDisabled
              ? selectionLimitReasonId
              : undefined;
          const rankingExclusion = isRankable(row) ? null : formatTimingExclusion(row.ranking_exclusion_reason);
          const outliers = queryOutliers(row, sortedQueryIds, colMins);
          return (
            <article
              key={row.result_id}
              data-testid={`query-heatmap-mobile-card-${row.result_id}`}
              role="listitem"
              class={`rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] p-3 shadow-sm ${
                isSelected ? "border-[var(--bb-accent-hover)] bg-[var(--bb-tone-info-bg)]" : ""
              }`}
            >
              <div class="flex items-start gap-3">
                {hasSelection && (
                  <input
                    type="checkbox"
                    checked={isSelected}
                    disabled={selectionDisabled}
                    onChange={() => toggleRow(row)}
                    aria-label={compareSelectionLabel({
                      platform: row.platform,
                      benchmark: summary.benchmark,
                      scaleFactor: summary.scale_factor,
                      phase: summary.phase,
                      runDate: row.run_date,
                      resultId: row.result_id,
                    })}
                    aria-describedby={comparisonReasonId}
                    title={comparisonExclusion ?? undefined}
                    class="mt-1 h-4 w-4 shrink-0 rounded border-[var(--bb-data-border-strong)]"
                  />
                )}
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-baseline gap-1.5">
                    <h2 class="text-sm font-semibold text-[var(--bb-data-fg-primary)]">
                      <a
                        href={resultReceiptHref(row)}
                        aria-label={resultIdentityAriaLabel(row, "receipt")}
                        title={rowIdentity}
                        class="text-[var(--bb-data-fg-primary)] no-underline hover:text-[var(--bb-accent-hover)] hover:underline"
                      >
                        {row.platform}
                      </a>
                    </h2>
                    {row.platform_version && (
                      <VersionLabel version={row.platform_version} plain class="shrink-0 text-[var(--bb-data-fg-subtle)]" />
                    )}
                    <RunDateChip runDate={row.run_date} />
                    {rankingExclusion && (
                      <span
                        role="img"
                        tabIndex={0}
                        class="text-xs text-[var(--bb-data-fg-subtle)] cursor-help"
                        title={rankingExclusion}
                        aria-label={rankingExclusion}
                        data-testid={`heatmap-mobile-compliance-marker-${row.result_id}`}
                      >
                        *
                      </span>
                    )}
                  </div>
                  {(identityCounts.get(`${row.platform}\0${row.platform_version}`) ?? 0) > 1 && (
                    <span class="block font-mono text-xs text-[var(--bb-data-fg-muted)]" data-testid="visible-run-qualifier-mobile">{row.short_id || row.result_id}</span>
                  )}
                  {hasSelection && comparisonCopy && (
                    <CompareDisabledReason id={comparisonReasonId} copy={comparisonCopy} />
                  )}
                </div>
                <dl class="shrink-0 text-right">
                  <dt class="text-[0.65rem] font-semibold uppercase text-[var(--bb-data-fg-subtle)]">{primaryLabel}</dt>
                  <dd class="font-mono text-sm font-semibold text-[var(--bb-data-fg-primary)]">
                    {fmtPrimary(getPrimaryValue(row))}
                  </dd>
                </dl>
              </div>

              <div class="mt-3 flex flex-wrap gap-1.5">
                {showLabelsCol && (
                  <>
                    <TrustBadge trustLabel={row.trust_label} compact />
                    <FundingChip funding={row.funding} compact />
                    <ValidationBadge
                      validationStatus={row.validation_status}
                      overrideRules={parseOverrideRules(row.override_rules)}
                      showMissing
                    />
                  </>
                )}
                {showGeomeanCol && (
                  <span class="rounded-full bg-[var(--bb-surface-app)] px-2 py-0.5 font-mono text-xs text-[var(--bb-data-fg-muted)]">
                    Geomean {fmtGeomean(validPrimaryMetricValue(row, "display_geomean_ms"))}
                  </span>
                )}
              </div>

              <div class="mt-3 border-t border-[var(--bb-data-border)] pt-3">
                <div class="text-[0.65rem] font-semibold uppercase text-[var(--bb-data-fg-subtle)]">Query outliers</div>
                {outliers.length > 0 ? (
                  <div class="mt-2 grid gap-2" role="list" aria-label={`${row.platform} query outliers`}>
                    {outliers.map((outlier) => (
                      <div
                        key={outlier.queryId}
                        role="listitem"
                        class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md bg-[var(--bb-surface-data-muted)] px-2 py-1.5"
                      >
                        <div class="min-w-0">
                          <div
                            class="truncate font-mono text-xs font-semibold text-[var(--bb-data-fg-primary)]"
                            title={queryDisplayLabel(outlier.queryId)}
                          >
                            {queryDisplayLabel(outlier.queryId)}
                          </div>
                          <div class="text-xs text-[var(--bb-data-fg-muted)]">{outlier.ratioLabel}</div>
                        </div>
                        <div class="font-mono text-xs font-semibold text-[var(--bb-data-fg-primary)]">
                          {formatCompactLatencyMs(outlier.ms)}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p class="mt-2 rounded-md bg-[var(--bb-surface-data-muted)] px-2 py-1.5 text-xs text-[var(--bb-data-fg-muted)]">
                    No query timings published.
                  </p>
                )}
              </div>
            </article>
          );
        })}
      </div>

      <div class="hidden md:block">
        <div class="mb-2 flex items-center justify-between gap-3 text-xs text-[var(--bb-data-fg-muted)]">
          <TableScrollHint
            scrollerRef={scrollContainerRef}
            testId="query-heatmap-scroll-hint"
            label="Scroll for query columns →"
            wrapperClassName={null}
            className="italic"
          />
          <span>{sortedQueryIds.length.toLocaleString()} queries</span>
        </div>
        <div class="overflow-hidden rounded-t-lg border border-b-0 border-[var(--bb-data-border)]">
          {renderLegend("query-heatmap-legend")}
        </div>
        <div
          aria-hidden="true"
          class="pointer-events-none sticky top-0 z-40 h-0 overflow-visible"
          data-testid="query-heatmap-page-sticky-header-shell"
        >
          <div
            ref={pageStickyHeaderRef}
            class="overflow-x-hidden rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] shadow-sm"
            data-testid="query-heatmap-page-sticky-header"
          >
            <table role="presentation" class="min-w-max text-sm">
              <thead class="bg-[var(--bb-surface-data-muted)]">{renderHeaderRow(true)}</thead>
            </table>
          </div>
        </div>
        <div
          ref={scrollContainerRef}
          class="overflow-x-auto rounded-b-lg border border-t-0 border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] shadow-sm"
          data-testid="query-heatmap-scroll-container"
          onScroll={syncPageStickyHeaderScroll}
        >
          <table
            ref={gridRef}
            role="grid"
            aria-label={`${summary.benchmark} ${formatScaleFactor(summary.scale_factor)} ${summary.phase} results`}
            class="min-w-max text-sm"
          >
            <thead class="bg-[var(--bb-surface-data-muted)]">{renderHeaderRow()}</thead>
            <tbody class="divide-y divide-[var(--bb-data-border)]">
              {sorted.map((row, rowIdx) => {
                const rowIdentity = rowIdentityLabels[rowIdx] ?? row.platform;
                const isSelected = selectedIds?.has(rowKey(row)) ?? false;
                const comparable = isComparable(row);
                const capDisabled = selectionAtCap && !isSelected;
                const selectionDisabled = !comparable || capDisabled;
                const comparisonExclusion = comparable ? null : formatTimingExclusion(row.comparison_exclusion_reason);
                const comparisonCopy = describeCompareExclusionReason(row.comparison_exclusion_reason);
                const comparisonReasonId = comparisonCopy
                  ? `query-heatmap-compare-reason-${row.result_id}`
                  : capDisabled
                    ? selectionLimitReasonId
                    : undefined;
                const rankingExclusion = isRankable(row) ? null : formatTimingExclusion(row.ranking_exclusion_reason);
                return (
                  <tr
                    key={row.result_id}
                    role="row"
                    data-testid={row.result_id}
                    data-density-contract="benchmark-matrix-row"
                    class={`hover:bg-[var(--bb-surface-data-muted)] ${isSelected ? "bg-[var(--bb-tone-info-bg)]" : ""}`}
                  >
                  {hasSelection && (
                    <td
                      role="gridcell"
                      class={`table-td-dense sticky z-10 px-2 ${
                        isSelected ? "bg-[var(--bb-tone-info-bg)]" : "bg-[var(--bb-surface-data)]"
                      }`}
                      style={stickyCellStyle("checkbox")}
                    >
                      <input
                        type="checkbox"
                        checked={isSelected}
                        disabled={selectionDisabled}
                        onChange={() => toggleRow(row)}
                        aria-label={compareSelectionLabel({
                          platform: row.platform,
                          benchmark: summary.benchmark,
                          scaleFactor: summary.scale_factor,
                          phase: summary.phase,
                          runDate: row.run_date,
                          resultId: row.result_id,
                        })}
                        aria-describedby={comparisonReasonId}
                        title={comparisonExclusion ?? undefined}
                        class="h-4 w-4 rounded border-[var(--bb-data-border-strong)]"
                      />
                    </td>
                  )}
                  <td
                    role="gridcell"
                    class={`table-td-dense sticky z-10 ${
                      isSelected ? "bg-[var(--bb-tone-info-bg)]" : "bg-[var(--bb-surface-data)]"
                    }`}
                    style={stickyCellStyle("platform")}
                  >
                    <div class="flex items-baseline gap-1.5">
                      <a
                        href={resultReceiptHref(row)}
                        aria-label={resultIdentityAriaLabel(row, "receipt")}
                        title={rowIdentity}
                        class="truncate font-medium text-[var(--bb-data-fg-primary)] no-underline hover:text-[var(--bb-accent-hover)] hover:underline"
                      >
                        {row.platform}
                      </a>
                      {row.platform_version && (
                        <VersionLabel version={row.platform_version} plain class="shrink-0 text-[var(--bb-data-fg-subtle)]" />
                      )}
                      {rankingExclusion && (
                        <span
                          role="img"
                          tabIndex={0}
                          class="shrink-0 text-xs text-[var(--bb-data-fg-subtle)] cursor-help"
                          title={rankingExclusion}
                          aria-label={rankingExclusion}
                          data-testid={`heatmap-compliance-marker-${row.result_id}`}
                        >
                          *
                        </span>
                      )}
                    </div>
                    {(identityCounts.get(`${row.platform}\0${row.platform_version}`) ?? 0) > 1 && (
                      <span class="block font-mono text-xs text-[var(--bb-data-fg-muted)]" data-testid="visible-run-qualifier">{row.short_id || row.result_id}</span>
                    )}
                    {hasSelection && comparisonCopy && (
                      <CompareDisabledReason id={comparisonReasonId} copy={comparisonCopy} />
                    )}
                  </td>
                  <td
                    role="gridcell"
                    class={`table-td-dense sticky z-10 whitespace-nowrap ${
                      isSelected ? "bg-[var(--bb-tone-info-bg)]" : "bg-[var(--bb-surface-data)]"
                    }`}
                    style={stickyCellStyle("run")}
                  >
                    <RunDateChip runDate={row.run_date} />
                  </td>
                  <td
                    role="gridcell"
                    class={`table-td-dense sticky z-10 whitespace-nowrap font-mono ${
                      isSelected ? "bg-[var(--bb-tone-info-bg)]" : "bg-[var(--bb-surface-data)]"
                    }`}
                    style={stickyCellStyle("primary")}
                  >
                    {fmtPrimary(getPrimaryValue(row))}
                  </td>
                  {showGeomeanCol && (
                    <td
                      role="gridcell"
                      class={`table-td-dense sticky z-10 whitespace-nowrap font-mono text-[var(--bb-data-fg-muted)] ${
                        isSelected ? "bg-[var(--bb-tone-info-bg)]" : "bg-[var(--bb-surface-data)]"
                      }`}
                      style={stickyCellStyle("geomean")}
                    >
                      {fmtGeomean(validPrimaryMetricValue(row, "display_geomean_ms"))}
                    </td>
                  )}
                  {showLabelsCol && (
                    <td
                      role="gridcell"
                      class={`table-td-dense sticky z-10 ${
                        isSelected ? "bg-[var(--bb-tone-info-bg)]" : "bg-[var(--bb-surface-data)]"
                      }`}
                      style={stickyCellStyle("labels")}
                      data-testid={`heatmap-labels-${row.result_id}`}
                    >
                      <div class="flex flex-nowrap items-center gap-1">
                        <TrustBadge trustLabel={row.trust_label} compact />
                        <FundingChip funding={row.funding} compact />
                        <ValidationBadge
                          validationStatus={row.validation_status}
                          overrideRules={parseOverrideRules(row.override_rules)}
                          showMissing
                        />
                      </div>
                    </td>
                  )}
                  {sortedQueryIds.map((qid, colIdx) => {
                    const rawMs = row.timings[qid] ?? null;
                    const timingExclusionReason = row.timing_eligibility[qid]?.timing_exclusion_reason ?? null;
                    const ms = platformTimingValue(row, qid);
                    const minInCol = colMins[qid] ?? null;
                    const hue = suppressHeat ? null : colorForCell(ms, minInCol);
                    const lightness = suppressHeat ? null : lightnessForCell(ms, minInCol);
                    const ratio =
                      ms !== null && minInCol !== null && minInCol > 0 ? ms / minInCol : null;
                    const isExcludedTiming = rawMs !== null && ms === null;
                    const excludedReason =
                      timingExclusionReason !== null
                        ? formatTimingExclusion(timingExclusionReason)
                        : rawMs === 0
                          ? formatTimingExclusion("zero_timing")
                          : "Timing is excluded from display evidence.";
                    const ariaLabel =
                      ms !== null
                        ? ratio !== null
                          ? ratio <= 1.005
                            ? `${formatCompactLatencyMs(ms)}, fastest in column`
                            :
                              `${formatCompactLatencyMs(ms)}, ${formatSpeedup(ratio).valueText} slower than fastest in column`
                          : formatCompactLatencyMs(ms)
                        : isExcludedTiming
                          ? `${formatDurationMs(rawMs)}, excluded: ${excludedReason}`
                          : `No published query run for ${row.platform} ${qid}`;

                    const isFocused = focusPos.row === rowIdx && focusPos.col === colIdx;

                    const cellStyle: JSX.CSSProperties | undefined =
                      hue !== null
                        ? ({
                            "--cell-hue": String(hue),
                            "--cell-lightness": lightness ?? "95%",
                          } as JSX.CSSProperties)
                        : undefined;

                    return (
                      <td
                        key={qid}
                        role="gridcell"
                        data-cell={`${rowIdx}-${colIdx}`}
                        tabIndex={isFocused ? 0 : -1}
                        class={`table-td-dense min-w-[6rem] whitespace-nowrap text-right font-mono ${
                          hue !== null ? "heatmap-cell" : ms === null ? "bg-[var(--bb-surface-data-muted)] text-[var(--bb-data-fg-subtle)]" : ""
                        }`}
                        style={cellStyle}
                        aria-label={ariaLabel}
                        title={
                          isExcludedTiming
                            ? excludedReason
                            : ms === null
                              ? "No published run for this query/platform cell."
                              : undefined
                        }
                        onKeyDown={(e) => handleCellKey(e as KeyboardEvent, rowIdx, colIdx)}
                        onFocus={() => setAnnouncement(`${queryDisplayLabel(qid)}: ${ariaLabel}`)}
                      >
                        {ms !== null ? formatCompactLatencyMs(ms) : isExcludedTiming ? "Excluded" : "No run"}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

interface QueryOutlier {
  queryId: string;
  ms: number;
  ratio: number;
  ratioLabel: string;
}

function queryOutliers(
  row: PlatformRow,
  sortedQueryIds: string[],
  colMins: Record<string, number | null>,
): QueryOutlier[] {
  return sortedQueryIds
    .map((queryId) => {
      const ms = platformTimingValue(row, queryId);
      if (ms === null) return null;
      const minInCol = colMins[queryId] ?? null;
      const ratio = minInCol !== null && minInCol > 0 ? ms / minInCol : null;
      return {
        queryId,
        ms,
        ratio: ratio ?? 0,
        ratioLabel: queryRatioLabel(ratio),
      };
    })
    .filter((item): item is QueryOutlier => item !== null)
    .sort((a, b) => b.ratio - a.ratio || b.ms - a.ms || a.queryId.localeCompare(b.queryId))
    .slice(0, MOBILE_OUTLIER_LIMIT);
}

function queryRatioLabel(ratio: number | null): string {
  if (ratio === null) return "No ranking baseline";
  if (ratio <= 1.005) return "Fastest in ranking";
  return `${formatSpeedup(ratio).valueText} slower than fastest`;
}
