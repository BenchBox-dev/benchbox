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

export interface RunColorSlotInput {
  result_id: string;
  platform_id?: string | null;
  platform: string;
}

const PREFERRED_RUN_COLOR_SLOTS: ReadonlyMap<string, number> = new Map([
  ["duckdb", 0],
  ["snowflake", 1],
  ["databricks", 2],
  ["bigquery", 3],
  ["clickhouse-local", 4],
  ["clickhouse-server", 4],
  ["datafusion", 5],
  ["apache-doris", 5],
  ["starrocks", 5],
  ["polars", 6],
  ["pandas", 6],
  ["sqlite", 7],
  ["cedardb", 7],
  ["spark", 8],
  ["pyspark", 8],
  ["lakesail", 8],
  ["ducklake", 9],
]);

const RUN_COLOR_SLOT_COUNT = PALETTE.length;

function stableSlotForKey(key: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0) % RUN_COLOR_SLOT_COUNT;
}

export function assignRunColorSlots(runs: ReadonlyArray<RunColorSlotInput>): Map<string, number> {
  const taken = new Array<boolean>(RUN_COLOR_SLOT_COUNT).fill(false);
  const slots = new Map<string, number>();
  runs.forEach((run, position) => {
    const key = run.platform_id ?? run.platform;
    let slot = PREFERRED_RUN_COLOR_SLOTS.get(key) ?? stableSlotForKey(key);
    if (taken.every(Boolean)) {
      slot = position % RUN_COLOR_SLOT_COUNT;
    } else {
      while (taken[slot]) slot = (slot + 1) % RUN_COLOR_SLOT_COUNT;
    }
    taken[slot] = true;
    slots.set(run.result_id, slot);
  });
  return slots;
}

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
