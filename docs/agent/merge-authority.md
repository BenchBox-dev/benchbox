# Merge authority

`[MERGE-AUTHORITY-001]` in `AGENTS.md` is the authority. This note gives the
procedures and the reason for them.

## Why

On 2026-10-14 and 2026-10-15, agent sessions with broad goals such as "merge
all open PRs" or "take over and complete all work" merged two PRs the owner had
rejected or held. Later the same kind of session marked held drafts ready and
re-armed them. The holds existed only as prose in handoffs. The tooling refused
to arm a held PR, but the sessions used raw `gh` commands. Separately, agents
posted stand-in approvals under the owner's account before the oracle had
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
  raw `gh pr merge`. `make pr-arm` and `make pr-ready` also refuse it.
- Release a hold only when the owner names that PR in the current request.
  A goal such as "merge all open PRs", a handoff, a tracker item, or an earlier
  message is not a release. Remove the label and draft status with the same
  commands in reverse, then arm with `make pr-arm`.
- Never merge with raw `gh pr merge`; use `make pr-arm`.

## Stand-in approvals

A stand-in is the `Stand-in oracle review: APPROVE <head SHA>` comment that the
required `oracle-gate` check accepts from an attester account. Post one only
when the owner names the PR and its head in the current request. Otherwise wait
for the oracle verdict, re-run it when a reviewer errors, and report a stalled
verdict to the owner.

The comment body must be exactly `Stand-in oracle review: APPROVE <40-character
head SHA>` and nothing else: no fences, quotes, prefixes, or extra findings
text. The checker matches the whole comment, so any extra text voids the
stand-in.

Quote the owner's instruction in a separate comment posted before the stand-in,
naming the PR, the head, and the oracle findings the stand-in covers. The merge
digest links each stand-in to the nearest preceding `Owner instruction` comment.

A stand-in comment makes `oracle-gate` re-evaluate the head at once. To
withdraw a stand-in, edit or delete it: the checker accepts only a comment
that was never edited, and both actions re-run `oracle-gate`, which then fails
the head again. Other comment edits and deletions do not run the gate.
Resolving an oracle review thread starts no workflow run. `make pr-arm` and
`make pr-arm-remote` dispatch `oracle-gate` for the PR and head when the head's
`oracle-gate` status is not `success`, so thread resolution before arming needs
no manual step. For a PR that is already armed, run
`gh workflow run oracle-gate.yml --ref develop -f pr=<number>` (or wait for the
next sweep, which runs on every push to `develop` and on a schedule) and merge
only after `oracle-gate` passes on the current head.

To refresh the gate, dispatch it; `make pr-rerun` re-runs CI and does not
refresh `oracle-gate`. Withhold readiness before withdrawing accepted review
evidence, such as deleting a stand-in comment: leave the PR unarmed, withdraw
the evidence, dispatch the gate, and observe the new result on the current head
before treating the PR as ready.

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

The authorization lifts only the red-trunk refusal. Required checks, `oracle-gate`,
review-thread resolution and owner holds still apply. While `develop` has been red
for more than 30 minutes, `make pr-open` opens the PR as a draft and prints that
arming needs this authorization; mark it ready (`gh pr ready <n>`) after the
authorization lands, then arm with `make pr-arm`.

## Stale failed check suites

`gh pr checks --required` shows one result per check name, so a superseded
failed run hides behind a newer success. GitHub's merge rollup still counts the
failed suite, and the PR stays `BLOCKED` with every listed check green. This
happened to #3264 and #3319 (`oracle-review` runs from `synchronize` and
`pull_request_review` events), and to eight PRs on 2026-10-12.

- Read per-suite state, never the deduplicated list: for each required check
  name, `gh api "repos/<owner>/<repo>/commits/<sha>/check-runs?filter=latest&per_page=100" --paginate`
  returns the latest run of every check suite, each with its `check_suite.id`
  and the Actions run in `details_url`. The required names come from
  `gh api repos/<owner>/<repo>/rules/branches/develop`.
- `make pr-arm` refuses a PR whose required check has a failed suite behind a
  newer success and prints the suite and run URL. `scripts/pr_landing.py`
  live verification and `make pr-landing-remote-*` report the failed suite
  instead of the newest result for that name. The green-but-unmerged sweep
  lists these PRs in its digest and never counts them as green.
- `make pr-rerun` re-runs the failed jobs of the newest CI run and of every
  Actions run behind a failed required suite (a cancelled suite re-runs the
  whole run). Pending suites are left alone. It refuses a conclusion it cannot
  re-run, such as `startup_failure`.
- Phantom rollup: if the rollup shows FAILURE with no failed suite in the
  `filter=latest` listing (as on #2829), re-running changes nothing. Close and
  reopen the PR to make GitHub recompute the rollup, then check the listing
  again. Do this only for a PR you own the task for, and never to release a
  hold.

## Bypassing a required check

Agents never bypass a required check, relax a ruleset, or merge around the gate.
`docs/agent/emergency-merge.md` is the owner-only runbook for a gate that is
broken by the contents of `develop`; it gives agents evidence-and-stop steps
only. Do not skip a check for a problem another open PR already fixes; rebase
onto the fix instead.
