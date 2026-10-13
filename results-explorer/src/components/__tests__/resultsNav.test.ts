import { describe, expect, it } from "vitest";
import {
  RESULTS_NAV_SECTIONS,
  activeResultsNavSection,
  isLocalResultPath,
} from "@/components/resultsNav";

describe("activeResultsNavSection", () => {
  it.each([
    ["/results/", "overview"],
    ["/results", "overview"],
    ["/results/benchmarks", "benchmarks"],
    ["/results/benchmarks/", "benchmarks"],
    ["/results/tpch/", "benchmarks"],
    ["/results/clickbench/", "benchmarks"],
    ["/results/platforms", "platforms"],
    ["/results/platforms/", "platforms"],
    ["/results/p/duckdb/", "platforms"],
    ["/results/p/clickhouse/", "platforms"],
    ["/results/p/duckdb", "platforms"],
    ["/results/local/res_123", null],
    ["/results/compare", "compare"],
    ["/results/compare/", "compare"],
    ["/results/compare/?a=1&b=2", "compare"],
    ["/results/query", "query"],
    ["/results/query/", "query"],
    ["/results/query/?benchmark=tpch", "query"],
  ])("resolves %s to the %s section", (path, expectedId) => {
    expect(activeResultsNavSection(path)?.id ?? null).toBe(expectedId);
  });

  it.each([
    "/results/r/abc123",
    "/results/r/abc123/passes",
    "/results/local",
    "/results/local/",
    "/results/local/abc123",
    "/results/unknown-multi/segment",
    "/unrelated",
  ])("leaves %s without an owning section", (path) => {
    expect(activeResultsNavSection(path)).toBeNull();
  });

  it("keeps run detail pages section-free so no section misclaims them", () => {
    expect(activeResultsNavSection("/results/r/tpch-duckdb-sf0.1-20260315-abcd1234")).toBeNull();
    expect(activeResultsNavSection("/results/r/tpch-duckdb-sf0.1-20260315-abcd1234/passes")).toBeNull();
  });

  it("resolves reserved single segments to their own section, never Benchmarks", () => {
    const expected: Record<string, string | null> = {
      benchmarks: "benchmarks",
      platforms: "platforms",
      compare: "compare",
      query: "query",
      p: "platforms",
      r: null,
      local: null,
    };
    for (const [segment, ownerId] of Object.entries(expected)) {
      expect(activeResultsNavSection(`/results/${segment}/`)?.id ?? null, segment).toBe(ownerId);
    }
  });

  it("exposes exactly the five pinned section links in order", () => {
    expect(RESULTS_NAV_SECTIONS.map((section) => [section.label, section.href])).toEqual([
      ["Overview", "/results/"],
      ["Benchmarks", "/results/benchmarks/"],
      ["Platforms", "/results/platforms/"],
      ["Compare", "/results/compare"],
      ["Find runs", "/results/query"],
    ]);
  });
});

describe("isLocalResultPath", () => {
  it.each(["/results/local", "/results/local/", "/results/local/abc123"])("marks %s as local", (path) => {
    expect(isLocalResultPath(path)).toBe(true);
  });

  it.each(["/results/", "/results/query", "/results/r/abc123"])("marks %s as not local", (path) => {
    expect(isLocalResultPath(path)).toBe(false);
  });
});
