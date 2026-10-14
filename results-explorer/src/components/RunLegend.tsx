import { paletteColor } from "@/lib/chartTheme";
import { MISSING_MARKER_RADIUS, RUN_MARKER_RADIUS, runMarkerPath } from "@/lib/chartMarkers";

export interface RunLegendEntry {
  key: string;
  label: string;
  colorSlot: number;
}

const ICON_SIZE = 18;
const ICON_CENTER = ICON_SIZE / 2;

export function RunLegend({ runs, showMissing = false }: { runs: readonly RunLegendEntry[]; showMissing?: boolean }) {
  return (
    <div
      class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-[var(--bb-data-fg-muted)]"
      role="group"
      aria-label="Run legend"
    >
      {runs.map((run) => (
        <span key={run.key} class="flex items-center gap-1">
          <svg width={ICON_SIZE} height={ICON_SIZE} viewBox={`0 0 ${ICON_SIZE} ${ICON_SIZE}`} aria-hidden="true">
            <path
              d={runMarkerPath(run.colorSlot, ICON_CENTER, ICON_CENTER, RUN_MARKER_RADIUS)}
              fill={paletteColor(run.colorSlot)}
              stroke="var(--bb-surface-data)"
              stroke-width={0.75}
            />
          </svg>
          {run.label}
        </span>
      ))}
      {showMissing
        ? (
          <span class="flex items-center gap-1">
            <svg width={ICON_SIZE} height={ICON_SIZE} viewBox={`0 0 ${ICON_SIZE} ${ICON_SIZE}`} aria-hidden="true">
              <circle
                cx={ICON_CENTER}
                cy={ICON_CENTER}
                r={MISSING_MARKER_RADIUS}
                fill="none"
                stroke="var(--bb-chart-label-muted)"
                stroke-width={1.5}
              />
            </svg>
            No successful measurement
          </span>
        )
        : null}
    </div>
  );
}
