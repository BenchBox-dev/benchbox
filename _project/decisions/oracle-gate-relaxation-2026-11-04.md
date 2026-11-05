# Decision: remove `oracle-gate` for one merge to repair the oracle callers' secrets

Date: 2026-11-04
Status: Decided and in place.
Related: `docs/agent/emergency-merge.md`, `docs/internal/repo-admin-settings.md`,
`scripts/ruleset_drift_check.py`, `_project/decisions/develop-ruleset-relaxation-history-2026-10-26.md`,
`_project/decisions/native-merge-queue-activation-20260822.md`,
`_project/decisions/github-org-transfer-benchbox-dev-2026-08-21.md`,
`_project/decisions/strict-base-refresh-policy-2026-08-14.md`, `docs/internal/github-org-transfer.md`.

## Decision

The owner removed the `oracle-gate` required context from `develop-squash-only` (ruleset id
`15611785`) for one merge: PR #3951 at head `02f83694807a9b32b77726d8de0dcd666274eba4`. The
context was restored, bound to integration 5201041 (`benchbox-oracle`), immediately afterwards.
This is a single-merge relaxation under owner decision Q8 as recorded in
`develop-ruleset-relaxation-history-2026-10-26.md` (policy reconciliation, item 3). It supersedes
none of the zero-relaxation records listed above. No bypass actor was added and enforcement stayed
`active`.

## Why the gate could not pass its own repair

PR #3942 (merged as `dff1efde16`) moved `oracle-verdict.yml` and `oracle-gate.yml` onto the
Theseus reusable workflows in `joeharris76/theseus`, which is owned by a different account from
this organization. A called workflow receives only the secrets its caller passes, and environment
secrets are no exception. The callers passed none, so every secret resolved to an empty string.
The reviewer and gate then posted nothing, and their jobs still went green. Every run of the new
callers logged `HAS_APP: false`: develop push runs 41883976808 and 41883976951, schedule run
41891058745, and PR run 41887238141 on #3925. No `oracle-verdict` or `oracle-gate` status could
be posted on any pull request, including the repair, because the gate runs from `develop`'s
callers.

`secrets: inherit` does not cross owners. A probe (run 41912655674 in the archived
`BenchBox-dev/secrets-probe-caller`, secret held only in an environment) showed:

| Caller passes | Callee | Result |
|---|---|---|
| nothing | undeclared | empty, job succeeds |
| `secrets: inherit` | undeclared | empty, job succeeds |
| `secrets: inherit` | declares the secret as required | fails at startup |
| the secret by name | declares the secret | delivered |

## The repair

PR #3951 promotes the Theseus lock from v0.1.4 (`fd1355c`) to v0.1.6 (`6e5765e`). Its reusable
workflows declare their secrets as required. The callers now map each secret by name: seven for the
review and two for the gate. Repository-level secrets are not passed. Before the merge:
- CI run 41920597690 passed every required context except the base-pinned `oracle-gate`.
- An independent review and the trusted local Theseus review both returned SHIP.
- The owner read the full diff of the named head and said so in a comment on the PR at 00:31:25Z:
  https://github.com/BenchBox-dev/benchbox/pull/3951#issuecomment-6646175939.

## Ruleset window

| Action | Opening version | Time (UTC) | Closing version | Time (UTC) | Duration | Performed by |
|---|---|---|---|---|---|---|
| Remove `oracle-gate` | 56358143 | 2026-11-05 00:31:29.835 | 56358154 | 2026-11-05 00:31:34.935 | 5.1 s | Owner (`joeharris76`, actor id 57046) |

Merges inside the window, from `git log --first-parent origin/develop`: one, commit
`80f82f31eb984c432d2974f6c86671011a1c9cdb` at 00:31:32Z (PR #3951, merged by `joeharris76`). No
other pull request was armed while the ruleset was relaxed.

The restore was verified with
`scripts/ruleset_drift_check.py --compare-capture /tmp/ruleset-15611785-before.json` ("Ruleset
15611785 matches", exit 0, `oracle-gate` bound to 5201041) and with
`--require-bypass-actor-visibility`.

## After the merge

On `80f82f31eb`, the develop push sweeps (oracle-verdict 41941969876, oracle-gate 41941970012) logged
`HAS_APP: true`. The review run they dispatched (41942047290) logged `HAS_APP: true`,
`HAS_TAILNET: true` and a masked `ORACLE_GATEWAY_URL`. `benchbox-oracle[bot]` then posted
`oracle-verdict` success (00:33:02Z) and `oracle-gate` success (00:33:46Z, run 41942185807) on PR
#3947 head `993cf9eb5b8e6fdf169f4b0a6c731b90d6526e50`. These are the first statuses posted through
the Theseus workflows.
