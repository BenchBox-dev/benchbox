import { expect, test } from "@playwright/test";
import { waitForDataLoaded, waitForShell } from "../support/fixtures";

function searchParam(url: string, key: string): string | null {
  return new URL(url).searchParams.get(key);
}

test.describe("BenchmarkIndex URL validation", () => {
  test("invalid phase query state is replaced with an available phase", async ({ page }) => {
    await page.goto("/results/tpch/?sf=0.01&phase=throughput");
    await waitForShell(page);
    await waitForDataLoaded(page, /TPC-H Results/);

    await expect
      .poll(() => searchParam(page.url(), "phase"), { timeout: 20_000 })
      .not.toBe("throughput");

    await expect(page.getByRole("table", { name: /tpch SF 0\.01 power results/i })).toBeVisible();
    await expect(page.getByText(/No benchmark data available/i)).not.toBeVisible();
  });

  test("invalid scale-factor query state is replaced with a known scale factor", async ({ page }) => {
    await page.goto("/results/tpch/?sf=abc&phase=power");
    await waitForShell(page);
    await waitForDataLoaded(page, /TPC-H Results/);

    await expect
      .poll(() => searchParam(page.url(), "sf"), { timeout: 20_000 })
      .toBe("0.01");

    await expect(page.getByRole("table", { name: /tpch SF 0\.01 power results/i })).toBeVisible();
    await expect(page.getByText(/No benchmark data available/i)).not.toBeVisible();
  });
});
