import type { BenchmarkSummary, PlatformRow } from "@/types";
import { useElementSize } from "@/lib/useElementSize";
import { axisLabelAnchor, barRowLayout, chartFrame } from "@/lib/chartFrame";
import { paletteColor } from "@/lib/chartTheme";
import { buildLogLatencyScale, computeBoxStats, logLatencyFraction, logLatencyTicks, spaceTicks } from "@/lib/chartMath";
import { formatTimingExclusion, isTimingDisplayable, platformTimingValue } from "@/lib/displayEligibility";
import { formatLatencyAxisLabels } from "@/lib/metricFormatters";
import { orderRunsByDisplayGeomean, fitRunLabels } from "@/lib/chartRunRows";
import { formatRunIdentityLabelsForCohort } from "@/lib/runIdentity";

const LABEL_W = 200;
const ROW_H = 48;
const AXIS_H = 36;
const PADDING_TOP = 12;
const PADDING_RIGHT = 12;
const LABEL_FONT_PX = 13;
const LABEL_GUTTER_PX = 6;

interface Props {
  summary: BenchmarkSummary;
  colorIndexFor?: (platform: PlatformRow) => number;
}

export function DistributionBox({ summary, colorIndexFor }: Props) {
  const [containerRef, { width: containerWidth }] = useElementSize();
  const frame = chartFrame(containerWidth);
  const w = frame.width;
  const layout = barRowLayout(frame, { labelWidth: LABEL_W, rowHeight: ROW_H, valueTrail: PADDING_RIGHT });

  const cohortLabels = formatRunIdentityLabelsForCohort(
    summary.platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
  );
  const rawLabels = summary.platforms.map((p, i) => cohortLabels[i]?.disambiguated ?? p.platform);
  const labelMaxWidthPx = layout.labelAbove ? w : LABEL_W - LABEL_GUTTER_PX;
  const displayLabels = fitRunLabels(rawLabels, labelMaxWidthPx, LABEL_FONT_PX);
  const rows = orderRunsByDisplayGeomean(summary.platforms)
    .map(({ run: p, sourceIndex: i }) => ({
      label: displayLabels[i] ?? p.platform,
      fullLabel: cohortLabels[i]?.full ?? rawLabels[i] ?? p.platform,
      color: paletteColor(colorIndexFor?.(p) ?? p.color_slot ?? i),
      isDisplayable: isTimingDisplayable(p),
      stats: computeBoxStats(summary.query_ids.map((queryId) => platformTimingValue(p, queryId))),
    }))
    .filter((r) => r.isDisplayable && r.stats !== null) as {
    label: string;
    fullLabel: string;
    color: string;
    isDisplayable: boolean;
    stats: NonNullable<ReturnType<typeof computeBoxStats>>;
  }[];

  if (rows.length === 0) return null;
  const excludedRows = summary.platforms.filter((platform) => !isTimingDisplayable(platform));

  const allMs = rows.flatMap((r) => [r.stats.min, r.stats.q1, r.stats.median, r.stats.q3, r.stats.max]);
  const scale = buildLogLatencyScale(allMs, { lowerPad: 0.3, upperPad: 0.3 });
  if (scale === null) return null;
  const logScale = scale;

  const plotW = layout.plotWidth;
  const totalH = PADDING_TOP + rows.length * layout.rowHeight + AXIS_H;

  function xFor(ms: number): number {
    return layout.plotX + logLatencyFraction(ms, logScale) * plotW;
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
        aria-label="Distribution box plots of per-query latency"
      >
        {rows.map((row, ri) => {
          const y = PADDING_TOP + ri * layout.rowHeight;
          const midY = y + layout.barCenter;
          const boxH = ROW_H * 0.46;
          const { min, q1, median, q3, max } = row.stats;

          return (
            <g key={row.label}>
              <text
                x={layout.labelAbove ? 0 : LABEL_W - 6}
                y={y + layout.labelBaseline}
                text-anchor={layout.labelAbove ? "start" : "end"}
                style={{ fontSize: `${LABEL_FONT_PX}px`, fill: "var(--bb-chart-label)" }}
              >
                <title>{row.fullLabel}</title>
                {row.label}
              </text>

              <line
                x1={xFor(min)}
                y1={midY}
                x2={xFor(max)}
                y2={midY}
                stroke={row.color}
                stroke-width={1.5}
                stroke-dasharray="3 2"
              />
              <line x1={xFor(min)} y1={midY - 5} x2={xFor(min)} y2={midY + 5} stroke={row.color} stroke-width={1.5} />
              <line x1={xFor(max)} y1={midY - 5} x2={xFor(max)} y2={midY + 5} stroke={row.color} stroke-width={1.5} />

              <rect
                x={xFor(q1)}
                y={midY - boxH / 2}
                width={Math.max(2, xFor(q3) - xFor(q1))}
                height={boxH}
                fill={row.color}
                fill-opacity={0.18}
                stroke={row.color}
                stroke-width={1.5}
                rx={2}
              />

              <line
                x1={xFor(median)}
                y1={midY - boxH / 2}
                x2={xFor(median)}
                y2={midY + boxH / 2}
                stroke={row.color}
                stroke-width={2.5}
              />

              {ri < rows.length - 1 && (
                <line
                  x1={0}
                  y1={y + layout.rowHeight}
                  x2={w}
                  y2={y + layout.rowHeight}
                  stroke="var(--bb-chart-grid)"
                  stroke-width={1}
                />
              )}
            </g>
          );
        })}

        <g transform={`translate(0, ${PADDING_TOP + rows.length * layout.rowHeight})`}>
          <line x1={layout.plotX} y1={0} x2={layout.plotX + plotW} y2={0} stroke="var(--bb-chart-grid)" stroke-width={1} />
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
            x={layout.plotX + plotW / 2}
            y={30}
            text-anchor="middle"
            style={{ fontSize: "10px", fill: "var(--bb-chart-label-muted)" }}
          >
            Latency (log scale)
          </text>
        </g>
      </svg>
      <p class="mt-1 text-[10px] text-[var(--bb-data-fg-subtle)]">
        Box: Q1-Q3 · Line: median · Whiskers: min/max of per-query display latencies
        {excludedRows.length > 0
          ? ` · ${excludedRows.length} row(s) excluded: ${formatTimingExclusion(excludedRows[0])}`
          : ""}
      </p>
    </div>
  );
}
