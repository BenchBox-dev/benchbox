import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { waitForShell } from "../support/fixtures";

const SOURCE_BUNDLES_DIR = fileURLToPath(new URL("../../test-fixtures/source/bundles/", import.meta.url));
const localBundleName = readdirSync(SOURCE_BUNDLES_DIR).find((name) => name.startsWith("tpch-duckdb-sf0.01-"));
if (!localBundleName) throw new Error("TPC-H DuckDB source bundle not found");
const LOCAL_BUNDLE = join(SOURCE_BUNDLES_DIR, localBundleName);
const localBundle = JSON.parse(readFileSync(LOCAL_BUNDLE, "utf8")) as { run: { id: string } };

test.describe("local result preview", () => {
  test("opens a local bundle in the existing detail UI without uploading it", async ({ page }) => {
    const requests: Array<{ method: string; url: string; body: string | null }> = [];
    page.on("request", (request) => {
      requests.push({ method: request.method(), url: request.url(), body: request.postData() });
    });

    await page.goto("/results/");
    await waitForShell(page);
    await page.getByTestId("local-result-file-input").setInputFiles(LOCAL_BUNDLE);

    await expect(page).toHaveURL(/\/results\/local\/local-[0-9a-f]{12}$/);
    const main = page.getByRole("main");
    await expect(main.getByTestId("local-result-banner")).toContainText("has not been uploaded");
    await expect(main.getByRole("heading", { name: /TPC-H result: DuckDB/ })).toBeVisible();
    await expect(main.locator('[data-role="trust"]')).toContainText("Local");
    await expect(main.getByText(/Local preview ID/)).toBeVisible();
    await expect(main.getByRole("link", { name: "Submit for public review" })).toHaveAttribute(
      "href",
      "/docs/contributing-results.html",
    );
    await expect(main.getByRole("link", { name: "Compare this result" })).toHaveCount(0);
    await expect(main.getByRole("link", { name: "Download bundle" })).toHaveCount(0);

    expect(requests.filter((request) => ["POST", "PUT", "PATCH"].includes(request.method))).toEqual([]);
    expect(requests.some((request) => request.body?.includes(localBundle.run.id) ?? false)).toBe(false);
    expect(requests.some((request) => request.body?.includes("database_path") ?? false)).toBe(false);
    expect(requests.some((request) => request.url.includes(localBundle.run.id))).toBe(false);
    expect(requests.some((request) => request.url.includes(localBundleName))).toBe(false);

    const persistentState = await page.evaluate(async () => ({
      localStorage: Object.entries(localStorage),
      sessionStorage: Object.entries(sessionStorage),
      indexedDatabases: typeof indexedDB.databases === "function"
        ? (await indexedDB.databases()).map((database) => database.name)
        : [],
    }));
    expect(persistentState).toEqual({ localStorage: [], sessionStorage: [], indexedDatabases: [] });
  });

  test("explains that memory-only state is gone after a reload", async ({ page }) => {
    await page.goto("/results/");
    await waitForShell(page);
    await page.getByTestId("local-result-file-input").setInputFiles(LOCAL_BUNDLE);
    await expect(page).toHaveURL(/\/results\/local\/local-[0-9a-f]{12}$/);
    await expect(page.getByTestId("local-result-banner")).toBeVisible();

    await page.reload();
    await expect(page.getByRole("alert")).toContainText("no longer available");
    await expect(page.getByRole("button", { name: "Open result file again" })).toBeVisible();

    await page.getByRole("main").getByTestId("local-result-file-input").setInputFiles(LOCAL_BUNDLE);
    await expect(page.getByTestId("local-result-banner")).toBeVisible();
  });

  test("offers the file chooser on the bare local route", async ({ page }) => {
    await page.goto("/results/local/");
    await expect(page.getByRole("heading", { name: "Open a local result" })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Choose result file" })).toBeFocused();
    await expect(page.getByRole("heading", { name: /local results/i })).toHaveCount(0);
  });
});
