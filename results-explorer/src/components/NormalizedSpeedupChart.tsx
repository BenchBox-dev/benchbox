import { useState } from "preact/hooks";
import { speedupRatio } from "@/lib/chartMath";
import { isValidTimingValue } from "@/lib/displayEligibility";
import { formatSpeedup, formatRatio } from "@/lib/metricFormatters";
import { assignRunColorSlots, paletteColor } from "@/lib/chartTheme";
import { MISSING_MARKER_RADIUS, RUN_MARKER_RADIUS, runMarkerPath } from "@/lib/chartMarkers";
import { RunLegend } from "@/components/RunLegend";
import { useElementSize } from "@/lib/useElementSize";
import { chartFrame } from "@/lib/chartFrame";
import { estimateTextWidth, fitLabel, labelColumnWidth } from "@/lib/chartLabels";
import { formatRunIdentitiesForCohort, type RunIdentitySource } from "@/lib/runIdentity";
import { queryDisplayLabel } from "@/lib/queryLabels";

export interface SpeedupDomain {
  lo: number;
  hi: number;
}

function niceAtOrBelow(value: number): number {
  const exponent = Math.floor(Math.log10(value));
  const candidates = [0.5, 1, 2, 5].map((mantissa) => Number((mantissa * 10 ** exponent).toPrecision(12)));
  return Math.max(...candidates.filter((candidate) => candidate <= value * (1 + 1e-9)));
}

function niceAtOrAbove(value: number): number {
  const exponent = Math.floor(Math.log10(value));
  const candidates = [1, 2, 5, 10].map((mantissa) => Number((mantissa * 10 ** exponent).toPrecision(12)));
  return candidates.find((candidate) => candidate >= value * (1 - 1e-9)) ?? 10 ** (exponent + 1);
}

export function speedupDomainForValues(values: readonly number[]): SpeedupDomain {
  const measured = values.filter((value) => Number.isFinite(value) && value > 0);
  if (measured.length === 0) return { lo: 0.5, hi: 2 };
  const lo = niceAtOrBelow(Math.min(1, ...measured));
  let hi = niceAtOrAbove(Math.max(1, ...measured));
  if (hi <= lo) hi = lo * 10;
  return { lo, hi };
}

function niceRatioStops(domain: SpeedupDomain): number[] {
  const stops: number[] = [];
  for (let exponent = Math.floor(Math.log10(domain.lo)); exponent <= Math.ceil(Math.log10(domain.hi)); exponent += 1) {
    for (const mantissa of [1, 2, 5]) {
      const value = Number((mantissa * 10 ** exponent).toPrecision(12));
      if (value >= domain.lo * (1 - 1e-9) && value <= domain.hi * (1 + 1e-9)) stops.push(value);
    }
  }
  return stops;
}

export function ratioTicks(domain: SpeedupDomain, plotWidthPx: number, minGapPx = 56): number[] {
  const pxPerDecade = plotWidthPx / (Math.log10(domain.hi) - Math.log10(domain.lo));
  const thin = (candidates: number[], anchor: number): number[] => {
    const kept: number[] = [];
    let last = anchor;
    for (const candidate of candidates) {
      if (Math.abs(Math.log10(candidate) - Math.log10(last)) * pxPerDecade >= minGapPx) {
        kept.push(candidate);
        last = candidate;
      }
    }
    return kept;
  };
  const stops = niceRatioStops(domain);
  const anchor = stops.includes(1) ? 1 : (stops[0] ?? domain.lo);
  const above = thin(stops.filter((stop) => stop > anchor), anchor);
  const below = thin(stops.filter((stop) => stop < anchor).reverse(), anchor).reverse();
  return [...below, anchor, ...above];
}

export function formatRatioTick(value: number): string {
  return formatRatio(value);
}

interface SpeedupEntry {
  queryId: string;
  speedups: (number | null)[];
}

export interface SpeedupRunInput extends Partial<RunIdentitySource> {
  platform: string;
  platform_id?: string | null;
}

interface Props {
  queries: { queryId: string; timings: ({ ms: number; status: string } | null)[] }[];
  results: readonly SpeedupRunInput[];
  baselineIdx: number;
  queryHref?: (queryId: string) => string;
}

const ROW_H = 28;
const TOP_H = 30;
const AXIS_H = 26;
const LABEL_FONT_PX = 10;
const LABEL_GAP_PX = 8;
const TICK_FONT_PX = 9;
const AXIS_TEXT_FONT_PX = 10;
const MARKER_OVERHANG_PX = 8;
const MISSING_GUTTER_PX = 24;
const MISSING_MARKER_CENTER_PX = 8;
const PLOT_RIGHT_PAD_PX = 12;

export function NormalizedSpeedupChart({ queries, results, baselineIdx, queryHref }: Props) {
  const [containerRef, { width: containerWidth }] = useElementSize();
  const drawWidth = chartFrame(containerWidth, { minWidth: 300 }).width;
  const [showComparableOnly, setShowComparableOnly] = useState(true);

  if (queries.length === 0 || results.length < 2) return null;

  const runLabels = formatRunIdentitiesForCohort(
    results.map((run, index) => ({ ...run, result_id: run.result_id ?? `run-${index}` })),
    "chart",
  );
  const colorSlots = assignRunColorSlots(
    results.map((run, index) => ({ result_id: String(index), platform_id: run.platform_id, platform: run.platform })),
  );
  const colorSlotOf = (runIndex: number): number => colorSlots.get(String(runIndex)) ?? runIndex;
  const baselineIndex = baselineIdx >= 0 && baselineIdx < results.length ? baselineIdx : 0;
  const baselineLabel = runLabels[baselineIndex] ?? "the selected baseline";
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
  const nonBaselineIndices = results.map((_, index) => index).filter((index) => index !== baselineIndex);
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
  const domainText = `${formatRatioTick(domain.lo)} to ${formatRatioTick(domain.hi)}`;
  const chartDescription =
    `For each query, speedup is baseline latency divided by candidate latency relative to ${baselineLabel}. ` +
    "Values above 1 are faster; values below 1 are slower. Queries without valid timing on every run are hidden by default. " +
    `The horizontal log scale runs from ${domainText} and includes every measured speedup; ` +
    `exact ratios are in the tooltip and table. ` +
    `${entries.length} queries are shown. An accessible table follows the chart.`;

  if (allNearEqual && hiddenEntryCount === 0) {
    return (
      <SpeedupParityState
        baseline={baselineLabel}
        candidates={nonBaselineIndices.map((index) => runLabels[index] ?? "Candidate")}
        speedupCount={measuredSpeedups.length}
        queryCount={entries.length}
      />
    );
  }

  const labelW = labelColumnWidth(entries.map((entry) => queryDisplayLabel(entry.queryId)), LABEL_FONT_PX, drawWidth);
  const plotX = labelW + (hasMissingSpeedups ? MISSING_GUTTER_PX : MARKER_OVERHANG_PX);
  const plotRight = drawWidth - PLOT_RIGHT_PAD_PX;
  const plotWidth = Math.max(1, plotRight - plotX);
  const toX = (speedup: number): number =>
    plotX +
    ((Math.log10(speedup) - Math.log10(domain.lo)) / (Math.log10(domain.hi) - Math.log10(domain.lo))) * plotWidth;
  const ticks = ratioTicks(domain, plotWidth);
  const totalHeight = TOP_H + entries.length * ROW_H + AXIS_H;

  const directionLabels =
    estimateTextWidth("← slower than baseline", AXIS_TEXT_FONT_PX) +
      estimateTextWidth("faster than baseline →", AXIS_TEXT_FONT_PX) +
      16 <=
    plotRight - plotX
      ? { slower: "← slower than baseline", faster: "faster than baseline →" }
      : { slower: "← slower", faster: "faster →" };

  return (
    <div ref={containerRef} class="w-full">
      <p id="normalized-speedup-description" class="sr-only">{chartDescription}</p>
      {(hiddenEntryCount > 0 || (!showComparableOnly && fullyComparableEntries.length < allEntries.length)) && (
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--bb-data-fg-muted)]">
          <span>
            {showComparableOnly
              ? `Showing ${entries.length} of ${allEntries.length} queries shared by every selected run; ${hiddenEntryCount} hidden because at least one run is missing data.`
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
        data-plot-start={plotX}
      >
        {ticks.map((tick) => {
          const x = toX(tick);
          const tickText = formatRatioTick(tick);
          const halfTextWidth = estimateTextWidth(tickText, TICK_FONT_PX) / 2;
          const anchor = x - plotX < halfTextWidth ? "start" : plotRight - x < halfTextWidth ? "end" : "middle";
          return (
            <g key={tick}>
              <line
                x1={x}
                y1={TOP_H - 4}
                x2={x}
                y2={totalHeight - AXIS_H}
                stroke={tick === 1 ? "var(--bb-chart-axis)" : "var(--bb-chart-grid-muted)"}
                stroke-width={tick === 1 ? 1.5 : 1}
                stroke-dasharray={tick === 1 ? "none" : "3 3"}
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

        {entries.map(({ queryId, speedups }, queryIndex) => {
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
            <g key={`${queryIndex}-${queryId}`} data-query-row={queryId}>
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
              {speedups.map((speedup, candidateIndex) => {
                const runIndex = runIndexForCandidate(candidateIndex);
                const runLabel = runLabels[runIndex] ?? "Candidate";
                if (speedup === null) {
                  return (
                    <circle
                      key={candidateIndex}
                      cx={labelW + MISSING_MARKER_CENTER_PX}
                      cy={rowCenter}
                      r={MISSING_MARKER_RADIUS}
                      fill="none"
                      stroke="var(--bb-chart-label-muted)"
                      stroke-width={1.5}
                      data-platform-marker={runLabel}
                    >
                      <title>{`${runLabel} · ${fullLabel}: comparable timing unavailable`}</title>
                    </circle>
                  );
                }
                const direction = speedup > 1 ? "faster" : speedup < 1 ? "slower" : "equal";
                return (
                  <path
                    key={candidateIndex}
                    d={runMarkerPath(colorSlotOf(runIndex), toX(speedup), rowCenter, RUN_MARKER_RADIUS)}
                    fill={paletteColor(colorSlotOf(runIndex))}
                    stroke="var(--bb-surface-data)"
                    stroke-width={0.75}
                    data-platform-marker={runLabel}
                    data-speedup-direction={direction}
                  >
                    <title>
                      {`${runLabel} compared with ${baselineLabel}, ${fullLabel}: ` +
                        `${formatSpeedup(speedup).valueText}, ${direction} than baseline`}
                    </title>
                  </path>
                );
              })}
            </g>
          );
        })}

        <text
          x={plotX}
          y={totalHeight - 8}
          text-anchor="start"
          font-size={AXIS_TEXT_FONT_PX}
          fill="var(--bb-chart-label-muted)"
          data-axis-direction="slower"
        >
          {directionLabels.slower}
        </text>
        <text
          x={plotRight}
          y={totalHeight - 8}
          text-anchor="end"
          font-size={AXIS_TEXT_FONT_PX}
          fill="var(--bb-chart-label-muted)"
          data-axis-direction="faster"
        >
          {directionLabels.faster}
        </text>
      </svg>
      <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]" data-testid="normalized-speedup-domain">
        Log scale {domainText}; every measured speedup is shown.
      </p>

      <div class="sr-only">
      <table>
        <caption>Per-query speedups relative to {baselineLabel}</caption>
        <thead>
          <tr><th>Query</th><th>Candidate</th><th>Speedup</th></tr>
        </thead>
        <tbody>
          {entries.flatMap((entry, entryIndex) =>
            entry.speedups.map((speedup, candidateIndex) => (
              <tr key={`${entryIndex}-${entry.queryId}-${candidateIndex}`}>
                <td>{entry.queryId}</td>
                <td>{runLabels[runIndexForCandidate(candidateIndex)] ?? "Candidate"}</td>
                <td>
                  {speedup === null
                    ? "Not available: comparable timing unavailable"
                    : formatSpeedup(speedup).accessibleText}
                </td>
              </tr>
            )),
          )}
        </tbody>
      </table>
      </div>

      <div class="mt-2 text-xs text-[var(--bb-data-fg-muted)]">
        <span>Baseline: <strong>{baselineLabel}</strong></span>
        <span class="ml-3">Values to the right of 1× are faster; values to the left are slower.</span>
      </div>
      <RunLegend
        runs={nonBaselineIndices.map((resultIndex) => ({
          key: `${runLabels[resultIndex] ?? "run"}-${resultIndex}`,
          label: runLabels[resultIndex] ?? "Candidate",
          colorSlot: colorSlotOf(resultIndex),
        }))}
        showMissing={hasMissingSpeedups}
      />
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
        within 1% of 1× relative to {baseline}.
      </p>
      <p class="mt-2 text-xs text-[var(--bb-data-fg-muted)]">
        Baseline: <strong>{baseline}</strong>
        {candidates.length > 0 && <> · Compared with {candidates.join(", ")}</>}
      </p>
    </div>
  );
}
