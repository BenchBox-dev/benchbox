import { fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CapturedPlanViewer } from "@/components/CapturedPlanViewer";
import { MAX_PLAN_SIDECAR_BYTES } from "@/lib/queryPlans";

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
});
