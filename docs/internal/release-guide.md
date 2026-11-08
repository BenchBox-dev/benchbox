# Cutting a BenchBox release

BenchBox releases from `develop`, the default release line, in
`BenchBox-dev/BenchBox`, and from on-demand `release/X.Y.x` maintenance
lines (see "Maintenance releases"), the only other release line. A release is an
annotated `vX.Y.Z` tag on a commit of one of those branches whose `trunk.yml`
push run is green. Pushing the tag starts `.github/workflows/release-v2.yml`,
which admits the artifacts that run built, verifies them, publishes them with
attestations, and creates the GitHub release.
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
   install matrix, checks the continuous UAT evidence and the fresh release
   canary (the full UAT runs only as the fallback when no qualifying evidence
   exists), waits for the `pypi` or `test-pypi` environment
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

`trunk.yml` runs on pushes to `release/**` as well as `develop`, so each
commit merged to a maintenance line gets its own trunk run and `dist-artifact`
on that branch. Only `develop` runs drive the develop-only consumers
(`trunk-red-disarm.yml`, `site-inputs.yml`); a red trunk run on a maintenance
line does not disarm other pull requests.

`release-canary.yml` tests the branch it runs on when that branch is a
`release/X.Y.x` line and `develop` otherwise. The schedule only fires on
`develop`, so a maintenance line needs a manual run:
`gh workflow run release-canary.yml --ref release/X.Y.x`. The run is judged on
the line like any other canary, and it opens no incident issue (those belong to
`develop`). `scripts/release_canary_freshness.py` takes the tag, finds the
protected line the tagged commit is on, and accepts only a canary run on that
same line: a `release/X.Y.x` canary never satisfies a `develop` tag or a tag on
another line, and a `develop` canary never satisfies a maintenance tag.

Prerequisite still open: `oracle-gate` and the review gate name `develop` as
the only gated base until the pinned Theseus callers accept release lines, so a
pull request into a maintenance line cannot report `oracle-gate` before that
pin lands. See the gate coverage note in
[repo-admin-settings.md](repo-admin-settings.md).

The procedure:

1. Confirm both rulesets are live with `scripts/ruleset_drift_check.py`. The
   legacy `release` branch and its rulesets are gone; Git cannot hold `release`
   and `release/X.Y.x` together, so none may be recreated.
2. The owner creates the branch from the `vX.Y.0` tag commit, or from the newest
   `vX.Y.Z` tag of that minor:
   `git push origin "vX.Y.0^{commit}:refs/heads/release/X.Y.x"`.
3. The branch starts with the workflows and release scripts of that tag. Because
   the `oracle` environment admits `release/*`, the owner makes the branch carry
   `develop`'s current `.github/workflows/review-verdict.yml`,
   `review-gate.yml`, `.github/theseus-lock.json` and
   `.github/oracle-reviewers.yml` before anyone dispatches workflows on it: a
   tag that predates the pinned callers must not leave older reviewer code
   able to run with the App secrets. If the tag predates maintenance-line
   support, the first pull request into the branch brings
   `.github/workflows/trunk.yml`, `release-canary.yml`, `ci.yml`,
   `release-v2.yml`, `scripts/release_artifact_consumer.py`,
   `scripts/release_canary_freshness.py` (and whatever they need) from
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
   check for a maintenance commit. Dispatch the line's canary on the merged
   commit (`gh workflow run release-canary.yml --ref release/X.Y.x`) and wait
   for it to go green; the release workflow requires a green canary at most 48
   hours old on an ancestor of the tagged commit.
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

`release-v2.yml` requires a fresh canary: the newest `release-canary.yml` run on
an ancestor of the release commit must be green and at most 48 hours old. A
missing, red, or old run blocks the release; investigate the canary before
tagging. `release-v2.yml` also runs the ruleset drift check itself during
admission, so ruleset drift blocks publication regardless of the canary.

Stress tests, live cloud integrations, and long-running campaigns outside the
release UAT remain advisory until their credential, cost, and flake policies are
stable enough to make them release-blocking.

## UAT matrix campaign evidence

The three-stage UAT matrix runs hosted. On `develop` it runs continuously
through `.github/workflows/trunk-uat.yml`; a release checks that evidence, and
only the fallback described below runs the matrix inside `release-v2.yml`.
Running the
campaign locally is optional (run stage 2 after stage 1 completes, and stage 3
after stage 2 completes):

```bash
make uat-sweep CONFIG=tests/uat/configs/release-gate-01-native-dataframe.yaml
make uat-sweep CONFIG=tests/uat/configs/release-gate-02-docker-nonoltp.yaml
make uat-sweep CONFIG=tests/uat/configs/release-gate-03-docker-oltp.yaml
make uat-gate-check STAGE1=<run-dir> STAGE2=<run-dir> STAGE3=<run-dir>
```

### Release UAT in `release-v2`

A release does not run the UAT matrix in the normal case. Continuous UAT on
`develop` (`.github/workflows/trunk-uat.yml`, nightly at 02:00 UTC, skipped when
nothing relevant changed) produces the evidence, and the release checks it. The
shard jobs below are the fallback that runs when no qualifying evidence exists.

One GitHub-hosted job stops at six hours and stage 1 alone needs more than that,
so each stage is split into shard jobs that run in parallel:

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
- Every tag may reuse earlier UAT evidence through the `uat-reuse` job: first
  the newest qualifying nightly trunk UAT run (see Trunk UAT below), then, for
  a final tag, its newest release candidate's run. The conditions are in
  [release-artifacts.md](release-artifacts.md). Anything that changes a guarded
  UAT input after the evidence run (see that page) sends the release back to
  the full stages; land an envelope fix on `develop`, dispatch `trunk-uat.yml`
  on that commit, and tag after it finishes. Trunk evidence is kept 90 days and
  a release candidate's shard artifacts 30 days. Tags on a `release/X.Y.x`
  maintenance branch have no trunk evidence and run the full stages unless
  their release candidate qualifies.
- Independently of UAT, `admit` refuses any tag unless the newest
  `release-canary.yml` run that tested an ancestor of the tagged commit
  concluded `success` and started at most 48 hours ago. A red, stale, missing or
  non-ancestor canary is named in the job summary; fix the canary (or wait for
  the next daily run) and re-run the tag.

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
[Trunk UAT workflow reference](#trunk-uat-workflow-reference).

### Release probe

A change to the release workflow can be wrong in ways unit tests do not show: a
job skipped by an implicit `success()`, a condition that reads an output a
skipped job never wrote. The probe runs the real `release-v2.yml` job graph
and its real conditions without a tag and without publishing, so such a change
is exercised before it merges.

Start a probe from a branch (never from a tag):

```bash
gh workflow run release-v2.yml --ref <branch> -f tag=v0.0.0rc0 -f probe=reuse
gh workflow run release-v2.yml --ref <branch> -f tag=v0.0.0rc0 -f probe=fallback
gh workflow run release-v2.yml --ref <branch> -f tag=v0.0.0rc0 -f probe=guarded-change
```

`v0.0.0rc0` is the synthetic probe tag and the only tag a probe accepts. In
probe mode:

- `admit` skips the tag, ruleset, readiness and blocker checks, which need a
  real tag. It still runs the real canary freshness check; the probe supplies a
  fresh green canary for the probed commit through `scripts/release_probe.py`,
  so a regression in that check still turns the probe red. It then builds
  candidate and probe-version distributions the way the trunk producer does and
  admits the probe-version pair with a receipt the real verifier accepts.
- `uat-reuse` runs the real decision (`release_reuse_uat_evidence.py decide`)
  against synthetic GitHub data. `probe=reuse` offers a qualifying trunk UAT run
  and must reuse it; `probe=fallback` offers none and must fall back to the full
  UAT stages; `probe=guarded-change` offers a green, otherwise qualifying trunk
  run whose evidence commit predates a synthetic commit that changes a guarded
  input (`tests/uat/probe_guarded_change.txt`) and must refuse reuse, with the
  job summary naming that path, so the full UAT stages run.
- The UAT shard jobs are replaced by one stub job that writes shard evidence
  the real `make uat-gate-check` and `release_uat_evidence_check.py` accept for
  the admitted distribution digest (`scripts/release_probe_uat.py`). The
  `release-uat` job, its combined gate and the shard-result detector run
  unchanged, on the reuse path and on both full-UAT paths.
- `install-matrix` runs one `ubuntu-latest` Python 3.12 cell.
- `publish` and `github-release` do not run. `publish-probe` stages the
  admitted files, verifies `SHA256SUMS` and runs `twine check`. It has
  `contents: read` only, no environment, no `id-token`, no attestation and no
  upload step. `release-outcome` reads `publish-probe` in place of `publish`,
  so a probe whose dry run was skipped is red.

A probe aimed at a real tag, a probe from a tag ref, and a real release of
`v0.0.0rc0` all fail at the classification guard, before any gate or build step.
`scripts/release_probe_graph.py` evaluates the job conditions of the workflow
under every combination of job outcomes and fails if `publish` is skipped after
green gates, if `publish` runs after a red gate, or if any job that can publish
runs in probe mode; `tests/unit/workflows/test_release_v2_probe.py` pins the
probe's permissions and the issue-3947 graph shape.

`release-v2-probe.yml` runs on every pull request. When the pull request
touches `release-v2.yml`, `trunk-uat.yml`, `trunk.yml`, `release-uat-env`, the
probe workflow, `scripts/release_*.py`, `scripts/release_requirements.txt`,
`scripts/update_version.py`, `tests/uat/gate_summary.py`, `tests/uat/_cli.py`,
`tests/uat/config.py`, `Makefile`, `make/uat.mk` or a release-gate UAT
config (a rename counts under both its old and new path), it dispatches every probe variant
from the pull request branch and fails unless every one succeeds; otherwise it passes
at once. It runs on every pull request so that it can be made a required
check without leaving unrelated pull requests pending. The probe builds its own
distributions, so it does not execute the `dist-artifact` job of `trunk.yml`;
a drift test keeps the build and verification commands aligned.
Fork pull requests cannot dispatch and fail.

## Trunk UAT workflow reference

`.github/workflows/trunk-uat.yml` runs the three release-gate UAT stages against
the trunk distribution of the newest releasable `develop` commit, every night at
02:00 UTC and on demand. UAT is a continuous property of trunk: a release checks
the evidence of a trunk run instead of discovering UAT failures during the
release. It changes no stage config, timeout, memory limit, validator or floor;
it reuses the `release-uat-env` action, the `release-v2.yml` shard matrix and
`make uat-gate-check`.

### Which commit runs

`scripts/trunk_uat.py plan` selects the source commit with
`scripts/release_releasable.py` (see [release-artifacts.md](release-artifacts.md)):
the newest `develop` commit with a successful `trunk.yml` push run and a dist
artifact that passes admission selection. If none of the newest 40 qualifies,
the run fails with `no releasable commit among the newest 40 successful trunk
runs`.

`scripts/trunk_uat.py admit` downloads that commit's trunk dist, verifies the
artifact digest, producer receipt and bundled binaries, and writes an admission
receipt whose tag is `v<version of pyproject.toml at the commit>` and whose tag
object is the commit. That is the same directory shape `release_admitted_dist.py`
verifies, so the `release-uat-env` action installs the trunk dist unchanged. The
distribution is never rebuilt, and nothing is published.

### Deferral, skipping, and the baseline

`plan` makes one of three decisions. Every decision is written to the plan job
summary and the job outputs.

| Decision | Summary text | When |
| --- | --- | --- |
| defer | `deferred: release-v2 run <id> in progress` | Any `release-v2.yml` run is not completed, for a scheduled or a dispatched run. |
| skip | `skipped: unchanged since <sha>` | Scheduled only: no path in the skip path set changed between the skip baseline and the source commit. |
| run | `run: <reason>` | Everything else: dispatch, no baseline, a baseline that was not green, an unreachable baseline commit, or a changed skip path. |

Only the `schedule` event can skip. `workflow_dispatch` and any other trigger
always run unless deferred.

The skip path set is `GUARDED_TREES` imported from
`scripts/release_reuse_uat_evidence.py`, plus `benchbox`, `uv.lock`,
`.github/workflows/trunk-uat.yml` and `.github/workflows/release-v2.yml`.
`tests/unit/workflows/test_trunk_uat_workflow.py` fails if the set stops
covering the reuse guard. A path matches when it equals an entry or lies below
one.

The skip baseline is the newest earlier run on the same branch that executed
shards: the run that uploaded a `trunk-uat-record-<sha>` artifact. A
run uploads that artifact only when at least one cell executed and the run was
not cancelled, so skipped, deferred, cancelled and zero-cell runs never become
the baseline. A scheduled run skips behind the baseline only when the baseline's
gate verdict was `green` and every job of that run succeeded; after a red or
partial baseline it runs again (`run: baseline run <id> at <sha> was not green
(...)`), so an unchanged red trunk never reports a green skip. A red run that
executed cells is still the baseline, and a green run clears it. An expired
artifact is ignored, so a baseline older than 90 days forces a run.

A skipped run finishes after the plan job. It starts no shard, so it ends in
minutes.

### Jobs

| Job | Purpose |
| --- | --- |
| `plan` | Decision above. Needs `actions: read`. |
| `admit` | Trunk dist of the source commit, uploaded as `admitted-v<version>` for the action. |
| `uat-stage1`, `uat-stage2`, `uat-stage3` | The same 16 shard jobs, matrices, timeouts and step definitions as `release-v2.yml`. A workflow test compares them with the live `release-v2.yml`. |
| `gate` | `scripts/release_uat_evidence_check.py` (every shard staged a run with attempted cells), then `make uat-gate-check` over every shard with the dist digest, as `release-uat` does. |
| `record` | Per-cell records, cost, and the zero-cell detector. Runs after a cancellation. |

Concurrency group `trunk-uat` queues a second run behind the first and never
cancels it, so runs never overlap. 16 shards hold 16 of the 20 concurrent jobs
of the Free plan for hours; keep the 02:00 UTC schedule.

### Evidence and history

Every artifact name includes the source commit; every evidence artifact is kept
90 days. Uploads overwrite, so re-running only the failed jobs of a run replaces
the evidence of the shards that ran again and the gate sees every shard.

| Artifact | Contents |
| --- | --- |
| `release-uat-trunk-<sha>-stage<N>-<shard>` | The shard's `uat_*` run directory and `dist-digest.txt`. The `release-uat-<tag>-stage<N>-<shard>` naming of `release-v2` with the tag component `trunk-<sha>`, so the release evidence detector reads it unchanged. |
| `trunk-uat-gate-<sha>` | `uat-gate-summary.json` (the combined gate verdict) and `dist-digest.txt`. |
| `trunk-uat-record-<sha>` | `trunk-uat-record.json` and `cells-history.jsonl`. Present only for executed runs. |

`trunk-uat-record.json` binds the run to `source_sha` and `dist_digest` (the
SHA-256 of `dist-digest.txt`, the value passed to `uat-gate-check`) and records
the gate `verdict`, cell counts, per-shard state and hours, `runner_hours`,
`wall_hours`, and `over_gate`.

The per-cell history has two readable forms, both built from the shard evidence
the harness already writes. The artifacts of successive runs are the
append-only log.

`cells-history.jsonl` is one JSON row per executed cell (schema `1`):

- run: `run_id`, `run_attempt`, `event`, `source_sha`, `dist_digest`;
- cell: `stage`, `shard`, `platform`, `benchmark`, `scale`;
- result: `status`, `terminal_state`, `elapsed_s`, `peak_rss_gib` (from the
  `# UAT_PEAK_RSS_GIB` marker in the cell log, else null);
- failure: `failure_kind` and `failure_signature` exactly as the harness
  classified them in `cells.jsonl` (null for a passed cell);
- attempt: `attempt`, `retry_signature`, `retry_outcome`, `first_attempt_status`
  and `first_attempt_elapsed_s` from the infrastructure retry, with `attempt`
  1 when the harness wrote no retry.

Print the rows of the newest executed runs, newest first:

```bash
uv run -- python scripts/trunk_uat.py history --branch develop --run-limit 30
```

Python callers use `scripts.trunk_uat.history_rows(branch=..., run_limit=...)`.

`scripts/uat_rubric.py` reads a directory of run directories instead. Download
the shard run directories of the newest executed runs into that layout
(`<run id>-<sha prefix>/stage<N>-<shard>/cells.jsonl` and the rest of the run
directory) and point the rubric at it:

```bash
uv run -- python scripts/trunk_uat.py fetch-runs --output ~/trunk-uat-history --run-limit 30
uv run -- python scripts/uat_rubric.py --history-dir ~/trunk-uat-history --output-dir ~/trunk-uat-rubric
```

### Failure detectors

- The `record` job fails the run with `zero cells executed: <n> shard evidence
  directories held no cells.jsonl rows (<job results>)` when no shard reported a
  cell, including when `admit` failed and no shard ran. Such a run is not a
  baseline and uploads no record artifact.
- The `gate` job fails unless every shard job succeeded and the combined gate is
  green.
- The `record` summary prints `DECISION GATE` when a run used more than 30
  runner-hours or a shard ran longer than 6 hours. Stop and take the numbers to
  the owner before the schedule continues.

### Interaction with `release-v2`

A `release-v2.yml` run that is not completed defers trunk UAT (above). The
release workflow does not cancel a trunk UAT run that is already executing:
its full-UAT fallback and a running trunk UAT run proceed side by side, and
`release-v2.yml` holds no `actions: write` permission. An operator who wants to
stop an in-progress trunk UAT run, for example to free runners before a
release, runs the command by hand with a token that has `actions: write`:

```bash
uv run -- python scripts/trunk_uat.py cancel --reason "release-v2 run <id> full UAT fallback" \
  --summary "$GITHUB_STEP_SUMMARY"
```

The command cancels every unfinished `trunk-uat.yml` run and records the run
ids in the summary. The cancelled run's `record` job still runs and records
`cancelled: release-v2 run <id> in progress (full UAT fallback)`; a cancelled run
is never a skip baseline.

A release reuses a trunk UAT run only when every shard, `gate` and `record` job
of the run's latest attempt succeeded and ran in that attempt. Re-running only
the failed jobs of a run mixes attempts, so the run stops qualifying and the
release runs the full UAT; re-run all jobs to produce reusable evidence. The
newest completed trunk UAT run that executed cells for an ancestor of the tagged
commit decides, and no older trunk run is used after it. When that run is red (a
non-success conclusion, a job that did not succeed in its latest attempt, or
evidence for more than one source commit) or cannot be evaluated because a
GitHub read failed, a final tag also skips the release
candidate fallback and runs the full UAT. When that run is green but does not
match the tag (changed guarded inputs, `uv.lock`, `trunk-uat.yml` or
distribution, or expired artifacts), a final tag may still reuse its release
candidate run.

### Operating it

Run it now on `develop`:

```bash
gh workflow run trunk-uat.yml --ref develop
```

Calibrate a change to the workflow on a branch before it merges: add a temporary
`push` trigger for that branch, push, and record the run's wall time,
runner-hours and per-shard state from the `record` job summary. Remove the
trigger before the pull request. A push run executes unconditionally, uploads
evidence on the branch, and is never the baseline of a `develop` run. A workflow
test fails if the final file has any trigger other than `schedule` and
`workflow_dispatch`.

Rollback: revert the commit that added `trunk-uat.yml`. `release-v2.yml` keeps
its own full-UAT fallback and does not depend on this workflow.

## UAT gate-tuning rubric

The owner approved a standing rubric on 2026-11-05 so that envelope tuning no
longer needs an owner interrupt. This page describes what is automated, what is
still the owner's decision, and how to run each part. The release campaign
procedure is in [UAT release campaign](uat-release-campaign.md).

### What the rubric allows

| Situation | Action | Who decides |
| --- | --- | --- |
| A cell fails only with an infrastructure signature | One automatic retry in the same shard | Automatic |
| A cell's infrastructure-failure rate exceeds 20% over its last 10 runs and another scale of the same platform x benchmark stays gated | Add a runtime envelope entry for that cell | Any maintainer, from the generated proposal |
| That cell is the last gated scale of its platform x benchmark | Never pruned; file a tracker item | Maintainer |
| A cell's only failures in history were infrastructure failures | Proposed for the drop list | Owner, through the choice dialog |
| Product failure: wrong results, validation failure, crash | Never retried, never pruned | Not applicable |
| Anything else (platform-wide exclusion, any drop-list removal) | Owner decision | Owner |

The rubric never changes a timeout, memory limit, validator setting or floor.
`python -m tests.uat.config_guard --base <ref>` compares those values in
`tests/uat/configs/` and `docker/` against a git ref and fails on any
difference; run it against `origin/develop` before opening a PR that touches
UAT.

### Failure signatures

`tests/uat/failure_signature.py` classifies a failed cell.

| Kind | Signature | Evidence |
| --- | --- | --- |
| Infrastructure | `timeout` | The cell hit `per_cell_timeout_s` |
| Infrastructure | `memory` | The cell was killed (exit 137 or -9): the per-cell memory cap or the OOM killer |
| Infrastructure | `download_error` | `DownloadError` or `download failed after` in the failure output, with no failed queries |
| Infrastructure | `singlestore_1712` | SingleStore error 1712 in the failure output, or in every failed query's error |
| Product | `query_failure` | A failed query whose error is not 1712 (a mix of 1712 and other errors counts here) |
| Product | `validation_failure` | Result or schema validation failed, or the validator rollup reports an error |
| Product | `crash` | Any other non-zero exit |

A cell is infrastructure-only when its single failure matches an
infrastructure signature and carries no product evidence. Everything else is a
product failure.

### Automatic retry

A release-gate config enables the retry with
`execute.infrastructure_retry_job_budget_s`, the execute phase's share of the
job time in seconds. The three release-gate configs set it to 19800, which
leaves 15 minutes of the 345-minute step limit for validate, package and
report. A config without the key never retries.

- A failed cell whose signature is infrastructure runs once more with the same
  arguments, the same timeout and the same memory limit.
- No retry starts when the remaining budget is below `per_cell_timeout_s`; the
  skipped retry is recorded.
- The cell passes only if the retry passes. A second infrastructure failure
  fails the cell, and a product failure is never retried.
- The cell row in `cells.jsonl` records `attempt`, `retry_signature`,
  `retry_outcome` (`retry_passed`, `retry_failed` or
  `skipped_insufficient_time`) and the first attempt's status, log and elapsed
  time. The first attempt's log stays beside the retry's.
- `uat_gate_summary.json` carries `infrastructure_retry` with `triggered`,
  `retried`, `retry_passed`, `retry_failed`, `skipped_insufficient_time` and
  `retried_by_signature`. Zeros are a valid result. The `singlestore_1712`
  count is the per-run retry count that `singlestore-tpchavoc-10v8-memory`
  needs: error 1712 stays retryable only until that item is done, because it may
  be a plan defect.

### Pinned images

Every image in `docker/**/docker-compose*.y*ml` carries an `@sha256` digest. A
service that builds a local image pins the base image in its Dockerfile
instead. `tests/uat/image_pins.py` rejects floating references, and the
repository test `test_every_uat_compose_image_in_the_repository_is_pinned`
runs it:

```bash
uv run -- python -m tests.uat.image_pins
```

To bump an image, resolve the tag to its index digest and change the reference
in one commit:

```bash
skopeo inspect --raw docker://<image>:<tag> | shasum -a 256
```

Keep the tag next to the digest so the version stays readable. Revert a single
image by reverting its line.

### History reports

`scripts/uat_rubric.py` reads trunk UAT history and writes two reports.
History is a directory of run directories, each holding `cells.jsonl` and
`uat_gate_summary.json` (and `validator_rollup.tsv` when present), for example
the downloaded `release-uat-*` shard evidence artifacts. Dry runs are ignored.

```bash
uv run -- python scripts/uat_rubric.py --history-dir <dir> --output-dir <dir>
```

`uat_rubric_proposals.json`:

- `proposals`: cells above the threshold with another scale still gated. Each
  has an `envelope` with `reason_line` and `evidence_line`, which paste into
  `_RELEASE_GATE_RUNTIME_ENVELOPES` and `_RELEASE_GATE_RUNTIME_ENVELOPE_EVIDENCE`
  in `tests/uat/compatibility.py`, and a `tracker_item` draft.
- `tracker_item_suggestions`: flaky cells that are the last gated scale. Fix the
  infrastructure instead of pruning.
- `held_for_product_signal`: flaky cells that ever failed with a product
  signature; they are not proposed.
- `red_shards`: how many red shards there were and how many carried a product
  signature, for the dashboard.

`uat_signal_record.json`:

- `cells`: per cell, the runs, the passes and the product and infrastructure
  failures by signature. `defect_classes_caught` lists the product signatures
  the cell has ever caught.
- `drop_list`: cells with at least one infrastructure failure and no product
  failure over at least 10 runs, status `proposed_for_owner_review`. Present
  the first real drop list to the owner through the choice dialog before
  dropping any cell. Last gated scales appear under `kept_last_gated_scale`.

Window, threshold and minimum history are the `--window`, `--threshold` and
`--min-runs` options; the defaults are the rubric's 10 runs and 20%. A cell with
fewer runs than the window is not judged. An absorbed retry counts as an
infrastructure failure of that run. A cell is gated when the release-gate stage
configs (`--stage-config`) still enumerate it.

### Filing rubric items

Tracker items filed from these reports use the title prefix `UAT envelope:` and
list `uat-rubric` among their links, so the dashboard can count them. The
`tracker_item` drafts in the proposals report already follow that shape.

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
and recovery. This repository holds no fork-write credential and runs no refresh
of its own.

## Recovering from common failures

- **A `release-v2.yml` gate fails before publication** (`admit`, the install
  matrix, the UAT evidence check, the canary check, the fallback release UAT, or
  the release-blocking issue check): fix the cause on
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
