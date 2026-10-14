import { expect, test } from "@playwright/test";
import { waitForDataElement, waitForDataLoaded, waitForShell } from "../support/fixtures";

test.describe("BenchmarkIndex", () => {
  test("@smoke loads directly at /results/tpch/ and syncs SF filter into the URL", async ({
    page,
  }) => {
    await page.goto("/results/tpch/");
    await waitForShell(page);

    await waitForDataLoaded(page, /TPC-H Results/);
    await expect(page.getByRole("heading", { name: /TPC-H Results/ })).toBeVisible();

    await expect(page).toHaveURL(/\/results\/tpch\//);
    const sfValue = await page.evaluate(() => new URL(window.location.href).searchParams.get("sf"));
    expect(sfValue === null || sfValue === "0.01").toBeTruthy();
  });

  test("renders a platform row per platform in the fixture corpus", async ({ page }) => {
    await page.goto("/results/tpch/");
    await waitForShell(page);
    await expect(page.getByRole("heading", { name: /TPC-H Results/ })).toBeVisible();

    const table = page.getByRole("table", { name: /tpch SF 0\.01 power results/i });
    await waitForDataElement(page, table.locator("tbody tr").first());
    for (const platform of ["DuckDB", "DataFusion", "Polars"]) {
      await expect(table.getByText(platform, { exact: false }).first()).toBeVisible({
        timeout: 20_000,
      });
    }
  });

  test("benchmark switcher only lists benchmarks with public results", async ({ page }) => {
    await page.goto("/results/tpch/");
    await waitForShell(page);

    const switcher = page.getByTestId("benchmark-switcher");
    await expect(switcher).toBeVisible();
    await expect.poll(async () => (await switcher.locator("option").count())).toBeGreaterThan(0);

    const labels = (await switcher.locator("option").allTextContents()).map((label) => label.trim());
    expect(labels).toContain("TPC-H");
    expect(labels).toContain("SSB");
    expect(labels).not.toContain("AMPLab");
    expect(labels).not.toContain("TPC-DI");
    expect(labels).not.toContain("TPC-DS");
  });

  test("direct route to a no-result benchmark renders an empty-state with Back to Results", async ({ page }) => {
    await page.goto("/results/amplab/");
    await waitForShell(page);

    await expect(page.getByRole("heading", { name: "AMPLab", level: 1 })).toBeVisible();
    await expect(page.getByText("No published results yet for AMPLab.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to Results" })).toBeVisible();
  });
});
