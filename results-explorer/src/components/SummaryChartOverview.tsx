import { useEffect, useMemo, useState } from "preact/hooks";
import type { ComponentChildren, JSX } from "preact";
import type { BenchmarkSummary, PlatformRow } from "@/types";
import {
  applicableCharts,
  buildRenderableSummary,
  CHART_REGISTRY_BY_ID,
  type ChartContext,
  type ChartHistoricalEntry,
  type ChartRegistryEntry,
} from "@/lib/chartRegistry";
import { assignRunColorSlots, paletteColor, PHASE_COLORS } from "@/lib/chartTheme";
import {
  buildLatencyBarScale,
  buildLogLatencyScale,
  computeECDFPoints,
  computeRankTable,
  latencyScaleFraction,
  lightnessForCell,
  logLatencyFraction,
} from "@/lib/chartMath";
import { fitLabel } from "@/lib/chartLabels";
import {
  chartDatasetClassLabel,
  chartDatasetEmptyTitle,
  filterSummaryForChartDataset,
  isRankable,
  isTimingDisplayable,
  isValidTimingValue,
  normalizePrimaryMetric,
  platformTimingValue,
  primaryMetricHigherIsBetter,
  primaryMetricValue,
  summarizeChartDatasetExclusions,
  validPrimaryMetricValue,
} from "@/lib/displayEligibility";
import { formatRunIdentitiesForCohort, formatRunIdentityLabelsForCohort } from "@/lib/runIdentity";
import { fmtGeomean, fmtScore } from "@/utils";
import { AnalysisCard, AnalysisCardGrid } from "@/components/AnalysisCardGrid";
import { DistributionBox } from "@/components/DistributionBox";
import { QueryHeatmap } from "@/components/QueryHeatmap";
import { RankGateNotice } from "@/components/RankGateNotice";
import { QueryHistogram } from "@/components/QueryHistogram";
import { CostScatter } from "@/components/CostScatter";
import { TimeSeries } from "@/components/TimeSeries";
import { PercentileLadder } from "@/components/PercentileLadder";
import { StackedPhase } from "@/components/StackedPhase";
import { CDFChart } from "@/components/CDFChart";
import { RankTable } from "@/components/RankTable";
import { EmptyState } from "@/components/EmptyState";

interface Props {
  context: Extract<ChartContext, { kind: "summary" }>;
  excludeChartIds?: readonly string[];
  cardAnchors?: Readonly<Record<string, string>>;
  forceOpenChartId?: string | null;
  rankGateReason?: string | null;
  rankGateContext?: { benchmark: string; scaleFactor: string; phase: string };
}

const LONG_LAYOUT_CHART_IDS = [
  "query_heatmap",
  "percentile_ladder",
  "cdf_chart",
  "query_histogram",
  "stacked_phase",
  "time_series",
  "rank_table",
  "cost_scatter",
] as const;

const CHART_DISPLAY_TITLES: Record<string, string> = {
  query_heatmap: "Query matrix",
  rank_table: "Query ranks",
};

const PLATFORM_LABEL_FONT_PX = 14;
const PLATFORM_LABEL_WIDTH_PX = 190;
const THUMBNAIL_FONT_PX = 10;
const THUMBNAIL_LABEL_WIDTH_PX = 88;

export function additionalAnalysesLabel(count: number): string {
  return `${count} additional ${count === 1 ? "analysis" : "analyses"}`;
}

export function orderByDisplayGeomean<T extends PlatformRow>(platforms: readonly T[]): T[] {
  return [...platforms].sort((a, b) => {
    const av = validPrimaryMetricValue(a, "display_geomean_ms");
    const bv = validPrimaryMetricValue(b, "display_geomean_ms");
    if (av !== null && bv !== null) return av - bv;
    if (av !== null) return -1;
    if (bv !== null) return 1;
    return a.platform.localeCompare(b.platform);
  });
}

export function runColorIndexFor(summary: BenchmarkSummary | null): (platform: PlatformRow) => number {
  const indexByResultId = new Map(summary?.platforms.map((platform, index) => [platform.result_id, index]) ?? []);
  return (platform) => platform.color_slot ?? indexByResultId.get(platform.result_id) ?? 0;
}

function exclusionCauses(
  excludedCount: number,
  reasons: readonly { reason: string; count: number }[],
  unexplainedCause: string,
): string {
  const explained = reasons.reduce((total, entry) => total + entry.count, 0);
  const causes = reasons.map((entry) => entry.reason.replace(/\.$/, ""));
  if (excludedCount > explained) causes.push(unexplainedCause);
  return causes.join("; ");
}

function populationQueryText(
  summary: BenchmarkSummary,
  rows: readonly PlatformRow[],
  scope: "cohort" | "compare",
): string {
  const total = summary.query_ids.length;
  if (scope !== "compare" || rows.length === 0) return `${total} queries`;
  const shared = summary.query_ids.filter((queryId) =>
    rows.every((row) => {
      const value = row.timings[queryId];
      return typeof value === "number" && Number.isFinite(value) && value > 0;
    }),
  ).length;
  return shared === total ? `${total} queries` : `${shared} shared of ${total} queries`;
}

export function SummaryMetricTable({
  summary,
  scope = "cohort",
  framed = true,
}: {
  summary: BenchmarkSummary;
  scope?: "cohort" | "compare";
  framed?: boolean;
}) {
  const displayRows = summary.platforms.filter(
    (platform) => isTimingDisplayable(platform) && validPrimaryMetricValue(platform, "display_geomean_ms") !== null,
  );
  const scoreMetric = normalizePrimaryMetric(summary.ranking?.primary_metric) === "throughput_at_size"
    ? "throughput_at_size"
    : "power_score";
  const scoreName = scoreMetric === "throughput_at_size" ? "Throughput@Size" : "Power@Size";
  const powerRows = summary.platforms.filter(
    (platform) => isRankable(platform) && validPrimaryMetricValue(platform, scoreMetric) !== null,
  );
  const displayScale = buildLatencyBarScale(displayRows.map((platform) => platform.display_geomean_ms));
  const maxPower = Math.max(...powerRows.map((platform) => primaryMetricValue(platform, scoreMetric) ?? 0), 1);
  const labels = formatRunIdentityLabelsForCohort(
    summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
  );
  const displayLabels = fitPlatformLabels(labels.map((label) => label.disambiguated));
  const rows = orderByDisplayGeomean(summary.platforms);
  const rowIndexByResultId = new Map(summary.platforms.map((platform, index) => [platform.result_id, index]));
  const total = summary.platforms.length;
  const noun = scope === "compare" ? "selected" : "submitted";
  const showScoreColumn = powerRows.length > 0;
  const heading = showScoreColumn ? `Geomean and ${scoreName}` : "Geomean";
  const unrankedCount = total - powerRows.length;
  const undisplayedCount = total - displayRows.length;
  const rankingExclusions = summarizeChartDatasetExclusions(summary.platforms, "rank_safe");
  const displayExclusions = summarizeChartDatasetExclusions(summary.platforms, "display_safe");
  const rankingNote = unrankedCount === 0
    ? null
    : `Not ranked: ${unrankedCount} of ${total} runs ${unrankedCount === 1 ? "is" : "are"} excluded from ranking — ${exclusionCauses(unrankedCount, rankingExclusions, `${scoreName} is not recorded`)}.`;
  const displayNote = undisplayedCount === 0
    ? null
    : `Not in the latency chart: ${undisplayedCount} of ${total} runs ${undisplayedCount === 1 ? "is" : "are"} excluded — ${exclusionCauses(undisplayedCount, displayExclusions, "display timing is not recorded")}.`;

  return (
    <section class={framed ? "card" : undefined} aria-labelledby="summary-metric-table-title">
      <h3
        id="summary-metric-table-title"
        class="mb-4 border-b border-[var(--bb-data-border)] pb-3 text-lg font-semibold text-[var(--bb-data-fg-primary)]"
      >
        {heading}
      </h3>
      <div class="overflow-x-auto">
        <table
          class="summary-metric-table min-w-[38rem] w-full border-collapse"
          aria-label="Speed and throughput by platform"
        >
          <caption class="sr-only">
            Display geomean latency{showScoreColumn ? ` and ${scoreName}` : ""} for every{" "}
            {scope === "compare" ? "selected run" : "submitted platform"}
          </caption>
          <thead>
            <tr class="border-b border-[var(--bb-data-border-strong)]">
              <th
                scope="col"
                class="w-[14rem] px-2 py-2 text-left text-xs font-medium text-[var(--bb-data-fg-muted)]"
              >
                Platform
              </th>
              <th scope="col" class="px-2 py-2 text-left text-xs font-medium text-[var(--bb-data-fg-muted)]">
                <span class="block">Display geomean</span>
                <span class="font-normal text-[var(--bb-data-fg-subtle)]">lower is better</span>
              </th>
              {showScoreColumn && (
                <th scope="col" class="px-2 py-2 text-left text-xs font-medium text-[var(--bb-data-fg-muted)]">
                  <span class="block">{scoreName}</span>
                  <span class="font-normal text-[var(--bb-data-fg-subtle)]">higher is better</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((platform, rowIndex) => {
              const sourceIndex = rowIndexByResultId.get(platform.result_id) ?? rowIndex;
              const color = paletteColor(platform.color_slot ?? sourceIndex);
              const displayValue = validPrimaryMetricValue(platform, "display_geomean_ms");
              const powerValue = isRankable(platform)
                ? validPrimaryMetricValue(platform, scoreMetric)
                : null;
              const displayFraction = displayValue !== null && displayScale
                ? latencyScaleFraction(displayValue, displayScale)
                : null;
              const powerFraction = powerValue !== null ? Math.min(1, powerValue / maxPower) : null;
              return (
                <tr key={platform.result_id} class="border-b border-[var(--bb-data-border)] align-middle">
                  <th scope="row" class="px-2 py-1.5 text-left text-sm font-medium text-[var(--bb-data-fg-primary)]">
                    <span
                      class="flex min-w-0 items-center gap-2"
                      title={labels[sourceIndex]?.full ?? platform.platform}
                    >
                      <span
                        class="h-2.5 w-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: color }}
                        aria-hidden="true"
                      />
                      <span class="min-w-0 truncate" title={labels[sourceIndex]?.full ?? platform.platform}>
                        {displayLabels[sourceIndex] ?? platform.platform}
                      </span>
                    </span>
                  </th>
                  <InlineMetricCell
                    value={displayValue}
                    formatted={displayValue === null ? "—" : fmtGeomean(displayValue)}
                    fraction={displayFraction}
                    color={color}
                    title={
                      displayValue === null
                        ? "Display timing unavailable"
                        : `${fmtGeomean(displayValue)} display geomean`
                    }
                  />
                  {showScoreColumn && (
                    <InlineMetricCell
                      value={powerValue}
                      formatted={powerValue === null ? "—" : fmtScore(powerValue)}
                      fraction={powerFraction}
                      color={color}
                      title={
                        powerValue === null
                          ? `${scoreName} unavailable or not rank-safe`
                          : `${fmtScore(powerValue)} ${scoreName}`
                      }
                    />
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div
        class="mt-4 grid gap-3 border-t border-[var(--bb-data-border)] pt-4 text-sm
          text-[var(--bb-data-fg-muted)] sm:grid-cols-2"
      >
        <p>
          <strong class="text-[var(--bb-data-fg-primary)]">Population:</strong>{" "}
          {`${total} ${noun} runs · ${displayRows.length} display-safe rows · ${populationQueryText(summary, displayRows, scope)}`}
        </p>
        {rankingNote !== null && <p>{rankingNote}</p>}
        {displayNote !== null && <p class="sm:col-span-2 text-xs text-[var(--bb-data-fg-subtle)]">{displayNote}</p>}
      </div>
    </section>
  );
}

function fitPlatformLabels(labels: readonly string[]): string[] {
  const fitted = labels.map((label) => fitLabel(label, PLATFORM_LABEL_WIDTH_PX, PLATFORM_LABEL_FONT_PX).text);
  const counts = new Map<string, number>();
  for (const text of fitted) counts.set(text, (counts.get(text) ?? 0) + 1);
  return fitted.map((text, index) => ((counts.get(text) ?? 0) > 1 ? labels[index]! : text));
}

export function SummaryChartOverview({
  context,
  excludeChartIds = [],
  cardAnchors,
  forceOpenChartId,
  rankGateReason,
  rankGateContext,
}: Props) {
  const summary = buildRenderableSummary(context);
  const [openChartIds, setOpenChartIds] = useState<Set<string>>(() => new Set());
  const [highContrast, setHighContrast] = useState(false);

  useEffect(() => {
    if (!forceOpenChartId) return;
    setOpenChartIds((current) => (current.has(forceOpenChartId) ? current : new Set(current).add(forceOpenChartId)));
  }, [forceOpenChartId]);

  useEffect(() => {
    if (!forceOpenChartId || !openChartIds.has(forceOpenChartId)) return;
    const anchorId = cardAnchors?.[forceOpenChartId];
    if (!anchorId) return;
    const frame = requestAnimationFrame(() => {
      document.getElementById(anchorId)?.scrollIntoView?.({ block: "start" });
    });
    return () => cancelAnimationFrame(frame);
  }, [cardAnchors, forceOpenChartId, openChartIds]);

  const charts = useMemo(() => {
    const applicableById = new Map(applicableCharts(context).map((chart) => [chart.id, chart]));
    const excluded = new Set(excludeChartIds);
    return LONG_LAYOUT_CHART_IDS.map((id) => {
      const chart = applicableById.get(id) ?? CHART_REGISTRY_BY_ID[id];
      return chart && !excluded.has(id) ? chart : null;
    }).filter((chart): chart is ChartRegistryEntry => chart !== null);
  }, [context, excludeChartIds]);

  if (summary === null) return null;
  const colorIndexFor = runColorIndexFor(summary);

  return (
    <div class="scroll-mt-24 space-y-6" data-testid="summary-chart-overview" id="cohort-charts">
      <SummaryMetricTable summary={summary} />

      <section class="card" aria-labelledby="latency-distribution-title">
        <h3
          id="latency-distribution-title"
          class="mb-4 border-b border-[var(--bb-data-border)] pb-3 text-lg font-semibold text-[var(--bb-data-fg-primary)]"
        >
          Latency distribution
        </h3>
        <div data-chart-container>
          <DistributionBox
            summary={{ ...summary, platforms: orderByDisplayGeomean(summary.platforms) }}
            colorIndexFor={runColorIndexFor(summary)}
          />
        </div>
        <div class="mt-3 border-t border-[var(--bb-data-border)] pt-3 text-sm text-[var(--bb-data-fg-muted)]">
          <p>
            The spread is across different queries, not repeated runs of the same query.
            Whiskers show the observed per-query range.{" "}
            <a
              class="font-medium text-[var(--bb-accent)]"
              href={cardAnchors?.query_heatmap ? `#${cardAnchors.query_heatmap}` : "#"}
              data-testid="see-per-query-matrix-link"
              onClick={(event) => {
                if (!cardAnchors?.query_heatmap) event.preventDefault();
                setOpenChartIds((current) => (current.has("query_heatmap") ? current : new Set(current).add("query_heatmap")));
              }}
            >
              See the per-query matrix
            </a>
            .
          </p>
        </div>
      </section>

      {charts.length > 0 && (
        <AnalysisCardGrid
          headingId="summary-more-views-title"
          headingLevel="h3"
          count={additionalAnalysesLabel(charts.length)}
          description={
            <>
              {excludeChartIds.includes("query_heatmap")
                ? "The evidence matrix above is the query heatmap. "
                : ""}
              Each thumbnail is drawn from this cohort when the cohort has data for it. Open a card to
              inspect the full chart and its data scope.
            </>
          }
        >
          {charts.map((chart) => {
            const isOpen = openChartIds.has(chart.id);
            const chartSummary = filterSummaryForChartDataset(summary, chart.eligibilityClass);
            const isEmpty = shouldShowEmptyState(summary, chartSummary, chart);
            const exclusions = summarizeChartDatasetExclusions(summary.platforms, chart.eligibilityClass);
            const isGatedRankCard = chart.id === "rank_table" && Boolean(rankGateReason);
            return (
              <AnalysisCard
                key={chart.id}
                id={chart.id}
                anchorId={cardAnchors?.[chart.id]}
                title={CHART_DISPLAY_TITLES[chart.id] ?? chart.shortTitle}
                isOpen={isOpen}
                onToggle={(open) => {
                  setOpenChartIds((current) => {
                    const next = new Set(current);
                    if (open) next.add(chart.id);
                    else next.delete(chart.id);
                    return next;
                  });
                }}
                renderThumbnail={() =>
                  isGatedRankCard ? (
                    <MiniUnavailable label="Ranking unavailable" />
                  ) : (
                    <ChartThumbnail
                      chartId={chart.id}
                      summary={chartSummary}
                      historical={context.historical ?? []}
                      colorIndexFor={colorIndexFor}
                    />
                  )
                }
                fullHeaderExtra={
                  chart.id === "query_heatmap" && !isEmpty ? (
                    <div class="mb-2 flex justify-end">
                      <button
                        type="button"
                        class={`rounded-md border px-3 py-1 text-xs transition-colors ${
                          highContrast
                            ? "border-[var(--bb-accent-hover)] bg-[var(--bb-tone-info-bg)] text-[var(--bb-tone-info-fg)]"
                            : "border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] text-[var(--bb-data-fg-muted)] hover:bg-[var(--bb-surface-data-muted)]"
                        }`}
                        onClick={() => setHighContrast((v) => !v)}
                        aria-pressed={highContrast}
                        title="Switch the matrix palette to greyscale for color-vision accessibility"
                      >
                        Reduced color
                      </button>
                    </div>
                  ) : undefined
                }
                renderFull={() =>
                  isGatedRankCard ? (
                    <RankGateNotice
                      reason={rankGateReason as string}
                      benchmark={rankGateContext?.benchmark ?? summary.benchmark}
                      scaleFactor={rankGateContext?.scaleFactor ?? String(summary.scale_factor)}
                      phase={rankGateContext?.phase ?? summary.phase}
                    />
                  ) : isEmpty ? (
                    <ChartDatasetEmptyState chart={chart} summary={summary} />
                  ) : (
                    renderExpandedChart(chart, chartSummary, context.historical ?? [], { highContrast, colorIndexFor }) ?? (
                      <p class="text-sm italic text-[var(--bb-data-fg-subtle)]">
                        No chart data is available for this view.
                      </p>
                    )
                  )
                }
                footer={
                  <div class="flex flex-wrap items-start justify-between gap-2">
                    <div class="min-w-0 flex-1">
                      {!isGatedRankCard && chartSummary.platforms.length > 0 && exclusions.length > 0 ? (
                        <ChartDatasetExclusionSummary
                          eligibilityClass={chart.eligibilityClass}
                          originalCount={summary.platforms.length}
                          renderedCount={chartSummary.platforms.length}
                          reasons={exclusions}
                        />
                      ) : null}
                    </div>
                  </div>
                }
              />
            );
          })}
        </AnalysisCardGrid>
      )}
    </div>
  );
}

function InlineMetricCell({
  value,
  formatted,
  fraction,
  color,
  title,
}: {
  value: number | null;
  formatted: string;
  fraction: number | null;
  color: string;
  title: string;
}) {
  return (
    <td class="summary-metric-cell px-2 py-1.5" title={title}>
      <div class="flex items-center gap-2">
        <span class="w-[4.5rem] shrink-0 text-right font-mono text-sm text-[var(--bb-data-fg-primary)]">
          {formatted}
        </span>
        <span class="sr-only">{value === null ? "unavailable" : "measured"}</span>
        <div class="summary-metric-track min-w-0 flex-1" aria-hidden="true">
          <span
            class="summary-metric-fill"
            style={{ width: `${fraction === null ? 0 : Math.max(3, fraction * 100)}%`, backgroundColor: color }}
          />
        </div>
      </div>
    </td>
  );
}

function shouldShowEmptyState(
  summary: BenchmarkSummary,
  chartSummary: BenchmarkSummary,
  chart: ChartRegistryEntry,
): boolean {
  if (chart.eligibilityClass === "provenance_only" || chart.eligibilityClass === "trend_safe") return false;
  return summary.platforms.length > 0 && chartSummary.platforms.length === 0;
}

function ChartDatasetEmptyState({ chart, summary }: { chart: ChartRegistryEntry; summary: BenchmarkSummary }) {
  const reasons = summarizeChartDatasetExclusions(summary.platforms, chart.eligibilityClass);
  return (
    <div
      role="status"
      aria-label={`${chart.title} unavailable`}
      class="rounded-md border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-3 py-3 text-sm"
    >
      <p class="font-semibold text-[var(--bb-data-fg-primary)]">{chartDatasetEmptyTitle(chart.eligibilityClass)}</p>
      <p class="mt-1 text-[var(--bb-data-fg-muted)]">
        This view requires {chartDatasetClassLabel(chart.eligibilityClass)}. Submitted rows remain visible in the
        table or receipt, but they are not plotted as normal chart data.
      </p>
      {reasons.length > 0 && (
        <ul class="mt-2 space-y-1 text-xs text-[var(--bb-data-fg-subtle)]">
          {reasons.slice(0, 3).map(({ reason, count }) => (
            <li key={reason}>
              {count} {count === 1 ? "row" : "rows"}: {reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ChartDatasetExclusionSummary({
  eligibilityClass,
  originalCount,
  renderedCount,
  reasons,
}: {
  eligibilityClass: ChartRegistryEntry["eligibilityClass"];
  originalCount: number;
  renderedCount: number;
  reasons: { reason: string; count: number }[];
}) {
  if (renderedCount >= originalCount) return null;
  const excludedCount = originalCount - renderedCount;
  return (
    <p class="mt-3 text-[11px] text-[var(--bb-data-fg-subtle)]">
      {excludedCount} {excludedCount === 1 ? "row" : "rows"} excluded from this view&apos;s{" "}
      {chartDatasetClassLabel(eligibilityClass)} dataset: {reasons[0]?.reason ?? "eligibility policy"}
    </p>
  );
}

function renderExpandedChart(
  chart: ChartRegistryEntry,
  summary: BenchmarkSummary,
  historical: ChartHistoricalEntry[],
  options: { highContrast?: boolean; colorIndexFor?: (platform: PlatformRow) => number } = {},
): ComponentChildren {
  switch (chart.id) {
    case "query_heatmap":
      return <QueryHeatmap summary={summary} variant="card" highContrast={options.highContrast} />;
    case "percentile_ladder": {
      const cohortLabels = formatRunIdentitiesForCohort(
        summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
        "chart",
      );
      return (
        <PercentileLadder
          rows={summary.platforms.flatMap((platform, index) =>
            platform.percentile_stats !== null
              ? [
                  {
                    result_id: platform.result_id,
                    platform: platform.platform,
                    displayLabel: cohortLabels[index] ?? platform.platform,
                    percentile_stats: platform.percentile_stats,
                    colorIdx: platform.color_slot ?? index,
                  },
                ]
              : [],
          )}
        />
      );
    }
    case "cdf_chart":
      return <CDFChart summary={summary} colorIndexFor={options.colorIndexFor} />;
    case "query_histogram":
      return <QueryHistogram summary={summary} />;
    case "stacked_phase":
      return <StackedPhase summary={summary} />;
    case "time_series":
      return (
        <TimeSeries
          entries={historical}
          primaryMetric={normalizePrimaryMetric(summary.ranking?.primary_metric)}
        />
      );
    case "rank_table":
      return <RankTable summary={summary} />;
    case "cost_scatter":
      return <CostScatter summary={summary} />;
    default:
      return null;
  }
}

function ChartThumbnail({
  chartId,
  summary,
  historical,
  colorIndexFor,
}: {
  chartId: string;
  summary: BenchmarkSummary;
  historical: ChartHistoricalEntry[];
  colorIndexFor?: (platform: PlatformRow) => number;
}) {
  switch (chartId) {
    case "query_heatmap":
      return <MiniHeatmap summary={summary} />;
    case "percentile_ladder":
      return <MiniPercentiles summary={summary} />;
    case "cdf_chart":
      return <MiniCDF summary={summary} colorIndexFor={colorIndexFor} />;
    case "query_histogram":
      return <MiniHistogram summary={summary} />;
    case "stacked_phase":
      return <MiniPhases summary={summary} />;
    case "time_series":
      return <MiniTrend summary={summary} historical={historical} />;
    case "rank_table":
      return <MiniRanks summary={summary} />;
    case "cost_scatter":
      return <MiniCost summary={summary} />;
    default:
      return <MiniUnavailable label="Preview unavailable" />;
  }
}

const THUMBNAIL_FRAME_HEIGHT_CLASS = "h-[7.5rem]";

function MiniFrame({ children }: { children: ComponentChildren }) {
  return (
    <div
      class={`summary-chart-thumbnail mt-4 flex ${THUMBNAIL_FRAME_HEIGHT_CLASS} items-center justify-center overflow-hidden rounded-md bg-[var(--bb-surface-data-muted)] p-2`}
    >
      {children}
    </div>
  );
}

function MiniUnavailable({ label }: { label: string }) {
  return (
    <MiniFrame>
      <EmptyState compact title={label} class="w-full text-xs" />
    </MiniFrame>
  );
}

function MiniHeatmap({ summary }: { summary: BenchmarkSummary }) {
  const queryIds = summary.query_ids.slice(0, 14);
  const platforms = summary.platforms.slice(0, 5);
  const hasTiming = platforms.some((platform) =>
    queryIds.some((queryId) => isValidTimingValue(platformTimingValue(platform, queryId))),
  );
  if (!hasTiming) return <MiniUnavailable label="No query timings recorded" />;
  const minima = new Map(
    queryIds.map((queryId) => {
      const values = platforms.map((platform) => platformTimingValue(platform, queryId)).filter(isValidTimingValue);
      return [queryId, values.length > 0 ? Math.min(...values) : null];
    }),
  );
  return (
    <MiniFrame>
      <div
        class="summary-mini-heatmap grid h-full w-full gap-px"
        style={{
          gridTemplateColumns: `repeat(${queryIds.length}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${platforms.length}, minmax(0, 1fr))`,
        }}
        role="img"
        aria-label="Heatmap thumbnail"
      >
        {platforms.flatMap((platform) =>
          queryIds.map((queryId) => {
            const lightness = lightnessForCell(platformTimingValue(platform, queryId), minima.get(queryId) ?? null);
            const cellStyle = lightness === null
              ? undefined
              : ({ "--cell-lightness": lightness } as JSX.CSSProperties);
            return (
              <span
                key={`${platform.result_id}-${queryId}`}
                class={`rounded-[1px] ${lightness === null ? "bg-[var(--bb-surface-data-muted)]" : "heatmap-cell"}`}
                style={cellStyle}
                title={`${platform.platform} · ${queryId}`}
              />
            );
          }),
        )}
      </div>
    </MiniFrame>
  );
}

function MiniPercentiles({ summary }: { summary: BenchmarkSummary }) {
  const cohortLabels = formatRunIdentitiesForCohort(
    summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
    "chart",
  );
  const rows = summary.platforms.flatMap((platform, index) =>
    isTimingDisplayable(platform) && platform.percentile_stats !== null
      ? [{ result_id: platform.result_id, label: cohortLabels[index] ?? platform.platform, stats: platform.percentile_stats, colorIdx: platform.color_slot ?? index }]
      : [],
  ).slice(0, 6);
  if (rows.length === 0) return <MiniUnavailable label="No percentile statistics recorded" />;
  const max = Math.max(...rows.map((row) => row.stats.p99), 1);
  return (
    <MiniFrame>
      <div class="summary-mini-stack w-full space-y-1.5">
        {rows.map((row) => {
          const fitted = fitLabel(row.label, THUMBNAIL_LABEL_WIDTH_PX, THUMBNAIL_FONT_PX);
          return (
            <div key={row.result_id} class="flex items-center gap-1.5">
              <span
                class="w-24 shrink-0 truncate text-[10px] leading-none text-[var(--bb-data-fg-subtle)]"
                title={row.label}
              >
                {fitted.text}
              </span>
              <span class="relative h-2 flex-1 rounded-sm bg-[var(--bb-data-border)]">
                <span
                  class="absolute inset-y-0 left-0 rounded-sm opacity-40"
                  style={{ width: `${(row.stats.p99 / max) * 100}%`, backgroundColor: paletteColor(row.colorIdx) }}
                />
                <span
                  class="absolute inset-y-0 left-0 rounded-sm"
                  style={{ width: `${(row.stats.p50 / max) * 100}%`, backgroundColor: paletteColor(row.colorIdx) }}
                />
              </span>
            </div>
          );
        })}
      </div>
    </MiniFrame>
  );
}

function MiniCDF({
  summary,
  colorIndexFor,
}: {
  summary: BenchmarkSummary;
  colorIndexFor?: (platform: PlatformRow) => number;
}) {
  const series = summary.platforms.slice(0, 6).map((platform, index) => ({
    color: paletteColor(colorIndexFor?.(platform) ?? platform.color_slot ?? index),
    points: computeECDFPoints(summary.query_ids.map((queryId) => platformTimingValue(platform, queryId))),
  })).filter((entry) => entry.points.length > 0);
  const allMs = series.flatMap((entry) => entry.points.map((point) => point.x));
  const scale = buildLogLatencyScale(allMs, { lowerPad: 0.2, upperPad: 0.2 });
  if (scale === null) return <MiniUnavailable label="No valid display timings" />;
  const width = 240;
  const height = 86;
  return (
    <MiniFrame>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        class="summary-mini-plot h-[5.5rem] w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label="CDF thumbnail"
      >
        <line x1="8" y1="76" x2="232" y2="76" stroke="var(--bb-chart-grid)" />
        <line x1="8" y1="8" x2="8" y2="76" stroke="var(--bb-chart-grid)" />
        {series.map((entry, index) => {
          const d = entry.points.map((point, pointIndex) => {
            const x = 8 + logLatencyFraction(point.x, scale) * 224;
            const y = 76 - (point.y / 100) * 68;
            return pointIndex === 0 ? `M${x.toFixed(1)},${y.toFixed(1)}` : `H${x.toFixed(1)} V${y.toFixed(1)}`;
          }).join(" ");
          return <path key={index} d={d} fill="none" stroke={entry.color} stroke-width="1.5" />;
        })}
      </svg>
    </MiniFrame>
  );
}

function MiniHistogram({ summary }: { summary: BenchmarkSummary }) {
  const values = summary.query_ids.slice(0, 20).map((queryId) => {
    const timings = summary.platforms
      .map((platform) => platformTimingValue(platform, queryId))
      .filter(isValidTimingValue);
    return timings.length > 0 ? timings.reduce((total, value) => total + value, 0) / timings.length : null;
  }).filter(isValidTimingValue);
  if (values.length === 0) return <MiniUnavailable label="No valid query timings" />;
  const max = Math.max(...values, 1);
  return (
    <MiniFrame>
      <div class="summary-mini-plot flex h-[5.5rem] w-full items-end gap-1 border-b border-l border-[var(--bb-data-border)] px-2 pb-1">
        {values.map((value, index) => (
          <span
            key={index}
            class="min-w-0 flex-1 rounded-t-sm bg-[var(--bb-chart-cat-1)]"
            style={{ height: `${Math.max(4, (value / max) * 100)}%` }}
          />
        ))}
      </div>
    </MiniFrame>
  );
}

function MiniPhases({ summary }: { summary: BenchmarkSummary }) {
  const rows = summary.platforms
    .filter((platform) => platform.phase_durations && Object.keys(platform.phase_durations).length > 0)
    .slice(0, 6);
  if (rows.length === 0) return <MiniUnavailable label="No phase breakdown recorded" />;
  const max = Math.max(
    ...rows.map((row) => Object.values(row.phase_durations!).reduce((total, value) => total + value, 0)),
    1,
  );
  return (
    <MiniFrame>
      <div class="summary-mini-stack w-full space-y-1.5">
        {rows.map((row) => {
          const phases = Object.entries(row.phase_durations!);
          const total = phases.reduce((sum, [, value]) => sum + value, 0);
          return (
            <div
              key={row.result_id}
              class="flex h-2 overflow-hidden rounded-sm bg-[var(--bb-data-border)]"
              style={{ width: `${Math.max(12, (total / max) * 100)}%` }}
            >
              {phases.map(([phase, value]) => (
                <span
                  key={phase}
                  style={{
                    width: `${(value / total) * 100}%`,
                    backgroundColor: PHASE_COLORS[phase] ?? "var(--bb-chart-axis)",
                  }}
                />
              ))}
            </div>
          );
        })}
      </div>
    </MiniFrame>
  );
}

function MiniTrend({ summary, historical }: { summary: BenchmarkSummary; historical: ChartHistoricalEntry[] }) {
  const metric = normalizePrimaryMetric(summary.ranking?.primary_metric);
  const byPlatform = new Map<string, ChartHistoricalEntry[]>();
  historical.forEach((entry) => {
    const group = byPlatform.get(entry.platform_id) ?? [];
    group.push(entry);
    byPlatform.set(entry.platform_id, group);
  });
  const series = Array.from(byPlatform.values())
    .slice(0, 6)
    .map((entries) =>
      entries
        .filter((entry) => isValidTimingValue(trendEntryValue(entry, metric)))
        .sort((a, b) => a.run_date.localeCompare(b.run_date)),
    )
    .filter((points) => points.length >= 2);
  if (series.length === 0) return <MiniUnavailable label="Not enough history for a trend" />;
  const slotByPlatformId = assignRunColorSlots(
    series.map((points) => ({ result_id: points[0]!.platform_id, platform_id: points[0]!.platform_id, platform: points[0]!.platform })),
  );
  const values = series.flat().map((entry) => trendEntryValue(entry, metric) as number);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return (
    <MiniFrame>
      <svg
        viewBox="0 0 240 86"
        class="summary-mini-plot h-[5.5rem] w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label="Trend thumbnail"
      >
        <line x1="8" y1="76" x2="232" y2="76" stroke="var(--bb-chart-grid)" />
        {series.map((points, index) => {
          const d = points.map((entry, pointIndex) => {
            const x = 12 + (pointIndex / Math.max(points.length - 1, 1)) * 216;
            const normalized = ((trendEntryValue(entry, metric) as number) - min) / span;
            const y = primaryMetricHigherIsBetter(metric) ? 10 + (1 - normalized) * 62 : 10 + normalized * 62;
            return `${pointIndex === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
          }).join(" ");
          const slot = slotByPlatformId.get(points[0]!.platform_id) ?? index;
          return <path key={index} d={d} fill="none" stroke={paletteColor(slot)} stroke-width="1.5" />;
        })}
      </svg>
    </MiniFrame>
  );
}

function trendEntryValue(entry: ChartHistoricalEntry, metric: ReturnType<typeof normalizePrimaryMetric>): number | null {
  if (metric === "power_score") return entry.power_score;
  if (metric === "throughput_at_size") return entry.throughput_at_size ?? null;
  return entry.display_geomean_ms;
}

function MiniRanks({ summary }: { summary: BenchmarkSummary }) {
  const queryIds = summary.query_ids.slice(0, 8);
  const hasTiming = summary.platforms.some((platform) =>
    queryIds.some((queryId) => isValidTimingValue(platformTimingValue(platform, queryId))),
  );
  if (!hasTiming) {
    return <MiniUnavailable label="No rank-safe query timings" />;
  }
  const ranks = computeRankTable(
    queryIds,
    summary.platforms.map((platform) =>
      Object.fromEntries(queryIds.map((queryId) => [queryId, platformTimingValue(platform, queryId)])),
    ),
  );
  return (
    <MiniFrame>
      <div
        class="summary-mini-grid grid h-full w-full gap-px"
        style={{
          gridTemplateColumns: `repeat(${summary.platforms.length}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${queryIds.length}, minmax(0, 1fr))`,
        }}
        role="img"
        aria-label="Rank table thumbnail"
      >
        {queryIds.flatMap((queryId) =>
          summary.platforms.map((platform, index) => {
            const rank = ranks[queryId]?.[index];
            return (
              <span
                key={`${queryId}-${platform.result_id}`}
                class="flex items-center justify-center rounded-[1px] font-mono text-[10px] leading-none"
                style={{
                  backgroundColor: rank === 1 ? "var(--bb-chart-cat-1)" : "var(--bb-tone-neutral-bg)",
                  color: rank === 1 ? "var(--bb-surface-data)" : "var(--bb-data-fg-muted)",
                }}
              >
                {rank ?? "—"}
              </span>
            );
          }),
        )}
      </div>
    </MiniFrame>
  );
}

function MiniCost({ summary }: { summary: BenchmarkSummary }) {
  const metric = normalizePrimaryMetric(summary.ranking?.primary_metric);
  const points = summary.platforms.filter(
    (platform) =>
      Number.isFinite(platform.normalized_cost_usd) &&
      platform.normalized_cost_usd !== null &&
      validPrimaryMetricValue(platform, metric) !== null,
  );
  if (points.length < 2) return <MiniUnavailable label="No normalized cost recorded" />;
  const costs = points.map((point) => point.normalized_cost_usd as number);
  const metricValues = points.map((point) => validPrimaryMetricValue(point, metric) as number);
  const minCost = Math.min(...costs);
  const maxCost = Math.max(...costs);
  const minMetric = Math.min(...metricValues);
  const maxMetric = Math.max(...metricValues);
  const higherIsBetter = primaryMetricHigherIsBetter(metric);
  return (
    <MiniFrame>
      <div class="summary-mini-plot relative h-[5.5rem] w-full border-b border-l border-[var(--bb-data-border)]">
        {points.map((point, index) => {
          const x = ((point.normalized_cost_usd as number) - minCost) / (maxCost - minCost || 1);
          const normalized = ((validPrimaryMetricValue(point, metric) as number) - minMetric) /
            (maxMetric - minMetric || 1);
          const y = higherIsBetter ? 1 - normalized : normalized;
          return (
            <span
              key={point.result_id}
              class="absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
              style={{
                left: `${8 + x * 90}%`,
                top: `${8 + y * 84}%`,
                backgroundColor: paletteColor(point.color_slot ?? index),
              }}
              title={point.platform}
            />
          );
        })}
      </div>
    </MiniFrame>
  );
}
