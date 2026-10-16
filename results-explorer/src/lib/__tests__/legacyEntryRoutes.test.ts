import { describe, expect, it } from "vitest";
import { legacyEntryRedirect } from "@/lib/legacyEntryRoutes";

describe("legacyEntryRedirect", () => {
  it("sends the local entry shapes to the local result page", () => {
    expect(legacyEntryRedirect("?mode=local", "")).toBe("/results/local");
    expect(legacyEntryRedirect("", "#local")).toBe("/results/local");
  });

  it("sends run selections to the compare page", () => {
    expect(legacyEntryRedirect("?compare=true&ids=a,b", "")).toBe("/results/compare?ids=a%2Cb");
    expect(legacyEntryRedirect("?ids=a,b", "")).toBe("/results/compare?ids=a%2Cb");
  });

  it("keeps the chart value with the selection", () => {
    expect(legacyEntryRedirect("?chart=distribution_box&ids=a,b", "")).toBe(
      "/results/compare?ids=a%2Cb&chart=distribution_box",
    );
    expect(legacyEntryRedirect("?chart=cdf_chart&ids=a,b", "")).toBe("/results/compare?ids=a%2Cb&chart=cdf_chart");
  });

  it("opens the compare page without a selection when only the flag is present", () => {
    expect(legacyEntryRedirect("?compare=true", "")).toBe("/results/compare");
  });

  it("leaves the overview alone when no entry parameter is present", () => {
    expect(legacyEntryRedirect("", "")).toBeNull();
    expect(legacyEntryRedirect("?benchmark=tpch", "#top")).toBeNull();
  });
});
