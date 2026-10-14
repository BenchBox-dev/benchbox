import { render, screen } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { CompareCta } from "@/components/CompareCta";
import { buildCompareUrl } from "@/lib/resultLinks";

function ids(count: number): string[] {
  return Array.from({ length: count }, (_, index) => `tpch-snowflake-sf10.0-2026101${index}-f083592${index}`);
}

function renderCta(selectedIds: string[], allIds: string[]) {
  return render(<CompareCta selectedIds={selectedIds} allIds={allIds} buildHref={buildCompareUrl} testId="cta" />);
}

describe("CompareCta", () => {
  it("offers Compare All Runs as the primary action when nothing is selected and 8 runs are comparable", () => {
    renderCta([], ids(8));

    const primary = screen.getByTestId("cta");
    expect(primary.tagName).toBe("A");
    expect(primary.textContent).toBe("Compare All Runs");
    expect(primary.className).toContain("btn-primary");
    expect(primary.getAttribute("href")).toBe(buildCompareUrl(ids(8)));
    expect(screen.queryByTestId("cta-all")).toBeNull();
  });

  it("offers Compare All Runs for exactly two and exactly ten comparable runs", () => {
    const { unmount } = renderCta([], ids(2));
    expect(screen.getByTestId("cta").textContent).toBe("Compare All Runs");
    unmount();

    renderCta([], ids(10));
    expect(screen.getByTestId("cta").textContent).toBe("Compare All Runs");
  });

  it("shows Compare N selected as primary with a secondary Compare All Runs when a subset is selected", () => {
    const all = ids(8);
    const selection = all.slice(0, 3);
    renderCta(selection, all);

    const primary = screen.getByTestId("cta");
    expect(primary.textContent).toBe("Compare 3 selected");
    expect(primary.className).toContain("btn-primary");
    expect(primary.getAttribute("href")).toBe(buildCompareUrl(selection));

    const secondary = screen.getByTestId("cta-all");
    expect(secondary.textContent).toBe("Compare All Runs");
    expect(secondary.className).toContain("btn-secondary");
    expect(secondary.getAttribute("href")).toBe(buildCompareUrl(all));
  });

  it("omits the secondary action when the selection equals the comparable set", () => {
    const all = ids(4);
    renderCta([...all].reverse(), all);

    expect(screen.getByTestId("cta").textContent).toBe("Compare 4 selected");
    expect(screen.queryByTestId("cta-all")).toBeNull();
  });

  it("omits the secondary action when the comparable set is too large to compare all", () => {
    const all = ids(12);
    renderCta(all.slice(0, 2), all);

    expect(screen.getByTestId("cta").textContent).toBe("Compare 2 selected");
    expect(screen.queryByTestId("cta-all")).toBeNull();
  });

  it("disables the control with a clear hint when no run is selected and more than ten are comparable", () => {
    renderCta([], ids(12));

    const control = screen.getByTestId("cta") as HTMLButtonElement;
    expect(control.tagName).toBe("BUTTON");
    expect(control.disabled).toBe(true);
    expect(control.getAttribute("aria-disabled")).toBe("true");
    expect(control.className).toContain("btn-primary");
    expect(control.textContent).toBe("Select 2–10 runs to compare");
  });

  it("disables the control when only one run is selected and no comparable set exists", () => {
    renderCta(ids(1), []);

    const control = screen.getByTestId("cta") as HTMLButtonElement;
    expect(control.disabled).toBe(true);
    expect(control.textContent).toBe("Select 2–10 runs to compare");
  });

  it("disables the control with the at-most message when more than ten runs are selected", () => {
    const all = ids(8);
    renderCta(ids(11), all);

    const control = screen.getByTestId("cta") as HTMLButtonElement;
    expect(control.tagName).toBe("BUTTON");
    expect(control.disabled).toBe(true);
    expect(control.textContent).toBe("Select at most 10 runs");
    expect(screen.queryByTestId("cta-all")).toBeNull();
  });

  it("never renders arrows or small-text overrides", () => {
    renderCta(ids(3), ids(8));

    expect(screen.getByTestId("cta").textContent).not.toContain("→");
    expect(screen.getByTestId("cta").className).not.toContain("text-xs");
  });
});
