import { paletteColor } from "@/lib/chartTheme";
import { runMarkerPath } from "@/lib/chartMarkers";

export interface RunLegendEntry {
  key: string;
  label: string;
  runIndex: number;
}

export function RunLegend({ runs }: { runs: readonly RunLegendEntry[] }) {
  return (
    <div
      class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-[var(--bb-data-fg-muted)]"
      role="group"
      aria-label="Run legend"
    >
      {runs.map((run) => (
        <span key={run.key} class="flex items-center gap-1">
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path
              d={runMarkerPath(run.runIndex, 7, 7, 5)}
              fill={paletteColor(run.runIndex)}
              stroke="var(--bb-surface-data)"
              stroke-width={0.75}
            />
          </svg>
          {run.label}
        </span>
      ))}
    </div>
  );
}
