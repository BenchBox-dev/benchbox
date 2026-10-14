import { render, screen } from "@testing-library/preact";
import { describe, expect, it } from "vitest";

import { IdentityDiffStrip, axisValueForRun, identityStripDiffCount, identityStripFields } from "@/components/IdentityDiffStrip";
import { buildComparabilityFields } from "@/components/ComparabilityReceipt";
import type { DetailResult } from "@/types";

function run(overrides: Partial<DetailResult> = {}): DetailResult {
  return {
    result_id: "r1",
    benchmark: "tpch",
    scale_factor: 1,
    platform: "DuckDB",
    platform_id: "duckdb",
    platform_version: "1.4.3",
    driver_version: "1.4.3",
    run_date: "2026-08-01",
    total_duration_s: 10,
    geomean_ms: 10,
    display_geomean_ms: 10,
    power_score: null,
    has_display_timing: true,
    display_timings: [],
    queries: [],
    environment: { arch: "arm64", cpu_family: "apple_silicon", cpu_model: "Apple M4", cpu_count: 10, memory_gb: 16 },
    ...overrides,
  } as unknown as DetailResult;
}

describe("axis selection", () => {
  it("reports the seven selected identity axes", () => {
    const fields = identityStripFields([run(), run({ result_id: "r2" })]);
    expect(fields).toHaveLength(7);
    expect(fields.map((f) => f.label)).toEqual([
      "Platform version",
      "Driver version",
      "Architecture",
      "CPU family",
      "CPU model",
      "CPU evidence",
      "Memory",
    ]);
  });

  it("reads the same fields the receipt does rather than re-deriving them", () => {
    const results = [run(), run({ result_id: "r2", platform_version: "1.5.0" })];
    const receipt = new Map(buildComparabilityFields(results).map((f) => [f.label, f]));
    for (const field of identityStripFields(results)) {
      expect(field).toEqual(receipt.get(field.label));
    }
  });
});

describe("marking which axes differ", () => {
  it("counts only the axes that actually differ", () => {
    const results = [run(), run({ result_id: "r2", platform_version: "1.5.0" })];
    expect(identityStripDiffCount(results)).toBe(1);
  });

  it("says so plainly when nothing differs", () => {
    render(<IdentityDiffStrip results={[run(), run({ result_id: "r2" })]} />);
    expect(screen.getByText(/No differences were recorded for these 7 fields/)).toBeTruthy();
  });

  it("reports the differing count with correct grammar", () => {
    render(<IdentityDiffStrip results={[run(), run({ result_id: "r2", platform_version: "1.5.0" })]} />);
    expect(screen.getByText(/1 of 7 axis differs/)).toBeTruthy();
  });

  it("pluralises for several differing axes", () => {
    const other = run({ result_id: "r2", platform_version: "1.5.0", driver_version: "9.9.9" });
    render(<IdentityDiffStrip results={[run(), other]} />);
    expect(screen.getByText(/2 of 7 axes differ/)).toBeTruthy();
  });

  it("formats fractional memory values consistently", () => {
    const fractional = run({
      environment: {
        arch: "arm64",
        cpu_family: "apple_silicon",
        cpu_model: "Apple M4",
        cpu_count: 10,
        memory_gb: 15.613975524902344,
      },
    });
    expect(axisValueForRun("Memory", fractional)).toBe("15.6 GB");
  });
});

describe("runs without CPU data", () => {
  it("renders 'not recorded' rather than guessing a vendor from the architecture", () => {
    const noCpu = run({
      result_id: "r2",
      environment: { arch: "arm64" },
    } as Partial<DetailResult>);
    render(<IdentityDiffStrip results={[noCpu, run({ result_id: "r3", environment: { arch: "arm64" } } as Partial<DetailResult>)]} />);
    expect(screen.getAllByText("Not recorded").length).toBeGreaterThan(0);
  });
});

describe("multi-run identity matrix (N > 2 runs)", () => {
  it("renders a matrix with one row per axis and one column per run", () => {
    const r1 = run({ result_id: "r1", platform_version: "1.0.0" });
    const r2 = run({ result_id: "r2", platform_version: "2.0.0" });
    const r3 = run({ result_id: "r3", platform_version: "1.0.0" });
    render(<IdentityDiffStrip results={[r1, r2, r3]} baselineIndex={0} runLabels={["Run 1", "Run 2", "Run 3"]} />);

    expect(screen.getByText("Run 1 (baseline)")).toBeTruthy();
    expect(screen.getByText("Run 2")).toBeTruthy();
    expect(screen.getByText("Run 3")).toBeTruthy();
    expect(screen.getByText("Platform version")).toBeTruthy();
    expect(screen.getByText("Architecture")).toBeTruthy();
    expect(screen.getByText(/1 of 7 axis varies across the whole selection/)).toBeTruthy();
  });

  it("marks differing cells relative to baseline and identifies matching cells", () => {
    const r1 = run({ result_id: "r1", platform_version: "1.0.0" });
    const r2 = run({ result_id: "r2", platform_version: "2.0.0" });
    const r3 = run({ result_id: "r3", platform_version: "1.0.0" });
    render(<IdentityDiffStrip results={[r1, r2, r3]} baselineIndex={0} runLabels={["Run 1", "Run 2", "Run 3"]} />);

    const cellDiff = screen.getByTestId("matrix-cell-Platform version-1");
    expect(cellDiff.textContent).not.toContain("Differs");
    expect(cellDiff).toHaveAttribute("data-differs", "true");
    expect(cellDiff.textContent).toContain("2.0.0");

    const cellMatch = screen.getByTestId("matrix-cell-Platform version-2");
    expect(cellMatch.textContent).toContain("Same");
    expect(cellMatch.textContent).toContain("1.0.0");
  });

  it("renders 'not recorded' rather than 'differs' when a run lacks CPU data", () => {
    const r1 = run({ result_id: "r1" });
    const r2 = run({ result_id: "r2", environment: { arch: "arm64" } as any });
    const r3 = run({ result_id: "r3" });
    render(<IdentityDiffStrip results={[r1, r2, r3]} baselineIndex={0} />);

    const missingCell = screen.getByTestId("matrix-cell-CPU family-1");
    expect(missingCell.textContent).toContain("Not recorded");
    expect(missingCell.textContent).not.toContain("Differs");
  });

  it("keeps a recorded candidate value visible when only the baseline is missing", () => {
    const baseline = run({ result_id: "r1", environment: { arch: "arm64" } as any });
    const candidate = run({ result_id: "r2" });
    const third = run({ result_id: "r3" });
    render(<IdentityDiffStrip results={[baseline, candidate, third]} baselineIndex={0} />);

    const candidateCell = screen.getByTestId("matrix-cell-CPU model-1");
    expect(candidateCell).toHaveTextContent("Apple M4");
    expect(candidateCell).toHaveTextContent("No baseline");
    expect(candidateCell).not.toHaveTextContent("Not recorded");
  });
});

describe("rendering guards", () => {
  it("renders nothing for a single run", () => {
    const { container } = render(<IdentityDiffStrip results={[run()]} />);
    expect(container.textContent).toBe("");
  });

  it("renders nothing for an empty selection", () => {
    const { container } = render(<IdentityDiffStrip results={[]} />);
    expect(container.textContent).toBe("");
  });
});

describe("more than four runs", () => {
  const labelsFor = (count: number) => Array.from({ length: count }, (_, i) => `DuckDB run ${i + 1}`);
  const tenRuns = () =>
    Array.from({ length: 10 }, (_, i) =>
      run({ result_id: `r${i + 1}`, platform_version: i % 3 === 1 ? "1.5.0" : "1.4.3" }),
    );

  it("lists every run as its own row so all ten stay reachable", () => {
    const labels = labelsFor(10);
    render(<IdentityDiffStrip results={tenRuns()} baselineIndex={0} runLabels={labels} />);

    for (const label of labels.slice(1)) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByText("DuckDB run 1 (baseline)")).toBeTruthy();
    expect(screen.getAllByRole("row")).toHaveLength(11);
    expect(screen.getByTestId("matrix-run-9-Platform version").textContent).toContain("1.4.3");
  });

  it("keeps the table scrollable with a sticky run column", () => {
    const { container } = render(<IdentityDiffStrip results={tenRuns()} baselineIndex={0} runLabels={labelsFor(10)} />);
    const scroller = container.querySelector('[data-testid="identity-matrix-scroller"]');
    expect(scroller?.classList.contains("overflow-x-auto")).toBe(true);
    expect(container.querySelector("tbody th.sticky")).toBeTruthy();
  });

  it("marks the baseline once in the header rather than on every baseline cell", () => {
    const { container } = render(<IdentityDiffStrip results={tenRuns()} baselineIndex={0} runLabels={labelsFor(10)} />);
    expect(screen.queryAllByText("Baseline")).toHaveLength(0);
    expect(container.textContent?.split("(baseline)").length).toBe(2);
  });

  it("shows a not-recorded value once, without a second pill", () => {
    const runs = tenRuns();
    runs[4] = run({ result_id: "r5", environment: { arch: "arm64" } as any });
    render(<IdentityDiffStrip results={runs} baselineIndex={0} runLabels={labelsFor(10)} />);
    const cell = screen.getByTestId("matrix-run-4-CPU family");
    expect(cell.textContent).toBe("Not recorded");
  });

  it("marks the differing attribute once and highlights only the differing cells", () => {
    render(<IdentityDiffStrip results={tenRuns()} baselineIndex={0} runLabels={labelsFor(10)} />);
    expect(screen.getAllByText("Differs")).toHaveLength(1);
    expect(screen.getByTestId("differs-marker-Platform version").textContent).toContain("in DuckDB run 2, DuckDB run 5, DuckDB run 8");
    expect(screen.getByTestId("matrix-run-1-Platform version")).toHaveAttribute("data-differs", "true");
    expect(screen.getByTestId("matrix-run-2-Platform version")).not.toHaveAttribute("data-differs");
  });
});

describe("one differs marker per attribute", () => {
  it("renders a single marker for each differing attribute in the column layout", () => {
    const r1 = run({ result_id: "r1" });
    const r2 = run({ result_id: "r2", platform_version: "1.5.0", driver_version: "9.9.9" });
    const r3 = run({ result_id: "r3", platform_version: "1.5.0" });
    render(<IdentityDiffStrip results={[r1, r2, r3]} baselineIndex={0} runLabels={["Run 1", "Run 2", "Run 3"]} />);

    expect(screen.getAllByText("Differs")).toHaveLength(2);
    expect(screen.getByTestId("differs-marker-Platform version").textContent).toContain("in Run 2, Run 3");
    expect(screen.getByTestId("differs-marker-Driver version").textContent).toContain("in Run 2");
    expect(screen.queryByTestId("differs-marker-Architecture")).toBeNull();
  });

  it("highlights differing cells without a pill in each cell", () => {
    const r1 = run({ result_id: "r1" });
    const r2 = run({ result_id: "r2", platform_version: "1.5.0" });
    const r3 = run({ result_id: "r3", platform_version: "1.5.0" });
    const { container } = render(<IdentityDiffStrip results={[r1, r2, r3]} baselineIndex={0} runLabels={["Run 1", "Run 2", "Run 3"]} />);

    expect(container.querySelectorAll('[data-differs="true"]')).toHaveLength(2);
    expect(screen.getByTestId("matrix-cell-Platform version-1").textContent).not.toContain("Differs");
  });
});

describe("display labels for hardware values", () => {
  it("shows CPU family and architecture as readable labels rather than enum values", () => {
    const apple = run({ result_id: "r1" });
    const amd = run({ result_id: "r2", environment: { arch: "x86_64", cpu_family: "amd_epyc", cpu_model: "EPYC 9454", cpu_count: 48, memory_gb: 384 } as any });
    const third = run({ result_id: "r3" });
    render(<IdentityDiffStrip results={[apple, amd, third]} />);

    expect(screen.getByTestId("matrix-cell-CPU family-0")).toHaveTextContent("Apple silicon");
    expect(screen.getByTestId("matrix-cell-CPU family-1")).toHaveTextContent("AMD EPYC");
    expect(screen.getByTestId("matrix-cell-Architecture-1")).toHaveTextContent("x86-64");
    expect(screen.getByTestId("matrix-cell-Architecture-0")).toHaveTextContent("Arm64");
    expect(screen.queryByText(/apple_silicon|amd_epyc|x86_64/)).toBeNull();
  });

  it("uses the same labels in the transposed layout", () => {
    const runs = Array.from({ length: 5 }, (_, i) =>
      run({ result_id: `r${i + 1}`, environment: { arch: "x86_64", cpu_family: "intel_xeon", cpu_model: "Xeon 8488C", cpu_count: 8, memory_gb: 32 } as any }),
    );
    render(<IdentityDiffStrip results={runs} baselineIndex={0} />);

    expect(screen.getByTestId("matrix-run-3-CPU family")).toHaveTextContent("Intel Xeon");
    expect(screen.getByTestId("matrix-run-3-Architecture")).toHaveTextContent("x86-64");
  });
});
