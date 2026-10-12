import { formatRunIdentitiesForCohort } from "@/lib/runIdentity";
import type { BenchmarkSummary } from "@/types";
import { useElementSize } from "@/lib/useElementSize";
import { chartFrame } from "@/lib/chartFrame";
import { paletteColor } from "@/lib/chartTheme";
import { queryDisplayLabel, sortQueryIds } from "@/lib/queryLabels";
import { formatTimingExclusion, platformTimingValue } from "@/lib/displayEligibility";
import { formatLatencyMs } from "@/lib/metricFormatters";

const MAX_PER_PANEL = 33;
const LABEL_W = 64;
const ROW_H = 40;
const TOP_H = 30;
const AXIS_H = 24;
const MARKER_RADIUS = 2.25;

interface Props {
  summary: BenchmarkSummary;
  preserveOrder?: boolean;
}

function fmtMs(ms: number): string {
  return formatLatencyMs(ms, { subMillisecond: "compact" }).valueText;
}

function markerOffset(index: number, count: number): number {
  if (count <= 1) return 0;
  const spacing = Math.min(3.5, (ROW_H - 2 * MARKER_RADIUS - 2) / (count - 1));
  return (index - (count - 1) / 2) * spacing;
}

function timingLabel(summary: BenchmarkSummary, platformIndex: number, queryId: string, runLabel: string): string {
  const platform = summary.platforms[platformIndex]!;
  const ms = platformTimingValue(platform, queryId);
  const value = ms === null
    ? formatTimingExclusion(platform.timing_eligibility[queryId]?.timing_exclusion_reason ?? "missing_timing")
    : fmtMs(ms);
  return `${runLabel} · ${queryDisplayLabel(queryId)}: ${value}`;
}

export function QueryHistogram({ summary, preserveOrder = false }: Props) {
  const [containerRef, { width: containerWidth }] = useElementSize(300);
  const width = chartFrame(containerWidth, { minWidth: 300 }).width;
  const { platforms, query_ids } = summary;

  if (platforms.length === 0 || query_ids.length === 0) return null;

  const runLabels = formatRunIdentitiesForCohort(platforms, "chart");
  const sortedQueryIds = preserveOrder ? query_ids : sortQueryIds(query_ids);
  const maxMs = Math.max(1, ...platforms.flatMap((platform) =>
    sortedQueryIds.map((queryId) => platformTimingValue(platform, queryId) ?? 0),
  ));
  const plotX = LABEL_W;
  const plotWidth = Math.max(1, width - plotX - 12);
  const queryPanels: string[][] = [];
  for (let index = 0; index < sortedQueryIds.length; index += MAX_PER_PANEL) {
    queryPanels.push(sortedQueryIds.slice(index, index + MAX_PER_PANEL));
  }

  return (
    <div ref={containerRef} class="w-full">
      <p id="query-histogram-description" class="sr-only">
        Each row is one query. Marker colors identify runs and horizontal position shows latency in milliseconds.
        Tooltips and the accessible table give each run's timing or exclusion reason.
      </p>
      {queryPanels.map((queryIds, panelIndex) => {
        const height = TOP_H + queryIds.length * ROW_H + AXIS_H;
        const firstQuery = panelIndex * MAX_PER_PANEL + 1;
        const lastQuery = Math.min(firstQuery + queryIds.length - 1, sortedQueryIds.length);
        return (
          <div key={panelIndex} class="mb-3">
            {queryPanels.length > 1 && (
              <p class="mb-0.5 ml-16 text-[10px] text-[var(--bb-data-fg-subtle)]">
                Queries {firstQuery}-{lastQuery}
              </p>
            )}
            <svg
              class="bb-chart-svg"
              width="100%"
              height={height}
              viewBox={`0 0 ${width} ${height}`}
              role="img"
              aria-label={`Horizontal query latency chart${queryPanels.length > 1 ? `, panel ${panelIndex + 1}` : ""}`}
              aria-describedby="query-histogram-description"
              data-orientation="horizontal"
            >
              {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
                const x = plotX + fraction * plotWidth;
                return (
                  <g key={fraction}>
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
                      text-anchor={fraction === 1 ? "end" : fraction === 0 ? "start" : "middle"}
                      font-size="9"
                      fill="var(--bb-chart-label-muted)"
                    >
                      {fmtMs(fraction * maxMs)}
                    </text>
                  </g>
                );
              })}
              {queryIds.map((queryId, queryIndex) => {
                const rowTop = TOP_H + queryIndex * ROW_H;
                const rowCenter = rowTop + ROW_H / 2;
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
                    <text
                      x={plotX - 8}
                      y={rowCenter + 3}
                      text-anchor="end"
                      data-query-label={queryId}
                      font-size="10"
                      fill="var(--bb-chart-label-muted)"
                    >
                      {queryDisplayLabel(queryId)}
                    </text>
                    {platforms.map((platform, platformIndex) => {
                      const ms = platformTimingValue(platform, queryId);
                      const color = paletteColor(platformIndex);
                      const y = rowCenter + markerOffset(platformIndex, platforms.length);
                      if (ms === null) {
                        return (
                          <path
                            key={platform.result_id}
                            d={`M${plotX - 2},${y - MARKER_RADIUS} L${plotX + 2},${y + MARKER_RADIUS} M${plotX + 2},${y - MARKER_RADIUS} L${plotX - 2},${y + MARKER_RADIUS}`}
                            fill="none"
                            stroke={color}
                            stroke-width={1.5}
                            data-platform-marker={platform.result_id}
                          >
                            <title>{timingLabel(summary, platformIndex, queryId, runLabels[platformIndex] ?? platform.platform)}</title>
                          </path>
                        );
                      }
                      const x = plotX + (ms / maxMs) * plotWidth;
                      return (
                        <circle
                          key={platform.result_id}
                          cx={x}
                          cy={y}
                          r={MARKER_RADIUS}
                          fill={color}
                          stroke="var(--bb-surface-data)"
                          stroke-width={0.75}
                          data-platform-marker={platform.result_id}
                        >
                          <title>{timingLabel(summary, platformIndex, queryId, runLabels[platformIndex] ?? platform.platform)}</title>
                        </circle>
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
                Latency (ms)
              </text>
            </svg>
          </div>
        );
      })}
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
      <div
        class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-[var(--bb-data-fg-muted)]"
        role="group"
        aria-label="Run legend"
      >
        {platforms.map((platform, index) => (
          <span key={platform.result_id} class="flex items-center gap-1">
            <span class="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: paletteColor(index) }} />
            {runLabels[index] ?? platform.platform}
          </span>
        ))}
      </div>
    </div>
  );
}
