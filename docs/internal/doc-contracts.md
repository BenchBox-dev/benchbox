# Documentation contracts export

Site-owned documentation is validated against facts that core exports, not against
strings the pages are expected to contain. `scripts/doc_contracts.py` builds
`doc-contracts.json` from the live registries, the CLI command tree, the MCP tool
schemas and a few core-owned data files. The site reads that file in its own CI, so a
drift between core behavior and a page is detected after the core change merges.

## Producing and checking the export

```bash
make doc-contracts
make doc-contracts-check
```

`doc-contracts` writes `./doc-contracts.json`, which git ignores. `doc-contracts-check` builds the export twice, validates the schema and requires identical output.

`make docs-generate` runs the check, so the `docs` required context covers it.

The export is deterministic: keys are sorted, no timestamps or absolute paths are
written, and lists that carry registry order are kept in that order. The schema is
`quality/doc-contracts.schema.json`; it sets `additionalProperties: false` on every
object the exporter owns, so a new field requires a schema change in the same commit.

## Sections

| Section | Facts | Replaces |
| --- | --- | --- |
| `package` | version, Development Status classifier, core dependencies, optional extras, release-marker regex | install and beta-surface page checks, README version substitution selector |
| `benchmarks` | ids, public and loader ids, core-only ids, per-benchmark status, query count, DataFrame support, API surfaces, facade counts | public-contract and support-status page checks |
| `platforms` | per-platform status, category, SQL and DataFrame support, per-status snapshot, count summary | support-status snapshot and registry-count markers |
| `platform_manifest` | canonical keys, scoped aliases, adapter coordinates | the generated block in the adding-platforms guide |
| `execution` | default engine, engine classes, declared and selectable engines, compute declarations, reserved and deprecated option keys | execution vocabulary and compute guide checks |
| `platform_options` | per-platform option names, value kind, choices, defaults, aliases | `--platform-option` example validation |
| `tuning` | applied and unapplied tuning types, introspector support, verifiable platforms | tuning support matrix |
| `cli` | every command path and option, the help catalog | quickstart command resolution, CLI example checks |
| `mcp` | local and remote tool parameter schemas, the run-surface ledger data | MCP reference page checks |
| `public_symbols` | the public API symbol inventory | API reference heading coverage |
| `sf1_calibrated_sizes` | baseline sizes, tolerance, measurable benchmark ids | calibrated-size table checks |
| `write_primitives` | validation-query bounds from the catalog YAML | sketch-function page checks |
| `publication` | publish allowlist and exclusions, gated pages, link-check ignore patterns | publish-policy readers and link-check policy |
| `ownership` | ownership counts, site-owned paths, transitions | negative ownership guard |

The MCP omission tiers, tool-to-CLI mapping and omitted command families live in
`quality/mcp-run-surface.json`. The exporter embeds that file and the retained MCP tests
read the same file, so the two cannot disagree.

## Lock-test inventory

`quality/doc-lock-tests.json` maps every test that reads or names a site-owned page to
one of these classifications:

- `contract-field`: replaced by named JSON-pointer fields in `doc-contracts.json`.
- `governance-stays`: asserts a core-owned policy; it stays in core.
- `runtime-stays`: asserts runtime behavior and reads no page; it stays in core.
- `incidental-deleted`: asserts the wording of one page; it is deleted at cutover, not ported.
- `wiring-deleted`: asserts that a page exists or is well-formed; the site build owns that.

`tests/unit/scripts/test_doc_contracts.py` checks that each row names an existing file
and tests, that each named field exists in the built export, and that every
`lock-test` file in the reference inventory has a row.

## Reference inventory

`quality/docs-reference-inventory.json` lists every tracked file outside `docs/` that
references a site-owned path: files named by `quality/docs-ownership.json`, generated
output directories, transition sources, and the extra core inputs listed in
`quality/docs-reference-rules.json`. References are found mechanically: `docs/...`
tokens and `"docs" / "a" / "b"` or `Path("docs", "a")` segment chains. Public URLs and
nested directories such as `other/docs/` are not references.

Each file is classified by the first matching rule in `quality/docs-reference-rules.json`.
A referencing file that matches no rule, or a rule that matches no referencing file,
fails generation, so the inventory has no unclassified rows. Rules marked `aggregate`
(historical records under `_project`) store a count and a digest of the targets instead
of the list.

| Disposition | Meaning at cutover |
| --- | --- |
| `contract-replaced` | the check reads exported fields |
| `contract-tooling` | exporter and inventory code, or its core-owned input |
| `lock-test` | mapped row by row in `quality/doc-lock-tests.json` |
| `governance-stays` | core-owned policy that keeps reading core files |
| `site-pipeline-cutover` | bundle assembly and link checkers that move to the site |
| `generator-deleted` | Python markdown generators removed once the site renders the pages |
| `wiring-updated` | Make, CI, path-filter, inert-path and registry entries edited |
| `prose-link-pinned` | prose relinked to the published or a pinned URL |
| `runtime-link-updated` | shipped source or examples that cite a docs path |
| `historical-record` | audits, decisions and exports that are not rewritten |
| `fixture-data` | docs-shaped sample strings; no page is read |

```bash
make docs-reference-inventory
make docs-reference-inventory-check
```

`docs-reference-inventory` rewrites `quality/docs-reference-inventory.json`; the `-check` target fails when that file is stale.
