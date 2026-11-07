# Cutting a BenchBox release

BenchBox releases from one long-lived branch, `develop`, in
`BenchBox-dev/BenchBox`, and from on-demand `release/X.Y.x` maintenance
lines (see "Maintenance releases"). A release is an annotated `vX.Y.Z` tag on a
commit of one of those branches whose `trunk.yml` push run is green. Pushing the
tag starts `.github/workflows/release-v2.yml`, which admits the artifacts that
run built, verifies them, publishes them with attestations, and creates the
GitHub release.
This guide is the maintainer runbook. The artifact, admission, and publishing
contract lives in [release-artifacts.md](release-artifacts.md); this guide does
not repeat it.

This flow releases the Python package. It does not publish `benchbox.dev`: the
site is built and deployed from `BenchBox-dev/benchbox-site` by its
`.github/workflows/deploy.yml`.

## The flow

```bash
make worktree-create BRANCH=chore/release-X.Y.Z WORKTREE_PATH=../BenchBox.wt-release-X.Y.Z
cd ../BenchBox.wt-release-X.Y.Z
make agent-write-preflight
make release-prep VERSION=X.Y.Z
make release-check VERSION=X.Y.Z RELEASE_PRE_TAG=1
```

1. `release-prep` bumps the version markers, refreshes `uv.lock`, and compiles
   the `changelog.d` fragments into the dated `## [X.Y.Z]` section. Curate the
   section, commit the result as one pull request against `develop`, and merge
   it like any other pull request. See "Preparing and checking a release on
   develop".
2. Wait for the `trunk.yml` push run of the merged commit to finish green. Every
   push to `develop` gets its own run, and a later push never cancels or
   replaces it, so merging can continue while it runs. The publisher uses that
   run's `dist-artifact` and never rebuilds, so a commit whose run failed cannot
   be released.
3. Tag the merged `develop` commit with an annotated, PEP 440 tag and push it:
   `git tag -a vX.Y.Z <sha> -m "BenchBox X.Y.Z"` then
   `git push origin vX.Y.Z`. Use `VERSION=0.5.0rc1` and tag `v0.5.0rc1` for a
   TestPyPI rehearsal, then `VERSION=0.5.0` and tag `v0.5.0` for PyPI.
4. Follow the `release-v2.yml` run. It admits the trunk artifacts, runs the
   install matrix and the release UAT (or verified reuse of the release
   candidate's evidence), waits for the `pypi` or `test-pypi` environment
   approval, publishes, and creates the GitHub release from the curated
   changelog section. Confirm the GitHub release, the PyPI version, and
   `gh attestation verify` as described in [release-artifacts.md](release-artifacts.md)
   before reporting the release complete.

Choose an unused version and resolve any existing `vX.Y.Z` tag before starting.
Never retag or force-push a published tag.

## Maintenance releases

A maintenance line ships a fix on an older minor without shipping everything on
`develop`, for example a security fix as `0.5.1` after `0.6.0` is out. Lines
are on demand: a `release/X.Y.x` branch exists only while that minor is
supported, and only the owner creates or deletes one (rulesets
`release-branches` and `release-branch-lifecycle` in
[repo-admin-settings.md](repo-admin-settings.md)).

Admission (`scripts/release_artifact_consumer.py`) accepts a tag only when its
commit is on exactly one protected line, `develop` or the `release/X.Y.x`
branch for the tag's `X.Y`, and that exact commit has a successful `trunk.yml`
push run on that branch with its `dist-artifact`. It refuses:

- a `vX.Y.Z` tag on a `develop` commit while `release/X.Y.x` exists;
- a tag for another minor on a maintenance branch (`v0.6.1` on `release/0.5.x`);
- a commit on both lines, or on neither;
- a commit whose trunk run is missing, failed, or ran on another branch.

Prerequisite: `trunk.yml` does not yet run on pushes to `release/**`, and
`oracle-gate` does not yet cover pull requests into a maintenance line. Both
arrive with a later change (multi-base support in the review gate, plus a
`release/**` push trigger for `trunk.yml`). Until it lands, admission refuses
every maintenance tag because no trunk run exists on the branch, and a pull
request into a maintenance line cannot report `oracle-gate`. A maintenance
release therefore needs that change first; do not create a line before then.
See the gate coverage note in [repo-admin-settings.md](repo-admin-settings.md).

The procedure:

1. Once, delete the legacy `release` branch if it still exists: Git cannot hold
   `release` and `release/X.Y.x` together. Confirm both rulesets are live with
   `scripts/ruleset_drift_check.py`.
2. The owner creates the branch from the `vX.Y.0` tag commit, or from the newest
   `vX.Y.Z` tag of that minor:
   `git push origin "vX.Y.0^{commit}:refs/heads/release/X.Y.x"`.
3. The branch starts with the workflows and release scripts of that tag. If the
   tag predates maintenance-line support, the first pull request into the branch
   brings `.github/workflows/trunk.yml`, `ci.yml`, `release-v2.yml` and
   `scripts/release_artifact_consumer.py` (and whatever they need) from
   `develop`; `ci.yml` accepts a `release/X.Y.x` base.
4. Land the fix by pull request into `release/X.Y.x`; the pull request
   squash-merges like any other. Land the same fix on `develop` through its own
   pull request when `develop` has the defect too. Add a `changelog.d` fragment
   on each branch.
5. Prepare the release on the branch, as for `develop`: from a worktree on
   `release/X.Y.x`, `make release-prep VERSION=X.Y.Z` and
   `make release-check VERSION=X.Y.Z RELEASE_PRE_TAG=1`, committed as one pull
   request into the branch. The lock-revision baseline is the newest final tag
   older than `X.Y.Z`, so `0.5.1` compares with `v0.5.0` even when `v0.6.0`
   exists.
6. Wait for the `trunk.yml` push run of the merged commit on `release/X.Y.x`:
   `gh run list --workflow trunk.yml --branch release/X.Y.x --event push`.
   `release_flow.py check --releasable-commit` and
   `scripts/release_releasable.py` look at `develop` only; admission is the
   check for a maintenance commit.
7. Tag the commit with an annotated tag and push it. Rehearse with
   `vX.Y.ZrcN` first: it publishes to TestPyPI from the same checks. For the
   final tag, release UAT reuses the newest `vX.Y.ZrcN` evidence of the same
   version when every reuse condition holds, and otherwise runs the full
   three-stage UAT; a maintenance line has no other UAT evidence to fall back
   on. The GitHub release is created with `--latest=false`, so "Latest" keeps
   pointing at the newest minor, and PyPI resolves `pip install benchbox` by
   version, not by upload time.
8. Add the release's changelog section to `develop`. Open a pull request into
   `develop` that copies the `## [X.Y.Z] - <date>` section (print it from the
   tag with `python scripts/generate_changelog_entry.py --version X.Y.Z
   --print-section`) into `CHANGELOG.md` below every newer section and above
   the previous release of that minor, and adds
   `[X.Y.Z]: https://github.com/BenchBox-dev/BenchBox/compare/vX.Y.(Z-1)...vX.Y.Z`
   to the link list in the same position. Leave `[Unreleased]` alone. The pull
   request is a `docs:` change and needs no fragment.
9. When the line is no longer supported, the owner deletes the branch:
   `git push origin --delete release/X.Y.x`. The tags stay.

## Release canary and ruleset drift

`release-canary.yml` runs daily and on manual dispatch. Scheduled runs execute
from the default branch, but the workflow checks out `develop` before running
release evidence and records that checked SHA in `release-canary-summary.json`.
Its credential-free non-fast suite is
`(slow or resource_heavy) and not (stress or live_integration)`. The same
workflow runs `scripts/ruleset_drift_check.py` against
`docs/internal/repo-admin-settings.md`, so ruleset drift turns the canary red
instead of silently invalidating release assumptions. The ruleset drift check
must use `RULESET_DRIFT_TOKEN`, a repository secret with enough ruleset
visibility to expose bypass actors; the default `GITHUB_TOKEN` is insufficient
for that part of the contract. The canary's `pypi-latest-installability` job
installs the newest published `benchbox` from PyPI, so a broken published
release is detected without waiting for the next release.

`release-v2.yml` does not consult the canary. Before tagging, check the latest
`release-canary.yml` run yourself: a missing, red, or old run is a reason to
investigate first. `release-v2.yml` runs the ruleset drift check itself during
admission, so ruleset drift blocks publication regardless of the canary.

Stress tests, live cloud integrations, and long-running campaigns outside the
release UAT remain advisory until their credential, cost, and flake policies are
stable enough to make them release-blocking.

## UAT matrix campaign evidence

The three-stage UAT matrix runs hosted and is blocking for `publish`. Running the
campaign locally is optional (run stage 2 after stage 1 completes, and stage 3
after stage 2 completes):

```bash
make uat-sweep CONFIG=tests/uat/configs/release-gate-01-native-dataframe.yaml
make uat-sweep CONFIG=tests/uat/configs/release-gate-02-docker-nonoltp.yaml
make uat-sweep CONFIG=tests/uat/configs/release-gate-03-docker-oltp.yaml
make uat-gate-check STAGE1=<run-dir> STAGE2=<run-dir> STAGE3=<run-dir>
```

### Release UAT in `release-v2`

One GitHub-hosted job stops at six hours and stage 1 alone needs more than that,
so `release-v2.yml` splits each stage into shard jobs that run in parallel:

- `uat-stage1`, `uat-stage2`, and `uat-stage3` each run one matrix entry per
  platform group. A shard runs `make uat-sweep CONFIG=<stage config>
  PLATFORMS=<a,b>`, which narrows the config's platform set without changing
  its scales, per-cell timeout, validators, or floors.
- All three stages start once `admit` passes. Each shard is its own hosted
  runner, so shards share no Docker state, and one run reports every failure.
- Each shard uploads its run directory as `release-uat-<tag>-stage<N>-<shard>`.
- The `release-uat` job downloads every shard and runs `make uat-gate-check`
  with several run directories per stage. It is red unless the union of a
  stage's shard cells equals the stage's planned cells (nothing missing, nothing
  duplicated, nothing outside the plan), every shard is green with the same
  source commit and the admitted distribution digest, the cross-scale floor is
  met by the summed clean pairs of the stage's shards, and the Docker start
  ordering holds for stage directories that ran on the same host as the
  previous stage's shards. Each run records `[host] id=<hash>` (machine id plus
  `RUNNER_NAME`) in `uat_lifecycle.log`; a missing id counts as the same host.
- A final tag may reuse its newest release candidate's UAT evidence through the
  `uat-reuse` job. The conditions, and what to freeze between the candidate and
  the final, are in [release-artifacts.md](release-artifacts.md). The
  candidate's shard artifacts are kept 30 days, so tag the final within that
  window or expect a full UAT run.

The shard assignment is the `matrix.include` list of each `uat-stage` job.
`tests/uat/test_release_gate_shards.py` fails when a config's platform set and
its shards drift apart, so add a new platform to a shard in the same change that
adds it to a config group. Size a shard from measured per-platform time and keep
it well under six hours; the SQLite shard is the longest. To see what a shard
plans without running it:

```bash
make uat-sweep CONFIG=tests/uat/configs/release-gate-01-native-dataframe.yaml PLATFORMS=sqlite DRY_RUN=1
```

Locally, `make uat-gate-check STAGE1="<dir> <dir>" ...` accepts the shard run
directories of a stage, and a single unsharded run directory per stage still
works. See `docs/internal/uat-release-campaign.md` for campaign ordering and its
report checklist.

### Trunk UAT

`trunk-uat.yml` runs the same three stages and the same shard jobs every night
at 02:00 UTC against the trunk dist of the newest releasable `develop` commit,
skips a scheduled run when nothing the release UAT depends on changed since the
last run that executed shards, and defers while a `release-v2` run is in
progress. Change a platform's shard in `release-v2.yml` and `trunk-uat.yml`
together; `tests/unit/workflows/test_trunk_uat_workflow.py` fails when their
matrices differ. Its evidence is kept 90 days, and each run appends per-cell
results (terminal state, elapsed time, peak memory, failure signature, attempt)
to the history that `scripts/trunk_uat.py history` reads. Dispatch it with
`gh workflow run trunk-uat.yml --ref develop` after an envelope fix lands. The
rules, artifacts and the decision gate on runner cost are in
[trunk-uat.md](trunk-uat.md).

## Release notes from fragments

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
   commit that added the previous final release's changelog section that added
   no fragment, so a missing entry is a visible decision.

Re-running prep appends fragments added since the last run to the existing
`[X.Y.Z]` section and keeps its date; with no fragment pending the file is left
unchanged byte for byte. A non-empty `[Unreleased]` section aborts the compile.
The curation gate still enforces the length half of the standard: at most 45
bullets and 160 lines, no trailing `(#NNNN)` references, and no duplicate `###`
headings. A longer section means entries need merging or trimming, not a way
around the gate.

## Changelog merges

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

## Preparing and checking a release on develop

`release-prep` and `release-check` prepare release content in an ordinary pull
request against `develop`. The publisher is `.github/workflows/release-v2.yml`;
see "The flow" for tagging.

The `install-matrix` job of `release-v2.yml` installs the admitted wheel on
Ubuntu, macOS and Windows and runs `scripts/smoke_sqlglot_runtime_install.py`
against it. That script installs the wheel beside public `sqlglot`, checks that
`benchbox._vendor.sqlglot` loads from the bundled zip, translates SQL, stays
separate from the public `sqlglot`, and leaves the public package intact after
`benchbox` is uninstalled. Dispatch `windows-fast.yml` on a branch to run the same
script on `windows-latest` before tagging.

Windows verification of the zip-loaded runtime: the script passed on
`windows-latest` with Python 3.12 in `windows-fast.yml` run 40504950564
(installed wheel, bundled import from the zip, translation, coexistence with
public `sqlglot`, uninstall). No path-separator or zipimport change was needed.

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
  unrelated merges. `release-v2.yml` still runs the check unconditionally.
- the bundled SQLGlot runtime carries no published security advisory
  (`scripts/check_sqlglot_advisories.py`, `make sqlglot-advisory-check`).
  `pip-audit` and other package scanners only see declared requirements, and
  SQLGlot ships inside `benchbox/_vendor`, so this check derives the upstream
  version from `benchbox/_vendor/sqlglot.json` (`<upstream X.Y.Z>.<fork
  revision>`) and queries the OSV database for it. A matching advisory, or an
  unreachable database, fails the check. The release owner acts on a failure:
  refresh the fork onto an upstream release that fixes the advisory and adopt
  the new wheel; there is no waiver. It runs wherever the freshness check
  runs. `SKIP_UPSTREAM_FRESHNESS=1` skips both checks in `make release-check`
  because both need network access. A pull request that edits
  `benchbox/_vendor/sqlglot.json` or the advisory check re-runs it.
- with `RELEASE_PRE_TAG=1` (`release_flow.py check --pre-tag`), no
  `changelog.d/*.json` fragment is pending. The pull request check passes it
  only when the pull request changes the release identity: a version marker,
  the lock package version, or the creation or removal of the project
  version's changelog section (`release-identity-changed`, reported by
  `release_flow.py select` separately from enforcement-file changes; editing an
  existing section or the lock schema does not count), which is the check that
  stops a release preparation pull request from merging to `develop` with
  fragments left uncompiled. `release-v2.yml` tags merged `develop` commits, so
  its tag-time `release-check` passes the flag and refuses to publish a tag
  whose tree still carries a fragment, for example one merged after the
  preparation pull request. A default `release-check` passes while fragments
  are pending.
- with `release_flow.py check --releasable-commit <sha>` (not reachable through
  `make release-check`), `<sha>` has a successful `trunk.yml` push run on
  `develop` and an unexpired dist artifact, judged by the same producer
  selection admission uses. When it does not, the check fails with the reason
  and names the newest develop commit that is releasable. It needs `gh` access
  to `BenchBox-dev/BenchBox`. `scripts/release_releasable.py newest` prints that
  commit alone, and `scripts/release_releasable.py check --sha <sha>` runs the
  commit check without the release content checks.

The `sqlglot-delivery` workflow adopts verified fork deliveries. It runs daily
on `develop` in `BenchBox-dev/BenchBox` and on `workflow_dispatch`
(`dry_run`, default true, and `retry`), and is driven by
`scripts/sqlglot_delivery.py`.

- **Trust root.** `.github/sqlglot-delivery.json` fixes the producer
  repository, workflow path, branch `main`, allowed events, artifact prefix and
  record schema and kind. A delivery record can never change it; every field
  the record repeats must equal what the trusted producer run says.
- **Plan.** `plan` lists every producer run (it refuses a listing whose count
  differs from `total_count`), keeps only trusted runs, reads every attempt's
  jobs, and collects the successful receipt jobs
  (`delivered benchbox-sqlglot <version> sha256:<digest>`). It picks the newest
  receipt above the pin, finds the newest run and attempt that still has an
  unexpired `benchbox-sqlglot-delivery-a<attempt>` artifact, and verifies the
  record, `SHA256SUMS` and wheel bytes against the receipt. A run that only
  changes the producer's tooling, with the same version and hash, is not a new
  adoption. A delivery that fails verification fails the run; it is never
  skipped for an older one.
- **Prepare** has no credentials. It runs the adopter
  (`scripts/adopt_sqlglot_wheel.py`) with the version, fork SHA, upstream SHA
  and hash from the verified record, requires that only the pin, the wheel
  archive, the license notice and the baseline identity changed, and emits a
  git bundle and a pull request title and body. The body carries a marker that
  binds version, fork SHA, upstream SHA, hash, the producer run and attempt,
  and the adoption commit and its parent.
- **Publish** runs only when `SQLGLOT_DELIVERY_ENABLED` is `true`, the run is
  not a dry run and the plan says `adopt`. It re-checks the bundle, pushes
  `chore/sqlglot-<version>` and opens the pull request, resuming after an
  interrupted push or pull request creation. At most one automation adoption
  pull request is open. Opening the same version and hash again changes
  nothing. A newer delivery closes an older automation pull request that is
  unarmed, unheld and not green; it waits behind one that is armed, green or
  held. A pull request counts as automation-owned only when the live head is
  the single commit whose SHA and parent the marker recorded, the body has no
  edit by anyone but the delivery App (read from the GraphQL
  `userContentEdits` history; an unreadable history refuses), and the author,
  head repository, branch name and title match. A commit email proves nothing.
  A branch left behind by an interrupted push is resumed only when its tip is
  the prepared commit, which is reproducible for an unmoved `develop`;
  otherwise it is refused and a person deletes it. Human-authored and
  human-modified pull requests and branches, and any
  pull request with the `no-auto-merge` hold, are never touched. A pair whose
  pull request was closed unmerged is not proposed again unless a person
  dispatches the workflow with `retry`, which uses a fresh
  `chore/sqlglot-<version>-retryN` branch. A pair whose adoption merged and was
  reverted is never proposed again.
- **Arm** runs after publish in the same job and arms an automation pull
  request only by running `scripts/trunk_revert.py arm-gate` and
  `scripts/pr_arm.py` on the exact head, as `make pr-arm` does. It re-reads
  the pull request and repeats the full ownership check live, both when it
  lists the pull request and again immediately before it runs `pr_arm.py`, and
  refuses on any mismatch. It never
  re-arms after a person disabled auto-merge or applied the hold. The one
  automation disarm it recovers from is the trunk-red disarm by
  `github-actions[bot]` (`trunk-red-disarm.yml`). The pull request is usually
  not green on the run that opens it, so the next daily run arms it.
- **Kill switch and dry run.** Every mutating command refuses unless the
  repository variable `SQLGLOT_DELIVERY_ENABLED` is exactly `true`. A manual
  dispatch defaults to `dry_run`: the prepare job verifies the delivery and
  writes the plan to the run summary with a read-only token, and nothing is
  published. `publish` and `arm` also take `--dry-run` for a local report of
  the actions they would take.
- **Credentials.** The publish job mints a token from the delivery GitHub App
  with `contents` and `pull-requests` write, using the secrets
  `SQLGLOT_DELIVERY_APP_ID` and `SQLGLOT_DELIVERY_APP_PRIVATE_KEY`. Without
  them the job fails with a message and publishes nothing. The prepare job
  holds no secret and a read-only token, and the publish job never runs code
  from the prepared artifact.
- **Rollback.** Revert the adoption pull request on `develop`, and in the same
  reviewed pull request add `{"version", "sha256", "reason"}` to `suppressed`
  in `.github/sqlglot-delivery.json` (`forkSha` is optional and informational).
  A suppressed pair is decided from the producer's receipt alone: it is skipped
  before any artifact is downloaded, so it stays skipped after its artifact
  expires or its record stops verifying, and the next best delivery is
  considered. The skipped pairs are listed in `skippedSuppressed`.
- **Producer exceptions.** A version delivered with two hashes, or a pinned
  version delivered with a different hash, fails the run with `Producer
  exception` and names the fork. The producer owns the fix in
  `BenchBox-dev/benchbox-sqlglot`; core opens no issue and adopts nothing
  until the producer publishes a newer version.

To adopt a wheel by hand, for example while the workflow is disabled, run
`make sqlglot-adopt-wheel WHEEL=<path> SHA256=<digest> EXPECTED_VERSION=<version>
FORK_SHA=<sha> UPSTREAM_SHA=<sha>` (the digest may instead sit in
`<wheel>.sha256` beside it; add `DRY_RUN=1` to only verify). Take the version,
fork and upstream SHAs and wheel digest from the fork's verified delivery
record; all three identities are required, and a wheel whose identity differs
is refused before anything is changed or a dry run reports success. The wheel must be
the verified, clean, pure-Python release artifact and strictly newer than the
pinned runtime. The command installs it, rewrites `benchbox/_vendor/sqlglot.json`
and the identity in `scripts/sqlglot_runtime_baseline.json`, runs the runtime pin
and retirement/baseline checks, and restores the previous files if either fails.
It publishes nothing; review any changed baseline outcome, then open the pull
request. Give it a `chore(deps)` title with a `changed` changelog fragment, as
the delivery workflow does.
The retirement check executes a QuestDB case, so `BENCHBOX_QUESTDB_DSN` must
point at a running QuestDB.

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

After a release publishes, verify the changelog against PyPI's live publication
state rather than assuming the newest git tag was published:

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

The fork owns the upstream lifecycle. Its `benchbox-upstream.yml` workflow
discovers a newer final upstream tag, refreshes the fork with
`python -m downstream.refresh auto`, opens the refresh pull request and merges it
once the fork's required checks pass; its `benchbox-delivery.yml` workflow then
publishes the verified wheel delivery that `sqlglot-delivery.yml` adopts here.
Refresh conflicts, failed refreshes and failing fork checks are issues in
`BenchBox-dev/benchbox-sqlglot`, not in this repository. The fork runbook,
`downstream/MAINTAINING.md`, describes its kill switch, dry runs, credentials
and recovery.

The `sqlglot-fork-refresh` workflow in this repository is the legacy producer
and is retiring. Until it is disabled it runs nightly, refreshes a checkout of
the fork's `main` with the same `auto` command, pushes a successful refresh as
`refresh/upstream-X.Y.Z` with the `SQLGLOT_FORK_REFRESH_TOKEN` secret, opens the
fork pull request, and raises a per-tag issue here (`SQLGlot fork refresh to
vX.Y.Z needs follow-up`, or `SQLGlot fork refresh check failed` for the check
itself) when it cannot. It never merges or adopts. Disable it before enabling the
fork's controller, so only one workflow writes upstream refreshes to the fork;
its workflow, script, tests and secret are then removed.

## Recovering from common failures

- **A `release-v2.yml` gate fails before publication** (`admit`, the install
  matrix, release UAT, or the release-blocking issue check): fix the cause on
  `develop` and, when the failure was in the tagged tree, tag a new version.
  Re-running a failed tag is allowed only for a run that failed before
  publishing and only while the trunk artifact for that commit is unchanged;
  see [release-artifacts.md](release-artifacts.md).
- **The trunk run of the commit is red or its dist artifact is missing or
  expired**: the commit cannot be released. Run
  `uv run -- python scripts/release_releasable.py newest` for the newest
  releasable `develop` commit, or merge a new commit and tag that one.
- **Release canary is missing, stale, or red**: inspect the latest
  `release-canary.yml` run. If the non-fast canary failed, fix through
  `develop`; if ruleset drift failed, update the live GitHub ruleset or
  `docs/internal/repo-admin-settings.md` so they match.
- **Wheel content is wrong**: adjust `pyproject.toml` / `MANIFEST.in`
  excludes on `develop`, then release a patch version. PyPI rejects
  re-uploads of an existing version, so always bump.
- **`release-v2.yml` fails after publication**: investigate via `gh run view`,
  fix the underlying issue, bump to the next patch version, and release again.
  Do **not** force-push or re-tag; the tag is already public.

## Recovering from a broken PyPI release

PyPI versions are **immutable**: once `X.Y.Z` is published you cannot
re-upload or delete-and-replace it, even to fix a broken build. This is
generalized guidance — `_project/TODO/main/active/release-recovery-v0-3-1.yaml`
is the worked precedent for what happens when this bites in
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
separate recovery procedure. Follow "The flow" with the next patch version.

## Reference

- Make targets: `release-prep`, `release-check`.
- Publisher: `.github/workflows/release-v2.yml`; contract in
  [release-artifacts.md](release-artifacts.md).
- Canary workflow: `.github/workflows/release-canary.yml`.
- Ruleset drift gate: `scripts/ruleset_drift_check.py`.
- Release preparation and pre-tag check: `scripts/release_flow.py`.
- Version updater: `scripts/update_version.py`.
- Changelog generator: `scripts/generate_changelog_entry.py`.
