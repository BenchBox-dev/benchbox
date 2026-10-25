# Stop requiring `oracle-review` on `develop`

Date: 2026-10-24
Status: Decided. The runbook and the review predicate change first; the owner
then removes `oracle-review` from the hosted `develop-squash-only` ruleset
(id `15611785`), the order `docs/internal/repo-admin-settings.md` gives for
changing required contexts.
Related: `_project/decisions/oracle-gate-required-check-2026-10-21.md`,
`docs/internal/repo-admin-settings.md` (Branch ruleset — `develop`),
`docs/internal/oracle-review-v2.md` (Required gate from trusted code),
`_project/scripts/ruleset_review_enforcement.py`.

## Decision

`oracle-gate`, bound to the `benchbox-oracle` App (integration ID `5201041`),
is the only required oracle check. `oracle-review` leaves the required status
checks. The develop review predicate (`review_enforcement_findings`) now passes
only when `oracle-gate` is required with that integration ID; a bare
`oracle-review` no longer satisfies it.

## Why

`oracle-review` runs its workflow file from the pull request, so a pull request
that edits `.github/workflows/` can change the check that judges it.
`oracle-gate` applies the same rule from `develop` code on trusted triggers and
is posted by the App the ruleset binds to. A stale failed `oracle-review` run
also kept the required check red after the oracle passed a head, which needed a
recovery command; `oracle-gate` re-evaluates the head itself.

## Agreement before the change

`docs/internal/oracle-review-v2.md` asked for production runs showing the two
contexts agreeing. On 2026-10-23 every pull request head that had an
`oracle-review` run created from 2026-10-22 onward was compared, using the
latest `oracle-review` check run and the latest App-posted `oracle-gate`
status:

| Result | Heads |
|---|---|
| Both passed, or neither passed | 195 |
| `oracle-review` failed, `oracle-gate` passed | 1 |
| `oracle-review` ran, no `oracle-gate` status | 2 |

The one disagreement (head `5900dae78`) was an `oracle-review` run that
finished as failure at 03:35 just before the oracle posted SHIP; it never ran
again, while `oracle-gate` re-evaluated at 03:36 and passed. The two heads
without a gate status were replaced by new pushes before the gate ran. Every
merged head had both contexts passing, as both were required.

## What stays

- `.github/workflows/oracle-review.yml` keeps its path and name but reduces to
  a notification: it triggers on `pull_request_review` submitted, edited and
  dismissed, has no token permissions, and runs one no-op `review-event` job.
  It reports no `oracle-review` check. Its completion is how `oracle-gate`
  learns of review events, which have no trusted trigger of their own.
  Retiring the workflow needs a replacement for that trigger.
- Gate refresh is `gh workflow run oracle-gate.yml --ref develop -f pr=<number>`; CI rerun is `make pr-rerun`.
  `make oracle-review-recover` was retired because it refreshed no required check.

## Owner step

After the runbook change merges, remove `oracle-review` from the required
status checks of ruleset `15611785`, keeping `oracle-gate` with
`integration_id` 5201041. Then confirm with
`gh auth token | uv run -- python scripts/ruleset_drift_check.py --token-stdin`
and the binding readback in `docs/internal/repo-admin-settings.md`.
