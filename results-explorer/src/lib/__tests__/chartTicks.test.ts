import { describe, expect, it } from "vitest";
import { formatTickMs, logTicks } from "@/lib/chartTicks";

function tickGapsPx(ticks: number[], min: number, max: number, plotWidth: number): number[] {
  const span = Math.log10(max) - Math.log10(min);
  return ticks.slice(1).map((tick, index) => ((Math.log10(tick) - Math.log10(ticks[index]!)) / span) * plotWidth);
}

describe("logTicks", () => {
  it("returns nice 1-2-5 values inside the range", () => {
    const ticks = logTicks(12, 6840, 900, 56);
    expect(ticks.length).toBeGreaterThanOrEqual(2);
    for (const tick of ticks) {
      expect(tick).toBeGreaterThanOrEqual(12);
      expect(tick).toBeLessThanOrEqual(6840);
      const mantissa = tick / 10 ** Math.floor(Math.log10(tick));
      expect([1, 2, 5]).toContain(Math.round(mantissa * 1000) / 1000);
    }
  });

  it("keeps neighbouring ticks at least the minimum gap apart on a log scale", () => {
    const min = 0.1;
    const max = 50_000;
    const plotWidth = 260;
    const ticks = logTicks(min, max, plotWidth, 56);
    for (const gap of tickGapsPx(ticks, min, max, plotWidth)) {
      expect(gap).toBeGreaterThanOrEqual(56 - 1e-9);
    }
  });

  it("always returns at least two distinct ticks, even for a narrow range", () => {
    const ticks = logTicks(10, 12, 300, 56);
    expect(ticks).toHaveLength(2);
    expect(new Set(ticks).size).toBe(2);
  });

  it("never repeats a tick value", () => {
    for (const [min, max, width] of [[0.1, 50_000, 900], [1, 10, 300], [627, 627, 300], [3, 9_000, 180]] as const) {
      const ticks = logTicks(min, max, width);
      expect(new Set(ticks).size).toBe(ticks.length);
    }
  });

  it("covers a sub-millisecond to multi-second range without repeated labels", () => {
    const ticks = logTicks(0.1, 50_000, 500);
    const labels = ticks.map(formatTickMs);
    expect(new Set(labels).size).toBe(labels.length);
    expect(ticks[0]).toBeGreaterThanOrEqual(0.1);
    expect(ticks.at(-1)).toBeLessThanOrEqual(50_000);
  });

  it("thins ticks on narrow plots", () => {
    const wide = logTicks(0.1, 50_000, 1200);
    const narrow = logTicks(0.1, 50_000, 160);
    expect(narrow.length).toBeLessThan(wide.length);
    expect(narrow.length).toBeGreaterThanOrEqual(2);
  });
});

describe("formatTickMs", () => {
  it("formats sub-millisecond, millisecond and second ticks distinctly", () => {
    expect(formatTickMs(0.1)).toBe("0.1 ms");
    expect(formatTickMs(0.5)).toBe("0.5 ms");
    expect(formatTickMs(2)).toBe("2 ms");
    expect(formatTickMs(50)).toBe("50 ms");
    expect(formatTickMs(1000)).toBe("1 s");
    expect(formatTickMs(2500)).toBe("2.5 s");
  });

  it("gives every tick of a wide range its own label", () => {
    const ticks = logTicks(0.1, 50_000, 900);
    const labels = ticks.map(formatTickMs);
    expect(new Set(labels).size).toBe(ticks.length);
  });
});
