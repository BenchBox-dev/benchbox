# ADR: Own published documentation in the site repository

## Status

> **Core removal (2026-10-26).** The migration this ADR constrains has
> landed: the site-owned Markdown and the Python Markdown generators named
> below now live in the `benchbox-site` repository, and the site-inputs
> bundle default is schema 5 with no `docs`/`downloads` members. The body
> below is kept as the historical record; treat generator and frozen-source
> references as pre-removal.

Accepted 2026-10-18. Supersedes D1 and refines D16 of the
[site repository split](adr-site-repo-split.md). This record defines the target
ownership and migration constraints; it does not change publication, bundle
emission, generators, or deployed behavior.

## Context

The site repository isolates the renderer and deployment control plane, but its
hand-written documentation still arrives from core. That makes prose changes
wait for core integration and leaves publication dependent on Python Markdown
generators and tests that inspect local documentation text. Code-derived facts
must remain authoritative in core without keeping authorship there.

The existing publication boundary is defined by `docs/publish-allowlist.txt` and
`docs/publish-exclusions.txt`. The site's
[`sources.ts`](https://github.com/BenchBox-dev/benchbox-site/blob/main/website/src/converter/sources.ts)
consumes those lists, and
[`assembleDocsSource`](https://github.com/BenchBox-dev/benchbox-site/blob/main/website/src/lib/site-inputs.ts)
assembles bundle docs and downloads. Core's `scripts/site_inputs.py` already
exports and attests site inputs; `scripts/generate_query_docs.py`,
`scripts/generate_compat_docs.py`, and
`scripts/generate_tuning_support_matrix.py` already compute the generated
reference material. Extend those data boundaries rather than copy Python into
the site or establish a second source of runtime facts.

`scripts/export_docs_data.py` is that data boundary for generated pages. It emits deterministic JSON, validated
by `quality/docs-data.schema.json`, with per-query identity, descriptions, groups, reference and native SQL,
DataFrame renderings, source links, notices and recipe inputs; per-benchmark metadata; the compatibility rule
inventory; and the tuning platform support inputs. Run `uv run -- python scripts/export_docs_data.py --output
PATH`, and add `--check` to fail when `PATH` differs from a fresh export. The Markdown generators keep rendering
until the site generator replaces them.

## Ownership

`quality/docs-ownership.json` is the explicit migration inventory. Its schema is
integer `1`; its `files` map assigns every tracked `docs/**` file exactly once
to `site-owned`, `core-only`, or `core-generated`. Its `generated` records name
each output path and generator, including the ignored query output root
`docs/benchmarks/queries/` and the three tracked compatibility/tuning outputs.
Generated classification records provenance, not permanent core authorship of
rendered pages. Transitions record the exceptions below instead of hiding them
in directory heuristics. Validation rejects missing, duplicate, unsafe, unknown,
or misclassified inputs and incorrect generated boundaries.

| Material | Authority and destination |
|---|---|
| Hand-written published pages and their authored assets | Site, preserving documentation-relative paths and public URLs. |
| Query, compatibility and tuning facts | Core exports structured JSON; site TypeScript renders the pages at build time. |
| `docs/agent/`, `docs/internal/`, `docs/design/future-state/`, excluded threat model, workaround index and licensing analysis | Core-only; never imported as published sources. |
| `docs/README.md` | Core retains only the minimal release/version marker required by core consumers. Site owns the landing prose and tags at its own `docs/README.md`; its version token comes from `manifest.package_version`. The same relative path has different roles, not shared prose ownership. |
| `docs/publish-*.txt` | Core retains governance records and their core assertions. Site replaces list-driven source filtering with a negative ownership guard that rejects core-only content. |
| `docs/development/browser-duckdb-schema.sql` | Relocate the core Explorer contract to `_project/design/browser-duckdb-schema.sql` and migrate all consumers before removing the old path. It is not site prose. |
| `docs/linkcheck_ignore.txt` | Migrate the applicable placeholder and ignore policy into site `.lychee.toml`; do not silently weaken it. |
| `docs/operations/local-platform-provisioning.tsv` | Retain in core; user-facing references become pinned GitHub links. |

Core-only operational bindings to published paths must be relocated or changed
to the correct authority before deletion. Mixed governance tests keep their core
assertions; moving prose is not permission to delete release, publication,
privacy, runtime, or soundness controls.

## Data and semantic contracts

Core exports deterministic, schema-validated `docs-data` and
`doc-contracts.json` from real registries, APIs and source data. Query data must
include the metadata, SQL and DataFrame renderings, variant/error information,
source targets and notices needed by the current renderer. Compatibility and
tuning data must likewise preserve every existing generated fact. Retain the
Python Markdown generators until parity and cutover are proven; delete them
afterward rather than maintain two permanent renderers.

The contract export carries code-derived facts: platform and benchmark
registries, support statuses, execution vocabulary, CLI and platform options,
public symbols, calibrated sizes, quickstart commands, sketch-function data,
package version, and public contract records. In particular, export the platform
manifest facts used by the authored block in
`development/adding-new-platforms.md`; TypeScript compares that block with the
facts instead of introducing another Python prose rewrite.

Inventory every reference to site-owned paths across tests, scripts, runtime
code, workflows, Makefiles and tracked project tooling. Each assertion must
become a semantic contract check, remain a core behavior/governance assertion,
or be removed as obsolete wiring or incidental wording. Do not export expected
document strings as self-certified facts or preserve source-text assertions
merely because they existed.

The site validates semantic contracts in both pull-request CI and
bundle-dispatch deployment. A core change can therefore expose documentation
drift after merge, as the split ADR permits; a failing consumer must retain the
last good site, not publish inconsistent docs. No Node build is added to core.
No Python source, project/lock files, tooling invocation or Python source history
enters the site; Python, uv and uvx examples in documentation remain allowed.
No JavaScript or TypeScript source enters core.

## Bundle compatibility and activation

D16's producer-first rule means capability first, not unsupported emission.
Schema majors have exact, fail-closed member sets. Every emitted bundle must be
accepted by the currently deployed site, including during rollback.

1. Land core's schema-4 and schema-5 producer capabilities behind explicit
   local/CI selectors. Published bundles remain schema 3 with its unchanged
   member set; candidate bundles are not normal publication artifacts.
2. Deploy the site consumer accepting schemas `[3, 4]`, with both schemas proven
   and schema-4 documentation data and contracts understood.
3. Only then activate core schema-4 publication, retaining authored docs and
   downloads until the ownership cutover prerequisites pass.
4. At the snapshot import, deploy the site accepting `[4, 5]`. Schema 4 supplies
   the pinned data/contracts while the site owns prose and rendering; schema 5
   support is ready before any schema-5 publication.
5. Only after that consumer is deployed may core remove the `docs` and
   `downloads` members and emit schema 5. Exact schema-specific checks remain
   mandatory; accepting a major never means accepting arbitrary members.

Hosted repository settings, rulesets and required-check names do not change.
Core's `docs` required context continues reporting. Protected core paths retain
oracle review; unavailable or blocking oracle review stops the change.

## History, rights and canonical links

Filter the authorized documentation history into
`history/docs-through-<core sha>` in the site repository. This archive branch is
never merged. Import the reviewed snapshot with a squash, not a history merge.
Scrub private `Claude-Session` lines and unresolved real secrets while preserving
historical human messages, authorship, trailers and required notices. Privacy
placeholders must stay narrowly equivalent to existing policy; any unresolved
real secret blocks publication. Python source/tooling must be absent from both
the snapshot and the filtered site history.

Preserve rights and attribution with the content. Before removing download
copies, place byte-identical copies of the canonical TPC EULAs beside their
query templates in core and validate them against the originals. Redirects
retain visible links to the applicable notice at the pinned revision. This
record does not alter licence terms or establish legal compliance.

Retire documentation download copies in favor of commit-pinned GitHub targets.
Legacy `/docs/_downloads/<md5>/<name>` paths become redirect pages, with real
browser navigation verified for every inventoried legacy target. Out-of-docs
links also use pinned GitHub URLs. Published page references use canonical
`https://benchbox.dev/docs/...html` URLs; core-only documents stay GitHub
references rather than becoming published accidentally.

## Single cutover proof

Move all published authorship together, only after the data exports, consumer,
semantic checks, source relocation and rendering parity are ready. Merge
outstanding query-link changes before recording the snapshot. If they cannot
merge, stop snapshot selection rather than applying an unmerged patch to an
import required to equal the snapshot.

Record the core snapshot SHA, ownership inventory, generated-output inventory,
filtered-history audit and known-good deployment receipt. Freeze moved content
and prove no moved prose or asset changed after the snapshot. A release update
may change only the retained README marker line; landing prose changes must
invalidate and refresh the snapshot. Both repositories must agree on that
exception.

Prove TypeScript/Python generated-output parity using identical pinned inputs.
If output still differs after one fix attempt, stop. Compare the ownership
cutover against the same-input baseline: only edit URLs may differ. Redirect
and canonical-link changes, spelling corrections and generator provenance
updates need a separately reviewed output inventory before they enter that
baseline. Require complete legacy-navigation proof, EULA
adjacency, and successful privacy, Explorer compatibility, snapshot-digest,
link and origin deployment gates. A failed or unprovable condition blocks the
cutover; it is not waived by this decision.

## Rollback and consequences

Before each production change, verify that the known-good receipt and its
retained artifact are retrievable. On regression, use the site deployment
workflow's rollback mode with that receipt's run identifier, verify restoration,
receipt and probes, and stop. Never rebuild an old site against a newer bundle
as rollback. Keep compatible producer emission while recovering; do not remove
the consumer's only usable schema or rely on restoring deleted source paths.

Documentation authorship becomes independent of core releases, while factual
correctness remains a cross-repository contract. Trunk-only documentation and
the existing release policy remain unchanged; release-pinned documentation is
not introduced. Live-site remediation and advisory pre-merge builds remain
related work, not implicitly completed by this decision.

The [future-state proposals](../../design/future-state/index.md) and
[architecture contract index](../../design/future-state/contract-index.md)
retain their existing priorities and evidence gates. This is documentation
ownership, not package extraction, public API redesign, or authorization to
remove the live bundle-publish subsystem.
