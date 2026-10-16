import { render, waitFor } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("preact-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("preact-router")>()),
  route: vi.fn(),
}));

vi.mock("@/db", () => ({
  queryRows: vi.fn(),
  resetDuckDbInitializationFailures: vi.fn(),
}));

import { route } from "preact-router";
import { queryRows } from "@/db";
import { clearDuckdbQueryCachesForTests } from "@/lib/duckdbQueries";
import { Home } from "@/pages/Home";

beforeEach(() => {
  vi.clearAllMocks();
  clearDuckdbQueryCachesForTests();
  vi.mocked(queryRows).mockResolvedValue([]);
});

function openAt(url: string) {
  window.history.replaceState(null, "", url);
  return render(<Home />);
}

describe("Home entry redirects", () => {
  it.each([
    ["/results/?mode=local", "/results/local"],
    ["/results/#local", "/results/local"],
    ["/results/?compare=true&ids=a,b", "/results/compare?ids=a%2Cb"],
    ["/results/?chart=distribution_box&ids=a,b", "/results/compare?ids=a%2Cb&chart=distribution_box"],
    ["/results/?chart=cdf_chart&ids=a,b", "/results/compare?ids=a%2Cb&chart=cdf_chart"],
  ])("sends %s to %s before loading any data", async (url, target) => {
    openAt(url);

    await waitFor(() => expect(route).toHaveBeenCalledWith(target, true));
    expect(queryRows).not.toHaveBeenCalled();
  });

  it("loads the overview data when there is nothing to redirect", async () => {
    openAt("/results/");

    await waitFor(() => expect(queryRows).toHaveBeenCalled());
    expect(route).not.toHaveBeenCalled();
  });
});
