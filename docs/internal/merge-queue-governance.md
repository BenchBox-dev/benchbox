# Merge and trunk governance

This document defines how a change reaches `refs/heads/develop` and how
`develop` stays green afterwards: required checks, review of result-affecting
changes, auto-merge, the post-merge `trunk.yml` run, the revert-first policy
and the nightly run. The repository uses no GitHub merge queue and no strict
up-to-date rule; the file keeps its original name so existing links work.
Decision and evidence:
`_project/decisions/merge-queue-retirement-2026-10-03.md`.

---

## 1. Governance Invariants

1. **Squash Integration Only:** Every pull request into `develop` must be
   squash-merged. Merge commits and rebase-and-merge remain forbidden.
2. **Zero Bypass Actors:** The `develop-squash-only` ruleset sets
   `bypass_actors: []`: no user, bot or organization admin may bypass its
   status checks, linear history or required thread resolution. Code-owner
   review is disabled.
3. **Soundness Review Boundary:** A result-affecting pull request (one that
   changes a path in `.github/soundness-paths.txt`) needs a completed external
   adversarial review of its current head. The required `oracle-gate` check
   passes only on a successful latest `oracle-verdict` commit status that the
   `benchbox-oracle` app created for that head, or on a listed attester's stand-in approval for that head after an
   independent review. The reviewer posts Critical and High findings as PR
   review threads, and required thread resolution makes them binding. A
   scheduled digest lists merged soundness-path commits with each one's review
   signal, and opens a tracker item for any with none. There is no PR-body
   attestation: the author can write that text, so it cannot bind, and the
   `soundness-flag` check that tested for it is deleted.
4. **Fail-Closed Execution:** A required check that cannot establish its
   result must report failure, not success.

---

## 2. Required Status Checks Contract

`develop` requires five always-reporting unit jobs in
`.github/workflows/ci.yml`, plus `hold` and `oracle-gate`. `.github/ci-units.yml` maps
changed paths to units. An untouched unit reports success; a touched unit
succeeds only if every job it requires succeeded, so a skipped required job
fails it. Checks run on the pull request head, which need not be current with
`develop` to merge.

| Required Context | Workflow Path | Contract |
| --- | --- | --- |
| `core` | `.github/workflows/ci.yml` | Lint, type checks, fast tests on Python 3.14 and parity gates on the PR head. Selection currently runs in shadow mode (computed and recorded, always escalates). The PR medium step runs the node IDs selected by coverage impact (`scripts/coverage_test_selection.py select` against the newest `t2-coverage-map-<sha>` artifact, found by asking each recent `trunk-coverage.yml` develop run (push, scheduled or dispatched) for the artifact named after its head SHA and keeping the newest that is an ancestor of the base, plus the name-based `map_tests` fallback); tests that failed in the map's shards are always included; changes to paths listed as inert by `.github/inert-paths.txt` are inert (they add no tests and never trigger escalation; machine-read documents instead select the tests that read them from `_project/inert-doc-readers.json`, escalating only when nothing reads them), while any other non-Python change in a code-routed tree, any changed executable statement that no recorded test executed (changed blank, comment, and docstring lines are ignored rather than counted uncovered; new, renamed, or develop-changed files, module-level code, subprocess-only code, and `conftest.py` or fixture plugins included), a missing, stale, version-mismatched, or non-ancestor map, an unreadable durations file, a selection whose estimated runtime exceeds the cap or whose node count exceeds the argv guard, or an empty selection escalates through `scripts/heavy_tier_needed.py --selection-in`, so the full sharded heavy tier (medium-test, correctness-gate, required-local-cases, plan-capture-gate, DataFusion integration, and the macOS and Windows TPC-H binary-framing matrix) runs on `heavy-needed` alone instead of being skipped; `trunk.yml` runs the Python 3.12 fast lane, the uninstrumented medium tier with real per-test timeouts, the correctness gate and `required-local-cases` on `develop` after each merge, while `trunk-coverage.yml` runs the instrumented coverage shards and the map combine job whose `t2-coverage-map-<sha>` artifact feeds PR selection. |
| `explorer` | `.github/workflows/ci.yml` | CLI-versus-explorer parity fixtures on Explorer pipeline and visualization changes. Reports success on unaffected paths. The Explorer app and its browser suite live in `BenchBox-dev/benchbox-site`. |
| `results-data` | `.github/workflows/ci.yml` | Corpus inventory and validation, submission validator sync, and corpus contract tests on results-data changes. |
| `docs` | `.github/workflows/ci.yml` | Example validation, generated docs inputs (`make docs-generate`), and spell check on docs changes. The site build runs in `BenchBox-dev/benchbox-site`. |
| `tooling` | `.github/workflows/ci.yml` | Every event. Base-branch guard, comment policy, content guard, skill integrity, and audit checks by path. |
| `oracle-gate` | `.github/workflows/oracle-gate.yml` (commit status posted by the `benchbox-oracle` app from `develop` code; the ruleset binds the context to that app) | Passes when only paths listed as inert by `.github/inert-paths.txt` (see Inert-path manifest in `docs/internal/oracle-review-v2.md`) change, or when the latest `oracle-verdict` commit status on the current head, created by the `benchbox-oracle` app after any retarget, is success and none of the app's threads is unresolved. Each new head gets its own oracle result (`.github/workflows/oracle-verdict.yml`; rerun with a `/oracle-review` comment): a full review, a follow-up on files whose patch changed, or the earlier decision carried over when a rebase or prose-only push leaves the patch unchanged (Rounds in `docs/internal/oracle-review-v2.md`). If the oracle cannot review or its verdict is overruled, the check also passes on an independent stand-in review of the exact head followed by a `Stand-in oracle review: APPROVE <full head SHA>` comment from an account in `STANDIN_ATTESTERS`, posted after the oracle's latest review of that head (dev-loop ADR, D4; `docs/internal/soundness-drain.md`). A plain owner comment does not. The `oracle-review` workflow (`.github/workflows/oracle-review.yml`) reports no check; it only notifies `oracle-gate` of review events. |

### Shadow selection miss report

`core` also measures the shadow selection. After the heavy medium-test shards finish, the aggregate downloads the shard junit reports and the `coverage-selection-<pr>-<sha>` artifact, runs `scripts/coverage_test_selection.py misses`, and writes a `Shadow selection misses` section to the step summary plus a `coverage-selection-misses-<pr>-<sha>` artifact (`selected_count`, `failed`, `missed`, `selection_escalates`, `caught_by_escalation`, `would_escalate`). A failed test counts as missed when neither its node ID nor its bare file path is in the selection and the selection would not have escalated to the full tier; a selected sibling node does not cover it. A selection that would escalate runs every medium test, so its failures are listed under `caught_by_escalation` and not as misses. When the selection or any junit report is missing or unparseable, the step reports `not available` with no escalation verdict instead of a clean bill. The step reports only and never fails the job.

Four pull requests whose regressions reached Trunk without a PR run executing the failing test (#3018, #3066, #3122, #3185) are pinned as replays in `tests/unit/scripts/test_selection_pr_replays.py`. Each fixture in `tests/fixtures/ci/selection_replays/` holds the PR's diff hunks, the failing test ids, and the per-line coverage hits of a real per-test coverage run of the failing test file (with a `provenance` block naming the base, the command and the digest of the full map); other tests that cover the same lines appear as one recorded stand-in node. The case for #3018 has no coverage hits because its failing test was deleted with the site; it pins the routing and non-Python escalation instead. Each replay must select the failing test or escalate to the full tier, and the miss report must list those failures as caught. When the miss report is unavailable it claims no escalation catches (`selection_escalates` is null).

The coverage selection step in `ci-paths` should finish within 60 seconds and 3 GB peak RSS on that runner; it already prints `elapsed=` and `rss=`. Measured on PR #3264's CI run 39794063479: 21.6 s and 1384.5 MB with the v2 map, against 44.6 s and 2.2 GB for the v1 map.

---

## 3. Merge Path

A pull request is armed with `make pr-arm`, which enables auto-merge for the
exact head. GitHub squash-merges the pull request once every required check is
green on that head and all review threads are resolved. No queue or
combined-tree build runs first. A branch behind `develop` merges without a
refresh unless it conflicts. A changed head needs fresh CI and a fresh oracle
review or stand-in approval before re-arming.

Slow-marked reproducer jobs stay required in PR CI through the `core` unit,
because the post-merge run has no slow-signature lane.

---

## 4. Developer Workflow

### A. Submitting & Arming a PR

Use the standard Makefile targets:

```bash
make pr-open
make pr-ready PR=<number> HEAD=$(git rev-parse HEAD) EVIDENCE=<readiness.json>
```

1. `make pr-open` opens or reuses the pull request against `develop`, with a
   currency check. It opens a non-revert branch as a draft once the newest
   `trunk.yml` run on `develop` has been red for more than 30 minutes (see
   section 5).
2. When the PR is ready to merge, `make pr-ready` runs the exact readiness
   transaction: it verifies the exact checkout, live PR identity, review
   state, required checks, holds and readiness evidence, then arms
   auto-merge.
3. GitHub merges the PR once the required checks are green on the head.

### B. Soundness Path Withholding

If a PR changes any soundness path (for example `benchbox/core/equivalence/`,
`benchbox/core/expected_results/` or `soundness_paths.py`):

1. Withdraw readiness before editing an armed PR
   (`make pr-landing-withdraw PR=<n> HEAD=<sha>`). After the last push, get
   CI and the oracle's success review (or the stand-in approval) on that exact
   head, then arm with `make pr-arm`. The `no-auto-merge` label blocks arming
   only; to hold an armed PR, disable auto-merge
   (`gh pr merge <n> --disable-auto`) or withdraw it as above, then add the
   label.
2. The required `oracle-gate` check must pass on the current head before the
   PR can merge.
3. A soundness-path PR that sits green and unarmed is reported by the
   soundness-drain digest (`make soundness-drain-report`, see
   `docs/internal/soundness-drain.md`), not by the nightly green-unmerged
   sweep, which skips soundness-gated PRs.

---

## 5. Trunk Run and Revert

`.github/workflows/trunk.yml` runs on pushes to `develop` and tests it in its
merged state; it is not a required PR check. It runs the fast lane, the
four-shard medium tier without coverage instrumentation and with real per-test
timeouts (`--timeout=60`, no `pytest_cov`, no timeout plugin, no `COVERAGE_*`
environment), sharded by the same duration weights as PR CI
(`tests/fixtures/medium_shard_durations.json`), the correctness gate, and the
`required-local-cases` job (`make test-required-local-cases`, which fails the
run if a required local-engine case skips). The medium shards run with a
25-minute budget, matching the uninstrumented PR heavy tier. The `dist-artifact`
job builds and verifies the release wheel and sdist. The marker and
forbidden-path guards run in the PR `lint` job (`guard-fast-lane-markers`);
the test-count ceiling is retired.

`.github/workflows/trunk-coverage.yml` runs on a schedule (six producer runs a
day at `17 1,5,9,13,17,21 * * *` UTC) and on `workflow_dispatch`, not on every
push to `develop`, and publishes the coverage map that PR selection reads; it
is not a required PR check and no arming gate consults it (`trunk.yml` runs on
every push that is not replaced while waiting and remains what `scripts/trunk_revert.py` reads). A
scheduled run checks out the `develop` tip at trigger time, so the map can lag
`develop` by up to one cadence interval plus run time; the cadence, the
acceptable map age and the cron itself are owner decisions. It runs the same
four-shard medium tier with per-test line coverage enabled
(`COVERAGE_CORE=ctrace`, `-p pytest_cov --cov --cov-context=test` under
`scripts/coverage_map.ini`, one data file per shard) and a `medium-coverage-map`
combine job (`if: always()` on producer events, `t2-coverage-map-<sha>` artifact with a 7-day
retention) that builds only when all 4 shard data files exist
(`--expected-shards 4`), records the failed test node IDs from the shard junit
reports into the map so `select` always includes them, and fails the run unless
a `fetch-map --base-sha $GITHUB_SHA` self-check resolves the map it just
uploaded; the self-check prints the producer run ID, event and artifact name,
and `fetch-map` returns the same fields (`run_id`, `event`, `artifact`) in its
JSON and as the `map-run-id`, `map-event` and `map-artifact` step outputs.
Selection trusts a map only from a `trunk-coverage.yml` run on `develop` in this
repository whose event is `push`, `schedule` or `workflow_dispatch` and whose
head SHA is an ancestor of the PR base; other workflows, branches,
repositories and non-ancestors are rejected. `push` stays trusted so maps from
before the cutover, and from a rollback, still resolve. The coverage shards run with a 33-minute budget to cover the roughly
30% coverage overhead and use the same durations file as the uninstrumented
tier; the weights therefore skew low for instrumented runs, which is accepted
because the split only needs roughly balanced shards, not exact ones. That
overhead is an average: tight data-generation loops slow far more under the
`ctrace` tracer. The ClickBench `test_generate_data` test went from 59s to more
than 300s, past its `timeout(300)` marker, and the TPC-DI one from 34s to more
than 60s. So the coverage shards load `scripts/pytest_coverage_timeout.py`,
which maps every per-test timeout, the global `--timeout=60` and explicit
`timeout` markers alike, to `min(t * 8, 1200)` (`COVERAGE_TIMEOUT_FACTOR`,
`COVERAGE_TIMEOUT_CAP_SECONDS`). Scaling only the global default would leave
the marked ClickBench test failing. The 1200 s cap is about 1.9x the worst
measured coverage runtime (ClickBench `test_generate_data` at 628 s, Trunk run
39596269177), so a genuinely slow test still passes while a hung one cannot
hold a shard past 20 minutes. The PR medium tests and the gating trunk
`medium-test` shards, which run without coverage, still enforce the unscaled
bounds before merge.

A change to the Trunk lane itself cannot be exercised by an ordinary PR run, so
the PR `ci.yml` calls the Trunk workflows directly. A PR that changes
`.github/workflows/trunk.yml`, `scripts/release_canary_sharding.py`,
`scripts/pytest_shard_evidence.py` or `tests/fixtures/medium_shard_durations.json`
runs the `trunk-lane-plain` job. One that changes
`.github/workflows/trunk-coverage.yml`, `scripts/pytest_coverage_timeout.py`,
`scripts/coverage_map.ini` or any of the other three paths runs
`trunk-lane-coverage`. The changed-path list names both sides of a rename, so
moving one of these files still triggers its lane. Each lane is a
`workflow_call` of the real file with `medium-only: true`, so the PR head runs
the same collection, shard scheduling, environment and coverage setting as the
post-merge run, with the fast lane, correctness gate, distribution build and
coverage map jobs skipped. Their evidence artifacts carry a `trunk-lane-plain-`
or `trunk-lane-coverage-` prefix so they never mix with the PR medium tier's
`t2-medium-*` evidence that `core` verifies. `core` needs both jobs and expects
them to run exactly when their paths changed, so a red or missing run blocks the
merge and unaffected PRs skip both.

Replaying the precedents against the current layout. #3200 put per-test
coverage into the medium shards by editing `trunk.yml`. Coverage now lives in
`trunk-coverage.yml`, so the same change today edits that file or
`scripts/pytest_coverage_timeout.py` and runs `trunk-lane-coverage`, which is
the lane where datagen tests exceeded their unscaled timeouts. #3400 changed
both Trunk workflows and `release_canary_sharding.py`, so both lanes would have
run with module-grouped shard scheduling, the condition under which the
process-global chDB server broke. Both replays are inferred from the changed
paths; the historical heads were not re-run. #2821 changed only the `ci.yml`
`code-test` job and its tests, so no Trunk-lane path matched and this run
cannot replay it; that incident was a missing TypeScript install in the `fast`
job of `trunk.yml`, which the medium-only run does not execute.

The v2 map stores a node table with per-line integer indices, per-file
executable-statement line sets from coverage's own analysis (changed blank,
comment, and docstring lines are ignored rather than counted uncovered), the
setup/call/teardown phase of each hit with setup-phase hits expanded to the
whole test module, and the shard junit failures. `select` rejects older map
versions by escalating with the version mismatch as the reason, and replaces
the node-count cap with an estimated-runtime cap summed from the durations
file (the file default for unknown tests), keeping a node-count guard only
against argv limits. `fetch-map` reports `missing` when no map was published
and `lookup-failed` (with a `::warning::` carrying the API error) when the
lookup itself failed, so a broken query cannot pass as an absent map.

A daily `coverage-map-staleness` job in the same workflow (cron
`47 7 * * *`, `issues: write` only there) runs on its own cron, so the producer
jobs and the monitor never run in the same workflow run. The monitor fires about
two and a half hours after the 05:17 producer was scheduled, so that run has
normally finished, and well before the 09:17 one starts. It opens or updates a single
`ci-coverage-map` labelled issue when the newest map is more than 20 develop
commits behind (the staleness limit, separate from the 50-commit distance
selection tolerates between the map and the PR base) or the last 3 map-producing runs failed, and closes it once
healthy. A map-producing run is a completed `develop` run whose
`medium-coverage-map` job concluded `success` or `failure`; monitor-only,
cancelled and skipped runs are not counted and cannot reset the failure history.
Each counted run is judged by that job's conclusion, not the workflow run's, so a
failed test shard that still published a fresh map does not count toward the
three-failure stale verdict.
A failed lookup, or no completed producer run, yields an `unknown` verdict: the
monitor comments on an open issue and exits non-zero, so a broken interval is
never counted healthy.

Two limits are decided separately. Artifact discovery inspects the newest 60
`trunk-coverage.yml` runs on `develop` (`MAX_DISCOVERY_RUNS`: six producers plus
the monitor is seven runs a day, so 60 runs reach back about eight and a half
days, and a few extra dispatches still leave the window longer than the 7-day
map retention, so discovery runs out of unexpired artifacts before it runs out
of runs). Selection
accepts a map at most 50 develop commits behind the PR base
(`MAX_MAP_BEHIND_COMMITS`); a PR based further ahead of the newest map
escalates with that reason instead of using stale impact data. Failed or
expired production, an unavailable lookup and an older PR base all fail closed
the same way: `fetch-map` reports `missing` or `lookup-failed`, `select`
escalates to the full tier, and nothing selects from a map it could not verify.
Recovery is to fix the producer failure and `workflow_dispatch` the workflow,
which publishes a fresh map on `develop`. Rolling back to per-push production
means restoring `push: branches: [develop]` in the workflow triggers; no script
change is needed because `push` runs remain trusted and the monitor counts
them as producers. Roll back when the staleness issue opens twice within seven
days, or when the newest map is more than 20 commits behind the base on more
than 5% of code PRs. PR queue time is tracked but is not a rollback criterion.

GitHub starts scheduled workflows late and sometimes skips or bunches them (a
daily cron can start hours late, an hourly one can fire only a handful of times
a day). The monitor therefore never infers health from the clock: it judges
only completed producer runs, the newest map's commit distance and the last
three producer results, so an irregular build time changes how soon a problem is
seen, not whether it is seen. A monitor that runs before the day's producer has
finished simply reads the previous completed runs.

Measurement for the schedule change: record for seven days producer and
monitor run outcomes, `fetch-map` outcomes with `run_id`, `event`, map age and
commit distance, and selection escalation reasons, treating any missing
interval as unmeasured rather than healthy. Compare a seven-day pre-merge
baseline against the seven days after, normalised for merge volume and
including dispatch and monitor overhead, using the CI usage report. Raw
before/after totals alone do not decide the change. The 20-PR shadow-miss
window for `cov-select-exit-shadow-20261021` restarts from the merge, since map
freshness changed. The
`tests/conftest.py` coverage warning reads the data file named by
`COVERAGE_FILE` so sharded runs report their own file.

Develop pushes share one `trunk-develop` concurrency group. One run is in flight
and the newest push waits behind it; a newer push replaces a waiting one, and a
started run is never cancelled. A replaced commit has no run of its own, so a red
run covers every commit merged since the last run, and the arm gate and
`make trunk-status` name all of them. A revert of any of those commits is exempt
from the gate, and one revert does not reopen arming for other PRs until a run
is green. Runs started by `ci.yml` and by manual dispatch keep a per-commit group.
This covers runs for delivered events, not push-event delivery itself. See
[the latest-head trunk decision](../../_project/decisions/trunk-latest-head-runs-2026-10-30.md).
Release admission requires the latest exact-commit trunk `push` run on `develop`
to succeed and have a matching artifact attempt; it never falls back to an
older success. See [the release artifact contract](release-artifacts.md).

When a trunk run fails, revert the culprit first and fix it afterwards:

```bash
make trunk-revert PR=<number>
```

So nothing new stacks on a broken tip, `make pr-open` opens a non-revert
branch as a draft, and says arming needs authorization, when the newest
completed `trunk.yml` run on `develop` finished red more than 30
minutes ago (`scripts/trunk_revert.py gate`). A newer run still in progress
does not hide that red. When `gh` cannot read the runs it warns instead of
drafting. Separately, when the newest run (by creation time) has been queued,
waiting or in progress for more than 30 minutes, `make pr-open` and
`make pr-arm` print a warning naming the run and its age and carry on; a slow
run never blocks. If trunk
stays red over two hours with no revert pull request open, that is a risk
signal for the owner, who should revert the culprit; nothing enforces the
two-hour figure.

Arming is stricter than opening: `make pr-arm`, `make pr-arm-remote`,
`make pr-landing-remote-ready` and `make pr-landing-ready ARM=1` run `scripts/trunk_revert.py arm-gate`, which
refuses at once, naming the culprit PR and run, while trunk is red and no
revert of the culprit commit is on `develop`. The culprit lies in the first red
completed run after the last green one: when a run covers several commits
(waiting pushes are replaced, so a run's range reaches back to the last green
run), the gate names every commit in that range, and a failure that persists
across later merges still blames the range that broke it. A revert of any
covered commit is exempt. When the range holds more than one commit, one
revert does not reopen arming for other PRs; only a green run does. If the
range cannot be read in full, the culprit is undetermined and the gate refuses.
The gate widens its run fetch (30, 100, 300, then
1000 runs) until it finds a green run or the run history ends, and refuses
when the streak outruns that bound. An owner-authorized fix-forward comment
(see [merge authority](../agent/merge-authority.md)) also admits one PR at its
current head; required checks, `oracle-gate`, threads and holds still apply. PRs
armed before the run failed still merge, and arming outside these targets
bypasses the gate; see [develop push gaps](develop-post-merge-gaps.md).

Red-streak rule with out-of-order results: runs are ordered by creation time,
never by completion time, and unfinished and cancelled runs are skipped. A
completed green on a later commit clears every earlier red, because that run
tested a tree containing the earlier commit, even when the earlier red finishes
after it. A green on an earlier commit never clears a later red. The streak
starts at the oldest red completed run newer than the newest green. A red with
an unfinished run older than it and no green between is undetermined, so
arming is refused without naming a culprit until that run finishes.

If more than one medium-tier shard is killed in a day, someone should read
that shard's memory sampler output for the test that is growing a worker.

A stale-base incident is a trunk failure where the culprit pull request was
green on its older base and the failure needs a commit that merged after that
base. A failure that rebasing onto the then-current `develop` would not have
prevented (a flaky test, a break that was already on the PR's base, or one
caused by the PR alone) does not count. If two stale-base incidents occur in
one week, the owner should reconsider reinstating the strict up-to-date rule
and its refresh cost.

---

## 6. Post-Merge Soundness Digest

`.github/workflows/soundness-merge-digest.yml` runs daily and on demand. It
runs `_project/scripts/soundness_merge_digest.py`, which reads the
first-parent commits on `develop` since a stored checkpoint and keeps those
that change a path in `.github/soundness-paths.txt`, counting both sides of a
rename. Each commit
is judged by the manifest and predicate (`_project/scripts/soundness_paths.py`)
as they stood at its first parent, so a commit that removes a rule cannot hide
its own change or an earlier one. The manifest, predicate, digest script and
digest workflow are always kept, whatever the manifest says; the script and
workflow are also listed in it.

For each kept commit, the digest finds the pull request that merged into
`develop` as that commit and records which review signal it had at merge for
its final content:

- the `benchbox-oracle` app's latest review, submitted after any retarget,
  of the last content commit or of a later merge that only refreshed the
  base, when that review reports success;
- a stand-in approval of the merged head, posted after any retarget and after
  the oracle's latest decisive verdict (`success` or `failure`) on that head.
  A later oracle review with no decisive verdict, such as `pending`, does not
  void it. The `oracle-gate` check uses the same rule through the same
  helper;
- for merges before the cut-over (2026-10-09 00:16 UTC), the Codex
  connector's submitted review of the last content commit (or of a later
  base-only refresh merge), or its thumbs-up reaction; or
- an external review posted as a PR comment from a listed attester naming its
  reviewer on a `Reviewer: codex`, `muse` or `agy` line (or `Soundness review:`).

The reaction and comment must come after the last content commit and before
the merge. A review submitted after the merge, or still pending, does not
count. An "eyes" reaction is not a signal.

An oracle review counts as a signal only if it was never edited (read through
GraphQL `lastEditedAt` and `includesCreatedEdit`) and its decision agrees with the
App's own `oracle-verdict` status for that head: the status created by the App's Bot account
whose description carries the review marker's `[run <uuid>]` (every App success or
failure when the marker has no run uuid), the one created nearest the review's submission, which must be `success` exactly when the
decision is `SHIP`. A review that fails either check, or whose edit state or statuses cannot be read,
is not counted and is listed under "Unverifiable oracle evidence". The severity of an
oracle finding thread whose first comment was edited, or whose edit state cannot be read, is
likewise not reported and the thread is listed there.

Because authors set commit dates, the digest dates the last content commit by
GitHub's first run of this PR's workflows for it. With no such run (for
example, pushed with `[skip ci]` or runs expired), it uses the first run of a
later commit, then the merge time. Each fallback moves the date later, so it
can report a false gap but cannot hide a real one.

A merge of `develop` into the branch is a refresh, not content, when it has
two parents, one already on `develop`, and its tree equals the mechanical
merge of the parents. A merge is content if it needed conflict resolution,
carries any other change, merges two branches not on `develop`, or has more
than two parents.

A commit gets an issue labelled `soundness-review-gap` when it has no signal,
no merged pull request, or a reviewer thread resolved with no later commit.
The issue body carries a `Reason class` line naming why: `override of an
oracle High finding` (an oracle thread rated Critical or High resolved with no
later commit), `deferred finding` (any other thread resolved with no later
commit) or `no review signal` (no signal, or no merged pull request). An agent
runs the external review and either records a clean result on the issue or
opens a fix or revert pull request.

Every run also re-evaluates each open gap issue, whatever its age. A closure
comment records the evidence and opens with a `soundness-gap-closed` marker.
Two things close an issue, and nothing else does:

- A comment on the issue from a listed attester (`STANDIN_ATTESTERS` in
  `_project/scripts/oracle_review_check.py`, a `User` account) closes any gap
  issue. The comment must start with `External review: codex`, `muse` or
  `agy`, have a line beginning `Verified clean`, and contain no line with a
  `**Critical**: `, `**High**: `, `**Medium**: ` or `**Low**: ` finding. A
  `Reviewer:` or `Soundness review:` line, a heading that is not first, or any
  listed finding does not close it.
- Re-evaluation closes only an issue whose `Why it is listed` line is exactly
  `no completed review after the last content commit`, when the digest now
  finds a review signal for the commit (the signals above, all bounded by the
  merge window). Thread state is never an input: an issue opened for a
  reviewer thread (override of an oracle High finding or deferred finding), for
  a merge with no pull request, or for several reasons at once, and an issue
  whose body cannot be parsed, never closes by re-evaluation, so unresolving or
  deleting a thread changes nothing.

A closed issue is never reopened or edited. A run without `--apply` lists the
issues it would close.

The checkpoint lives in the body of the one issue labelled
`soundness-merge-digest` and must be on the first-parent history of
`develop`. The run fails on a missing issue, a second labelled issue, a body
without the checkpoint marker, or a checkpoint off that history, so a damaged
checkpoint cannot silently skip commits. A failed or incomplete read also
fails the run and leaves the checkpoint unchanged. To record the first
checkpoint, dispatch the workflow with `bootstrap` set; that run reports
nothing, and later runs report the commits after it. Restore a damaged
checkpoint with `--since <sha> --apply`. Run it locally without changing
anything:
`uv run -- python _project/scripts/soundness_merge_digest.py --since <sha>`.

An oracle or connector review names a commit, so it cannot be backdated.
Known limits: a posted review is text the author can write, so it shows a
review was recorded, not what it examined. A run that lists no pull request is
matched to one by repository and branch name, so an author who reuses a branch
name across PRs, shares a commit between them and pushes the final content with
`[skip ci]` can make the date earlier than it was. That needs deliberate
set-up, like posting a review comment that was never written, and the digest
does not defend against it.

---

## 7. Nightly Run

`.github/workflows/nightly-v2.yml` (Nightly T3) runs daily and on dispatch. It
runs the slow and scheduled checks no required check covers, plus the advisory
ruleset drift check, which is also run by hand after a settings change and no
longer runs in `ci.yml` (it ran only in merge groups). A nightly failure never
blocks a merge.

---

## 8. Historical: the Merge Queue

Until 2026-10-03 the repository ran a GitHub merge queue on `develop` with the
strict up-to-date rule. The queue configuration, follower visual baseline
policy and queue canary rehearsal are in
`_project/decisions/native-merge-queue-activation-20260822.md`,
`_project/decisions/visual-baseline-site-equivalent-ancestor-2026-09-27.md`
and `docs/internal/merge-queue-canary-runbook.md`. They no longer apply.
