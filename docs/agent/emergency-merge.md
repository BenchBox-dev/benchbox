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

Agents never run, script, or propose any of these, whoever asks and whatever the
outage:

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
5. Stop acting on the blockage. Keep working only on a normal PR that repairs
   the gate: it changes the gate or the data it reads, and it merges through
   `make pr-arm` like any other PR once the gates can pass.

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

## Follow-up

After any owner action:

- The fix PR for the broken gate lands through the normal path, and the
  tracker entry links it. The action is not finished until that PR merges.
- Check that the ruleset still has no bypass actors and enforcement is active.
  This command only reads:

  ```bash
  gh api repos/BenchBox-dev/BenchBox/rulesets/15611785 \
    --jq '{enforcement, bypass_actors: [.bypass_actors[]?.actor_type]}'
  gh auth token | uv run -- python scripts/ruleset_drift_check.py --token-stdin
  ```

  The first must show `enforcement: active` and an empty `bypass_actors` list,
  and the drift check must exit 0. If either differs, report it to the owner;
  do not change the ruleset.
- The owner decides whether a follow-up issue prevents a repeat, for example a
  gate that rejects stale ledger entries when they are stranded.
