import { HEAT_MIN_RATIO, HEAT_MAX_RATIO } from "@/lib/chartTheme";
import { isValidTimingValue, validTimingValues } from "@/lib/displayEligibility";

export const LATENCY_LOG_SCALE_THRESHOLD = 10;

export type LatencyScaleMode = "linear" | "log";

export interface LatencyBarScale {
  mode: LatencyScaleMode;
  min: number;
  max: number;
  domainMin: number;
  domainMax: number;
  spanRatio: number;
}

const LATENCY_LOG_DOMAIN_PAD = Math.sqrt(10);
const LATENCY_LOG_TICKS_MS = [0.1, 1, 10, 100, 1000, 10000, 100000, 1000000];

function log2Latency(ms: number): number {
  return Math.log2(Math.max(ms, Number.MIN_VALUE));
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export function buildLatencyBarScale(
  values: (number | null)[],
  logThreshold = LATENCY_LOG_SCALE_THRESHOLD,
): LatencyBarScale | null {
  const valid = validTimingValues(values);
  if (valid.length === 0) return null;

  const min = Math.min(...valid);
  const max = Math.max(...valid);
  const spanRatio = max / min;
  const mode: LatencyScaleMode = spanRatio >= logThreshold ? "log" : "linear";

  if (mode === "linear") {
    return { mode, min, max, domainMin: 0, domainMax: max, spanRatio };
  }

  return {
    mode,
    min,
    max,
    domainMin: min / LATENCY_LOG_DOMAIN_PAD,
    domainMax: max,
    spanRatio,
  };
}

export function latencyScaleFraction(value: number | null, scale: LatencyBarScale): number | null {
  if (!isValidTimingValue(value)) return null;
  if (scale.mode === "linear") {
    if (scale.domainMax <= 0) return null;
    return clamp01(value / scale.domainMax);
  }

  const logMin = log2Latency(scale.domainMin);
  const logMax = log2Latency(scale.domainMax);
  const logRange = logMax - logMin || 1;
  return clamp01((log2Latency(value) - logMin) / logRange);
}

export function latencyScaleTicks(scale: LatencyBarScale): number[] {
  if (scale.mode === "linear") {
    return [0, 0.25, 0.5, 0.75, 1].map((fraction) => fraction * scale.domainMax);
  }

  const interior = LATENCY_LOG_TICKS_MS.filter(
    (ms) => ms >= scale.domainMin * 0.99 && ms <= scale.domainMax * 1.01,
  );
  const combined = [scale.domainMin, ...interior, scale.domainMax];
  const seen = new Set<number>();
  const ticks: number[] = [];
  for (const value of combined) {
    if (!seen.has(value)) {
      seen.add(value);
      ticks.push(value);
    }
  }
  return ticks;
}

export const LOG_LATENCY_TICKS_MS = [0.1, 1, 10, 100, 1000, 10000];

const LOG_LATENCY_TICK_MANTISSAS = [1, 1.5, 2, 3, 5, 7];

const MAX_LOG_LATENCY_TICKS = 8;

export interface LogLatencyScale {
  logMin: number;
  logMax: number;
  logRange: number;
  floorMs: number;
}

export function logLatencyValue(ms: number, floorMs = 0.1): number {
  return Math.log2(Math.max(ms, floorMs));
}

export function buildLogLatencyScale(
  values: readonly number[],
  options: {
    lowerPad?: number;
    upperPad?: number;
    floorMs?: number;
    minValue?: number;
    maxValue?: number;
  } = {},
): LogLatencyScale | null {
  const valid = validTimingValues(values);
  if (valid.length === 0) return null;

  const floorMs = options.floorMs ?? 0.1;
  const minValue = options.minValue ?? Math.min(...valid);
  const maxValue = options.maxValue ?? Math.max(...valid);
  const logMin = logLatencyValue(minValue, floorMs) - (options.lowerPad ?? 0);
  const logMax = logLatencyValue(maxValue, floorMs) + (options.upperPad ?? 0);
  return {
    logMin,
    logMax,
    logRange: logMax - logMin || 1,
    floorMs,
  };
}

export function logLatencyFraction(ms: number, scale: LogLatencyScale): number {
  return (logLatencyValue(ms, scale.floorMs) - scale.logMin) / scale.logRange;
}

export function logLatencyTicks(scale: LogLatencyScale, tolerance = 0.05): number[] {
  const inRange = (ms: number, slack: number) =>
    ms >= scale.floorMs &&
    logLatencyValue(ms, scale.floorMs) >= scale.logMin - slack &&
    logLatencyValue(ms, scale.floorMs) <= scale.logMax + slack;

  const decades = LOG_LATENCY_TICKS_MS.filter((ms) => inRange(ms, tolerance));
  if (decades.length >= 3) return decades;

  const lowestDecade = Math.floor(Math.log10(Math.max(2 ** scale.logMin, Number.MIN_VALUE)));
  const highestDecade = Math.ceil(Math.log10(2 ** scale.logMax));
  const subdivided: number[] = [];
  for (let exponent = lowestDecade; exponent <= highestDecade; exponent += 1) {
    for (const mantissa of LOG_LATENCY_TICK_MANTISSAS) {
      const ms = mantissa * 10 ** exponent;
      if (inRange(ms, 0)) subdivided.push(ms);
    }
  }
  const domainMin = Math.max(scale.floorMs, 2 ** scale.logMin);
  const domainMax = 2 ** scale.logMax;
  if (subdivided.length < 4 && Number.isFinite(domainMin) && Number.isFinite(domainMax) && domainMax > domainMin) {
    const linear = niceLinearTicks(domainMin, domainMax, MAX_LOG_LATENCY_TICKS);
    if (linear.length > subdivided.length) return thinTicks(linear, MAX_LOG_LATENCY_TICKS);
  }

  if (subdivided.length >= 2) return thinTicks(subdivided, MAX_LOG_LATENCY_TICKS);

  if (!Number.isFinite(domainMin) || !Number.isFinite(domainMax) || domainMax <= domainMin) return subdivided;
  return [domainMin, Math.sqrt(domainMin * domainMax), domainMax];
}

function niceStep(roughStep: number): number {
  if (!(roughStep > 0)) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const residual = roughStep / magnitude;
  const niceResidual = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10;
  return niceResidual * magnitude;
}

function niceLinearTicks(min: number, max: number, targetCount: number): number[] {
  if (!(max > min)) return [min];
  const step = niceStep((max - min) / Math.max(1, targetCount - 1));
  const start = Math.ceil(min / step) * step;
  const raw: number[] = [];
  for (let v = start; v <= max + step * 1e-6; v += step) {
    raw.push(Number(v.toFixed(6)));
  }
  return raw;
}

function thinTicks(ticks: readonly number[], max: number): number[] {
  if (ticks.length <= max) return [...ticks];
  const stride = Math.ceil((ticks.length - 1) / (max - 1));
  const kept = ticks.filter((_, index) => index % stride === 0);
  const last = ticks[ticks.length - 1]!;
  if (kept[kept.length - 1] !== last) kept.push(last);
  return kept;
}

export function colorForCell(ms: number | null, minInCol: number | null): number | null {
  if (!isValidTimingValue(ms) || !isValidTimingValue(minInCol)) return null;
  const ratio = Math.max(HEAT_MIN_RATIO, Math.min(HEAT_MAX_RATIO, ms / minInCol));
  const t = Math.log10(ratio);
  return Math.round(120 * (1 - t));
}

export function lightnessForCell(ms: number | null, minInCol: number | null): string | null {
  if (!isValidTimingValue(ms) || !isValidTimingValue(minInCol)) return null;
  const ratio = Math.max(HEAT_MIN_RATIO, Math.min(HEAT_MAX_RATIO, ms / minInCol));
  const t = Math.log10(ratio);
  return `${Math.round(95 - 70 * t)}%`;
}

export function vsSlowestRatio(thisMs: number | null, slowestMs: number | null): number | null {
  if (!isValidTimingValue(thisMs) || !isValidTimingValue(slowestMs)) return null;
  return slowestMs / thisMs;
}

export function speedupRatio(baselineMs: number | null, thisMs: number | null): number | null {
  if (!isValidTimingValue(baselineMs) || !isValidTimingValue(thisMs)) return null;
  return baselineMs / thisMs;
}

export function deltaPct(thisMs: number | null, baselineMs: number | null): number | null {
  if (!isValidTimingValue(thisMs) || !isValidTimingValue(baselineMs)) return null;
  return ((thisMs - baselineMs) / baselineMs) * 100;
}

export function sortByMagnitudeDesc<T extends { deltaPct: number }>(groups: [string, T[]][]): [string, T[]][] {
  return [...groups].sort((a, b) => {
    const maxA = Math.max(...a[1].map((e) => Math.abs(e.deltaPct)));
    const maxB = Math.max(...b[1].map((e) => Math.abs(e.deltaPct)));
    return maxB - maxA;
  });
}

export function perQuerySpeedup(validMs: number[]): number | null {
  const valid = validTimingValues(validMs);
  if (valid.length === 0) return null;
  const fastest = Math.min(...valid);
  const slowest = Math.max(...valid);
  return slowest / fastest;
}

export function geomeanMs(values: (number | null)[]): number | null {
  const valid = validTimingValues(values);
  if (valid.length === 0) return null;
  return Math.exp(valid.reduce((sum, v) => sum + Math.log(v), 0) / valid.length);
}

export interface BoxStats {
  min: number;
  q1: number;
  median: number;
  q3: number;
  max: number;
}

export function computeBoxStats(values: (number | null)[]): BoxStats | null {
  const valid = validTimingValues(values);
  if (valid.length === 0) return null;
  const sorted = [...valid].sort((a, b) => a - b);
  return {
    min: sorted[0]!,
    q1: computePercentile(sorted, 25)!,
    median: computePercentile(sorted, 50)!,
    q3: computePercentile(sorted, 75)!,
    max: sorted[sorted.length - 1]!,
  };
}

export function computeECDFPoints(values: (number | null)[]): { x: number; y: number }[] {
  const valid = validTimingValues(values);
  if (valid.length === 0) return [];
  const sorted = [...valid].sort((a, b) => a - b);
  const n = sorted.length;
  return sorted.map((x, i) => ({ x, y: ((i + 1) / n) * 100 }));
}

export function computeRankTable(
  queryIds: string[],
  timingsByPlatform: Record<string, number | null>[],
): Record<string, (number | null)[]> {
  const ranks: Record<string, (number | null)[]> = {};
  const nPlatforms = timingsByPlatform.length;
  for (const qid of queryIds) {
    const valid: { i: number; ms: number }[] = [];
    for (let i = 0; i < nPlatforms; i++) {
      const ms = timingsByPlatform[i]?.[qid] ?? null;
      if (isValidTimingValue(ms)) valid.push({ i, ms });
    }
    valid.sort((a, b) => a.ms - b.ms);
    const rankArr: (number | null)[] = new Array(nPlatforms).fill(null);
    let r = 1;
    for (let j = 0; j < valid.length; j++) {
      if (j > 0 && valid[j]!.ms !== valid[j - 1]!.ms) r = j + 1;
      rankArr[valid[j]!.i] = r;
    }
    ranks[qid] = rankArr;
  }
  return ranks;
}

export function computePercentile(values: number[], p: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const n = sorted.length;
  if (n === 1) return sorted[0]!;
  const k = (p / 100) * (n - 1);
  const f = Math.floor(k);
  const c = Math.ceil(k);
  if (f === c) return sorted[Math.round(k)]!;
  return sorted[f]! * (c - k) + sorted[c]! * (k - f);
}

export const DIVERGING_RATIO_CLAMP = 4;

export function divergingRatioPosition(
  ratio: number | null | undefined,
  clamp: number = DIVERGING_RATIO_CLAMP,
): number | null {
  if (ratio === null || ratio === undefined) return null;
  if (!Number.isFinite(ratio) || ratio <= 0) return null;
  const limit = Math.log2(Math.max(clamp, 1 + Number.EPSILON));
  const position = Math.log2(ratio) / limit;
  return Math.max(-1, Math.min(1, position));
}

export function queryDisagreementSpread(ratios: readonly (number | null | undefined)[]): number | null {
  const usable = ratios.filter(
    (r): r is number => r !== null && r !== undefined && Number.isFinite(r) && r > 0,
  );
  if (usable.length < 2) return null;
  const logs = usable.map((r) => Math.log2(r));
  return Math.max(...logs) - Math.min(...logs);
}

export function spaceTicks(ticks: readonly number[], positionPx: (value: number) => number, minGapPx = 48): number[] {
  const kept: number[] = [];
  for (const tick of ticks) {
    const previous = kept[kept.length - 1];
    if (previous === undefined || Math.abs(positionPx(tick) - positionPx(previous)) >= minGapPx) kept.push(tick);
  }
  const last = ticks[ticks.length - 1];
  if (last !== undefined && kept[kept.length - 1] !== last && kept.length > 1) {
    if (Math.abs(positionPx(last) - positionPx(kept[kept.length - 2]!)) >= minGapPx) kept[kept.length - 1] = last;
  }
  return kept;
}
