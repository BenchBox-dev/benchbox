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
Resolving an oracle review thread starts no workflow run, so after resolving
threads run `gh workflow run oracle-gate.yml --ref develop -f pr=<number>` (or
wait for the next sweep, which runs on every push to `develop` and on a schedule) and merge only after `oracle-gate` passes on the
current head.

To refresh the gate, dispatch it; `make pr-rerun` re-runs CI and does not
refresh `oracle-gate`. Withhold readiness before withdrawing accepted review
evidence, such as deleting a stand-in comment: leave the PR unarmed, withdraw
the evidence, dispatch the gate, and observe the new result on the current head
before treating the PR as ready.

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

The `develop` ruleset has no bypass actors, so a bypass means relaxing the
ruleset. An agent may do this only after the owner approves the bypass for that
PR in the current request. Then:

1. Post a PR comment quoting the owner's approval and stating which check is
   bypassed and why.
2. Relax the narrowest rule, merge the one PR, and restore the ruleset
   immediately. Confirm with
   `gh auth token | uv run -- python scripts/ruleset_drift_check.py --token-stdin`.
3. Open an issue for the failure the bypass skipped, and link it from the PR.

Do not bypass a check for a problem another open PR already fixes; rebase onto
the fix instead.

When the failing check is itself broken by the contents of `develop`, so that
no PR can pass it, follow `docs/agent/emergency-merge.md`. It gives agents
evidence-and-stop steps only; it does not authorize a ruleset change or a raw
merge.
