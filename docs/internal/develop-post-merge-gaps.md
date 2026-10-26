<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Develop push gaps

```{tags} contributor, operations, ci
```

## What failed

GitHub has **dropped push delivery** for consecutive `develop` merges. In
principle every push to `develop` starts every workflow that listens for it. In
practice:

| When | Signal |
| --- | --- |
| 2026-08-03 | Three consecutive develop merges got **no** push workflows at all, which left the `published-results` corpus mirror stale (see the header comment on `corpus-drift-check.yml`). |
| 2026-08-04 | Six consecutive develop merges produced **no** run of the post-merge workflow that existed then. |

The mechanism (GitHub delivery drop, concurrency edge, or API lag) is not
fully proven. What is proven is the **observable gap**: `git log
origin/develop` SHAs with no matching workflow run.

That is a silent failure mode, and every workflow that fires only on `push` to
develop shares it. The full list of those workflows, what each loses when a push
is dropped, and how to recover is in
[`develop-push-drop-inventory.md`](develop-push-drop-inventory.md).

## What changed with trunk.yml

The earlier post-merge workflow (`develop-post-merge.yml`), its hourly tip sweep,
and its daily gap detector (`develop-post-merge-gap-detector.yml`) were retired.
`.github/workflows/trunk.yml` restores validation of the merged tree: the fast
lane, four-shard medium tier, correctness gate and required-local-cases. It also
builds the verified release distribution artifact. A failing trunk run leads to
a revert (`make trunk-revert PR=<n>`). Its `concurrency.queue: max` retains
pending runs instead of replacing them when merges arrive together.

This closes the concurrency-replacement gap for delivered events, not the
event-delivery gap. A push that GitHub never delivers produces no run for that
commit. The next delivered push tests a tree containing it but cannot certify
that earlier commit's release artifact. The fast-lane count baseline and delta
guard are retired. The remaining consequences include:

| Workflow | Loses when its push is dropped | Recovery |
| --- | --- | --- |
| `trunk.yml` | Test evidence and the release distribution artifact for that exact commit. Admission requires a successful trunk `push` run on `develop`; a dispatch or later commit cannot substitute. | Verify that the release candidate has its own successful push run before tagging it; otherwise choose a tested candidate or obtain an owner decision on recovery. |

Check exact-commit workflow evidence before blaming a PR or admitting a release.

## Red trunk stops arming

A red `trunk.yml` run names its culprit, stops the repository's arming
commands, and disarms PRs armed before the red run completed.
`scripts/trunk_revert.py arm-gate` runs first in `make pr-arm`,
`make pr-arm-remote`, `make pr-landing-remote-ready`, and `make pr-landing-ready`
with `ARM=1`, which is what `make pr-ready` and `make pr-arm-auto-merge` reach. It walks the
completed `trunk.yml` runs on `develop` back to the last success, ignoring runs still in
progress and cancelled runs. If the newest completed run failed, `timed_out` or
`startup_failure`, arming is refused with the run URL and the culprit: the first red
run of that streak, not the newest one. The culprit is the PR associated with that
run's head commit through the GitHub commits API, or the bare commit when no merged
PR is associated. The message gives the `make trunk-revert PR=<n>` command.

The refusal lifts when either:

- a newer completed trunk run succeeds, or
- one of the 100 newest `develop` commits, read through the GitHub API, says
  `This reverts commit <culprit sha>`, which `make trunk-revert` writes.

The revert PR itself is exempt: a `fix/revert-*` branch can be armed only if
its own commits carry `This reverts commit <culprit sha>`, so the branch name
alone buys nothing. Unlike `make pr-open`, which waits 30 minutes before
opening new PRs as drafts, arming is refused as soon as the red run completes.

A fix-forward PR is the other way through. While `develop` has been red for more than
30 minutes, `make pr-open` opens a non-revert branch as a draft instead of refusing
it, and says that arming needs authorization. The arm gate admits that PR once the
owner's GitHub account has posted an unedited comment whose whole body is
`Fix-forward authorized: <40-hex head SHA> for trunk run <run databaseId>`, created
after that run completed, with the PR's current head and the first red run of the
streak (the culprit above). A new push, or a streak that starts from a different run,
voids it. The authorization lifts only the red-trunk refusal: required checks,
`oracle-gate`, review-thread resolution and owner holds still apply, and a draft
must still be marked ready before it can be armed. `docs/agent/merge-authority.md`
says when an agent may post it.

Bound:

- **Armed PRs are disarmed while the streak is unreverted.** When a
  `trunk.yml` run on `develop` completes, `trunk-disarm.yml` reuses the
  arm-gate decision in `scripts/trunk_revert.py` and disables auto-merge on
  the armed PRs the gate would refuse. The streak and its culprit are read
  once per run: if that lookup fails, the workflow fails and disarms nothing.
  A per-PR read failure disarms that PR, except a `fix/revert-*` branch,
  which is never touched without proof it is not the genuine revert. The
  remaining window is merges made through the GitHub UI or raw
  `gh pr merge`, and PRs armed after the disarm ran through a path that
  bypasses the gate.
- **Bypass.** The gate lives in the repository's make targets. Arming from the
  GitHub UI or `gh pr merge --auto`, or calling `scripts/pr_arm.py` directly,
  skips it. A required check or ruleset would close this; both are owner
  settings.
- **Fails closed on read errors.** If `gh` cannot read the PR, the trunk
  runs or the recent `develop` commits, arming is refused. The only allowed
  gap is a repository with no `trunk.yml` run yet. `make pr-open` still fails
  open.
- **A flaky red run names an innocent PR.** The remedy is to rerun trunk to
  green; a revert is not required.
- **The authorization is audit-enforced.** Every agent acts through the owner's
  account, so no API signal separates the owner's comment from an agent's. The gate
  binds the comment to the head and the run, never to who typed it. The gate also
  requires the PR to share at least one path with the red streak, so a self-posted
  comment narrows to PRs that share a path with the streak's commits. That does not
  bound what the PR changes, because a shared file such as the `Makefile` qualifies;
  the owner's instruction in the current request is the control.
- **Delivery.** A push that GitHub never delivers produces no run, so the gate
  cannot see a red tree it was never told about.

Run `make trunk-status` to read the same decision on demand: it prints the culprit and
the revert command and exits non-zero while `develop` is red with the culprit not
yet reverted. `make pr-open` names the same culprit when its 30-minute grace has
lapsed and it opens the PR as a draft.

## Diagnostic

Exact-SHA coverage for the last 10 develop commits, for one workflow:

```bash
bash -c '
  shas=$(git log --format=%H origin/develop -10)
  runs=$(gh run list --workflow trunk.yml --event push --branch develop --limit 100 --json headSha --jq .[].headSha)
  for s in $shas; do
    echo "$runs" | grep -q "$s" || { echo "missing: $s"; }
  done
'
```

A missing SHA after a real push-drop episode is expected, and stays missing
until it ages out of the window. A docs baseline can be recovered by dispatch
as above. A dispatched trunk run gives test evidence, but release admission
requires a push-produced artifact and rejects dispatch as a substitute.

## Retired tooling

`scripts/detect_develop_post_merge_gaps.py`, `scripts/sweep_coverage.py`, and
`scripts/post_merge_signature.py` were written for the retired workflow and
have no workflow caller now. They remain in the tree until a follow-up removes
them together with their tests.

## What we deliberately did not do

- **No extra push-adjacent spam** (`pull_request`, `repository_dispatch`,
  and so on) to force more events. That papers over the
  class without bounding exposure cleanly and burns runners on every related
  event. `trunk-disarm.yml` listens for `workflow_run` completion of
  `trunk.yml`, but it forces nothing: it only reacts to a delivered Trunk
  completion, so a dropped push delays the disarm until the next delivered
  run instead of manufacturing coverage.
- **No replacement of the push trigger.** Push stays the primary trigger for
  workflows that record state on a develop commit; recovery is dispatch.
- **No mutation jobs on schedule.** Anything that opens or closes GitHub
  objects runs on push, dispatch, or a delivered `trunk.yml` completion,
  never on a timer, so a cron run cannot change state
  without a real merge event.
