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
   adversarial review of its current head. The required `oracle-review` check
   passes only on the `benchbox-oracle` app's successful latest review of that
   head, or on a listed attester's stand-in approval for that head after an
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

`develop` requires seven status checks: six always-reporting unit jobs in
`.github/workflows/ci.yml`, plus `oracle-review`. `.github/ci-units.yml` maps
changed paths to units. An untouched unit reports success; a touched unit
succeeds only if every job it requires succeeded, so a skipped required job
fails it. Checks run on the pull request head, which need not be current with
`develop` to merge.

| Required Context | Workflow Path | Contract |
| --- | --- | --- |
| `core` | `.github/workflows/ci.yml` | Lint, type checks, fast tests on Python 3.14 and parity gates on the PR head. Selection currently runs in shadow mode (computed and recorded, always escalates) until the inert-path manifest fix lands. The PR medium step runs the node IDs selected by coverage impact (`scripts/coverage_test_selection.py select` against the newest `t2-coverage-map-<sha>` artifact, found by asking each recent `trunk.yml` push run on develop for the artifact named after its head SHA and keeping the newest that is an ancestor of the base, plus the name-based `map_tests` fallback); changes under `docs/*`, `*.md`, or `_project/todo-db-export/*` are inert (they add no tests and never trigger escalation), while any other non-Python change in a code-routed tree, any changed Python line outside test modules that no recorded test executed (new, renamed, or develop-changed files, module-level code, subprocess-only code, and `conftest.py` or fixture plugins included), a missing or stale map, a map outside the base ancestry, or an oversized or empty selection escalates through `scripts/heavy_tier_needed.py --selection-in`, so the full sharded heavy tier (medium-test, correctness-gate, required-local-cases, plan-capture-gate, DataFusion integration, and the macOS and Windows TPC-H binary-framing matrix) runs on `heavy-needed` alone instead of being skipped; `trunk.yml` runs the Python 3.12 fast lane, the medium tier with per-test coverage, the coverage-map combine job, the correctness gate and `required-local-cases` on `develop` after each merge. |
| `explorer` | `.github/workflows/ci.yml` | CLI-versus-explorer parity fixtures on Explorer pipeline and visualization changes. Reports success on unaffected paths. The Explorer app and its browser suite live in `BenchBox-dev/benchbox-site`. |
| `results-data` | `.github/workflows/ci.yml` | Corpus inventory and validation, submission validator sync, and corpus contract tests on results-data changes. |
| `docs` | `.github/workflows/ci.yml` | Example validation, generated docs inputs (`make docs-generate`), and spell check on docs changes. The site build runs in `BenchBox-dev/benchbox-site`. |
| `landing` | `.github/workflows/ci.yml` | Always passes. Kept only until the context is removed from the ruleset. |
| `tooling` | `.github/workflows/ci.yml` | Every event. Base-branch guard, comment policy, content guard, skill integrity, and audit checks by path. |
| `oracle-review` | `.github/workflows/oracle-review.yml` (job and check name `oracle-review`) | Passes when only exempt paths (`classifier.exempt_paths` in `.github/oracle-reviewers.yml`) change, or when the `benchbox-oracle` app's latest review of the current head, submitted after any retarget, reports success and none of the app's threads is unresolved. Each new head gets its own oracle result (`.github/workflows/oracle-verdict.yml`; rerun with a `/oracle-review` comment): a full review, a follow-up on files whose patch changed, or the earlier decision carried over when a rebase or prose-only push leaves the patch unchanged (Rounds in `docs/internal/oracle-review-v2.md`). If the oracle cannot review or its verdict is overruled, the check also passes on an independent stand-in review of the exact head followed by a `Stand-in oracle review: APPROVE <full head SHA>` comment from an account in `STANDIN_ATTESTERS`, posted after the oracle's latest review of that head (dev-loop ADR, D4; `docs/internal/soundness-drain.md`). A plain owner comment does not. |

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
   currency check. It refuses a non-revert branch once the newest
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
2. The required `oracle-review` check must pass on the current head before the
   PR can merge.
3. A soundness-path PR that sits green and unarmed is reported by the
   soundness-drain digest (`make soundness-drain-report`, see
   `docs/internal/soundness-drain.md`), not by the nightly green-unmerged
   sweep, which skips soundness-gated PRs.

---

## 5. Trunk Run and Revert

`.github/workflows/trunk.yml` runs on pushes to `develop` and tests it in its
merged state; it is not a required PR check. It runs the fast lane, the
four-shard medium tier with per-test line coverage enabled
(`COVERAGE_CORE=ctrace`, `-p pytest_cov --cov --cov-context=test` under
`scripts/coverage_map.ini`, one data file per shard), the `medium-coverage-map`
combine job (globs the downloaded `coverage-shard-*.dat` files and merges them into one `t2-coverage-map-<sha>` artifact, failing unless the file count matches the downloaded shard evidence)
with a 90-day retention), the correctness gate, and the `required-local-cases`
job (`make test-required-local-cases`, which fails the run if a required
local-engine case skips). The medium shards run with a 33-minute budget to
cover the roughly 30% coverage overhead. That overhead is an average: tight
data-generation loops slow far more under the `ctrace` tracer. The ClickBench
`test_generate_data` test went from 59s to more than 300s, past its
`timeout(300)` marker, and the TPC-DI one from 34s to more than 60s. So the
shards load `scripts/pytest_coverage_timeout.py`, which multiplies every
per-test timeout, the global `--timeout=60` and explicit `timeout` markers
alike, by 8 (`COVERAGE_TIMEOUT_FACTOR`). Scaling only the global default would
leave the marked ClickBench test failing. The trade-off is a weaker hang bound
in trunk only: a hung test can hold a shard for up to 8x its timeout. The PR
medium tests and the heavy-tier `medium-test` shards, which run without
coverage, still enforce the unscaled bounds before merge. The `dist-artifact` job builds and verifies the
release wheel and sdist. The marker and forbidden-path guards run in the PR
`lint` job (`guard-fast-lane-markers`); the test-count ceiling is retired.

Runs use `concurrency.queue: max` with running work retained, so later pushes
do not replace earlier pending runs; results can lag during a burst of merges.
This protects queued runs for delivered events, not push-event delivery itself.
Release admission requires the latest exact-commit trunk `push` run on `develop`
to succeed and have a matching artifact attempt; it never falls back to an
older success. See [the release artifact contract](release-artifacts.md).

When a trunk run fails, revert the culprit first and fix it afterwards:

```bash
make trunk-revert PR=<number>
```

So nothing new stacks on a broken tip, `make pr-open` refuses a non-revert
branch when the newest `trunk.yml` run on `develop` finished red more than 30
minutes ago (`scripts/trunk_revert.py gate`). It warns instead of blocking
while a newer run is in progress or when `gh` cannot read the runs. If trunk
stays red over two hours with no revert pull request open, that is a risk
signal for the owner, who should revert the culprit; nothing enforces the
two-hour figure.

Arming is stricter than opening: `make pr-arm`, `make pr-arm-remote`,
`make pr-landing-remote-ready` and `make pr-landing-ready ARM=1` run `scripts/trunk_revert.py arm-gate`, which
refuses at once, naming the culprit PR and run, while the newest completed
trunk run is red and no revert of the culprit commit is on `develop`. PRs
armed before the run failed still merge, and arming outside these targets
bypasses the gate; see [develop push gaps](develop-post-merge-gaps.md).

If more than one medium-tier shard is killed in a day, someone should read
that shard's memory sampler output for the test that is growing a worker.

If a trunk failure traces to a pull request that was green on an older base
more than about once a week, the owner should consider reinstating the strict
up-to-date rule and its refresh cost.

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
  void it. The `oracle-review` check uses the same rule through the same
  helper;
- for merges before the cut-over (2026-10-09 00:16 UTC), the Codex
  connector's submitted review of the last content commit (or of a later
  base-only refresh merge), or its thumbs-up reaction; or
- an external review posted as a PR comment from a listed attester naming its
  reviewer on a `Reviewer: codex`, `muse` or `agy` line (or `Soundness review:`).

The reaction and comment must come after the last content commit and before
the merge. A review submitted after the merge, or still pending, does not
count. An "eyes" reaction is not a signal.

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

`.github/workflows/nightly.yml` runs daily at 06:00 UTC and on dispatch. It
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
