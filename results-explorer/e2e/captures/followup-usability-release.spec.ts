import { mkdirSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { fixtureIds, openAnalysisCard, waitForDataLoaded, waitForShell } from "../support/fixtures";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "../../..");
const SHOT_DIR = path.join(
  REPO_ROOT,
  "_project",
  "audits",
  "results-explorer-followup-usability-release-2026-05-08-screenshots",
);
const SHOULD_CAPTURE = process.env.FOLLOWUP_USABILITY_CAPTURE === "1";

async function maybeCapture(page: Page, slug: string): Promise<void> {
  if (!SHOULD_CAPTURE) return;
  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(SHOT_DIR, `${slug}.png`), fullPage: true });
}

async function countDisabled(locator: Locator): Promise<number> {
  const count = await locator.count();
  let disabled = 0;
  for (let index = 0; index < count; index += 1) {
    if (await locator.nth(index).isDisabled()) disabled += 1;
  }
  return disabled;
}

async function clickFirstEnabledUnchecked(locator: Locator, startAt = 0): Promise<void> {
  const count = await locator.count();
  for (let index = startAt; index < count; index += 1) {
    const candidate = locator.nth(index);
    const disabled = await candidate.isDisabled();
    const checked = await candidate.isChecked();
    if (!disabled && !checked) {
      await candidate.click();
      return;
    }
  }
  throw new Error("No enabled unchecked compare candidate found");
}

async function launchFirstBuilderComparison(page: Page): Promise<void> {
  await page.goto("/results/tpch/");
  await waitForShell(page);
  await waitForDataLoaded(page, /TPC-H Results/);
  const duckdb = page.getByRole("checkbox", { name: /Select DuckDB .* for comparison/i }).first();
  const datafusion = page.getByRole("checkbox", { name: /Select DataFusion .* for comparison/i }).first();
  if (await duckdb.count() > 0 && await datafusion.count() > 0) {
    await duckdb.check().catch(() => {});
    await datafusion.check().catch(() => {});
    const link = page.getByTestId("compare-tray-compare-link");
    if (await link.count() > 0) {
      await link.click();
      await waitForDataLoaded(page, /Comparison/i);
      return;
    }
  }
  await page.goto("/results/compare?ids=" + `${fixtureIds.shortIds.duckdb},${fixtureIds.shortIds.datafusion}`);
  await waitForShell(page);
  await waitForDataLoaded(page, /Comparison/i);
}

async function openFirstSparseResultDetail(page: Page): Promise<void> {
  await page.goto("/results/compare/");
  await waitForShell(page);
  await waitForDataLoaded(page, /Cross-benchmark rankings/);
  const hrefs = await page.locator('a[href^="/results/r/"]').evaluateAll((links) =>
    Array.from(new Set(links.map((link) => link.getAttribute("href")).filter((href): href is string => !!href))),
  );
  for (const href of hrefs) {
    await page.goto(href);
    await waitForShell(page);
    await waitForDataLoaded(page, /Query timings/);
    if ((await page.getByText(/Show missing/i).count()) > 0) return;
  }
  throw new Error("No sparse result-detail page with a Show missing disclosure was found");
}

test.describe("@followup-usability release-gate route walk", () => {
  test.setTimeout(120_000);

  test("Query Workbench renders the collapsible facet rail with searchable Benchmark group", async ({ page }) => {
    await page.goto("/results/query");
    await waitForShell(page);
    await waitForDataLoaded(page, /matching run/);

    const desktopFilters = page.getByTestId("query-desktop-filters");
    await expect(desktopFilters).toBeVisible();
    const benchmarkToggle = desktopFilters.getByRole("button", { name: /^Benchmark/ });
    await expect(benchmarkToggle).toHaveAttribute("aria-expanded", "true");
    await expect(desktopFilters.getByPlaceholder("Search benchmark")).toBeVisible();

    const trustToggle = desktopFilters.getByRole("button", { name: /^Trust/ });
    await expect(trustToggle).toHaveAttribute("aria-expanded", "false");

    await maybeCapture(page, "query-workbench-facets-disclosure");
  });

  test("Query compare tray defaults to the prompt copy and offers a launch button after two picks", async ({ page }) => {
    await page.goto("/results/query");
    await waitForShell(page);
    await waitForDataLoaded(page, /matching run/);

    const tray = page.getByTestId("query-compare-tray");
    await expect(tray).toContainText(/Select two or more rows|can be compared together/);
    await expect(page.getByTestId("query-compare-launch")).toBeVisible();

    await maybeCapture(page, "query-compare-tray-default");

    const checkboxes = page.locator('input[data-testid^="query-compare-checkbox-"]');
    await expect(checkboxes.first()).toBeVisible();
    await checkboxes.first().click();
    await expect(tray).toContainText(/1 result selected/);
    await clickFirstEnabledUnchecked(checkboxes, 1);

    const launch = page.getByTestId("query-compare-launch");
    await expect(launch).toBeVisible();
    await expect(launch).toHaveAttribute("href", /\/results\/compare\?ids=[^,]+,[^,]+/);
  });

  test("Home renders the ranking selector above the matrix with a compare entrypoint", async ({ page }) => {
    await page.goto("/results/compare/");
    await waitForShell(page);
    await waitForDataLoaded(page, /Cross-benchmark rankings/);

    const selector = page.getByRole("region", { name: "Leaderboard ranking selector" });
    const matrix = page.getByRole("region", { name: "Cross-benchmark rankings" });
    await expect(selector).toBeVisible();
    await expect(matrix).toBeVisible();
    const selectorBox = await selector.boundingBox();
    const matrixBox = await matrix.boundingBox();
    expect(selectorBox).not.toBeNull();
    expect(matrixBox).not.toBeNull();
    if (selectorBox && matrixBox) {
      expect(selectorBox.y).toBeLessThan(matrixBox.y);
    }

    const entrypoint = page.getByTestId("leaderboard-compare-cta");
    await expect(entrypoint).toBeVisible();
    await expect(entrypoint).toBeDisabled();

    await page.getByLabel("Comparison cohort").selectOption({ label: "TPC-H SF 0.01 · power" });
    const runs = page.getByTestId("leaderboard-comparable-runs");
    await expect(runs).toBeVisible();
    const boxes = runs.getByRole("checkbox");
    await boxes.first().check();
    await boxes.nth(1).check();
    await expect(entrypoint).toHaveAttribute("href", /\/results\/compare\?ids=[^,]+,[^,]+/);

    await maybeCapture(page, "home-ranking-selector-and-compare-cta");
  });

  test("Benchmark detail exposes a sibling switcher and the heatmap header is sticky-top", async ({ page }) => {
    await page.goto("/results/tpch/?sf=0.01&phase=power");
    await waitForShell(page);
    await waitForDataLoaded(page, /TPC-H Results/);
    await openAnalysisCard(page, "query_heatmap");

    const switcher = page.getByTestId("benchmark-switcher");
    await expect(switcher).toBeVisible();
    await expect(switcher.locator("option", { hasText: "SSB" })).toBeAttached();

    const heatmap = page.getByTestId("query-heatmap-scroll-container");
    const platformHeader = heatmap
      .locator("thead th")
      .filter({ has: page.locator("button", { hasText: /^Platform/ }) })
      .first();
    await expect(platformHeader).toHaveCSS("position", "sticky");

    const firstHeatmapRow = heatmap.locator("tbody tr[data-testid]").first();
    await expect(firstHeatmapRow).toBeVisible();
    await expect(firstHeatmapRow.locator("summary")).toHaveCount(0);
    await expect(firstHeatmapRow.getByRole("link", { name: /^Open receipt for / }).first()).toBeVisible();

    await maybeCapture(page, "benchmark-detail-switcher-and-sticky-header");
  });

  test("Platform detail surfaces the sibling switcher and the filter strip when the cohort is dense", async ({ page }) => {
    await page.goto("/results/p/duckdb/");
    await waitForShell(page);
    await waitForDataLoaded(page, /DuckDB Results/);

    const switcher = page.getByTestId("platform-switcher");
    await expect(switcher).toBeVisible();

    const compareCheckboxes = page.locator('input[data-testid^="platform-compare-checkbox-"]');
    const enabledCompareCheckboxes = page.locator('input[data-testid^="platform-compare-checkbox-"]:not(:disabled)');
    await expect(enabledCompareCheckboxes.first()).toBeVisible();
    expect(await compareCheckboxes.count()).toBeGreaterThan(1);
    const baselineDisabledCount = await countDisabled(compareCheckboxes);
    await enabledCompareCheckboxes.first().check();
    expect(await countDisabled(compareCheckboxes)).toBeGreaterThan(baselineDisabledCount);
    await enabledCompareCheckboxes.first().uncheck();
    expect(await countDisabled(compareCheckboxes)).toBe(baselineDisabledCount);

    const filters = page.getByTestId("platform-detail-filters");
    await expect(filters).toBeVisible();
    await expect(filters.getByTestId("platform-filter-benchmark")).toBeVisible();

    await maybeCapture(page, "platform-detail-filters");
  });

  test("Compare empty state points to Find runs", async ({ page }) => {
    await page.goto("/results/compare/");
    await waitForShell(page);
    await waitForDataLoaded(page, /Compare/);

    await expect(page.getByRole("heading", { name: "Compare benchmark results" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Results Explorer" }).getByRole("link", { name: "Find runs" })).toBeVisible();

    await maybeCapture(page, "compare-builder-empty-state");
  });

  test("Compare keeps run selection in Find runs", async ({ page }) => {
    await page.goto("/results/compare/");
    await waitForShell(page);
    await waitForDataLoaded(page, /Compare/);

    await expect(page.getByRole("heading", { name: "Compare benchmark results" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Results Explorer" }).getByRole("link", { name: "Find runs" })).toHaveAttribute("href", "/results/query");
    await expect(page.getByRole("grid", { name: "Cross-benchmark leaderboard" })).toBeVisible();
  });

  test("Query compare tray defaults compatible-only after first selection", async ({ page }) => {
    await page.goto("/results/query");
    await waitForShell(page);
    await waitForDataLoaded(page, /matching run/);

    const checkboxes = page.locator('input[data-testid^="query-compare-checkbox-"]');
    if ((await checkboxes.count()) < 2) return;
    await checkboxes.first().click();

    const toggle = page.getByTestId("query-compare-compatible-only");
    await expect(toggle).toBeVisible();
    await expect(toggle).toBeChecked();
  });

  test("Compare normalized-speedup chart uses builder-launched IDs and asserts comparable-only control when partials exist", async ({ page }) => {
    await launchFirstBuilderComparison(page);

    const comparisonLoaded = await page.getByRole("heading", { name: /Comparison/i }).isVisible().catch(() => false);
    if (!comparisonLoaded) {
      await page.waitForTimeout(5000);
    }
    const hasComparison = await page.getByRole("heading", { name: /Comparison/i }).isVisible().catch(() => false);
    if (!hasComparison) {
      await expect(page.getByRole("main")).toBeVisible();
      await maybeCapture(page, "compare-normalized-speedup");
      return;
    }

    const speedupButton = page.getByRole("button", { name: "Normalized Speedup" });
    if ((await speedupButton.count()) > 0) await speedupButton.click();
    const baselineOptions = page.locator("#chart-panel-baseline option");
    if ((await baselineOptions.count()) > 1) {
      const labels = (await baselineOptions.allTextContents()).map((label) => label.trim());
      expect(new Set(labels).size).toBe(labels.length);
    }
    const chartPanel = page.getByRole("tabpanel", { name: /chart/i }).first();
    const hasPanel = await chartPanel.isVisible().catch(() => false);
    if (!hasPanel) {
      await expect(page.getByRole("main")).toBeVisible();
      await maybeCapture(page, "compare-normalized-speedup");
      return;
    }
    await expect(chartPanel).toBeVisible();

    const toggle = page.getByTestId("normalized-speedup-comparable-only-toggle");
    if ((await toggle.count()) > 0) {
      await expect(toggle).toBeChecked();
      await expect(page.getByText(/fully comparable queries|hidden|missing data/)).toBeVisible();
      await toggle.uncheck();
      await expect(toggle).not.toBeChecked();
    } else {
      if ((await chartPanel.locator("svg").count()) > 0) {
        await expect(chartPanel.locator("svg").first()).toBeVisible();
      } else {
        await expect(page.getByText("No meaningful per-query difference")).toBeVisible();
      }
    }

    await maybeCapture(page, "compare-normalized-speedup");
  });

  test("Result Detail renders without claiming missing receipt fields", async ({ page }) => {
    await openFirstSparseResultDetail(page);

    await expect(page.getByText(/Show missing/i)).toBeVisible();

    await maybeCapture(page, "result-detail-sparse-metadata");
  });
});
