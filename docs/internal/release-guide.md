# Cutting a BenchBox release

BenchBox releases follow a **version-branch flow** on a single repo
(`BenchBox-dev/BenchBox`) with two long-lived branches: `develop` (dev work)
and `release` (release-only). This guide is the maintainer runbook.

## The flow

```bash
make worktree-create BRANCH=chore/cut-next-release WORKTREE_PATH=../BenchBox.wt-cut-next-release
cd ../BenchBox.wt-cut-next-release
make agent-write-preflight
make release-cut VERSION=X.Y.Z
make release-finalize VERSION=X.Y.Z
```

Between `release-cut` and `release-finalize`, review the PR and wait for
`validate-base` and `release-required-result`.

Choose an unused version and resolve any existing `vX.Y.Z` branch or tag before
starting. `release-cut` fetches `origin` and accepts a new cut only from a clean
linked worktree whose HEAD is exactly the fetched `origin/develop` commit. An
ahead, behind, or dirty checkout stops before any version file changes. The
two release Make targets handle the cut and finalization. Wheel install,
release canary, and correctness remain the blocking gates; UAT is a
non-blocking matrix campaign.

This flow releases the Python package. It does not publish `benchbox.dev`: the site is built and
deployed from `BenchBox-dev/benchbox-site` by its `.github/workflows/deploy.yml`.

## Pre-merge release-required contract

Release PRs target `release` and must be opened from branches accepted by
`.github/workflows/validate-release-pr.yml` (`vX.Y.Z`, optionally with a suffix).
Before a release PR can merge, the `release-only` ruleset must require:

- `validate-base`
- `release-required-result`

`release-required-result` is the stable umbrella check in
`.github/workflows/test.yml`. A green result means the release PR branch passed:

- the required fast lane, `test (ubuntu-latest, 3.12)`;
- the bounded real-result correctness gate, `make test-correctness-gate`
  (`DuckDB x TPC-H` at SF=1 with `qgen -d` default substitution parameters, through
  generate/load/execute with EXACT stored answer-set row-count validation of the
  18 answer-stable TPC-H queries; Q11/Q16/Q18/Q20 are excluded for answer-set
  boundary sensitivity, and validation is cardinality-level, not value-level).
  The stored digests are Linux-generated, so on Apple silicon with macOS 26 or newer run
  `make ci-linux` for parity rather than reading a local mismatch as a defect;
- the credential-free integration-not-slow suite:
  `tests/integration -m "integration and not (slow or stress or resource_heavy or live_integration)"`;
- the complete medium tier, `medium (shard 0)` through `medium (shard 3)`, with
  the same selection as the trunk medium shards;
- isolated exact-one-wheel package build/install smoke;
- dependency upper-bound checks;
- release-branch curation checks that confirm dev-only paths are absent.

It does **not** rerun the full stress matrix, live cloud integrations, or
long-running UAT on the release PR itself. Slow/resource-heavy coverage is
enforced through the freshness-based release canary below. Wheel install,
canary, and correctness gates remain blocking; long-running UAT is a
non-blocking matrix campaign.

## Release canary and ruleset drift

Release PRs also depend on the `validate-base` workflow's release-readiness
steps. That workflow queries the latest completed `release-canary.yml` run,
reads the canary summary artifact, and fails the release PR when the canary is
missing, red, older than 48 hours, or when the tested `develop` SHA recorded in
the artifact is not an ancestor of the release PR head. The only bootstrap
exception is the first release that introduces `release-canary.yml` before the
workflow exists on the default branch; in that case `validate-base` runs the
same non-fast canary suite and ruleset drift check inline, then later releases
return to the scheduled/manual canary evidence path.

`release-canary.yml` runs daily and on manual dispatch. Scheduled runs execute
from the default branch, but the workflow checks out `develop` before running
release evidence and records that checked SHA in `release-canary-summary.json`.
Its blocking canary suite is the credential-free non-fast family:
`(slow or resource_heavy) and not (stress or live_integration)`. The same
workflow also runs `scripts/ruleset_drift_check.py` against
`docs/internal/repo-admin-settings.md`, so ruleset drift makes the canary red
instead of silently invalidating release assumptions. The ruleset drift check
must use `RULESET_DRIFT_TOKEN`, a repository secret with enough ruleset
visibility to expose bypass actors; the default `GITHUB_TOKEN` is insufficient
for that part of the contract.

`validate-base` checks out the release PR's base commit and runs
`scripts/release_readiness_check.py` from that trusted tree, so the readiness
gate that evaluates a release is the copy already on `release`, not the copy on
the release head. A change to the readiness gate therefore first takes effect
on the release *after* the one that ships it. Plan gate hardening accordingly:
it does not protect its own release.

Stress tests, live cloud integrations, and long-running UAT remain advisory
until their credential, cost, and flake policies are stable enough to make them
release-blocking.

## UAT matrix campaign evidence (advisory)

`scripts/release_readiness_check.py` reports committed UAT evidence when it
exists. A green verdict, clean source tree, ancestor source SHA, and recent
completion remain useful campaign-quality signals, but missing, red, stale,
non-ancestor, or dirty evidence does not fail `validate-base`. Producing the
three-stage evidence is not a release-cut precondition.

Running the optional campaign (run stage 2 after stage 1 completes, and stage 3
after stage 2 completes):

```bash
make uat-sweep CONFIG=tests/uat/configs/release-gate-01-native-dataframe.yaml
make uat-sweep CONFIG=tests/uat/configs/release-gate-02-docker-nonoltp.yaml
make uat-sweep CONFIG=tests/uat/configs/release-gate-03-docker-oltp.yaml
make uat-gate-check STAGE1=<run-dir> STAGE2=<run-dir> STAGE3=<run-dir>
```

Review the campaign report. It is historical evidence, not a release input.
See `docs/internal/uat-release-campaign.md` for campaign
ordering and its report checklist. The emergency override below applies only
to the blocking canary check.

Emergency override is admin-only: set repository variables
`RELEASE_READINESS_OVERRIDE_SHA` to the exact release PR head SHA and
`RELEASE_READINESS_OVERRIDE_REASON` to the incident/approval record. Remove
both variables after the release. API outages, stale canaries, or ruleset drift
must not be bypassed with an undocumented local change.

### What `release-cut` does

1. Cuts a `vX.Y.Z` branch from the exact fetched `origin/develop` commit in a
   linked worktree (`develop` itself is never modified).
2. Bumps the 6 version sources via `scripts/update_version.py`. The
   `## [X.Y.Z]` changelog section is not built here: `make release-prep` builds
   it from the `changelog.d` fragments on `develop` before the cut (see
   "Release notes from fragments" below).
3. Gates on `generate_changelog_entry.py --require-prepared`, which refuses
   unless `CHANGELOG.md` has the `## [X.Y.Z]` section and no `changelog.d/*.json`
   fragment is pending, then on `generate_changelog_entry.py --check-curation`,
   which inspects the section's text: it rejects verbatim commit subjects
   (trailing `(#NNNN)`), duplicate `###` headings, and more than 45 bullets or
   160 lines. A cut from a tree without a prepared section stops here: run
   `make release-prep VERSION=X.Y.Z` on `develop`, merge it, and cut again.
   A stopped cut is resumed with `make release-cut` (see "Resuming or aborting
   a cut" below); that resume is refused once `origin/develop` moves past the
   cut's starting commit, which a busy `develop` does within minutes.
   `RELEASE_ALLOW_RAW_CHANGELOG=1` accepts a flagged section deliberately.

   **Non-interactive cuts (agents, CI).** To replace the prepared section in
   the same invocation, write the section first and pass it in:

   ```bash
   make release-cut VERSION=X.Y.Z CHANGELOG_SECTION=/tmp/benchbox-X.Y.Z-section.md
   ```

   The file holds the section body only: the `###` groups and their bullets,
   without the `## [X.Y.Z] - date` header, which the cut adds. Keep it outside
   the worktree, because an untracked file there blocks `release-cut-abort`.
   The text replaces any `[X.Y.Z]` section already present, and goes below
   `[Unreleased]`, which the cut leaves unchanged. The `--require-prepared` and
   `--check-curation` gates still run afterwards, so pending fragments and raw
   commit subjects are still refused, and the exact-`origin/develop` start
   check still runs first.
4. Curates the release branch — `git rm`'s the dev-only and deferred
   release paths while retaining the curated Results Explorer publication
   inputs: `results-data/`,
   `_project/scripts/explorer_pipeline/`,
   `_project/scripts/explorer_publish.py`, and
   `_project/scripts/results_explorer_snapshot_invariants.py`. The rest of
   `_project/`, agent configs, and dev-tooling root files remain
   development-only; the exact list is enforced by the `release-cut:`
   Makefile target and `scripts/check_release_curation.py`.
   Explorer seed, submission and sync workflows remain development-only.
   `changelog.d/` is development-only too: `release-prep` consumes every
   fragment, and the release tree carries none.
5. Commits a single `Release vX.Y.Z` commit on `vX.Y.Z`.
6. Merges `origin/release` into `vX.Y.Z` with `-s ours
   --allow-unrelated-histories`. `release` and `develop` have unrelated roots and
   diverge permanently, so without this the release PR is unmergeable, GitHub
   cannot compute a merge ref, and **no CI runs at all**. `-s ours` keeps the
   curated release tree byte-for-byte (the merge is guarded on that) and only
   records `release` as a second parent, which `release-finalize`'s squash-merge
   then collapses away.
7. Pushes and opens a PR against `release`.
8. Sweeps prior `v*` branches on origin (option-c lifecycle: keep until
   superseded, then auto-delete on the next `release-cut`).

### Release notes from fragments

Each pull request that changes user-visible behavior adds one
`changelog.d/<branch>.json` fragment (`make changelog-entry`; schema and
rendering in `scripts/changelog_fragments.py`). `[Unreleased]` stays empty.
Write for users, not contributors: include only changes a user would notice.
Fragments render under `Before you upgrade`, `Added`, `Changed`, `Fixed`,
`Security` and `Documentation`, in that order; behavior changes carry `BREAKING:`.

`make release-prep VERSION=X.Y.Z` compiles the fragments with
`scripts/generate_changelog_entry.py --compile-fragments`:

1. Every fragment is validated first. An invalid one aborts the run with a
   path-specific error before any file, fragment or version marker changes.
2. The starting draft is the existing `[X.Y.Z]` body on a re-run, otherwise the
   body of the newest pre-release section of the same base version (for example
   `[0.5.0rc1]` when preparing `0.5.0` or `0.5.0rc2`), otherwise empty.
3. User entries are appended under the six canonical headings: into a heading
   that already exists, or into a new heading created in canonical order. A
   heading is never duplicated.
4. `## [X.Y.Z] - DATE` is written directly below the empty `[Unreleased]`, and
   the pre-release sections that were folded in are removed.
5. Compare links are updated. A pre-release adds `[X.Y.Z]` (previous final tag
   to the pre-release tag) and leaves `[Unreleased]` at the previous final tag.
   A final release drops the folded sections' links, adds `[X.Y.Z]`, and points
   `[Unreleased]` at `vX.Y.Z...HEAD`, which is what the published-release
   accounting check expects.
6. Every consumed fragment, user and internal, is deleted. Git history is the
   archive.
7. A worksheet (file, pull request, category, tags), the bullet and line counts
   against the 45/160 ceilings, and a coverage report are printed. The coverage
   report lists `feat`, `fix` and `perf` squash commits on `develop` since the
   commit that added the previous final release's changelog section (release
   tags sit on the unrelated `release` history, so the tag is not a usable
   lower bound) that added no fragment, so a missing entry is a visible decision.

Re-running prep appends fragments added since the last run to the existing
`[X.Y.Z]` section and keeps its date; with no fragment pending the file is left
unchanged byte for byte. A non-empty `[Unreleased]` section aborts the compile.
The curation gate still enforces the length half of the standard: at most 45
bullets and 160 lines. A longer section means entries need merging or
trimming, not a way around the gate.

### Changelog merges

Each pull request adds its own `changelog.d/<branch>.json` file, so two
branches never edit the same lines and a merge or rebase cannot conflict on
release notes. `CHANGELOG.md` is edited only by `make release-prep`, which
compiles the fragments. GitHub's server-side merge ignores local merge
drivers, which is why the repository does not rely on one.

The `ci-paths` job runs `scripts/changelog_fragments.py check` on every pull
request. It fails when `[Unreleased]` holds any text, and when a pull request
that changes shipped paths (`.github/changelog-paths.txt`) with a user-facing
title adds no fragment. Release preparation pull requests are exempt from the
entry requirement. The fragment schema limits are 2000 characters for entry
text and 600 for each detail line.

### Tests on the release tree

The release PR's required jobs run the fast and medium tiers on the curated
tree: the `test` job runs the fast tier, and the `medium` jobs run the complete
medium tier in four shards. A test that reads a path removed in step 4 passes on
develop but fails or errors there, and blocks `release-required-result`.
`scripts/release_curation_dry_run.py` catches this early. It reads the
`git rm` commands from the `release-cut:` recipe, so it removes exactly what a
cut removes. It applies them in a scratch worktree of the committed `HEAD`, then
runs the fast and medium tiers there. CI runs it on every pull request for the
test modules the PR changes. Before a cut, run both tiers on the whole curated
tree (about 20 minutes locally):

```bash
uv run -- python scripts/release_curation_dry_run.py
```

To fix a failure, change the test, not the curation:

- When one test reads a file that release-cut removes, skip that test when the
  file is absent, for example
  `@pytest.mark.skipif(not PATH.exists(), reason="_project scripts are not in this checkout (release tree)")`.
- When every test in a module needs a removed file, put the same `skipif` in the
  module's `pytestmark`. When the module imports a removed helper, use
  `pytest.importorskip`.
- Add the test file to the release-cut strip list only when the whole file tests
  development-only tooling. The list is the `git rm` lines in the `release-cut:`
  target and the leftover guard below them.

### Resuming or aborting a cut

Steps 1-3 are idempotent, so an interrupted cut is resumed by re-running the
same command from the `vX.Y.Z` branch:

```bash
make release-cut VERSION=X.Y.Z
```

This reuses the branch and keeps the CHANGELOG section. The branch is reused rather than recreated, the version bump and `uv lock`
re-apply to the same values, and an existing `## [X.Y.Z]` section is left
untouched — so a section you curated between runs survives. To throw the cut
away instead (this discards tracked edits and restores the creating worktree branch):

```bash
make release-cut-abort VERSION=X.Y.Z
```

`release-cut-abort` works in the creating linked worktree, even if the primary
clone holds `develop`. It returns to the branch recorded when the worktree was
created and deletes only an uncommitted local release branch. It refuses a
pushed branch or tag, any local tag, a moved or committed branch, and untracked
files. Inspect the edits before deliberately discarding them. `release-cut`
refuses to resume a branch that already carries its `Release vX.Y.Z` commit or
exists on origin or when a local release tag already exists. It also refuses
when fetched `origin/develop` has advanced
past the cut's starting commit. The branch and curated files remain untouched;
review the new develop commits and preserve any authored changelog text before
deciding whether to discard the cut and start again.

### What `release-finalize` does

1. Finds the unique open or merged release PR for `vX.Y.Z`.
2. For an open PR, checks the required PR status list once and refuses to continue unless
   both `validate-base` and `release-required-result` are present and green.
   Missing means the ruleset/workflow contract is broken; pending means wait
   in GitHub Actions and rerun the command. `release-finalize` does not poll.
3. Rechecks the PR head and squash-merges with that expected SHA. A concurrent
   head change fails before merge. The `release-only` ruleset also requires the
   two green contexts.
4. Confirms the PR is `MERGED` with `mergedAt` and a merge commit, fetches
   `origin/release` and tags, then checks that the merge commit is on `release`.
   It creates or verifies `vX.Y.Z` at that exact commit without checking out a
   local `release` branch. A mismatched local or remote tag stops finalization.
5. Pushes an absent remote tag. The tag is lightweight, so `release-v2.yml`
   starts on the push and refuses it at its annotated-tag check; that failure is
   expected on this path. `release.yml` no longer starts on a tag push, so run it
   by hand with `gh workflow run release.yml --ref vX.Y.Z`:
   `dependency-bounds` and `sqlglot-upstream-freshness` → `build` (with `SOURCE_DATE_EPOCH` from the tag
   commit) → `publish` (PyPI trusted publisher) → `github-release` →
   `test-installation` (cross-platform pip install verification).
   The GitHub Release step extracts a non-empty, curated changelog section and
   creates or updates the release idempotently, including its attached build
   artifacts.
6. Leaves `develop` untouched. Dev-only paths persist on develop by
   design (per A3 in `_project/decisions/single-repo-migration.md`); the
   release squash on `release` does not need to be replayed onto develop.

**The site.** A release needs no site deploy step; deploys are automatic from
`BenchBox-dev/benchbox-site`. `release-finalize` sends nothing to the site. The site's `/docs/` and
`/docs/dev/` pages build from the newest certified `develop` bundle, which `site-inputs.yml` builds
and sends to `BenchBox-dev/benchbox-site` as a `core-bundle` dispatch after each successful push to
`develop`. The landing page and the release blog posts live in `BenchBox-dev/benchbox-site`; change
them there by pull request.

**Resuming finalization.** Run `make release-finalize VERSION=X.Y.Z` again after
an interrupted merge, tag creation, or tag push. If the PR is still open, its
current exact head must pass the required checks before merge. If it is merged,
the command resumes from the recorded merge commit and does not merge again. A
matching local tag is pushed; a matching remote tag is accepted as already
pushed. A mismatched tag, closed unmerged PR, missing merge commit, or merge
commit not on fetched `release` requires investigation and is never retagged
or force-pushed. If the tag is already pushed and `release.yml` is pending or
failed, inspect that workflow and the PyPI/GitHub artifacts separately; a
successful finalize command alone does not prove package publication.

**Syncing `develop`'s version.** `release-cut`/`release-finalize` never modify
`develop` (step 6), so its declared version does not track releases on its own —
D5's original "rebase `develop` onto `main`" step became impossible once
`release`/`develop` diverged as unrelated roots. After a release publishes, bump
`develop` to match on a branch off `develop`:
`scripts/update_version.py --version X.Y.Z --update-pyproject`, then `uv lock`,
then PR back to `develop`. This realigns all five version sources
(`benchbox/__init__.py`, `pyproject.toml` and the three `Current release:` doc
markers) with the latest published release, so `develop` no longer trails PyPI.

No unit test pins the repository to the published version. A release-prep PR on
`develop` raises the declared version before PyPI serves it, so a test that compared
`pyproject.toml` with PyPI would fail every release-prep PR. PyPI remains the
authority for publication state; the command below checks it on demand.

After the synchronization PR is prepared, verify it against PyPI's live
publication state rather than assuming the newest git tag was published:

```bash
uv run -- python scripts/generate_changelog_entry.py --check-release-accounting
```

If an existing GitHub Release has missing or generic notes, replace only its
body from the validated changelog section. This command is idempotent and
refuses empty notes and sections that fail the curation checks:

```bash
uv run -- python scripts/generate_changelog_entry.py \
  --version X.Y.Z --sync-github-release-notes
```

The notes-only recovery does not retag, republish to PyPI, or replace release
assets.

Push-to-release jobs are post-merge signals. They may still start when `release`
advances, but they are not pre-publish evidence: the tag push follows the
successful release PR merge and `.github/workflows/release.yml` begins from
that public tag. If a post-merge `release` check fails after the tag is pushed,
handle it as a patch release or incident; do not treat the already-published
release as if it had been blocked.

## Recovering from common failures

- **`validate-base` or `release-required-result` is missing**: stop. The
  `release-only` ruleset or release workflow contract is out of sync;
  do not finalize until both stable required contexts exist.
- **`validate-base` or `release-required-result` is pending or failed**: wait
  for GitHub Actions or fix on a feature branch off `develop`, PR back to
  `develop`, then inspect the stale `vX.Y.Z` branch and resolve its
  disposition first (keep, delete locally, delete on origin) before re-running
  `make release-cut`. The option-c sweep only deletes *prior* release branches
  (`Makefile` excludes `v$(VERSION)` via `grep -Fxv`), and
  `scripts/release_cut_start.sh` hard-fails on any branch or tag collision, so
  re-running the cut without resolving the stale branch stops at the collision
  check with no further instruction.
- **UAT campaign evidence is missing, stale, red, dirty, or non-ancestor**:
  follow up with the optional three-stage campaign and review its report (see
  "UAT matrix campaign evidence"). This does not block `release-finalize` and
  does not use the emergency override.
- **Release canary is missing, stale, or red**: inspect the latest
  `release-canary.yml` run. If the non-fast canary failed, fix through
  `develop`; if ruleset drift failed, update the live GitHub ruleset or this
  runbook so they match. Use the emergency override variables only with an
  explicit incident/approval record.
- **Wheel content is wrong**: adjust `pyproject.toml` / `MANIFEST.in`
  excludes on `develop`, then cut a patch release. PyPI rejects
  re-uploads of an existing version, so always bump.
- **`release.yml` fails after tag push**: investigate via `gh run view`,
  fix the underlying issue, bump to the next patch version, and re-cut.
  Do **not** force-push or re-tag; the tag is already public.

## Recovering from a broken PyPI release

PyPI versions are **immutable**: once `X.Y.Z` is published you cannot
re-upload or delete-and-replace it, even to fix a broken build. This is
generalized guidance — `_project/TODO/main/active/release-recovery-v0-3-1.yaml`
is the worked precedent (see below) for what happens when this bites in
practice: `v0.3.0` published 2026-05-16 with a broken clean import
(`ModuleNotFoundError: No module named 'pandas'`) and sat as PyPI-latest,
uncaught, for over a month, because nightly's package-install test built a
wheel from `develop` rather than checking the artifact actually on PyPI.
(`.github/workflows/release-canary.yml`'s `pypi-latest-installability` job
now closes that specific detection gap going forward — see "Release canary
and ruleset drift" above.)

Two response options once a bad version is confirmed on PyPI, and when to
use each:

- **Yank the broken version.** PyPI's "yank" (`pip index` / PyPI project UI)
  hides a release from default dependency resolution without deleting it —
  existing installs that already pinned the bad version are unaffected, but
  new `pip install benchbox` resolutions skip it and fall back to the latest
  non-yanked version. Use this when there is a safe prior version to fall
  back to and the fix is not ready yet.
- **Ship a `.postN` or the next patch.** This repo's actual precedent is a
  full next patch release (`v0.3.0` -> `v0.3.1`), not a `.postN` suffix, per
  `release-recovery-v0-3-1`. Use this when the fix is ready quickly and a
  monotonic forward release is cleaner than yanking (e.g. there is no good
  prior fallback version, or you want users who already resolved the bad
  version to get the fix on their next install/upgrade rather than silently
  falling back).

Either path is a judgment call based on the incident (is there a safe
fallback version, is a fix already close) — it is not automated by the
canary itself; a canary failure is a signal to make that call, not a
trigger for an automatic rollback action.

The forward-fix path uses the same flow as any other release — there is no
separate recovery procedure:

```bash
make worktree-create BRANCH=chore/cut-patch-release WORKTREE_PATH=../BenchBox.wt-cut-patch-release
cd ../BenchBox.wt-cut-patch-release
make agent-write-preflight
make release-cut VERSION=X.Y.Z
make release-finalize VERSION=X.Y.Z
```

As in the main flow, review the PR and wait for `validate-base` and
`release-required-result` before `release-finalize`.

See `release-recovery-v0-3-1` for the worked example of diagnosing a broken
PyPI-latest release, confirming the fix on `develop`, and cutting the
recovery version through this same flow.

## Preparing and checking a release on develop

`release-prep` and `release-check` prepare release content in an ordinary pull
request against `develop`. They do not replace `release-cut` and
`release-finalize` above, which remain the legacy path and publish through
`release.yml` run by hand (`gh workflow run release.yml --ref vX.Y.Z`). The
publisher for this path is `.github/workflows/release-v2.yml`, which starts when
an annotated `vX.Y.Z` tag is pushed. Tag the merged develop commit only after
its `trunk.yml` push run is green, because the workflow publishes that run's
`dist-artifact` and never rebuilds. Use a PEP 440 version: `VERSION=0.5.0rc1`
and tag `v0.5.0rc1` for a TestPyPI rehearsal, then `VERSION=0.5.0` and tag
`v0.5.0` for PyPI. See `release-artifacts.md` for the gates it runs.

```bash
make release-prep VERSION=X.Y.Z
make release-check VERSION=X.Y.Z [BASE_REF=<immutable-predecessor-sha>] [RELEASE_PRE_TAG=1]
```

`release-prep` runs `scripts/update_version.py` (`pyproject.toml`,
`benchbox/__init__.py` and the documentation release markers), then `uv lock`,
then `scripts/generate_changelog_entry.py --compile-fragments`, which builds the
`[X.Y.Z]` section from `changelog.d` (see "Release notes from fragments").
Commit the changed files, including the deleted fragments, as one PR.

`release-check` (`scripts/release_flow.py check`) exits non-zero and lists every
problem it finds. It verifies that:

- the version in `pyproject.toml`, `benchbox/__init__.py`, the documentation
  markers, and the `uv.lock` package entry equals `VERSION`;
- `CHANGELOG.md` has a dated, curated `## [X.Y.Z]` section;
- the `uv.lock` schema revision was not downgraded
  (`_project/scripts/check_uv_lock_revision.py`);
- every top-level path is accounted for (`scripts/check_release_curation.py`);
- no capped dependency reached its bound
  (`scripts/check_dependency_bounds.py --fail-on=cap-reached`);
- the bundled SQLGlot runtime is based on the newest upstream release
  (`scripts/check_sqlglot_upstream_freshness.py`). A newer upstream tag fails
  the check until the fork is refreshed and the new wheel adopted (see
  `downstream/MAINTAINING.md` in `BenchBox-dev/benchbox-sqlglot`). If a refresh
  must be deferred, add a waiver for exactly that tag to
  `scripts/sqlglot_refresh_waiver.json` with `tag`, `reason`, `approved` and
  `expires` (at most 31 days after approval); the owner reviews it like any
  release-enforcement change. The check needs network access to the upstream
  repository and fails when it is unreachable. The pull request check runs it
  only when the pull request changes the release identity (version markers or
  the `[X.Y.Z]` changelog section) or edits the check itself
  (`scripts/check_sqlglot_upstream_freshness.py`,
  `scripts/sqlglot_refresh_waiver.json`); a pull request that only edits other
  release enforcement files (`Makefile`, `ci.yml`, the other check scripts) passes
  `SKIP_UPSTREAM_FRESHNESS=1` so an upstream SQLGlot release cannot block
  unrelated merges. The release workflows and `release-readiness` still run
  the check unconditionally.
- with `RELEASE_PRE_TAG=1` (`release_flow.py check --pre-tag`), no
  `changelog.d/*.json` fragment is pending. The pull request check passes it
  only when the pull request changes the release identity: a version marker,
  the lock package version, or the creation or removal of the project
  version's changelog section (`release-identity-changed`, reported by
  `release_flow.py select` separately from enforcement-file changes; editing an
  existing section or the lock schema does not count), which is the check that
  stops a release preparation pull request from merging to `develop` with
  fragments left
  uncompiled. `release-v2.yml` tags merged `develop` commits, so its tag-time
  `release-check` passes the flag and refuses to publish a tag whose tree still
  carries a fragment, for example one merged after the preparation pull
  request. The legacy `release-cut` path is covered separately: its recipe
  removes `changelog.d` from the release branch and `--require-prepared`
  refuses to cut while fragments are pending. A default `release-check` passes
  while fragments are pending.

To adopt a refreshed fork wheel, run
`make sqlglot-adopt-wheel WHEEL=<path> SHA256=<digest>` (or put the digest in
`<wheel>.sha256` beside it; add `DRY_RUN=1` to only verify). The wheel must be
the verified, clean, pure-Python release artifact and strictly newer than the
pinned runtime. The command installs it, rewrites `benchbox/_vendor/sqlglot.json`
and the identity in `scripts/sqlglot_runtime_baseline.json`, runs the runtime pin
and retirement/baseline checks, and restores the previous files if either fails.
It publishes nothing; add the changelog fragment, review any changed baseline
outcome, then open the pull request.

Both targets need the full development tree, because the lock-revision check
lives under `_project/`.
The check uses a locked environment and leaves `uv.lock` unchanged. Its revision
baseline defaults to the newest final release tag older than `VERSION`; that
tag is resolved once to a commit. CI selects changes to managed release markers,
the current version's changelog section, the lock schema, or release enforcement
inputs. Its always-required `ci-paths` job runs the check with the immutable
pull-request event base SHA as `BASE_REF`; a failed check blocks
`core`. Unchanged release identity avoids the dependency installation and check.
Missing baseline history fails the check. Preparation uses a frozen environment
until its explicit `uv lock` step. It validates the date and every changelog
fragment (a dry-run compile) before changing version markers; a later command
failure can leave partial changes for inspection.

## Reference

- Makefile targets: `release-cut`, `release-finalize`, `release-prep`, `release-check`.
- Workflow: `.github/workflows/release.yml`.
- Canary workflow: `.github/workflows/release-canary.yml`.
- Release-readiness gate: `scripts/release_readiness_check.py`.
- Ruleset drift gate: `scripts/ruleset_drift_check.py`.
- Curation drift guard: `scripts/check_release_curation.py` (runs in
  `lint.yml` on every PR).
- Release preparation and pre-tag check: `scripts/release_flow.py`.
- Version updater: `scripts/update_version.py`.
- Changelog generator: `scripts/generate_changelog_entry.py`.
- Architecture record: `_project/decisions/single-repo-migration.md`
  (D5 / A3 / A4 / A5, plus the amendments for the 2-command flow, the
  develop-not-modified rule, and the v0.3.0 release-scope curation).
