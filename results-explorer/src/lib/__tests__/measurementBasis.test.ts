import { describe, expect, it } from "vitest";

import {
  ALL_WARM,
  BASES_URL_KEY,
  BASIS_URL_KEY,
  DEFAULT_BASIS,
  MAX_COMPARISON_SERIES,
  WARMUP,
  basesEqual,
  basesInComparison,
  basesSerde,
  basisSerde,
  crossRunComparison,
  decodeBasis,
  encodeBasis,
  isCollapsedStatistic,
  isDefaultBasis,
  warmPass,
  withinRunComparison,
  type MeasurementBasis,
} from "@/lib/measurementBasis";

const MIN_ALL_WARM: MeasurementBasis = { passes: ALL_WARM, statistic: "min" };
const WARMUP_BASIS: MeasurementBasis = { passes: WARMUP, statistic: "median" };
const PASS_2: MeasurementBasis = { passes: warmPass(2), statistic: "median" };

describe("basis predicates", () => {
  it("recognises the published default basis", () => {
    expect(isDefaultBasis(DEFAULT_BASIS)).toBe(true);
    expect(isDefaultBasis({ passes: ALL_WARM, statistic: "median" })).toBe(true);
  });

  it("does not treat a different statistic over the same passes as the default", () => {
    expect(isDefaultBasis(MIN_ALL_WARM)).toBe(false);
  });

  it("reports the statistic as collapsed for every single-pass selection", () => {
    expect(isCollapsedStatistic(WARMUP_BASIS)).toBe(true);
    expect(isCollapsedStatistic(PASS_2)).toBe(true);
    expect(isCollapsedStatistic(DEFAULT_BASIS)).toBe(false);
  });

  it("compares warm-pass bases by pass number, not just by kind", () => {
    expect(basesEqual(warmPassBasis(1), warmPassBasis(1))).toBe(true);
    expect(basesEqual(warmPassBasis(1), warmPassBasis(2))).toBe(false);
  });

  function warmPassBasis(pass: number): MeasurementBasis {
    return { passes: warmPass(pass), statistic: "median" };
  }
});

describe("cross-run comparison construction", () => {
  const runs = [{ resultId: "a" }, { resultId: "b" }];

  it("builds a comparison carrying exactly one basis", () => {
    const result = crossRunComparison(runs, MIN_ALL_WARM);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(basesInComparison(result.comparison)).toEqual([MIN_ALL_WARM]);
  });

  it("rejects a single run, because one run is not a comparison", () => {
    const result = crossRunComparison([{ resultId: "a" }], DEFAULT_BASIS);
    expect(result).toEqual({ ok: false, error: { kind: "too_few_series", count: 1 } });
  });

  it("rejects more runs than the surfaces can render", () => {
    const tooMany = Array.from({ length: MAX_COMPARISON_SERIES + 1 }, (_, i) => ({
      resultId: `r${i}`,
    }));
    expect(crossRunComparison(tooMany, DEFAULT_BASIS)).toEqual({
      ok: false,
      error: { kind: "too_many_series", count: MAX_COMPARISON_SERIES + 1, max: MAX_COMPARISON_SERIES },
    });
  });
});

describe("within-run comparison construction", () => {
  it("allows the basis to vary per series", () => {
    const result = withinRunComparison("a", [{ basis: DEFAULT_BASIS }, { basis: MIN_ALL_WARM }]);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(basesInComparison(result.comparison)).toEqual([DEFAULT_BASIS, MIN_ALL_WARM]);
  });

  it("rejects duplicate bases, which would draw the same figure twice", () => {
    const result = withinRunComparison("a", [{ basis: DEFAULT_BASIS }, { basis: DEFAULT_BASIS }]);
    expect(result).toEqual({ ok: false, error: { kind: "duplicate_basis" } });
  });

  it("deduplicates by value, not by identity", () => {
    const result = withinRunComparison("a", [
      { basis: { passes: warmPass(3), statistic: "median" } },
      { basis: { passes: warmPass(3), statistic: "median" } },
    ]);
    expect(result.ok).toBe(false);
  });
});

describe("URL grammar", () => {
  it("spells the default basis as `default`, not as all_warm:median", () => {
    expect(encodeBasis(DEFAULT_BASIS)).toBe("default");
    expect(decodeBasis("all_warm:median")).toEqual(DEFAULT_BASIS);
    expect(encodeBasis(decodeBasis("all_warm:median")!)).toBe("default");
  });

  it("uses the read model's own basis vocabulary", () => {
    expect(encodeBasis(MIN_ALL_WARM)).toBe("all_warm:min");
    expect(encodeBasis(WARMUP_BASIS)).toBe("warmup");
    expect(encodeBasis(PASS_2)).toBe("warm_pass_2");
  });

  it("round-trips every basis it can spell", () => {
    for (const basis of [DEFAULT_BASIS, MIN_ALL_WARM, WARMUP_BASIS, PASS_2, warmPassBasis(11)]) {
      expect(decodeBasis(encodeBasis(basis))).toEqual(basis);
    }
  });

  it("spells the statistic on every pass selection, including single-pass ones", () => {
    const minWarmup: MeasurementBasis = { passes: WARMUP, statistic: "min" };
    const minPass2: MeasurementBasis = { passes: warmPass(2), statistic: "min" };
    expect(encodeBasis(minWarmup)).toBe("warmup:min");
    expect(encodeBasis(minPass2)).toBe("warm_pass_2:min");
    expect(decodeBasis("warmup:min")).toEqual(minWarmup);
    expect(decodeBasis("warm_pass_2:min")).toEqual(minPass2);
  });

  it("leaves the median implicit so each basis has one spelling", () => {
    expect(encodeBasis(WARMUP_BASIS)).toBe("warmup");
    expect(decodeBasis("warmup:median")).toEqual(WARMUP_BASIS);
    expect(encodeBasis(decodeBasis("warmup:median")!)).toBe("warmup");
    expect(encodeBasis(decodeBasis("warm_pass_2:median")!)).toBe("warm_pass_2");
  });

  it("still refuses a suffix on `default`, which names one specific basis", () => {
    expect(decodeBasis("default:min")).toBeNull();
    expect(decodeBasis("default:median")).toBeNull();
  });

  it("round-trips every basis the model can construct", () => {
    const selections = [ALL_WARM, WARMUP, warmPass(1), warmPass(2), warmPass(17)];
    for (const passes of selections) {
      for (const statistic of ["median", "min"] as const) {
        const basis: MeasurementBasis = { passes, statistic };
        expect(decodeBasis(encodeBasis(basis))).toEqual(basis);
      }
    }
  });

  it("rejects malformed tokens instead of falling back to the default", () => {
    for (const raw of ["", "warm", "warm_pass_", "warm_pass_0", "warm_pass_-1", "all_warm:mean", "a:b:c"]) {
      expect(decodeBasis(raw)).toBeNull();
    }
  });

  it("exposes one `basis` key for cross-run and `bases` only for within-run", () => {
    expect(BASIS_URL_KEY).toBe("basis");
    expect(BASES_URL_KEY).toBe("bases");
  });

  it("round-trips a within-run bases list", () => {
    const bases = [DEFAULT_BASIS, MIN_ALL_WARM, PASS_2];
    expect(basesSerde.encode(bases)).toBe("default,all_warm:min,warm_pass_2");
    expect(basesSerde.decode("default,all_warm:min,warm_pass_2")).toEqual(bases);
  });

  it("invalidates the whole bases list when one token is unparseable", () => {
    expect(basesSerde.decode("default,nonsense,warm_pass_2")).toBeNull();
    expect(basesSerde.decode("")).toEqual([]);
  });

  it("exposes serdes shaped for useUrlState", () => {
    expect(basisSerde.encode(PASS_2)).toBe("warm_pass_2");
    expect(basisSerde.decode("warm_pass_2")).toEqual(PASS_2);
  });

  function warmPassBasis(pass: number): MeasurementBasis {
    return { passes: warmPass(pass), statistic: "median" };
  }
});
