import { formatLatencyMs } from "@/lib/metricFormatters";

const NICE_MANTISSAS = [1, 2, 5] as const;
const FALLBACK_MIN_MS = 1;
const FALLBACK_MAX_MS = 10;
const RANGE_TOLERANCE = 1e-9;

function niceTicksWithin(lo: number, hi: number): number[] {
  const ticks: number[] = [];
  for (let exponent = Math.floor(Math.log10(lo)); exponent <= Math.ceil(Math.log10(hi)); exponent += 1) {
    for (const mantissa of NICE_MANTISSAS) {
      const value = Number((mantissa * 10 ** exponent).toPrecision(12));
      if (value >= lo * (1 - RANGE_TOLERANCE) && value <= hi * (1 + RANGE_TOLERANCE)) ticks.push(value);
    }
  }
  return ticks;
}

export function logTicks(minValue: number, maxValue: number, plotWidthPx: number, minGapPx = 56): number[] {
  const validRange = Number.isFinite(minValue) && Number.isFinite(maxValue) && minValue > 0 && maxValue > 0;
  let lo = validRange ? Math.min(minValue, maxValue) : FALLBACK_MIN_MS;
  let hi = validRange ? Math.max(minValue, maxValue) : FALLBACK_MAX_MS;
  if (hi <= lo) hi = lo * 10;

  const logSpan = Math.log10(hi) - Math.log10(lo);
  const pxPerDecade = plotWidthPx / logSpan;
  const kept: number[] = [];
  for (const tick of niceTicksWithin(lo, hi)) {
    const previous = kept[kept.length - 1];
    if (previous === undefined || (Math.log10(tick) - Math.log10(previous)) * pxPerDecade >= minGapPx) {
      kept.push(tick);
    }
  }
  if (kept.length >= 2) return kept;
  return [lo, hi];
}

export function formatTickMs(ms: number): string {
  return formatLatencyMs(ms, { context: "axis" }).valueText;
}
