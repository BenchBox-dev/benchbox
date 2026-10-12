import { expect, test } from "@playwright/test";
import { fixtureIds, waitForDataElement, waitForDataLoaded, waitForShell } from "../support/fixtures";

test.describe("tray accessibility: announcements, focus, escape", () => {
  test.describe.configure({ mode: "serial" });

  test("tray is a region with accessible name and no focus trap", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openBenchmarkTray(page);
    const tray = page.getByTestId("compare-tray");
    await expect(tray).toHaveAttribute("role", "region");
    await expect(tray).toHaveAttribute("aria-label", "Comparison selection");
    await expect(tray).not.toHaveAttribute("role", "dialog");
    await expect(page.getByTestId("compare-tray-compare-link")).toBeVisible();
  });

  test("selection changes announce once via polite live region, focus stays on checkbox", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/results/tpch/");
    await waitForShell(page);
    await waitForDataLoaded(page, /TPC-H Results/);

    const duckdbCheckbox = page
      .locator(`[data-testid="list-${fixtureIds.ids.duckdb}"]:visible`)
      .first()
      .getByRole("checkbox");
    await waitForDataElement(page, page.getByTestId(`list-${fixtureIds.ids.duckdb}`).first());
    await duckdbCheckbox.scrollIntoViewIfNeeded();
    await duckdbCheckbox.focus();
    await duckdbCheckbox.check();
    await expect(duckdbCheckbox).toBeFocused();

    const datafusionCheckbox = page
      .locator(`[data-testid="list-${fixtureIds.ids.datafusion}"]:visible`)
      .first()
      .getByRole("checkbox");
    await datafusionCheckbox.focus();
    await datafusionCheckbox.check();
    await expect(datafusionCheckbox).toBeFocused();
    await expect(page.getByTestId("compare-tray")).toBeVisible();
    const announcer = page.getByTestId("compare-tray-announcer");
    await expect(announcer).toHaveAttribute("aria-live", "polite");
    await expect.poll(() => announcer.textContent() || "", { timeout: 5000 }).toMatch(/Ready to compare|2 results selected|result selected/i);
  });

  test("collapse control is button with aria-expanded and aria-controls", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openBenchmarkTray(page);
    const toggle = page.getByTestId("compare-tray-toggle");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle).toHaveAttribute("aria-controls", "compare-tray-details");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  test("Escape collapses without clearing and focus lands on valid element", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openBenchmarkTray(page);
    const toggle = page.getByTestId("compare-tray-toggle");
    await toggle.click();
    await expect(page.getByTestId("compare-tray")).toHaveAttribute("data-collapsed", "false");
    await expect(toggle).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("compare-tray")).toHaveAttribute("data-collapsed", "true");
    await expect(page.getByTestId("compare-tray-compare-link")).toBeVisible();
    const focused = await page.evaluate(() => document.activeElement?.getAttribute("data-testid") || document.activeElement?.tagName);
    expect(focused).toBeTruthy();
  });

  test("Escape outside tray does not collapse", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openBenchmarkTray(page);
    await page.getByTestId("compare-tray-toggle").click();
    await expect(page.getByTestId("compare-tray")).toHaveAttribute("data-collapsed", "false");
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("compare-tray")).toHaveAttribute("data-collapsed", "false");
  });

  test("disabled reason preserved via aria-describedby on capped checkbox", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/results/tpch/");
    await waitForShell(page);
    await waitForDataLoaded(page, /TPC-H Results/);

    const checkboxes = page.getByRole("checkbox", { name: /Select .* for comparison/i });
    await expect(checkboxes.first()).toBeVisible();
    await expect(page.getByTestId("list-row-disabled-reason").first()).toBeVisible();
    const n = await checkboxes.count();
    expect(n).toBeGreaterThanOrEqual(11);

    const handles = await checkboxes.all();
    const comparable: typeof handles = [];
    for (const cb of handles) {
      if (await cb.isEnabled()) comparable.push(cb);
      if (comparable.length >= 11) break;
    }
    expect(comparable.length).toBeGreaterThanOrEqual(11);
    for (let i = 0; i < 10; i++) {
      const cb = comparable[i];
      if (!cb) throw new Error(`expected comparable checkbox at index ${i}`);
      await expect(cb).toBeEnabled();
      await cb.scrollIntoViewIfNeeded();
      await cb.check();
      await expect(cb).toBeChecked();
    }

    const eleventh = comparable[10];
    if (!eleventh) throw new Error("expected eleventh comparable checkbox for cap assertion");
    await expect(eleventh).toBeDisabled();
    const describedBy = await eleventh.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const reason = page.locator(`#${describedBy}`);
    await expect(reason).toBeVisible();
    expect(await reason.textContent()).toMatch(/Up to 10|compare|selection/i);

    await expect(page.getByTestId("compare-tray")).toHaveAttribute("role", "region");
    await expect(page.getByTestId("compare-tray-announcer")).toHaveAttribute("aria-live", "polite");
  });
});

async function openBenchmarkTray(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/results/tpch/");
  await waitForShell(page);
  await waitForDataLoaded(page, /TPC-H Results/);
  const row1 = page
    .locator(`[data-testid="list-${fixtureIds.ids.duckdb}"]:visible`)
    .first();
  const row2 = page
    .locator(`[data-testid="list-${fixtureIds.ids.datafusion}"]:visible`)
    .first();
  await waitForDataElement(page, row1);
  await row1.scrollIntoViewIfNeeded();
  await row1.getByRole("checkbox").check();
  await waitForDataElement(page, row2);
  await row2.scrollIntoViewIfNeeded();
  await row2.getByRole("checkbox").check();
  await expect(page.getByTestId("compare-tray")).toBeVisible();
}
