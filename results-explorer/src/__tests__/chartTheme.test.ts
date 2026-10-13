import { describe, it, expect } from "vitest";
import {
  PALETTE,
  FASTER_FILL,
  SLOWER_FILL,
  DIVERGING_MAX_PCT,
  paletteColor,
  timeSeriesColor,
  HEAT_MIN_RATIO,
  HEAT_MAX_RATIO,
  PHASE_COLORS,
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

  it("timeSeriesColor gives the same colour as paletteColor for every run index", () => {
    for (let i = 0; i < 25; i++) {
      expect(timeSeriesColor(i)).toBe(paletteColor(i));
    }
  });

  it("run 7 and run 8 keep their category colours in time series", () => {
    expect(timeSeriesColor(6)).toBe("var(--bb-chart-cat-7)");
    expect(timeSeriesColor(7)).toBe("var(--bb-chart-cat-8)");
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
