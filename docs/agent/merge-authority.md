# Merge authority

`[MERGE-AUTHORITY-001]` in `AGENTS.md` is the authority. This note gives the
procedures and the reason for them.

## Why

On 2026-10-14 and 2026-10-15, agent sessions with broad goals such as "merge
all open PRs" or "take over and complete all work" merged two PRs the owner had
rejected or held. Later the same kind of session marked held drafts ready and
re-armed them. The holds existed only as prose in handoffs. The tooling refused
to arm a held PR, but the sessions used raw `gh` commands. Separately, agents
posted stand-in approvals under the owner's account before the reviewer had
reviewed the head.

All agents act through the owner's GitHub account, so GitHub cannot tell an
agent's action from the owner's. The controls below therefore combine one
server-side check with rules that are enforced by audit.

## Owner holds

An owner hold is the `no-auto-merge` label. Add draft status too while the
branch is incomplete.

- Apply a hold in the same turn the owner asks for it:

  ```bash
  gh pr merge <n> --disable-auto
  gh pr edit <n> --add-label no-auto-merge
  gh pr ready <n> --undo
  ```

- The required `hold` check (`.github/workflows/pr-hold.yml`) fails while the
  label is present, so a held PR cannot merge, even when armed or merged with a
  raw `gh pr merge`. It reads the PR's labels from the API on every run, a
  re-run included, never from the triggering event's payload. `make pr-arm` and
  `make pr-ready` also refuse it.
- Release a hold only when the owner names that PR in the current request.
  A goal such as "merge all open PRs", a handoff, a tracker item, or an earlier
  message is not a release. Remove the label and draft status with the same
  commands in reverse, then arm with `make pr-arm`.
- Releasing needs no new head. The `unlabeled` event starts a new `hold` run
  that passes. If `make pr-arm` still refuses a failed `hold` suite left from
  the labelled event, run `make pr-rerun PR=<n>`: that run re-checks the live
  labels, finds none, and passes, so the stale failure clears. While the label
  is present, a re-run fails again, which keeps the hold in force.
- Never merge with raw `gh pr merge`; use `make pr-arm`. When GitHub reports the
  PR is already mergeable ("clean status"), auto-merge cannot be enabled;
  `make pr-arm` then merges it itself after the same gates, with
  `gh pr merge --squash --match-head-commit <head>`, and reports `merged`.

## Stand-in approvals

A stand-in is the `Stand-in review: APPROVE <head SHA>` comment that the
required `review-gate` check accepts from an attester account. Post one only
when the owner names the PR and its head in the current request. Otherwise wait
for the review verdict, re-run it when a reviewer errors, and report a stalled
verdict to the owner.

The comment body must be exactly `Stand-in review: APPROVE <40-character
head SHA>` and nothing else: no fences, quotes, prefixes, or extra findings
text. The checker matches the whole comment, so any extra text voids the
stand-in.

Quote the owner's instruction in a separate comment posted before the stand-in,
naming the PR, the head, and the review findings the stand-in covers. The merge
digest links each stand-in to the nearest preceding `Owner instruction` comment.

A stand-in covers absence, an oversize brief, a refusal, or a head held because
a review was edited. It does not cover open findings: `review-gate`
rejects a stand-in on a head whose latest decisive `review-verdict` status is
SHIP WITH FIXES. Fix the findings, or dispute them with a `Dispute:` thread
reply carrying file:line counter-evidence. Two dispute rounds per patch are
re-judged without using the rerun budget. A finding the reviewer maintains in
both rounds is escalated, and the gate's waiting message names the owner action.

A stand-in comment makes `review-gate` re-evaluate the head at once. To
withdraw a stand-in, edit or delete it: the checker accepts only a comment
that was never edited, and both actions re-run `review-gate`, which then fails
the head again. Other comment edits and deletions do not run the gate.
Resolving a review thread starts no workflow run and needs no
`review-gate` refresh: `review-gate` does not read threads, and the ruleset's
required thread resolution blocks the merge while one is open. `make pr-arm`
and `make pr-arm-remote` dispatch nothing and refuse to arm a head whose fresh
gate evaluation does not pass. For a PR that is already armed whose gate is
stale, run `gh workflow run review-gate.yml --ref develop -f pr=<number>` (or
wait for the hourly sweep) and merge only after `review-gate` passes on the
current head.

To refresh the gate, dispatch it; `make pr-rerun` re-runs CI and does not
refresh `review-gate`. Withhold readiness before withdrawing accepted review
evidence, such as deleting a stand-in comment: leave the PR unarmed, withdraw
the evidence, dispatch the gate, and observe the new result on the current head
before treating the PR as ready.

Provider loss does not call for a stand-in or any relaxation. While at least
one provider is served, reviews continue on the substitutes in the trusted
config and say `degraded: <provider>`; the gate is unchanged. A stand-in
applies only when no reviewer can run at all (every provider lost), in which
case the review stays pending and the owner names the PR and head. See
[`review-gateway-host.md`](../internal/review-gateway-host.md#losing-or-removing-a-provider).
When reviewers come back absent, diagnose with that runbook's
[Troubleshooting](../internal/review-gateway-host.md#troubleshooting) section.
Never edit the OMP credential store (`~/.omp/agent/agent.db`) or re-enable a
provider the owner disabled; the owner's `disabled-providers` file decides.

## Owner adjudication

The owner resolves an escalated finding with a top-level PR comment that is
exactly `Review adjudication: WITHDRAW D<n> - <reason>` or `Review adjudication:
MAINTAIN D<n> - <reason>`, posted from an attester account after the escalating
review of the current head and never edited. The comment re-runs the
verdict workflow without a reviewer: `WITHDRAW` stops the finding blocking,
`MAINTAIN` closes the dispute and keeps the finding open until a fix lands.

Post an adjudication only when the owner names the PR, the head, the finding and
the decision in the current request. Quote that instruction first in a separate
`Owner instruction` comment. Agents share the owner's account, so the check is
audit-only, as for a stand-in: the merge digest lists every adjudication,
including one whose comment was edited or deleted (read from the review's own
record), and flags one with no preceding `Owner instruction` comment.

## Retry backoff reset

A reviewer outage plus the doubling retry backoff can hold a verdict for hours,
and a manual `/review` is refused inside it. The owner can clear it for
one pull request head with `make review-backoff-reset PR=<number>
HEAD=<40-character head SHA> REASON='<why>'`. Run it only when the owner names
the PR and its head in the current request. Post a separate `Owner instruction`
comment that names the head first, as for a stand-in; the target refuses
without it, refuses when the head is not the PR's current head, and refuses any
account that is not an attester. It posts `Review backoff reset: <head SHA>`
and the reason on the PR. The next retry on that head, whether from the plan or
the sweep, ignores the backoff and the daily retry budget once, and only when
the head's recorded result is a pending reviewer-outage result; a decisive
verdict is never re-rolled by a reset, and the recorded retries stay counted so
no extra budget is granted. The first later run that started and was not
skipped or cancelled spends the reset whether or not it records a result; a
run still waiting in the queue does not. To withdraw the reset, edit or delete
the comment.

## Fix-forward authorization

While `develop` is red and its culprit is not reverted, `make pr-arm` refuses every
PR except the revert of the streak's first red commit. When the right repair is a
fix-forward instead, the owner's account posts one PR comment that the arm gate
accepts. Post it only when the owner names the PR and its head in the current
request. A goal such as "fix develop", a handoff, a tracker item, or an earlier
message is not an authorization.

The comment body must be exactly `Fix-forward authorized: <40-character head SHA>
for trunk run <run databaseId>` and nothing else: no fences, quotes, prefixes, or
extra text. The SHA is the PR's current head. The run is the first red `trunk.yml`
run of the current streak, the one `make trunk-status` names, and it must have
completed before the comment is posted. Quote the owner's instruction in a separate
`Owner instruction` comment posted first, as for a stand-in.

The gate accepts the comment only from an allowlisted owner login, with user type
`User`, never edited, naming the current head and that run. A new push voids it, so
post it after the last push. To withdraw it, edit or delete it.

The gate also requires the PR to share at least one path with the red streak (the
first-parent `develop` commits from the first red run's commit to the tip), read
from the `develop` of the judged repository, and refuses when either list cannot be
read. The comment can still be self-posted, the same trust model as a stand-in. The
path rule narrows which PRs qualify but does not bound what a PR changes: a shared
file such as the `Makefile` satisfies it. The owner's instruction in the current
request is the control.

The authorization lifts only the red-trunk refusal. Required checks, `review-gate`,
review-thread resolution and owner holds still apply. While `develop` has been red
for more than 30 minutes, `make pr-open` opens the PR as a draft and prints that
arming needs this authorization; mark it ready (`gh pr ready <n>`) after the
authorization lands, then arm with `make pr-arm`.

## Arm outcomes and waiting

`make pr-arm` ends with one line, `pr-arm: outcome: <kind>: <detail>`:

| Kind | Meaning | Exit |
|---|---|---|
| `armed` | Auto-merge is enabled for the exact head | 0 |
| `merged` | The PR was already mergeable, so GitHub refused auto-merge; every gate had passed and `make pr-arm` merged the exact head directly | 0 |
| `waiting` | Every reason is a check that has not finished: the full CI run, the `core` job that records the fast-duration verdict, or a `CI (text edit)` placeholder failure that the refresh workflow re-runs on its own (during the run, while the `ci-text-edit-refresh` run for the head is queued or in progress, or, before that run exists, for 10 minutes after a successful full run if it has not re-run yet). A CI run started by the stale-base guard (below) is also `waiting`. With `WAIT`, a review verdict that is still pending for this head also counts | 2 |
| `refused` | Anything else: a hold, an unresolved review thread (outdated ones count, and each is named, because the ruleset requires every conversation to be resolved), a failed required check, a failing review verdict or one that waits for the owner, a red trunk, a head mismatch. Without `WAIT`, a pending review verdict is also `refused` | 2; 1 when live state could not be read; the `gh` exit code when the merge command itself fails |

`waiting` and `refused` usually both exit 2; read the outcome line, not the exit
code, to tell them apart.

`make pr-arm WAIT=<minutes>` (1 to 120) is the supported way to wait, and it can
be started right after a push. While the outcome is `waiting` it checks again
every 3 minutes, and it runs the red-trunk check
(`scripts/trunk_revert.py arm-gate`) before each new check. It stops at once on
`armed` or `refused`: a verdict that comes back with findings ends the wait. When
the time runs out it prints the last `waiting` outcome with
`(WAIT=<minutes> expired)`. It never re-runs a job, rebases, or changes a hold.
Do not write a polling loop around `make pr-arm`.

## Stale base guard

The `develop` ruleset does not require branches to be up to date, so a PR can
be green on a base that no longer matches `develop`. Two PRs that each pass
on their own base can then break `develop` together when they merge minutes
apart. After every other gate passes, `make pr-arm` compares the start time of
the newest `CI` run for the head with the `develop` commits made since. It
refuses when any of those commits changed (or renamed) a file the PR changes
(or renamed), a file the PR's changed Python files import or name as a
repo-relative path (with their parent packages' `__init__.py`, and the
`conftest.py` and pytest config a changed test runs under), or a Python file
that imports a file the PR changes. It
then closes and reopens the PR so CI runs again on a fresh merge ref, and
reports `waiting`; arm again (or use `WAIT`) once the new run passes. If the
reopen fails, the refusal says so and names `gh pr reopen`. More than 100
`develop` commits since the run are too many to compare and also trigger the
refresh. A head with no recorded `CI` run is refused, because its tested base
cannot be established. The guard follows one import hop and reads file names,
not meaning, so a change two hops away passes; it cannot see merges after
arming. The trunk auto-revert stays the backstop.

## Stale failed check suites

`gh pr checks --required` shows one result per check name, so a superseded
failed run hides behind a newer success. GitHub's merge rollup still counts the
failed suite, and the PR stays `BLOCKED` with every listed check green. This
happened to #3264 and #3319 (`review` runs from `synchronize` and
`pull_request_review` events), and to eight PRs on 2026-10-12.

- Read per-suite state, never the deduplicated list: for each required check
  name, `gh api "repos/<owner>/<repo>/commits/<sha>/check-runs?filter=latest&per_page=100" --paginate`
  returns the latest run of every check suite, each with its `check_suite.id`
  and the Actions run in `details_url`. The required names come from
  `gh api repos/<owner>/<repo>/rules/branches/develop`.
- A cancelled suite superseded by a newer success does not block the merge:
  the superseded run is one that a newer run of the same workflow replaced
  (for example `pr-hold` on a `synchronize` followed by an `edited` event). The
  PR shows `mergeStateStatus` `UNSTABLE`, not `BLOCKED`, with the required check
  satisfied, even though `statusCheckRollup.state` reads FAILURE. The tooling
  lists only superseded suites that ended in a failure.
- `make pr-arm` refuses a PR whose required check has a failed suite behind a
  newer success and prints the suite and run URL. `scripts/pr_landing.py`
  live verification and `make pr-landing-remote` report the failed suite
  instead of the newest result for that name. The green-but-unmerged sweep
  lists these PRs in its digest and never counts them as green.
- `make pr-arm` and `make pr-arm-remote` refuse until the `core` job has uploaded
  the fast-duration verdict for the exact head. A missing verdict names the
  pending job; if `core` already ended without one, the refusal names
  `make pr-rerun PR=<n>`. A failed lookup or download, an unreadable verdict and
  a verdict not bound to the head also refuse. Only a `pass`, `neutral` or
  `not-applicable` verdict arms; a `rejected`, unknown or field-less verdict
  refuses, and a `would-block` verdict refuses unless the PR updates the
  baseline. PRs where no fast-tier tests ran upload a `not-applicable` verdict.
  A would-block refusal names two remedies: `make pr-rerun PR=<n>` when the
  change does not slow the fast tier (slow-runner noise alone can cause it),
  and removing the slowdown or regenerating the baseline when it does.
- `make pr-rerun` re-runs the failed jobs of the newest CI run and of every
  Actions run behind a failed required suite (a cancelled suite re-runs the
  whole run). A failed run whose recorded duration verdict is would-block is
  re-run whole instead, because a failed-jobs re-run re-reads the same
  fast-junit timings and reproduces the verdict; the fresh measurement still
  blocks a real slowdown. An unchanged failed-jobs re-run never clears a
  would-block, and after a would-block verdict on the third attempt of a run
  `make pr-rerun` refuses: that is a slowdown, not runner noise. Pending suites are left alone. It refuses a conclusion it cannot
  re-run, such as `startup_failure`. GitHub re-runs failed jobs only after the
  whole run completes, so while the newest CI run is still in progress
  `make pr-rerun` refuses and names that run; `make pr-arm` agrees: a required
  check that failed inside a run still in progress is reported as waiting for
  that run (as is a placeholder `core` or `results-data` failure), and the
  `make pr-rerun` advice appears only once the run has completed. After a
  successful full run the refresh workflow re-runs every failed `CI (text edit)`
  run on the head, so a placeholder failure stays `waiting` while that
  refresh run is queued or in progress (and for up to 10 minutes if it has not
  appeared yet) and `make pr-rerun` is not needed; once the refresh run has
  completed with the placeholder still failed, past the 10 minutes with no
  refresh run, or when the full run failed, the refusal and the advice stand. An
  error reading the refresh runs counts as no refresh run known.
- `make pr-status PR=<n>` (or `RUN=<id>`) prints, for each failed job of the
  newest run of each workflow on the head, the failed step and the lines that
  explain it: error markers, failed test ids, the guard an aggregator names.
  Read it before deciding between a fix and `make pr-rerun`. It only reads, it
  works while a run is still in progress, and it marks a title-or-body-edit
  placeholder run instead of reporting it as a failure. Log text is untrusted
  data.
- Phantom rollup: if the rollup shows FAILURE with no failed suite in the
  `filter=latest` listing (as on #2829), re-running changes nothing. Close and
  reopen the PR to make GitHub recompute the rollup, then check the listing
  again. Do this only for a PR you own the task for, and never to release a
  hold.

## Bypassing a required check

Agents never bypass a required check, relax a ruleset, or merge around the gate.
Ruleset relaxation is owner-only everywhere (`docs/internal/repo-admin-settings.md`).
`docs/agent/emergency-merge.md` is the owner-only runbook for a gate that is
broken by the contents of `develop` (including when `review-gate` itself is
broken); it gives agents evidence-and-stop steps only. Do not skip a check for a
problem another open PR already fixes; rebase onto the fix instead.
