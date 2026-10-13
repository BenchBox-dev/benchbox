import { expect, test } from "@playwright/test";
import { waitForShell } from "../support/fixtures";

test.describe("NotFound", () => {
  test("an unknown path renders the 404 page with a link back to results", async ({ page }) => {
    await page.goto("/results/no/such/route/");
    await waitForShell(page);

    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
    const backLink = page.getByRole("link", { name: "Browse results" });
    await expect(backLink).toHaveAttribute("href", "/results/");
  });

  test("an unknown :benchmark slug renders 404 with a benchmark-specific message", async ({ page }) => {
    await page.goto("/results/does-not-exist/");
    await waitForShell(page);

    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/Benchmark "does-not-exist" is not part of the published corpus/i)).toBeVisible();
  });

  test("a known benchmark with no published results shows an empty-corpus state, not 404", async ({ page }) => {
    await page.goto("/results/clickbench/");
    await waitForShell(page);

    await expect(page.getByRole("heading", { name: /^ClickBench$/ })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/No published results yet for ClickBench/i)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Page not found" })).not.toBeVisible();
  });
});
