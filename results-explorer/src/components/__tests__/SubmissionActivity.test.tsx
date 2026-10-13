import { act, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, it, expect, vi } from "vitest";

import { SubmissionActivity } from "@/components/SubmissionActivity";

const REFERENCE = new Date("2026-09-08T00:00:00Z");

it("exposes weekly values without hover", () => {
  render(<SubmissionActivity rows={[{ id: "tpch", label: "TPC-H", href: "/results/tpch/", dates: ["2026-09-08"] }]} subject="benchmark" reference={REFERENCE} />);
  expect(screen.getByRole("cell", { name: "TPC-H: 1 run in the week of 2026-09-07" })).toBeTruthy();
});

function rowLabels(): string[] {
  const grid = screen.getByTestId("submission-activity-grid");
  return Array.from(grid.querySelectorAll("th[scope='row']")).map((cell) => cell.textContent ?? "");
}

function stubViewport(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })),
  );
}

function stubResizableViewport(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners = new Set<() => void>();
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      get matches() {
        return matches;
      },
      media: query,
      addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
    })),
  );
  return {
    change(next: boolean) {
      matches = next;
      for (const listener of listeners) listener();
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SubmissionActivity", () => {
  const rows = [
    { id: "tpch", label: "TPC-H", href: "/results/tpch/", dates: ["2026-09-01", "2026-09-02", "2026-08-20"] },
    { id: "ssb", label: "SSB", href: "/results/ssb/", dates: ["2026-08-20"] },
  ];

  it("orders subjects by submissions inside the window", () => {
    render(<SubmissionActivity rows={rows} subject="benchmark" reference={REFERENCE} />);
    expect(rowLabels()).toEqual(["TPC-H", "SSB"]);
  });

  it("counts runs per subject and links each row to its page", () => {
    render(<SubmissionActivity rows={rows} subject="benchmark" reference={REFERENCE} />);
    const grid = screen.getByTestId("submission-activity-grid");
    const firstRow = grid.querySelector("tbody tr")!;
    expect(within(firstRow as HTMLElement).getByRole("link", { name: "TPC-H" })).toHaveAttribute(
      "href",
      "/results/tpch/",
    );
    expect((firstRow.lastElementChild as HTMLElement).textContent).toBe("3");
  });

  it("drops subjects whose runs all fall outside the window", () => {
    render(
      <SubmissionActivity
        rows={[...rows, { id: "old", label: "Ancient", href: "/results/old/", dates: ["2020-01-01"] }]}
        subject="benchmark"
        reference={REFERENCE}
      />,
    );
    expect(rowLabels()).not.toContain("Ancient");
  });

  it("anchors the window to the newest submission when the corpus is stale", () => {
    render(
      <SubmissionActivity
        rows={[{ id: "tpch", label: "TPC-H", href: "/results/tpch/", dates: ["2024-03-04", "2024-03-11"] }]}
        subject="benchmark"
        reference={REFERENCE}
      />,
    );
    expect(rowLabels()).toEqual(["TPC-H"]);
  });

  it("renders nothing when no run carries a usable date", () => {
    const { container } = render(
      <SubmissionActivity
        rows={[{ id: "x", label: "X", href: "/x", dates: ["not-a-date"] }]}
        subject="benchmark"
        reference={REFERENCE}
      />,
    );
    expect(container.textContent).toBe("");
  });

  it("collapses the long tail into a count rather than a hundred rows", () => {
    const many = Array.from({ length: 25 }, (_, index) => ({
      id: `p${index}`,
      label: `Platform ${index}`,
      href: `/results/p/${index}/`,
      dates: ["2026-09-01"],
    }));
    render(<SubmissionActivity rows={many} subject="platform" reference={REFERENCE} maxRows={20} />);
    expect(rowLabels()).toHaveLength(20);
    expect(screen.getByText("5 less active platforms not shown.")).toBeTruthy();
  });

  it("narrows the week window below the sm breakpoint so every cell stays on screen", () => {
    stubViewport(true);
    const rows = [
      { id: "tpch", label: "TPC-H", href: "/results/tpch/", dates: ["2026-09-01", "2026-08-20"] },
    ];
    render(<SubmissionActivity rows={rows} subject="benchmark" reference={REFERENCE} />);

    expect(screen.getByText(/Runs per week over the last 26 weeks/)).toBeTruthy();
    expect(screen.getByText(/Showing the trailing 10 of 26 weeks; totals cover all 26/)).toBeTruthy();
    const grid = screen.getByTestId("submission-activity-grid");
    const firstDataRow = within(grid).getAllByRole("row")[0]!;
    expect(firstDataRow.children).toHaveLength(12);
  });

  it("uses the full desktop week window when the viewport is not narrow", () => {
    stubViewport(false);
    const rows = [
      { id: "tpch", label: "TPC-H", href: "/results/tpch/", dates: ["2026-09-01", "2026-08-20"] },
    ];
    render(<SubmissionActivity rows={rows} subject="benchmark" reference={REFERENCE} />);

    expect(screen.getByText(/Runs per week over the last 26 weeks/)).toBeTruthy();
    expect(screen.queryByText(/Showing the trailing/)).toBeNull();
    const grid = screen.getByTestId("submission-activity-grid");
    const firstDataRow = within(grid).getAllByRole("row")[0]!;
    expect(firstDataRow.children).toHaveLength(28);
  });

  it("keeps a subject last active between the mobile and desktop windows visible at both widths", () => {
    const oldDate = new Date(REFERENCE.getTime() - 15 * 7 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const rows = [
      { id: "tpch", label: "TPC-H", href: "/results/tpch/", dates: ["2026-09-01"] },
      { id: "old", label: "Old Platform", href: "/results/old/", dates: [oldDate] },
    ];

    stubViewport(false);
    const desktop = render(<SubmissionActivity rows={rows} subject="platform" reference={REFERENCE} />);
    expect(rowLabels()).toEqual(["Old Platform", "TPC-H"]);
    const desktopTotal = within(screen.getByTestId("submission-activity-grid"))
      .getAllByRole("row")[0]!.lastElementChild!.textContent;
    desktop.unmount();

    stubViewport(true);
    render(<SubmissionActivity rows={rows} subject="platform" reference={REFERENCE} />);
    expect(rowLabels()).toEqual(["Old Platform", "TPC-H"]);
    const mobileTotal = within(screen.getByTestId("submission-activity-grid"))
      .getAllByRole("row")[0]!.lastElementChild!.textContent;

    expect(mobileTotal).toBe(desktopTotal);
  });

  it("re-renders with the other window when matchMedia reports a viewport change", () => {
    const viewport = stubResizableViewport(false);
    const rows = [{ id: "tpch", label: "TPC-H", href: "/results/tpch/", dates: ["2026-09-01", "2026-08-20"] }];
    render(<SubmissionActivity rows={rows} subject="benchmark" reference={REFERENCE} />);

    expect(screen.getByText(/Runs per week over the last 26 weeks/)).toBeTruthy();
    let grid = screen.getByTestId("submission-activity-grid");
    expect(within(grid).getAllByRole("row")[0]!.children).toHaveLength(28);

    act(() => viewport.change(true));

    expect(screen.getByText(/Runs per week over the last 26 weeks/)).toBeTruthy();
    expect(screen.getByText(/Showing the trailing 10 of 26 weeks; totals cover all 26/)).toBeTruthy();
    grid = screen.getByTestId("submission-activity-grid");
    expect(within(grid).getAllByRole("row")[0]!.children).toHaveLength(12);
  });

  it("gives every week column the same width, so a single run does not stretch into a bar", () => {
    stubViewport(false);
    const rows = [
      { id: "doris", label: "Apache Doris", href: "/results/p/doris/", dates: ["2026-09-01"] },
      { id: "duck", label: "DuckDB", href: "/results/p/duckdb/", dates: ["2026-09-01", "2026-08-20", "2026-08-13"] },
    ];
    render(<SubmissionActivity rows={rows} subject="platform" reference={REFERENCE} />);

    const grid = screen.getByTestId("submission-activity-grid");
    const weekColumns = Array.from(grid.querySelectorAll("colgroup col")).slice(1, -1);
    expect(weekColumns).toHaveLength(26);
    expect(new Set(weekColumns.map((col) => col.getAttribute("class") ?? ""))).toEqual(new Set([""]));

    for (const row of Array.from(grid.querySelectorAll("tbody tr")).slice(0, 2)) {
      const weekCells = Array.from(row.children).slice(1, -1);
      expect(weekCells).toHaveLength(26);
      expect(new Set(weekCells.map((cell) => cell.getAttribute("class")))).toEqual(new Set(["p-0"]));
    }
  });

  it("explains the heatmap intensity with a legend from fewer to more runs", () => {
    render(
      <SubmissionActivity
        rows={[{ id: "tpch", label: "TPC-H", href: "/results/tpch/", dates: ["2026-09-01", "2026-08-20"] }]}
        subject="benchmark"
        reference={REFERENCE}
      />,
    );

    const legend = screen.getByTestId("submission-activity-legend");
    expect(legend).toHaveTextContent("fewer → more runs");
    expect(legend.querySelectorAll("[aria-hidden='true'] > span")).toHaveLength(5);
  });

  it("gives each activity label a title with its full name, even when it wraps", () => {
    stubViewport(true);
    const rows = [
      { id: "ch-local", label: "ClickHouse Local", href: "/results/p/clickhouse-local/", dates: ["2026-09-01"] },
      { id: "ch-server", label: "ClickHouse Server", href: "/results/p/clickhouse-server/", dates: ["2026-09-01"] },
    ];
    render(<SubmissionActivity rows={rows} subject="platform" reference={REFERENCE} />);

    const headers = Array.from(screen.getByTestId("submission-activity-grid").querySelectorAll("th[scope='row']"));
    expect(headers.map((header) => header.getAttribute("title"))).toEqual(["ClickHouse Local", "ClickHouse Server"]);
    expect(headers[0]!.className).toContain("break-words");
  });
});
