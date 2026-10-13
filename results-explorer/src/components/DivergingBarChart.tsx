import { formatPercentChange } from "@/lib/metricFormatters";
import { deltaPct, sortByMagnitudeDesc } from "@/lib/chartMath";
import { assignRunColorSlots, DIVERGING_MAX_PCT, paletteColor } from "@/lib/chartTheme";
import { useElementSize } from "@/lib/useElementSize";
import { chartFrame } from "@/lib/chartFrame";
import { fitLabel, labelColumnWidth } from "@/lib/chartLabels";
import { formatRunIdentitiesForCohort, type RunIdentitySource } from "@/lib/runIdentity";
import { queryDisplayLabel } from "@/lib/queryLabels";
import { RunLegend } from "@/components/RunLegend";

interface DivergingEntry {
  queryId: string;
  runIndex: number;
  label: string;
  deltaPct: number;
}

export interface DivergingRunInput extends Partial<RunIdentitySource> {
  platform: string;
  platform_id?: string | null;
}

interface Props {
  queries: { queryId: string; timings: ({ ms: number; status: string } | null)[] }[];
  results: readonly DivergingRunInput[];
  baselineIdx: number;
}

const TEXT_FONT_PX = 10;
const LABEL_GAP_PX = 8;
const ROW_H = 10;
const BAR_H = 8;
const GROUP_PADDING_PX = 6;
const HEADER_H = 18;
const FOOTER_PAD_PX = 4;
const PLOT_EDGE_PX = 8;
const VALUE_RESERVE_PX = 72;
const OVERFLOW_ARROW_PX = 4;
const VALUE_GAP_PX = 4;

export function DivergingBarChart({ queries, results, baselineIdx }: Props) {
  const [containerRef, { width: containerWidth }] = useElementSize();
  const drawWidth = chartFrame(containerWidth, { minWidth: 300 }).width;

  if (queries.length === 0 || results.length < 2) return null;

  const runLabels = formatRunIdentitiesForCohort(
    results.map((run, index) => ({ ...run, result_id: run.result_id ?? `run-${index}` })),
    "chart",
  );
  const colorSlots = assignRunColorSlots(
    results.map((run, index) => ({ result_id: String(index), platform_id: run.platform_id, platform: run.platform })),
  );
  const colorSlotOf = (runIndex: number): number => colorSlots.get(String(runIndex)) ?? runIndex;
  const baselineLabel = runLabels[baselineIdx] ?? "the selected baseline";

  const rawEntries: DivergingEntry[] = [];
  for (const { queryId, timings } of queries) {
    const baselineT = timings[baselineIdx];
    const baselineMs = baselineT && baselineT.ms > 0 ? baselineT.ms : null;
    if (!baselineMs) continue;
    timings.forEach((t, i) => {
      if (i === baselineIdx || !t || t.ms <= 0) return;
      const dp = deltaPct(t.ms, baselineMs);
      if (dp === null) return;
      rawEntries.push({ queryId, runIndex: i, label: runLabels[i] ?? "Candidate", deltaPct: dp });
    });
  }

  const grouped = new Map<string, DivergingEntry[]>();
  for (const entry of rawEntries) {
    const group = grouped.get(entry.queryId) ?? [];
    group.push(entry);
    grouped.set(entry.queryId, group);
  }
  const sortedGroups = sortByMagnitudeDesc([...grouped.entries()]);

  const candidateCount = results.length - 1;
  const groupHeight = candidateCount * ROW_H + GROUP_PADDING_PX;
  const totalHeight = HEADER_H + sortedGroups.length * groupHeight + FOOTER_PAD_PX;
  const labelW = labelColumnWidth(
    sortedGroups.map(([queryId]) => queryDisplayLabel(queryId)),
    TEXT_FONT_PX,
    drawWidth,
  );
  const plotX = labelW + PLOT_EDGE_PX;
  const plotRight = drawWidth - PLOT_EDGE_PX;
  const centerX = (plotX + plotRight) / 2;
  const halfWidth = Math.max(1, (plotRight - plotX) / 2 - VALUE_RESERVE_PX);
  const largestMagnitude = rawEntries.reduce((max, entry) => Math.max(max, Math.abs(entry.deltaPct)), 0);
  const scaleMax = largestMagnitude > 0 ? Math.min(DIVERGING_MAX_PCT, largestMagnitude) : 1;
  const overflowCount = rawEntries.filter((entry) => Math.abs(entry.deltaPct) > scaleMax).length;

  const faster = rawEntries.filter((e) => e.deltaPct < 0).length;
  const slower = rawEntries.filter((e) => e.deltaPct > 0).length;
  const sorted = [...rawEntries].sort((a, b) => a.deltaPct - b.deltaPct);
  const medianDelta = sorted.length > 0
    ? sorted[Math.floor(sorted.length / 2)]?.deltaPct ?? 0
    : 0;
  const chartDescription =
    `Per-query percentage change relative to ${baselineLabel}. Negative values are faster; positive values are slower. ` +
    `Bars are scaled to the largest change, capped at ${DIVERGING_MAX_PCT}%; ` +
    `larger changes end in an arrow and show their true value. ` +
    `${faster} entries are faster and ${slower} are slower. An accessible table follows the chart.`;
  const slotOf = (runIndex: number): number => (runIndex < baselineIdx ? runIndex : runIndex - 1);

  return (
    <div ref={containerRef} class="w-full">
      <p id="diverging-bar-description" class="sr-only">{chartDescription}</p>
      <p class="mb-2 text-xs text-[var(--bb-data-fg-muted)]">
        {faster} quer{faster === 1 ? "y" : "ies"} faster, {slower} quer{slower === 1 ? "y" : "ies"} slower
        {rawEntries.length > 0 && ` · median delta: ${formatPercentChange(medianDelta)}`}
        {" · sorted by magnitude"}
      </p>
      <svg
        class="bb-chart-svg"
        width="100%"
        height={totalHeight}
        viewBox={`0 0 ${drawWidth} ${totalHeight}`}
        role="img"
        aria-label="Diverging bar chart"
        aria-describedby="diverging-bar-description"
        data-scale-max={scaleMax}
        data-overflow-count={overflowCount}
      >
        <line
          x1={centerX}
          y1={HEADER_H - 2}
          x2={centerX}
          y2={totalHeight - FOOTER_PAD_PX}
          stroke="var(--bb-chart-axis)"
          stroke-width={1}
        />
        <text x={plotX} y={12} text-anchor="start" font-size={TEXT_FONT_PX} fill="var(--bb-chart-label-muted)">
          ← faster than baseline
        </text>
        <text x={plotRight} y={12} text-anchor="end" font-size={TEXT_FONT_PX} fill="var(--bb-chart-label-muted)">
          slower than baseline →
        </text>

        {sortedGroups.map(([queryId, entries], groupIndex) => {
          const groupTop = HEADER_H + groupIndex * groupHeight;
          const fullLabel = queryDisplayLabel(queryId);
          const fitted = fitLabel(fullLabel, labelW - LABEL_GAP_PX, TEXT_FONT_PX);
          return (
            <g key={`${groupIndex}-${queryId}`} data-query-row={queryId}>
              <text
                x={labelW - LABEL_GAP_PX}
                y={groupTop + groupHeight / 2 + 3.5}
                text-anchor="end"
                data-query-label={queryId}
                font-size={TEXT_FONT_PX}
                font-family="monospace"
                fill="var(--bb-chart-label-muted)"
              >
                {fitted.text}
                {fitted.truncated ? <title>{fullLabel}</title> : null}
              </text>
              {entries.map((entry) => {
                const rowTop = groupTop + GROUP_PADDING_PX / 2 + slotOf(entry.runIndex) * ROW_H;
                const barY = rowTop + (ROW_H - BAR_H) / 2;
                const rowCenter = rowTop + ROW_H / 2;
                const magnitude = Math.abs(entry.deltaPct);
                const overflow = magnitude > scaleMax;
                const length = Math.max(1, (Math.min(magnitude, scaleMax) / scaleMax) * halfWidth);
                const isSlower = entry.deltaPct > 0;
                const tipX = isSlower ? centerX + length : centerX - length;
                const valueX = isSlower
                  ? tipX + (overflow ? OVERFLOW_ARROW_PX + VALUE_GAP_PX : VALUE_GAP_PX)
                  : tipX - (overflow ? OVERFLOW_ARROW_PX + VALUE_GAP_PX : VALUE_GAP_PX);
                const arrowPoints = isSlower
                  ? `${tipX},${barY} ${tipX + OVERFLOW_ARROW_PX},${rowCenter} ${tipX},${barY + BAR_H}`
                  : `${tipX},${barY} ${tipX - OVERFLOW_ARROW_PX},${rowCenter} ${tipX},${barY + BAR_H}`;
                const valueText = formatPercentChange(entry.deltaPct);
                return (
                  <g key={`${entry.runIndex}-${entry.label}`} data-run-index={entry.runIndex}>
                    <rect
                      x={isSlower ? centerX : tipX}
                      y={barY}
                      width={length}
                      height={BAR_H}
                      fill={paletteColor(colorSlotOf(entry.runIndex))}
                      opacity={0.85}
                    >
                      <title>{`${entry.label} · ${fullLabel}: ${valueText} vs ${baselineLabel}`}</title>
                    </rect>
                    {overflow ? <polygon points={arrowPoints} fill={paletteColor(colorSlotOf(entry.runIndex))} /> : null}
                    <text
                      x={valueX}
                      y={rowCenter + 3.5}
                      text-anchor={isSlower ? "start" : "end"}
                      font-size={TEXT_FONT_PX}
                      fill="var(--bb-data-fg-primary)"
                      data-value-label={entry.label}
                    >
                      {valueText}
                    </text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>

      <div class="sr-only">
      <table>
        <caption>Per-query percentage changes relative to {baselineLabel}</caption>
        <thead>
          <tr><th>Query</th><th>Candidate</th><th>Change</th></tr>
        </thead>
        <tbody>
          {rawEntries.map((entry, index) => (
            <tr key={`${index}-${entry.queryId}-${entry.runIndex}`}>
              <td>{entry.queryId}</td>
              <td>{entry.label}</td>
              <td>{formatPercentChange(entry.deltaPct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      <div class="mt-2 text-xs text-[var(--bb-data-fg-muted)]">
        <span>Baseline: <strong>{baselineLabel}</strong></span>
        <span class="ml-3">Left of centre is faster than the baseline, right is slower.</span>
      </div>
      <RunLegend
        runs={results.flatMap((_, index) =>
          index === baselineIdx
            ? []
            : [{ key: `${runLabels[index] ?? "run"}-${index}`, label: runLabels[index] ?? "", colorSlot: colorSlotOf(index) }],
        )}
      />
    </div>
  );
}
