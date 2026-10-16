import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { render } from "@testing-library/preact";
import { beforeEach, describe, expect, it } from "vitest";
import { Layout } from "@/components/Layout";
import { HEADER_CTA, HEADER_LINKS, HEADER_NAV_ARIA_LABEL } from "@/components/headerContract";

const indexHtml = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "index.html"), "utf8");
const staticDocument = new DOMParser().parseFromString(indexHtml, "text/html");

function sitePathOf(href: string): string {
  return href.startsWith("https://benchbox.dev") ? href.slice("https://benchbox.dev".length) || "/" : href;
}

describe("static results shell", () => {
  it("serves the landmarks, skip link and fallbacks before any script runs", () => {
    const skipLink = staticDocument.querySelector("a.skip-link");
    expect(skipLink?.getAttribute("href")).toBe("#main");
    expect(staticDocument.querySelectorAll("header")).toHaveLength(1);
    expect(staticDocument.querySelectorAll("main#main")).toHaveLength(1);
    expect(staticDocument.querySelectorAll("footer")).toHaveLength(1);
    expect(staticDocument.querySelectorAll(`header nav[aria-label="${HEADER_NAV_ARIA_LABEL}"]`)).toHaveLength(1);
    expect(staticDocument.querySelector("main#main noscript")).not.toBeNull();
    expect(staticDocument.querySelector('main#main [role="status"]')?.textContent).toContain("Loading");
    expect(staticDocument.querySelector('meta[name="description"]')?.getAttribute("content")).toBeTruthy();
  });

  it("lists the same header links as the mounted header", () => {
    const staticLinks = Array.from(staticDocument.querySelectorAll(`header nav[aria-label="${HEADER_NAV_ARIA_LABEL}"] a`)).map(
      (link) => [link.textContent?.trim(), link.getAttribute("href")],
    );
    const expected = [...HEADER_LINKS, HEADER_CTA].map((link) => [link.label, sitePathOf(link.href)]);
    expect(staticLinks).toEqual(expected);
  });

  describe("after mounting", () => {
    beforeEach(() => {
      document.body.innerHTML = staticDocument.body.innerHTML;
    });

    it("replaces the static shell with exactly one header, main and footer", () => {
      const root = document.getElementById("app")!;
      root.replaceChildren();
      render(
        <Layout>
          <p>Page content</p>
        </Layout>,
        { container: root },
      );

      expect(document.querySelectorAll(`header nav[aria-label="${HEADER_NAV_ARIA_LABEL}"]`)).toHaveLength(1);
      expect(document.querySelectorAll("header")).toHaveLength(1);
      expect(document.querySelectorAll("main#main")).toHaveLength(1);
      expect(document.querySelectorAll("footer")).toHaveLength(1);
      expect(document.querySelectorAll("a.skip-link")).toHaveLength(1);
      expect(document.querySelector("main#main")?.textContent).toContain("Page content");
    });
  });
});
