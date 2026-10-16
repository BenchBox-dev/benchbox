import { expect, test } from "@playwright/test";
import { waitForDataElement, waitForShell } from "../support/fixtures";

test.describe("results page before and after scripts run", () => {
  test("serves the shell, landmarks and fallbacks in the raw response", async ({ request }) => {
    const response = await request.get("/results/");
    expect(response.ok()).toBe(true);
    const html = await response.text();

    expect(html).toMatch(/<title>[^<]*Results[^<]*BenchBox[^<]*<\/title>/i);
    expect(html).toMatch(/<meta[^>]+name="description"[^>]+content="[^"]+"/i);
    expect(html).toMatch(/<a[^>]+href="#main"[^>]*>\s*Skip to content\s*<\/a>/i);
    expect(html).toMatch(/<nav[^>]+aria-label="BenchBox"/i);
    expect(html).toMatch(/<main[^>]+id="main"/i);
    expect(html).toMatch(/<noscript>[\s\S]*?<a[^>]+href="\/"[\s\S]*?<\/noscript>/i);
  });

  test("makes the skip link the first keyboard stop", async ({ page }) => {
    await page.goto("/results/");
    await waitForShell(page);

    await page.keyboard.press("Tab");
    const focused = page.locator(":focus");
    await expect(focused).toHaveText(/Skip to content/);
    await expect(focused).toHaveAttribute("href", "#main");
  });

  test("renders trust tiers in title case", async ({ page }) => {
    await page.goto("/results/tpch/");
    await waitForShell(page);

    const trustFilter = page.getByTestId("benchmark-trust-filter");
    await waitForDataElement(page, trustFilter);
    await expect(trustFilter.locator("option")).toHaveText([
      "All trust tiers",
      "Community Submission",
      "Maintainer Run",
      "Vendor Supplied",
    ]);
  });
});
