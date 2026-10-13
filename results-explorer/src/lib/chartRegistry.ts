import { hasCostEstimate } from "@/lib/costDisplay";
import type { BenchmarkSummary, DetailResult, PlatformRow } from "@/types";
import { assignRunColorSlots } from "@/lib/chartTheme";
import { canonicalPhase } from "@/lib/displayLabels";
import {
  isTimingDisplayable,
  isValidTimingValue,
  type ChartDatasetEligibilityClass,
  type PrimaryMetric,
} from "@/lib/displayEligibility";

export interface ChartHistoricalEntry {
  result_id: string;
  benchmark: string;
  scale_factor: number;
  platform: string;
  platform_id: string;
  run_date: string;
  power_score: number | null;
  throughput_at_size?: number | null;
  display_geomean_ms: number | null;
}

export interface DataRequirements {
  requiresSummary?: boolean;
  requiresTwoResults?: boolean;
  requiresCostData?: boolean;
  requiresPowerScore?: boolean;
  requiresPhaseDurations?: boolean;
  requiresHistorical?: boolean;
  requiresQueryTimings?: boolean;
  requiresPercentileStats?: boolean;
}

export type ChartQuestionGroupId =
  | "overview"
  | "per_query"
  | "distribution"
  | "cost"
  | "trend"
  | "rank";

export interface ChartQuestionGroup {
  id: ChartQuestionGroupId;
  label: string;
  description: string;
}

export interface ChartRegistryEntry {
  id: string;
  title: string;
  shortTitle: string;
  description: string;
  questionGroup: ChartQuestionGroupId;
  eligibilityClass: ChartDatasetEligibilityClass;
  requires: DataRequirements;
  cli_equivalent: string;
  foldedInto?: string;
}

export interface ChartQuestionGroupWithCharts extends ChartQuestionGroup {
  charts: ChartRegistryEntry[];
}

export const CHART_QUESTION_GROUPS: readonly ChartQuestionGroup[] = [
  {
    id: "overview",
    label: "Overview",
    description: "Top-line metric summaries and phase composition",
  },
  {
    id: "per_query",
    label: "Per-query",
    description: "Query-level comparisons and outlier views",
  },
  {
    id: "distribution",
    label: "Distribution",
    description: "Latency spread and percentile shape",
  },
  {
    id: "cost",
    label: "Cost",
    description: "Cost and performance tradeoffs",
  },
  {
    id: "trend",
    label: "Trend",
    description: "Historical movement over time",
  },
  {
    id: "rank",
    label: "Rank",
    description: "Per-query ranking tables",
  },
] as const;

export const CHART_QUESTION_GROUP_BY_ID: Readonly<Record<ChartQuestionGroupId, ChartQuestionGroup>> =
  Object.fromEntries(CHART_QUESTION_GROUPS.map((group) => [group.id, group])) as Record<
    ChartQuestionGroupId,
    ChartQuestionGroup
  >;

export const CHART_REGISTRY: readonly ChartRegistryEntry[] = [
  {
    id: "performance_bar",
    title: "Geomean latency by run",
    shortTitle: "Performance",
    description: "Bar chart comparing total runtime across platforms",
    questionGroup: "overview",
    eligibilityClass: "display_safe",
    requires: { requiresSummary: true },
    cli_equivalent: "performance_bar",
  },
  {
    id: "power_bar",
    title: "Power@Size by run",
    shortTitle: "Power",
    description: "Bar chart comparing TPC Power@Size metric across platforms (higher is better)",
    questionGroup: "overview",
    eligibilityClass: "rank_safe",
    requires: { requiresSummary: true, requiresPowerScore: true },
    cli_equivalent: "power_bar",
  },
  {
    id: "distribution_box",
    title: "Latency distribution",
    shortTitle: "Box Plot",
    description: "Box plot showing query execution time distribution",
    questionGroup: "distribution",
    eligibilityClass: "display_safe",
    requires: { requiresSummary: true, requiresQueryTimings: true },
    cli_equivalent: "distribution_box",
  },
  {
    id: "query_heatmap",
    title: "Per-query latency heatmap",
    shortTitle: "Heatmap",
    description: "Heatmap comparing per-query execution times across platforms",
    questionGroup: "per_query",
    eligibilityClass: "display_safe",
    requires: { requiresSummary: true, requiresQueryTimings: true },
    cli_equivalent: "query_heatmap",
  },
  {
    id: "query_histogram",
    title: "Per-query Latency",
    shortTitle: "Latency",
    description: "Horizontal latency plot with one marker per run on each query row",
    questionGroup: "per_query",
    eligibilityClass: "display_safe",
    requires: { requiresSummary: true, requiresQueryTimings: true },
    cli_equivalent: "query_histogram",
  },
  {
    id: "cost_scatter",
    title: "Cost vs performance",
    shortTitle: "Cost",
    description: "Scatter plot of normalized cost vs performance with cost-status empty states",
    questionGroup: "cost",
    eligibilityClass: "cost_safe",
    requires: { requiresSummary: true, requiresCostData: true },
    cli_equivalent: "cost_scatter",
  },
  {
    id: "time_series",
    title: "Performance over time",
    shortTitle: "Trend",
    description: "Line chart showing performance trends over time",
    questionGroup: "trend",
    eligibilityClass: "trend_safe",
    requires: { requiresHistorical: true },
    cli_equivalent: "time_series",
  },
  {
    id: "comparison_bar",
    title: "Comparison by run",
    shortTitle: "Compare",
    description:
      "Paired side-by-side bars comparing two runs per query with % change annotations",
    questionGroup: "per_query",
    eligibilityClass: "compare_safe",
    requires: { requiresTwoResults: true },
    cli_equivalent: "comparison_bar",
  },
  {
    id: "diverging_bar",
    title: "Difference (%) vs Baseline",
    shortTitle: "Difference (%)",
    description:
      "Centered-zero view of each query's percentage change against the baseline, sorted by magnitude; shown through the Difference (%) option of the per-query comparison",
    questionGroup: "per_query",
    eligibilityClass: "compare_safe",
    requires: { requiresTwoResults: true },
    cli_equivalent: "diverging_bar",
    foldedInto: "normalized_speedup",
  },
  {
    id: "summary_box",
    title: "Run summary",
    shortTitle: "Summary",
    description:
      "Bordered panel with aggregate stats (geo mean, total time, improved/regressed counts)",
    questionGroup: "overview",
    eligibilityClass: "provenance_only",
    requires: {},
    cli_equivalent: "summary_box",
  },
  {
    id: "percentile_ladder",
    title: "Latency percentiles",
    shortTitle: "Percentiles",
    description: "Percentile ladder chart (P50/P90/P95/P99) across platforms",
    questionGroup: "distribution",
    eligibilityClass: "display_safe",
    requires: { requiresSummary: true, requiresPercentileStats: true },
    cli_equivalent: "percentile_ladder",
  },
  {
    id: "normalized_speedup",
    title: "Per-query comparison vs baseline",
    shortTitle: "vs Baseline",
    description:
      "For each query, compare every run with the baseline as a speedup (default) or as a percentage difference; speedups above 1 are faster",
    questionGroup: "per_query",
    eligibilityClass: "compare_safe",
    requires: { requiresTwoResults: true },
    cli_equivalent: "normalized_speedup",
  },
  {
    id: "stacked_phase",
    title: "Time by phase",
    shortTitle: "Phases",
    description: "Stacked phase breakdown chart across benchmark execution phases",
    questionGroup: "overview",
    eligibilityClass: "display_safe",
    requires: { requiresSummary: true, requiresPhaseDurations: true },
    cli_equivalent: "stacked_phase",
  },
  {
    id: "sparkline_table",
    title: "Geomean and cost by run",
    shortTitle: "Sparklines",
    description: "Compact sparkline table of key metrics across platforms",
    questionGroup: "overview",
    eligibilityClass: "display_safe",
    requires: { requiresSummary: true },
    cli_equivalent: "sparkline_table",
  },
  {
    id: "cdf_chart",
    title: "Cumulative latency distribution",
    shortTitle: "CDF",
    description: "Cumulative distribution chart of per-query execution latency",
    questionGroup: "distribution",
    eligibilityClass: "display_safe",
    requires: { requiresSummary: true, requiresQueryTimings: true },
    cli_equivalent: "cdf_chart",
  },
  {
    id: "rank_table",
    title: "Query rank summary",
    shortTitle: "Ranks",
    description: "Per-query platform ranking table (1st=fastest)",
    questionGroup: "rank",
    eligibilityClass: "rank_safe",
    requires: { requiresSummary: true, requiresQueryTimings: true },
    cli_equivalent: "rank_table",
  },
] as const;

export const ALL_CHART_IDS: readonly string[] = CHART_REGISTRY.map((e) => e.id);

export const CHART_REGISTRY_BY_ID: Readonly<Record<string, ChartRegistryEntry>> =
  Object.fromEntries(CHART_REGISTRY.map((e) => [e.id, e]));

export function isValidChartId(id: string): boolean {
  return id in CHART_REGISTRY_BY_ID;
}

export type ChartContext =
  | { kind: "summary"; summary: BenchmarkSummary | null; historical?: ChartHistoricalEntry[] }
  | {
      kind: "compare";
      results: DetailResult[];
      primaryMetric?: PrimaryMetric;
    }
  | {
      kind: "detail";
      detail: DetailResult;
      historical?: ChartHistoricalEntry[];
      primaryMetric?: PrimaryMetric;
    };

interface ChartCapabilities {
  hasSummary: boolean;
  hasTwoResults: boolean;
  hasCostData: boolean;
  hasPowerScore: boolean;
  hasPhaseDurations: boolean;
  hasHistorical: boolean;
  hasQueryTimings: boolean;
  hasPercentileStats: boolean;
}

function rankingFromPrimaryMetric(
  primaryMetric: PrimaryMetric | undefined,
): { primary_metric: string; primary_order: "asc" | "desc" } {
  return primaryMetric === "power_score" || primaryMetric === "throughput_at_size"
    ? { primary_metric: primaryMetric, primary_order: "desc" }
    : { primary_metric: "display_geomean_ms", primary_order: "asc" };
}

function buildDetailSummary(
  detail: DetailResult,
  primaryMetric: PrimaryMetric | undefined,
): BenchmarkSummary {
  return {
    benchmark: detail.benchmark,
    scale_factor: detail.scale_factor,
    phase: canonicalPhase(detail.test_type),
    stream_count: detail.stream_count ?? null,
    query_ids: detail.display_timings.map((timing) => timing.query_id),
    platforms: [detailToPlatformRow(detail)],
    cell_reduction: "median",
    ranking: {
      ...rankingFromPrimaryMetric(primaryMetric),
      secondary_metric: "platform",
    },
  };
}

function buildCompareSummary(
  results: DetailResult[],
  primaryMetric: PrimaryMetric | undefined,
): BenchmarkSummary | null {
  if (results.length === 0) return null;

  const queryIds = [...new Set(results.flatMap((result) => result.display_timings.map((timing) => timing.query_id)))].sort(
    (a, b) => a.localeCompare(b, undefined, { numeric: true }),
  );

  return {
    benchmark: results[0]!.benchmark,
    scale_factor: results[0]!.scale_factor,
    phase: canonicalPhase(results[0]!.test_type),
    stream_count: results[0]!.stream_count ?? null,
    query_ids: queryIds,
    platforms: results.map(detailToPlatformRow),
    cell_reduction: "median",
    ranking: {
      ...rankingFromPrimaryMetric(primaryMetric),
      secondary_metric: "platform",
    },
  };
}

function detailToPlatformRow(detail: DetailResult): PlatformRow {
  return {
    result_id: detail.result_id,
    short_id: detail.result_id.slice(0, 8),
    platform_id: detail.platform_id,
    platform: detail.platform,
    platform_version: detail.platform_version,
    tuning_mode: detail.tuning_mode,
    tuning_hash: detail.tuning_hash,
    execution_mode: detail.execution_mode,
    trust_label: detail.trust_label,
    funding: detail.funding,
    run_date: detail.run_date,
    is_ranking_eligible: detail.ranking_exclusion_reason === null,
    has_display_timing: detail.has_display_timing,
    valid_query_count: detail.valid_query_count,
    missing_query_count: detail.missing_query_count,
    zero_timing_count: detail.zero_timing_count,
    display_exclusion_reason: detail.display_exclusion_reason,
    comparison_exclusion_reason: detail.comparison_exclusion_reason,
    ranking_exclusion_reason: detail.ranking_exclusion_reason,
    power_score: detail.power_score,
    throughput_at_size: detail.throughput_at_size ?? null,
    display_geomean_ms: detail.display_geomean_ms,
    sample_geomean_ms: detail.geomean_ms,
    cost_usd: detail.cost_usd,
    normalized_cost_usd: detail.normalized_cost_usd,
    cost_model_version: detail.cost_model_version,
    cost_model_source: detail.cost_model_source,
    cost_scope: detail.cost_scope,
    cost_status: detail.cost_status,
    estimated_cost_usd: detail.estimated_cost_usd ?? null,
    cost_estimate_method: detail.cost_estimate_method ?? null,
    cost_estimate_source: detail.cost_estimate_source ?? null,
    cost_estimate_pricing_date: detail.cost_estimate_pricing_date ?? null,
    cost_estimate_assumptions: detail.cost_estimate_assumptions ?? null,
    billing_unit: detail.billing_unit,
    pricing_region: detail.pricing_region,
    deployment_class: detail.deployment_class,
    cloud_provider: detail.cloud_provider,
    cloud_region: detail.cloud_region,
    instance_or_warehouse: detail.instance_or_warehouse,
    instance_type: detail.instance_type,
    warehouse_size: detail.warehouse_size,
    node_count: detail.node_count,
    cluster_size: detail.cluster_size,
    storage_format: detail.storage_format,
    storage_tier: detail.storage_tier,
    compliance_class: detail.compliance_class,
    percentile_stats: null,
    phase_durations: null,
    timings: Object.fromEntries(detail.display_timings.map((timing) => [timing.query_id, timing.display_ms])),
    timing_eligibility: Object.fromEntries(
      detail.display_timings.map((timing) => [
        timing.query_id,
        {
          is_valid_display_timing: timing.is_valid_display_timing,
          timing_exclusion_reason: timing.timing_exclusion_reason,
        },
      ]),
    ),
  };
}

function withRunColorSlots(summary: BenchmarkSummary): BenchmarkSummary {
  const slots = assignRunColorSlots(summary.platforms);
  return {
    ...summary,
    platforms: summary.platforms.map((platform) => ({ ...platform, color_slot: slots.get(platform.result_id) })),
  };
}

export function buildRenderableSummary(context: ChartContext): BenchmarkSummary | null {
  if (context.kind === "summary") return context.summary ? withRunColorSlots(context.summary) : null;
  if (context.kind === "detail") return withRunColorSlots(buildDetailSummary(context.detail, context.primaryMetric));
  const summary = buildCompareSummary(context.results, context.primaryMetric);
  return summary ? withRunColorSlots(summary) : null;
}

function getChartCapabilities(context: ChartContext): ChartCapabilities {
  const summary = buildRenderableSummary(context);
  const historical =
    context.kind === "summary"
      ? context.historical ?? []
      : context.kind === "detail"
        ? context.historical ?? []
        : [];

  return {
    hasSummary: summary !== null,
    hasTwoResults: context.kind === "compare" && context.results.length >= 2,
    hasCostData:
      summary?.platforms.some(
        (platform) =>
          (platform.cost_status === "normalized" && platform.normalized_cost_usd != null) || hasCostEstimate(platform),
      ) ?? false,
    hasPowerScore: summary?.platforms.some((platform) => isValidTimingValue(platform.power_score)) ?? false,
    hasPhaseDurations:
      summary?.platforms.some(
        (platform) =>
          isTimingDisplayable(platform) &&
          platform.phase_durations !== null &&
          Object.keys(platform.phase_durations).length > 0,
      ) ?? false,
    hasHistorical: historical.length >= 2,
    hasQueryTimings:
      (summary?.platforms.some(
        (platform) =>
          summary.query_ids.some((queryId) => Object.prototype.hasOwnProperty.call(platform.timings, queryId)),
      ) ?? false),
    hasPercentileStats:
      summary?.platforms.some((platform) => isTimingDisplayable(platform) && platform.percentile_stats !== null) ?? false,
  };
}

function contextSupportsEntry(entry: ChartRegistryEntry, context: ChartContext): boolean {
  if (context.kind === "compare") {
    return entry.id !== "time_series";
  }

  if (context.kind === "detail") {
    return !entry.requires.requiresTwoResults && entry.id !== "time_series";
  }

  if (context.summary === null) {
    return entry.id === "summary_box" || entry.id === "time_series";
  }

  return true;
}

export function applicableCharts(context: ChartContext): ChartRegistryEntry[] {
  const capabilities = getChartCapabilities(context);
  return CHART_REGISTRY.filter((entry) => {
    if (!contextSupportsEntry(entry, context)) return false;

    const requires = entry.requires;
    if (requires.requiresSummary && !capabilities.hasSummary) return false;
    if (requires.requiresTwoResults && !capabilities.hasTwoResults) return false;
    if (requires.requiresCostData && !capabilities.hasCostData) return false;
    if (requires.requiresPowerScore && !capabilities.hasPowerScore) return false;
    if (requires.requiresPhaseDurations && !capabilities.hasPhaseDurations) return false;
    if (requires.requiresHistorical && !capabilities.hasHistorical) return false;
    if (requires.requiresQueryTimings && !capabilities.hasQueryTimings) return false;
    if (requires.requiresPercentileStats && !capabilities.hasPercentileStats) return false;
    return true;
  });
}

export function groupChartsByQuestion(
  charts: readonly ChartRegistryEntry[],
): ChartQuestionGroupWithCharts[] {
  return CHART_QUESTION_GROUPS.map((group) => ({
    ...group,
    charts: charts.filter((chart) => chart.questionGroup === group.id),
  })).filter((group) => group.charts.length > 0);
}
