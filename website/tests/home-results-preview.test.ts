import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const websiteRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const home = readFileSync(path.join(websiteRoot, "src/pages/index.astro"), "utf8");
const searchBox = readFileSync(path.join(websiteRoot, "src/components/SearchBox.astro"), "utf8");

const RUN_IDS = "6235bd1a,47bdcef5,282a4d75,19b96c85";
const FULL_LABEL = "DuckDB v2.0.0-alpha38615";

function hrefs(): string[] {
  return Array.from(home.matchAll(/href="(\/results\/[^"]*)"/g), (match) => match[1]!.replaceAll("&amp;", "&"));
}

describe("home results preview", () => {
  it("names only a version that a published result records", () => {
    expect(home).not.toMatch(/2\.0\.0-(?!alpha38615)/);
    expect(home).toContain(`title="${FULL_LABEL}"`);
    expect(home).toContain(`<title>${FULL_LABEL}</title>`);
  });

  it("never replaces an accessible name with the shortened version", () => {
    expect(home).not.toMatch(/aria-label="DuckDB v2\.0\.0/);
    expect(home).not.toMatch(/<text[^>]*aria-label=/);
  });

  it("links each preview card to the comparison and the chart it shows", () => {
    const links = hrefs();
    expect(links).toContain(`/results/compare?ids=${RUN_IDS}`);
    expect(links).toContain(`/results/compare?ids=${RUN_IDS}&chart=distribution_box`);
    expect(links).toContain(`/results/compare?ids=${RUN_IDS}&chart=cdf_chart`);
  });

  it("opens results links in the same tab without an external-link arrow", () => {
    const anchors = Array.from(home.matchAll(/<a\b[^>]*href="\/results[^"]*"[^>]*>[\s\S]*?<\/a>/g), (match) => match[0]);
    expect(anchors.length).toBeGreaterThan(0);
    for (const anchor of anchors) {
      expect(anchor).not.toContain('target="_blank"');
      expect(anchor).not.toContain("↗");
    }
  });

  it("links the local step straight to the local result page", () => {
    expect(hrefs()).toContain("/results/local");
  });
});

describe("search dialog", () => {
  it("shows a status instead of an input that nothing reads", () => {
    expect(searchBox).toContain("data-search-status");
    expect(searchBox).not.toMatch(/<input[^>]*type="search"/);
  });

  it("reports a failed search load to the reader", () => {
    expect(searchBox).toMatch(/\.catch\([\s\S]*showStatus\(/);
  });
});
