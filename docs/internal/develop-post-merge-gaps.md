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

- **No extra push-adjacent spam** (`pull_request`, `workflow_run`,
  `repository_dispatch`, and so on) to force more events. That papers over the
  class without bounding exposure cleanly and burns runners on every related
  event.
- **No replacement of the push trigger.** Push stays the primary trigger for
  workflows that record state on a develop commit; recovery is dispatch.
- **No mutation jobs on schedule.** Anything that opens or closes GitHub
  objects stays push and dispatch only, so a cron run cannot change state
  without a real merge event.
