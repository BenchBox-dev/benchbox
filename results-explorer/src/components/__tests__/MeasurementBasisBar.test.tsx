import { render, screen } from "@testing-library/preact";
import { describe, expect, it, vi } from "vitest";

import { MeasurementBasisBar } from "@/components/MeasurementBasisBar";
import { ALL_WARM, DEFAULT_BASIS, WARMUP, warmPass, type MeasurementBasis } from "@/lib/measurementBasis";

const PASSES = [ALL_WARM, WARMUP, warmPass(1), warmPass(2), warmPass(3)];

function renderBar(overrides: Partial<Parameters<typeof MeasurementBasisBar>[0]> = {}) {
  const onBasisChange = vi.fn();
  render(
    <MeasurementBasisBar
      basis={DEFAULT_BASIS}
      onBasisChange={onBasisChange}
      availablePasses={PASSES}
      comparableQueryCount={22}
      totalQueryCount={22}
      runCount={2}
      statisticCollapsed={false}
      {...overrides}
    />,
  );
  return { onBasisChange };
}

describe("the shared basis lock", () => {
  it("states that the basis is shared across the comparison's runs", () => {
    renderBar({ runCount: 2 });
    expect(screen.getByText("Shared across 2 runs")).toBeTruthy();
  });

  it("names what is measured and states the shared-query basis", () => {
    renderBar({ comparableQueryCount: 22, totalQueryCount: 22 });
    expect(screen.getByText(/Latency figures use the published median over all warm passes/)).toBeTruthy();
    expect(screen.getByText(/All 22 queries are shared, with data for every selected run/)).toBeTruthy();
    expect(screen.getByText(/wall-clock totals and phase durations remain run-wide context/)).toBeTruthy();
  });

  it("says how many queries are shared and why the rest are hidden", () => {
    renderBar({ comparableQueryCount: 108, totalQueryCount: 157 });
    expect(
      screen.getByText(
        "Latency figures use the published median over all warm passes. 108 of 157 queries are shared, with data for every selected run; 49 queries are hidden because at least one run is missing data.",
      ),
    ).toBeTruthy();
  });

  it("pluralises the hidden-query sentence", () => {
    renderBar({ comparableQueryCount: 102, totalQueryCount: 103 });
    expect(screen.getByText(/102 of 103 queries are shared.*1 query is hidden/)).toBeTruthy();
  });
});

describe("the statistic control", () => {
  it("offers median and fastest when the selection yields several executions", () => {
    renderBar({ statisticCollapsed: false });
    expect(screen.getByLabelText("Measurement statistic")).toBeTruthy();
    expect(screen.queryByTestId("statistic-locked")).toBeNull();
  });

  it("renders a locked display, not a control, when the statistic collapses", () => {
    renderBar({ statisticCollapsed: true });
    expect(screen.queryByLabelText("Measurement statistic")).toBeNull();
    expect(screen.getByTestId("statistic-locked")).toBeTruthy();
  });

  it("says why the statistic is locked", () => {
    renderBar({ statisticCollapsed: true });
    expect(screen.getByText(/median and fastest are the same over one execution/)).toBeTruthy();
  });

  it("keeps the pass control usable while the statistic is locked", () => {
    renderBar({ statisticCollapsed: true });
    expect(screen.getByLabelText("Measurement passes")).toBeTruthy();
  });
});

describe("basis changes", () => {
  it("keeps the statistic when only the pass selection changes", () => {
    const minBasis: MeasurementBasis = { passes: ALL_WARM, statistic: "min" };
    const { onBasisChange } = renderBar({ basis: minBasis });
    const select = screen.getByLabelText("Measurement passes") as HTMLSelectElement;
    select.value = "warm_pass_2";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    expect(onBasisChange).toHaveBeenCalledWith({ passes: warmPass(2), statistic: "min" });
  });

  it("keeps the pass selection when only the statistic changes", () => {
    const { onBasisChange } = renderBar({ basis: { passes: warmPass(3), statistic: "median" } });
    const select = screen.getByLabelText("Measurement statistic") as HTMLSelectElement;
    select.value = "min";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    expect(onBasisChange).toHaveBeenCalledWith({ passes: warmPass(3), statistic: "min" });
  });

  it("only offers pass selections the runs can serve", () => {
    renderBar({ availablePasses: [ALL_WARM, WARMUP] });
    const select = screen.getByLabelText("Measurement passes") as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(["default", "warmup"]);
  });
});
