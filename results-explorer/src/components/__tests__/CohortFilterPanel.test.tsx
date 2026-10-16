import { render, screen, fireEvent } from "@testing-library/preact";
import { describe, expect, it, vi } from "vitest";
import { CohortFilterPanel, type CohortFilterFieldSpec } from "@/components/CohortFilterPanel";

function baseField(overrides: Partial<CohortFilterFieldSpec> = {}): CohortFilterFieldSpec {
  return {
    id: "scale-filter",
    testId: "scale-filter",
    label: "Scale",
    value: "0.1",
    options: [{ value: "0.1", label: "SF 0.1" }],
    onChange: vi.fn(),
    ...overrides,
  };
}

describe("CohortFilterPanel", () => {
  it("disables a field with zero options and explains why", () => {
    const field = baseField({ options: [], disabledReason: "No data recorded for this filter in the current results." });
    render(<CohortFilterPanel fields={[field]} showClear={false} onClear={() => {}} />);

    const select = screen.getByTestId("scale-filter") as HTMLSelectElement;
    expect(select).toBeDisabled();
    expect(select.title).toBe("No data recorded for this filter in the current results.");
  });

  it("disables a field with exactly one option and explains why", () => {
    const field = baseField({ disabledReason: "Only one value is present in the current results." });
    render(<CohortFilterPanel fields={[field]} showClear={false} onClear={() => {}} />);

    const select = screen.getByTestId("scale-filter") as HTMLSelectElement;
    expect(select).toBeDisabled();
    expect(select.title).toBe("Only one value is present in the current results.");
  });

  it("keeps a field enabled once it has an active selection, even with one remaining option", () => {
    const field = baseField({ value: "0.1", disabledReason: null });
    render(<CohortFilterPanel fields={[field]} showClear={false} onClear={() => {}} />);

    const select = screen.getByTestId("scale-filter") as HTMLSelectElement;
    expect(select).not.toBeDisabled();
  });

  it("shows Clear filters only when at least one filter is active, and clears on click", () => {
    const onClear = vi.fn();
    const { rerender } = render(
      <CohortFilterPanel fields={[baseField()]} showClear={false} onClear={onClear} clearTestId="clear-filters" />,
    );
    expect(screen.queryByTestId("clear-filters")).toBeNull();

    rerender(<CohortFilterPanel fields={[baseField()]} showClear={true} onClear={onClear} clearTestId="clear-filters" />);
    const clearButton = screen.getByTestId("clear-filters");
    expect(clearButton).toBeTruthy();
    fireEvent.click(clearButton);
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("shows a disabled 'N selected' placeholder for a facet whose URL state holds more values than the select offers", () => {
    const field = baseField({
      id: "trust-filter",
      testId: "trust-filter",
      label: "Trust tier",
      value: "__multiple__",
      options: [
        { value: "all", label: "All tiers" },
        { value: "maintainer-run", label: "Maintainer Run" },
        { value: "community-submission", label: "Community Submission" },
      ],
      multiValueOption: { value: "__multiple__", label: "2 tiers selected" },
    });
    render(<CohortFilterPanel fields={[field]} showClear={false} onClear={() => {}} />);

    const select = screen.getByTestId("trust-filter") as HTMLSelectElement;
    expect(select.value).toBe("__multiple__");
    const multipleOption = Array.from(select.options).find((option) => option.value === "__multiple__");
    expect(multipleOption).toBeTruthy();
    expect(multipleOption?.disabled).toBe(true);
    expect(multipleOption?.text).toBe("2 tiers selected");
  });

  it("calls onChange with the selected value", () => {
    const onChange = vi.fn();
    const field = baseField({
      options: [
        { value: "all", label: "All" },
        { value: "0.1", label: "SF 0.1" },
        { value: "1", label: "SF 1" },
      ],
      value: "all",
      onChange,
    });
    render(<CohortFilterPanel fields={[field]} showClear={false} onClear={() => {}} />);

    fireEvent.change(screen.getByTestId("scale-filter"), { target: { value: "1" } });
    expect(onChange).toHaveBeenCalledWith("1");
  });
});
