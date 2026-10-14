import { describe, expect, it } from "vitest";
import { fitRunLabels, orderRunsByDisplayGeomean } from "@/lib/chartRunRows";
import { estimateTextWidth } from "@/lib/chartLabels";

describe("orderRunsByDisplayGeomean", () => {
  it("sorts fastest first, keeps the original index, and puts runs without a timing last", () => {
    const rows = orderRunsByDisplayGeomean([
      { id: "trino", display_geomean_ms: 119 },
      { id: "presto", display_geomean_ms: null },
      { id: "duckdb", display_geomean_ms: 29 },
      { id: "snowflake", display_geomean_ms: 47 },
    ]);
    expect(rows.map((row) => row.run.id)).toEqual(["duckdb", "snowflake", "trino", "presto"]);
    expect(rows.map((row) => row.sourceIndex)).toEqual([2, 3, 0, 1]);
  });
});

describe("fitRunLabels", () => {
  it("keeps a label whole when it fits the width", () => {
    expect(fitRunLabels(["ClickHouse Local v25.8"], 300, 12)).toEqual(["ClickHouse Local v25.8"]);
  });

  it("truncates a label that does not fit and keeps the full text available to callers", () => {
    const [fitted] = fitRunLabels(["ClickHouse Local v25.8 2026-10-01"], 120, 12);
    expect(fitted!.endsWith("…")).toBe(true);
    expect(estimateTextWidth(fitted!, 12)).toBeLessThanOrEqual(120);
  });

  it("keeps colliding labels distinct with a middle ellipsis that still fits", () => {
    const labels = ["DataFusion v53.0.0 2026-05-06", "DataFusion v53.0.0 2026-05-07"];
    const fitted = fitRunLabels(labels, 60, 12);
    expect(new Set(fitted).size).toBe(2);
    for (const label of fitted) {
      expect(label).toContain("…");
      expect(estimateTextWidth(label, 12)).toBeLessThanOrEqual(60);
    }
    expect(fitted[0]!.endsWith("06")).toBe(true);
    expect(fitted[1]!.endsWith("07")).toBe(true);
  });
});
