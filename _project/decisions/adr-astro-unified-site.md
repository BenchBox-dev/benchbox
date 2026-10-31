# ADR: Move benchbox.dev to one Astro site

## Status

Accepted 2026-10-03 under standing approval S1 (gate G0, tracker item
`astro-site-00-decisions-adr`), after independent review. See
[Approval](#approval).

Partly superseded by [the site repo split](../../docs/internal/adr/adr-site-repo-split.md):
the site, its inventory and its deploy plane moved to `BenchBox-dev/benchbox-site`,
Sphinx was retired at cutover (D11), and docs revision follows trunk only (D7,
which supersedes ADR D13). Paths below that name `landing/`, `website/`,
`docs/_templates/`, `docs/conf.py` or `_project/design/site-inventory/` describe
the layout at decision time.

## Context

benchbox.dev is assembled from three separately built parts by
`scripts/assemble_public_site.py`:

- `landing/`: a static landing page and the `/prompts/` composer.
- `docs/`: Sphinx with furo, MyST, ablog (blog), sphinx_tags, sphinx_design and
  autodoc. 354 Markdown and 53 reStructuredText source files (some excluded
  from the build); 27 pages carry 83 autodoc directives.
- `results-explorer/`: Vite 6, Preact, TypeScript, Tailwind and DuckDB-WASM,
  served at `/results/`.

The parts share no layout, tokens, navigation or search. The goal is one site
with one shell, built by Astro, that keeps every public URL and the Atom feed.

Design reference: <https://claude.ai/artifact/J43yBKwtQAbL6d6fTWVfQB>.

Related records:

- [`adr-dev-loop-v2`](../../docs/development/adr/adr-dev-loop-v2.md), D2 (site-deploy plane)
  and D4 (soundness review).
- [`adr-independent-publication-authorities`](../../docs/development/adr/adr-independent-publication-authorities.md),
  superseded for the control plane by dlv2 D2; its artifact boundaries still apply.
- Tracker items `dlv2-60-publication-integration`,
  `dlv2-62-publication-cutover-and-retirement`,
  `dlv2-61-retire-docs-yml-pages-fallback` and
  `ci-dedupe-11-required-public-site-visual-check`.
- [`public-site-visual-required-check-2026-09-24`](public-site-visual-required-check-2026-09-24.md).

## Decisions

Each decision is **decided** unless marked **deferred to gate**.

### D1 Package location: decided

The Astro package lives in `website/`. `assemble_public_site.py --site-dir site`
writes its output to `site/` (gitignored, deleted on each run), and the
dlv2-60 tracker item reserves `site/routes.yml`.

### D2 Explorer integration: decided

`results-explorer/` stays its own Vite and Preact package, served at
`/results/`. It is reused, not left unchanged. It needs:

- the shared header and shell extracted from `App.tsx`;
- controlled global CSS;
- query-string state kept;
- deep links to nested `/results/` routes on GitHub Pages;
- worker and WASM asset paths, and their recovery, verified in a browser.

Docs and blog pages never download the Explorer runtime. Embeds come only after
cutover (`astro-site-60`). Rationale: the Explorer has its own test suites and
data contract; folding it into Astro would change query and admission code that
G-d freezes.

### D3 URL policy: decided

Keep existing routes where practical, `.html` included. Quality is measured by a
reviewed URL compatibility report (`astro-site-50`): zero broken public URLs
and zero broken fragments. Each redirect page (meta refresh plus canonical;
GitHub Pages cannot send 301s) is listed with a reason. There is no
redirect-percentage threshold. Rationale: a percentage hides which links break;
a reviewed list does not.

### D4 Content source: decided

`docs/` stays the content directory. Until the renderer cutover, every page
builds in both Sphinx and Astro from the same `docs/` sources. No converted
output is committed and nothing is edited in two places. Rationale: a second
copy drifts the day it is made.

### D5 Versions: decided

Follow the dlv2-60 `site/routes.yml` manifest: `/` and `/docs/` from the latest
release tag; `/docs/dev/`, `/blog/` and `/results/` from trunk. The API
reference documents the released wheel. Hosting historical guide versions is a
separate decision, out of scope here.

### D6 Layouts: decided, with a spike check

A custom Astro shell for landing, blog and results. Docs use Starlight inside
the shared shell. Custom docs layouts replace Starlight only if the spike (G1,
`astro-site-10`) shows its overrides are brittle. Rationale: Starlight gives
sidebar, table of contents and Pagefind for free; the spike tests whether its
shell can be replaced cleanly.

### D7 API reference: decided

The reference is a set of curated, authored Markdown contracts for an explicit
public-symbol list, verified against the released wheel. It has no docstring
dependency and does not archive removed prose
([comment policy](../../docs/development/comment-policy.md)). The symbol list,
verification, selection rule, page template, drift check and URL and anchor map
are in [the API reference contract decision](astro-api-reference-contract.md).

### D8 Feed: decided

Keep Atom at `/blog/atom.xml` with unchanged entry ids. Rationale: changed ids
make every reader show all posts as new.

### D9 Search: decided

Pagefind indexes docs and blog HTML only. The first release's unified search
covers docs and blog; results stay reachable through Explorer filtering.
Static result summaries (`astro-site-61`) come later. Search indexes obey
Explorer visibility and withdrawal rules.

### D10 Tooling: decided

Node 20, to match CI (`ci.yml` pins 20). Node 20 is past end of life, so the
spike (G1) confirms the chosen Astro and Starlight versions support it. If they
need Node 22, the spike's follow-up moves CI and `website/` to 22 in one PR with
its drift guard. An exact lockfile. No root `package.json` unless the spike
shows workspaces are needed.

Node 22 for `website/` and the `site-build` job (spike finding). `website/` ships in release tags, like `docs/` and `landing/`, and stays out of the Python sdist and wheel.

### D11 Blog drafts: decided

`_blog/` (109 Markdown files: research, outlines and archives) stays outside
every published collection.

### D12 Publication: decided

The site plugs into the dlv2-60 site-deploy plane (dlv2 D2) and keeps its
artifact boundaries. Explorer code, corpus data, prose and API docs stay
separately sourced and pinned, with provenance, privacy checks, route digests,
receipts and rollback by receipt.

### D13 Atomic renderer cutover: decided

The renderer set covers every route except `/results/`: `/`, `/prompts/`,
`/docs/`, `/docs/dev/`, `/blog/` and the root `404.html`. Before cutover these
are "legacy": the static `landing/` pages plus Sphinx. After cutover they are
Astro. `/results/` is always the Vite Explorer; after cutover it runs inside
the Astro shell (D2).

Production never serves an artifact that mixes legacy and Astro routes. Legacy
renders every route in the set until a release tag contains `website/` and all
migrated content. At that release one site-deploy run switches the whole set
to Astro, and the Explorer to the shared shell, together. Astro then renders
stable content from the release tag and dev content from trunk. Site-deploy
assembly refuses an artifact that mixes legacy and Astro routes.

### D14 Overlapping work: decided

Ownership follows `quality/comment-cleanup-scope.json` and the tracker.

Rules for every `comment-cleanup-*` item below:

- **Scope.** Comment-only edits. They must not delete files, rename routes or
  change rendered DOM (excluding comment nodes) or visual output. The proof is the `astro-site-01`
  inventory diff plus the ci-dedupe-11 visual check. A byte change that only
  removes a comment is allowed.
- **Order.** On a file that an `astro-site-*` item also edits, the PR that lands
  first wins and the other rebases. No item holds a file open across a merge.
- **After retirement.** A file that `astro-site-54` deletes leaves the
  cleanup item's scope once 54 lands.

| Item | Agreed scope and order |
| --- | --- |
| `comment-cleanup-site-assets` | Owns `landing/**`, `docker/**` and the root `index.html`. The general rules apply. Once `astro-site-35` has ported landing, comment removal in `landing/**` is moot; the item skips it. |
| `comment-cleanup-documentation-samples` | Owns `docs/**`, including `docs/_static/**`, `docs/_templates/**` and every page. It must not edit an `.rst` page from the day `astro-site-30` starts until its PR merges; 30 records its start in its tracker context. Pages that `astro-site-31` or `33` read keep their MyST constructs; removing comments must not change those constructs. |
| `comment-cleanup-generators` | Owns `scripts/generate_{compat_docs,query_docs,landing_quickstarts,corpus_inventory,changelog_entry}.py`. Output bytes never change. Output paths and CLI flags stay frozen until `astro-site-32` has repointed them; after that it rebases on 32. |
| `comment-cleanup-contracts-gate` | Keeps singleton ownership of `docs/conf.py`: the autodoc config, the `config-inited` `_generate_query_docs` hook and the autodoc consumer edges. Other items edit `docs/conf.py` only as named here, each rebasing on any contracts-gate change: `astro-site-30` may set `myst_enable_extensions`; `astro-site-32` changes only the `_generate_query_docs` hook, turning it into an explicit build step; `astro-site-54` deletes the file. `astro-site-34` writes the authored contract pages under `docs/`, in the format fixed by `astro-site-02`, so Sphinx builds them (D4). It also retargets or removes the 83 autodoc directives on 27 pages. That discharges the contracts-gate acceptance for those directives. |
| Docstring removal (module `comment-cleanup-*` items) | Must not delete a public docstring that autodoc renders until `astro-site-02` has recorded the contract source SHA and `astro-site-34` has landed the page for that symbol. Gating runs through `comment-cleanup-contracts-gate`, which already blocks module deletion. |
| `comment-cleanup-explorer-ui`, `comment-cleanup-explorer-build`, `comment-cleanup-explorer-data` | explorer-ui owns `src/App.tsx` and `src/index.css`; explorer-data owns `src/lib`, `db.ts` and `types.ts`; explorer-build owns `vite.config.ts` and the rest of `results-explorer/**`. `astro-site-21` and `astro-site-40` edit only shell, CSS, path and config code there, never query, metric or admission logic (G-d). On shared files, the general ordering rule applies. Explorer test suites stay green on both sides. |
| `comment-cleanup-publication-tooling` | Owns `scripts/assemble_public_site.py` and `scripts/publication/**`, including `check_artifact_privacy.py`. `astro-site-52` changes behaviour only through the dlv2-60 plane, and `astro-site-54` retires the legacy assembly; the cleanup item rebases on both. Astro items call the privacy check unchanged (G-h). |
| `comment-cleanup-operational-tooling` | Owns other `scripts/` files the migration uses, such as `scripts/explorer_publish.py` and `scripts/ruleset_drift_check.py`. Astro items call them unchanged except for drift-guard pins that G-e requires; the general ordering rule applies. |
| `comment-cleanup-unit-tests-cli-scripts-rest` | Owns `tests/unit/scripts/test_assemble_public_site.py` and `tests/unit/workflows/test_release_isolation.py`, which `astro-site-52` and `54` change. The general ordering rule applies. |
| `comment-cleanup-shared-infrastructure` | Owns, for comment removal, every path the scope file assigns it, including `.github/**`, `make/**`, `Makefile`, `pyproject.toml`, `.gitignore` and `AGENTS.md`. `astro-site-20`, `51` and `52` add jobs and targets there. The general ordering rule applies. |
| `dlv2-61-retire-docs-yml-pages-fallback` | May move linkcheck, spellcheck and example validation out of `docs.yml`. It may delete the release-to-Pages fallback only after dlv2-62. The last Sphinx artifact stays redeployable until `astro-site-54` closes. |
| `dlv2-60`, `dlv2-62` | Own `site/routes.yml`, the site-deploy workflow and receipts. Today `/site/` is gitignored and rebuilt by assembly; dlv2-60 resolves that collision. `astro-site-52` adds the `website/` route builder and the mixed-renderer refusal to that plane. It does not create a second deploy path. |
| `ci-dedupe-11-required-public-site-visual-check` | Owns the visual acceptance check. `astro-site-03` documents the two-slot approval procedure; `astro-site-51` switches captures to Astro output at cutover. Neither weakens the check. |

### Visual-change procedure: decided

On 2026-10-03 the public-site visual comparison became advisory until the
public site is in production ([merge-queue governance](../../docs/operations/merge-queue-governance.md)).
The comparison still evaluates the exact-head approval slots.

- **Runbook.** It covers both phases: an advisory review recorded in the PR
  now, and the two exact-head approval slots once the check is required again.
  It lives in the [visual check runbook](../../docs/development/results-explorer-browser-testing.md).
- **Rehearsal (owner decision, 2026-10-03).** The runbook's merge closes
  `astro-site-03`. The two-slot rehearsal moves to `astro-site-21`'s PR, because
  adopting the shared tokens restyles the landing page, which is a genuine
  intentional visual change. The owner sets the four slot variables for that
  PR's head and merge group, since setting them needs repository
  administration. The run ids are recorded in this section when it merges.

### Site inventory: decided

`scripts/site_inventory.py` and its baseline under
`inventory/baseline-develop/` in `BenchBox-dev/benchbox-site` are the parity oracle for
`astro-site-50`.

- **`diff`** fails on regressions against the baseline: missing paths, missing
  fragments, newly broken links, feed id or link changes and canonical loss.
  Paths and fragments that are meant to go are listed with reasons in a
  reviewed expected-removals file.
- **`check`** fails on every broken link in one inventory. The links already
  broken on develop are listed in `known-broken-links.json`, so
  `make site-inventory-check` fails only on new ones.
- **G-c obligation.** `astro-site-50` must drive the known-broken list to empty.
  That list includes the Atom feed, whose entry links point at
  `/blog/blog/<post>.html`. Fixing the links must keep the entry ids unchanged
  (D8).
- **Diff work owed by `astro-site-50`.**
  - Expected removals must support fragment-only patterns, or theme ids must be
    recorded apart from content ids. Otherwise the 48 Furo theme ids on every
    page cannot be allowed without hiding real losses.
  - Feed comparison must apply allowances, and must compare only entries
    present in both feeds, so that fixing the feed links and the rolling
    10-entry window do not fail the diff.

## Global guardrails

Copied verbatim from tracker item `astro-site-00-decisions-adr`.

G-a No change to production deploy wiring until dlv2-62 is done; the site plugs into site-deploy only.
G-b docs/ is the single content source; both renderers build it until cutover.
G-c Zero broken public URLs or fragments. Redirect pages are listed and reviewed (D3).
G-d Explorer behaviour is frozen during the migration. Its vitest, jest-axe and Playwright suites stay green; only shell, path and config edits are allowed.
G-e Required checks are never weakened. A new or swapped required context lands with its drift guard in the same PR. Any PR that changes rendered output uses the visual-change procedure (astro-site-03): separate exact-head approvals for the PR head and the merge_group head_sha. A PR approval never carries over to the merge group.
G-f COMMENT-POLICY-001 applies to TS, Astro and CSS. Removed prose is never archived into strings or metadata.
G-g No new third-party runtime scripts, analytics or CDN assets; fonts are self-hosted or system. Prism moves off cdnjs.
G-h npm audit:high clean; exact lockfile; check_artifact_privacy.py passes on every assembled artifact.
G-i One PR per item to develop through the merge queue; no stacked PRs.

G-j Do not commit screenshots or generated binaries.
G-k Production never serves a mixed-renderer artifact (D13).
ABANDON if spike gate G1 fails, or if the reviewed URL compatibility report (astro-site-50) cannot reach zero broken public URLs and fragments.

### Amendment to G-i

Owner instruction, 2026-10-03: put as much work as possible into each PR. One
PR may carry several items. Each item's tracker entry is finished once the PR
that carries it merges. PRs still target develop through the merge queue, and
none are stacked.

## Standing approvals

Copied verbatim from tracker item `astro-site-00-decisions-adr`.

S1 Gates 00, 02, 03, G1 (10), G2 (50), G3 (53), the D14 overlap agreements, and intentional visual-change approvals (both slots) are approved by the coordinating agent session once the item's written criteria pass AND an independent reviewer subagent that did not author the work has adversarially reviewed the evidence with no unresolved Critical/High finding. Record the evidence and approver session in the ADR/decision file. Starting items 60 and 61 is covered by the same rule.
S2 The dlv2 prerequisite chain (dlv2-f0, dlv2-f3, dlv2-60, dlv2-62, dlv2-61) is in scope for the same session, under the dlv2 ADR's standing approvals. A live claim held by another worker is never taken over unless the takeover rules are met.
S3 Release: the agent prepares everything and runs the TestPyPI rehearsal. The real PyPI release is triggered by the owner. While waiting, the agent works other ready items and schedules wake-ups; waiting is not a stop.
S4 G1 NO-GO: record it in the spike report, drop astro-site-20 through astro-site-61 with that reason, keep 01-03, and end with a summary.

## Stop conditions

Abandon the migration if G1 fails (S4), or if the reviewed URL compatibility
report cannot reach zero broken public URLs and fragments. Pause, without
abandoning, for an owner-only action outside S1–S4, a denied permission,
live-cloud spend, a HOLD or unresolved Critical/High review, or a production
probe failure that rollback does not fix.

## Approval

- Owner: Joe Harris, standing approvals S1–S4, 2026-10-03.
- Approver: coordinating session
  <https://claude.ai/code/session_01GZSaWtTiCz9etgrhYRuVzu>, 2026-10-03.
- Independent review: a separate reviewer subagent that did not write this ADR,
  2026-10-03. Round 1 found 2 High, 6 Medium and 4 Low issues. All were fixed in
  this file. Round 2: PASS, no Critical or High findings. Its five Medium and four Low findings were fixed in this file.
- D14 agreements: approved under S1 by the approver session above, for every
  row of the D14 table.
