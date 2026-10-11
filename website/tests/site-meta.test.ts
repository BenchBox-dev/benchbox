import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { builtSite } from "./built-site.ts";
import { REDIRECT_PAGES } from "../src/lib/legacy-assets.ts";
import { canonicalPath, pageMeta, renderRobots, renderSitemap, sitemapPathForFile } from "../src/lib/page-meta.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..");
const dist = builtSite();
const origin = "https://benchbox.dev";

describe("canonicalPath", () => {
  it("keeps the site root and the Explorer directory", () => {
    expect(canonicalPath("/")).toBe("/");
    expect(canonicalPath("/index.html")).toBe("/");
    expect(canonicalPath("/results/")).toBe("/results/");
  });

  it("appends the html extension to page paths", () => {
    expect(canonicalPath("/docs/benchmarks/tpch")).toBe("/docs/benchmarks/tpch.html");
    expect(canonicalPath("/docs/benchmarks/")).toBe("/docs/benchmarks/index.html");
  });

  it("leaves paths that already end in html", () => {
    expect(canonicalPath("/docs/index.html")).toBe("/docs/index.html");
  });
});

describe("pageMeta", () => {
  it("emits canonical, description, Open Graph and Twitter tags", () => {
    const tags = pageMeta({ title: "T", description: "D", pathname: "/blog/x" });
    const names = tags.map((tag) => tag.attrs.rel ?? tag.attrs.name ?? tag.attrs.property);
    expect(names).toEqual(
      expect.arrayContaining(["canonical", "description", "og:title", "og:description", "og:url", "og:image", "twitter:card", "twitter:title", "twitter:description"]),
    );
    expect(tags.find((tag) => tag.attrs.rel === "canonical")?.attrs.href).toBe(`${origin}/blog/x.html`);
  });

  it("falls back to the site description", () => {
    const tags = pageMeta({ title: "T", pathname: "/" });
    expect(tags.find((tag) => tag.attrs.name === "description")?.attrs.content).toContain("database benchmarking");
  });
});

describe("sitemap and robots", () => {
  it("maps files to served paths", () => {
    expect(sitemapPathForFile("index.html")).toBe("/");
    expect(sitemapPathForFile("results/index.html")).toBe("/results/");
    expect(sitemapPathForFile("docs/index.html")).toBe("/docs/index.html");
  });

  it("escapes and sorts urls", () => {
    const locs = [...renderSitemap(["/b.html", "/a&b.html"]).matchAll(/<loc>([^<]*)<\/loc>/g)].map((match) => match[1]);
    expect(locs).toEqual([`${origin}/a&amp;b.html`, `${origin}/b.html`]);
  });

  it("points robots at the sitemap", () => {
    expect(renderRobots()).toContain(`Sitemap: ${origin}/sitemap.xml`);
  });
});

function inventoryPages(site: string): string[] {
  const code = [
    "import json, sys",
    "from pathlib import Path",
    "sys.path.insert(0, 'scripts')",
    "from site_inventory import build_inventory",
    "print(json.dumps(sorted(build_inventory(Path(sys.argv[1]))['pages'])))",
  ].join("\n");
  const run = spawnSync("python3", ["-c", code, site], { cwd: repoRoot, encoding: "utf-8", maxBuffer: 64 * 1024 * 1024 });
  if (run.status !== 0) throw new Error(`site inventory failed: ${run.stderr}`);
  return (JSON.parse(run.stdout) as string[]).map((entry) => entry.slice(1));
}

function head(site: string, file: string): string {
  const markup = readFileSync(path.join(site, file), "utf-8");
  return markup.slice(0, markup.indexOf("</head>"));
}

function content(markup: string, attribute: "name" | "property", value: string): string | undefined {
  const tag = new RegExp(`<meta[^>]*${attribute}="${value}"[^>]*>`).exec(markup)?.[0];
  return tag && /content="([^"]*)"/.exec(tag)?.[1];
}

describe.skipIf(!dist)("built site", () => {
  const site = dist as string;
  const pages = dist ? inventoryPages(site).filter((file) => !Object.hasOwn(REDIRECT_PAGES, file)) : [];

  it("gives every html page canonical, description, Open Graph and Twitter tags", () => {
    const missing: string[] = [];
    for (const file of pages) {
      const markup = head(site, file);
      const canonical = /<link[^>]*rel="canonical"[^>]*href="([^"]*)"/.exec(markup)?.[1] ?? /<link[^>]*href="([^"]*)"[^>]*rel="canonical"/.exec(markup)?.[1];
      const required = [
        canonical,
        content(markup, "name", "description"),
        content(markup, "property", "og:title"),
        content(markup, "property", "og:description"),
        content(markup, "property", "og:url"),
        content(markup, "property", "og:image"),
        content(markup, "name", "twitter:card"),
        content(markup, "name", "twitter:title"),
        content(markup, "name", "twitter:description"),
      ];
      if (required.some((value) => !value)) missing.push(file);
      else if (!canonical?.startsWith(origin)) missing.push(`${file} (canonical origin)`);
      else if (file !== "404.html" && canonical !== `${origin}${sitemapPathForFile(file)}`) missing.push(`${file} (canonical ${canonical})`);
      else if (content(markup, "property", "og:url") !== canonical) missing.push(`${file} (og:url)`);
      else if (content(markup, "name", "twitter:card") !== "summary_large_image") missing.push(`${file} (twitter:card)`);
    }
    expect(pages.length).toBeGreaterThan(1000);
    expect(missing).toEqual([]);
  });

  it("generates the blog drafts page but excludes it from sitemap.xml", () => {
    const draftsPage = readFileSync(path.join(site, "blog", "drafts.html"), "utf-8");
    expect(draftsPage).toContain("<title>Drafts - BenchBox Blog</title>");
    const listed = [...readFileSync(path.join(site, "sitemap.xml"), "utf-8").matchAll(/<loc>([^<]*)<\/loc>/g)].map((match) => match[1]);
    expect(listed).not.toContain(`${origin}/blog/drafts.html`);
  });

  it("lists exactly the inventory html pages except 404.html and blog/drafts.html in sitemap.xml", () => {
    const listed = [...readFileSync(path.join(site, "sitemap.xml"), "utf-8").matchAll(/<loc>([^<]*)<\/loc>/g)].map((match) => match[1]);
    const expected = pages.filter((file) => file !== "404.html" && file !== "blog/drafts.html").map((file) => `${origin}${sitemapPathForFile(file)}`);
    expect(new Set(listed).size).toBe(listed.length);
    expect([...listed].sort()).toEqual([...expected].sort());
  });

  it("references the sitemap from robots.txt", () => {
    expect(readFileSync(path.join(site, "robots.txt"), "utf-8")).toContain(`Sitemap: ${origin}/sitemap.xml`);
  });

  it("keeps CNAME and .nojekyll and no stray sitemap files", () => {
    expect(readFileSync(path.join(site, "CNAME"), "utf-8")).toBe(readFileSync(path.join(repoRoot, "docs", "CNAME"), "utf-8"));
    expect(readFileSync(path.join(site, ".nojekyll"), "utf-8")).toBe("");
    for (const stray of ["sitemap-index.xml", "sitemap-0.xml"]) expect(() => readFileSync(path.join(site, stray))).toThrow();
  });

  it("publishes the Explorer page exactly as the Explorer builds it", () => {
    const published = readFileSync(path.join(site, "results", "index.html"), "utf-8");
    const source = readFileSync(path.join(repoRoot, "results-explorer", "index.html"), "utf-8");
    expect(published).toContain('<link rel="canonical" href="https://benchbox.dev/results/" />');
    expect(source).toContain('href="https://benchbox.dev/results/"');
  });

  it("keeps the results redirect and noindex in the 404 page", () => {
    const markup = readFileSync(path.join(site, "404.html"), "utf-8");
    expect(markup).toContain("benchbox.results.redirect");
    expect(markup).toContain("window.location.pathname.startsWith('/results/')");
    expect(markup).toContain("window.location.replace('/results/')");
    expect(markup).toContain('name="robots" content="noindex"');
  });
});
