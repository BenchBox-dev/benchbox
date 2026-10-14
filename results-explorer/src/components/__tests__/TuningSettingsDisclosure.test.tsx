import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { extractTuningBlock, TuningSettingsDisclosure } from "@/components/TuningSettingsDisclosure";

const BUNDLE_URL = "/results/bundles/tpch_sf1_duckdb_tuned.json";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TuningSettingsDisclosure", () => {
  it("loads and shows the bundle's tuning block on demand", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ platform: { tuning: { memory_limit: "8GB" } } }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TuningSettingsDisclosure bundleUrl={BUNDLE_URL} />);

    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Show settings/ }));

    await waitFor(() => expect(screen.getByText(/"memory_limit": "8GB"/)).toBeTruthy());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /Hide settings/ }).getAttribute("aria-expanded")).toBe("true");
  });

  it("shows an alert when the bundle cannot be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ json: async () => Promise.reject(new SyntaxError("bad json")) }));
    render(<TuningSettingsDisclosure bundleUrl={BUNDLE_URL} />);

    fireEvent.click(screen.getByRole("button", { name: /Show settings/ }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Could not load tuning settings."));
  });

  it("returns an empty block when the bundle has no platform tuning", () => {
    expect(extractTuningBlock({ platform: { name: "duckdb" } })).toEqual({});
    expect(extractTuningBlock({})).toEqual({});
  });
});
