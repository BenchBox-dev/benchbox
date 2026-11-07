# Decision: remove `oracle-gate` for one merge to repair the review gateway after the Muse login was lost

Date: 2026-11-06
Status: Decided and in place.
Related: `docs/agent/emergency-merge.md`, `docs/internal/repo-admin-settings.md`,
`scripts/ruleset_drift_check.py`, `docs/internal/oracle-gateway-host.md`,
`_project/decisions/oracle-gate-relaxation-2026-11-04.md`,
`_project/decisions/develop-ruleset-relaxation-history-2026-10-26.md`,
`_project/decisions/native-merge-queue-activation-20260822.md`,
`_project/decisions/github-org-transfer-benchbox-dev-2026-08-21.md`,
`_project/decisions/strict-base-refresh-policy-2026-08-14.md`, `docs/internal/github-org-transfer.md`.

## Decision

The `oracle-gate` required context was removed from `develop-squash-only` (ruleset id `15611785`)
for one merge: PR #3965 at head `b033713515ce2a7f8ffee615180243dd3dadfce6`. The context was
restored, bound to integration 5201041 (`benchbox-oracle`), immediately afterwards. This is a
single-merge relaxation under owner decision Q8 as recorded in
`develop-ruleset-relaxation-history-2026-10-26.md` (policy reconciliation, item 3). It supersedes
none of the zero-relaxation records cited by `docs/agent/emergency-merge.md`:

- `_project/decisions/native-merge-queue-activation-20260822.md:88`
- `_project/decisions/github-org-transfer-benchbox-dev-2026-08-21.md:74-75`
- `docs/internal/github-org-transfer.md:8`
- `_project/decisions/strict-base-refresh-policy-2026-08-14.md:175-176`

No bypass actor was added and enforcement stayed `active`.

The owner posted the full-diff review comment on #3965 at 2026-11-06T13:48:34Z: "Reviewed the full
diff of b033713515ce2a7f8ffee615180243dd3dadfce6". The owner then explicitly instructed the agent to
complete the remaining owner steps in the session: hold, capture, relax, merge, restore, and bring
the gateway back. The ruleset change was therefore executed by the agent on that explicit owner
instruction, which supersedes the rule that agents never relax a ruleset for this one merge only.

## Why the gate could not pass its own repair

The gateway host script requires a broker login for every provider in `GATEWAY_PROVIDERS`. The
broker's `muse-code` login was lost at 2026-11-03 16:03:56 (broker log). Hosts that were already
running kept serving; the host script refused on restart with `error: no broker login for:
muse-code`. Theseus `gateway-clients` refused the same way for any reviewer config that still named
a `muse-code` fallback (`review-verdict: error: no logged-in broker account for muse-code`).

Hosted reviews need the gateway, so no `oracle-verdict` or `oracle-gate` status could be posted on
any pull request, including the repair. The gate runs from `develop`, so the repair could not
satisfy it. `make pr-open` also refused while the gateway was down, so the owner opened the PR by
hand. Reproduced on `origin/develop` `20a0045113e6c8ba13c3e7c4f78351e3fe81f81e`.

## The repair

PR #3965 removes every `muse-code/...` row from the fallback chains in
`.github/oracle-omp-config.yml`, moves the `slow` role and its chain key to
`anthropic/claude-fable-5-1`, drops `muse-code` from `GATEWAY_PROVIDERS` and its test, and updates
`docs/internal/oracle-gateway-host.md`. Before the merge:
- Theseus `gateway-clients`, run offline from the verified `develop` install with the new config
  and accounts for only anthropic, openai-codex and google-antigravity, succeeded and listed 8
  models, none from `muse-code`; the same command failed on `develop`'s config.
- `make comment-policy-check` reported 0 violations; the affected unit tests passed under
  `make heavy-run`.

## Ruleset window

The capture was taken at relaxation time: `/Users/joe/scratch-ruleset/ruleset-15611785-before.json`.
No other pull request was armed, so no holds were needed. Only `oracle-gate` was removed;
`review-gate` was not required yet.

| Action | Time (UTC) | Detail |
|---|---|---|
| Remove `oracle-gate` | 2026-11-06 14:03:13 | Executed by the agent on the owner's instruction |
| Squash merge | 2026-11-06 14:03:19 | #3965 merged as `34c947c60a5f085d25041db54b72af3a2e2baa92` |
| Restore `oracle-gate` | 2026-11-06 14:03:20 | Bound to integration 5201041 |

The window lasted 7 seconds.

Follow-up checks:
- Enforcement `active` with no bypass actors.
- `scripts/ruleset_drift_check.py --compare-capture` exited 0 ("Ruleset 15611785 matches").
- `--require-bypass-actor-visibility` exited 1 only for "release-only: live ruleset is missing".
  This is unrelated: the release ruleset was deliberately deleted by the legacy-release retirement.

## After the merge

The gateway was restored at about 14:05Z: `oracle_gateway_host.sh check` exited 0, with 8/8 models
on the local and tailnet endpoints for `review-ci`, `review-local`, `review-cloud` and
`review-theseus`. The three-provider gateway (anthropic, openai-codex, google-antigravity) stands.
Hardening the host against the loss of a provider login is tracked as a separate follow-up item.
