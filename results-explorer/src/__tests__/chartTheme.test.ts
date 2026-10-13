import { describe, it, expect } from "vitest";
import {
  PALETTE,
  FASTER_FILL,
  SLOWER_FILL,
  DIVERGING_MAX_PCT,
  paletteColor,
  TIME_SERIES_PALETTE,
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

  it("TIME_SERIES_PALETTE has 8 distinct colors", () => {
    expect(TIME_SERIES_PALETTE.length).toBe(8);
    expect(new Set(TIME_SERIES_PALETTE).size).toBe(8);
  });

  it("timeSeriesColor wraps with modulo", () => {
    for (let i = 0; i < TIME_SERIES_PALETTE.length; i++) {
      expect(timeSeriesColor(i)).toBe(TIME_SERIES_PALETTE[i]);
    }
    expect(timeSeriesColor(TIME_SERIES_PALETTE.length)).toBe(TIME_SERIES_PALETTE[0]);
  });

  it("timeSeriesColor never returns undefined", () => {
    for (let i = 0; i < 20; i++) {
      expect(timeSeriesColor(i)).toBeTruthy();
    }
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
});
