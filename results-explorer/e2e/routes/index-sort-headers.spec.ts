import { expect, test, type Locator } from "@playwright/test";
import { openAnalysisCard, waitForDataLoaded, waitForResultRows, waitForShell } from "../support/fixtures";

function sortableHeader(scope: Locator, label: RegExp): Locator {
  return scope.locator("th[aria-sort]").filter({ hasText: label }).first();
}

async function rowIds(rows: Locator): Promise<string[]> {
  return rows.evaluateAll((elements) =>
    elements.map((row) => row.getAttribute("data-testid") ?? ""),
  );
}

async function columnTexts(rows: Locator, columnIndex: number): Promise<string[]> {
  return rows.evaluateAll(
    (elements, index) =>
      elements.map((row) =>
        (row.children.item(index)?.textContent ?? "").replace(/\s+/g, " ").trim(),
      ),
    columnIndex,
  );
}

function parseScale(cellText: string): number {
  return Number(cellText.match(/[\d.]+/)?.[0] ?? Number.NaN);
}

async function platformCellLabels(rows: Locator, columnIndex: number): Promise<string[]> {
  return rows.evaluateAll(
    (elements, index) =>
      elements.map((row) => {
        const cell = row.children.item(index);
        return cell?.querySelector(".font-medium")?.textContent?.trim() ?? cell?.textContent?.trim() ?? "";
      }),
    columnIndex,
  );
}

test.describe("Index sortable headers", () => {
  test("benchmark section deep links survive loading and sibling navigation", async ({ page }) => {
    for (const target of ["?view=list&sf=0.01&phase=power", "?sf=0.01&phase=power#benchmark-section-list"]) {
      await page.goto(`/results/tpch/${target}`);
      await waitForDataLoaded(page, /TPC-H Results/);
      const list = page.locator("#benchmark-section-list");
      await waitForResultRows(page, list, 3);
      await expect.poll(async () => Math.abs(((await list.boundingBox())?.y ?? Infinity) - 96)).toBeLessThan(2);
    }

    await page.selectOption("#benchmark-switcher", "ssb");
    await expect(page).toHaveURL(/\/results\/ssb\/(?:\?phase=standard)?#benchmark-section-list$/);
    const list = page.locator("#benchmark-section-list");
    await expect(list.locator("tbody tr")).not.toHaveCount(0);
    await expect.poll(async () => Math.abs(((await list.boundingBox())?.y ?? Infinity) - 96)).toBeLessThan(2);
  });

  test("PlatformIndex headers update aria-sort and row order", async ({ page }) => {
    await page.goto("/results/p/duckdb/");
    await waitForShell(page);
    await waitForDataLoaded(page, /DuckDB Results/);

    const table = page.locator("table").first();
    const rows = table.locator("tbody tr[data-testid]");
    await expect.poll(() => rows.count()).toBeGreaterThanOrEqual(2);

    const initialOrder = await rowIds(rows);
    const scaleHeader = sortableHeader(table, /^Scale/);
    const scaleButton = scaleHeader.getByRole("button", { name: /Scale/ });

    await scaleButton.click();
    await expect(scaleHeader).toHaveAttribute("aria-sort", "ascending");
    await scaleButton.click();
    await expect(scaleHeader).toHaveAttribute("aria-sort", "descending");

    const scales = (await columnTexts(rows, 2)).map(parseScale);
    expect(scales).toEqual([...scales].sort((a, b) => b - a));
    expect(await rowIds(rows)).not.toEqual(initialOrder);
  });

  test("BenchmarkIndex matrix headers update aria-sort and row order", async ({ page }) => {
    await page.goto("/results/tpch/?sf=0.01&phase=power");
    await waitForShell(page);
    await waitForDataLoaded(page, /TPC-H Results/);

    await openAnalysisCard(page, "query_heatmap");
    const table = page.getByRole("table", { name: /tpch SF 0\.01 power results/i });
    await waitForResultRows(page, table, 3);
    const rows = table.locator("tbody tr[data-testid]");
    await expect.poll(() => rows.count()).toBeGreaterThanOrEqual(3);

    const platformHeader = sortableHeader(table, /^Platform/);
    await platformHeader.getByRole("button", { name: /Platform/ }).click();

    await expect(platformHeader).toHaveAttribute("aria-sort", "ascending");
    const platforms = (await platformCellLabels(rows, 0)).map((label) => label.split(" · ")[0] ?? label);
    expect(platforms).toEqual([...platforms].sort((a, b) => a.localeCompare(b)));
  });

  test("BenchmarkIndex list headers update aria-sort and row order", async ({ page }) => {
    await page.goto("/results/tpch/?sf=0.01&phase=power");
    await waitForShell(page);
    await waitForDataLoaded(page, /TPC-H Results/);

    const table = page.locator("#benchmark-section-list table").first();
    const rows = table.locator("tbody tr[data-testid]");
    await expect.poll(() => rows.count()).toBeGreaterThanOrEqual(3);

    const platformHeader = sortableHeader(table, /^Platform/);
    await platformHeader.getByRole("button", { name: /Platform/ }).click();

    await expect(platformHeader).toHaveAttribute("aria-sort", "ascending");
    const platforms = await platformCellLabels(rows, 1);
    const platformName = (label: string) => label.split(" · ", 1)[0] ?? label;
    expect(platforms).toEqual([...platforms].sort((a, b) => platformName(a).localeCompare(platformName(b))));
  });
});
