import { fireEvent, render, screen, within } from "@testing-library/preact";
import { describe, expect, it, vi } from "vitest";
import { FacetDrawer, FacetRail, type FacetGroup } from "@/components/FacetRail";

const GROUPS: FacetGroup[] = [
  {
    key: "benchmark",
    label: "Benchmark",
    selected: ["tpch"],
    options: [
      { value: "tpch", label: "TPC-H", count: 12 },
      { value: "clickbench", label: "ClickBench", count: 4 },
    ],
  },
  {
    key: "platform",
    label: "Platform",
    selected: [],
    searchable: true,
    options: [
      { value: "duckdb", label: "DuckDB", count: 8 },
      { value: "sqlite", label: "SQLite", count: 3 },
    ],
  },
];

const ENVIRONMENT_GROUPS: FacetGroup[] = [
  {
    key: "cloud_provider",
    label: "Cloud provider",
    selected: ["aws"],
    searchable: true,
    options: [
      { value: "aws", label: "aws", count: 7 },
      { value: "gcp", label: "gcp", count: 4 },
    ],
  },
];

describe("FacetRail", () => {
  it("renders active chips, counts, searchable groups, and reset", () => {
    const onToggle = vi.fn();
    const onReset = vi.fn();

    render(
      <FacetRail
        groups={GROUPS}
        resultCount={16}
        activeChips={[{ key: "benchmark:tpch", label: "TPC-H" }]}
        onToggle={onToggle}
        onReset={onReset}
      />,
    );

    expect(screen.getByText("16 matching results")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "TPC-H" }));
    expect(onReset).toHaveBeenCalled();

    const platformSection = screen.getByRole("heading", { name: "Platform" }).closest("section")!;
    expect(within(platformSection).getByTestId("facet-platform-options").className).toContain("overflow-y-auto");
    fireEvent.input(within(platformSection).getByRole("searchbox"), { target: { value: "duck" } });
    expect(within(platformSection).getByText("DuckDB")).toBeTruthy();
    expect(within(platformSection).queryByText("SQLite")).toBeNull();

    fireEvent.click(within(platformSection).getByRole("checkbox"));
    expect(onToggle).toHaveBeenCalledWith("platform", "duckdb");

    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(onReset).toHaveBeenCalledTimes(2);
  });

  it("binds the full label as the title wherever a facet label is truncated", () => {
    const longLabel = "DuckDB v2.0.0-alpha38615 on a very long machine description";
    const groups: FacetGroup[] = [
      { key: "platform", label: "Platform", selected: ["duckdb"], options: [{ value: "duckdb", label: longLabel, count: 1 }] },
    ];

    render(
      <FacetRail
        groups={groups}
        resultCount={1}
        activeChips={[{ key: "platform:duckdb", label: longLabel }]}
        onToggle={vi.fn()}
        onReset={vi.fn()}
      />,
    );

    const platformSection = screen.getByRole("heading", { name: "Platform" }).closest("section")!;
    expect(within(platformSection).getByText(longLabel)).toHaveAttribute("title", longLabel);
  });

  it("renders canonical environment facet values with stable display labels", () => {
    const onToggle = vi.fn();

    render(
      <FacetRail
        groups={ENVIRONMENT_GROUPS}
        resultCount={11}
        activeChips={[
          { key: "cloud_provider:gcp", facetKey: "cloud_provider", value: "gcp", label: "gcp" },
        ]}
        onToggle={onToggle}
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "GCP" })).toBeTruthy();

    const providerSection = screen.getByRole("heading", { name: "Cloud provider" }).closest("section")!;
    expect(within(providerSection).getByRole("checkbox", { name: "Cloud provider: AWS (7)" })).toBeChecked();

    fireEvent.input(within(providerSection).getByRole("searchbox"), { target: { value: "gcp" } });
    fireEvent.click(within(providerSection).getByRole("checkbox", { name: "Cloud provider: GCP (4)" }));
    expect(onToggle).toHaveBeenCalledWith("cloud_provider", "gcp");
  });

  it("generates unique panel ids when duplicate rails render the same groups", () => {
    render(
      <>
        <FacetRail groups={GROUPS} resultCount={16} onToggle={vi.fn()} onReset={vi.fn()} />
        <FacetRail groups={GROUPS} resultCount={16} onToggle={vi.fn()} onReset={vi.fn()} />
      </>,
    );

    const panelIds = Array.from(document.querySelectorAll<HTMLButtonElement>("button[aria-controls]")).map(
      (button) => button.getAttribute("aria-controls"),
    );
    const renderedIds = Array.from(document.querySelectorAll<HTMLElement>("[id]")).map((element) => element.id);

    expect(panelIds).toHaveLength(GROUPS.length * 2);
    expect(new Set(panelIds).size).toBe(panelIds.length);
    panelIds.forEach((panelId) => {
      expect(panelId).toBeTruthy();
      expect(renderedIds.filter((id) => id === panelId)).toHaveLength(1);
    });
  });
});

describe("FacetRail collapsible groups", () => {
  const COLLAPSE_GROUPS: FacetGroup[] = [
    {
      key: "benchmark",
      label: "Benchmark",
      selected: ["tpch"],
      searchable: true,
      options: [
        { value: "tpch", label: "TPC-H", count: 12 },
        { value: "clickbench", label: "ClickBench", count: 4 },
        { value: "tpcds", label: "TPC-DS", count: 6 },
      ],
    },
    {
      key: "trust_tier",
      label: "Trust",
      selected: ["official"],
      defaultCollapsed: true,
      options: [
        { value: "official", label: "Official", count: 5 },
        { value: "community-submission", label: "Community", count: 2 },
      ],
    },
    {
      key: "deployment_class",
      label: "Deployment",
      selected: [],
      defaultCollapsed: true,
      options: [
        { value: "local-laptop", label: "Local laptop", count: 8 },
        { value: "cloud-instance", label: "Cloud instance", count: 4 },
      ],
    },
  ];

  it("renders default-collapsed groups without their option list and shows selected chips", () => {
    render(
      <FacetRail
        groups={COLLAPSE_GROUPS}
        resultCount={20}
        activeChips={[]}
        onToggle={vi.fn()}
        onReset={vi.fn()}
      />,
    );

    const trustToggle = screen.getByRole("button", { name: /^Trust/ });
    expect(trustToggle).toHaveAttribute("aria-expanded", "false");
    const trustSection = trustToggle.closest("section")!;
    expect(within(trustSection).queryByRole("checkbox")).toBeNull();
    const selectedSummary = within(trustSection).getByTestId(
      "facet-trust_tier-selected-summary",
    );
    expect(within(selectedSummary).getByText("Official")).toBeTruthy();

    const deploymentToggle = screen.getByRole("button", { name: /^Deployment/ });
    expect(deploymentToggle).toHaveAttribute("aria-expanded", "false");
    const deploymentSection = deploymentToggle.closest("section")!;
    expect(within(deploymentSection).queryByText("Local laptop")).toBeNull();

    const benchmarkToggle = screen.getByRole("button", { name: /^Benchmark/ });
    expect(benchmarkToggle).toHaveAttribute("aria-expanded", "true");
    const benchmarkSection = benchmarkToggle.closest("section")!;
    expect(within(benchmarkSection).getAllByRole("checkbox").length).toBe(3);
  });

  it("toggles a group expanded and collapsed via its header button", () => {
    render(
      <FacetRail
        groups={COLLAPSE_GROUPS}
        resultCount={20}
        activeChips={[]}
        onToggle={vi.fn()}
        onReset={vi.fn()}
      />,
    );

    const trustToggle = screen.getByRole("button", { name: /^Trust/ });
    fireEvent.click(trustToggle);
    expect(trustToggle).toHaveAttribute("aria-expanded", "true");
    const trustSection = trustToggle.closest("section")!;
    expect(within(trustSection).getAllByRole("checkbox").length).toBe(2);

    fireEvent.click(trustToggle);
    expect(trustToggle).toHaveAttribute("aria-expanded", "false");
    expect(within(trustSection).queryByRole("checkbox")).toBeNull();
  });

  it("preserves searchable behavior on expanded groups", () => {
    render(
      <FacetRail
        groups={COLLAPSE_GROUPS}
        resultCount={20}
        activeChips={[]}
        onToggle={vi.fn()}
        onReset={vi.fn()}
      />,
    );

    const benchmarkSection = screen
      .getByRole("button", { name: /^Benchmark/ })
      .closest("section")!;
    fireEvent.input(within(benchmarkSection).getByRole("searchbox"), {
      target: { value: "tpc-d" },
    });
    expect(within(benchmarkSection).getByText("TPC-DS")).toBeTruthy();
    expect(within(benchmarkSection).queryByText("TPC-H")).toBeNull();
    expect(within(benchmarkSection).queryByText("ClickBench")).toBeNull();
  });
});

describe("FacetDrawer", () => {
  it("shows a closed summary and opens the facet dialog", () => {
    const onOpenChange = vi.fn();

    const { rerender } = render(
      <FacetDrawer
        groups={GROUPS}
        resultCount={16}
        activeChips={[{ key: "benchmark:tpch", label: "TPC-H" }]}
        onToggle={vi.fn()}
        onReset={vi.fn()}
        open={false}
        onOpenChange={onOpenChange}
      />,
    );

    const trigger = screen.getByRole("button", { name: /Filters/ });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveAttribute("data-result-count", "16");
    expect(within(trigger).getByText("TPC-H")).toBeTruthy();
    expect(within(trigger).getByText("16 results")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Filter results" })).toBeNull();
    fireEvent.click(trigger);
    expect(onOpenChange).toHaveBeenCalledWith(true);

    rerender(
      <FacetDrawer
        groups={GROUPS}
        resultCount={16}
        activeChips={[{ key: "benchmark:tpch", label: "TPC-H" }]}
        onToggle={vi.fn()}
        onReset={vi.fn()}
        open
        onOpenChange={onOpenChange}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Filter results" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("tabindex", "-1");
    expect(dialog.className).toContain("fixed inset-0");

    dialog.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("button", { name: "Done" })).toHaveFocus();

    expect(within(dialog).getByRole("button", { name: "Reset filters" })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply filters" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);

    fireEvent.click(screen.getByRole("button", { name: "Close filters" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
