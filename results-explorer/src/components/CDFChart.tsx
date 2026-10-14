import type { BenchmarkSummary, PlatformRow } from "@/types";
import { useElementSize } from "@/lib/useElementSize";
import { axisLabelAnchor, chartFrame } from "@/lib/chartFrame";
import { paletteColor } from "@/lib/chartTheme";
import { buildLogLatencyScale, computeECDFPoints, logLatencyFraction, logLatencyTicks, spaceTicks } from "@/lib/chartMath";
import { formatTimingExclusion, isTimingDisplayable, platformTimingValue } from "@/lib/displayEligibility";
import { formatLatencyAxisLabels } from "@/lib/metricFormatters";
import { orderRunsByDisplayGeomean, fitRunLabels } from "@/lib/chartRunRows";
import { formatRunIdentityLabelsForCohort } from "@/lib/runIdentity";

const Y_TICKS_PCT = [0, 25, 50, 75, 100];

const LABEL_W = 36;
const AXIS_H = 40;
const PADDING_TOP = 8;
const PADDING_RIGHT = 12;
const PLOT_H = 180;
const LEGEND_FONT_PX = 12;
const LEGEND_SWATCH_PX = 24;

interface Props {
  summary: BenchmarkSummary;
  colorIndexFor?: (platform: PlatformRow) => number;
}

export function CDFChart({ summary, colorIndexFor }: Props) {
  const [containerRef, { width: containerWidth }] = useElementSize();
  const frame = chartFrame(containerWidth);
  const w = frame.width;

  const cohortLabels = formatRunIdentityLabelsForCohort(
    summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
  );
  const rawLabels = cohortLabels.map((label) => label.disambiguated);
  const series = orderRunsByDisplayGeomean(summary.platforms)
    .map(({ run: p, sourceIndex: i }) => ({
      label: rawLabels[i] ?? p.platform,
      fullLabel: cohortLabels[i]?.full ?? p.platform,
      color: paletteColor(colorIndexFor?.(p) ?? p.color_slot ?? i),
      isDisplayable: isTimingDisplayable(p),
      points: computeECDFPoints(summary.query_ids.map((queryId) => platformTimingValue(p, queryId))),
    }))
    .filter((s) => s.isDisplayable && s.points.length > 0);
  const legendLabels = fitRunLabels(
    series.map((s) => s.label),
    Math.max(w - LEGEND_SWATCH_PX, LEGEND_SWATCH_PX),
    LEGEND_FONT_PX,
  );

  if (series.length === 0) return null;
  const excludedRows = summary.platforms.filter((platform) => !isTimingDisplayable(platform));

  const allMs = series.flatMap((s) => s.points.map((p) => p.x));
  const scale = buildLogLatencyScale(allMs, { lowerPad: 0.2, upperPad: 0.2 });
  if (scale === null) return null;
  const logScale = scale;

  const plotW = w - LABEL_W - PADDING_RIGHT;
  const totalH = PADDING_TOP + PLOT_H + AXIS_H;

  function xFor(ms: number): number {
    return LABEL_W + logLatencyFraction(ms, logScale) * plotW;
  }
  function yFor(pct: number): number {
    return PADDING_TOP + PLOT_H * (1 - pct / 100);
  }

  const xTicks = spaceTicks(logLatencyTicks(logScale), xFor);
  const tickLabels = formatLatencyAxisLabels(xTicks);

  return (
    <div ref={containerRef} class="w-full">
      <svg
        class="bb-chart-svg"
        width="100%"
        height={totalH}
        viewBox={`0 0 ${w} ${totalH}`}
        role="img"
        aria-label="Cumulative distribution of per-query latency"
      >
        {Y_TICKS_PCT.map((pct) => {
          const y = yFor(pct);
          return (
            <g key={pct}>
              <line x1={LABEL_W} y1={y} x2={w - PADDING_RIGHT} y2={y} stroke="var(--bb-chart-grid)" stroke-width={1} />
              <text x={LABEL_W - 4} y={y + 4} text-anchor="end" style={{ fontSize: "10px", fill: "var(--bb-chart-label-muted)" }}>
                {pct}%
              </text>
            </g>
          );
        })}

        {series.map((s) => {
          const d = s.points
            .map((p, i) => {
              const x = xFor(p.x).toFixed(1);
              const y = yFor(p.y).toFixed(1);
              if (i === 0) return `M${x},${y}`;
              return `H${x} V${y}`;
            })
            .join(" ");
          return (
            <path key={s.fullLabel} d={d} stroke={s.color} stroke-width={2} fill="none">
              <title>{s.fullLabel}</title>
            </path>
          );
        })}

        <g transform={`translate(0, ${PADDING_TOP + PLOT_H})`}>
          <line x1={LABEL_W} y1={0} x2={w - PADDING_RIGHT} y2={0} stroke="var(--bb-chart-grid)" stroke-width={1} />
          {xTicks.map((ms, index) => {
            const x = xFor(ms);
            return (
              <g key={ms}>
                <line x1={x} y1={0} x2={x} y2={4} stroke="var(--bb-chart-label-muted)" stroke-width={1} />
                <text
                  x={x}
                  y={16}
                  text-anchor={axisLabelAnchor(x, w)}
                  style={{ fontSize: "10px", fill: "var(--bb-chart-axis)" }}
                >
                  {tickLabels[index]}
                </text>
              </g>
            );
          })}
          <text
            x={LABEL_W + plotW / 2}
            y={30}
            text-anchor="middle"
            style={{ fontSize: "10px", fill: "var(--bb-chart-label-muted)" }}
          >
            Latency (log scale)
          </text>
        </g>
      </svg>

      <ul class="mt-1.5 flex list-none flex-wrap gap-x-3 gap-y-1 p-0 text-xs text-[var(--bb-data-fg-muted)]">
        {series.map((s, index) => (
          <li key={s.fullLabel} class="flex items-center gap-1.5" title={s.fullLabel}>
            <span
              class="inline-block h-0.5 w-4 flex-shrink-0 rounded-sm"
              style={{ backgroundColor: s.color }}
            />
            {legendLabels[index] ?? s.label}
          </li>
        ))}
      </ul>
      {excludedRows.length > 0 && (
        <p class="mt-1 text-[10px] text-[var(--bb-data-fg-subtle)]">
          {excludedRows.length} row(s) excluded from CDF: {formatTimingExclusion(excludedRows[0])}
        </p>
      )}
    </div>
  );
}
