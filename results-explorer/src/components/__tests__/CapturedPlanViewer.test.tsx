import { fireEvent, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CapturedPlanViewer } from "@/components/CapturedPlanViewer";
import { MAX_PLAN_SIDECAR_BYTES } from "@/lib/queryPlans";

const MULTI_QUERY_SIDECAR = {
  version: "1.0",
  queries: {
    "18": {
      plan: {
        query_id: "18",
        platform: "datafusion",
        logical_root: { operator_type: "Aggregate", group_by_keys: ["c_name"], children: [] },
      },
    },
    "Q19": {
      plan: {
        query_id: "Q19",
        platform: "datafusion",
        logical_root: { operator_type: "Filter", filter_expressions: ["p_brand <> 'Brand#45'"], children: [] },
      },
    },
  },
};

function stubSidecar(body: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, text: async () => JSON.stringify(body) }));
}

const DAG_SIDECAR = {
  version: "1.0",
  queries: {
    Q1: {
      plan_format: "dag",
      plan: {
        query_id: "Q1",
        platform: "duckdb",
        logical_root: {
          operator_type: "Join",
          join_type: "inner",
          children: [{ operator_type: "Scan", table_name: "lineitem", children: [] }],
        },
      },
    },
  },
};

afterEach(() => vi.unstubAllGlobals());

describe("CapturedPlanViewer", () => {
  it("loads a published sidecar on demand and shows its per-query operator tree", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify(DAG_SIDECAR),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    expect(await screen.findByRole("region", { name: "Query plan for Q1" })).toBeTruthy();
    expect(screen.getByText("Join")).toBeTruthy();
    expect(screen.getByText("Table: lineitem")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith("/results/r1.plans.json", expect.any(Object));
  });

  it("renders plan text as text and never as markup", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: async () => JSON.stringify({ queries: { Q1: { plan: "<img src=x onerror=alert(1)>" } } }),
      }),
    );
    const { container } = render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    expect(await screen.findByText("<img src=x onerror=alert(1)>" )).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows a clear unavailable state when the sidecar cannot be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, text: async () => "" }));
    render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Published query plans are unavailable. Use Download plans to get the sidecar file.",
    );
  });

  it("rejects an oversized sidecar from Content-Length before reading its body", async () => {
    const text = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        headers: { get: () => "2000001" },
        text,
      }),
    );
    render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Published query plans are unavailable.");
    expect(text).not.toHaveBeenCalled();
  });

  it("stops reading a body that exceeds the viewer limit when Content-Length is absent", async () => {
    const overflow = new TextEncoder().encode("x".repeat(MAX_PLAN_SIDECAR_BYTES + 1));
    const chunks: Uint8Array[] = [overflow.subarray(0, 7), overflow.subarray(7)];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        headers: { get: () => null },
        body: {
          getReader: () => {
            let index = 0;
            return {
              read: async () =>
                index < chunks.length
                  ? { done: false, value: chunks[index++]! }
                  : { done: true, value: undefined },
              cancel: async () => undefined,
              releaseLock: () => undefined,
            };
          },
        },
      }),
    );
    render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Published query plans are unavailable.");
  });

  it("drops the first result's plans and loads the new sidecar when the result changes", async () => {
    const secondSidecar = {
      version: "1.0",
      queries: {
        Q2: {
          plan_format: "dag",
          plan: {
            query_id: "Q2",
            platform: "duckdb",
            logical_root: { operator_type: "Scan", table_name: "orders", children: [] },
          },
        },
      },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, text: async () => JSON.stringify(DAG_SIDECAR) })
      .mockResolvedValueOnce({ ok: true, text: async () => JSON.stringify(secondSidecar) });
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));
    expect(await screen.findByRole("region", { name: "Query plan for Q1" })).toBeTruthy();

    rerender(<CapturedPlanViewer plansUrl="/results/r2.plans.json" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));
    expect(await screen.findByRole("region", { name: "Query plan for Q2" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Query plan for Q1" })).toBeNull();
    expect(fetchMock).toHaveBeenLastCalledWith("/results/r2.plans.json", expect.any(Object));
  });

  it("states when plans were captured without a published file and does not fetch", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<CapturedPlanViewer plansUrl={null} />);

    expect(screen.getByText("Execution plans were recorded, but no published plan file is available for this result."))
      .toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows only the plan for the compared query when queryId is given", async () => {
    stubSidecar(MULTI_QUERY_SIDECAR);
    render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" queryId="18" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    expect(await screen.findByRole("region", { name: "Query plan for 18" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Query plan for Q19" })).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("shows a clear message when the run has no plan for the compared query", async () => {
    stubSidecar(MULTI_QUERY_SIDECAR);
    render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" queryId="7" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    expect(await screen.findByText("No captured plan for query 7 in this run.")).toBeTruthy();
    expect(screen.queryByRole("region", { name: /Query plan for/ })).toBeNull();
  });

  it("matches Q-prefixed and bare query ids in both directions", async () => {
    stubSidecar(MULTI_QUERY_SIDECAR);
    const { unmount } = render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" queryId="Q19" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    expect(await screen.findByRole("region", { name: "Query plan for Q19" })).toBeTruthy();
    unmount();

    stubSidecar(MULTI_QUERY_SIDECAR);
    render(<CapturedPlanViewer plansUrl="/results/r2.plans.json" queryId="19" />);
    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    expect(await screen.findByRole("region", { name: "Query plan for Q19" })).toBeTruthy();
  });

  it("keeps the query selector for a query with several captured plans", async () => {
    stubSidecar({
      queries: {
        "Q18#1": { plan: { query_id: "Q18", logical_root: { operator_type: "Scan", children: [] } } },
        "Q18#2": { plan: { query_id: "Q18", logical_root: { operator_type: "Join", children: [] } } },
        Q19: { plan: { query_id: "Q19", logical_root: { operator_type: "Filter", children: [] } } },
      },
    });
    render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" queryId="18" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    const selector = await screen.findByRole("combobox");
    expect(within(selector).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Q18#1",
      "Q18#2",
    ]);
  });

  it("keeps long plan text inside an overflow-x-auto container so the page does not overflow", async () => {
    const longOperand = "l_shipdate_between_" + "x".repeat(240);
    stubSidecar({
      queries: {
        "18": {
          plan: {
            query_id: "18",
            logical_root: { operator_type: "Filter", filter_expressions: [longOperand], children: [] },
          },
        },
      },
    });
    render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" queryId="18" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));

    const region = await screen.findByRole("region", { name: "Query plan for 18" });
    expect(region.classList.contains("overflow-x-auto")).toBe(true);
    expect(region.classList.contains("max-w-full")).toBe(true);
    expect(region.querySelector("[data-testid='captured-plan-node']")).not.toBeNull();
    expect(region.textContent).toContain(longOperand);
    expect(region.querySelector("li.break-words")?.textContent).toContain(longOperand);
  });

  it("renders plan text with the 12px text-xs size and no sub-11px size classes", async () => {
    stubSidecar({
      queries: {
        "18": {
          plan: {
            query_id: "18",
            logical_root: { operator_type: "Scan", table_name: "lineitem", children: [] },
          },
        },
      },
    });
    const { container } = render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" queryId="18" />);

    fireEvent.click(screen.getByRole("button", { name: "View query plans" }));
    const region = await screen.findByRole("region", { name: "Query plan for 18" });

    expect(screen.getByText("Table: lineitem").classList.contains("break-words")).toBe(true);
    expect(screen.getByText("Table: lineitem").closest("ul")?.classList.contains("text-xs")).toBe(true);
    expect(region.querySelectorAll("[class*='text-[0-9']")).toHaveLength(0);
    expect(container.querySelectorAll("[class*='text-[0-9']")).toHaveLength(0);
  });

  it("toggles the plan region from a native button that is reachable by keyboard", async () => {
    stubSidecar(DAG_SIDECAR);
    render(<CapturedPlanViewer plansUrl="/results/r1.plans.json" />);

    const toggle = screen.getByRole("button", { name: "View query plans" });
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.getAttribute("tabindex")).toBeNull();
    toggle.focus();
    expect(document.activeElement).toBe(toggle);

    fireEvent.keyDown(toggle, { key: "Enter" });
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(await screen.findByRole("region", { name: "Query plan for Q1" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Hide query plans" }));
    expect(screen.getByRole("button", { name: "View query plans" }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("region", { name: "Captured query plans" })).toBeNull();
  });
});
