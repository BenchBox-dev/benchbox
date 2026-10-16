import { expect, test } from "@playwright/test";
import { fixtureIds, waitForShell } from "../support/fixtures";

const RUN_IDS = `${fixtureIds.shortIds.duckdb},${fixtureIds.shortIds.datafusion}`;
const CHART_IDS = ["distribution_box", "cdf_chart"] as const;

test.describe("entry links", () => {
  test("serves one header, main and footer once the app has mounted", async ({ page }) => {
    await page.goto("/results/");
    await waitForShell(page);

    await expect(page.locator('header nav[aria-label="BenchBox"]')).toHaveCount(1);
    await expect(page.locator("main#main")).toHaveCount(1);
    await expect(page.locator("footer")).toHaveCount(1);
    await expect(page.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });

  test("moves focus to the main region from the skip link", async ({ page }) => {
    await page.goto("/results/");
    await waitForShell(page);

    await page.getByRole("link", { name: "Skip to content" }).focus();
    await page.keyboard.press("Enter");

    await expect(page.locator("main#main")).toBeFocused();
    await expect(page).toHaveURL(/\/results\/#main$/);
  });

  for (const chartId of CHART_IDS) {
    test(`opens the comparison at the ${chartId} chart from the compare link`, async ({ page }) => {
      await page.goto(`/results/compare?ids=${RUN_IDS}&chart=${chartId}`);
      await waitForShell(page);

      const chart = page.getByTestId(`chart-panel-chart-${chartId}`);
      await expect(chart).toBeFocused({ timeout: 30_000 });
      expect(new URL(page.url()).searchParams.get("chart")).toBe(chartId);
    });

    test(`redirects the older ${chartId} link to the same chart`, async ({ page }) => {
      await page.goto(`/results/?chart=${chartId}&ids=${RUN_IDS}`);
      await waitForShell(page);

      await expect(page).toHaveURL(/\/results\/compare\?/);
      await expect(page.getByTestId(`chart-panel-chart-${chartId}`)).toBeFocused({ timeout: 30_000 });
    });
  }

  test("redirects the older compare flag link to the selected runs", async ({ page }) => {
    await page.goto(`/results/?compare=true&ids=${RUN_IDS}`);
    await waitForShell(page);

    await expect(page).toHaveURL(/\/results\/compare\?/);
    expect(new URL(page.url()).searchParams.get("ids")?.split(",").sort()).toEqual(RUN_IDS.split(",").sort());
  });

  for (const entry of ["/results/?mode=local", "/results/#local", "/results/local"]) {
    test(`focuses the local file chooser from ${entry}`, async ({ page }) => {
      await page.goto(entry);
      await waitForShell(page);

      await expect(page).toHaveURL(/\/results\/local$/);
      await expect(page.getByRole("button", { name: "Choose result file" })).toBeFocused();
      await expect(page.getByTestId("local-result-file-input").first()).toBeAttached();
    });
  }
});
