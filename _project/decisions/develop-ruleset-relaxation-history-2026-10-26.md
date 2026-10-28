# Decision: documentation of historical ruleset relaxation windows on develop

Date: 2026-10-26
Status: Decided and in place. Recorded from the ruleset history API, PR records, and git logs.
Related: `docs/agent/emergency-merge.md`, `docs/internal/repo-admin-settings.md`,
`scripts/ruleset_drift_check.py`, `_project/decisions/develop-tooling-context-removal-2026-10-24.md`,
`_project/decisions/native-merge-queue-activation-20260822.md`,
`_project/decisions/github-org-transfer-benchbox-dev-2026-08-21.md`,
`_project/decisions/strict-base-refresh-policy-2026-08-14.md`,
`_project/decisions/develop-ruleset-drift-required-2026-08-13.md`.

## Observation

Three historical ruleset relaxation windows on `develop-squash-only` (ruleset id `15611785`)
predating the 2026-10-24 tooling removal record were unrecorded in `_project/decisions/`:
windows on 2026-10-04, 2026-10-13/14, and 2026-10-14. This record reconstructs and
documents the facts of each window from GitHub's ruleset history API (`gh api repos/BenchBox-dev/benchbox/rulesets/15611785/history`),
pull request records, check-run data, and first-parent git merge history on `develop`.
Where interpretations cannot be directly verified from API payloads, they are labeled
as inferences.

## Ruleset history windows

All ruleset versions below are for `develop-squash-only` (ruleset id `15611785`).
All versions were modified through actor id `57046` (User `joeharris76`).
Times are US Eastern (EDT / UTC-4).

| Window | Action | Opening version | Time | Closing version | Time | Duration | Performed by |
|---|---|---|---|---|---|---|---|
| 2026-10-04 | Remove `oracle-review` | 51862350 | 12:35:20.972 | 51862373 | 12:35:47.034 | 26.062 s | Owner (`joeharris76`) |
| 2026-10-13/14 | Remove `tooling` | 53099386 | 23:13:35.997 | 53099454 | 23:14:19.740 | 43.743 s | Owner credentials (`joeharris76`) |
| 2026-10-14 | Add bypass actor | 53169168 | 10:17:16.852 | 53169197 | 10:17:28.602 | 11.750 s | Owner credentials (`joeharris76`) |

## Window 1: 2026-10-04 (Removal of `oracle-review`)

- Opening version: `51862350` at 12:35:20.972 EDT (16:35:20.972Z). Required checks:
  `['core', 'explorer', 'results-data', 'docs', 'landing', 'tooling']` (removed `oracle-review`).
  Bypass actors: `[]`. Enforcement: `active`.
- Closing version: `51862373` at 12:35:47.034 EDT (16:35:47.034Z). Required checks:
  `['core', 'explorer', 'results-data', 'docs', 'landing', 'tooling', 'oracle-review']`
  (restored `oracle-review`). Bypass actors: `[]`. Enforcement: `active`.
- Performed by: Owner (`joeharris76`, actor id 57046).
- Merges inside the window (from `git log --first-parent origin/develop`):
  1. Commit `df92ddfb11966f650438d82b32685f587e6b9737` at 12:35:22 EDT (PR #2635,
     "feat(ci): let a listed attester substitute a stand-in review for the Codex connector",
     `mergedAt` 16:35:23Z).
  2. Commit `4f6dc4bda04117b2752f65c69266f9050edafd0f` at 12:35:26 EDT (PR #2621,
     "fix(equivalence): fail closed when the derived ORDER BY check cannot evaluate",
     `mergedAt` 16:35:27Z).
  3. Commit `767d8f72c2f0771be157a795a323edd9ed86c402` at 12:35:28 EDT (PR #2625,
     "fix/datagen empty manifest validation", `mergedAt` 16:35:29Z).
  4. Commit `a4e1a817a91e2f81ae61d04ed1c4175aa5fe52f6` at 12:35:43 EDT (PR #2612,
     "feat(ci): enforce the TPC-DS cross-surface gate on Polars, pandas and DataFusion",
     `mergedAt` 16:35:43Z).
- Context and evidence:
  - PR comments on #2612, #2625, and others show the Codex code review connector was at its
    usage limits ("You have reached your Codex usage limits for code reviews").
  - On PRs #2635, #2621, and #2612, the owner posted: "Merged under a recorded, owner-authorized
    ruleset exception: `oracle-review` was removed from the `develop-squash-only` required checks
    from 16:35:21Z to 16:35:47Z on 2026-10-04 to merge #2635, #2621 and #2612, because the Codex
    connector was at its usage limit and the ruleset has no bypass actor. Every other required check
    passed on the merged head, and an independent stand-in review is posted on this PR. The ruleset
    was restored to its prior state immediately afterwards (verified identical). From #2635 onward,
    a listed attester can record a stand-in review with `Stand-in oracle review: APPROVE <head SHA>`."
  - PR #2625 also merged inside the window [inference: PR #2625 was armed with auto-merge and
    merged as soon as `oracle-review` was removed from the required check list].
  - Gap: the owner's quoted exception names only #2635, #2621 and #2612. PR #2625 was not
    named in it, and this record cites no stand-in review or other review evidence for #2625.

## Window 2: 2026-10-13/14 (Removal of `tooling`)

- Opening version: `53099386` at 23:13:35.997 EDT (2026-10-14T03:13:35.997Z). Required checks:
  `['core', 'explorer', 'results-data', 'docs', 'landing', 'oracle-review']` (removed `tooling`).
  Bypass actors: `[]`. Enforcement: `active`.
- Closing version: `53099454` at 23:14:19.740 EDT (2026-10-14T03:14:19.740Z). Required checks:
  `['core', 'explorer', 'results-data', 'docs', 'landing', 'tooling', 'oracle-review']`
  (restored `tooling`). Bypass actors: `[]`. Enforcement: `active`.
- Performed by: Owner account credentials (`joeharris76`, actor id 57046).
- Merges inside the window:
  1. Commit `c14ecdb9c3aa2a6ea1366a79fe7450f17f78d255` at 23:14:05 EDT (PR #2954,
     "fix(policy): drop comment-scope evidence for suppressions that were removed",
     `mergedAt` 2026-10-14T03:14:05Z).
- Context and evidence:
  - PR #2954 head `b28d4d95b2` failed `tooling` due to stale comment-scope evidence for suppressions
    that had been removed on `develop`. The failure was base-pinned; PR #2954 was the repair that
    fixed the scope evidence, but could not pass the base checker without removing `tooling`.

## Window 3: 2026-10-14 (Addition of bypass actor)

- Opening version: `53169168` at 10:17:16.852 EDT (14:17:16.852Z). Added bypass actor:
  `[{'actor_id': None, 'actor_type': 'OrganizationAdmin', 'bypass_mode': 'pull_request'}]`.
  Required checks: all 7 required checks remained in place. Enforcement: `active`.
- Closing version: `53169197` at 10:17:28.602 EDT (14:17:28.602Z). Bypass actors: `[]`
  (removed `OrganizationAdmin` bypass actor). Enforcement: `active`.
- Performed by: Owner account credentials (`joeharris76`, actor id 57046).
- Merges inside the window:
  1. Commit `e8e0e9f0d7ccc1ae65c0dcd23fde517f2d30fdeb` at 10:17:25 EDT (PR #2953,
     "fix(scope): drop spent C901 directive entries that block every PR",
     `mergedAt` 14:17:26Z).
- Context and evidence:
  - PR #2953 head `0bc02317d8` failed `tooling` due to spent C901 directive entries on `develop`
    that blocked every PR. Instead of removing the `tooling` context, an `OrganizationAdmin` bypass
    actor was added to bypass the failing check and squash-merge the repair PR #2953. The bypass
    actor was removed 11.75 seconds later.

## Policy reconciliation

Owner decision Q8 of 2026-10-26 ratifies the policy:
1. Zero bypass actors: adding a bypass actor is permanently forbidden (the window 3 bypass is
   an unrepeated historical exception).
2. Ruleset relaxation is owner-only everywhere: agents never relax rulesets.
3. Base-broken gates: when a required check is broken by `develop`, only the owner may relax the
   ruleset by removing exactly one required context (including `oracle-gate` when it is the broken
   context) for a single named merge, immediately restoring it and verifying with
   `scripts/ruleset_drift_check.py --compare-capture`.
