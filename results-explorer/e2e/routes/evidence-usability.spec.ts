import { expect, test } from "@playwright/test";
import { fixtureIds, openAnalysisCard, waitForDataElement, waitForDataLoaded } from "../support/fixtures";

test("tablet matrix exposes query timings after horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.goto("/results/tpch/");
  await openAnalysisCard(page, "query_heatmap");
  const scroller = page.getByTestId("query-heatmap-scroll-container");
  const cell = scroller.locator("tbody td[data-cell]").first();
  await waitForDataElement(page, cell);
  await cell.scrollIntoViewIfNeeded();
  await scroller.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
  const lastCell = scroller.locator("tbody tr").first().locator("td[data-cell]").last();
  const proof = await lastCell.evaluate((element) => {
    const box = element.getBoundingClientRect();
    const container = element.closest("[data-testid=query-heatmap-scroll-container]")!.getBoundingClientRect();
    const x = Math.min(box.right, container.right) - 8;
    const y = box.top + box.height / 2;
    return { text: element.textContent, visibleWidth: Math.min(box.right, container.right) - Math.max(box.left, container.left), hit: element.contains(document.elementFromPoint(x, y)) };
  });
  expect(proof.visibleWidth).toBeGreaterThan(40);
  expect(proof.text).toMatch(/ms|s/);
  expect(proof.hit).toBe(true);
  const qualifiers = scroller.getByTestId("visible-run-qualifier");
  await expect(qualifiers.first()).toBeVisible();
  const values = await qualifiers.allTextContents();
  expect(new Set(values).size).toBe(values.length);
});

test("single-run links retain the resolved run in the picker", async ({ page }) => {
  await page.goto(`/results/compare?ids=${fixtureIds.shortIds.duckdb}`);
  const pick = page.getByRole("link", { name: "Find runs to compare with this run" });
  await waitForDataElement(page, pick);
  await expect(pick).toHaveAttribute("href", `/results/query?pick=${fixtureIds.ids.duckdb}`);
  await pick.click();
  await waitForDataLoaded(page, /Find runs/);
  const tray = page.getByTestId(`query-compare-selected-${fixtureIds.ids.duckdb}`);
  await expect(tray).toBeVisible();
  await expect(tray).toContainText("DuckDB");
});

test("landing-card dates toggle without following the card link", async ({ page }) => {
  await page.goto("/results/platforms/");
  const chip = page.getByTestId("platforms-index-list").getByTestId("run-date-chip").first();
  await waitForDataElement(page, chip);
  const date = await chip.textContent();
  await chip.click();
  await expect(chip).toHaveAttribute("data-showing", "age");
  await expect(page).toHaveURL(/\/results\/platforms\/$/);
  await chip.click();
  await expect(chip).toHaveText(date!);
});

test("platform measurement basis changes latency evidence and preserves its comparison URL", async ({ page }) => {
  await page.goto("/results/p/duckdb/");
  const selector = page.getByRole("combobox", { name: "Measurement basis" });
  await waitForDataElement(page, selector);
  await page.getByTestId(`platform-compare-checkbox-${fixtureIds.ids.duckdb}`).check();
  await page.getByTestId(`platform-compare-checkbox-${fixtureIds.ids.duckdbTuned}`).check();
  await selector.selectOption("all_warm:min");
  await expect(page.getByText("Loading measurement passes…")).toHaveCount(0);
  const compare = page.getByTestId("compare-tray-compare-link");
  await expect(compare).toHaveAttribute("href", /basis=all_warm:min/);
  const row = page.getByTestId(fixtureIds.ids.duckdb);
  await expect(row.locator('td[aria-colindex="7"]')).toHaveText("—");
  await expect(row.locator('td[aria-colindex="8"]')).toHaveText(/\d.*(?:ms|s)/);
  await compare.click();
  await waitForDataLoaded(page, /TPC-H Comparison/);
  await expect(page.getByRole("region", { name: "Measurement basis" })).toContainText(/fastest/i);
});
