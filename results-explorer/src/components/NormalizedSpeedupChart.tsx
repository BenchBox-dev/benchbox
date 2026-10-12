import { useState } from "preact/hooks";
import { speedupRatio } from "@/lib/chartMath";
import { isValidTimingValue } from "@/lib/displayEligibility";
import { formatSpeedup } from "@/lib/metricFormatters";
import {
  FASTER_FILL,
  SLOWER_FILL,
  SPEEDUP_GRID_STOPS,
  SPEEDUP_LOG2_MIN,
  SPEEDUP_LOG2_RANGE,
  paletteColor,
} from "@/lib/chartTheme";
import { useElementSize } from "@/lib/useElementSize";
import { chartFrame } from "@/lib/chartFrame";

function toX(speedup: number, width: number): number {
  const clamped = Math.max(0.2, Math.min(speedup, 5));
  return ((Math.log2(clamped) - SPEEDUP_LOG2_MIN) / SPEEDUP_LOG2_RANGE) * width;
}

interface SpeedupEntry {
  queryId: string;
  speedups: (number | null)[];
}

interface Props {
  queries: { queryId: string; timings: ({ ms: number; status: string } | null)[] }[];
  results: { platform: string }[];
  baselineIdx: number;
}

const ROW_H = 30;
const LABEL_W = 64;
const AXIS_H = 22;
const PADDING_RIGHT = 8;

function markerOffset(index: number, count: number): number {
  if (count <= 1) return 0;
  const spacing = Math.min(2.5, (ROW_H - 8) / (count - 1));
  return (index - (count - 1) / 2) * spacing;
}

function missingMarkerPath(x: number, y: number): string {
  return `M${x - 2.5},${y - 2.5} L${x + 2.5},${y + 2.5} M${x + 2.5},${y - 2.5} L${x - 2.5},${y + 2.5}`;
}

export function NormalizedSpeedupChart({ queries, results, baselineIdx }: Props) {
  const [containerRef, { width: containerWidth }] = useElementSize();
  const drawWidth = chartFrame(containerWidth, { minWidth: 300 }).width;
  const [showComparableOnly, setShowComparableOnly] = useState(true);

  if (queries.length === 0 || results.length < 2) return null;

  const baselineIndex = baselineIdx >= 0 && baselineIdx < results.length ? baselineIdx : 0;
  const baselineLabel = results[baselineIndex]?.platform ?? "the selected baseline";
  const allEntries: SpeedupEntry[] = queries.map(({ queryId, timings }) => {
    const baselineT = timings[baselineIndex];
    const baselineMs = isValidTimingValue(baselineT?.ms) ? baselineT.ms : null;
    const speedups = timings
      .filter((_, index) => index !== baselineIndex)
      .map((timing) => speedupRatio(baselineMs, timing?.ms ?? null));
    return { queryId, speedups };
  });
  const fullyComparableEntries = allEntries.filter((entry) =>
    entry.speedups.every((speedup) => speedup !== null),
  );
  const entries = showComparableOnly && fullyComparableEntries.length > 0
    ? fullyComparableEntries
    : allEntries;
  const hiddenEntryCount = allEntries.length - entries.length;
  const nonBaselineResults = results.filter((_, index) => index !== baselineIndex);
  const measuredSpeedups = entries
    .flatMap((entry) => entry.speedups)
    .filter((speedup): speedup is number => speedup !== null);
  const hasMissingSpeedups = entries.some((entry) => entry.speedups.some((speedup) => speedup === null));
  const allNearEqual =
    !hasMissingSpeedups &&
    measuredSpeedups.length > 0 &&
    measuredSpeedups.every((speedup) => Math.abs(speedup - 1) <= 0.01);
  const chartDescription =
    `For each query, speedup is baseline latency divided by candidate latency relative to ${baselineLabel}. ` +
    "Values above 1 are faster; values below 1 are slower. Queries without valid timing on every run are hidden by default. " +
    `The horizontal scale runs from 0.2× to 5×; values beyond the edges are clipped, with exact ratios in the tooltip and table. ` +
    `${entries.length} queries are shown. An accessible table follows the chart.`;
  const plotWidth = Math.max(1, drawWidth - LABEL_W - PADDING_RIGHT);
  const totalHeight = AXIS_H + entries.length * ROW_H + 4;

  if (allNearEqual && hiddenEntryCount === 0) {
    return (
      <SpeedupParityState
        baseline={baselineLabel}
        candidates={nonBaselineResults.map((result) => result.platform)}
        speedupCount={measuredSpeedups.length}
        queryCount={entries.length}
      />
    );
  }

  return (
    <div ref={containerRef} class="w-full overflow-x-auto">
      <p id="normalized-speedup-description" class="sr-only">{chartDescription}</p>
      {(hiddenEntryCount > 0 || (!showComparableOnly && fullyComparableEntries.length < allEntries.length)) && (
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--bb-data-fg-muted)]">
          <span>
            {showComparableOnly
              ? `Showing ${entries.length} fully comparable queries (${hiddenEntryCount} hidden — at least one run missing data).`
              : `Showing all ${entries.length} queries; ${allEntries.length - fullyComparableEntries.length} have missing data.`}
          </span>
          <label class="inline-flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={showComparableOnly}
              onChange={(event) => setShowComparableOnly((event.target as HTMLInputElement).checked)}
              data-testid="normalized-speedup-comparable-only-toggle"
              class="h-3.5 w-3.5 rounded border-[var(--bb-data-border-strong)]"
            />
            <span>Comparable only</span>
          </label>
        </div>
      )}
      <svg
        class="bb-chart-svg"
        width="100%"
        height={totalHeight}
        viewBox={`0 0 ${drawWidth} ${totalHeight}`}
        role="img"
        aria-label={`Per-query speedups relative to ${baselineLabel}`}
        aria-describedby="normalized-speedup-description"
        data-row-layout="query"
      >
        {SPEEDUP_GRID_STOPS.map((stop) => {
          const x = LABEL_W + toX(stop, plotWidth);
          return (
            <g key={stop}>
              <line
                x1={x}
                y1={AXIS_H - 2}
                x2={x}
                y2={totalHeight - 3}
                stroke={stop === 1 ? "var(--bb-chart-axis)" : "var(--bb-chart-grid)"}
                stroke-width={stop === 1 ? 1.5 : 1}
                stroke-dasharray={stop === 1 ? "none" : "3 3"}
              />
              <text
                x={x}
                y={12}
                text-anchor={stop === 0.2 ? "start" : stop === 5 ? "end" : "middle"}
                font-size="9"
                fill="var(--bb-chart-label-muted)"
              >
                {formatSpeedup(stop, { unit: "×" }).valueText}
              </text>
            </g>
          );
        })}

        {entries.map(({ queryId, speedups }, rowIndex) => {
          const rowTop = AXIS_H + rowIndex * ROW_H;
          const rowCenter = rowTop + ROW_H / 2;
          return (
            <g key={`${queryId}-${rowIndex}`} data-query-row={queryId}>
              <text
                x={LABEL_W - 5}
                y={rowCenter + 3}
                text-anchor="end"
                font-size="9"
                fill="var(--bb-chart-axis)"
                font-family="monospace"
              >
                {queryId}
              </text>
              <line
                x1={LABEL_W}
                x2={drawWidth - PADDING_RIGHT}
                y1={rowTop + ROW_H}
                y2={rowTop + ROW_H}
                stroke="var(--bb-chart-grid-muted)"
                stroke-width={1}
              />
              {speedups.map((speedup, candidateIndex) => {
                const x = speedup === null
                  ? LABEL_W + toX(1, plotWidth)
                  : LABEL_W + toX(speedup, plotWidth);
                const y = rowCenter + markerOffset(candidateIndex, speedups.length);
                const color = paletteColor(candidateIndex < baselineIndex ? candidateIndex : candidateIndex + 1);
                const candidateName = nonBaselineResults[candidateIndex]?.platform ?? "Candidate";
                if (speedup === null) {
                  return (
                    <path
                      key={candidateIndex}
                      d={missingMarkerPath(x, y)}
                      fill="none"
                      stroke="var(--bb-chart-label-muted)"
                      stroke-width={1.5}
                      data-platform-marker={candidateName}
                    >
                      <title>{`${candidateName} · ${queryId}: comparable timing unavailable`}</title>
                    </path>
                  );
                }
                return (
                  <circle
                    key={candidateIndex}
                    cx={x}
                    cy={y}
                    r={2.5}
                    fill={color}
                    stroke="var(--bb-surface-data)"
                    stroke-width={0.75}
                    data-platform-marker={candidateName}
                    data-speedup-direction={speedup > 1 ? "faster" : speedup < 1 ? "slower" : "equal"}
                  >
                    <title>
                      {`${candidateName} compared with ${baselineLabel}, ${queryId}: ` +
                        `${formatSpeedup(speedup, { unit: "×" }).valueText}, ` +
                        `${speedup > 1 ? "faster" : speedup < 1 ? "slower" : "equal"} than baseline`}
                    </title>
                  </circle>
                );
              })}
            </g>
          );
        })}
      </svg>

      <table class="sr-only">
        <caption>Per-query speedups relative to {baselineLabel}</caption>
        <thead>
          <tr><th>Query</th><th>Candidate</th><th>Speedup</th></tr>
        </thead>
        <tbody>
          {entries.flatMap((entry, entryIndex) =>
            entry.speedups.map((speedup, candidateIndex) => (
              <tr key={`${entry.queryId}-${entryIndex}-${candidateIndex}`}>
                <td>{entry.queryId}</td>
                <td>{nonBaselineResults[candidateIndex]?.platform ?? "Candidate"}</td>
                <td>
                  {speedup === null
                    ? "Not available: comparable timing unavailable"
                    : formatSpeedup(speedup, { unit: "×" }).accessibleText}
                </td>
              </tr>
            )),
          )}
        </tbody>
      </table>

      <div class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-[var(--bb-data-fg-muted)]" aria-label="Run legend">
        <span>Baseline: <strong>{baselineLabel}</strong></span>
        {nonBaselineResults.map((result, index) => {
          const colorIndex = index < baselineIndex ? index : index + 1;
          return (
            <span key={`${result.platform}-${index}`} class="flex items-center gap-1">
              <span class="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: paletteColor(colorIndex) }} />
              {result.platform}
            </span>
          );
        })}
        <span class="ml-1">
          Values to the right of 1× are faster; values to the left are slower.
        </span>
        <span class="flex items-center gap-1">
          <span class="inline-block h-2 w-3 rounded-sm" style={{ backgroundColor: FASTER_FILL }} />
          faster than baseline
        </span>
        <span class="flex items-center gap-1">
          <span class="inline-block h-2 w-3 rounded-sm" style={{ backgroundColor: SLOWER_FILL }} />
          slower than baseline
        </span>
      </div>
    </div>
  );
}

function SpeedupParityState({
  baseline,
  candidates,
  speedupCount,
  queryCount,
}: {
  baseline: string;
  candidates: string[];
  speedupCount: number;
  queryCount: number;
}) {
  return (
    <div class="rounded-lg panel-muted p-4 text-sm">
      <p class="font-semibold text-[var(--bb-data-fg-primary)]">No meaningful per-query difference</p>
      <p class="mt-1 text-[var(--bb-data-fg-muted)]">
        All {queryCount.toLocaleString()} compared queries ({speedupCount.toLocaleString()} query-run pairs) are
        within 1% of 1.00× relative to {baseline}.
      </p>
      <p class="mt-2 text-xs text-[var(--bb-data-fg-muted)]">
        Baseline: <strong>{baseline}</strong>
        {candidates.length > 0 && <> · Compared with {candidates.join(", ")}</>}
      </p>
    </div>
  );
}
