import { render, screen } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { SubscaleCohortNotice } from "@/components/SubscaleNotice";

describe("SubscaleCohortNotice", () => {
  it("shows the badge and TPC-DS disclaimer for a subscale TPC-DS cohort", () => {
    render(<SubscaleCohortNotice benchmark="tpcds" scaleFactor={0.1} />);

    const notice = screen.getByTestId("subscale-cohort-notice");
    expect(notice.textContent).toContain("Subscale · Unofficial");
    expect(notice.textContent).toContain("SF<1 is below TPC-DS's minimum scale.");
  });

  it("renders nothing at SF 1 or for other benchmarks", () => {
    const { container: sf1 } = render(<SubscaleCohortNotice benchmark="tpch" scaleFactor={1} />);
    const { container: ssb } = render(<SubscaleCohortNotice benchmark="ssb" scaleFactor={0.01} />);

    expect(sf1.textContent).toBe("");
    expect(ssb.textContent).toBe("");
  });
});
