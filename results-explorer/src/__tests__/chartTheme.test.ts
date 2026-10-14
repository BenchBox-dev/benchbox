import { describe, it, expect } from "vitest";
import {
  PALETTE,
  FASTER_FILL,
  SLOWER_FILL,
  DIVERGING_MAX_PCT,
  paletteColor,
  HEAT_MIN_RATIO,
  HEAT_MAX_RATIO,
  PHASE_COLORS,
  assignRunColorSlots,
} from "@/lib/chartTheme";
import { lightnessForCell } from "@/lib/chartMath";

describe("chartTheme", () => {
  it("PALETTE has 10 distinct colors for run comparisons", () => {
    expect(PALETTE.length).toBe(10);
    expect(new Set(PALETTE).size).toBe(10);
  });

  it("PALETTE is the ten categorical chart tokens in order", () => {
    PALETTE.forEach((color, index) => {
      expect(color).toBe(`var(--bb-chart-cat-${index + 1})`);
    });
  });

  it("PALETTE uses no semantic or tone tokens, so run colours never read as success or danger", () => {
    for (const color of PALETTE) {
      expect(color).not.toMatch(/bb-chart-(success|danger|warning)|bb-tone-/);
    }
  });

  it("paletteColor(i) returns PALETTE[i] for each supported run", () => {
    for (let i = 0; i < PALETTE.length; i++) {
      expect(paletteColor(i)).toBe(PALETTE[i]);
    }
  });

  it("paletteColor wraps after 10 runs", () => {
    expect(paletteColor(10)).toBe(PALETTE[0]);
    expect(paletteColor(17)).toBe(PALETTE[7]);
    expect(paletteColor(20)).toBe(PALETTE[0]);
  });

  it("paletteColor never returns undefined", () => {
    for (let i = 0; i < 20; i++) {
      expect(paletteColor(i)).toBeTruthy();
    }
  });

  it("FASTER_FILL and SLOWER_FILL are distinct", () => {
    expect(FASTER_FILL).not.toBe(SLOWER_FILL);
  });

  it("DIVERGING_MAX_PCT is a positive number", () => {
    expect(DIVERGING_MAX_PCT).toBeGreaterThan(0);
  });

  it("run 7 and run 8 keep their category colours", () => {
    expect(paletteColor(6)).toBe("var(--bb-chart-cat-7)");
    expect(paletteColor(7)).toBe("var(--bb-chart-cat-8)");
  });

  it("HEAT_MIN_RATIO is 1 (fastest anchor)", () => {
    expect(HEAT_MIN_RATIO).toBe(1);
  });

  it("HEAT_MAX_RATIO is 10 (slowest clamp)", () => {
    expect(HEAT_MAX_RATIO).toBe(10);
  });

  it("HEAT_MIN_RATIO < HEAT_MAX_RATIO", () => {
    expect(HEAT_MIN_RATIO).toBeLessThan(HEAT_MAX_RATIO);
  });

  it("maps the ranking best to a lighter cell and worse values to darker cells", () => {
    expect(lightnessForCell(HEAT_MIN_RATIO, HEAT_MIN_RATIO)).toBe("95%");
    expect(lightnessForCell(HEAT_MAX_RATIO, HEAT_MIN_RATIO)).toBe("25%");
  });

  it("PHASE_COLORS covers the 6 canonical phase names", () => {
    const phases = ["data_generation", "schema_creation", "data_loading", "validation", "power_test", "throughput_test"];
    for (const phase of phases) {
      expect(PHASE_COLORS[phase]).toBeTruthy();
    }
  });

  it("PHASE_COLORS values are distinct CSS variable tokens", () => {
    const values = Object.values(PHASE_COLORS);
    expect(new Set(values).size).toBe(values.length);
    for (const v of values) {
      expect(v).toMatch(/^var\(--bb-chart-[a-z0-9-]+\)$/);
    }
  });

  it("PHASE_COLORS never reuse a run colour", () => {
    const runColors = new Set<string>(PALETTE);
    for (const v of Object.values(PHASE_COLORS)) {
      expect(runColors.has(v)).toBe(false);
    }
  });
});

describe("assignRunColorSlots", () => {
  const COMMON_PLATFORM_IDS = [
    "duckdb",
    "snowflake",
    "databricks",
    "bigquery",
    "clickhouse-local",
    "datafusion",
    "polars",
    "sqlite",
    "spark",
    "ducklake",
  ];

  function run(resultId: string, platformId: string) {
    return { result_id: resultId, platform_id: platformId, platform: platformId };
  }

  it("returns the same slots for the same runs in the same order", () => {
    const runs = [run("tpch-snowflake-sf10.0-20261003-f0835929", "snowflake"), run("tpch-duckdb-sf10.0-20261002-aa11bb22", "duckdb")];
    expect([...assignRunColorSlots(runs)]).toEqual([...assignRunColorSlots(runs)]);
  });

  it("gives each of the ten common platforms its own slot", () => {
    const runs = COMMON_PLATFORM_IDS.map((platformId) => run(`tpch-${platformId}-sf1-20261001-0000`, platformId));
    const slots = assignRunColorSlots(runs);
    const assigned = runs.map((item) => slots.get(item.result_id));
    expect(new Set(assigned).size).toBe(COMMON_PLATFORM_IDS.length);
    assigned.forEach((slot) => expect(slot).toBeGreaterThanOrEqual(0));
    assigned.forEach((slot) => expect(slot).toBeLessThan(PALETTE.length));
  });

  it("keeps a platform on its preferred slot when it is the only run of that platform", () => {
    const slots = assignRunColorSlots([run("tpch-snowflake-sf1-20261001-0000", "snowflake"), run("tpch-polars-sf1-20261001-0000", "polars")]);
    expect(slots.get("tpch-snowflake-sf1-20261001-0000")).toBe(1);
    expect(slots.get("tpch-polars-sf1-20261001-0000")).toBe(6);
  });

  it("probes to a free slot when two runs of one platform share a view", () => {
    const first = "tpch-duckdb-sf1-20261001-aaaa1111";
    const second = "tpch-duckdb-sf1-20261002-bbbb2222";
    const slots = assignRunColorSlots([run(first, "duckdb"), run(second, "duckdb")]);
    expect(slots.get(first)).toBe(0);
    expect(slots.get(second)).toBe(1);
  });

  it("wraps the probe when the preferred slot is the last one", () => {
    const slots = assignRunColorSlots([run("a", "ducklake"), run("b", "ducklake")]);
    expect(slots.get("a")).toBe(9);
    expect(slots.get("b")).toBe(0);
  });

  it("reuses slots in order once there are more than ten runs", () => {
    const runs = Array.from({ length: 12 }, (_, index) => run(`run-${index}`, "duckdb"));
    const slots = assignRunColorSlots(runs);
    const assigned = runs.map((item) => slots.get(item.result_id));
    expect(new Set(assigned.slice(0, 10)).size).toBe(10);
    expect(assigned[10]).toBe(0);
    expect(assigned[11]).toBe(1);
  });

  it("hashes an unknown platform id to a stable slot", () => {
    const first = assignRunColorSlots([run("tpch-acme-sf1-20261001-0000", "acme-warehouse")]).get("tpch-acme-sf1-20261001-0000");
    const second = assignRunColorSlots([run("tpch-acme-sf1-20261001-0000", "acme-warehouse")]).get("tpch-acme-sf1-20261001-0000");
    expect(first).toBeGreaterThanOrEqual(0);
    expect(first).toBeLessThan(PALETTE.length);
    expect(second).toBe(first);
  });

  it("falls back to the platform name when platform_id is missing", () => {
    const slots = assignRunColorSlots([{ result_id: "tpch-x-1", platform_id: null, platform: "duckdb" }]);
    expect(slots.get("tpch-x-1")).toBe(0);
  });
});
