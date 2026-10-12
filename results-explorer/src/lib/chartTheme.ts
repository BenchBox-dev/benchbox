export const PALETTE = [
  "var(--bb-chart-cat-1)",
  "var(--bb-chart-cat-2)",
  "var(--bb-chart-cat-3)",
  "var(--bb-chart-cat-4)",
  "var(--bb-chart-cat-5)",
  "var(--bb-chart-cat-6)",
  "var(--bb-chart-success)",
  "var(--bb-chart-danger)",
  "var(--bb-tone-warning-fg)",
  "var(--bb-tone-danger-fg)",
] as const;

export function paletteColor(index: number): string {
  return PALETTE[index % PALETTE.length] ?? "var(--bb-chart-cat-1)";
}

export const TIME_SERIES_PALETTE = [
  "var(--bb-chart-cat-1)",
  "var(--bb-chart-cat-2)",
  "var(--bb-chart-cat-3)",
  "var(--bb-chart-cat-4)",
  "var(--bb-chart-cat-5)",
  "var(--bb-chart-cat-6)",
  "var(--bb-chart-warning)",
  "var(--bb-chart-axis)",
] as const;

export function timeSeriesColor(index: number): string {
  return TIME_SERIES_PALETTE[index % TIME_SERIES_PALETTE.length] ?? "var(--bb-chart-cat-1)";
}

export const HEAT_MIN_RATIO = 1;
export const HEAT_MAX_RATIO = 10;

export const SPEEDUP_LOG2_MIN = Math.log2(0.2);
export const SPEEDUP_LOG2_MAX = Math.log2(5);
export const SPEEDUP_LOG2_RANGE = SPEEDUP_LOG2_MAX - SPEEDUP_LOG2_MIN;
export const SPEEDUP_GRID_STOPS = [0.2, 0.25, 0.5, 1, 2, 4, 5] as const;

export const DIVERGING_MAX_PCT = 300;

export const PHASE_COLORS: Record<string, string> = {
  data_generation: "var(--bb-chart-grid)",
  schema_creation: "var(--bb-chart-cat-1)",
  data_loading: "var(--bb-chart-cat-3)",
  validation: "var(--bb-chart-success)",
  power_test: "var(--bb-chart-warning)",
  throughput_test: "var(--bb-chart-danger)",
};

export const FASTER_FILL = "var(--bb-chart-success)";
export const SLOWER_FILL = "var(--bb-chart-danger)";
