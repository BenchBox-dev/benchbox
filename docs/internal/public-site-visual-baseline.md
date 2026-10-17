# Public-site visual baseline

This page holds the maintainer procedure for the public-site visual comparison
that runs in CI. The Explorer's own browser suite is described in
`docs/development/results-explorer-browser-testing.md`.

The public-site visual comparison is advisory until the public site is in
production and feeds no required status context.

The full public-site visual suite is broader than the Explorer's current
functional gate. Raw screenshots stay out of Git, protected `develop` produces SHA-bound
baseline artifacts, and pull requests compare against the exact base-SHA
artifact. Missing or unverifiable baselines fail closed; a PR diagnostic
artifact is never promoted directly to a baseline. The capture harness at `results-explorer/e2e/captures/public-site-pages.spec.ts`
and Pages-shaped server are reusable building blocks. `.github/workflows/docs.yml`
uploads the protected baseline from `develop` and retrieves the exact
base-SHA artifact for pull requests. A changed public-site
tree cannot pass without that comparison. `Public-site visual acceptance`
reports on every develop PR; it skips the build only when
the former documentation input paths are unaffected. Pull requests keep a
short retry when downloading the baseline.

If a protected `develop` push was dropped or its baseline expired, dispatch
Documentation on `develop` with `baseline_source_sha` set to the exact base
SHA shown by the failing PR:

```bash
gh workflow run docs.yml --ref develop -f baseline_source_sha=<full-protected-develop-base-sha>
```

The dispatch validates that the SHA is an ancestor of protected `develop`,
checks out that exact tree, and uploads a SHA-named artifact. The downloader
verifies the protected run and manifest source SHA. Wait for its visual job to
finish, then rerun the failed workflow. A PR diagnostic artifact cannot serve
as recovery input.

An intentional visual change or route/viewport addition needs explicit
maintainer acceptance. After reviewing the PR's `public-site-visual-diagnostics-*`
artifact, set the repository variable `APPROVED_HEAD_SHA` to the PR's complete
head SHA and set `APPROVAL_REASON` to a nonempty review note, then rerun the
failed workflow. The approval applies only when both values are present and the
approved SHA exactly equals GitHub's current PR head SHA. It may accept changed
digests and unexpected new captures, but it never accepts a capture missing
from the current matrix. Clear both variables after the approved run so only
one reviewed head occupies the repository-wide approval slot.

The PR-head slot is the only approval slot. `site-docs.yml` triggers only on
`pull_request`, and its compare step reads only `APPROVED_HEAD_SHA`,
`APPROVAL_REASON` and the PR head SHA.

This approval does not replace a baseline. The protected `develop` push or its
validated `workflow_dispatch` run uploads the SHA-bound baseline after the
reviewed PR merges. PR diagnostic artifacts remain short-lived and non-promotable.

### Renderer-switch pull request

The Astro build is captured ahead of the switch by the advisory
`Capture public site from the Astro build` step of the `site-build` job in
`site-docs.yml`, locally by `make site-build site-visual-capture`. It captures
the same route and viewport matrix from `website/dist` with
`PUBLIC_SITE_VISUAL_RENDERER=astro`, uploads
`public-site-visual-astro-<run id>`, and never compares or replaces a baseline.
On a pull request the capture records the merge commit it built
(`github.sha`), not the head commit.
Each manifest records its `renderer`. If a route or viewport changes, update
the matrix in `public-site-pages.spec.ts` in the same change.

When the baseline and the current capture come from different renderers, the
comparison reports every capture as changed and passes only through the
exact-head approval described above. A missing capture still fails. The
comparison is never skipped, disabled or loosened.

**What the switch pull request changes.**

1. `docs.yml`: the `build` job produces the Astro site instead of assembling
   the Sphinx site (the `Assemble public site` step and the upload named
   `Upload assembled site for visual acceptance`), and the
   `Capture public site` step of `public-site-visual-regression` sets
   `PUBLIC_SITE_VISUAL_RENDERER: astro`. The push run on `develop` then
   publishes an Astro baseline for the merge commit.
2. `site-docs.yml`: the `docs-build` job and the `Capture public site` step of the
   `Public-site visual regression` job make the same change, so the pull
   request captures the Astro build against the Sphinx baseline of its exact
   base.
3. `public-site-pages.spec.ts`: the guard that restricts
   `PUBLIC_SITE_VISUAL_RENDERER=astro` to the capture phase stays in place. The
   workflows' compare step leaves `PUBLIC_SITE_VISUAL_RENDERER` unset, so it
   defaults to `sphinx` and the astro renderer reaches the comparison through
   the captured manifest. The guard therefore blocks only a local single-pass
   or compare run with `PUBLIC_SITE_VISUAL_RENDERER=astro`; lift it only for
   that local use, after the renderer cutover.

**Approval and baseline.** The decision record requires an exact-head approval
for the PR head, with queue position never substituting for it. Develop has no
merge queue, so there is no second slot.

1. PR head. Review the PR run's `public-site-visual-diagnostics-*` artifact
   against the Sphinx baseline. Set `APPROVED_HEAD_SHA` to the PR head SHA and
   `APPROVAL_REASON` to the review note, then re-run the failed job.
2. After the merge, the `Documentation` push run on `develop` must capture the
   Astro build and upload `public-site-visual-baseline-<merge commit>`. Confirm
   the artifact exists and that its `manifest.json` has `"renderer": "astro"`.
   If the push was dropped, dispatch Documentation with `baseline_source_sha`
   set to the merge commit.
3. Clear the approval variables. Hold other site-changing PRs until step 2
   is confirmed, because they need the Astro baseline for their exact base.

## What CI gates

See `docs/development/results-explorer-browser-testing.md` for the CI jobs that gate the Explorer.
