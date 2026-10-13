import { formatRunIdentitiesForCohort } from "@/lib/runIdentity";
import type { BenchmarkSummary } from "@/types";
import { useElementSize } from "@/lib/useElementSize";
import { chartFrame } from "@/lib/chartFrame";
import { paletteColor } from "@/lib/chartTheme";
import { runMarkerPath } from "@/lib/chartMarkers";
import { RunLegend } from "@/components/RunLegend";
import { queryDisplayLabel, sortQueryIds } from "@/lib/queryLabels";
import { formatTimingExclusion, platformTimingValue } from "@/lib/displayEligibility";
import { formatLatencyMs } from "@/lib/metricFormatters";

const LABEL_W = 132;
const ROW_H = 28;
const TOP_H = 30;
const AXIS_H = 26;
const MARKER_RADIUS = 4.5;
const MISSING_MARKER_RADIUS = 3.5;

interface Props {
  summary: BenchmarkSummary;
  preserveOrder?: boolean;
  queryHref?: (queryId: string) => string;
}

function fmtMs(ms: number): string {
  return formatLatencyMs(ms, { subMillisecond: "compact" }).valueText;
}

function timingLabel(summary: BenchmarkSummary, platformIndex: number, queryId: string, runLabel: string): string {
  const platform = summary.platforms[platformIndex]!;
  const ms = platformTimingValue(platform, queryId);
  const value = ms === null
    ? formatTimingExclusion(platform.timing_eligibility[queryId]?.timing_exclusion_reason ?? "missing_timing")
    : fmtMs(ms);
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

function scaleTicks(scale: LogScale, plotWidth: number): number[] {
  const candidates = [scale.lo, scale.hi];
  for (let exponent = Math.ceil(scale.logLo); exponent <= Math.floor(scale.logHi); exponent += 1) {
    candidates.push(10 ** exponent);
  }
  const ordered = [...new Set(candidates)].sort((a, b) => a - b);
  const kept: number[] = [];
  for (const tick of ordered) {
    const position = (Math.log10(tick) - scale.logLo) / (scale.logHi - scale.logLo);
    if (kept.every((keptTick) => {
      const keptPosition = (Math.log10(keptTick) - scale.logLo) / (scale.logHi - scale.logLo);
      return Math.abs(position - keptPosition) * plotWidth >= 44;
    })) {
      kept.push(tick);
    }
  }
  return kept;
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
  const plotX = LABEL_W;
  const plotWidth = Math.max(1, width - plotX - 12);
  const toX = (ms: number): number =>
    plotX + ((Math.log10(ms) - scale.logLo) / (scale.logHi - scale.logLo)) * plotWidth;
  const height = TOP_H + sortedQueryIds.length * ROW_H + AXIS_H;

  return (
    <div ref={containerRef} class="w-full">
      <p id="query-histogram-description" class="sr-only">
        Each row is one query. Every run shares the same query row; marker shape and color identify runs
        and horizontal position shows latency on a logarithmic millisecond scale from {fmtMs(scale.lo)} to{" "}
        {fmtMs(scale.hi)}. Tooltips and the accessible table give each run's timing or exclusion reason.
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
      >
        {scaleTicks(scale, plotWidth).map((tick) => {
          const x = toX(tick);
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
                text-anchor="middle"
                font-size="9"
                fill="var(--bb-chart-label-muted)"
              >
                {fmtMs(tick)}
              </text>
            </g>
          );
        })}
        {sortedQueryIds.map((queryId, queryIndex) => {
          const rowTop = TOP_H + queryIndex * ROW_H;
          const rowCenter = rowTop + ROW_H / 2;
          const label = (
            <text
              x={plotX - 8}
              y={rowCenter + 3}
              text-anchor="end"
              data-query-label={queryId}
              font-size="10"
              font-family="monospace"
              fill="var(--bb-chart-label-muted)"
            >
              {queryDisplayLabel(queryId)}
              <title>{queryDisplayLabel(queryId)}</title>
            </text>
          );
          return (
            <g key={queryId} data-query-row={queryId}>
              <line
                x1={plotX}
                x2={plotX + plotWidth}
                y1={rowTop + ROW_H}
                y2={rowTop + ROW_H}
                stroke="var(--bb-chart-grid-muted)"
                stroke-width={1}
              />
              {queryHref !== undefined
                ? (
                  <a href={queryHref(queryId)} aria-label={`Compare ${queryDisplayLabel(queryId)} across selected runs`}>
                    {label}
                  </a>
                )
                : label}
              {platforms.map((platform, platformIndex) => {
                const ms = platformTimingValue(platform, queryId);
                const color = paletteColor(platformIndex);
                if (ms === null || ms <= 0) {
                  return (
                    <circle
                      key={platform.result_id}
                      cx={plotX}
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
                    d={runMarkerPath(platformIndex, toX(ms), rowCenter, MARKER_RADIUS)}
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
      <table class="sr-only">
        <caption>Per-query latency by run, in milliseconds</caption>
        <thead>
          <tr><th>Query</th><th>Run</th><th>Latency</th></tr>
        </thead>
        <tbody>
          {sortedQueryIds.flatMap((queryId) => platforms.map((platform, platformIndex) => {
            const ms = platformTimingValue(platform, queryId);
            const cell = ms === null
              ? formatTimingExclusion(platform.timing_eligibility[queryId]?.timing_exclusion_reason ?? "missing_timing")
              : fmtMs(ms);
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
      <RunLegend
        runs={platforms.map((platform, index) => ({
          key: platform.result_id,
          label: runLabels[index] ?? platform.platform,
          runIndex: index,
        }))}
      />
    </div>
  );
}
