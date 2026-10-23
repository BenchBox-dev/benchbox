# Emergency merge for base-pinned gate breakage

`[MERGE-AUTHORITY-001]` in `AGENTS.md` is the authority, and
`docs/agent/merge-authority.md` defines the general bypass rule. This note is
the procedure for one case: a required gate is itself broken by the contents of
`develop`, so no PR can pass it, including the PR that repairs it.

## Why

On 2026-10-14 a stale scope-ledger entry on `develop` failed the comment gate
for every PR. The gate runs the copy of its checker that lives on the base
branch, so the repair PR was judged by the broken checker and could not turn
green. The `develop-squash-only` ruleset has no bypass actors, so even an admin
API merge was refused. The fix was improvised under pressure: a temporary
org-admin `pull_request` bypass, an immediate merge, and a restore. The bypass
then turned out to be unnecessary, because another PR had removed the stranded
entries about eleven hours earlier and the repair PR's gate would have passed
after a rebase. The procedure below makes the confirmation step mandatory and
fixes the order of the remaining steps so nobody has to improvise again.

## When it applies

All of these must hold:

- A required check on `develop` fails for every PR, or for the specific repair
  PR, because of content on the current `develop` head (a base-pinned checker,
  ledger, manifest, or workflow), not because of the PR's own changes.
- The repair PR changes only what is needed to clear that failure.
- No other PR, open or merged, already clears the failure.

A check that fails for a flaky, slow, or externally down reason is not covered.
Re-run it, or report the outage to the owner. A failing `hold` check is never
covered: a hold is released only as `docs/agent/merge-authority.md` describes.

## Who may invoke it

Only the owner starts it, by naming the specific PR and the specific check to
bypass in the current request. A goal such as "unblock the queue", a handoff, a
tracker item, a standing instruction, or an earlier message is not an
invocation. The approval covers one PR at one head SHA. A new push, or a second
PR, needs a new approval.

Agents never invoke it on their own initiative, post a stand-in approval to
avoid it, lift a hold to avoid it, or reuse an approval for another PR. An
agent that sees every PR blocked reports the evidence to the owner and stops.

The procedure does not change who names the PR. Bypassing the failed gate does
not waive the other required checks: every context that can still pass must
pass, and `oracle-review` and `hold` keep their normal meaning.

## Evidence to record before any bypass

Post one PR comment before the ruleset changes, containing:

1. The owner's approval, quoted, with the PR number and head SHA.
2. The name of the failing check and the run URL of a failure on the repair
   PR's current head.
3. A failure on the current `develop` head, from a run created after the
   latest `develop` push: a `workflow_dispatch` or trunk run on `develop`, or a
   run on an unrelated PR rebased on that head. Record the `develop` SHA and the
   run URL. If the check no longer fails on current `develop`, stop: rebase the
   PR onto `develop` and let the gate run normally.
4. The list of open and recently merged PRs that touch the failing path, and
   why none of them clears the failure. Check with
   `gh pr list --state all --search "<path or check name>" --limit 20` and
   `git log origin/develop -- <path>`.
5. The exact rule being relaxed and the restore command.

## Procedure

Run these from a linked worktree. Replace `<n>` and `<sha>`.

1. Save the live ruleset so the restore is exact:

   ```bash
   gh api repos/BenchBox-dev/BenchBox/rulesets/15611785 > /tmp/develop-ruleset-before.json
   jq '{bypass_actors, enforcement}' /tmp/develop-ruleset-before.json
   ```

   Confirm `bypass_actors` is `[]` and `enforcement` is `active`. If either
   differs, stop and report it: the ruleset already drifted.

2. Announce the window by commenting on the PR, `Emergency merge window open:
   ruleset relaxed for #<n> at <sha>`. The comment is the public record that
   the ruleset is relaxed; the window is closed by the comment in step 5.

3. Relax only the bypass list, as a `pull_request` bypass for organization
   admins. Leave every rule, required context, and the enforcement mode as is:

   ```bash
   jq '{name, target, enforcement, conditions, rules,
        bypass_actors: [{actor_id: 1, actor_type: "OrganizationAdmin", bypass_mode: "pull_request"}]}' \
     /tmp/develop-ruleset-before.json \
     | gh api -X PUT repos/BenchBox-dev/BenchBox/rulesets/15611785 --input -
   ```

4. Merge the one PR, pinned to the approved head. This is the only raw
   `gh pr merge` the procedure permits, because `make pr-arm` waits for the
   required checks that cannot pass:

   ```bash
   gh pr merge <n> --squash --admin --match-head-commit <sha>
   ```

   Do this in the same command batch as step 3 and step 5. Do not leave the
   ruleset relaxed while waiting for CI, a review, or the owner.

5. Restore immediately, whether or not the merge succeeded:

   ```bash
   jq '{name, target, enforcement, conditions, rules, bypass_actors}' \
     /tmp/develop-ruleset-before.json \
     | gh api -X PUT repos/BenchBox-dev/BenchBox/rulesets/15611785 --input -
   ```

6. Verify the restore. All three must pass:

   ```bash
   gh api repos/BenchBox-dev/BenchBox/rulesets/15611785 \
     --jq '{enforcement, bypass_actors: [.bypass_actors[]?.actor_type]}'
   gh auth token | uv run -- python scripts/ruleset_drift_check.py --token-stdin
   gh pr view <n> --json state,mergeCommit
   ```

   The first must show `enforcement: active` and no bypass actors, and the
   drift check must exit 0.

7. Close the window with a PR comment stating the merge commit, the restore
   verification output, and the time the ruleset was relaxed and restored.
   Report the same to the owner.

If the restore fails or the verification does not match, stop everything else
and tell the owner at once. Retry the restore from the saved file; do not
continue with other work while the ruleset is relaxed.

## Undoing a bypass merge

The merge is a normal commit on `develop`. To undo it, use `make trunk-revert`.
Reverting does not need a new break-glass if the revert PR can pass the gates;
if the gate is still broken, the revert is another invocation and needs its own
owner approval.

## Follow-up requirement

A bypass is not complete until a follow-up issue exists and is linked from the
PR. The issue records:

- why the gate was broken by the base, and why the gate was not able to pass
  the repair PR;
- the failure the bypass skipped, if any check was bypassed rather than fixed;
- the change that prevents a repeat, for example running the repair PR against
  the head's copy of the checker, or a gate that rejects stale ledger entries
  at the time they are stranded.

The owner decides whether the issue becomes a tracker item. The agent does not
close the issue and does not describe the incident as resolved until the
follow-up fix lands.

## Review of this procedure

Check this note after any use. If a step was wrong or unusable, correct it in a
PR the owner reviews. Do not edit it in the same PR as a bypassed merge.
