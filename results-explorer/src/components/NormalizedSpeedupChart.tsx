import { useState } from "preact/hooks";
import { speedupRatio } from "@/lib/chartMath";
import { isValidTimingValue } from "@/lib/displayEligibility";
import { formatSpeedup } from "@/lib/metricFormatters";
import { FASTER_FILL, SLOWER_FILL, paletteColor } from "@/lib/chartTheme";
import { runMarkerPath } from "@/lib/chartMarkers";
import { RunLegend } from "@/components/RunLegend";
import { useElementSize } from "@/lib/useElementSize";
import { chartFrame } from "@/lib/chartFrame";

export interface SpeedupDomain {
  lo: number;
  hi: number;
}

export function speedupDomainForValues(values: readonly number[]): SpeedupDomain {
  const measured = values.filter((value) => Number.isFinite(value) && value > 0);
  if (measured.length === 0) return { lo: 0.5, hi: 2 };
  const lo = Math.min(1, Math.min(...measured));
  const hi = Math.max(1, Math.max(...measured));
  const snappedLo = 2 ** Math.floor(Math.log2(lo));
  let snappedHi = 2 ** Math.ceil(Math.log2(hi));
  if (snappedHi <= snappedLo) snappedHi = snappedLo * 2;
  return { lo: snappedLo, hi: snappedHi };
}

export function speedupGridStops(domain: SpeedupDomain): number[] {
  const stops: number[] = [];
  for (
    let exponent = Math.round(Math.log2(domain.lo));
    2 ** exponent <= domain.hi * (1 + 1e-9);
    exponent += 1
  ) {
    stops.push(2 ** exponent);
  }
  return stops;
}

function toX(speedup: number, domain: SpeedupDomain, width: number): number {
  const logLo = Math.log2(domain.lo);
  return ((Math.log2(speedup) - logLo) / (Math.log2(domain.hi) - logLo)) * width;
}

interface SpeedupEntry {
  queryId: string;
  speedups: (number | null)[];
}

interface Props {
  queries: { queryId: string; timings: ({ ms: number; status: string } | null)[] }[];
  results: { platform: string }[];
  baselineIdx: number;
  queryHref?: (queryId: string) => string;
}

const ROW_H = 30;
const LABEL_W = 64;
const AXIS_H = 22;
const PADDING_RIGHT = 8;
const MARKER_RADIUS = 4.5;
const MISSING_MARKER_RADIUS = 3.5;

export function NormalizedSpeedupChart({ queries, results, baselineIdx, queryHref }: Props) {
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
  const runIndexForCandidate = (candidateIndex: number): number =>
    candidateIndex < baselineIndex ? candidateIndex : candidateIndex + 1;
  const measuredSpeedups = entries
    .flatMap((entry) => entry.speedups)
    .filter((speedup): speedup is number => speedup !== null);
  const hasMissingSpeedups = entries.some((entry) => entry.speedups.some((speedup) => speedup === null));
  const allNearEqual =
    !hasMissingSpeedups &&
    measuredSpeedups.length > 0 &&
    measuredSpeedups.every((speedup) => Math.abs(speedup - 1) <= 0.01);
  const domain = speedupDomainForValues(measuredSpeedups);
  const stops = speedupGridStops(domain);
  const domainText =
    `${formatSpeedup(domain.lo, { unit: "×" }).valueText} to ${formatSpeedup(domain.hi, { unit: "×" }).valueText}`;
  const chartDescription =
    `For each query, speedup is baseline latency divided by candidate latency relative to ${baselineLabel}. ` +
    "Values above 1 are faster; values below 1 are slower. Queries without valid timing on every run are hidden by default. " +
    `The horizontal log scale runs from ${domainText} and includes every measured speedup; ` +
    `exact ratios are in the tooltip and table. ` +
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
        role={queryHref === undefined ? "img" : "group"}
        aria-label={`Per-query speedups relative to ${baselineLabel}`}
        aria-describedby="normalized-speedup-description"
        data-row-layout="query"
        data-speedup-domain={`${domain.lo}:${domain.hi}`}
      >
        {stops.map((stop) => {
          const x = LABEL_W + toX(stop, domain, plotWidth);
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
                text-anchor={stop === domain.lo ? "start" : stop === domain.hi ? "end" : "middle"}
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
          const queryLabel = (
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
          );
          return (
            <g key={`${queryId}-${rowIndex}`} data-query-row={queryId}>
              {queryHref !== undefined
                ? (
                  <a href={queryHref(queryId)} aria-label={`Compare ${queryId} across selected runs`}>
                    {queryLabel}
                  </a>
                )
                : queryLabel}
              <line
                x1={LABEL_W}
                x2={drawWidth - PADDING_RIGHT}
                y1={rowTop + ROW_H}
                y2={rowTop + ROW_H}
                stroke="var(--bb-chart-grid-muted)"
                stroke-width={1}
              />
              {speedups.map((speedup, candidateIndex) => {
                const runIndex = runIndexForCandidate(candidateIndex);
                const color = paletteColor(runIndex);
                const candidateName = nonBaselineResults[candidateIndex]?.platform ?? "Candidate";
                if (speedup === null) {
                  return (
                    <circle
                      key={candidateIndex}
                      cx={LABEL_W + toX(1, domain, plotWidth)}
                      cy={rowCenter}
                      r={MISSING_MARKER_RADIUS}
                      fill="none"
                      stroke="var(--bb-chart-label-muted)"
                      stroke-width={1.5}
                      data-platform-marker={candidateName}
                    >
                      <title>{`${candidateName} · ${queryId}: comparable timing unavailable`}</title>
                    </circle>
                  );
                }
                return (
                  <path
                    key={candidateIndex}
                    d={runMarkerPath(runIndex, LABEL_W + toX(speedup, domain, plotWidth), rowCenter, MARKER_RADIUS)}
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
                  </path>
                );
              })}
            </g>
          );
        })}
      </svg>
      <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]" data-testid="normalized-speedup-domain">
        Log scale {domainText}; every measured speedup is shown.
      </p>

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
      </div>
      <RunLegend
        runs={nonBaselineResults.map((result, index) => ({
          key: `${result.platform}-${index}`,
          label: result.platform,
          runIndex: runIndexForCandidate(index),
        }))}
      />
      <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-[var(--bb-data-fg-muted)]">
        <span>
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
