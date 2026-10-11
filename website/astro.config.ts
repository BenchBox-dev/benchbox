import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { unified } from "@astrojs/markdown-remark";
import preact from "@astrojs/preact";
import starlight from "@astrojs/starlight";
import { ExpressiveCodeTheme } from "@astrojs/starlight/expressive-code";
import type { AstroIntegration } from "astro";
import { defineConfig } from "astro/config";
import type { SidebarManifest } from "./src/converter/sidebar.ts";
import { toStarlightSidebar } from "./src/converter/sidebar.ts";
import { renderRobots, renderSitemap, sitemapPathForFile } from "./src/lib/page-meta.ts";
import { publishLegacyFiles, renderObjectsInventory, REDIRECT_PAGES, type InventoryEntry, type LegacyFiles } from "./src/lib/legacy-assets.ts";
import { docutilsQuotes, SMARTYPANTS } from "./src/lib/smartypants.ts";
import { treeDigest } from "./src/lib/tree-digest.ts";
import { headingIds } from "./src/plugins/heading-ids.ts";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const codeTheme = ExpressiveCodeTheme.fromJSONString(readFileSync(path.join(repoRoot, "website", "src", "lib", "code-theme.json"), "utf-8"));

function generatedSidebar() {
  const manifest = path.join(repoRoot, "website", ".generated", "manifest", "sidebar.json");
  if (!existsSync(manifest) && process.env.BENCHBOX_ALLOW_EMPTY_SIDEBAR === "1") return [];
  if (!existsSync(manifest)) throw new Error(`Converter output is missing: ${manifest}. Run npm run convert first.`);
  return toStarlightSidebar(JSON.parse(readFileSync(manifest, "utf-8")) as SidebarManifest);
}

function htmlFiles(root: string, relative = ""): string[] {
  return readdirSync(path.join(root, relative), { withFileTypes: true }).flatMap((entry) => {
    const child = path.posix.join(relative, entry.name);
    if (entry.isDirectory()) return entry.name === "assets" || entry.name === "pagefind" || entry.name === "_astro" ? [] : htmlFiles(root, child);
    return entry.name.endsWith(".html") ? [child] : [];
  });
}

const sitemapOwnedByPublishStatic: AstroIntegration = { name: "@astrojs/sitemap", hooks: {} };

const devDirectoryIndex: AstroIntegration = {
  name: "benchbox-dev-directory-index",
  hooks: {
    "astro:server:setup": ({ server }) => {
      server.middlewares.use((request, _response, next) => {
        const [pathname, query] = (request.url ?? "").split("?", 2);
        if (pathname.length > 1 && pathname.endsWith("/") && !/^\/[@_]/.test(pathname)) {
          request.url = `${pathname}index.html${query === undefined ? "" : `?${query}`}`;
        }
        next();
      });
    },
  },
};

const publishStatic = (): AstroIntegration => ({
  name: "benchbox-publish-static",
  hooks: {
    "astro:build:done": ({ dir }) => {
      const out = fileURLToPath(dir);
      const explorerDist = path.join(repoRoot, "results-explorer", "dist");
      if (!existsSync(explorerDist)) throw new Error(`Results Explorer build is missing: ${explorerDist}`);
      const sourceDigest = treeDigest(explorerDist);
      cpSync(explorerDist, path.join(out, "results"), { recursive: true });
      const mountedDigest = treeDigest(path.join(out, "results")).sha256;
      if (mountedDigest !== sourceDigest.sha256) throw new Error(`Results Explorer was altered while mounting: ${sourceDigest.sha256} became ${mountedDigest}`);
      cpSync(path.join(repoRoot, "landing", "hero.png"), path.join(out, "hero.png"));
      const images = path.join(repoRoot, "docs", "blog", "images");
      mkdirSync(path.join(out, "_images"), { recursive: true });
      cpSync(path.join(repoRoot, "docs", "CNAME"), path.join(out, "CNAME"));
      writeFileSync(path.join(out, ".nojekyll"), "");
      writeFileSync(path.join(out, "robots.txt"), renderRobots());
      const pages = htmlFiles(out).filter(
        (file) => file !== "404.html" && file !== "blog/drafts.html" && !Object.hasOwn(REDIRECT_PAGES, file),
      );
      writeFileSync(path.join(out, "sitemap.xml"), renderSitemap(pages.map(sitemapPathForFile)));
      const legacy = JSON.parse(readFileSync(path.join(repoRoot, "website", ".generated", "manifest", "legacy-files.json"), "utf-8")) as LegacyFiles;
      for (const name of legacy.images) cpSync(path.join(images, name), path.join(out, "_images", name));
      publishLegacyFiles(legacy, path.join(repoRoot, "docs"), out);
      const entries = JSON.parse(readFileSync(path.join(repoRoot, "website", ".generated", "manifest", "inventory-entries.json"), "utf-8")) as InventoryEntry[];
      const version = /^version = "([^"]+)"/m.exec(readFileSync(path.join(repoRoot, "pyproject.toml"), "utf-8"))?.[1] ?? "";
      writeFileSync(path.join(out, "docs", "objects.inv"), renderObjectsInventory(entries, "BenchBox", version));
    },
  },
});

export default defineConfig({
  site: "https://benchbox.dev",
  trailingSlash: "ignore",
  build: { format: "file" },
  markdown: { processor: unified({ remarkPlugins: [headingIds, docutilsQuotes], smartypants: SMARTYPANTS }) },
  integrations: [
    sitemapOwnedByPublishStatic,
    devDirectoryIndex,
    preact(),
    starlight({
      title: "BenchBox",
      pagefind: false,
      disable404Route: true,
      lastUpdated: false,
      routeMiddleware: "./src/starlight-route.ts",
      customCss: [
        "@fontsource-variable/instrument-sans/wght.css",
        "@fontsource-variable/martian-mono/wdth.css",
        "../landing/shared/site-tokens.css",
        "../landing/shared/site-shell.css",
        "./src/styles/shell.css",
        "./src/styles/starlight-map.css",
      ],
      components: {
        Header: "./src/components/starlight/Header.astro",
        Footer: "./src/components/starlight/Footer.astro",
        Sidebar: "./src/components/starlight/Sidebar.astro",
        TwoColumnContent: "./src/components/starlight/TwoColumnContent.astro",
        ThemeProvider: "./src/components/starlight/ThemeProvider.astro",
        ThemeSelect: "./src/components/starlight/Empty.astro",
      },
      sidebar: generatedSidebar(),
      expressiveCode: {
        themes: [codeTheme],
        useStarlightUiThemeColors: false,
        minSyntaxHighlightingColorContrast: 4.5,
        styleOverrides: {
          borderRadius: "14px",
          borderColor: "var(--code-border)",
          codeBackground: "var(--code-bg)",
          codeForeground: "var(--code-fg)",
          frames: {
            frameBoxShadowCssValue: "none",
            editorBackground: "var(--code-bg)",
            editorTabBarBackground: "var(--code-bg)",
            editorTabBarBorderBottomColor: "color-mix(in srgb, var(--code-fg) 16%, transparent)",
            editorActiveTabBackground: "var(--code-bg)",
            editorActiveTabForeground: "var(--code-comment)",
            editorActiveTabIndicatorTopColor: "transparent",
            editorActiveTabIndicatorBottomColor: "var(--code-comment)",
            terminalBackground: "var(--code-bg)",
            terminalTitlebarBackground: "var(--code-bg)",
            terminalTitlebarForeground: "var(--code-comment)",
            terminalTitlebarDotsForeground: "var(--code-comment)",
            terminalTitlebarBorderBottomColor: "color-mix(in srgb, var(--code-fg) 16%, transparent)",
            inlineButtonForeground: "var(--code-fg)",
            inlineButtonBorder: "var(--code-fg)",
          },
        },
      },
    }),
    publishStatic(),
  ],
});
