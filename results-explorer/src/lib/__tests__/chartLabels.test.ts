import { describe, expect, it } from "vitest";
import { estimateTextWidth, fitLabel, labelColumnWidth } from "@/lib/chartLabels";

const LABEL_FONT_PX = 10;

describe("estimateTextWidth", () => {
  it("scales a monospace advance with font size and character count", () => {
    expect(estimateTextWidth("Q18", LABEL_FONT_PX)).toBeCloseTo(18.6);
    expect(estimateTextWidth("Q18", 20)).toBeCloseTo(37.2);
  });
});

describe("fitLabel", () => {
  it("keeps a label that fits unchanged and untruncated", () => {
    expect(fitLabel("aggregation_distinct_groupby", 400, LABEL_FONT_PX)).toStrictEqual({
      text: "aggregation_distinct_groupby",
      truncated: false,
    });
  });

  it("truncates long labels at the end with an ellipsis and keeps the distinguishing prefix", () => {
    const fitted = fitLabel("aggregation_distinct_groupby", 100, LABEL_FONT_PX);
    expect(fitted.truncated).toBe(true);
    expect(fitted.text.endsWith("…")).toBe(true);
    expect(fitted.text.startsWith("aggregation_dis")).toBe(true);
    expect(estimateTextWidth(fitted.text, LABEL_FONT_PX)).toBeLessThanOrEqual(100);
  });

  it("never returns more than the available width even for a tiny budget", () => {
    expect(fitLabel("broadcast_join_three_tables", 5, LABEL_FONT_PX)).toStrictEqual({ text: "…", truncated: true });
  });
});

describe("labelColumnWidth", () => {
  it("adds padding to the longest label", () => {
    const width = labelColumnWidth(["Q1", "aggregation_distinct"], LABEL_FONT_PX, 1600);
    expect(width).toBeCloseTo(estimateTextWidth("aggregation_distinct", LABEL_FONT_PX) + 12);
  });

  it("clamps to a 48 px floor for short labels", () => {
    expect(labelColumnWidth(["Q1"], LABEL_FONT_PX, 1600)).toBe(48);
  });

  it("caps at 320 px on wide charts and at 40 percent of the chart width on narrow ones", () => {
    const longLabels = ["broadcast_join_three_tables_with_a_very_long_name_indeed_yes_really"];
    expect(labelColumnWidth(longLabels, LABEL_FONT_PX, 1600)).toBe(320);
    expect(labelColumnWidth(longLabels, LABEL_FONT_PX, 390)).toBeCloseTo(0.4 * 390);
  });

  it("returns the floor for an empty label list", () => {
    expect(labelColumnWidth([], LABEL_FONT_PX, 1600)).toBe(48);
  });
});
