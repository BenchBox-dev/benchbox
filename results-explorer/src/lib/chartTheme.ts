export const PALETTE = [
  "var(--bb-chart-cat-1)",
  "var(--bb-chart-cat-2)",
  "var(--bb-chart-cat-3)",
  "var(--bb-chart-cat-4)",
  "var(--bb-chart-cat-5)",
  "var(--bb-chart-cat-6)",
  "var(--bb-chart-cat-7)",
  "var(--bb-chart-cat-8)",
  "var(--bb-chart-cat-9)",
  "var(--bb-chart-cat-10)",
] as const;

export function paletteColor(index: number): string {
  return PALETTE[index % PALETTE.length] ?? "var(--bb-chart-cat-1)";
}

export const timeSeriesColor = paletteColor;

export const HEAT_MIN_RATIO = 1;
export const HEAT_MAX_RATIO = 10;

export const DIVERGING_MAX_PCT = 300;

export const PHASE_COLORS: Record<string, string> = {
  data_generation: "var(--bb-chart-phase-1)",
  schema_creation: "var(--bb-chart-phase-2)",
  data_loading: "var(--bb-chart-phase-3)",
  validation: "var(--bb-chart-phase-4)",
  power_test: "var(--bb-chart-phase-5)",
  throughput_test: "var(--bb-chart-phase-6)",
};

export const FASTER_FILL = "var(--bb-chart-success)";
export const SLOWER_FILL = "var(--bb-chart-danger)";
