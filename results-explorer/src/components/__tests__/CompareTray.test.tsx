import { fireEvent, render, screen } from "@testing-library/preact";
import { describe, expect, it, vi } from "vitest";
import { CompareTray, type CompareTrayItem } from "@/components/CompareTray";

const ITEM: CompareTrayItem = {
  id: "result-1",
  platform: "DuckDB",
  benchmarkLabel: "TPC-H",
  scaleFactor: 10,
  phase: "power",
  runDate: "2026-08-24",
  trustLabel: "maintainer-run",
  funding: null,
  visibleResultId: "a1b2c3d4",
};

function renderTray(onRemove?: (item: CompareTrayItem) => void) {
  return render(
    <CompareTray
      summary="1 result selected"
      items={[ITEM]}
      allIds={["result-1"]}
      buildHref={(ids) => `/results/compare?ids=${ids.join(",")}`}
      onClear={() => undefined}
      onRemove={onRemove}
    />,
  );
}

describe("CompareTray", () => {
  it("names the selected run in its remove control and removes that item", () => {
    const onRemove = vi.fn();
    renderTray(onRemove);

    fireEvent.click(
      screen.getByRole("button", {
        name: "Remove DuckDB, Public ID a1b2c3d4, from comparison",
      }),
    );

    expect(onRemove).toHaveBeenCalledWith(ITEM);
  });

  it("does not show remove controls when the caller does not support per-run removal", () => {
    renderTray();

    expect(screen.queryByRole("button", { name: /from comparison/ })).toBeNull();
  });

  it("shows the selected run age beside its date", () => {
    renderTray();

    const chip = screen.getByTestId("compare-tray-row-result-1").querySelector("[data-testid=run-date-chip]")!;
    expect(chip.textContent).toBe("2026-08-24");
    fireEvent.click(chip);
    expect(chip.textContent).toMatch(/days ago/);
  });
});
