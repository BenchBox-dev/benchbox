import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";
import { buildSite } from "../src/converter/build.ts";
import { EXCLUSIONS_FILE } from "../src/converter/sources.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const goldenRoot = path.join(here, "golden");
const realDocs = path.join(here, "..", "..", "docs");
const update = process.env.UPDATE_GOLDEN === "1";

function filesUnder(root: string): string[] {
  if (!existsSync(root)) return [];
  const found: string[] = [];
  for (const name of readdirSync(root).sort()) {
    const absolute = path.join(root, name);
    if (statSync(absolute).isDirectory()) found.push(...filesUnder(absolute).map((entry) => `${name}/${entry}`));
    else found.push(name);
  }
  return found;
}

function outputFile(files: Map<string, string>, fixture: string): string {
  const bases = [`content/docs/${fixture}`, `content/${fixture}`];
  const candidates = bases.flatMap((base) => [base, base.replace(/\.md$/, ".mdx")]);
  const found = candidates.find((candidate) => files.has(candidate));
  if (found === undefined) throw new Error(`converter produced no output for ${fixture}`);
  return found;
}

const cases = readdirSync(goldenRoot)
  .filter((name) => statSync(path.join(goldenRoot, name)).isDirectory())
  .sort();

describe.each(cases)("golden %s", (name) => {
  const root = path.join(goldenRoot, name);
  const expectedRoot = path.join(root, "expected");
  const sources = JSON.parse(readFileSync(path.join(root, "source.json"), "utf-8")) as Record<string, string>;
  const pages = Object.keys(sources).filter((fixture) => fixture.endsWith(".md"));
  const workDir = mkdtempSync(path.join(os.tmpdir(), `golden-${name}-`));

  function materialize(): string {
    const target = path.join(workDir, "docs");
    rmSync(target, { recursive: true, force: true });
    cpSync(path.join(root, "docs"), target, { recursive: true });
    if (!existsSync(path.join(target, EXCLUSIONS_FILE))) writeFileSync(path.join(target, EXCLUSIONS_FILE), "");
    for (const [fixture, real] of Object.entries(sources)) {
      mkdirSync(path.dirname(path.join(target, fixture)), { recursive: true });
      copyFileSync(path.join(realDocs, real), path.join(target, fixture));
    }
    return target;
  }

  afterAll(() => rmSync(workDir, { recursive: true, force: true }));

  it(name === "tpc-ds-diagrams" ? "renders the Mermaid entity diagram" : "converts to the expected output", () => {
    const result = buildSite({ docsRoot: materialize() });
    const errorsPath = path.join(expectedRoot, "errors.txt");
    const produced = result.errors.map((error) => error.message).join("\n");
    if (name === "tpc-ds-diagrams") {
      expect(result.errors).toEqual([]);
      const output = result.files.get(outputFile(result.files, "benchmarks/tpc-ds.md"));
      expect(output).toContain("<Mermaid diagram={");
      return;
    }
    if (update) {
      mkdirSync(expectedRoot, { recursive: true });
      if (result.errors.length > 0) writeFileSync(errorsPath, `${produced}\n`);
      else
        for (const fixture of pages) {
          const file = outputFile(result.files, fixture);
          const target = path.join(expectedRoot, file);
          mkdirSync(path.dirname(target), { recursive: true });
          writeFileSync(target, result.files.get(file) ?? "");
        }
      return;
    }
    if (existsSync(errorsPath)) {
      expect(produced).toBe(readFileSync(errorsPath, "utf-8").trimEnd());
      return;
    }
    expect(result.errors).toEqual([]);
    const expectedFiles = filesUnder(expectedRoot);
    expect(expectedFiles.length).toBeGreaterThan(0);
    for (const file of expectedFiles) expect(result.files.get(file)).toBe(readFileSync(path.join(expectedRoot, file), "utf-8"));
  });
});
