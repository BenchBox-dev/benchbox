import type { ComponentChildren } from "preact";
import { render, screen } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/Layout", () => ({
  Layout: ({ children }: { children: ComponentChildren }) => <div>{children}</div>,
}));
vi.mock("@/lib/pickingState", () => ({
  PickingStateProvider: ({ children }: { children: ComponentChildren }) => <>{children}</>,
}));
vi.mock("@/lib/localResultState", () => ({
  LocalResultProvider: ({ children }: { children: ComponentChildren }) => <>{children}</>,
}));
vi.mock("@/pages/QueryRunComparison", () => ({
  QueryRunComparison: ({ queryId, url }: { queryId?: string; url?: string }) => (
    <div data-testid="query-run-route">{queryId}|{url}</div>
  ),
}));

import { App } from "@/App";

describe("single-query comparison deep link routing", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/results/compare/query/Q10?ids=run-b%2Crun-a&basis=warm_pass_2");
  });

  it("routes query IDs and their ordered run URL state to the single-query page", async () => {
    render(<App />);

    const route = await screen.findByTestId("query-run-route");
    expect(route).toHaveTextContent("Q10");
    expect(route).toHaveTextContent("ids=run-b%2Crun-a");
    expect(route).toHaveTextContent("basis=warm_pass_2");
  });

  it("accepts the trailing-slash form", async () => {
    window.history.replaceState(null, "", "/results/compare/query/Q1/?ids=run-a");
    render(<App />);

    expect(await screen.findByTestId("query-run-route")).toHaveTextContent("Q1");
  });
});
