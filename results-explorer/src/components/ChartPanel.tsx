import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { ComponentChildren, JSX } from "preact";
import type { BenchmarkSummary, PlatformRow, RankingConfig } from "@/types";
import {
  applicableCharts,
  buildRenderableSummary,
  groupChartsByQuestion,
  type ChartContext,
  type ChartHistoricalEntry,
  type ChartRegistryEntry,
} from "@/lib/chartRegistry";
import { useElementSize } from "@/lib/useElementSize";
import { stringSerde, useUrlState } from "@/lib/useUrlState";
import { axisLabelAnchor, barRowLayout, chartFrame } from "@/lib/chartFrame";
import { PowerBar } from "@/components/PowerBar";
import { DistributionBox } from "@/components/DistributionBox";
import { QueryHeatmap } from "@/components/QueryHeatmap";
import { QueryHistogram } from "@/components/QueryHistogram";
import { CostScatter } from "@/components/CostScatter";
import { TimeSeries } from "@/components/TimeSeries";
import { GroupedQueryChart } from "@/components/QueryTimingChart";
import { NormalizedSpeedupChart, type SpeedupRunInput } from "@/components/NormalizedSpeedupChart";
import { DivergingBarChart } from "@/components/DivergingBarChart";
import { SegmentedControl } from "@/components/SegmentedControl";
import { PercentileLadder } from "@/components/PercentileLadder";
import { StackedPhase } from "@/components/StackedPhase";
import { SparklineTable } from "@/components/SparklineTable";
import { CDFChart } from "@/components/CDFChart";
import { RankTable } from "@/components/RankTable";
import { runColorIndexFor, SummaryChartOverview, SummaryMetricTable } from "@/components/SummaryChartOverview";
import { fmtGeomean, fmtScore } from "@/utils";
import { paletteColor } from "@/lib/chartTheme";
import { RunDateChip } from "@/components/RunAge";
import { MISSING_VALUE_GLYPH } from "@/lib/metricFormatters";
import {
  formatRunIdentitiesForCohort,
  formatRunIdentityLabelsForCohort,
  preserveUniqueAfterTruncation,
} from "@/lib/runIdentity";
import {
  buildLatencyBarScale,
  geomeanMs,
  latencyScaleFraction,
  latencyScaleTicks,
} from "@/lib/chartMath";
import {
  chartDatasetClassLabel,
  chartDatasetEmptyTitle,
  filterSummaryForChartDataset,
  isRankable,
  isTimingDisplayable,
  isValidTimingValue,
  normalizePrimaryMetric,
  platformTimingValue,
  primaryMetricLabel,
  primaryMetricValue,
  summarizeChartDatasetExclusions,
  validPrimaryMetricValue,
  type ChartDatasetEligibilityClass,
} from "@/lib/displayEligibility";

interface ChartPanelProps {
  context: ChartContext;
  summaryLayout?: "tabs" | "long";
  baselineIndex?: number;
  onBaselineIndexChange?: (baselineIndex: number) => void;
  suppressWinnerClaims?: boolean;
  suppressionReason?: string;
  excludeChartIds?: readonly string[];
  queryFilter?: readonly string[];
  cardAnchors?: Readonly<Record<string, string>>;
  forceOpenChartId?: string | null;
  focusChartId?: string | null;
  queryHref?: (queryId: string) => string;
  rankGateReason?: string | null;
  rankGateContext?: { benchmark: string; scaleFactor: string; phase: string };
}

const CHART_URL_KEY = "chart";

interface CompareQueryRow {
  queryId: string;
  timings: ({ ms: number; status: "pass" } | null)[];
}

interface ValueLabelPlacement {
  x: number;
  textAnchor: "start" | "end";
  fill: string;
  placement: "outside" | "inside" | "gutter";
}

export function ChartPanel(props: ChartPanelProps) {
  if (props.summaryLayout === "long") {
    if (props.context.kind === "summary") {
      if (props.context.summary !== null) {
        return (
          <SummaryChartOverview
            context={props.context}
            excludeChartIds={props.excludeChartIds}
            cardAnchors={props.cardAnchors}
            forceOpenChartId={props.forceOpenChartId}
            rankGateReason={props.rankGateReason}
            rankGateContext={props.rankGateContext}
          />
        );
      }
      return <ChartPanelTabs {...props} />;
    }
    return <ChartPanelLong {...props} />;
  }
  return <ChartPanelTabs {...props} />;
}

function selectPanelCharts(
  context: ChartContext,
  excludeChartIds: readonly string[] | undefined,
  queryFilter: readonly string[] | undefined,
): ChartRegistryEntry[] {
  const applicable = applicableCharts(context).filter((entry) => entry.foldedInto === undefined);
  const exclude = new Set(excludeChartIds ?? []);
  if (queryFilter) {
    exclude.add("cost_scatter");
    exclude.add("power_bar");
  }
  if (exclude.size === 0) return [...applicable];
  return applicable.filter((entry) => !exclude.has(entry.id));
}

function buildChartSummary(
  summary: BenchmarkSummary | null,
  eligibilityClass: ChartDatasetEligibilityClass,
  queryFilter: readonly string[] | undefined,
): BenchmarkSummary | null {
  if (!summary) return null;
  const base = filterSummaryForChartDataset(summary, eligibilityClass);
  if (!queryFilter) return base;
  const filteredQueryIds = queryFilter.filter((q) => base.query_ids.includes(q));
  const sharedValidQueryIds = filteredQueryIds.filter((q) =>
    base.platforms.every((p) => {
      const v = p.timings[q];
      return v !== null && v !== undefined && Number.isFinite(v) && v > 0;
    }),
  );
  const filteredPlatforms = base.platforms.map((platform) => {
    const filteredTimings: Record<string, number | null> = {};
    for (const q of filteredQueryIds) {
      if (q in platform.timings) {
        filteredTimings[q] = platform.timings[q] ?? null;
      }
    }
    const geomean =
      sharedValidQueryIds.length >= 2
        ? geomeanMs(sharedValidQueryIds.map((q) => platform.timings[q]!))
        : null;
    return {
      ...platform,
      timings: filteredTimings,
      display_geomean_ms: geomean,
      sample_geomean_ms: geomean,
      power_score: null,
      throughput_at_size: null,
      normalized_cost_usd: null,
      cost_status: "unavailable" as const,
    };
  });
  const ranking: RankingConfig | null =
    queryFilter && base.ranking && normalizePrimaryMetric(base.ranking.primary_metric) !== "display_geomean_ms"
      ? {
          primary_metric: "display_geomean_ms",
          secondary_metric: base.ranking.secondary_metric,
          primary_order: "asc",
        }
      : base.ranking;
  return {
    ...base,
    ranking,
    query_ids: filteredQueryIds,
    platforms: filteredPlatforms,
  };
}

function chartSummaryLabelList(chartSummary: BenchmarkSummary | null): string[] {
  if (!chartSummary) return [];
  return formatRunIdentitiesForCohort(
    chartSummary.platforms.map((platform) => ({ ...platform, scale_factor: chartSummary.scale_factor })),
    "chart",
  );
}

function ChartPanelTabs({
  context,
  baselineIndex,
  onBaselineIndexChange,
  suppressWinnerClaims = false,
  suppressionReason,
  excludeChartIds,
  queryFilter,
  queryHref,
}: ChartPanelProps) {
  const charts = useMemo(
    () => selectPanelCharts(context, excludeChartIds, queryFilter),
    [context, excludeChartIds, queryFilter],
  );
  const chartGroups = useMemo(() => groupChartsByQuestion(charts), [charts]);
  const summary = useMemo(() => buildRenderableSummary(context), [context]);
  const colorIndexFor = useMemo(() => runColorIndexFor(summary), [summary]);
  const historical = useMemo(
    () =>
      context.kind === "summary"
        ? context.historical ?? []
        : context.kind === "detail"
          ? context.historical ?? []
          : [],
    [context],
  );
  const summaryPlatformSelectLabels = useMemo(
    () =>
      summary
        ? formatRunIdentitiesForCohort(
            summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
            "selectOption",
          )
        : [],
    [summary],
  );
  const preferredId = useMemo(() => preferredChartId(context, charts), [context, charts]);
  const [activeId, setActiveId] = useUrlState<string>(CHART_URL_KEY, preferredId, stringSerde);
  const [localBaselineIdx, setLocalBaselineIdx] = useState(0);
  const groupTabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const isBaselineControlled = baselineIndex !== undefined;
  const baselineIdx = normalizeBaselineIndex(
    summary?.platforms.length ?? 0,
    isBaselineControlled ? baselineIndex : localBaselineIdx,
  );
  const setBaselineIdx = onBaselineIndexChange ?? setLocalBaselineIdx;

  useEffect(() => {
    if (!charts.some((chart) => chart.id === activeId)) {
      setActiveId(preferredId);
    }
  }, [activeId, charts, preferredId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!charts.some((chart) => chart.id === activeId)) return;
    const inUrl = new URLSearchParams(window.location.search).get(CHART_URL_KEY);
    const expected = activeId === preferredId ? null : activeId;
    if (inUrl !== expected) setActiveId(activeId);
  }, [activeId, charts, preferredId, setActiveId]);

  useEffect(() => {
    if (!isBaselineControlled) setLocalBaselineIdx(0);
  }, [context, isBaselineControlled]);

  const activeChart = charts.find((chart) => chart.id === activeId) ?? charts[0] ?? null;
  const activeGroup =
    activeChart !== null
      ? (chartGroups.find((group) => group.charts.some((chart) => chart.id === activeChart.id)) ??
        chartGroups[0] ??
        null)
      : null;
  const activeGroupCharts = activeGroup?.charts ?? [];
  const activeEligibilityClass = activeChart?.eligibilityClass ?? "provenance_only";
  const showBaseline =
    !isBaselineControlled &&
    context.kind === "compare" &&
    (activeChart?.id === "normalized_speedup" || activeChart?.id === "diverging_bar");
  const chartSummary = useMemo(
    () => buildChartSummary(summary, activeEligibilityClass, queryFilter),
    [activeEligibilityClass, summary, queryFilter],
  );
  const chartPlatformLabels = useMemo(() => chartSummaryLabelList(chartSummary), [chartSummary]);
  const compareRows = useMemo(() => {
    if (context.kind !== "compare" || !chartSummary) return [];
    return chartSummary.query_ids.map((queryId) => ({
      queryId,
      timings: chartSummary.platforms.map((platform) => {
        const ms = platformTimingValue(platform, queryId);
        return ms !== null ? { ms, status: "pass" as const } : null;
      }),
    }));
  }, [chartSummary, context]);
  const compareGroups = useMemo(() => {
    if (context.kind !== "compare" || !chartSummary) return [];
    return chartSummary.query_ids.map((queryId) => ({
      queryId,
      values: chartSummary.platforms.map((platform, index) => {
        const timing = platformTimingValue(platform, queryId);
        return {
          label: chartPlatformLabels[index] ?? platform.platform,
          value: timing ?? null,
          color: paletteColor(platform.color_slot ?? index),
        };
      }),
    }));
  }, [chartPlatformLabels, chartSummary, context]);
  const chartExclusionSummary = useMemo(
    () => (summary ? summarizeChartDatasetExclusions(summary.platforms, activeEligibilityClass) : []),
    [activeEligibilityClass, summary],
  );
  const chartDatasetEmpty = shouldShowChartDatasetEmpty(activeEligibilityClass, summary, chartSummary);

  if (activeChart === null || activeGroup === null) return null;

  const selectGroup = (group: (typeof chartGroups)[number]) => {
    const nextChart =
      group.charts.find((chart) => chart.id === activeId) ??
      group.charts.find((chart) => chart.id === preferredId) ??
      group.charts[0];
    if (nextChart) setActiveId(nextChart.id);
  };

  const selectGroupFromKey = (
    event: JSX.TargetedKeyboardEvent<HTMLButtonElement>,
    currentIndex: number,
  ) => {
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (currentIndex + 1) % chartGroups.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (currentIndex - 1 + chartGroups.length) % chartGroups.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = chartGroups.length - 1;
    else return;
    event.preventDefault();
    const group = chartGroups[nextIndex];
    if (!group) return;
    selectGroup(group);
    groupTabRefs.current[group.id]?.focus();
  };

  return (
    <section class="card">
      <div class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 class="text-base font-semibold text-[var(--bb-data-fg-primary)]">Charts</h2>
        {chartGroups.length > 1 && (
          <div
            class="flex flex-wrap items-center gap-1 rounded-md panel-muted p-1"
            role="tablist"
            aria-label="Chart question groups"
          >
            {chartGroups.map((group, groupIndex) => {
              const selected = group.id === activeGroup.id;
              const tabId = `chart-question-${group.id}`;
              return (
                <button
                  key={group.id}
                  role="tab"
                  id={tabId}
                  ref={(node) => { groupTabRefs.current[group.id] = node; }}
                  tabIndex={selected ? 0 : -1}
                  type="button"
                  aria-selected={selected}
                  aria-controls="chart-panel-chart"
                  aria-label={group.label}
                  class={`min-h-9 rounded px-2 py-1 text-xs font-medium transition-colors sm:px-3 ${
                    selected
                      ? "bg-[var(--bb-surface-data)] text-[var(--bb-data-fg-primary)] shadow-sm"
                      : "text-[var(--bb-data-fg-muted)] hover:bg-[var(--bb-surface-data)] hover:text-[var(--bb-data-fg-primary)]"
                  }`}
                  onClick={() => selectGroup(group)}
                  onKeyDown={(event) => selectGroupFromKey(event, groupIndex)}
                  title={group.description}
                >
                  {group.label}
                </button>
              );
            })}
          </div>
        )}
        {activeGroupCharts.length > 1 && (
          <div
            class="flex flex-wrap items-center gap-1 rounded-md panel-muted p-1"
            role="group"
            aria-label={`${activeGroup.label} charts`}
          >
            {activeGroupCharts.map((chart) => {
              const selected = activeChart.id === chart.id;
              return (
                <button
                  key={chart.id}
                  type="button"
                  class={`min-h-9 rounded px-2 py-1 text-xs font-medium transition-colors sm:px-3 ${
                    selected
                      ? "bg-[var(--bb-surface-data)] text-[var(--bb-data-fg-primary)] shadow-sm"
                      : "text-[var(--bb-data-fg-muted)] hover:bg-[var(--bb-surface-data)] hover:text-[var(--bb-data-fg-primary)]"
                  }`}
                  onClick={() => setActiveId(chart.id)}
                  aria-pressed={selected}
                  aria-label={chart.title}
                  title={chart.description}
                >
                  {chartButtonLabel(chart)}
                </button>
              );
            })}
          </div>
        )}
        {showBaseline && summary && summary.platforms.length > 1 && (
          <div class="ml-auto flex items-center gap-2">
            <label class="text-xs text-[var(--bb-data-fg-muted)]" for="chart-panel-baseline">
              Baseline:
            </label>
            <select
              id="chart-panel-baseline"
              class="min-w-0 max-w-full rounded border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-2 py-1 text-xs text-[var(--bb-data-fg-primary)]"
              value={String(baselineIdx)}
              onChange={(event) => setBaselineIdx(Number((event.target as HTMLSelectElement).value))}
            >
              {summary.platforms.map((platform, index) => (
                <option key={platform.result_id} value={String(index)}>
                  {summaryPlatformSelectLabels[index] ?? platform.platform}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div
        id="chart-panel-chart"
        role="tabpanel"
        aria-labelledby={chartGroups.length > 1 ? `chart-question-${activeGroup.id}` : undefined}
        aria-label={chartGroups.length > 1 ? undefined : `${activeGroup.label} chart`}
        data-chart-container
      >
        {chartDatasetEmpty ? (
          <ChartDatasetEmptyState chart={activeChart} summary={summary!} />
        ) : queryFilter && chartSummary && chartSummary.query_ids.length === 0 ? (
          <div class="panel-muted rounded p-6 text-center text-sm text-[var(--bb-data-fg-muted)]">
            No queries match the selected filter.
          </div>
        ) : (
          <>
            {renderChart(activeChart, {
              context,
              summary: chartSummary,
              colorIndexFor,
              historical,
              compareRows,
              compareGroups,
              baselineIdx,
              platformLabels: chartPlatformLabels,
              suppressWinnerClaims:
                suppressWinnerClaims || Boolean(queryFilter && (chartSummary?.query_ids.length ?? 0) < 2),
              suppressionReason:
                suppressionReason ??
                (Boolean(queryFilter && (chartSummary?.query_ids.length ?? 0) < 2)
                  ? "No winner is named when fewer than two usable queries are selected"
                  : undefined),
              queryFilter,
              queryHref,
            })}
            {summary && chartSummary && chartSummary.platforms.length > 0 && chartExclusionSummary.length > 0 && (
              <ChartDatasetExclusionSummary
                eligibilityClass={activeChart.eligibilityClass}
                originalCount={summary.platforms.length}
                renderedCount={chartSummary.platforms.length}
                reasons={chartExclusionSummary}
              />
            )}
          </>
        )}
      </div>
    </section>
  );
}

const PER_QUERY_VIEW_URL_KEY = "per_query_view";

function PerQueryComparison({
  queries,
  results,
  baselineIdx,
  queryHref,
}: {
  queries: { queryId: string; timings: ({ ms: number; status: "pass" } | null)[] }[];
  results: readonly SpeedupRunInput[];
  baselineIdx: number;
  queryHref?: (queryId: string) => string;
}) {
  const [storedView, setStoredView] = useUrlState<string>(PER_QUERY_VIEW_URL_KEY, "speedup", stringSerde);
  const view = storedView === "difference" ? "difference" : "speedup";

  if (queries.length === 0) {
    return (
      <div class="panel-muted rounded p-6 text-center text-sm text-[var(--bb-data-fg-muted)]">
        No queries match the selected filter.
      </div>
    );
  }

  return (
    <div class="space-y-3">
      <SegmentedControl
        ariaLabel="Chart view"
        size="sm"
        value={view}
        onChange={(next) => setStoredView(next)}
        options={[
          { value: "speedup", label: "Speedup", title: "Baseline latency divided by run latency, on a log scale" },
          {
            value: "difference",
            label: "Difference (%)",
            title: "Percentage change from the baseline, centred on zero",
          },
        ]}
      />
      {view === "difference" ? (
        <DivergingBarChart queries={queries} results={results} baselineIdx={baselineIdx} />
      ) : (
        <NormalizedSpeedupChart queries={queries} results={results} baselineIdx={baselineIdx} queryHref={queryHref} />
      )}
    </div>
  );
}

function chartButtonLabel(chart: ChartRegistryEntry): string {
  return chart.shortTitle;
}

function ChartPanelLong({
  context,
  baselineIndex,
  onBaselineIndexChange,
  suppressWinnerClaims = false,
  suppressionReason,
  excludeChartIds,
  queryFilter,
  queryHref,
  focusChartId,
}: ChartPanelProps) {
  const panelRef = useRef<HTMLElement>(null);
  const charts = useMemo(
    () => selectPanelCharts(context, excludeChartIds, queryFilter),
    [context, excludeChartIds, queryFilter],
  );
  const chartGroups = useMemo(() => {
    const groups = groupChartsByQuestion(charts);
    if (context.kind !== "compare") return groups;
    const compareOrder = ["overview", "distribution", "per_query", "cost", "trend", "rank"] as const;
    return [...groups].sort((left, right) => compareOrder.indexOf(left.id) - compareOrder.indexOf(right.id));
  }, [charts, context.kind]);
  const summary = useMemo(() => buildRenderableSummary(context), [context]);
  const historical = useMemo(
    () =>
      context.kind === "summary"
        ? context.historical ?? []
        : context.kind === "detail"
          ? context.historical ?? []
          : [],
    [context],
  );
  const summaryPlatformSelectLabels = useMemo(
    () =>
      summary
        ? formatRunIdentitiesForCohort(
            summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
            "selectOption",
          )
        : [],
    [summary],
  );
  const isBaselineControlled = baselineIndex !== undefined;
  const [localBaselineIdx, setLocalBaselineIdx] = useState(0);
  const baselineIdx = normalizeBaselineIndex(
    summary?.platforms.length ?? 0,
    isBaselineControlled ? baselineIndex : localBaselineIdx,
  );
  const setBaselineIdx = onBaselineIndexChange ?? setLocalBaselineIdx;

  useEffect(() => {
    if (!isBaselineControlled) setLocalBaselineIdx(0);
  }, [context, isBaselineControlled]);

  const focusTargetPresent = focusChartId ? charts.some((chart) => chart.id === focusChartId) : false;
  useEffect(() => {
    if (!focusChartId || !focusTargetPresent) return;
    const target = panelRef.current?.querySelector<HTMLElement>(`[data-chart-id="${focusChartId}"]`);
    if (!target) return;
    if (typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "start" });
    target.focus({ preventScroll: true });
  }, [focusChartId, focusTargetPresent]);

  if (charts.length === 0) return null;

  const showBaseline =
    !isBaselineControlled &&
    context.kind === "compare" &&
    charts.some((chart) => chart.id === "normalized_speedup" || chart.id === "diverging_bar");

  return (
    <section ref={panelRef} class="card" data-testid="chart-panel-long">
      {context.kind === "compare" && summary && (
        <div class="mb-8" data-testid="chart-panel-compare-summary-table">
          <SummaryMetricTable summary={summary} scope="compare" framed={false} />
        </div>
      )}
      <div class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        {showBaseline && summary && summary.platforms.length > 1 && (
          <div class="ml-auto flex items-center gap-2">
            <label class="text-xs text-[var(--bb-data-fg-muted)]" for="chart-panel-long-baseline">
              Baseline:
            </label>
            <select
              id="chart-panel-long-baseline"
              class="min-w-0 max-w-full rounded border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-2 py-1 text-xs text-[var(--bb-data-fg-primary)]"
              value={String(baselineIdx)}
              onChange={(event) => setBaselineIdx(Number((event.target as HTMLSelectElement).value))}
            >
              {summary.platforms.map((platform, index) => (
                <option key={platform.result_id} value={String(index)}>
                  {summaryPlatformSelectLabels[index] ?? platform.platform}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      <div class="space-y-8">
        {chartGroups.map((group) => (
          <div key={group.id} class="space-y-8" data-testid={`chart-panel-group-${group.id}`}>
            {group.charts.map((chart) => (
              <ChartFigure
                key={chart.id}
                chart={chart}
                context={context}
                summary={summary}
                historical={historical}
                baselineIdx={baselineIdx}
                suppressWinnerClaims={suppressWinnerClaims}
                suppressionReason={suppressionReason}
                queryFilter={queryFilter}
                queryHref={queryHref}
              />
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

function ChartFigure({
  chart,
  context,
  summary,
  historical,
  baselineIdx,
  suppressWinnerClaims = false,
  suppressionReason,
  queryFilter,
  queryHref,
}: {
  chart: ChartRegistryEntry;
  context: ChartContext;
  summary: BenchmarkSummary | null;
  historical: ChartHistoricalEntry[];
  baselineIdx: number;
  suppressWinnerClaims?: boolean;
  suppressionReason?: string;
  queryFilter?: readonly string[];
  queryHref?: (queryId: string) => string;
}) {
  const chartSummary = useMemo(
    () => buildChartSummary(summary, chart.eligibilityClass, queryFilter),
    [summary, chart.eligibilityClass, queryFilter],
  );
  const colorIndexFor = useMemo(() => runColorIndexFor(summary), [summary]);
  const platformLabels = useMemo(() => chartSummaryLabelList(chartSummary), [chartSummary]);
  const compareRows = useMemo(() => {
    if (context.kind !== "compare" || !chartSummary) return [];
    return chartSummary.query_ids.map((queryId) => ({
      queryId,
      timings: chartSummary.platforms.map((platform) => {
        const ms = platformTimingValue(platform, queryId);
        return ms !== null ? { ms, status: "pass" as const } : null;
      }),
    }));
  }, [chartSummary, context]);
  const compareGroups = useMemo(() => {
    if (context.kind !== "compare" || !chartSummary) return [];
    return chartSummary.query_ids.map((queryId) => ({
      queryId,
      values: chartSummary.platforms.map((platform, index) => {
        const timing = platformTimingValue(platform, queryId);
        return {
          label: platformLabels[index] ?? platform.platform,
          value: timing ?? null,
          color: paletteColor(platform.color_slot ?? index),
        };
      }),
    }));
  }, [chartSummary, context, platformLabels]);
  const exclusionReasons = useMemo(
    () => (summary ? summarizeChartDatasetExclusions(summary.platforms, chart.eligibilityClass) : []),
    [summary, chart.eligibilityClass],
  );
  const datasetEmpty = shouldShowChartDatasetEmpty(chart.eligibilityClass, summary, chartSummary);
  const fewUsableQueries = Boolean(queryFilter && (chartSummary?.query_ids.length ?? 0) < 2);

  return (
    <div
      data-chart-container
      data-chart-id={chart.id}
      data-testid={`chart-panel-chart-${chart.id}`}
      tabIndex={-1}
    >
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-base font-semibold text-[var(--bb-data-fg-primary)]">{chart.title}</h3>
      </div>
      <div class="mt-3">
        {datasetEmpty ? (
          <ChartDatasetEmptyState chart={chart} summary={summary!} />
        ) : queryFilter && chartSummary && chartSummary.query_ids.length === 0 ? (
          <div class="panel-muted rounded p-6 text-center text-sm text-[var(--bb-data-fg-muted)]">
            No queries match the selected filter.
          </div>
        ) : (
          <>
            {renderChart(chart, {
              context,
              summary: chartSummary,
              colorIndexFor,
              historical,
              compareRows,
              compareGroups,
              baselineIdx,
              platformLabels,
              suppressWinnerClaims: suppressWinnerClaims || fewUsableQueries,
              suppressionReason:
                suppressionReason ??
                (fewUsableQueries
                  ? "No winner is named when fewer than two usable queries are selected"
                  : undefined),
              queryFilter,
              queryHref,
            })}
            {summary && chartSummary && chartSummary.platforms.length > 0 && exclusionReasons.length > 0 && (
              <ChartDatasetExclusionSummary
                eligibilityClass={chart.eligibilityClass}
                originalCount={summary.platforms.length}
                renderedCount={chartSummary.platforms.length}
                reasons={exclusionReasons}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

function normalizeBaselineIndex(platformCount: number, baselineIndex: number) {
  return baselineIndex >= 0 && baselineIndex < platformCount ? baselineIndex : 0;
}

function shouldShowChartDatasetEmpty(
  eligibilityClass: ChartDatasetEligibilityClass,
  summary: BenchmarkSummary | null,
  chartSummary: BenchmarkSummary | null,
): boolean {
  if (eligibilityClass === "provenance_only" || eligibilityClass === "trend_safe") return false;
  return Boolean(summary && chartSummary && summary.platforms.length > 0 && chartSummary.platforms.length === 0);
}

function ChartDatasetEmptyState({
  chart,
  summary,
  displayTitle,
}: {
  chart: ChartRegistryEntry;
  summary: BenchmarkSummary;
  displayTitle?: string;
}) {
  const reasons = summarizeChartDatasetExclusions(summary.platforms, chart.eligibilityClass);
  return (
    <div
      role="status"
      aria-label={`${displayTitle ?? chart.title} unavailable`}
      class="rounded-md border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-4 py-3 text-sm"
    >
      <p class="font-semibold text-[var(--bb-data-fg-primary)]">{chartDatasetEmptyTitle(chart.eligibilityClass)}</p>
      <p class="mt-1 text-[var(--bb-data-fg-muted)]">
        This chart requires {chartDatasetClassLabel(chart.eligibilityClass)}. Submitted rows remain visible in the
        table or receipt, but they are not plotted as normal chart data.
      </p>
      {reasons.length > 0 && (
        <ul class="mt-2 space-y-1 text-xs text-[var(--bb-data-fg-muted)]">
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
  eligibilityClass: ChartDatasetEligibilityClass;
  originalCount: number;
  renderedCount: number;
  reasons: { reason: string; count: number }[];
}) {
  if (renderedCount >= originalCount) return null;
  const excludedCount = originalCount - renderedCount;
  const topReason = reasons[0]?.reason ?? chartDatasetClassLabel(eligibilityClass);
  return (
    <p class="mt-2 text-[11px] text-[var(--bb-data-fg-subtle)]">
      {excludedCount} {excludedCount === 1 ? "row" : "rows"} excluded from this chart's{" "}
      {chartDatasetClassLabel(eligibilityClass)} dataset: {topReason}
    </p>
  );
}

function preferredChartId(
  context: ChartContext,
  charts: readonly ChartRegistryEntry[],
): string {
  const ids = new Set(charts.map((chart) => chart.id));

  if (context.kind === "compare") {
    if (context.results.length === 2 && ids.has("normalized_speedup")) {
      return "normalized_speedup";
    }
    if (ids.has("comparison_bar")) return "comparison_bar";
  }

  if (context.kind === "detail" && ids.has("query_histogram")) {
    return "query_histogram";
  }

  if (context.kind === "summary" && context.summary === null) {
    if (ids.has("time_series")) return "time_series";
    if (ids.has("summary_box")) return "summary_box";
  }

  if (context.kind === "summary") {
    if (ids.has("performance_bar")) return "performance_bar";
    if (ids.has("sparkline_table")) return "sparkline_table";
  }

  return charts[0]?.id ?? "";
}

function renderChart(
  chart: ChartRegistryEntry,
  {
    context,
    summary,
    colorIndexFor,
    historical,
    compareRows,
    compareGroups,
    baselineIdx,
    suppressWinnerClaims = false,
    suppressionReason,
    queryFilter,
    queryHref,
  }: {
    context: ChartContext;
    summary: BenchmarkSummary | null;
    colorIndexFor: (platform: PlatformRow) => number;
    historical: ChartHistoricalEntry[];
    compareRows: CompareQueryRow[];
    compareGroups: { queryId: string; values: { label: string; value: number | null; color: string }[] }[];
    baselineIdx: number;
    platformLabels: string[];
    suppressWinnerClaims?: boolean;
    suppressionReason?: string;
    queryFilter?: readonly string[];
    queryHref?: (queryId: string) => string;
  },
) {
  switch (chart.id) {
    case "performance_bar":
      return summary ? <PerformanceBar summary={summary} colorIndexFor={colorIndexFor} /> : null;
    case "power_bar":
      return summary ? <PowerBar summary={summary} colorIndexFor={colorIndexFor} /> : null;
    case "distribution_box":
      return summary ? <DistributionBox summary={summary} colorIndexFor={colorIndexFor} /> : null;
    case "query_heatmap":
      return summary ? (
        <QueryHeatmap summary={summary} preserveOrder={Boolean(queryFilter)} />
      ) : null;
    case "query_histogram":
      return summary ? (
        <QueryHistogram summary={summary} preserveOrder={Boolean(queryFilter)} queryHref={queryHref} />
      ) : null;
    case "cost_scatter":
      return summary ? <CostScatter summary={summary} /> : null;
    case "time_series":
      return historical && historical.length > 1 ? (
        <TimeSeries
          entries={historical}
          primaryMetric={normalizePrimaryMetric(summary?.ranking?.primary_metric)}
        />
      ) : (
        <p class="text-sm italic text-[var(--bb-data-fg-subtle)]">
          Not enough historical data for a trend chart.
        </p>
      );
    case "comparison_bar":
      return compareGroups.length > 0 ? (
        <div class="space-y-4">
          {summary && summary.platforms.length > 1 && (() => {
            const legendLabels = formatRunIdentitiesForCohort(
              summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
              "chart",
            );
            return (
              <div class="flex flex-wrap gap-4">
                {summary.platforms.map((platform, index) => (
                  <div key={platform.result_id} class="flex items-center gap-1.5 text-sm text-[var(--bb-data-fg-muted)]">
                    <span
                      class="inline-block h-3 w-3 rounded-sm"
                      style={{ backgroundColor: paletteColor(platform.color_slot ?? index) }}
                    />
                    {legendLabels[index] ?? platform.platform}
                  </div>
                ))}
              </div>
            );
          })()}
          <GroupedQueryChart groups={compareGroups} />
        </div>
      ) : (
        <div class="panel-muted rounded p-6 text-center text-sm text-[var(--bb-data-fg-muted)]">
          No queries match the selected filter.
        </div>
      );
    case "summary_box":
      return (
        <SummaryBoxPanel
          context={context}
          summary={summary}
          historical={historical}
          suppressWinnerClaims={suppressWinnerClaims}
          suppressionReason={suppressionReason}
        />
      );
    case "percentile_ladder":
      if (!summary) return null;
      {
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
    case "normalized_speedup":
      return summary ? (
        <PerQueryComparison
          queries={compareRows}
          results={summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor }))}
          baselineIdx={baselineIdx}
          queryHref={queryHref}
        />
      ) : null;
    case "stacked_phase":
      return summary ? <StackedPhase summary={summary} /> : null;
    case "sparkline_table":
      return summary ? <SparklineTable summary={summary} colorIndexFor={colorIndexFor} /> : null;
    case "cdf_chart":
      return summary ? <CDFChart summary={summary} colorIndexFor={colorIndexFor} /> : null;
    case "rank_table":
      return summary ? (
        <RankTable summary={summary} preserveOrder={Boolean(queryFilter)} />
      ) : null;
    default:
      return null;
  }
}

function PerformanceBar({
  summary,
  colorIndexFor,
}: {
  summary: BenchmarkSummary;
  colorIndexFor: (platform: PlatformRow) => number;
}) {
  const [containerRef, { width: containerWidth }] = useElementSize();
  const frame = chartFrame(containerWidth);
  const width = frame.width;
  const cohortLabels = formatRunIdentityLabelsForCohort(
    summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
  );
  const displayLabels = preserveUniqueAfterTruncation(
    cohortLabels.map((label) => label.disambiguated),
    22,
  );
  const labelByResultId = new Map(
    summary.platforms.map((platform, index) => [platform.result_id, displayLabels[index] ?? platform.platform]),
  );
  const fullLabelByResultId = new Map(
    summary.platforms.map((platform, index) => [platform.result_id, cohortLabels[index]?.full ?? platform.platform]),
  );
  const rows = summary.platforms
    .filter((platform) => isTimingDisplayable(platform) && isValidTimingValue(platform.display_geomean_ms))
    .sort((a, b) => (a.display_geomean_ms ?? Infinity) - (b.display_geomean_ms ?? Infinity))
    .map((platform) => ({
      ...platform,
      color: paletteColor(colorIndexFor(platform)),
      displayLabel: labelByResultId.get(platform.result_id) ?? platform.platform,
      fullLabel: fullLabelByResultId.get(platform.result_id) ?? platform.platform,
    }));

  if (rows.length === 0) {
    return (
      <p class="text-sm italic text-[var(--bb-data-fg-subtle)]">
        Geomean query timings are not available for these results.
      </p>
    );
  }

  const layout = barRowLayout(frame, { labelWidth: 160, rowHeight: 36, valueTrail: 96 });
  const labelWidth = layout.labelWidth;
  const rowHeight = layout.rowHeight;
  const axisHeight = 32;
  const topPadding = 8;
  const plotWidth = layout.plotWidth;
  const totalHeight = topPadding + rows.length * rowHeight + axisHeight;
  const scale = buildLatencyBarScale(rows.map((row) => row.display_geomean_ms));
  if (scale === null) return null;

  const ticks = latencyScaleTicks(scale);
  const scaleLabel = layout.labelAbove
    ? scale.mode === "log"
      ? "Log scale - lower is better"
      : "Median of passing - lower is better"
    : scale.mode === "log"
      ? "Geomean query time (log scale) - lower is better"
      : "Geomean query time (median-of-passing) - lower is better";

  return (
    <div ref={containerRef} class="w-full overflow-x-auto">
      <svg
        class="bb-chart-svg"
        width="100%"
        height={totalHeight}
        viewBox={`0 0 ${width} ${totalHeight}`}
        role="img"
        aria-label={
          scale.mode === "log"
            ? "Geomean performance comparison (log scale)"
            : "Geomean performance comparison"
        }
      >
        {rows.map((row, index) => {
          const fraction = latencyScaleFraction(row.display_geomean_ms, scale) ?? 0;
          const barWidth = fraction * plotWidth;
          const renderedBarWidth = Math.max(2, barWidth);
          const valueLabel = fmtGeomean(row.display_geomean_ms);
          const valueLabelPlacement: ValueLabelPlacement = layout.labelAbove
            ? { x: width, textAnchor: "end", fill: "var(--bb-chart-label)", placement: "outside" }
            : placePerformanceValueLabel({
                barWidth: renderedBarWidth,
                plotWidth,
                labelWidth,
                valueText: valueLabel,
              });
          const y = topPadding + index * rowHeight;
          const midY = y + layout.barCenter;
          const barHeight = 36 * 0.55;
          return (
            <g key={row.result_id}>
              <text
                x={layout.labelAbove ? 0 : labelWidth - 6}
                y={y + layout.labelBaseline}
                text-anchor={layout.labelAbove ? "start" : "end"}
                style={{ fontSize: "11px", fill: "var(--bb-chart-label)" }}
              >
                <title>{row.fullLabel}</title>
                {row.displayLabel}
              </text>
              <rect
                x={layout.plotX}
                y={midY - barHeight / 2}
                width={renderedBarWidth}
                height={barHeight}
                fill={row.color}
                opacity={0.85}
                rx={2}
              >
                <title>{`${row.fullLabel}: ${fmtGeomean(row.display_geomean_ms)}`}</title>
              </rect>
              {valueLabelPlacement.placement === "gutter" && (
                <line
                  x1={layout.plotX + renderedBarWidth + 4}
                  y1={midY}
                  x2={valueLabelPlacement.x - 5}
                  y2={midY}
                  stroke="var(--bb-chart-grid)"
                  stroke-width={1}
                  stroke-dasharray="2 2"
                />
              )}
              <text
                x={valueLabelPlacement.x}
                y={layout.labelAbove ? y + layout.labelBaseline : midY + 4}
                text-anchor={valueLabelPlacement.textAnchor}
                data-value-placement={valueLabelPlacement.placement}
                style={{ fontSize: "11px", fill: valueLabelPlacement.fill }}
              >
                {valueLabel}
              </text>
              {index < rows.length - 1 && (
                <line
                  x1={0}
                  y1={y + rowHeight}
                  x2={width}
                  y2={y + rowHeight}
                  stroke="var(--bb-chart-grid)"
                  stroke-width={1}
                />
              )}
            </g>
          );
        })}

        <g transform={`translate(0, ${topPadding + rows.length * rowHeight})`}>
          <line
            x1={layout.plotX}
            y1={0}
            x2={layout.plotX + plotWidth}
            y2={0}
            stroke="var(--bb-chart-grid)"
            stroke-width={1}
          />
          {ticks.map((value) => {
            const fraction = latencyScaleFraction(value, scale) ?? 0;
            const x = layout.plotX + fraction * plotWidth;
            return (
              <g key={value}>
                <line x1={x} y1={0} x2={x} y2={4} stroke="var(--bb-chart-label-muted)" stroke-width={1} />
                <text
                  x={x}
                  y={16}
                  text-anchor={axisLabelAnchor(x, width)}
                  style={{ fontSize: "10px", fill: "var(--bb-chart-axis)" }}
                >
                  {fmtGeomean(value)}
                </text>
              </g>
            );
          })}
          <text
            x={layout.labelAbove ? 0 : layout.plotX + plotWidth / 2}
            y={axisHeight - 2}
            text-anchor={layout.labelAbove ? "start" : "middle"}
            style={{ fontSize: "10px", fill: "var(--bb-chart-label-muted)" }}
          >
            {scaleLabel}
          </text>
        </g>
      </svg>
    </div>
  );
}

function placePerformanceValueLabel({
  barWidth,
  plotWidth,
  labelWidth,
  valueText,
}: {
  barWidth: number;
  plotWidth: number;
  labelWidth: number;
  valueText: string;
}): ValueLabelPlacement {
  const labelGap = 6;
  const labelPx = valueText.length * 6.5;
  const plotRight = labelWidth + plotWidth;
  const outsideX = labelWidth + barWidth + labelGap;
  const outsideFits = outsideX + labelPx <= plotRight - labelGap;
  if (outsideFits) {
    return {
      x: outsideX,
      textAnchor: "start",
      fill: "var(--bb-chart-label)",
      placement: "outside",
    };
  }

  const insideFits = barWidth >= labelPx + labelGap * 2;
  if (insideFits) {
    return {
      x: labelWidth + barWidth - labelGap,
      textAnchor: "end",
      fill: "var(--bb-chart-tooltip-bg)",
      placement: "inside",
    };
  }

  return {
    x: plotRight + labelGap,
    textAnchor: "start",
    fill: "var(--bb-chart-label)",
    placement: "gutter",
  };
}

function SummaryBoxPanel({
  context,
  summary,
  historical,
  suppressWinnerClaims = false,
  suppressionReason,
}: {
  context: ChartContext;
  summary: BenchmarkSummary | null;
  historical: ChartHistoricalEntry[];
  suppressWinnerClaims?: boolean;
  suppressionReason?: string;
}) {
  if (context.kind === "summary" && context.summary === null) {
    const benchmarks = new Set(historical.map((entry) => entry.benchmark));
    const latest = [...historical].sort((a, b) => b.run_date.localeCompare(a.run_date))[0] ?? null;
    return (
      <div class="grid gap-3 sm:grid-cols-3">
        <SummaryStat label="Runs" value={String(historical.length)} />
        <SummaryStat label="Benchmarks" value={String(benchmarks.size)} />
        <SummaryStat label="Latest run" value={latest ? <RunDateChip runDate={latest.run_date} /> : MISSING_VALUE_GLYPH} />
      </div>
    );
  }

  if (!summary) return null;

  const primaryMetric = normalizePrimaryMetric(summary.ranking?.primary_metric);
  const higherIsBetter = summary.ranking?.primary_order === "desc";
  const best = [...summary.platforms]
    .filter((platform) => validPrimaryMetricValue(platform, primaryMetric) !== null)
    .sort((a, b) => {
      const av = validPrimaryMetricValue(a, primaryMetric) ?? (higherIsBetter ? -Infinity : Infinity);
      const bv = validPrimaryMetricValue(b, primaryMetric) ?? (higherIsBetter ? -Infinity : Infinity);
      return higherIsBetter ? bv - av : av - bv;
    })[0] ?? null;
  const summaryLabels = formatRunIdentityLabelsForCohort(
    summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
  );
  const summaryLabelByResultId = new Map(
    summary.platforms.map((platform, index) => [platform.result_id, summaryLabels[index]?.compact ?? platform.platform]),
  );

  const sampleCount =
    context.kind === "detail"
      ? context.detail.display_timings.reduce((total, timing) => total + timing.sample_count, 0)
      : null;

  return (
    <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <SummaryStat label="Platforms" value={String(summary.platforms.length)} />
      <SummaryStat label="Queries" value={String(summary.query_ids.length)} />
      <SummaryStat
        label={
          suppressWinnerClaims
            ? primaryMetric === "display_geomean_ms"
              ? "Lowest geomean in ranking"
              : `Highest ${primaryMetricLabel(primaryMetric).toLowerCase()} in ranking`
            : primaryMetric === "power_score"
              ? "Best power"
              : primaryMetric === "throughput_at_size"
                ? "Best throughput"
                : "Best geomean"
        }
        value={
          suppressWinnerClaims ? (
            `Not comparable: ${suppressionReason ?? "selected runs are not from the same comparable ranking"}`
          ) : best ? (
            <>
              {`${summaryLabelByResultId.get(best.result_id) ?? best.platform} · ${
                primaryMetric === "display_geomean_ms"
                  ? fmtGeomean(best.display_geomean_ms)
                  : fmtScore(primaryMetricValue(best, primaryMetric))
              }`}
              {!isRankable(best) && (
                <span class="ml-2 text-xs font-normal text-[var(--bb-data-fg-subtle)]">not ranked</span>
              )}
            </>
          ) : (
            MISSING_VALUE_GLYPH
          )
        }
        title={suppressWinnerClaims ? suppressionReason : undefined}
      />
      <SummaryStat
        label={sampleCount !== null ? "Samples" : "Phase"}
        value={sampleCount !== null ? String(sampleCount) : sentenceCase(summary.phase)}
      />
    </div>
  );
}

function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function SummaryStat({ label, value, title }: { label: string; value: ComponentChildren; title?: string }) {
  return (
    <div class="rounded-lg panel-muted px-4 py-3" title={title}>
      <div class="text-xs font-medium uppercase tracking-wide text-[var(--bb-data-fg-subtle)]">{label}</div>
      <div class="mt-1 text-sm font-semibold text-[var(--bb-data-fg-primary)]">{value}</div>
    </div>
  );
}
