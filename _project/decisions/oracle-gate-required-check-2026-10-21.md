# Require `oracle-gate` from the oracle App on `develop`

Date: 2026-10-21
Status: Applied to the hosted `develop-squash-only` ruleset (id `15611785`) on
2026-10-21 by the repository owner. `oracle-review` stayed required until its
consumers and the runbook dropped it in one change:
`_project/decisions/oracle-review-check-retired-2026-10-24.md`.
Related: `docs/internal/repo-admin-settings.md` (Branch ruleset — `develop`),
`docs/internal/oracle-review-v2.md`, `.github/workflows/oracle-gate.yml`,
`_project/scripts/oracle_gate.py`, `_project/scripts/ruleset_review_enforcement.py`.

## Decision

Add `oracle-gate` to the required status checks, bound to the
`benchbox-oracle` GitHub App (integration ID `5201041`). The `oracle-gate`
workflow evaluates the oracle-review rule from `develop` code on trusted
triggers and posts the result through that App, so a pull request cannot change
how its own required oracle check runs.

## Change

Ruleset history versions `53637522` (2026-10-17T19:15:50Z) and `54219339`
(2026-10-21T23:37:02Z, actor `User 57046`, `joeharris76`), read with
`gh api repos/BenchBox-dev/benchbox/rulesets/15611785/history/<version>`.
Only the required status checks changed; every other rule is identical.

Before:

```json
{"strict_required_status_checks_policy":false,"required_status_checks":[{"context":"core","integration_id":null},{"context":"explorer","integration_id":null},{"context":"results-data","integration_id":null},{"context":"docs","integration_id":null},{"context":"tooling","integration_id":null},{"context":"oracle-review","integration_id":null},{"context":"hold","integration_id":null}]}
```

After:

```json
{"strict_required_status_checks_policy":false,"required_status_checks":[{"context":"core","integration_id":null},{"context":"explorer","integration_id":null},{"context":"results-data","integration_id":null},{"context":"docs","integration_id":null},{"context":"tooling","integration_id":null},{"context":"oracle-review","integration_id":null},{"context":"hold","integration_id":null},{"context":"oracle-gate","integration_id":5201041}]}
```

## Readback (2026-10-23)

- `gh api repos/BenchBox-dev/benchbox/rulesets/15611785` returns
  `updated_at` 2026-10-21T19:37:02-04:00, `oracle-gate` with `integration_id`
  5201041, and `oracle-review` with no integration ID.
- `gh api apps/benchbox-oracle --jq .id` returns `5201041`, so the binding
  names the oracle App. `ORACLE_GATE_INTEGRATION_ID` in
  `_project/scripts/ruleset_review_enforcement.py` holds the same value.
- The App posts `oracle-gate` as a commit status. On pull request #3340 (head
  `410b4b870`) the status was `success`, created by `benchbox-oracle[bot]`,
  and the pull request merged on 2026-10-23 with the check required.

## Why the binding matters

`oracle-gate` is a commit status. Any workflow with `statuses: write` can post
a status with that name to its own head. GitHub documents that a required
check bound to an app is satisfied only by that app: "If the status is set by
any other person or integration, merging won't be allowed"
([rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-status-checks-to-pass-before-merging)),
and "If status is then provided by a different app or by a user via a commit
status, merging will be prevented"
([changelog, 2021-12-01](https://github.blog/changelog/2021-12-01-ensure-required-status-checks-provided-by-the-intended-app)).
The repository has observed only the positive case, the App's own status
satisfying the check. A forged status from another identity has not been
tested here.

## Precondition not met at the time

`docs/internal/oracle-review-v2.md` asked for production runs showing
`oracle-gate` agreeing with `oracle-review` before the context became
required. That was not verified before this change. Both contexts have been
required together since, and pull requests merged only when both passed.
