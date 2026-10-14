import type { BenchmarkSummary, PlatformRow } from "@/types";
import { useElementSize } from "@/lib/useElementSize";
import { axisLabelAnchor, chartFrame } from "@/lib/chartFrame";
import { paletteColor } from "@/lib/chartTheme";
import { costModelDisclosure, estimatedCostValue, normalizedCostValue } from "@/lib/costDisplay";
import { normalizePrimaryMetric, primaryMetricHigherIsBetter, primaryMetricLabel, primaryMetricValue } from "@/lib/displayEligibility";
import { formatLatencyMs, formatScoreMetric, formatUsd } from "@/lib/metricFormatters";

const AXIS_W = 54;
const AXIS_H = 32;
const PADDING_TOP = 20;
const PADDING_RIGHT = 16;
const CHART_H = 180;

interface Props {
  summary: BenchmarkSummary;
}

type ScatterPoint = {
  result_id: string;
  platform: string;
  cost: number;
  estimated: boolean;
  perf: number;
  modelVersion: string | null | undefined;
  scope: string | null | undefined;
  provider: string | null | undefined;
  region: string | null | undefined;
  color: string;
};

export function CostScatter({ summary }: Props) {
  const [containerRef, { width: containerWidth }] = useElementSize();
  const frame = chartFrame(containerWidth);
  const w = frame.width;

  const metric = normalizePrimaryMetric(summary.ranking?.primary_metric);
  const higherIsBetter = primaryMetricHigherIsBetter(metric);

  const pts: ScatterPoint[] = summary.platforms
    .map((p, i) => {
      const metered = normalizedCostValue(p);
      const cost = metered ?? estimatedCostValue(p);
      return {
        result_id: p.result_id,
        platform: p.platform,
        cost,
        estimated: metered === null && cost !== null,
        perf: primaryMetricValue(p, metric),
        modelVersion: p.cost_model_version,
        scope: p.cost_scope,
        provider: p.cloud_provider,
        region: p.cloud_region ?? p.pricing_region,
        color: paletteColor(p.color_slot ?? i),
      };
    })
    .filter((p): p is ScatterPoint => p.cost !== null && p.perf !== null) as ScatterPoint[];

  if (pts.length === 0) {
    return (
      <div class="space-y-1 text-sm italic text-[var(--bb-data-fg-subtle)]">
        <p>No normalized cost data available for this ranking.</p>
        <p class="text-xs">{normalizedCostEmptyReason(summary.platforms)}</p>
      </div>
    );
  }

  const costs = pts.map((p) => p.cost);
  const perfs = pts.map((p) => p.perf);
  const minCost = Math.min(...costs);
  const maxCost = Math.max(...costs);
  const minPerf = Math.min(...perfs);
  const maxPerf = Math.max(...perfs);
  const costPad = (maxCost - minCost) * 0.1 || maxCost * 0.1 || 0.01;
  const perfPad = (maxPerf - minPerf) * 0.1 || maxPerf * 0.1 || 1;

  const xMin = Math.max(0, minCost - costPad);
  const xMax = maxCost + costPad;
  const yMin = Math.max(0, minPerf - perfPad);
  const yMax = maxPerf + perfPad;
  const xRange = xMax - xMin || 1;
  const yRange = yMax - yMin || 1;

  const plotW = w - AXIS_W - PADDING_RIGHT;
  const totalH = PADDING_TOP + CHART_H + AXIS_H;

  function xFor(cost: number): number {
    return AXIS_W + ((cost - xMin) / xRange) * plotW;
  }

  function yFor(perf: number): number {
    const normalized = (perf - yMin) / yRange;
    const pos = higherIsBetter ? normalized : 1 - normalized;
    return PADDING_TOP + CHART_H * (1 - pos);
  }

  const metricLabel = higherIsBetter
    ? `${primaryMetricLabel(metric)} (higher is better)`
    : "Geomean latency (lower is better)";
  const modelDisclosure = costModelDisclosure(summary.platforms);

  return (
    <div ref={containerRef} class="w-full">
      <svg
        class="bb-chart-svg"
        width="100%"
        height={totalH}
        viewBox={`0 0 ${w} ${totalH}`}
        role="img"
        aria-label={`Normalized cost vs ${metricLabel} scatter plot (${modelDisclosure})`}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const y = PADDING_TOP + f * CHART_H;
          const val = higherIsBetter ? yMax - f * yRange : yMin + f * yRange;
          const label = higherIsBetter
            ? formatScoreMetric(metric, val)
            : formatLatencyMs(val, { subMillisecond: "compact" }).valueText;
          return (
            <g key={f}>
              <line
                x1={AXIS_W}
                y1={y}
                x2={w - PADDING_RIGHT}
                y2={y}
                stroke="var(--bb-chart-grid-muted)"
                stroke-width={1}
              />
              <text
                x={AXIS_W - 4}
                y={y + 4}
                text-anchor="end"
                style={{ fontSize: "9px", fill: "var(--bb-chart-label-muted)" }}
              >
                {label}
              </text>
            </g>
          );
        })}

        {pts.map((p) => {
          const cx = xFor(p.cost);
          const cy = yFor(p.perf);
          const shortLabel = p.platform.length > 10 ? `${p.platform.slice(0, 9)}…` : p.platform;
          const regionLabel = [p.provider, p.region].filter(Boolean).join(" ");
          return (
            <g key={p.result_id}>
              <circle
                cx={cx}
                cy={cy}
                r={7}
                fill={p.estimated ? "none" : p.color}
                fill-opacity={p.estimated ? undefined : 0.85}
                stroke={p.color}
                stroke-width={p.estimated ? 2 : undefined}
              >
                <title>
                  {`${p.platform}: ${p.estimated ? "estimated ≈" : "normalized"} ${formatUsd(p.cost).valueText} / ${
                    higherIsBetter ? formatScoreMetric(metric, p.perf) : formatLatencyMs(p.perf).valueText
                  } (${p.modelVersion ?? "model unknown"}${regionLabel ? `, ${regionLabel}` : ""})`}
                </title>
              </circle>
              <text
                x={cx}
                y={cy - 11}
                text-anchor={axisLabelAnchor(cx, w, 40)}
                style={{ fontSize: "10px", fill: "var(--bb-chart-label)" }}
              >
                {shortLabel}
              </text>
            </g>
          );
        })}

        <line
          x1={AXIS_W}
          y1={PADDING_TOP}
          x2={AXIS_W}
          y2={PADDING_TOP + CHART_H}
          stroke="var(--bb-chart-grid)"
          stroke-width={1}
        />
        <line
          x1={AXIS_W}
          y1={PADDING_TOP + CHART_H}
          x2={w - PADDING_RIGHT}
          y2={PADDING_TOP + CHART_H}
          stroke="var(--bb-chart-grid)"
          stroke-width={1}
        />

        {[0, 0.5, 1].map((f) => {
          const x = AXIS_W + f * plotW;
          const cost = xMin + f * xRange;
          return (
            <g key={f}>
              <line
                x1={x}
                y1={PADDING_TOP + CHART_H}
                x2={x}
                y2={PADDING_TOP + CHART_H + 4}
                stroke="var(--bb-chart-label-muted)"
                stroke-width={1}
              />
              <text
                x={x}
                y={PADDING_TOP + CHART_H + 16}
                text-anchor={axisLabelAnchor(x, w)}
                style={{ fontSize: "10px", fill: "var(--bb-chart-axis)" }}
              >
                {formatUsd(cost).valueText}
              </text>
            </g>
          );
        })}
        <text
          x={AXIS_W + plotW / 2}
          y={PADDING_TOP + CHART_H + AXIS_H - 2}
          text-anchor="middle"
          style={{ fontSize: "10px", fill: "var(--bb-chart-label-muted)" }}
        >
          Normalized cost (USD)
        </text>

        <text
          x={0}
          y={0}
          style={{ fontSize: "9px", fill: "var(--bb-chart-label-muted)" }}
          transform={`rotate(-90) translate(${-(PADDING_TOP + CHART_H / 2)}, 11)`}
          text-anchor="middle"
        >
          {metricLabel}
        </text>
      </svg>
      <p class="mt-1 text-xs text-[var(--bb-data-fg-subtle)]">
        {modelDisclosure}. Rows with <code class="rounded bg-[var(--bb-surface-data-muted)] px-1">cost_status=normalized</code> or{" "}
        <code class="rounded bg-[var(--bb-surface-data-muted)] px-1">cost_status=estimated</code> are plotted; local and unavailable
        cost rows are omitted. Solid markers are normalized; hollow = estimated.
      </p>
    </div>
  );
}

function normalizedCostEmptyReason(platforms: PlatformRow[]): string {
  if (platforms.length === 0) return "No platforms are present in the selected ranking.";
  if (platforms.every((platform) => platform.cost_status === undefined || platform.cost_status === null)) {
    return "These older runs do not include the cost details needed for comparison.";
  }
  if (platforms.every((platform) => platform.cost_status === "not_applicable_local")) {
    return "Only local or self-hosted runs are shown, so cloud cost comparisons do not apply.";
  }

  const missing = new Set<string>();
  for (const platform of platforms) {
    if (platform.cost_status !== "unavailable") continue;
    if (!platform.cloud_provider) missing.add("cloud provider");
    if (!platform.cloud_region && !platform.pricing_region) missing.add("region");
    if (!platform.instance_type && !platform.warehouse_size && !platform.cluster_size) {
      missing.add("instance, warehouse, or cluster shape");
    }
    if (!platform.cost_model_version) missing.add("cost model version");
  }
  if (missing.size > 0) {
    return `These runs are missing required cost details: ${[...missing].join(", ")}.`;
  }
  return "None of these runs has a usable normalized cost.";
}
