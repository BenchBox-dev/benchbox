import { expect, test } from "@playwright/test";
import { fixtureIds, waitForDataLoaded, waitForShell } from "../support/fixtures";

const TPCH_ID = fixtureIds.ids.duckdb;
const TPCH_SHORT = fixtureIds.shortIds.duckdb;
const STAR_SCHEMA_SHORT = fixtureIds.shortIds.starSchema;
const TPCH_SF01_SHORT = fixtureIds.shortIds.duckdbSf01;

test.describe.configure({ mode: "serial" });

test.describe("Compare guardrails", () => {
  test("mixing benchmarks renders guardrails without suppressing raw evidence", async ({
    page,
  }) => {
    await page.goto(`/results/compare?ids=${TPCH_SHORT},${STAR_SCHEMA_SHORT}`);
    await waitForShell(page);
    await waitForDataLoaded(page, /Mixed Benchmark Comparison/);

    const main = page.getByRole("main");
    const receipt = main.getByRole("region", { name: "Comparison checks" });
    const summary = main.getByRole("region", { name: "Comparison summary" });
    await expect(receipt).toContainText("Benchmark");
    await expect(summary).toContainText("Not directly comparable: runs differ in benchmark");
    await expect(summary).toContainText("No winner named");
    await expect(main.getByRole("heading", { name: "Query-level differences" })).toBeVisible();
    await expect(main.getByRole("heading", { name: /Cannot compare/i })).toHaveCount(0);
  });

  test("mixing scale factors renders guardrails without suppressing raw evidence", async ({
    page,
  }) => {
    await page.goto(`/results/compare?ids=${TPCH_SHORT},${TPCH_SF01_SHORT}`);
    await waitForShell(page);
    await waitForDataLoaded(page, /TPC-H Comparison/);

    const main = page.getByRole("main");
    const receipt = main.getByRole("region", { name: "Comparison checks" });
    const summary = main.getByRole("region", { name: "Comparison summary" });
    await expect(receipt).toContainText("Scale factor");
    await expect(summary).toContainText("Not directly comparable: runs differ in scale factor");
    await expect(summary).toContainText("No winner named");
    await expect(main.getByRole("heading", { name: "Query-level differences" })).toBeVisible();
    await expect(main.getByRole("heading", { name: /Cannot compare/i })).toHaveCount(0);
  });

  test("a Compare URL that references one stale ID retains the resolvable result", async ({
    page,
  }) => {
    await page.goto(`/results/compare?ids=${TPCH_ID},tpch-unknown-does-not-exist`);
    await waitForShell(page);

    await waitForDataLoaded(page, /Compare benchmark results/);
    await expect(page.getByTestId("compare-url-notice")).toContainText("Ignored unavailable result ID");
    await expect(page.getByRole("link", { name: "Find runs to compare with this run" })).toHaveAttribute("href", /\/results\/query\?pick=/);
    await expect(page.getByRole("heading", { name: /Cannot compare/i })).toHaveCount(0);

    expect(page.url()).toContain("tpch-unknown-does-not-exist");
    await page.reload();
    await waitForDataLoaded(page, /Compare benchmark results/);
    await expect(page.getByRole("link", { name: "Find runs to compare with this run" })).toBeVisible();
  });

  test("all-unavailable compare IDs retain the removal-hint error", async ({ page }) => {
    await page.goto("/results/compare?ids=tpch-unknown-one,tpch-unknown-two");
    await waitForShell(page);
    await expect(page.getByText(/No result found for/i)).toBeVisible({ timeout: 20_000 });
  });

  test("duplicate and stale IDs are disclosed while valid entries are retained", async ({ page }) => {
    const ids = [TPCH_SHORT, TPCH_SHORT, STAR_SCHEMA_SHORT, TPCH_SF01_SHORT, "stale-one", "stale-two"].join(",");
    await page.goto(`/results/compare?ids=${ids}`);
    await waitForShell(page);
    await waitForDataLoaded(page, /Comparison/);

    const notice = page.getByTestId("compare-url-notice");
    await expect(notice).toContainText("Ignored duplicate result ID");
    await expect(notice).toContainText("Ignored unavailable result ID");
    await expect(notice).not.toContainText("comparisons are limited to");
    await expect(page.getByRole("heading", { name: "Comparison summary" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Cannot compare/i })).toHaveCount(0);
  });

  test("excess IDs beyond ten are disclosed while the first ten unique entries are retained", async ({ page }) => {
    const extra = [
      fixtureIds.shortIds.datafusion,
      fixtureIds.shortIds.polars,
      fixtureIds.shortIds.spark,
      fixtureIds.shortIds.pandas,
      fixtureIds.shortIds.cedardb,
      fixtureIds.shortIds.pandasTuned,
      fixtureIds.shortIds.polarsTuned,
      fixtureIds.shortIds.awsCloud,
    ];
    const ids = [TPCH_SHORT, TPCH_SHORT, STAR_SCHEMA_SHORT, TPCH_SF01_SHORT, ...extra, "stale-one", "stale-two"].join(",");
    await page.goto(`/results/compare?ids=${ids}`);
    await waitForShell(page);
    await waitForDataLoaded(page, /Comparison/);

    const notice = page.getByTestId("compare-url-notice");
    await expect(notice).toContainText("Ignored duplicate result ID");
    await expect(notice).toContainText("comparisons are limited to 10 unique results");
    await expect(page.getByRole("heading", { name: "Comparison summary" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Cannot compare/i })).toHaveCount(0);
  });

  test("missing comparison IDs settle within the first data wait after delayed worker startup", async ({ page }) => {
    let delayedWorker = false;
    await page.route("**/*duckdb*worker*.js", async (route) => {
      if (!delayedWorker) {
        delayedWorker = true;
        await new Promise((resolve) => setTimeout(resolve, 4500));
      }
      await route.continue();
    });
    const ids = [TPCH_SHORT, STAR_SCHEMA_SHORT, TPCH_SF01_SHORT, "stale-one"].join(",");
    await page.goto(`/results/compare?ids=${ids}`);
    await waitForShell(page);
    await expect(page.getByRole("heading", { name: "Comparison summary" })).toBeVisible();
    await expect(page.getByTestId("compare-url-notice")).toContainText("Ignored unavailable result ID");
    expect(delayedWorker).toBe(true);
  });
});
