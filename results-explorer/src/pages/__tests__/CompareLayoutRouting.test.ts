import { describe, expect, it } from "vitest";

import { compareLayoutForSelection, isWithinTieBand, shouldShowMultiRunStandings } from "@/pages/Compare";
import { COMPARE_TIE_THRESHOLD } from "@/lib/compareSummary";

describe("compareLayoutForSelection", () => {
  it("routes two distinct runs to the head-to-head layout", () => {
    expect(compareLayoutForSelection(["a", "b"])).toEqual({
      kind: "head_to_head",
      runIds: ["a", "b"],
    });
  });

  it("routes three and four runs to the multi-run layout", () => {
    expect(compareLayoutForSelection(["a", "b", "c"]).kind).toBe("multi_run");
    expect(compareLayoutForSelection(["a", "b", "c", "d"]).kind).toBe("multi_run");
  });

  it("routes a single distinct run to the within-run route", () => {
    expect(compareLayoutForSelection(["a"])).toEqual({ kind: "within_run", resultId: "a" });
  });

  it("treats ids that alias to one run as one run, not a head-to-head", () => {
    expect(compareLayoutForSelection(["a", "a"])).toEqual({ kind: "within_run", resultId: "a" });
  });

  it("collapses duplicates before counting", () => {
    expect(compareLayoutForSelection(["a", "b", "a"])).toEqual({
      kind: "head_to_head",
      runIds: ["a", "b"],
    });
  });

  it("reports an empty selection rather than guessing a layout", () => {
    expect(compareLayoutForSelection([])).toEqual({ kind: "empty" });
    expect(compareLayoutForSelection([""])).toEqual({ kind: "empty" });
  });

  it("preserves selection order in the routed run list", () => {
    expect(compareLayoutForSelection(["b", "a"])).toEqual({
      kind: "head_to_head",
      runIds: ["b", "a"],
    });
  });
});

describe("the headline tie band", () => {
  it("treats a ratio that rounds to 1× as a tie", () => {
    expect(isWithinTieBand(1.002)).toBe(true);
    expect(isWithinTieBand(0.998)).toBe(true);
    expect(isWithinTieBand(1)).toBe(true);
  });

  it("is symmetric, so neither direction is called a win inside the band", () => {
    expect(isWithinTieBand(1 + COMPARE_TIE_THRESHOLD / 2)).toBe(true);
    expect(isWithinTieBand(1 - COMPARE_TIE_THRESHOLD / 2)).toBe(true);
  });

  it("leaves a real difference alone", () => {
    expect(isWithinTieBand(1.4)).toBe(false);
    expect(isWithinTieBand(0.7)).toBe(false);
  });

  it("reuses the decision summary's threshold rather than a second one", () => {
    expect(isWithinTieBand(1 + COMPARE_TIE_THRESHOLD * 0.99)).toBe(true);
    expect(isWithinTieBand(1 + COMPARE_TIE_THRESHOLD * 1.01)).toBe(false);
  });

  it("does not treat a missing or non-finite ratio as a tie", () => {
    expect(isWithinTieBand(null)).toBe(false);
    expect(isWithinTieBand(Number.NaN)).toBe(false);
    expect(isWithinTieBand(Number.POSITIVE_INFINITY)).toBe(false);
  });
});

describe("multi-run standings claims", () => {
  it("suppresses numbered standings when comparison claims are suppressed", () => {
    expect(shouldShowMultiRunStandings(["a", "b", "c"], true)).toBe(false);
    expect(shouldShowMultiRunStandings(["a", "b", "c"], false)).toBe(true);
  });
});
