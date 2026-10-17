# Site deploy runbook (retired)

> **Retired.** `.github/workflows/site-deploy.yml` ("Site Deploy") has been
> disabled in GitHub since 2026-10-11. benchbox.dev is deployed by
> `.github/workflows/deploy.yml` in `BenchBox-dev/benchbox-site`. For deploys, rollback and the deploy
> target variable, see the "Deploys" section of that repository's `AGENTS.md`.
> Core feeds the deploy through the `site-inputs-<sha>` bundle that
> `site-inputs.yml` builds and sends as a `core-bundle` dispatch. Changes to
> `results-explorer/`, `website/`, `landing/`, `docs/blog/`, `_blog/` and
> `_project/design/site-inventory/` reach the site only when replayed there by
> pull request; see "Replaying core changes" in the same `AGENTS.md`.
>
> The sections below describe the retired Python deploy plane. Core's workflow
> tests still read this page, so it stays until the workflow,
> `scripts/site_deploy/` and those tests are deleted together. Do not follow
> it to deploy.

## Routes

`deploy/routes.yml` is the route manifest. `/`, `/docs/`, `/docs/dev/`,
`/blog/` and `/results/` (Explorer UI plus DuckDB snapshot) come from the
trunk candidate. The `release` ref stays declared because the renderer
selection reads the newest `v<major>.<minor>.<patch>` tag's
readiness. `/_static` and
`/_images` belong to the blog route because blog pages reference them with
root-relative paths; docs pages use their own `_static`, so the two Sphinx
builds never collide. The assembler refuses any path claimed twice. The corpus
SHA in a receipt is the git tree hash of `results-data/` at the trunk SHA.

## Renderer

One renderer builds every route of an artifact. `deploy/routes.yml` names the
policy:

- `renderer: sphinx` holds every route on Sphinx whatever the release tag
  contains. It is the rollback policy.
- `renderer: auto`, the committed value, selects the renderer from the tree of
  the newest release tag's commit, never from the working tree. It selects Astro
  when that tree contains every one of `website/package.json`,
  `website/package-lock.json`, `website/astro.config.ts`,
  `website/src/converter/cli.ts`, `website/src/pages/index.astro`,
  `website/src/pages/404.astro`, `website/src/pages/blog.astro`, at least one
  file under each of `website/src/pages/docs/`, `website/src/pages/blog/` and
  `website/src/pages/prompts/`, a `site-build:` target in
  `make/documentation.mk`, and no `.rst` file under `docs/` (every page has been
  migrated to MyST). This is what "the release tag contains `website/` and all
  migrated content" means. Any tag that misses one item selects Sphinx.

No policy forces Astro. `scripts/site_deploy/renderer.py` holds the list, and the
resolve step writes the selection and its reasons to `resolved.json`.
`test_the_committed_policy_selects_astro_only_for_a_ready_release_tag` shows
that the committed policy keeps Sphinx for a tag that is not ready and selects
Astro for a ready one, and
`test_auto_selects_astro_only_from_a_ready_release_tag` covers `auto` on
synthetic release trees.

While the selection is Sphinx, `/docs/dev/`, `/blog/` and the root files come
from a Sphinx build of trunk, so trunk must keep building with Sphinx until the
switch has been deployed.

Trunk routes are built with the same renderer, so when `auto` selects Astro from
the release tag, the resolve step also checks the trunk commit it deploys against
the same list. A ready release with a trunk that is not ready fails the resolve
step (`the release tree is ready for astro but trunk <sha> is not`) rather than
publishing Astro release routes beside Sphinx trunk routes, and
`assemble_public_site.py --routes` applies the same check to its trunk ref root.
Restore trunk's readiness or set `renderer: sphinx`.

### Cutover

1. Cut a release from a tree that meets the list above. Under `renderer: sphinx`
   this deploys as Sphinx, like any other release. That deploy is a
   precondition, not a formality: the first Astro deploy compares against the
   deployed generation's artifact, so a receipted Sphinx generation must be live
   before the switch. Without one, the pre-deploy visual comparison fails (`no
   receipted production generation to compare the candidate with; deploy and
   receipt a Sphinx generation before switching deploy/routes.yml to renderer:
   auto`) and the Astro deploy cannot publish. Confirm the newest `github-pages`
   deployment has a receipt before step 3. Trunk must also still meet the list
   at the commit that will be deployed.
2. Record the parity sign-off for that release (URL compatibility report and
   reviewed visual changes) in the cutover pull request.
3. The owner's cutover pull request changes `renderer: sphinx` to
   `renderer: auto` in `deploy/routes.yml`. Merge it only after step 2.
4. Dispatch `mode=preview`, then `mode=deploy`. The resolve step selects Astro,
   the pre-deploy visual comparison runs and fails until its approval is set
   (see below), and the deploy publishes every route with Astro in one run.
5. To return to Sphinx, roll back with the `full` phase to a Sphinx receipt and
   set `renderer: sphinx` again before the next forward deploy.

### Astro layout

With Astro, the trunk ref runs `make site-build`, which needs the ref's own
`results-explorer/dist`. `/` (with `/prompts/`, `/pagefind/` and the sitemap),
`/docs/` including the API reference, `/docs/dev/`, `/blog/` and `/_images/`
(the blog's images) come from trunk's `website/dist`; `/results/` is trunk's
Explorer build and snapshot, and the root `404.html`, `CNAME` and `.nojekyll`
come from trunk. Each docs and blog route
mounts the content-hashed assets of its own build under `/_astro/`: a file both
builds produce byte for byte is kept once, and the same path with different
bytes fails the assembly.

Trunk's docs are built for `/docs/`, so assembly rewrites their root-relative
and `https://benchbox.dev` URLs that start with `/docs/` in `href`, `src`,
`srcset`, `action`, `poster`, `content` and `data-*` attributes to start with
`/docs/dev/`. Sidebar, header and in-page links, images, downloads, canonical
URLs and redirect pages therefore stay inside `/docs/dev/`; prose and
scripts are not rewritten. The search box on every page loads the release
tag's `/pagefind/` index, so search results always point to the release pages.

Assembly refuses a mixed-renderer artifact. Each ref's stage must be the
selected renderer's output (an Astro stage must contain `_astro/`). Outside
`/results/`, a tree carries Astro output when any directory at any depth is
named `_astro`, and Sphinx output when any `_static/documentation_options.js`
exists. Every HTML page is classified by its markers: a Sphinx page loads
`_static/documentation_options.js`, an Astro page loads `/_astro/` assets or
carries Astro's generator meta. A page with neither marker is unattributed. A
Sphinx artifact accepts unattributed pages, because its landing pages, redirect
stubs, downloads and root `404.html` carry no Sphinx marker. An Astro artifact
refuses them, because every page of the Astro build carries one of its markers,
so an unmarked page is output the build did not produce. A page or asset of the
other renderer, or an unattributed page in an Astro artifact, fails the
assembly and removes the partial tree; the `mixed_version` gate repeats the
check on the final tree in deploy, preview and rollback runs. `assemble_public_site.py
--renderer` must equal the selection it recomputes from the release checkout.
A `ui-first` rollback across renderers is refused, because it would put one
renderer's root `404.html` into the other's artifact; use the `full` phase.

Each receipt records `renderer` at the top and on every route, and the Explorer
block adds `pins`: the UI digest (the Explorer tree without `data/`) with its
trunk SHA, and the snapshot's sha256 with the corpus SHA. An Explorer tree
without `data/results.duckdb` fails the assembly, and a `ui-first` rollback pins
the snapshot that the composed tree actually serves.

## Visual comparison before deploy

Route and digest probes prove that the served bytes are the artifact's bytes,
not that the layout is right. Trunk-sourced pages (`/docs/dev/`, `/blog/`,
`/results/`) are compared on every render change by the `Public-site visual
regression` job in `site-docs.yml`. Release-sourced pages are compared at
deploy time by the `Pre-deploy visual comparison` job of this workflow. It runs when the resolve
step reports `visual_required`: the candidate's release commit or renderer
differs from the deployed receipt's, or a first deploy would publish Astro. It is
skipped for rollback, which restores an artifact that was compared when it was
first deployed.

The job fetches the deployed generation's artifact through `fetch-run
--verify-tree` (digest checked against its receipt), captures it and the
candidate with `results-explorer/e2e/captures/public-site-pages.spec.ts`
restricted by `PUBLIC_SITE_VISUAL_ROUTES` to the release-sourced captures
(`landing` and `getting-started`), and compares the manifests with
`compareVisualManifestsAcrossRenderers`. A missing capture always fails. A
renderer change reports every capture as changed; with the same renderer, any
changed capture fails until approved. The deploy job waits for this job and
publishes only when it passed, or when it was skipped because it was not
required. A deploy with no receipted production generation cannot pass it.

The comparison is deliberately narrow. It covers two captures of release-sourced
pages: `landing` (`/`) and `getting-started`
(`/docs/usage/getting-started.html`). The other public-site captures
(`release-overview`, one `/blog/` post, and `results`, `results-benchmarks` and
`results-platforms` on `/results/`) are trunk-sourced, and the `Public-site
visual regression` job in `site-docs.yml` covers them on every pull request that
changes rendering, before it merges to trunk. No
other page is compared before deploy: the layout of the remaining `/docs/`
pages, the API reference, `/docs/dev/`, other blog posts, `/prompts/` and the
Explorer views beyond those captures relies on the build, link, route and
digest gates and on the parity sign-off recorded for the cutover release.

Approval follows the exact-match rule of
[the visual-change runbook](../internal/public-site-visual-baseline.md),
bound to what was compared. The job verifies both trees against their digests and
writes `visual-binding.json` into the `site-deploy-visual-<run_id>-<attempt>`
artifact, with the binding `<release_sha>+<candidate artifact sha256>+<baseline
artifact sha256>`. After reviewing the captures, set the repository variable
`SITE_DEPLOY_VISUAL_APPROVED_BINDING` to that exact binding and
`SITE_DEPLOY_VISUAL_APPROVAL_REASON` to the review note, re-run the failed jobs,
and clear both afterwards. A binding recorded for another release, candidate
artifact or production baseline does not apply. The pull request slot
(`APPROVED_HEAD_SHA`) is never read here.

## Candidate and generation

The merge queue is retired on `develop`, so `ci.yml` no longer certifies a trunk
commit. The candidate is the newest first-parent commit of `develop` that has a
successful `push` run of `.github/workflows/trunk.yml` on `develop` with that
exact `head_sha`; the run conclusion decides, and the lookup is
`actions/workflows/trunk.yml/runs?event=push&branch=develop&head_sha=<sha>&status=success`.
API errors, an unreadable response or a result too long to page fail the run
closed.

- The walk covers the newest 50 first-parent commits. If none of them is
  certified the run fails; it never falls back to an older or uncertified commit.
- Trunk runs on every push to `develop` with `cancel-in-progress: false`, but
  GitHub keeps only one pending run per group, so a burst of merges can leave
  intermediate commits without a run. Those commits are uncertified and the walk
  skips them; the candidate is then an older certified commit until the burst's
  last commit is certified.
- The build checks out the candidate, but the deploy control plane (this
  workflow, `deploy/`, `scripts/site_deploy/`, the assembler, the publication
  gate tools and the site inventory) must match the dispatched commit. If an
  older candidate's copy differs, the run fails; dispatch again once trunk has
  certified the newer commit.
- Trunk does not build the docs or the Explorer. Site-deploy's own build and
  gates (privacy, Explorer compatibility, mixed versions, corpus bijection,
  digest, validator parity, links) are what cover those artifacts.

The deployed generation is the newest successful `github-pages` deployment status
whose description reads `site-deploy receipt sha256:<digest> run:<id>`, plus the
retained `site-deploy-receipt-<id>-<attempt>` artifact whose bytes hash to that
digest. Deploy and preview refuse a candidate whose trunk SHA is a strict ancestor
of the deployed one, whose release tag is older, or that diverges. An identical
candidate is a no-op. The deploy job checks the generation again after approval
and before publishing.

If a newer successful deployment has no site-deploy receipt (for example a legacy
writer deployed after the last site-deploy run), the run fails closed. Setting
`bootstrap` narrows the guard instead of disabling it: the scan continues to the
newest deployment that does carry a receipt, orders the candidate against that
generation, and carries its generation number forward (the new receipt's parent
records `newer_unreceipted: true`). Only when no scanned deployment carries a
receipt at all is the run a first deploy at generation 1.

Limits. Deployment history is read through the newest 100 `github-pages`
deployments (receipts, rollback targets and the bootstrap scan all use the same
window), so a receipt older than that is not found; a bootstrap run that finds
none treats the site as a first deploy. Receipts and artifacts are retained for
90 days; after that a generation can no longer be read or restored, and the next
run fails closed until `bootstrap` is set.

## Deploy

1. Dispatch from `develop`: `gh workflow run site-deploy.yml --ref develop -f mode=preview`
   first, then `-f mode=deploy` (add `-f bootstrap=true` only for generation 1).
2. Read the gates before approving:

   ```bash
   gh run download <run_id> -n site-deploy-build-<run_id>-<attempt> -D /tmp/site-deploy-<run_id>
   jq '{ok, results: (.results | map_values(.status))}' /tmp/site-deploy-<run_id>/gates.json
   ```

   Approve only when `ok` is true. The gates are privacy, explorer compatibility,
   mixed UI and snapshot version, corpus bijection (against the trunk SHA and
   `publication/ledger-seed.json`), snapshot digest against the deployed snapshot,
   validator parity from the deployed trunk, and the cross-route link check.
   Trunk ownership for the link check is derived from `deploy/routes.yml`: every
   path under a route whose ref is a trunk ref, plus the root files, is trunk
   owned. A new broken link whose source or target is trunk owned fails unless it
   is listed in `_project/design/site-inventory/known-broken-links.json`. An entry
   that no longer matches a broken link fails the `site-parity` CI check
   (`site_inventory.py check --fail-on-stale`), so the change that fixes a link
   also removes its entry; this deploy check only reports such entries. Broken
   links among release-tag pages are tolerated only if they belong to a per-tag
   baseline set: the sorted list of release-owned broken links recorded in the
   last deploy's receipt (`link_baseline.links`) for the same release tag, or,
   when the receipt records none for that tag, the release-owned entries of the
   allowance file. Any release-owned broken link outside that set fails, so
   swapping fixed links for new ones fails, while a pure reduction passes and
   becomes the next baseline. A newer tag that adds broken links therefore fails
   until the allowance file is updated.

   When a release tag has no receipt baseline yet, the gate also reads
   `_project/design/site-inventory/release-known-broken/<tag>.json`, a reviewed
   list of `[source, target, reason]` entries with paths as served on the routed
   site (for example `/docs/...`), and adds it to the release-owned develop
   entries for that tag only. Add a file when a new release tag's pages break
   links that develop no longer lists; list exactly the release-owned broken
   links the gate reports for that tag, never trunk-owned ones.
3. Approve the pending deployment with the reviewer's own credentials, never with
   the workflow token:

   ```bash
   gh api repos/BenchBox-dev/BenchBox/actions/runs/<run_id>/pending_deployments
   gh api -X POST repos/BenchBox-dev/BenchBox/actions/runs/<run_id>/pending_deployments \
     -F 'environment_ids[]=<github-pages environment id>' \
     -f state=approved -f comment='gates.json reviewed'
   ```

   The token needs `repo` (classic) or Actions and Deployments read access, and
   its user must be a required reviewer of `github-pages`.
4. The `probe` job requests every manifest route from `https://benchbox.dev`,
   compares served bytes with the artifact checksums and writes the
   `site-deploy-receipt/v1` receipt. It uploads `site-deploy-receipt-<run_id>-<attempt>`
   for 90 days first, and only after that upload succeeded does a later step
   record the receipt's sha256 as a status on the deployment, so a recorded digest
   always has a retained receipt behind it. The deployment is the `github-pages`
   deployment whose status links to this run id, not one matched by start time.
   All artifact names carry the run attempt, and consumers read the producing
   job's outputs, so "Re-run failed jobs" of the same run works: the deploy job is
   not repeated, the probe job probes again, uploads a new attempt's receipt and
   records the newest digest. Do not start a new deploy until a receipt exists.

## Receipts

A receipt holds the run and deployment ids, the link-check baseline, per-route source SHA, corpus SHA,
tree sha256 of the published artifact, UI and snapshot read-model versions, gate
results, probe results, and the parent generation. A receipt is last-known-good
only when its gates and probes both passed.

Each route also records `lane_sha256`, the digest of every tree it contributes to the
artifact, and the receipt carries an `explorer` block with the Results Explorer's
digest (`sha256`), source SHA and corpus. The Explorer is pinned by that digest on
its own, apart from the whole-site `artifact.sha256`, so a change to prose never
reads as an Explorer change. The site build copies
`results-explorer/dist` byte for byte into `/results/` and fails if the mounted tree's
digest differs from the source's; it never rebuilds the Explorer source. Digests are
computed with the assembler's algorithm, and a symlink in the tree is a hard error in
the Node digest.

## Rollback

`gh workflow run site-deploy.yml --ref develop -f mode=rollback -f rollback_receipt_run_id=<run_id>`

The target receipt must be recorded on a `github-pages` deployment. The run
downloads the receipt matching that recorded digest and the retained artifact
named in it (`gh run download` is wrapped by `site_deploy fetch-run`), re-hashes
the tree against the receipt, checks the receipt digest equals the one resolved
earlier, requires last-known-good, re-uploads it as the Pages artifact, deploys
after approval, and probes. Approve it as above.

The Explorer UI must never meet an older snapshot, so restore the UI first: when
the full restore would pair the current UI with an older snapshot, the gates
fail and tell you to dispatch `-f rollback_phase=ui-first` first (restored UI
over the current snapshot), then the same dispatch with `rollback_phase=full`.
The ui-first phase also re-hashes the current generation's artifact against the
digest in the current receipt before composing it, and its receipt keeps the
current generation's route records except `/results/`, which takes the restored
one. It also restores the root `404.html`, the Results deep-link fallback that
the restored UI reads.

### Newest deployment has no receipt

When a legacy writer deployed after the last site-deploy run, the strict rollback
refuses because the current generation cannot be identified. Dispatch with
`-f current_generation_unknown=true` (still with a `rollback_receipt_run_id`
whose receipt is recorded on a `github-pages` deployment). The run then:

- downloads the live snapshot `https://benchbox.dev/results/data/results.duckdb`
  in the build job and reads its read-model version;
- uses that version for both the current snapshot and, as an upper bound, the
  current UI. This is conservative only if the live site satisfies UI <= snapshot
  (the invariant every site-deploy generation is gated on); a legacy deployment
  that violated it would make the bound too low, so check the live site first;
- supports only `rollback_phase=full`, because ui-first needs the current
  artifact; if the gate says the UI must be restored first, deploy forward
  (with `bootstrap`) before rolling back;
- records an unknown parent in the receipt (`parent.unknown: true`, the generation
  carried from the newest receipt-bearing deployment, or the target's when none is
  readable, and the live versions used);
- after approval, refuses if the newest `github-pages` deployment other than this
  run's own changed since resolution.

## Preview

`mode=preview` builds and gates the candidate exactly as a deploy does but has no
environment and no write scope. It serves the artifact from a local directory with
the Pages `404.html` fallback, probes it, and runs the drill: publish the single-ref
assembly of the same trunk SHA (A), publish the route build (B), roll back to A by
receipt, probe, and assert the tree digest equals A. The `site-deploy-preview-<run_id>-<attempt>`
artifact holds `preview-drill.json`, `parity.json` (route and byte differences against
the single-ref assembly), `production-parity.json` (informational comparison with the
live site), the drill receipts, and `gates.json`. Differences from the single-ref
assembly are expected only where a route changes source: `/docs/dev/` is new,
and `/results/` carries the trunk Explorer build and snapshot.

## Local rehearsal

Build both refs, then run `scripts/assemble_public_site.py --routes deploy/routes.yml
--ref-root release=<tag checkout> --ref-root trunk=<trunk checkout> --site-dir
<scratch>/site-build --receipt-out <scratch>/route-assembly.json`. Routes
mode refuses `--site-dir site`, which stays the output of the single-ref assembly.
