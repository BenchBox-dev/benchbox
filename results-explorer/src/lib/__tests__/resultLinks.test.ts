import { describe, expect, it } from "vitest";
import {
  buildCompareUrl,
  compareIdForRow,
  MAX_COMPARE_SELECTIONS,
  publicResultIdToken,
  resultDetailHref,
  resultIdentityAriaLabel,
  resultReceiptHref,
  visibleResultIdForRow,
} from "@/lib/resultLinks";

describe("result links and public identity", () => {
  const row = {
    result_id: "tpch-polars-sf0.01-20260403-a62d0d72",
    short_id: "d28345e6",
    platform: "Polars",
    run_date: "2026-04-03T12:00:00Z",
  };

  it("keeps compare URL aliases separate from visible public IDs", () => {
    expect(compareIdForRow(row)).toBe("d28345e6");
    expect(visibleResultIdForRow(row)).toBe("a62d0d72");
    expect(publicResultIdToken(row.result_id)).toBe("a62d0d72");
    expect(publicResultIdToken("synthetic-result-id")).toBe("synthetic-result-id");
  });

  it("supports up to ten exact result IDs in compare URLs", () => {
    const ids = Array.from({ length: 10 }, (_, index) => `result-${index + 1}`);
    expect(MAX_COMPARE_SELECTIONS).toBe(10);
    expect(buildCompareUrl(ids)).toBe(`/results/compare?ids=${ids.join(",")}`);
  });

  it("builds canonical result detail and receipt hrefs from result_id", () => {
    expect(resultDetailHref(row)).toBe("/results/r/tpch-polars-sf0.01-20260403-a62d0d72");
    expect(resultReceiptHref(row)).toBe("/results/r/tpch-polars-sf0.01-20260403-a62d0d72#run-receipt");
  });

  it("names the public ID in accessible result-link labels", () => {
    expect(resultIdentityAriaLabel(row, "receipt")).toMatch(
      /^Open receipt for Polars public ID a62d0d72 from 2026-04-03 \(.*days ago\)$/,
    );
  });
});
