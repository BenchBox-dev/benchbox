# Decision: one-context ruleset relaxation to repair a broken develop base

Date: 2026-10-24
Status: Decided and in place. Performed by the owner; recorded afterwards from the ruleset history.
Related: `docs/agent/emergency-merge.md`, `docs/internal/repo-admin-settings.md`,
`scripts/ruleset_drift_check.py`, `_project/decisions/native-merge-queue-activation-20260822.md`,
`_project/decisions/github-org-transfer-benchbox-dev-2026-08-21.md`,
`_project/decisions/strict-base-refresh-policy-2026-08-14.md`,
`_project/decisions/develop-ruleset-drift-required-2026-08-13.md`.

## Observation

The `tooling` check runs the comment-policy checker that lives on the base branch.
The comment-policy scope ledger on `develop` held an entry that named a EULA path
that does not exist in the tree. The checker failed on that entry for every pull
request, including pull request 3385, which repaired the entry. The check ran
the broken base checker against the repair, so the repair could not pass
`tooling` and no pull request could merge.

The failure needed a base repair that no pull request could provide, so the
owner performed the GitHub-side unblocking action that
`docs/agent/emergency-merge.md` reserves to the owner.

## Ruleset history

Ruleset `develop-squash-only` (id 15611785), from
`gh api repos/BenchBox-dev/benchbox/rulesets/15611785/history`. All versions
were written by the owner's user account. Times are 2026-10-24, US Eastern.

| Window | Version removing `tooling` | Time | Version restoring it | Time | Open |
|---|---|---|---|---|---|
| 1 | 54609076 | 11:55:23 | 54609195 | 11:57:14 | 1 m 51 s |
| 2 | 54609215 | 11:57:34 | 54609293 | 11:58:48 | 1 m 14 s |
| 3 | 54609810 | 12:07:03.988 | 54609816 | 12:07:07.636 | 3.6 s |

Pull request 3385 (merge commit `a56738db4ab4803d80d48a5a24c6d67bf845ea0a`)
merged during window 3. This record does not identify a merge in windows 1 and 2.

Only the `tooling` required context was removed in each window. No bypass actor
was added, enforcement stayed `active`, and the `oracle-gate` binding to the
`benchbox-oracle` App (integration 5201041), the required review-thread
resolution and the squash-only merge method were not edited.

## Options considered

1. Leave the gate wedged until a separate base repair landed. Rejected: the
   repair is the only change that can clear the entry, and it could not pass the
   check that the entry broke.
2. Add a bypass actor to the ruleset. Rejected: it widens the ruleset for every
   branch operation and is harder to reverse than removing one context.
3. Remove exactly one required context for one pull request, merge, and restore.
   Chosen: the smallest relaxation that lets the repair through, and it is
   reversible from a capture.

## Decision

The owner relaxed the ruleset by removing `tooling` from the required contexts
for the duration of each window, merged the repair in window 3, and restored the
context immediately each time.

Policy effect. The records cited under Related hold a policy of zero bypass
actors and no ruleset relaxation. This relaxation is a recorded exception to that
policy for the three windows above. Those records supersede nothing else and
otherwise stand: no bypass actor was added and no standing relaxation remains.
The exception is not a precedent for agents. Agents still never relax a ruleset
for a base-broken gate; the owner alone does, under
`docs/agent/emergency-merge.md`.

## Gaps this record exposes

- Two windows were opened before the window that merged. The restore in each
  case was prompt, but nothing at the time proved the live ruleset equalled its
  pre-relaxation state; the existing drift check ignored each context's
  `integration_id` and could not compare against a capture.
- The procedure had no capture step, no named pull request and head, and no hold
  on competing armed pull requests during a window.

`docs/agent/emergency-merge.md` and `docs/internal/repo-admin-settings.md` now
carry one procedure that closes these gaps, and
`scripts/ruleset_drift_check.py --compare-capture` proves the live ruleset equals
the capture taken before the change.

## Not decided here

Whether the checker should recover from a mispaired scope entry without a
ruleset change was considered and not adopted. It would be new trusted logic that
must be installed before an incident to help, and relaxation frequency does not
yet justify it. A separate proposal can reopen it.

A later version, 54642714 at 21:53:22 on 2026-10-24, is not part of this
exception.
