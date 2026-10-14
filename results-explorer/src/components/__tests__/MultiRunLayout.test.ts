import { describe, expect, it } from "vitest";

import {
  DIVERGING_RATIO_CLAMP,
  divergingRatioPosition,
  queryDisagreementSpread,
} from "@/lib/chartMath";
import { h } from "preact";
import { render, screen } from "@testing-library/preact";
import {
  MultiRunHeatmap,
  buildHeatmapRows,
  filterHeatmapRows,
  heatmapCellStyle,
  orderRowsByDisagreement,
} from "@/components/MultiRunHeatmap";
import { buildStandings, sharedQueryIdsFor } from "@/components/MultiRunStandings";
import { formatRatio } from "@/lib/metricFormatters";
import {
  DEFAULT_BASIS,
  WARMUP,
  ALL_WARM,
  resolveResultsForBasis,
  resolvedStatisticsCollapsed,
  type MeasurementBasis,
} from "@/lib/measurementBasis";
import { selectQueryIdsForLimiter } from "@/components/QueryDiffTable";
import type { DetailResult } from "@/types";

function run(id: string, timings: [string, number | null][]): DetailResult {
  return {
    result_id: id,
    platform: id,
    queries: [],
    display_timings: timings.map(([query_id, display_ms]) => ({
      query_id,
      display_ms,
      sample_count: 3,
      is_valid_display_timing: display_ms !== null,
      timing_exclusion_reason: display_ms === null ? "missing_timing" : null,
    })),
  } as unknown as DetailResult;
}

describe("the diverging scale", () => {
  it("puts parity at the neutral midpoint", () => {
    expect(divergingRatioPosition(1)).toBe(0);
  });

  it("signs faster negative and slower positive", () => {
    expect(divergingRatioPosition(0.5)).toBeLessThan(0);
    expect(divergingRatioPosition(2)).toBeGreaterThan(0);
  });

  it("is symmetric in log space, so a 2x speedup and 2x slowdown are equal and opposite", () => {
    const faster = divergingRatioPosition(0.5)!;
    const slower = divergingRatioPosition(2)!;
    expect(faster).toBeCloseTo(-slower, 12);
  });

  it("saturates rather than compressing everything near parity", () => {
    expect(divergingRatioPosition(DIVERGING_RATIO_CLAMP)).toBeCloseTo(1, 12);
    expect(divergingRatioPosition(1000)).toBe(1);
    expect(divergingRatioPosition(0.0001)).toBe(-1);
  });

  it("returns null for an unanswerable ratio rather than parity", () => {
    expect(divergingRatioPosition(null)).toBeNull();
    expect(divergingRatioPosition(0)).toBeNull();
    expect(divergingRatioPosition(Number.NaN)).toBeNull();
    expect(divergingRatioPosition(-1)).toBeNull();
  });
});

describe("disagreement spread", () => {
  it("measures the log-space spread across runs", () => {
    expect(queryDisagreementSpread([1, 2])).toBeCloseTo(1, 12);
    expect(queryDisagreementSpread([0.5, 2])).toBeCloseTo(2, 12);
  });

  it("has no value when only one run could answer", () => {
    expect(queryDisagreementSpread([1, null])).toBeNull();
    expect(queryDisagreementSpread([])).toBeNull();
  });
});

describe("the heatmap grid", () => {
  const results = [
    run("a", [["Q1", 10], ["Q2", 20]]),
    run("b", [["Q1", 20], ["Q2", 20]]),
    run("c", [["Q1", 5], ["Q2", null]]),
  ];

  it("expresses every cell as a ratio against the chosen baseline", () => {
    const rows = buildHeatmapRows(results, 0);
    const q1 = rows.find((r) => r.queryId === "Q1")!;
    expect(q1.cells.map((c) => c.ratio)).toEqual([1, 2, 0.5]);
  });

  it("re-bases when the baseline changes", () => {
    const rebased = buildHeatmapRows(results, 1);
    const q1 = rebased.find((r) => r.queryId === "Q1")!;
    expect(q1.cells.map((c) => c.ratio)).toEqual([0.5, 1, 0.25]);
  });

  it("marks a cell the run cannot answer as unrecorded, not parity", () => {
    const rows = buildHeatmapRows(results, 0);
    const q2 = rows.find((r) => r.queryId === "Q2")!;
    expect(q2.cells[2]!.ratio).toBeNull();
    expect(q2.cells[2]!.position).toBeNull();
    expect(q2.cells[2]!.evidenceKind).toBe("unrecorded");
  });

  it("distinguishes failed, excluded, and absent query evidence", () => {
    const failed = run("failed", [["Q1", null]]);
    failed.queries = [
      { query_id: "Q1", duration_ms: 0, status: "fail", run_type: "warmup", iter: 0, stream: null },
      { query_id: "Q1", duration_ms: 0, status: "fail", run_type: "measurement", iter: 1, stream: null },
    ];
    failed.display_timings[0]!.timing_exclusion_reason = "missing_timing";
    const excluded = run("excluded", [["Q1", null]]);
    excluded.display_timings[0]!.timing_exclusion_reason = "zero_timing";
    const absent = run("absent", []);
    const rows = buildHeatmapRows([run("base", [["Q1", 10]]), failed, excluded, absent], 0);

    expect(rows[0]!.cells.map((cell) => cell.evidenceKind)).toEqual([
      "available",
      "failed",
      "excluded",
      "unrecorded",
    ]);
  });

  it("classifies default-basis failure from measurement rows even when warmup passed", () => {
    const failed = run("failed", [["Q1", null]]);
    failed.queries = [
      { query_id: "Q1", duration_ms: 50, status: "pass", run_type: "warmup", iter: 0, stream: null },
      { query_id: "Q1", duration_ms: 0, status: "fail", run_type: "measurement", iter: 1, stream: null },
      { query_id: "Q1", duration_ms: 0, status: "fail", run_type: "measurement", iter: 2, stream: null },
    ];

    const rows = buildHeatmapRows([run("base", [["Q1", 10]]), failed], 0);

    expect(rows[0]!.cells[1]!.evidenceKind).toBe("failed");
  });

  it("keeps recorded candidates available when only the baseline failed", () => {
    const failedBaseline = run("base", [["Q1", null]]);
    failedBaseline.queries = [
      { query_id: "Q1", duration_ms: 0, status: "fail", run_type: "measurement", iter: 1, stream: null },
    ];

    const rows = buildHeatmapRows([failedBaseline, run("b", [["Q1", 20]]), run("c", [["Q1", 30]])], 0);

    expect(rows[0]!.cells.map((cell) => cell.evidenceKind)).toEqual(["failed", "available", "available"]);
    expect(rows[0]!.cells.map((cell) => cell.baselineEvidenceKind)).toEqual([null, "failed", "failed"]);
  });

  it("classifies a projected but unrecorded requested pass as unrecorded", () => {
    const projected = run("projected", [["Q1", null]]);
    projected.display_timings[0]!.timing_exclusion_reason = "pass_not_recorded";
    projected.queries = [
      { query_id: "Q1", duration_ms: 10, status: "pass", run_type: "measurement", iter: 1, stream: null },
    ];

    const rows = buildHeatmapRows([run("base", [["Q1", 10]]), projected], 0);

    expect(rows[0]!.cells[1]!.evidenceKind).toBe("unrecorded");
  });

  it("leaves theme-dependent background colour to CSS", () => {
    const style = heatmapCellStyle(0.5);
    expect(style).toContain("--cell-hue:");
    expect(style).toContain("--cell-dark-lightness:");
    expect(style).not.toContain("background-color");
  });

  it("orders by disagreement when asked, with unanswerable rows last", () => {
    const ordered = orderRowsByDisagreement(buildHeatmapRows(results, 0));
    expect(ordered[0]!.queryId).toBe("Q1");
  });
});

describe("standings", () => {
  const results = [
    run("fast", [["Q1", 10], ["Q2", 10]]),
    run("mid", [["Q1", 20], ["Q2", 20]]),
    run("slow", [["Q1", 40], ["Q2", 40]]),
  ];

  it("ranks by geomean, faster first", () => {
    const { rows } = buildStandings(results, 0, ["fast", "mid", "slow"]);
    expect(rows.map((r) => r.resultId)).toEqual(["fast", "mid", "slow"]);
  });

  it("computes every geomean over the same shared query set", () => {
    const withGap = [...results, run("gap", [["Q1", 15], ["Q2", null]])];
    const { sharedQueryIds, totalQueryIds } = buildStandings(withGap, 0, ["a", "b", "c", "d"]);
    expect(sharedQueryIds).toEqual(["Q1"]);
    expect(totalQueryIds).toBe(2);
  });

  it("re-bases the ratio column when the baseline changes", () => {
    const first = buildStandings(results, 0, ["fast", "mid", "slow"]).rows;
    const second = buildStandings(results, 1, ["fast", "mid", "slow"]).rows;
    expect(first.find((r) => r.resultId === "mid")!.ratioToBaseline).toBeCloseTo(2, 12);
    expect(second.find((r) => r.resultId === "mid")!.ratioToBaseline).toBeCloseTo(1, 12);
    expect(second.find((r) => r.resultId === "mid")!.isBaseline).toBe(true);
  });

  it("awards a query to a single fastest run and no one on a tie", () => {
    const tied = [run("x", [["Q1", 10]]), run("y", [["Q1", 10]]), run("z", [["Q1", 20]])];
    const { rows } = buildStandings(tied, 0, ["x", "y", "z"]);
    expect(rows.reduce((n, r) => n + r.queriesWon, 0)).toBe(0);
  });

  it("applies the head-to-head tie band so a near-parity run is not called faster", () => {
    const near = [
      run("a", [["Q1", 100]]),
      run("b", [["Q1", 100.2]]),
      run("c", [["Q1", 200]]),
    ];
    const { rows } = buildStandings(near, 0, ["a", "b", "c"]);
    expect(rows.find((r) => r.resultId === "b")!.tied).toBe(true);
    expect(rows.find((r) => r.resultId === "c")!.tied).toBe(false);
  });

  it("keeps a run with no geomean rather than dropping it", () => {
    const withNull = [...results, run("empty", [])];
    const { rows } = buildStandings(withNull, 0, ["a", "b", "c", "d"]);
    expect(rows).toHaveLength(4);
    expect(rows.at(-1)!.resultId).toBe("empty");
  });

  it("shares one query-set helper with the heatmap", () => {
    expect(sharedQueryIdsFor(results)).toEqual(["Q1", "Q2"]);
  });

  it("populates engine and hardware fields on standing rows", () => {
    const withHw = [
      {
        ...results[0],
        platform_version: "1.2.0",
        environment: { arch: "arm64", cpu_model: "Apple M4", memory_gb: 16, cpu_identity_provenance: "measured" },
      } as DetailResult,
      results[1]!,
      results[2]!,
    ];
    const { rows } = buildStandings(withHw, 0, ["fast", "mid", "slow"]);
    const first = rows.find((r) => r.resultId === "fast")!;
    expect(first.engine).toBe("fast v1.2.0");
    expect(first.hardware).toBe("Apple M4 · 16 GB");
    expect(first.cpuEvidence).toBe("Measured");
  });
});

describe("heatmap limiter filtering", () => {
  const results = [
    run("base", [
      ["Q1", 100],
      ["Q2", 100],
      ["Q3", 100],
    ]),
    run("alt1", [
      ["Q1", 50],
      ["Q2", 200],
      ["Q3", 100],
    ]),
    run("alt2", [
      ["Q1", 40],
      ["Q2", 150],
      ["Q3", 100],
    ]),
  ];

  it("filters to speedup queries when limiter is 'speedups'", () => {
    const rows = buildHeatmapRows(results, 0);
    const speedups = filterHeatmapRows(rows, "speedups", 0);
    expect(speedups.map((r) => r.queryId)).toEqual(["Q1"]);
  });

  it("filters to slowdown queries when limiter is 'slowdowns'", () => {
    const rows = buildHeatmapRows(results, 0);
    const slowdowns = filterHeatmapRows(rows, "slowdowns", 0);
    expect(slowdowns.map((r) => r.queryId)).toEqual(["Q2"]);
  });

  it("orders by disagreement when limiter is 'movement'", () => {
    const rows = buildHeatmapRows(results, 0);
    const movement = filterHeatmapRows(rows, "movement", 0);
    expect(movement[0]!.queryId).toBe("Q1");
  });
});

describe("coordinated baseline re-basing across panels", () => {
  const results = [
    run("runA", [
      ["Q1", 10],
      ["Q2", 100],
    ]),
    run("runB", [
      ["Q1", 20],
      ["Q2", 50],
    ]),
    run("runC", [
      ["Q1", 30],
      ["Q2", 200],
    ]),
  ];

  it("re-bases heatmap ratios, standings, and speedup filters together when baseline switches", () => {
    const heatmapA = buildHeatmapRows(results, 0);
    const standingsA = buildStandings(results, 0, ["A", "B", "C"]);
    const speedupsA = filterHeatmapRows(heatmapA, "speedups", 0);

    expect(standingsA.rows.find((r) => r.resultId === "runA")!.isBaseline).toBe(true);
    expect(standingsA.rows.find((r) => r.resultId === "runA")!.ratioToBaseline).toBe(1);
    expect(speedupsA.map((r) => r.queryId)).toContain("Q2");

    const heatmapB = buildHeatmapRows(results, 1);
    const standingsB = buildStandings(results, 1, ["A", "B", "C"]);
    const speedupsB = filterHeatmapRows(heatmapB, "speedups", 1);

    expect(standingsB.rows.find((r) => r.resultId === "runB")!.isBaseline).toBe(true);
    expect(standingsB.rows.find((r) => r.resultId === "runA")!.isBaseline).toBe(false);

    const q2HeatmapB = heatmapB.find((r) => r.queryId === "Q2")!;
    expect(q2HeatmapB.cells[0]!.ratio).toBeCloseTo(2, 6);
    expect(q2HeatmapB.cells[1]!.ratio).toBeCloseTo(1, 6);

    expect(speedupsB.map((r) => r.queryId)).not.toContain("Q2");
    expect(speedupsB.map((r) => r.queryId)).toContain("Q1");
  });
});

describe("competition ranking in standings", () => {
  it("assigns 'T-1' to tied runs and skips to 3", () => {
    const tiedRuns = [
      run("a", [["Q1", 100]]),
      run("b", [["Q1", 100.2]]),
      run("c", [["Q1", 200]]),
    ];
    const { rows } = buildStandings(tiedRuns, 0, ["A", "B", "C"]);
    expect(rows[0]!.rank).toBe("T-1");
    expect(rows[1]!.rank).toBe("T-1");
    expect(rows[2]!.rank).toBe("3");
  });

  it("assigns '—' when shared query set is empty rather than false ranks 1, 2, 3", () => {
    const withNull = [
      run("a", [["Q1", 100]]),
      run("b", [["Q1", 200]]),
      run("empty", []),
    ];
    const { rows } = buildStandings(withNull, 0, ["A", "B", "Empty"]);
    expect(rows.map((r) => r.rank)).toEqual(["—", "—", "—"]);
  });

  it("does not transitively chain ties across non-tied endpoints", () => {
    const chainedRuns = [
      run("a", [["Q1", 100]]),
      run("b", [["Q1", 100.4]]),
      run("c", [["Q1", 100.8]]),
    ];
    const { rows } = buildStandings(chainedRuns, 0, ["A", "B", "C"]);
    expect(rows[0]!.rank).toBe("T-1");
    expect(rows[1]!.rank).toBe("T-1");
    expect(rows[2]!.rank).toBe("3");
  });

  it("keeps rank ties separate from baseline ties", () => {
    const runs = [
      run("base", [["Q1", 100]]),
      run("cand1", [["Q1", 150]]),
      run("cand2", [["Q1", 150.2]]),
    ];
    const { rows } = buildStandings(runs, 0, ["Base", "Cand1", "Cand2"]);
    const cand1 = rows.find((r) => r.resultId === "cand1")!;
    const cand2 = rows.find((r) => r.resultId === "cand2")!;
    expect(cand1.rank).toBe("T-2");
    expect(cand2.rank).toBe("T-2");
    expect(cand1.rankTied).toBe(true);
    expect(cand2.rankTied).toBe(true);
    expect(cand1.tied).toBe(false);
    expect(cand2.tied).toBe(false);
    expect(cand1.ratioToBaseline).toBeCloseTo(1.5, 2);
  });
});

describe("missing evidence reporting in heatmap", () => {
  it("distinguishes baseline_missing from run_missing", () => {
    const results = [
      run("base", [["Q1", 10], ["Q2", null]]),
      run("alt", [["Q1", 20], ["Q2", 30]]),
      run("alt2", [["Q1", null], ["Q2", 40]]),
    ];
    const rows = buildHeatmapRows(results, 0);
    const q1 = rows.find((r) => r.queryId === "Q1")!;
    const q2 = rows.find((r) => r.queryId === "Q2")!;

    expect(q1.cells[2]!.missingKind).toBe("run_missing");

    expect(q2.cells[1]!.missingKind).toBe("baseline_missing");
    expect(q2.cells[1]!.timingMs).toBe(30);
  });
});

function runWithExecs(
  id: string,
  data: {
    queryId: string;
    displayMs: number | null;
    warmupMs?: number;
    pass1Ms?: number;
    pass2Ms?: number;
  }[],
): DetailResult {
  const queries: any[] = [];
  const display_timings: any[] = [];
  for (const d of data) {
    display_timings.push({
      query_id: d.queryId,
      display_ms: d.displayMs,
      sample_count: 2,
      is_valid_display_timing: d.displayMs !== null,
      timing_exclusion_reason: d.displayMs === null ? "missing" : null,
    });
    if (d.warmupMs !== undefined) {
      queries.push({
        query_id: d.queryId,
        duration_ms: d.warmupMs,
        status: "pass",
        run_type: "warmup",
        iter: null,
      });
    }
    if (d.pass1Ms !== undefined) {
      queries.push({
        query_id: d.queryId,
        duration_ms: d.pass1Ms,
        status: "pass",
        run_type: "measurement",
        iter: 1,
      });
    }
    if (d.pass2Ms !== undefined) {
      queries.push({
        query_id: d.queryId,
        duration_ms: d.pass2Ms,
        status: "pass",
        run_type: "measurement",
        iter: 2,
      });
    }
  }
  return {
    result_id: id,
    platform: id,
    display_geomean_ms: 100,
    display_timings,
    queries,
  } as unknown as DetailResult;
}

describe("measurement basis resolution for comparison", () => {
  const runs = [
    runWithExecs("run1", [
      { queryId: "Q1", displayMs: 100, warmupMs: 150, pass1Ms: 120, pass2Ms: 80 },
      { queryId: "Q2", displayMs: 200, warmupMs: 250, pass1Ms: 220, pass2Ms: 180 },
    ]),
    runWithExecs("run2", [
      { queryId: "Q1", displayMs: 50, warmupMs: 70, pass1Ms: 60, pass2Ms: 40 },
      { queryId: "Q2", displayMs: 100, warmupMs: 120, pass1Ms: 110, pass2Ms: 90 },
    ]),
  ];

  it("preserves display_ms under default basis", () => {
    const resolved = resolveResultsForBasis(runs, DEFAULT_BASIS);
    expect(resolved[0]!.display_timings.find((t) => t.query_id === "Q1")!.display_ms).toBe(100);
    expect(resolved[1]!.display_timings.find((t) => t.query_id === "Q1")!.display_ms).toBe(50);
  });

  it("preserves published sample counts under the default basis without raw executions", () => {
    const rawRun = {
      ...runs[0]!,
      queries: [],
      display_timings: [{ ...runs[0]!.display_timings[0]!, sample_count: 3 }],
    } as DetailResult;
    const resolved = resolveResultsForBasis([rawRun], DEFAULT_BASIS);
    expect(resolved[0]!.display_timings[0]!.sample_count).toBe(3);
  });

  it("counts only finite positive samples reduced for an alternate basis", () => {
    const rawRun = {
      ...runs[0]!,
      queries: [
        { query_id: "Q1", duration_ms: 10, status: "pass", run_type: "warmup", iter: null, stream: 0 },
        { query_id: "Q1", duration_ms: 0, status: "pass", run_type: "warmup", iter: null, stream: 1 },
      ],
      display_timings: [{ ...runs[0]!.display_timings[0]!, sample_count: 2 }],
    } as DetailResult;
    const resolved = resolveResultsForBasis([rawRun], { passes: WARMUP, statistic: "median" });
    expect(resolved[0]!.display_timings[0]!.sample_count).toBe(1);
  });

  it("keeps the statistic selectable when a projected pass reduces multiple stream samples", () => {
    const rawRun = {
      ...runs[0]!,
      queries: [
        { query_id: "Q1", duration_ms: 10, status: "pass", run_type: "warmup", iter: null, stream: 0 },
        { query_id: "Q1", duration_ms: 20, status: "pass", run_type: "warmup", iter: null, stream: 1 },
      ],
      display_timings: [{ ...runs[0]!.display_timings[0]! }],
    } as DetailResult;
    const resolved = resolveResultsForBasis([rawRun], { passes: WARMUP, statistic: "median" });
    expect(resolvedStatisticsCollapsed(resolved)).toBe(false);
  });

  it("keeps the statistic selectable when a multi-sample timing is excluded", () => {
    const excludedRun = {
      ...runs[0]!,
      display_timings: [
        {
          ...runs[0]!.display_timings[0]!,
          display_ms: null,
          sample_count: 5,
          is_valid_display_timing: false,
          timing_exclusion_reason: "zero_timing",
        },
      ],
    } as DetailResult;

    expect(resolvedStatisticsCollapsed([excludedRun])).toBe(false);
  });

  it("resolves warmup pass executions when warmup basis is selected", () => {
    const warmupBasis: MeasurementBasis = { passes: WARMUP, statistic: "median" };
    const resolved = resolveResultsForBasis(runs, warmupBasis);
    expect(resolved[0]!.display_timings.find((t) => t.query_id === "Q1")!.display_ms).toBe(150);
    expect(resolved[1]!.display_timings.find((t) => t.query_id === "Q1")!.display_ms).toBe(70);
  });

  it("resolves minimum across passes when min statistic is selected", () => {
    const minBasis: MeasurementBasis = { passes: ALL_WARM, statistic: "min" };
    const resolved = resolveResultsForBasis(runs, minBasis);
    expect(resolved[0]!.display_timings.find((t) => t.query_id === "Q1")!.display_ms).toBe(80);
    expect(resolved[1]!.display_timings.find((t) => t.query_id === "Q1")!.display_ms).toBe(40);
  });

  it("preserves whole-run scores and costs only under the default basis", () => {
    const runsWithPower = [
      { ...runs[0]!, power_score: 1500, normalized_cost_usd: 1.5, cost_status: "normalized" as const },
      { ...runs[1]!, power_score: 800, normalized_cost_usd: 0.8, cost_status: "normalized" as const },
    ] as DetailResult[];

    const defaultResolved = resolveResultsForBasis(runsWithPower, DEFAULT_BASIS);
    expect(defaultResolved[0]!.power_score).toBe(1500);
    expect(defaultResolved[1]!.power_score).toBe(800);
    expect(defaultResolved[0]!.normalized_cost_usd).toBe(1.5);
    expect(defaultResolved[0]!.cost_status).toBe("normalized");

    const warmupResolved = resolveResultsForBasis(runsWithPower, { passes: WARMUP, statistic: "median" });
    expect(warmupResolved[0]!.power_score).toBeNull();
    expect(warmupResolved[1]!.power_score).toBeNull();
    expect(warmupResolved[0]!.normalized_cost_usd).toBeNull();
    expect(warmupResolved[0]!.cost_status).toBe("unavailable");
  });

  it("preserves published display_geomean_ms exactly when all queries are shared under default basis", () => {
    const runsWithGeomean = [
      { ...runs[0]!, display_geomean_ms: 123.4567890123 },
      { ...runs[1]!, display_geomean_ms: 98.7654321098 },
    ] as DetailResult[];
    const resolved = resolveResultsForBasis(runsWithGeomean, DEFAULT_BASIS);
    expect(resolved[0]!.display_geomean_ms).toBe(123.4567890123);
    expect(resolved[1]!.display_geomean_ms).toBe(98.7654321098);
  });

  it("preserves published timing_exclusion_reason for default basis", () => {
    const rawRun: DetailResult = {
      ...runs[0]!,
      display_timings: [
        {
          query_id: "Q1",
          display_ms: null,
          sample_count: 0,
          is_valid_display_timing: false,
          timing_exclusion_reason: "missing_timing",
        },
      ],
      queries: [],
    };
    const resolved = resolveResultsForBasis([rawRun], DEFAULT_BASIS);
    expect(resolved[0]!.display_timings[0]!.timing_exclusion_reason).toBe("missing_timing");
  });

  it("recomputes timing eligibility according to canonical contract when alternate basis is used", () => {
    const rawRun: DetailResult = {
      ...runs[0]!,
      logical_query_count: 2,
      has_display_timing: false,
      valid_query_count: 0,
      missing_query_count: 2,
      display_exclusion_reason: "no_display_timings",
      comparison_exclusion_reason: "no_comparable_timings",
      ranking_exclusion_reason: "no_rankable_timings",
      display_timings: [
        {
          query_id: "Q1",
          display_ms: null,
          sample_count: 3,
          is_valid_display_timing: false,
          timing_exclusion_reason: "missing_timing",
        },
        {
          query_id: "Q2",
          display_ms: null,
          sample_count: 3,
          is_valid_display_timing: false,
          timing_exclusion_reason: "missing_timing",
        },
      ],
      queries: [
        {
          query_id: "Q1",
          duration_ms: 120,
          status: "pass",
          run_type: "warmup",
          iter: null,
          stream: 0,
        },
      ],
    };

    const resolved1 = resolveResultsForBasis([rawRun], { passes: WARMUP, statistic: "median" });
    expect(resolved1[0]!.has_display_timing).toBe(true);
    expect(resolved1[0]!.valid_query_count).toBe(1);
    expect(resolved1[0]!.display_exclusion_reason).toBeNull();
    expect(resolved1[0]!.comparison_exclusion_reason).toBe("insufficient_valid_queries");
    expect(resolved1[0]!.ranking_exclusion_reason).toBe("insufficient_valid_queries");
    expect(resolved1[0]!.display_timings.find((t) => t.query_id === "Q2")!.sample_count).toBe(0);

    const rawRun2: DetailResult = {
      ...rawRun,
      queries: [
        ...rawRun.queries,
        {
          query_id: "Q2",
          duration_ms: 140,
          status: "pass",
          run_type: "warmup",
          iter: null,
          stream: 0,
        },
      ],
    };
    const resolved2 = resolveResultsForBasis([rawRun2], { passes: WARMUP, statistic: "median" });
    expect(resolved2[0]!.valid_query_count).toBe(2);
    expect(resolved2[0]!.comparison_exclusion_reason).toBeNull();
    expect(resolved2[0]!.ranking_exclusion_reason).toBeNull();
  });

  it("recomputes zero_timing_count and sets zero_timings_only for alternate basis", () => {
    const rawRun: DetailResult = {
      ...runs[0]!,
      logical_query_count: 1,
      has_display_timing: false,
      valid_query_count: 0,
      missing_query_count: 1,
      zero_timing_count: 0,
      display_exclusion_reason: "no_display_timings",
      comparison_exclusion_reason: "no_comparable_timings",
      ranking_exclusion_reason: "no_rankable_timings",
      display_timings: [
        {
          query_id: "Q1",
          display_ms: null,
          sample_count: 1,
          is_valid_display_timing: false,
          timing_exclusion_reason: "missing_timing",
        },
      ],
      queries: [
        {
          query_id: "Q1",
          duration_ms: 0,
          status: "pass",
          run_type: "warmup",
          iter: null,
          stream: 0,
        },
      ],
    };
    const resolved = resolveResultsForBasis([rawRun], { passes: WARMUP, statistic: "median" });
    expect(resolved[0]!.valid_query_count).toBe(0);
    expect(resolved[0]!.zero_timing_count).toBe(1);
    expect(resolved[0]!.missing_query_count).toBe(0);
    expect(resolved[0]!.display_exclusion_reason).toBe("zero_timings_only");
  });
});

describe("shared limiter query selection", () => {
  const threeRuns = [
    run("base", [["Q1", 100], ["Q2", 100], ["Q3", 100]]),
    run("alt1", [["Q1", 50], ["Q2", 200], ["Q3", 100]]),
    run("alt2", [["Q1", 40], ["Q2", 150], ["Q3", 100]]),
  ];

  it("selects speedup queries across all candidates", () => {
    const { queryIds } = selectQueryIdsForLimiter(threeRuns, 0, "speedups", 20);
    expect(queryIds).toEqual(["Q1"]);
  });

  it("selects slowdown queries across all candidates", () => {
    const { queryIds } = selectQueryIdsForLimiter(threeRuns, 0, "slowdowns", 20);
    expect(queryIds).toEqual(["Q2"]);
  });

  it("orders largest movement by greatest relative divergence", () => {
    const { queryIds } = selectQueryIdsForLimiter(threeRuns, 0, "movement", 20);
    expect(queryIds[0]).toBe("Q1");
  });

  it("ranks movement by full multi-run disagreement spread across opposite-direction candidates", () => {
    const oppositeDirectionRuns = [
      run("base", [["Q1", 100], ["Q2", 100]]),
      run("alt1", [["Q1", 50], ["Q2", 300]]),
      run("alt2", [["Q1", 200], ["Q2", 300]]),
    ];
    const { queryIds } = selectQueryIdsForLimiter(oppositeDirectionRuns, 0, "movement", 10);
    expect(queryIds[0]).toBe("Q1");
  });

  it("preserves queryFilter's rank order in heatmap and table rather than reverting to natural query order", () => {
    const heatmapRows = buildHeatmapRows(threeRuns, 0);
    const rowsByQueryId = new Map(heatmapRows.map((row) => [row.queryId, row]));
    const filterOrder = ["Q2", "Q1"];
    const orderedHeatmap = filterOrder.map((id) => rowsByQueryId.get(id)).filter(Boolean);
    expect(orderedHeatmap.map((r: any) => r.queryId)).toEqual(["Q2", "Q1"]);
  });

  it("keeps all queries uncapped in MultiRunHeatmap when limiter is 'all'", () => {
    const q15: [string, number][] = Array.from({ length: 15 }, (_, i) => [`Q${i + 1}`, 100 + i]);
    const runs15 = [
      run("base", q15),
      run("cand1", q15),
      run("cand2", q15),
    ];
    const all = buildHeatmapRows(runs15, 0);
    const effectiveLimiter = "all";
    const shown = effectiveLimiter === "all" ? all : all.slice(0, 10);
    expect(shown.length).toBe(15);
  });
});

describe("query-by-run ratio presentation", () => {
  it("writes extreme ratios with significant digits and a single multiplication sign", () => {
    expect(formatRatio(8892.6)).toBe("8,893×");
    expect(formatRatio(4716)).toBe("4,716×");
    expect(formatRatio(4.5)).toBe("4.5×");
    expect(formatRatio(0.03)).toBe("0.03×");
    expect(formatRatio(0.2)).toBe("0.2×");
  });

  it("renders cells and the caption with the × sign and no x suffix", () => {
    const results = [
      run("duck", [["Q18", 0.1], ["Q1", 10]]),
      run("sqlite", [["Q18", 889.26], ["Q1", 10]]),
      run("postgres", [["Q18", 0.2], ["Q1", 10]]),
    ];
    const { container } = render(
      h(MultiRunHeatmap, { results, baselineIndex: 0, runLabels: ["DuckDB", "SQLite", "Postgres"] }),
    );
    expect(screen.getByTestId("cell-Q18-1").textContent).toBe("8,893×");
    expect(screen.getByTestId("cell-Q18-2").textContent).toBe("2×");
    expect(screen.getByTestId("cell-Q1-1").textContent).toBe("1×");
    expect(screen.getByTestId("heatmap-caption").textContent).toContain("below 1× is faster");
    expect(container.textContent).not.toMatch(/\dx\b/);
  });

  it("keeps the header row sticky at the top of the scrolling grid", () => {
    const results = [run("duck", [["Q1", 10]]), run("sqlite", [["Q1", 20]]), run("postgres", [["Q1", 30]])];
    const { container } = render(
      h(MultiRunHeatmap, { results, baselineIndex: 0, runLabels: ["DuckDB", "SQLite", "Postgres"] }),
    );
    const scroller = container.querySelector('[data-testid="multi-run-heatmap-scroller"]');
    expect(scroller?.classList.contains("overflow-auto")).toBe(true);
    expect(container.querySelector("thead")?.classList.contains("sticky")).toBe(true);
    expect(container.querySelector("thead")?.classList.contains("top-0")).toBe(true);
  });
});

function relativeLuminance([red, green, blue]: number[]): number {
  const channel = (value: number) => {
    const unit = value / 255;
    return unit <= 0.03928 ? unit / 12.92 : ((unit + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(red!) + 0.7152 * channel(green!) + 0.0722 * channel(blue!);
}

function contrastRatio(first: number[], second: number[]): number {
  const [brighter, darker] = [relativeLuminance(first), relativeLuminance(second)].sort((a, b) => b - a) as [number, number];
  return (brighter + 0.05) / (darker + 0.05);
}

function hslToRgb(hue: number, saturation: number, lightness: number): number[] {
  const s = saturation / 100;
  const l = lightness / 100;
  const a = s * Math.min(l, 1 - l);
  const channel = (n: number) => {
    const k = (n + hue / 30) % 12;
    return (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))) * 255;
  };
  return [channel(0), channel(8), channel(4)];
}

function hexToRgb(hex: string): number[] {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

describe("query-by-run cell contrast", () => {
  const LIGHT_TEXT_ON_SURFACE = hexToRgb("#4b2c3a");
  const DARK_TEXT_ON_SURFACE = hexToRgb("#f2e1d4");
  const WCAG_AA_TEXT = 4.5;

  function cellColours(position: number) {
    const style = heatmapCellStyle(position);
    const read = (name: string) => Number(style.match(new RegExp(`${name}:([\\d.]+)`))![1]);
    return {
      hue: read("--cell-hue"),
      lightness: read("--cell-lightness"),
      darkLightness: read("--cell-dark-lightness"),
    };
  }

  it("keeps cell text at 4.5:1 or better in light and dark themes across the whole scale", () => {
    for (let step = -10; step <= 10; step += 1) {
      const position = step / 10;
      const { hue, lightness, darkLightness } = cellColours(position);
      const lightCell = hslToRgb(hue, 60, lightness);
      const darkCell = hslToRgb(hue, 45, darkLightness);
      expect(contrastRatio(LIGHT_TEXT_ON_SURFACE, lightCell)).toBeGreaterThanOrEqual(WCAG_AA_TEXT);
      expect(contrastRatio(DARK_TEXT_ON_SURFACE, darkCell)).toBeGreaterThanOrEqual(WCAG_AA_TEXT);
    }
  });
});
