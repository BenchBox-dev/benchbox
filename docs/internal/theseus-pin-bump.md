# Theseus pin: bump and rollback

Theseus (`joeharris76/theseus`) is a pinned dependency. One commit SHA, the
lock's `workflow_sha` in `.github/theseus-lock.json` (equal to its
`source_commit`), names every Theseus reference in this repository:

| File | Where the SHA appears |
|---|---|
| `.github/theseus-lock.json` | `workflow_sha` and `source_commit` |
| `.github/workflows/review-verdict.yml` | the `uses:` ref and `with.theseus_ref` |
| `.github/workflows/review-gate.yml` | the `uses:` ref and `with.theseus_ref` |

A `uses:` ref cannot be templated, so the copies are kept equal by a test, not by
construction. `tests/unit/workflows/test_theseus_pin_consistency.py` reads every
workflow under `.github/workflows/`, finds each `uses: joeharris76/theseus/...@<ref>` and each
`theseus_ref:`, and fails when any ref is not a full commit, when a `uses:` ref and a
`theseus_ref` disagree, when two workflows name different commits, when any ref differs from the
lock, or when a workflow mentions the repository in a form the test does not read. A new
workflow that references Theseus is covered without editing the test.

## What a bump changes

One commit changes the lock, both callers, and, when the promoted source changes them,
the vendored matcher (`_project/scripts/soundness_paths.py`) and the policy's
`promotion.dependencies`. Nothing else moves. Policy, reviewer roles and required
checks are not part of a bump.

## Bump procedure

1. Choose a Theseus commit that is on its `main` and whose `test (py3.11)`,
   `test (py3.13)` and `build and installed-wheel smoke` checks passed.
   `scripts/theseus_release_check.py` makes the same check in CI and fails the medium-tier
   jobs when the locked commit does not meet it.
2. In a linked worktree (`make worktree-create BRANCH=chore/theseus-<version> ...`),
   change the lock (version, `source_commit`, `source_sha256`, `delta_sha256`, artifact
   sha256 and URLs, `workflow_sha`, matcher) and set both refs in both callers to the new
   `workflow_sha`. The promotion trust rules in `oracle-review-v2.md` ("Theseus promotion
   trust") decide what the lock must carry.
3. Run the consistency test and the caller tests:

   ```bash
   uv run -- pytest tests/unit/workflows/test_theseus_pin_consistency.py \
     tests/unit/workflows/test_oracle_verdict_workflow.py \
     tests/unit/workflows/test_oracle_gate_workflow.py \
     tests/unit/scripts/test_theseus_lock.py
   ```

4. `make pr-open` and let the gate hold the promotion until the oracle SHIP receipt for the
   exact head and lock digest is present. A stand-in approval cannot authorize a promotion.

## What cannot be exercised before the merge

A bump branch cannot run the new pin. The `oracle` environment deploys only from
`develop` (`repo-admin-settings.md`; `scripts/ruleset_drift_check.py` fails if the policy
widens), `gh workflow run review-verdict.yml` accepts `--ref develop` only, and a pull
request run uses the callers on `develop`. Until the callers on `develop` carry the new
pin, the reviewer and gate that judge the bump are the old ones. The first run of the new
pin is therefore a run on `develop`.

## Checks after the merge

Run these on the merge commit, before arming another pull request. They need one pull
request whose current head has no verdict (open a throwaway draft with a trivial change)
and one with a known defect. A dispatch on a head that already has a result reposts that
result and does not exercise the new pin.

```bash
gh workflow run review-verdict.yml --ref develop -f pr=<number> -f head=<head sha>
gh workflow run review-gate.yml --ref develop -f pr=<number> -f head_sha=<head sha>
gh run list --workflow review-verdict.yml --limit 5
gh run list --workflow review-gate.yml --limit 5
gh api repos/BenchBox-dev/BenchBox/commits/<head sha>/statuses \
  --jq '.[] | select(.context | test("review-(verdict|gate)|oracle-(verdict|gate)")) | [.context, .state, .created_at] | @tsv'
```

The pin is healthy when, on each pull request:

- the run logs `HAS_APP: true` (an empty secret still ends the job green, so a green run
  alone proves nothing);
- `review-verdict` and `oracle-verdict` are posted for the head, then `review-gate` and
  `oracle-gate`, all by `benchbox-oracle[bot]`;
- the trivial pull request reaches `success` on both gate contexts;
- the defect pull request gets a verdict that lists the defect, and its gate does not report
  `success`.

Once the posting detector is available, a green detector for the dispatched heads is part
of the same check. Until then the statuses above are the evidence; record the run links in
the tracker item that carried the bump.

## Rollback

Rollback is a revert of the pin commit. Trigger it when a post-merge check fails or when
reviews and gates stop posting on `develop`.

1. Find the pin commit: `git log --format='%h %s' -- .github/theseus-lock.json`.
2. In a linked worktree:

   ```bash
   make worktree-create BRANCH=revert/theseus-pin-<version> WORKTREE_PATH=../BenchBox.wt-revert-theseus-pin
   cd ../BenchBox.wt-revert-theseus-pin
   make agent-write-preflight
   git revert --no-commit <pin commit>
   ```

3. A revert restores the lock, both callers, the matcher and the policy together. If a later
   commit edited one of those files, resolve the conflict so that all three pin locations
   return to the previous `workflow_sha` and nothing else is lost.
4. Run the test command from the bump procedure. Commit with a subject that names the
   restored Theseus version, then `make pr-open`.
5. The revert is itself a lock change, so the gate holds it for the oracle SHIP receipt for
   its head. After the merge, repeat the checks above.

If the bad pin stops the gate from posting on every pull request, the revert cannot pass
the gate either, because the gate runs from `develop`'s callers. That is a base-pinned gate
failure: follow `docs/agent/emergency-merge.md`. Relaxing the ruleset or the `oracle`
environment is an owner action, and an agent never takes it.
