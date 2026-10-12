import { RunDateChip } from "@/components/RunAge";
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { route } from "preact-router";
import type {
  MetaCohort,
  MetaCohortPlatform,
  MetaLeaderboard as MetaLeaderboardData,
  MetaPlatform,
  MetaRank,
} from "@/types";
import { colorForCell, lightnessForCell } from "@/lib/chartMath";
import { formatTimingExclusion } from "@/lib/displayEligibility";
import { formatAverageRank, formatCoverage, formatRank, formatSpeedup } from "@/lib/metricFormatters";
import { fmtGeomean, fmtScoreCompact, fmtScoreExact } from "@/utils";
import { TrustBadge, ValidationBadge } from "@/components/TrustBadge";
import { FundingChip } from "@/components/FundingChip";
import { formatPhaseWithStreams, parseOverrideRules } from "@/lib/displayLabels";
import { SegmentedControl } from "@/components/SegmentedControl";
import { TableScrollHint } from "@/components/TableScrollHint";
import { formatRunAge } from "@/lib/runAge";
import {
  EXPLORER_PERFORMANCE_MARKS,
  EXPLORER_PERFORMANCE_MEASURES,
  markExplorerPerformance,
  measureExplorerPerformance,
} from "@/lib/performanceMarks";

export type MetaLeaderboardMode = "times" | "ranks" | "speedup";
type MetaLeaderboardSort = "avg_rank" | "coverage" | "best_rank" | "recent_activity";

interface MetaResultMetadata {
  trust_label: string;
  funding?: string | null;
  validation_status?: string | null;
  override_rules?: string | null;
  run_date?: string | null;
}

interface MetaLeaderboardProps {
  data: MetaLeaderboardData;
  mode: MetaLeaderboardMode;
  onModeChange: (mode: MetaLeaderboardMode) => void;
  cohortHref?: (cohort: MetaCohort) => string;
  platformHref?: (platformId: string) => string;
  resultMetadataById?: ReadonlyMap<string, MetaResultMetadata>;
}

type MetaLeaderboardCellState =
  | { kind: "missing" }
  | { kind: "unranked"; result: MetaCohortPlatform; label: "Excluded" | "Unranked"; reason: string }
  | { kind: "ranked"; rank: MetaRank; result?: MetaCohortPlatform };

const MODE_LABELS: Record<MetaLeaderboardMode, string> = {
  times: "Times",
  ranks: "Ranks",
  speedup: "Relative to best",
};
const AVG_RANK_LABEL = "Avg rank over covered rankings";
const COVERAGE_POLICY_COPY = "Missing rankings are not scored; coverage is shown separately.";
const SORT_LABELS: Record<MetaLeaderboardSort, string> = {
  avg_rank: "Avg rank",
  coverage: "Coverage",
  best_rank: "Best ranking",
  recent_activity: "Recent",
};
const SORT_TITLES: Record<MetaLeaderboardSort, string> = {
  avg_rank: AVG_RANK_LABEL,
  coverage: "Sort by number of covered rankings; missing rankings are not scored.",
  best_rank: "Sort by each platform's best rank in any visible ranking.",
  recent_activity: "Sort by the most recent visible run date.",
};
const MISSING_COHORT_TITLE = `No published run for this ranking. ${COVERAGE_POLICY_COPY}`;
const PLATFORM_RENDER_LIMIT = 25;
const PLATFORM_RENDER_INCREMENT = 200;

export function MetaLeaderboard({
  data,
  mode,
  onModeChange,
  cohortHref = (cohort) => cohort.href,
  platformHref = (platformId) => `/results/p/${platformId}/`,
  resultMetadataById,
}: MetaLeaderboardProps) {
  const { cohorts = [], platforms = [] } = data ?? {};
  const gridRef = useRef<HTMLTableElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [focusPos, setFocusPos] = useState({ row: 0, col: 0 });
  const [announcement, setAnnouncement] = useState("");
  const [sortKey, setSortKey] = useState<MetaLeaderboardSort>("avg_rank");
  const [visiblePlatformLimit, setVisiblePlatformLimit] = useState(PLATFORM_RENDER_LIMIT);
  const [showUnrankedPlatforms, setShowUnrankedPlatforms] = useState(false);

  const columnMins = useMemo(() => {
    const mins = new Map<string, number | null>();
    for (const cohort of cohorts) {
      const values = platforms
        .map((platform) => {
          const result = findCohortPlatform(cohort, variantKeyOf(platform));
          const state = metaLeaderboardCellState(platform.ranks[cohort.key], result, cohort);
          return state.kind === "ranked" ? cellShadeMetric(state.rank, mode, cohort) : null;
        })
        .filter((value): value is number => value !== null);
      mins.set(cohort.key, values.length > 0 ? Math.min(...values) : null);
    }
    return mins;
  }, [cohorts, mode, platforms]);

  const sortedPlatforms = useMemo(
    () =>
      [...platforms].sort((a, b) =>
        comparePlatforms(a, b, sortKey, cohorts, resultMetadataById),
      ),
    [cohorts, platforms, resultMetadataById, sortKey],
  );
  const collapsedPlatforms = useMemo(
    () =>
      sortedPlatforms.filter(
        (platform) => hasPublishedEvidence(platform, cohorts) && !hasRankedEvidence(platform, cohorts),
      ),
    [cohorts, sortedPlatforms],
  );
  const collapsedPlatformIds = useMemo(
    () => new Set(collapsedPlatforms.map((platform) => variantKeyOf(platform))),
    [collapsedPlatforms],
  );
  const defaultPlatforms = useMemo(
    () => sortedPlatforms.filter((platform) => !collapsedPlatformIds.has(variantKeyOf(platform))),
    [collapsedPlatformIds, sortedPlatforms],
  );
  const renderedPlatforms = showUnrankedPlatforms
    ? [...defaultPlatforms, ...collapsedPlatforms]
    : defaultPlatforms;
  const visiblePlatforms = renderedPlatforms.slice(0, visiblePlatformLimit);
  const allExcludedRankingReasons = useMemo(
    () =>
      new Map(
        cohorts.map((cohort) => [cohort.key, allExcludedRankingReason(cohort, platforms)]),
      ),
    [cohorts, platforms],
  );

  useEffect(() => {
    setVisiblePlatformLimit(PLATFORM_RENDER_LIMIT);
    setFocusPos({ row: 0, col: 0 });
  }, [cohorts, platforms, showUnrankedPlatforms, sortKey]);

  useEffect(() => {
    if (platforms.length === 0 || cohorts.length === 0) return;
    markExplorerPerformance(EXPLORER_PERFORMANCE_MARKS.LEADERBOARD_RENDERED, {
      once: true,
      detail: { cohortCount: cohorts.length, platformCount: platforms.length },
    });
    measureExplorerPerformance(
      EXPLORER_PERFORMANCE_MEASURES.LEADERBOARD_RENDER_AFTER_DATA,
      EXPLORER_PERFORMANCE_MARKS.HOME_LEADERBOARD_DATA_READY,
      EXPLORER_PERFORMANCE_MARKS.LEADERBOARD_RENDERED,
      { once: true },
    );
  }, [cohorts.length, platforms.length]);

  if (platforms.length === 0 || cohorts.length === 0) return null;

  function handleCellKey(event: KeyboardEvent, rowIdx: number, colIdx: number) {
    if (event.target !== event.currentTarget) return;
    let nextRow = rowIdx;
    let nextCol = colIdx;
    switch (event.key) {
      case "ArrowRight":
        nextCol = Math.min(colIdx + 1, cohorts.length - 1);
        break;
      case "ArrowLeft":
        nextCol = Math.max(colIdx - 1, 0);
        break;
      case "ArrowDown":
        nextRow = Math.min(rowIdx + 1, visiblePlatforms.length - 1);
        break;
      case "ArrowUp":
        nextRow = Math.max(rowIdx - 1, 0);
        break;
      case "Home":
        nextCol = 0;
        break;
      case "End":
        nextCol = cohorts.length - 1;
        break;
      case "PageUp":
        nextRow = 0;
        break;
      case "PageDown":
        nextRow = visiblePlatforms.length - 1;
        break;
      case "Enter":
      case " ": {
        event.preventDefault();
        const platform = visiblePlatforms[rowIdx];
        if (platform) route(platformHref(variantKeyOf(platform)));
        return;
      }
      default:
        return;
    }

    event.preventDefault();
    if (nextRow === rowIdx && nextCol === colIdx) return;
    setFocusPos({ row: nextRow, col: nextCol });
    const target = gridRef.current?.querySelector(
      `[data-cell="${nextRow}-${nextCol}"]`,
    ) as HTMLElement | null;
    target?.focus();
  }

  return (
    <section class="mb-12" aria-labelledby="meta-leaderboard-title">
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        class="sr-only"
      >
        {announcement}
      </div>

      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="meta-leaderboard-title" class="text-xl font-semibold text-[var(--bb-data-fg-primary)]">
            Cross-benchmark rankings
          </h2>
          <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">
            Compare measured values, ranks, or each result relative to the best result in its ranking.
          </p>
        </div>
        <div
          role="group"
          class="flex min-w-0 max-w-full flex-wrap items-center gap-3 rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-3 py-2"
          aria-label="Leaderboard display controls"
        >
          <div class="flex min-w-0 max-w-full flex-wrap items-center gap-2">
            <span class="text-[11px] font-semibold uppercase tracking-wide text-[var(--bb-data-fg-subtle)]">Sort</span>
            <SegmentedControl
              class="max-w-full flex-wrap"
              ariaLabel="Sort leaderboard"
              value={sortKey}
              onChange={setSortKey}
              size="sm"
              options={(Object.keys(SORT_LABELS) as MetaLeaderboardSort[]).map((value) => ({
                value,
                label: SORT_LABELS[value],
                title: SORT_TITLES[value],
              }))}
            />
          </div>
          <div class="flex min-w-0 max-w-full flex-wrap items-center gap-2">
            <span class="text-[11px] font-semibold uppercase tracking-wide text-[var(--bb-data-fg-subtle)]">Mode</span>
            <SegmentedControl
              class="max-w-full flex-wrap"
              ariaLabel="Display mode"
              value={mode}
              onChange={onModeChange}
              size="sm"
              options={[
                {
                  value: "times",
                  label: "Times",
                  title: "Measured values for each ranking. Lower latency is better; a higher power score is better.",
                },
                { value: "ranks", label: "Ranks", title: "Rank within each ranking (1 is best)" },
                { value: "speedup", label: "Relative to best", title: "1.00× is the best result in each ranking. Lower values are worse." },
              ]}
            />
          </div>
        </div>
      </div>

      <div class="mb-2 flex flex-wrap items-baseline justify-between gap-3">
        <p class="text-sm text-[var(--bb-data-fg-muted)]">
          Showing {visiblePlatforms.length.toLocaleString()} of {renderedPlatforms.length.toLocaleString()} {platformScopeLabel(
            collapsedPlatforms.length,
            showUnrankedPlatforms,
          )} across{" "}
          {cohorts.length.toLocaleString()} leaderboard {cohorts.length === 1 ? "ranking" : "rankings"}
        </p>
        <p id="meta-leaderboard-legend" class="text-xs text-[var(--bb-data-fg-subtle)]">
          {mode === "times" && "Heat: darker = worse within each ranking. "}
          {mode === "ranks" && "Heat: darker = a worse rank within each ranking. "}
          {mode === "speedup" &&
            "Heat: darker = farther from the best result (1.00×). Values below 1.00× are worse. "}
          <span class="italic">No run</span> means no result is published. <span class="font-medium">Excluded</span> or{" "}
          <span class="font-medium">Unranked</span> means the result is published but not scored.
        </p>
      </div>

      <div
        class="overflow-hidden rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] shadow-sm"
        style="contain: paint"
      >
        <TableScrollHint
          scrollerRef={scrollContainerRef}
          testId="meta-leaderboard-scroll-hint"
          label="Scroll table for more rankings →"
          wrapperClassName="flex justify-end"
          className="m-2"
        />
        <div ref={scrollContainerRef} class="overflow-x-auto" data-testid="meta-leaderboard-scroll-container">
          <table
            ref={gridRef}
            role="grid"
            aria-label="Cross-benchmark leaderboard"
            aria-describedby="meta-leaderboard-legend"
            class="min-w-full text-sm"
          >
            <thead class="bg-[var(--bb-surface-data-muted)]">
              <tr role="row">
                <th scope="col" class="table-th sticky left-0 z-10 min-w-40 bg-[var(--bb-surface-data-muted)] py-2">
                  Platform
                </th>
                {cohorts.map((cohort) => {
                  const allExcludedReason = allExcludedRankingReasons.get(cohort.key) ?? null;
                  return (
                    <th
                      key={cohort.key}
                      scope="col"
                      class="table-th min-w-40 whitespace-normal py-2"
                    >
                      <a
                        href={cohortHref(cohort)}
                        title={
                          allExcludedReason
                            ? `No ranked evidence. ${allExcludedReason} Open the ranking for published evidence.`
                            : `${cohort.platform_count} ranked platforms in this ranking`
                        }
                        class="flex flex-col gap-0.5 no-underline text-[var(--bb-data-fg-muted)] hover:text-[var(--bb-accent-hover)]"
                      >
                        <span class="font-semibold text-[var(--bb-data-fg-primary)]">{cohort.label}</span>
                        <span class="text-[10px] font-normal normal-case tracking-normal text-[var(--bb-data-fg-subtle)]">
                          {cohortMetricSublabel(cohort)}
                        </span>
                        {allExcludedReason && (
                          <span
                            class="mt-1 max-w-56 text-[10px] font-normal normal-case leading-snug tracking-normal text-[var(--bb-data-fg-muted)]"
                            data-testid={`all-excluded-ranking-${cohort.key}`}
                          >
                            No ranked evidence. {allExcludedReason} Open ranking for details.
                          </span>
                        )}
                      </a>
                    </th>
                  );
                })}
                <th
                  scope="col"
                  class="table-th whitespace-nowrap py-2 text-[var(--bb-data-fg-primary)]"
                  title={`Average rank over rankings where the platform has a published run. ${COVERAGE_POLICY_COPY}`}
                >
                  {AVG_RANK_LABEL}
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-[var(--bb-data-border)]">
              {visiblePlatforms.map((platform, rowIdx) => (
                <tr
                  key={variantKeyOf(platform)}
                  class="cursor-pointer hover:bg-[var(--bb-surface-data-muted)]"
                  onClick={() => route(platformHref(variantKeyOf(platform)))}
                >
                  <td class="table-td sticky left-0 z-10 bg-[var(--bb-surface-data)] py-2 font-medium text-[var(--bb-data-fg-primary)]">
                    <div class="flex flex-wrap items-center gap-2">
                      <a
                        href={platformHref(variantKeyOf(platform))}
                        class="no-underline hover:text-[var(--bb-accent-hover)]"
                        onClick={(event) => event.stopPropagation()}
                      >
                        {variantLabelOf(platform)}
                      </a>
                      <span
                        class="rounded border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--bb-data-fg-muted)]"
                        title={`Covered rankings in the current leaderboard view. ${COVERAGE_POLICY_COPY}`}
                      >
                        {formatCoverage(platform.n_cohorts, cohorts.length, { unitLabel: "rankings" }).valueText}
                      </span>
                    </div>
                  </td>
                  {cohorts.map((cohort, colIdx) => {
                    const rank = platform.ranks[cohort.key];
                    const result = findCohortPlatform(cohort, variantKeyOf(platform));
                    const cellState = metaLeaderboardCellState(rank, result, cohort);
                    const missing = cellState.kind === "missing";
                    const unranked = cellState.kind === "unranked";
                    const shadeMetric = cellState.kind === "ranked" ? cellShadeMetric(cellState.rank, mode, cohort) : null;
                    const minInCol = columnMins.get(cohort.key) ?? null;
                    const hue = shadeMetric !== null ? colorForCell(shadeMetric, minInCol) : null;
                    const lightness = shadeMetric !== null ? lightnessForCell(shadeMetric, minInCol) : null;
                    const active = focusPos.row === rowIdx && focusPos.col === colIdx;
                    const receiptState = cellState.kind !== "missing" ? cellState : null;
                    const metadata = receiptState?.result
                      ? resultMetadataById?.get(receiptState.result.result_id)
                      : undefined;
                    const runAge = formatRunAge(metadata?.run_date);
                    const runAgeLabel = runAge ?? (metadata ? "not recorded" : null);
                    const text = describeCell(platform, cohort, cellState, mode, runAgeLabel);
                    const title = cellTitle(platform, cohort, cellState, mode, runAgeLabel);
                    const receiptHref = receiptState?.result
                      ? `/results/r/${receiptState.result.result_id}#run-receipt`
                      : null;
                    const cellStyle: JSX.CSSProperties | undefined =
                      hue !== null
                        ? ({
                            "--cell-hue": String(hue),
                            "--cell-lightness": lightness ?? "95%",
                          } as JSX.CSSProperties)
                        : undefined;
                    return (
                      <td
                        key={cohort.key}
                        role="gridcell"
                        aria-colindex={colIdx + 1}
                        aria-rowindex={rowIdx + 1}
                        aria-label={text}
                        title={title}
                        class={`table-td py-2 text-center font-mono focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--bb-focus-ring)] ${
                          hue !== null ? "meta-heatmap-cell" : ""
                        } ${
                          missing ? "bg-[var(--bb-surface-data-muted)] text-[var(--bb-data-fg-subtle)] italic" : ""
                        } ${
                          unranked ? "bg-[var(--bb-surface-data-muted)] text-[var(--bb-data-fg-muted)]" : ""
                        }`}
                        style={cellStyle}
                        tabIndex={active ? 0 : -1}
                        data-cell={`${rowIdx}-${colIdx}`}
                        onFocus={() => {
                          setFocusPos({ row: rowIdx, col: colIdx });
                          setAnnouncement(text);
                        }}
                        onKeyDown={(event) => handleCellKey(event, rowIdx, colIdx)}
                      >
                        {receiptHref && receiptState ? (
                          <a
                            href={receiptHref}
                            aria-label={
                              mode === "speedup" && cellState.kind === "ranked"
                                ? `${cellText(cellState.rank, cohort, mode)}; ${nativeMetricText(cellState.rank, cohort)}`
                                : undefined
                            }
                            class="font-mono no-underline hover:text-[var(--bb-accent-hover)]"
                            onClick={(event) => event.stopPropagation()}
                            title={receiptLinkTitle(receiptState, cohort, runAgeLabel)}
                          >
                            {renderCellValue(cellState, cohort, mode)}
                          </a>
                        ) : (
                          renderCellValue(cellState, cohort, mode)
                        )}
                        {metadata && cellState.kind !== "missing" && (
                          <div class="mt-0.5 text-[10px] font-normal text-[var(--bb-data-fg-muted)]">
                            <RunDateChip runDate={metadata.run_date} />
                          </div>
                        )}
                        {metadata && cellState.kind !== "missing" && (
                          <div class="mt-0.5 flex flex-wrap justify-center gap-1">
                            {cellState.kind === "ranked" && (
                              <>
                                <TrustBadge trustLabel={metadata.trust_label} compact />
                                <FundingChip funding={metadata.funding} compact />
                              </>
                            )}
                            {(() => {
                              const overrideRules = parseOverrideRules(metadata.override_rules);
                              const isCleanPass =
                                metadata.validation_status?.trim().toLowerCase() === "passed";
                              if (isCleanPass && overrideRules.length === 0) return null;
                              return (
                                <ValidationBadge
                                  validationStatus={metadata.validation_status}
                                  overrideRules={overrideRules}
                                  showMissing
                                />
                              );
                            })()}
                          </div>
                        )}
                      </td>
                    );
                  })}
                  <td
                    class="table-td py-2 text-center font-mono font-semibold text-[var(--bb-data-fg-primary)]"
                    title={`${AVG_RANK_LABEL}: ${
                      formatCoverage(platform.n_cohorts, cohorts.length, { unitLabel: "rankings" }).valueText
                    }. ${COVERAGE_POLICY_COPY}`}
                  >
                    {platform.avg_rank !== null ? (
                      <>
                        {formatAverageRank(platform.avg_rank).valueText}
                        <span class="mt-1 block text-[11px] font-normal text-[var(--bb-data-fg-muted)]">
                          over {formatCoverage(platform.n_cohorts, cohorts.length).valueText}
                        </span>
                      </>
                    ) : (
                      <span class="text-[var(--bb-data-fg-subtle)]">No score</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {collapsedPlatforms.length > 0 && (
          <div class="border-t border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-3 text-center">
            <button
              type="button"
              class="cursor-pointer border-0 bg-transparent p-0 text-sm font-medium text-[var(--bb-accent-hover)] underline hover:text-[var(--bb-accent)]"
              aria-expanded={showUnrankedPlatforms}
              onClick={() => setShowUnrankedPlatforms((shown) => !shown)}
            >
              {showUnrankedPlatforms
                ? `Hide ${collapsedPlatforms.length.toLocaleString()} unranked ${collapsedPlatforms.length === 1 ? "platform" : "platforms"}`
                : `${collapsedPlatforms.length.toLocaleString()} more ${collapsedPlatforms.length === 1 ? "platform has" : "platforms have"} published results but nothing ranked — Show them`}
            </button>
          </div>
        )}
        {visiblePlatforms.length < renderedPlatforms.length && (
          <div class="border-t border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-3 text-center">
            <button
              type="button"
              class="btn btn-secondary"
              onClick={() => setVisiblePlatformLimit((limit) => limit + PLATFORM_RENDER_INCREMENT)}
            >
              Show more platforms
            </button>
          </div>
        )}
      </div>

      <details class="mt-3">
        <summary class="cursor-pointer text-xs text-[var(--bb-data-fg-subtle)] hover:text-[var(--bb-data-fg-muted)]">
          How this matrix is calculated
        </summary>
        <div class="mt-2 rounded-md border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-3 text-xs text-[var(--bb-data-fg-muted)] space-y-1">
          <p>
            <strong>Times</strong> shows the ranking&apos;s primary metric in its native units:
            geomean latency for most benchmarks, Power@Size for TPC-H/TPC-DS.
          </p>
          <p>
            <strong>Ranks</strong> keeps the original meta-leaderboard contract: 1 = best in the
            ranking. {COVERAGE_POLICY_COPY}
          </p>
          <p>
            <strong>Relative to best</strong> shows every result against the best result in its ranking. 1.00× is best; lower values are worse.
          </p>
        </div>
      </details>
    </section>
  );
}

function cohortMetricSublabel(cohort: MetaCohort): string {
  const metric = cohort.primary_metric;
  const direction = cohort.primary_order === "desc" ? "higher is better" : "lower is better";
  const metricLabel = metric === "power_score"
    ? "Power score"
    : metric === "throughput_at_size"
      ? "Throughput@Size"
      : metric === "display_geomean_ms" || metric === "geomean_ms"
        ? "Geomean latency"
        : metric === "total_duration_s"
          ? "Total duration"
          : metric;
  return `${formatPhaseWithStreams(cohort.phase, cohort.stream_count)} · ${metricLabel} · ${direction}`;
}

function cellText(rank: MetaRank, cohort: MetaCohort, mode: MetaLeaderboardMode): string {
  if (mode === "ranks") return formatRank(rank.rank, rank.total).valueText;
  if (mode === "speedup") {
    return rank.speedup_vs_best !== null && rank.speedup_vs_best !== undefined
      ? formatSpeedup(rank.speedup_vs_best).valueText
      : "-";
  }
  if (rank.metric_value === null || rank.metric_value === undefined) return "-";
  return cohort.primary_order === "desc" ? fmtScoreCompact(rank.metric_value) : fmtGeomean(rank.metric_value);
}

function nativeMetricText(rank: MetaRank, cohort: MetaCohort): string {
  if (rank.metric_value === null || rank.metric_value === undefined) return "Native value unavailable";
  const value = cohort.primary_order === "desc" ? fmtScoreCompact(rank.metric_value) : fmtGeomean(rank.metric_value);
  return `Native: ${value}`;
}

function exactMetricTitle(rank: MetaRank | undefined, cohort: MetaCohort): string | null {
  if (!rank || rank.metric_value === null || rank.metric_value === undefined) return null;
  if (cohort.primary_metric === "power_score") {
    return `Exact power score: ${fmtScoreExact(rank.metric_value)}`;
  }
  if (cohort.primary_metric === "throughput_at_size") {
    return `Exact Throughput@Size: ${fmtScoreExact(rank.metric_value)}`;
  }
  return null;
}

export function variantKeyOf(row: { variant_id?: string | null; platform_id: string }): string {
  return row.variant_id || row.platform_id;
}

export function variantLabelOf(row: { variant_label?: string | null; platform: string }): string {
  return row.variant_label || row.platform;
}

function findCohortPlatform(cohort: MetaCohort, platformId: string): MetaCohortPlatform | undefined {
  const rows = cohort.platforms?.filter((row) => variantKeyOf(row) === platformId);
  if (!rows || rows.length === 0) return undefined;
  return rows.find((row) => row.rank !== null) ?? rows[0];
}

function metaLeaderboardCellState(
  rank: MetaRank | undefined,
  result: MetaCohortPlatform | undefined,
  cohort: MetaCohort,
): MetaLeaderboardCellState {
  const rankSource = rank ?? rankFromResult(result, cohort);
  if (rankSource) return { kind: "ranked", rank: rankSource, result };
  if (result) {
    const exclusionReason =
      result.ranking_exclusion_reason ??
      cohort.cohort_ranking_exclusion_reason ??
      result.display_exclusion_reason ??
      result.comparison_exclusion_reason ??
      null;
    return {
      kind: "unranked",
      result,
      label: exclusionReason ? "Excluded" : "Unranked",
      reason: formatTimingExclusion(exclusionReason, "Published evidence is not rankable."),
    };
  }
  return { kind: "missing" };
}

function rankFromResult(result: MetaCohortPlatform | undefined, cohort: MetaCohort): MetaRank | undefined {
  if (!result || result.rank === null) return undefined;
  return {
    rank: result.rank,
    total: cohort.cohort_ranked_count,
    metric_value: result.metric_value,
    speedup_vs_best: result.speedup_vs_best,
  };
}

function cellTitle(
  platform: MetaPlatform,
  cohort: MetaCohort,
  state: MetaLeaderboardCellState,
  mode: MetaLeaderboardMode,
  runAge: string | null,
): string | undefined {
  if (state.kind === "missing") return MISSING_COHORT_TITLE;
  if (state.kind === "unranked") {
    return [
      `${platform.platform} has published evidence for ${cohort.label}, but it is ${state.label.toLowerCase()}: ${state.reason}`,
      runAgeText(runAge),
    ].filter(Boolean).join(" ");
  }
  const exact = exactMetricTitle(state.rank, cohort);
  const age = runAgeText(runAge);
  if (!exact && !age) return undefined;
  return [`${platform.platform} ${MODE_LABELS[mode].toLowerCase()} for ${cohort.label}.`, exact, age]
    .filter(Boolean)
    .join(" ");
}

function receiptLinkTitle(
  state: Exclude<MetaLeaderboardCellState, { kind: "missing" }>,
  cohort: MetaCohort,
  runAge: string | null,
): string {
  if (state.kind === "unranked") {
    return ["Open result receipt.", `${state.label}: ${state.reason}`, runAgeText(runAge)].filter(Boolean).join(" ");
  }
  const exact = exactMetricTitle(state.rank, cohort);
  const age = runAgeText(runAge);
  return ["Open result receipt.", exact, age].filter(Boolean).join(" ");
}

function renderCellValue(
  state: MetaLeaderboardCellState,
  cohort: MetaCohort,
  mode: MetaLeaderboardMode,
) {
  if (state.kind === "missing") return <span class="text-[var(--bb-data-fg-subtle)]">No run</span>;
  if (state.kind === "unranked") {
    return (
      <span class="text-xs font-normal text-[var(--bb-data-fg-muted)]">{state.label}</span>
    );
  }
  const rank = state.rank;
  const text = cellText(rank, cohort, mode);
  if (mode === "ranks") {
    return (
      <span class={rank.rank === 1 ? "font-semibold text-[var(--bb-tone-success-fg)]" : "text-[var(--bb-data-fg-primary)]"}>{text}</span>
    );
  }
  if (mode === "speedup") {
    return (
      <span>
        <span class="block font-medium text-[var(--bb-data-fg-primary)]">{text}</span>
        <span class="mt-0.5 block text-[10px] font-normal text-[var(--bb-data-fg-muted)]">
          {nativeMetricText(rank, cohort)}
        </span>
      </span>
    );
  }
  return text;
}

function cellShadeMetric(
  rank: MetaRank | undefined,
  mode: MetaLeaderboardMode,
  cohort: MetaCohort,
): number | null {
  if (!rank) return null;

  if (mode === "ranks") return rank.rank;

  if (cohort.primary_order === "desc") {
    if (rank.speedup_vs_best === null || rank.speedup_vs_best === undefined || rank.speedup_vs_best <= 0) {
      return null;
    }
    return 1 / rank.speedup_vs_best;
  }

  return rank.metric_value ?? null;
}

function describeCell(
  platform: MetaPlatform,
  cohort: MetaCohort,
  state: MetaLeaderboardCellState,
  mode: MetaLeaderboardMode,
  runAge: string | null,
): string {
  if (state.kind === "missing") {
    return `${platform.platform} has no published run for ${cohort.label}. ${COVERAGE_POLICY_COPY}`;
  }
  if (state.kind === "unranked") {
    return [
      `${platform.platform} has published evidence for ${cohort.label}, but it is ${state.label.toLowerCase()}: ${state.reason}`,
      runAgeText(runAge),
    ].filter(Boolean).join(" ");
  }
  const rank = state.rank;
  const text = cellText(rank, cohort, mode);
  const nativeSuffix = mode === "speedup" ? `; ${nativeMetricText(rank, cohort)}` : "";
  const age = runAgeText(runAge);
  return `${platform.platform} ${MODE_LABELS[mode].toLowerCase()} for ${cohort.label}: ${text}${nativeSuffix}${age ? `; ${age}` : ""}`;
}

function runAgeText(runAge: string | null): string | null {
  return runAge === null ? null : `Run age: ${runAge}.`;
}

function hasPublishedEvidence(platform: MetaPlatform, cohorts: MetaCohort[]): boolean {
  return cohorts.some((cohort) => findCohortPlatform(cohort, variantKeyOf(platform)) !== undefined);
}

function hasRankedEvidence(platform: MetaPlatform, cohorts: MetaCohort[]): boolean {
  return cohorts.some((cohort) => {
    const result = findCohortPlatform(cohort, variantKeyOf(platform));
    return metaLeaderboardCellState(platform.ranks[cohort.key], result, cohort).kind === "ranked";
  });
}

function allExcludedRankingReason(cohort: MetaCohort, platforms: MetaPlatform[]): string | null {
  const results = cohort.platforms ?? [];
  const hasRankedCell = platforms.some((platform) => {
    const result = findCohortPlatform(cohort, variantKeyOf(platform));
    return metaLeaderboardCellState(platform.ranks[cohort.key], result, cohort).kind === "ranked";
  });
  if (results.length === 0 || hasRankedCell) return null;

  const reasons = Array.from(
    new Set(
      results.map((result) =>
        formatTimingExclusion(
          result.ranking_exclusion_reason ?? cohort.cohort_ranking_exclusion_reason,
          "Published evidence is not rankable.",
        ),
      ),
    ),
  );
  return reasons.join(" ");
}

function platformScopeLabel(collapsedCount: number, showUnrankedPlatforms: boolean): string {
  if (collapsedCount === 0) return "ranked-scope platforms";
  return showUnrankedPlatforms ? "published platforms" : "ranked platforms";
}

function comparePlatforms(
  a: MetaPlatform,
  b: MetaPlatform,
  sortKey: MetaLeaderboardSort,
  cohorts: MetaCohort[],
  resultMetadataById?: ReadonlyMap<string, MetaResultMetadata>,
): number {
  const byName = () => a.platform.localeCompare(b.platform);

  if (sortKey === "coverage") {
    return (
      b.n_cohorts - a.n_cohorts ||
      compareNullableNumber(a.avg_rank, b.avg_rank, "asc") ||
      byName()
    );
  }

  if (sortKey === "best_rank") {
    return (
      compareNullableNumber(bestRank(a), bestRank(b), "asc") ||
      compareNullableNumber(a.avg_rank, b.avg_rank, "asc") ||
      byName()
    );
  }

  if (sortKey === "recent_activity") {
    const aDate = latestRunDate(a, cohorts, resultMetadataById);
    const bDate = latestRunDate(b, cohorts, resultMetadataById);
    return compareNullableString(aDate, bDate, "desc") || byName();
  }

  return compareNullableNumber(a.avg_rank, b.avg_rank, "asc") || byName();
}

function bestRank(platform: MetaPlatform): number | null {
  const ranks = Object.values(platform.ranks)
    .map((rank) => rank.rank)
    .filter((rank): rank is number => Number.isFinite(rank));
  return ranks.length > 0 ? Math.min(...ranks) : null;
}

function latestRunDate(
  platform: MetaPlatform,
  cohorts: MetaCohort[],
  resultMetadataById?: ReadonlyMap<string, MetaResultMetadata>,
): string | null {
  if (!resultMetadataById) return null;

  let latest: string | null = null;
  for (const cohort of cohorts) {
    for (const result of cohort.platforms ?? []) {
      if (result.platform_id !== platform.platform_id) continue;
      const runDate = resultMetadataById.get(result.result_id)?.run_date;
      if (!runDate) continue;
      if (latest === null || runDate.localeCompare(latest) > 0) {
        latest = runDate;
      }
    }
  }
  return latest;
}

function compareNullableNumber(
  a: number | null,
  b: number | null,
  direction: "asc" | "desc",
): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return direction === "asc" ? a - b : b - a;
}

function compareNullableString(
  a: string | null,
  b: string | null,
  direction: "asc" | "desc",
): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return direction === "asc" ? a.localeCompare(b) : b.localeCompare(a);
}
