import { expect, test } from "@playwright/test";
import { waitForDataLoaded, waitForShell } from "../support/fixtures";

test("matrix direction uses one reading rule across every visible column", async ({ page }) => {
  await page.goto("/results/compare/");
  await waitForShell(page);
  await waitForDataLoaded(page, /Cross-benchmark rankings/);

  await expect(page.getByRole("radio", { name: "Relative to best" })).toHaveAttribute("aria-checked", "true");
  expect(new URL(page.url()).searchParams.get("mode")).toBeNull();

  const grid = page.getByRole("grid", { name: "Cross-benchmark leaderboard" });
  const rankingHeaders = grid.locator("thead th").filter({ has: page.locator("a[href]") });
  const headerCount = await rankingHeaders.count();
  expect(headerCount).toBeGreaterThan(0);

  const sharedLegend = page.locator("#meta-leaderboard-legend");
  await expect(sharedLegend).toContainText("farther from the best result (1×)");
  await expect(sharedLegend).toContainText("Values below 1× are worse");

  for (let index = 0; index < headerCount; index += 1) {
    const header = rankingHeaders.nth(index);
    await expect(header).toContainText(/(higher|lower) is better/);
  }

  const rankedCells = grid.locator('tbody [role="gridcell"]:has-text("Native:")');
  expect(await rankedCells.count()).toBeGreaterThan(0);
  await expect(rankedCells.first()).toContainText(/1×|0\.\d+×/);
});
