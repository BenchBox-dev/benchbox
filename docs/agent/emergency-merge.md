# Base-pinned gate breakage

`[MERGE-AUTHORITY-001]` in `AGENTS.md` is the authority. This note says what an
agent does when a required gate is itself broken by the contents of `develop`,
so that no PR can pass it, including the PR that repairs it. It gives agents no
way to merge around the gate.

## Why

On 2026-10-14 a stale scope-ledger entry on `develop` failed the comment gate
for every PR. The gate runs the checker that lives on the base branch, so the
repair PR was judged by the broken checker. The `develop-squash-only` ruleset
has no bypass actors, so the repair was merged only after an improvised
ruleset change. Later review found the stranded entries had already been
removed by another PR about eleven hours earlier, so the bypass fixed nothing
that was still broken. The procedure below keeps the evidence step first and
keeps every GitHub-side action with the owner.

## What agents never do

This list applies when `develop`'s own content breaks a required gate so that
no PR can pass it. Agents never run, script, or propose any of these in that
case, whoever asks and whatever the outage:

- add a bypass actor to a ruleset, or relax the `develop-squash-only` ruleset
  in any way;
- merge with raw `gh pr merge`, with or without `--admin`;
- post a stand-in approval, or lift a hold, to get around the broken gate.

`docs/agent/merge-authority.md` already requires `make pr-arm` for every merge
and the owner naming each PR. A broken gate changes neither.

## What an agent does

1. Reproduce the failure on current `origin/develop`: fetch, then trigger or
   find a run created after the latest `develop` push, such as a
   `workflow_dispatch` or trunk run on `develop`, or a run on an unrelated PR
   rebased on that head. A failure that does not reproduce is not base-pinned;
   rebase the PR and let the gate run normally.
2. Capture the evidence: the run ids and URLs, the failing check context, the
   `develop` head SHA, and the base commit that introduced the breakage
   (`git log origin/develop -- <path>` on the file the gate reads). List the
   open and recently merged PRs that touch that path, and note whether any of
   them already clears the failure.
3. Record the evidence in the tracker entry for the broken gate. Create the
   entry if none exists, or add the evidence to the existing one, and mark it
   as needing the owner.
4. Announce it: comment the evidence summary on the affected PR and report it to
   the owner in the session.
5. Stop acting on the blockage. Prepare the repair PR: it changes the gate or
   the data it reads, and it goes through oracle review where one is required.
   Report to the owner the exact failing command, the failing run, and the
   `develop` SHA the failure was reproduced on. The repair PR cannot pass the
   broken gate, so it will not become mergeable by waiting or by re-running.
6. Arm and merge nothing during this procedure: no `make pr-arm`, no raw
   `gh pr merge`.
7. Give the owner the pull request number and head SHA of the repair, and take
   the read-only capture so the owner has it before any change, with
   `gh api repos/BenchBox-dev/benchbox/rulesets/15611785 > /tmp/ruleset-15611785-before.json`.

## What the owner does

Any GitHub-side action to unblock merges is the owner's alone and is outside
this note. The owner records the action in a new decision record at the time
it is taken.

The decision records below hold the current policy of zero bypass actors and no
ruleset relaxation:

- `_project/decisions/native-merge-queue-activation-20260822.md:88`
- `_project/decisions/github-org-transfer-benchbox-dev-2026-08-21.md:74-75`
- `docs/internal/github-org-transfer.md:8`
- `_project/decisions/strict-base-refresh-policy-2026-08-14.md:175-176`

Adopting any bypass or ruleset relaxation, even once, supersedes those
accepted records. The new decision record cites them and says which it
supersedes. Without that record, the policy stands.

## Owner sequence

Only the owner performs these, in this order. The owner's steps and the
comparison command are in `docs/internal/repo-admin-settings.md`.

1. Name one pull request and its head SHA, and hold every other armed pull
   request so only that head can merge during the window.
2. Capture the full ruleset JSON before any change.
3. Remove exactly one required context, the failing one, and merge the named
   head.
4. Restore the ruleset immediately after the merge attempt, whether it
   succeeded or failed. This step is unconditional and comes before any other
   action, including retrying the merge, starting other work, or recording
   notes.
5. Read the ruleset back and compare it with the capture using the command
   under Follow-up.
6. Record the action in the new decision record described above.

## Follow-up

After the owner reports the ruleset restored, the agent runs these read-only
checks and reports the output to the owner:

```bash
gh api repos/BenchBox-dev/BenchBox/rulesets/15611785 \
  --jq '{enforcement, bypass_actors: [.bypass_actors[]?.actor_type]}'
gh auth token | uv run -- python scripts/ruleset_drift_check.py --token-stdin
gh auth token | uv run -- python scripts/ruleset_drift_check.py --token-stdin \
  --compare-capture /tmp/ruleset-15611785-before.json
```

The first must show `enforcement: active` and an empty `bypass_actors` list,
the second must exit 0, and the third, which compares the live ruleset with the
capture taken before the change, must exit 0 and print no differing field. It
ignores only `updated_at`, `_links` and `current_user_can_bypass`, and it
compares every required context's `integration_id`. If any differs, the agent
reports the command output to the owner and arms or merges nothing; it does not
change the ruleset.

- The tracker entry links the repair PR and stays open until the repair has
  merged and the check above passes.
- The owner decides whether a follow-up issue prevents a repeat, for example a
  gate that rejects stale ledger entries when they are stranded.
