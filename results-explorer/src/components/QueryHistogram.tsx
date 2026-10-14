import { formatRunIdentitiesForCohort } from "@/lib/runIdentity";
import type { BenchmarkSummary } from "@/types";
import { useElementSize } from "@/lib/useElementSize";
import { chartFrame } from "@/lib/chartFrame";
import { paletteColor } from "@/lib/chartTheme";
import { MISSING_MARKER_RADIUS, RUN_MARKER_RADIUS, runMarkerPath } from "@/lib/chartMarkers";
import { RunLegend } from "@/components/RunLegend";
import { queryDisplayLabel, sortQueryIds } from "@/lib/queryLabels";
import { formatTimingExclusion, platformTimingValue } from "@/lib/displayEligibility";
import { formatCompactLatencyMs } from "@/lib/metricFormatters";
import { estimateTextWidth, fitLabel, labelColumnWidth } from "@/lib/chartLabels";
import { formatTickMs, logTicks } from "@/lib/chartTicks";

const ROW_H = 28;
const TOP_H = 30;
const AXIS_H = 26;
const LABEL_FONT_PX = 10;
const LABEL_GAP_PX = 8;
const TICK_FONT_PX = 9;
const MARKER_OVERHANG_PX = 8;
const MISSING_GUTTER_PX = 24;
const MISSING_MARKER_CENTER_PX = 8;
const PLOT_RIGHT_PAD_PX = 12;

interface Props {
  summary: BenchmarkSummary;
  preserveOrder?: boolean;
  queryHref?: (queryId: string) => string;
}

function timingLabel(summary: BenchmarkSummary, platformIndex: number, queryId: string, runLabel: string): string {
  const platform = summary.platforms[platformIndex]!;
  const ms = platformTimingValue(platform, queryId);
  const value = ms === null
    ? formatTimingExclusion(platform.timing_eligibility[queryId]?.timing_exclusion_reason ?? "missing_timing")
    : formatCompactLatencyMs(ms);
  return `${runLabel} · ${queryDisplayLabel(queryId)}: ${value}`;
}

interface LogScale {
  lo: number;
  hi: number;
  logLo: number;
  logHi: number;
}

function latencyScale(values: readonly number[]): LogScale {
  const positive = values.filter((value) => Number.isFinite(value) && value > 0);
  if (positive.length === 0) return { lo: 1, hi: 10, logLo: 0, logHi: 1 };
  const lo = Math.min(...positive);
  let hi = Math.max(...positive);
  if (hi <= lo) hi = lo * 10;
  return { lo, hi, logLo: Math.log10(lo), logHi: Math.log10(hi) };
}

export function QueryHistogram({ summary, preserveOrder = false, queryHref }: Props) {
  const [containerRef, { width: containerWidth }] = useElementSize(300);
  const width = chartFrame(containerWidth, { minWidth: 300 }).width;
  const { platforms, query_ids } = summary;

  if (platforms.length === 0 || query_ids.length === 0) return null;

  const runLabels = formatRunIdentitiesForCohort(platforms, "chart");
  const sortedQueryIds = preserveOrder ? query_ids : sortQueryIds(query_ids);
  const scale = latencyScale(
    platforms.flatMap((platform) =>
      sortedQueryIds.map((queryId) => platformTimingValue(platform, queryId) ?? Number.NaN),
    ),
  );
  const hasMissing = platforms.some((platform) =>
    sortedQueryIds.some((queryId) => {
      const ms = platformTimingValue(platform, queryId);
      return ms === null || ms <= 0;
    }),
  );
  const labelW = labelColumnWidth(sortedQueryIds.map(queryDisplayLabel), LABEL_FONT_PX, width);
  const plotX = labelW + (hasMissing ? MISSING_GUTTER_PX : MARKER_OVERHANG_PX);
  const plotWidth = Math.max(1, width - plotX - PLOT_RIGHT_PAD_PX);
  const plotRight = plotX + plotWidth;
  const toX = (ms: number): number =>
    plotX + ((Math.log10(ms) - scale.logLo) / (scale.logHi - scale.logLo)) * plotWidth;
  const height = TOP_H + sortedQueryIds.length * ROW_H + AXIS_H;

  return (
    <div ref={containerRef} class="w-full">
      <p id="query-histogram-description" class="sr-only">
        Each row is one query. Every run shares the same query row; marker shape and color identify runs
        and horizontal position shows latency on a logarithmic millisecond scale from {formatCompactLatencyMs(scale.lo)} to{" "}
        {formatCompactLatencyMs(scale.hi)}. Tooltips and the accessible table give each run's timing or exclusion reason.
        {hasMissing
          ? " A hollow circle left of the scale marks a run with no successful measurement for that query."
          : null}
      </p>
      <svg
        class="bb-chart-svg"
        width="100%"
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role={queryHref === undefined ? "img" : "group"}
        aria-label="Horizontal query latency chart"
        aria-describedby="query-histogram-description"
        data-orientation="horizontal"
        data-plot-start={plotX}
      >
        {logTicks(scale.lo, scale.hi, plotWidth).map((tick) => {
          const x = toX(tick);
          const tickText = formatTickMs(tick);
          const halfTextWidth = estimateTextWidth(tickText, TICK_FONT_PX) / 2;
          const anchor = x - plotX < halfTextWidth ? "start" : plotRight - x < halfTextWidth ? "end" : "middle";
          return (
            <g key={tick}>
              <line
                x1={x}
                x2={x}
                y1={TOP_H - 4}
                y2={height - AXIS_H}
                stroke="var(--bb-chart-grid-muted)"
                stroke-width={1}
              />
              <text
                x={x}
                y={TOP_H - 9}
                text-anchor={anchor}
                font-size={TICK_FONT_PX}
                fill="var(--bb-chart-label-muted)"
              >
                {tickText}
              </text>
            </g>
          );
        })}
        {sortedQueryIds.map((queryId, queryIndex) => {
          const rowTop = TOP_H + queryIndex * ROW_H;
          const rowCenter = rowTop + ROW_H / 2;
          const fullLabel = queryDisplayLabel(queryId);
          const fitted = fitLabel(fullLabel, labelW - LABEL_GAP_PX, LABEL_FONT_PX);
          const label = (
            <text
              x={labelW - LABEL_GAP_PX}
              y={rowCenter + 3}
              text-anchor="end"
              data-query-label={queryId}
              font-size={LABEL_FONT_PX}
              font-family="monospace"
              fill="var(--bb-chart-label-muted)"
            >
              {fitted.text}
              {fitted.truncated ? <title>{fullLabel}</title> : null}
            </text>
          );
          return (
            <g key={queryId} data-query-row={queryId}>
              <line
                x1={plotX}
                x2={plotRight}
                y1={rowTop + ROW_H}
                y2={rowTop + ROW_H}
                stroke="var(--bb-chart-grid-muted)"
                stroke-width={1}
              />
              {queryHref !== undefined
                ? (
                  <a href={queryHref(queryId)} aria-label={`Compare ${fullLabel} across selected runs`}>
                    {label}
                  </a>
                )
                : label}
              {platforms.map((platform, platformIndex) => {
                const ms = platformTimingValue(platform, queryId);
                const slot = platform.color_slot ?? platformIndex;
                const color = paletteColor(slot);
                if (ms === null || ms <= 0) {
                  return (
                    <circle
                      key={platform.result_id}
                      cx={labelW + MISSING_MARKER_CENTER_PX}
                      cy={rowCenter}
                      r={MISSING_MARKER_RADIUS}
                      fill="none"
                      stroke="var(--bb-chart-label-muted)"
                      stroke-width={1.5}
                      data-platform-marker={platform.result_id}
                    >
                      <title>{timingLabel(summary, platformIndex, queryId, runLabels[platformIndex] ?? platform.platform)}</title>
                    </circle>
                  );
                }
                return (
                  <path
                    key={platform.result_id}
                    d={runMarkerPath(slot, toX(ms), rowCenter, RUN_MARKER_RADIUS)}
                    fill={color}
                    stroke="var(--bb-surface-data)"
                    stroke-width={0.75}
                    data-platform-marker={platform.result_id}
                  >
                    <title>{timingLabel(summary, platformIndex, queryId, runLabels[platformIndex] ?? platform.platform)}</title>
                  </path>
                );
              })}
            </g>
          );
        })}
        <text
          x={plotX + plotWidth / 2}
          y={height - 5}
          text-anchor="middle"
          font-size="9"
          fill="var(--bb-chart-label-muted)"
        >
          Latency (ms, log scale)
        </text>
      </svg>
      <div class="sr-only">
      <table>
        <caption>Per-query latency by run, in milliseconds</caption>
        <thead>
          <tr><th>Query</th><th>Run</th><th>Latency</th></tr>
        </thead>
        <tbody>
          {sortedQueryIds.flatMap((queryId) => platforms.map((platform, platformIndex) => {
            const ms = platformTimingValue(platform, queryId);
            const cell = ms === null
              ? formatTimingExclusion(platform.timing_eligibility[queryId]?.timing_exclusion_reason ?? "missing_timing")
              : formatCompactLatencyMs(ms);
            return (
              <tr key={`${queryId}-${platform.result_id}`}>
                <td>{queryDisplayLabel(queryId)}</td>
                <td>{runLabels[platformIndex] ?? platform.platform}</td>
                <td>{cell}</td>
              </tr>
            );
          }))}
        </tbody>
      </table>
      </div>
      <RunLegend
        runs={platforms.map((platform, index) => ({
          key: platform.result_id,
          label: runLabels[index] ?? platform.platform,
          colorSlot: platform.color_slot ?? index,
        }))}
        showMissing={hasMissing}
      />
    </div>
  );
}
