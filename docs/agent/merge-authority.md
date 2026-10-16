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
required `oracle-review` check accepts from an attester account. Post one only
when the owner names the PR and its head in the current request. Otherwise wait
for the oracle verdict, re-run it when a reviewer errors, and report a stalled
verdict to the owner. Quote the owner's instruction in the stand-in comment.

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
