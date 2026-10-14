import { MISSING_VALUE_GLYPH } from "@/lib/metricFormatters";
import type { BenchmarkSummary, PlatformRow } from "@/types";
import { paletteColor } from "@/lib/chartTheme";
import { runMarkerPath } from "@/lib/chartMarkers";
import { computeRankTable } from "@/lib/chartMath";
import { formatTimingExclusion, isRankable, platformTimingValue } from "@/lib/displayEligibility";
import { queryDisplayLabel, sortQueryIds } from "@/lib/queryLabels";
import {
  formatRunIdentitiesForCohort,
  preserveUniqueAfterTruncation,
  type RunIdentitySource,
} from "@/lib/runIdentity";
import { resultIdentityAriaLabel, resultReceiptHref } from "@/lib/resultLinks";

export { preserveUniqueAfterTruncation } from "@/lib/runIdentity";

interface Props {
  summary: BenchmarkSummary;
  preserveOrder?: boolean;
}

function ordinal(n: number): string {
  const suffixes = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${suffixes[(v - 20) % 10] ?? suffixes[v] ?? suffixes[0]}`;
}

const HEADER_MARKER_SIZE = 14;
const HEADER_MARKER_CENTER = HEADER_MARKER_SIZE / 2;
const HEADER_MARKER_RADIUS = 5.5;
const HEADER_LABEL_MAX_LENGTH = 16;

function uniqueFastestCounts(
  platformCount: number,
  sharedQueryIds: readonly string[],
  ranks: Record<string, (number | null)[]>,
): number[] {
  const counts = new Array<number>(platformCount).fill(0);
  for (const queryId of sharedQueryIds) {
    const firsts = (ranks[queryId] ?? []).flatMap((rank, index) => (rank === 1 ? [index] : []));
    if (firsts.length === 1) counts[firsts[0]!]! += 1;
  }
  return counts;
}

export function RankTable({ summary, preserveOrder = false }: Props) {
  const { platforms, query_ids } = summary;
  if (platforms.length === 0 || query_ids.length === 0) return null;
  const sortedQueryIds = preserveOrder ? query_ids : sortQueryIds(query_ids);
  const rankableCellCount = countRankableTimingCells(platforms, sortedQueryIds);

  if (rankableCellCount === 0) {
    return <RankTableEmptyState summary={summary} queryIds={sortedQueryIds} />;
  }

  const rankingTimings = platforms.map((platform) =>
    Object.fromEntries(
      sortedQueryIds.map((queryId) => [queryId, isRankable(platform) ? platformTimingValue(platform, queryId) : null]),
    ),
  );
  const ranks = computeRankTable(sortedQueryIds, rankingTimings);
  const rankedTimings = rankingTimings.filter((_, i) => isRankable(platforms[i]!));
  const sharedQueryIds = sortedQueryIds.filter((queryId) => rankedTimings.every((timings) => timings[queryId] !== null));
  const winCounts = uniqueFastestCounts(platforms.length, sharedQueryIds, ranks);
  const maxWins = Math.max(...winCounts);

  const headerIdentitySources: RunIdentitySource[] = platforms.map((p) => ({
    result_id: p.result_id,
    platform: p.platform,
    platform_version: p.platform_version,
    run_date: p.run_date,
    trust_label: p.trust_label,
    deployment_class: p.deployment_class,
    instance_or_warehouse: p.instance_or_warehouse,
  }));
  const headerIdentities = formatRunIdentitiesForCohort(headerIdentitySources, "compact");
  const headerTooltips = formatRunIdentitiesForCohort(headerIdentitySources, "tooltip");
  const displayedHeaders = preserveUniqueAfterTruncation(headerIdentities, HEADER_LABEL_MAX_LENGTH);
  const excludedPlatformCount = platforms.filter((platform) => !isRankable(platform)).length;

  return (
    <div>
      <div class="w-full overflow-x-auto">
        <table
          class="text-xs border-collapse min-w-full"
          aria-label="Per-query platform rankings (1st = fastest)"
        >
        <thead>
          <tr>
            <th class="text-left px-2 py-1.5 border-b border-[var(--bb-data-border)] text-[var(--bb-data-fg-muted)] font-normal sticky left-0 bg-[var(--bb-surface-data)] min-w-[4rem]">
              Query
            </th>
            {platforms.map((p, i) => {
              const tooltip = headerTooltips[i] ?? p.platform;
              const truncated = displayedHeaders[i] ?? p.platform;
              const exclusion = isRankable(p) ? null : formatTimingExclusion(p.ranking_exclusion_reason);
              return (
                <th
                  key={p.result_id}
                  class="px-2 py-1.5 border-b border-[var(--bb-data-border)] text-[var(--bb-data-fg-primary)] font-semibold whitespace-nowrap text-center"
                  title={exclusion ? `${tooltip} - ${exclusion}` : tooltip}
                >
                  <svg
                    class="inline-block mr-1 align-middle"
                    width={HEADER_MARKER_SIZE}
                    height={HEADER_MARKER_SIZE}
                    viewBox={`0 0 ${HEADER_MARKER_SIZE} ${HEADER_MARKER_SIZE}`}
                    aria-hidden="true"
                  >
                    <path
                      d={runMarkerPath(p.color_slot ?? i, HEADER_MARKER_CENTER, HEADER_MARKER_CENTER, HEADER_MARKER_RADIUS)}
                      fill={paletteColor(p.color_slot ?? i)}
                      stroke="var(--bb-surface-data)"
                      stroke-width={0.75}
                    />
                  </svg>
                  <a
                    href={resultReceiptHref(p)}
                    aria-label={resultIdentityAriaLabel(p, "receipt")}
                    title={tooltip}
                    class="text-[var(--bb-data-fg-primary)] no-underline hover:text-[var(--bb-accent-hover)] hover:underline"
                  >
                    {truncated}
                  </a>
                  {exclusion && (
                    <span
                      class="ml-1 text-[var(--bb-data-fg-subtle)]"
                      aria-label={exclusion}
                      title={exclusion}
                      tabIndex={0}
                    >
                      *
                    </span>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sortedQueryIds.map((qid) => (
            <tr key={qid} class="hover:bg-[var(--bb-surface-data-muted)]">
              <td class="px-2 py-1 border-b border-[var(--bb-data-border)] text-[var(--bb-data-fg-muted)] font-mono sticky left-0 bg-[var(--bb-surface-data)]">
                {queryDisplayLabel(qid)}
              </td>
              {platforms.map((_, i) => {
                const r = ranks[qid]?.[i] ?? null;
                return (
                  <td
                    key={i}
                    class={`px-2 py-1 border-b border-[var(--bb-data-border)] text-center font-mono ${
                      r === 1
                        ? "font-bold text-[var(--bb-tone-success-fg)] bg-[var(--bb-tone-success-bg)]"
                        : r === null
                          ? "text-[var(--bb-data-fg-subtle)]"
                          : "text-[var(--bb-data-fg-muted)]"
                    }`}
                  >
                    {r === null ? MISSING_VALUE_GLYPH : ordinal(r)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr class="bg-[var(--bb-surface-data-muted)] border-t border-[var(--bb-data-border)]">
            <td class="px-2 py-1.5 text-[var(--bb-data-fg-muted)] sticky left-0 bg-[var(--bb-surface-data-muted)]">
              Fastest on (shared queries)
            </td>
            {winCounts.map((w, i) => (
              <td
                key={i}
                class={`px-2 py-1.5 text-center font-semibold ${
                  w === maxWins && maxWins > 0 ? "text-[var(--bb-tone-success-fg)]" : "text-[var(--bb-data-fg-primary)]"
                }`}
              >
                {w}
              </td>
            ))}
          </tr>
        </tfoot>
        </table>
      </div>
      <p class="mt-2 text-[11px] text-[var(--bb-data-fg-subtle)]">
        Fastest on counts the {sharedQueryIds.length} of {sortedQueryIds.length} queries that every ranked run has a
        timing for. A tie for first place is not counted.
      </p>
      {excludedPlatformCount > 0 && (
        <p class="mt-2 text-[11px] text-[var(--bb-data-fg-subtle)]">
          Columns marked with * are shown for reference but are not included in the ranking.
        </p>
      )}
    </div>
  );
}

function countRankableTimingCells(platforms: readonly PlatformRow[], queryIds: readonly string[]): number {
  return platforms.reduce((total, platform) => {
    if (!isRankable(platform)) return total;
    return total + queryIds.filter((queryId) => platformTimingValue(platform, queryId) !== null).length;
  }, 0);
}

function RankTableEmptyState({ summary, queryIds }: { summary: BenchmarkSummary; queryIds: readonly string[] }) {
  const reasons = summarizeRankChartReasons(summary.platforms, queryIds);
  return (
    <div
      role="status"
      aria-label="Rank chart unavailable"
      class="rounded-md border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-3 text-sm"
    >
      <p class="font-semibold text-[var(--bb-data-fg-primary)]">Rank chart unavailable</p>
      <p class="mt-1 text-[var(--bb-data-fg-muted)]">
        No timings in this ranking meet the requirements for ranking. The published runs are excluded or have no usable timing,
        so a normal rank matrix would be all dashes.
      </p>
      {reasons.length > 0 && (
        <ul class="mt-2 space-y-1 text-xs text-[var(--bb-data-fg-muted)]">
          {reasons.slice(0, 3).map(({ reason, count }) => (
            <li key={reason}>
              {count} {count === 1 ? "result" : "results"}: {reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function summarizeRankChartReasons(
  platforms: readonly PlatformRow[],
  queryIds: readonly string[],
): { reason: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const platform of platforms) {
    const reason = rankChartReason(platform, queryIds);
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([reason, count]) => ({ reason, count }))
    .sort((a, b) => b.count - a.count || a.reason.localeCompare(b.reason));
}

function rankChartReason(platform: PlatformRow, queryIds: readonly string[]): string {
  if (!isRankable(platform)) return formatTimingExclusion(platform.ranking_exclusion_reason);
  const hasValidTiming = queryIds.some((queryId) => platformTimingValue(platform, queryId) !== null);
  return hasValidTiming
    ? "Result has rankable timing evidence."
    : formatTimingExclusion("no_valid_display_timing", "No valid display timing is available.");
}
