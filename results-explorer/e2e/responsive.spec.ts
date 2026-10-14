import { expect, test, type Locator, type Page } from "@playwright/test";
import { fixtureIds, openAnalysisCard, waitForDataElement, waitForDataLoaded, waitForShell } from "./support/fixtures";

const SHORT_DUCKDB = fixtureIds.shortIds.duckdb;
const SHORT_DATAFUSION = fixtureIds.shortIds.datafusion;
const DETAIL_ID = fixtureIds.ids.duckdb;

const VIEWPORTS = [
  { name: "mobile", width: 390, height: 900, maxY: 1220 },
  { name: "tablet", width: 768, height: 900, maxY: 1200 },
  { name: "desktop", width: 1280, height: 900, maxY: 900 },
  { name: "wide", width: 1600, height: 900, maxY: 900 },
] as const;
const MIN_HOME_ROWS_ABOVE_FOLD_DESKTOP = 2;
const MIN_HOME_CHARTS = 5;

const AUDITED_ROUTES = [
  { path: "/results/", ready: /Recent Results/i },
  { path: "/results/tpch/", ready: /TPC-H Results/i },
  { path: "/results/p/duckdb/", ready: /DuckDB/i },
  { path: `/results/r/${DETAIL_ID}`, ready: /Query timings/i },
  { path: `/results/compare?ids=${SHORT_DUCKDB},${SHORT_DATAFUSION}`, ready: /TPC-H Comparison/i },
  { path: `/results/compare/query/1?ids=${SHORT_DUCKDB},${SHORT_DATAFUSION}`, ready: /Recorded execution times/i },
  { path: "/results/query", ready: /matching run/ },
] as const;

test.describe.configure({ mode: "parallel" });

test.describe("responsive explorer assertions", () => {
  for (const viewport of VIEWPORTS) {
    test(`secondary nav exposes every Explorer route at ${viewport.name}`, async ({ page }) => {
      await setViewport(page, viewport);
      await page.goto("/results/query");
      await waitForShell(page);

      const nav = page.getByTestId("results-explorer-nav");
      for (const label of ["Overview", "Benchmarks", "Platforms", "Compare", "Find runs"]) {
        await expect(nav.getByRole("link", { name: label })).toBeVisible();
      }
      await expect(nav.getByRole("link", { name: "Find runs" })).toHaveAttribute("aria-current", "page");
    });

    test(`home keeps headline, cohort summary, and leaderboard rows high in the viewport at ${viewport.name}`, async ({
      page,
    }) => {
      await setViewport(page, viewport);
      await page.goto("/results/compare/");
      await waitForDataLoaded(page, /Compare benchmark results/i);

      await expectTopWithin(
        page.getByRole("heading", { name: "Compare benchmark results" }),
        viewport.maxY,
        "home headline",
      );
      await expectTopWithin(
        page.getByRole("region", { name: "Leaderboard ranking selector" }),
        viewport.maxY,
        "active leaderboard summary",
      );

      const leaderboard = page.getByRole("grid", { name: "Cross-benchmark leaderboard" });
      const firstRow = leaderboard.locator("tbody tr").first();
      await expect(firstRow).toBeVisible({ timeout: 20_000 });
      await expectTopWithin(firstRow, viewport.maxY, "first leaderboard row");

      if (viewport.width >= 1280) {
        const rowCount = await leaderboard.locator("tbody tr").count();
        const aboveFold = await rowsAboveFold(leaderboard.locator("tbody tr"), viewport.height);
        expect(aboveFold).toBeGreaterThanOrEqual(Math.min(MIN_HOME_ROWS_ABOVE_FOLD_DESKTOP, rowCount));
      }
    });

    test(`benchmark heatmap exposes overflow affordance when needed at ${viewport.name}`, async ({ page }) => {
      await setViewport(page, viewport);
      await page.goto("/results/star_schema/?phase=standard");
      await waitForShell(page);
      await expect(page.getByRole("heading", { name: /SSB Results/i })).toBeVisible({
        timeout: 20_000,
      });

      await openAnalysisCard(page, "query_heatmap");
      const heatmap = page.getByTestId("query-heatmap-scroll-container").first();
      await expect(heatmap).toBeAttached();
      const hasHorizontalOverflow = await heatmap.evaluate((element) => element.scrollWidth > element.clientWidth);
      if (hasHorizontalOverflow) {
        await expect(page.getByTestId("query-heatmap-scroll-hint")).toBeVisible();
      }
    });

    test(`query workbench renders summary, active filters, and rows before deep controls at ${viewport.name}`, async ({
      page,
    }) => {
      await setViewport(page, viewport);
      await page.goto("/results/query");
      await waitForDataLoaded(page, /matching run/);

      await expectTopWithin(
        page.getByRole("heading", { name: "Find benchmark runs" }),
        viewport.maxY,
        "query headline",
      );
      await expectTopWithin(page.getByTestId("query-result-summary"), viewport.maxY, "query result summary");
      await expectTopWithin(page.getByTestId("query-results-panel"), viewport.maxY, "query results panel");

      const drawerTrigger = page.locator('[data-testid="query-mobile-filter-drawer"] button[data-result-count]').first();
      await expect(drawerTrigger).toHaveAttribute("data-result-count", /\d+/);

      const resultPanelY = await topOf(page.getByTestId("query-results-panel"));
      const visibleColumnsY = await topOf(page.getByTestId("query-visible-columns"));
      expect(resultPanelY).toBeLessThan(visibleColumnsY);
    });

    test(`compare route keeps decision summary and query evidence reachable at ${viewport.name}`, async ({ page }) => {
      await setViewport(page, viewport);
      await page.goto(`/results/compare?ids=${SHORT_DUCKDB},${SHORT_DATAFUSION}`);
      await waitForDataLoaded(page, /TPC-H Comparison/);

      await expectTopWithin(page.getByRole("heading", { name: /TPC-H Comparison/ }), viewport.maxY, "compare headline");
      const decisionSummary = page.getByRole("region", { name: "Comparison summary" });
      const queryEvidence = page.getByRole("heading", { name: "Query-level differences" });
      await expect(decisionSummary).toBeVisible();
      await expect(queryEvidence).toBeVisible();
      expect(await topOf(decisionSummary)).toBeLessThan(await topOf(queryEvidence));
    });
  }

  test("benchmark heatmap keeps its header visible during document scroll", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 600 });
    await page.goto("/results/tpch/?sf=0.01&phase=power");
    await waitForDataLoaded(page, /TPC-H Results/i);

    await openAnalysisCard(page, "query_heatmap");
    const heatmap = page.getByTestId("query-heatmap-scroll-container").first();
    await waitForDataElement(page, heatmap);
    await heatmap.evaluate((container) => {
      const tbody = container.querySelector("tbody");
      if (!tbody) throw new Error("missing query heatmap body");
      const rows = Array.from(tbody.querySelectorAll("tr"));
      for (let repeat = 0; repeat < 18; repeat += 1) {
        for (const row of rows) {
          tbody.appendChild(row.cloneNode(true));
        }
      }
    });

    const pageStickyHeader = page.getByTestId("query-heatmap-page-sticky-header").first();
    await expect(pageStickyHeader).toBeVisible();

    await heatmap.evaluate((container) => {
      container.scrollLeft = 160;
      container.dispatchEvent(new Event("scroll", { bubbles: true }));
    });
    await expect.poll(() => pageStickyHeader.evaluate((header) => header.scrollLeft)).toBe(160);

    const scrollY = await heatmap.evaluate((container) => {
      const rect = container.getBoundingClientRect();
      return window.scrollY + rect.top + 180;
    });
    await page.evaluate((y) => window.scrollTo(0, y), scrollY);

    await expect.poll(async () => (await pageStickyHeader.boundingBox())?.y ?? 999).toBeLessThanOrEqual(1);
    await expect.poll(async () => (await heatmap.locator("thead tr").first().boundingBox())?.y ?? 999).toBeLessThan(0);
    await expect(pageStickyHeader.getByText("Platform").first()).toBeVisible();
    await expect(pageStickyHeader.locator("th", { hasText: /^3\b/ }).first()).toBeVisible();
  });

  test("scroll affordance follows measured overflow at 1440px", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    await page.goto("/results/tpch/?sf=0.01&phase=power");
    await waitForDataLoaded(page, /TPC-H Results/i);
    await openAnalysisCard(page, "query_heatmap");
    const heatmap = page.getByTestId("query-heatmap-scroll-container").first();
    await waitForDataElement(page, heatmap);
    await expect.poll(() => heatmap.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
    await expect(page.getByTestId("query-heatmap-scroll-hint")).toBeVisible();

    await page.goto("/results/query");
    await waitForDataLoaded(page, /matching run/);
    const queryResults = page.getByTestId("query-results-scroll-container");
    await queryResults.locator("table").evaluate((table) => {
      table.style.width = "1800px";
    });
    await expect.poll(() => queryResults.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
    await expect(page.getByTestId("query-results-scroll-hint")).toBeVisible();

    await page.goto(`/results/r/${DETAIL_ID}`);
    await waitForDataLoaded(page, /Query timings/i);
    await expect(page.getByTestId("detail-timings-scroll-container")).toHaveCount(0);
    const passes = page.getByTestId("detail-passes-scroll-container");
    await expect.poll(() => passes.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(false);
    await expect(page.getByTestId("detail-passes-scroll-hint")).toHaveCount(0);
  });

  for (const viewport of VIEWPORTS.filter((item) => item.width <= 768)) {
    test(`audited routes avoid document overflow at ${viewport.name}`, async ({ page }) => {
      await setViewport(page, viewport);
      for (const route of AUDITED_ROUTES) {
        await page.goto(route.path);
        await waitForDataLoaded(page, route.ready);
        await expectNoDocumentOverflow(page, route.path);
      }
    });

    test(`mobile table affordances match measured overflow at ${viewport.name}`, async ({ page }) => {
      await setViewport(page, viewport);

      await page.goto("/results/");
      await waitForDataLoaded(page, /Recent Results/i);
      await expectScrollAffordance(page, "recent-results-scroll-container", "recent-results-scroll-hint");

      await page.goto(`/results/r/${DETAIL_ID}`);
      await waitForDataLoaded(page, /Query timings/i);
      await expectScrollAffordance(page, "detail-passes-scroll-container", "detail-passes-scroll-hint");

      await page.goto(`/results/compare?ids=${SHORT_DUCKDB},${SHORT_DATAFUSION}`);
      await waitForDataLoaded(page, /TPC-H Comparison/i);
      await expectScrollAffordance(page, "query-diff-scroll-container", "query-diff-scroll-hint");

      await page.goto("/results/query");
      await waitForDataLoaded(page, /matching run/);
      await expectScrollAffordance(page, "query-results-scroll-container", "query-results-scroll-hint");
    });
  }

  for (const viewport of VIEWPORTS.filter((item) => item.width <= 768)) {
    test(`chart drawings fit their own box at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/results/tpch/");
      await waitForDataLoaded(page, /More views/);

      const previews = page.getByTestId("summary-more-views").locator(":scope > div.grid > details");
      const previewCount = await previews.count();
      for (let p = 0; p < previewCount; p += 1) {
        const details = previews.nth(p);
        const isOpen = await details.evaluate((node) => (node as HTMLDetailsElement).open);
        if (!isOpen) {
          await details.locator("summary").first().click();
          await page.waitForTimeout(150);
        }
      }

      {
          const offenders = await page.evaluate(() => {
            const problems: string[] = [];
            for (const svg of Array.from(
              document.querySelectorAll("[data-chart-container] svg[role='img'], [data-chart-container] svg[role='group']"),
            )) {
              const box = svg.getBoundingClientRect();
              if (box.width === 0) continue;

              const viewBox = svg.getAttribute("viewBox");
              if (viewBox === null) {
                problems.push(`${svg.getAttribute("aria-label") ?? "chart"}: no viewBox`);
                continue;
              }

              const drawWidth = Number(viewBox.split(/\s+/)[2]);
              if (drawWidth < box.width - 1.5) {
                problems.push(
                  `${svg.getAttribute("aria-label") ?? "chart"}: draws ${drawWidth} into a ${box.width.toFixed(0)}px box`,
                );
              }

              for (const mark of Array.from(svg.querySelectorAll("text, rect, circle, path"))) {
                const markBox = mark.getBoundingClientRect();
                if (markBox.width === 0 && markBox.height === 0) continue;
                if (
                  markBox.right > box.right + 1 ||
                  markBox.left < box.left - 1 ||
                  markBox.bottom > box.bottom + 1
                ) {
                  const what =
                    mark.tagName === "text"
                      ? `"${(mark.textContent ?? "").slice(0, 24)}"`
                      : mark.tagName;
                  problems.push(
                    `${svg.getAttribute("aria-label") ?? "chart"}: ${what} falls outside the drawing`,
                  );
                }
              }
            }
            return problems;
          });

          expect(offenders, offenders.join("\n")).toEqual([]);
      }

      const chartsVisited = await page.locator("[data-chart-container] svg[role='img']").count();
      expect(chartsVisited, "charts measured").toBeGreaterThanOrEqual(MIN_HOME_CHARTS);
    });
  }

  for (const homeRoute of [
    { name: "default", path: "/results/compare/" },
    {
      name: "filtered deep link",
      path: "/results/compare/?bm=clickbench&scale_factor=0.1&trust_tier=maintainer-run",
    },
  ]) {
    test(`home skeleton and loaded shell keep the same rendered mobile geometry for the ${homeRoute.name} route`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: 390, height: 844 });

      let releaseSnapshot!: () => void;
      const snapshotGate = new Promise<void>((resolve) => {
        releaseSnapshot = resolve;
      });
      await page.route("**/results/data/results.duckdb", async (route) => {
        await snapshotGate;
        await route.continue();
      });

      await page.goto(homeRoute.path);
      await waitForShell(page);
      await expect(page.getByRole("region", { name: "Cross-benchmark leaderboard loading" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Active leaderboard filters" })).toHaveCount(0);
      const skeletonGeometry = await homeSharedGeometry(page);

      releaseSnapshot();
      await expect(page.getByRole("region", { name: "Cross-benchmark leaderboard loading" })).toHaveCount(0, { timeout: 30_000 });
      await expect(page.getByRole("region", { name: "Leaderboard ranking selector" })).toBeVisible();
      const loadedGeometry = await homeSharedGeometry(page);

      expect(loadedGeometry).toEqual(skeletonGeometry);
    });
  }
});

async function setViewport(
  page: Page,
  viewport: (typeof VIEWPORTS)[number],
) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
}

async function homeSharedGeometry(page: Page) {
  return page.evaluate(() => {
    const element = (selector: string) => {
      const match = document.querySelector<HTMLElement>(selector);
      if (!match) throw new Error(`missing shared Home geometry element: ${selector}`);
      return match;
    };
    const rounded = (value: number) => Math.round(value * 100) / 100;
    const box = (target: HTMLElement) => {
      const rect = target.getBoundingClientRect();
      return {
        x: rounded(rect.x),
        y: rounded(rect.y),
        width: rounded(rect.width),
        height: rounded(rect.height),
      };
    };
    const anchoredBox = (target: HTMLElement) => {
      const rect = target.getBoundingClientRect();
      return { x: rounded(rect.x), y: rounded(rect.y), width: rounded(rect.width) };
    };
    const styles = (target: HTMLElement, properties: string[]) => {
      const computed = getComputedStyle(target);
      return Object.fromEntries(properties.map((property) => [property, computed.getPropertyValue(property)]));
    };

    const wrapper = element('[data-testid="home-hero-wrapper"]');
    const intro = element('[data-testid="home-hero-intro"]');
    const headline = element('[data-testid="home-hero-intro"] h1');
    const subtitle = element('[data-testid="home-hero-intro"] p');
    const selector = element('[aria-label="Leaderboard ranking selector"]');
    const grid = element('[data-testid="home-ranking-selector-grid"]');
    const dataSurface = element('[data-testid="home-data-surface"]');

    return {
      wrapper: {
        box: box(wrapper),
        spacing: styles(wrapper, ["padding-top", "padding-right", "padding-bottom", "padding-left"]),
      },
      intro: box(intro),
      headline: {
        box: box(headline),
        type: styles(headline, ["font-size", "line-height"]),
      },
      subtitle: {
        box: box(subtitle),
        typeAndSpacing: styles(subtitle, ["margin-top", "font-size", "line-height"]),
      },
      selector: {
        box: box(selector),
        spacing: styles(selector, [
          "margin-top",
          "padding-top",
          "padding-right",
          "padding-bottom",
          "padding-left",
        ]),
      },
      grid: {
        box: box(grid),
        layout: styles(grid, ["grid-template-columns", "column-gap", "row-gap"]),
      },
      dataSurface: {
        box: anchoredBox(dataSurface),
        spacing: styles(dataSurface, ["padding-top", "padding-right", "padding-bottom", "padding-left"]),
      },
    };
  });
}

async function expectScrollAffordance(page: Page, containerTestId: string, hintTestId: string) {
  const container = page.getByTestId(containerTestId);
  await expect(container).toBeAttached();
  const hasHorizontalOverflow = await container.evaluate((element) => element.scrollWidth > element.clientWidth);
  if (hasHorizontalOverflow) {
    await expect(page.getByTestId(hintTestId)).toBeVisible();
  } else {
    await expect(page.getByTestId(hintTestId)).toHaveCount(0);
  }
}

async function topOf(locator: Locator): Promise<number> {
  await expect(locator).toBeAttached();
  const box = await locator.boundingBox();
  expect(box, `expected ${locator} to have a bounding box`).not.toBeNull();
  return box!.y;
}

async function expectTopWithin(locator: Locator, maxY: number, label: string) {
  await expect(locator).toBeVisible({ timeout: 20_000 });
  expect(await topOf(locator), `${label} top should be within ${maxY}px`).toBeLessThanOrEqual(maxY);
}

async function expectNoDocumentOverflow(page: Page, label: string) {
  const offenders = await page.evaluate(() => {
    const viewportWidth = document.documentElement.clientWidth;
    return Array.from(document.querySelectorAll<HTMLElement>("body *"))
      .filter((element) => {
        if (element.closest(".overflow-x-auto") || element.closest("svg[role='img']")) return false;
        if (element.closest('[data-testid="query-heatmap-page-sticky-header"]')) return false;
        const rect = element.getBoundingClientRect();
        return rect.right > viewportWidth + 1 || rect.left < -1;
      })
      .map((element) => ({
        tag: element.tagName,
        text: (element.textContent ?? "").trim().slice(0, 80),
        className: element.className,
      }))
      .slice(0, 5);
  });
  expect(offenders, `${label} should not leak content outside intentional scroll containers`).toEqual([]);
}

async function rowsAboveFold(rows: Locator, viewportHeight: number): Promise<number> {
  return rows.evaluateAll((elements, height) =>
    elements.filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.top >= 0 && rect.top < height;
    }).length,
    viewportHeight,
  );
}
